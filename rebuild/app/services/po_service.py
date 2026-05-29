"""
app/services/po_service.py
============================
Purchase Order lifecycle — create, receive, bill (3-way match).

3-way match workflow:
  1. PO created → sent to vendor
  2. Goods arrive → POReceipt + POReceiptLine(s) created
     * Receipt auto-writes InventoryTransaction (PO_RECEIPT) for each line
     * Drop-ship receipts do NOT increment inventory
  3. Vendor bill arrives → VendorBill + VendorBillLine(s) created
     * System compares billed qty/cost to received qty/cost
     * Discrepancies flagged on VendorBillLine.has_discrepancy
  4. Bill approved → queued for QBO sync
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, EntityType, FulfillmentSource, InventoryTxnType, SOLineStatus,
    POStatus, QBOSyncStatus, VendorBillStatus,
)
from app.models.inventory import InventoryTransaction
from app.models.product import Product
from app.models.purchase_order import (
    POLine, POReceipt, POReceiptLine,
    PurchaseOrder, VendorBill, VendorBillLine,
)
from app.models.quote import SOLine
from app.settings_utils import bump_counter
from app.services.base import BaseService
from app.services.product_service import ProductService


class POService(BaseService):

    # ── PO Creation ───────────────────────────────────────────────────────────

    def create_po(
        self,
        vendor_id: int,
        data: dict,
        is_drop_ship: bool = False,
        drop_ship_customer_id: int | None = None,
        drop_ship_address_id: int | None = None,
    ) -> PurchaseOrder:
        """
        Create a draft PO. Generates po_number (PO-YEAR-NNNN).
        """
        year = datetime.utcnow().year
        po_number = bump_counter(self.db, "next_po_number", "PO", year)

        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor_id,
            status=POStatus.DRAFT,
            is_drop_ship=is_drop_ship,
            drop_ship_customer_id=drop_ship_customer_id,
            drop_ship_address_id=drop_ship_address_id,
            freight_in_cost=float(data.get("freight_in_cost", 0.0)),
            vendor_confirmation_number=data.get("vendor_confirmation_number"),
            notes=data.get("notes", ""),
            internal_notes=data.get("internal_notes", ""),
            expected_at=data.get("expected_at"),
        )
        self.db.add(po)
        self.db.flush()

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.CREATED,
            new_value={"po_number": po_number, "vendor_id": vendor_id},
        )
        self.db.commit()
        return po

    def add_line(self, po_id: int, product_id: int | None, data: dict) -> POLine:
        """
        Add a product line to a draft PO.
        Pulls vendor cost from ProductVendorSource when product_id is given.
        """
        po = self._get_po_or_404(po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot add lines to a PO in status '{po.status}'")

        unit_cost = float(data.get("unit_cost", 0.0))

        # Auto-fill cost from preferred vendor source
        if product_id is not None and unit_cost == 0.0:
            from app.models.product import ProductVendorSource
            source = (
                self.db.query(ProductVendorSource)
                .filter(
                    ProductVendorSource.product_id == product_id,
                    ProductVendorSource.vendor_id == po.vendor_id,
                    ProductVendorSource.is_active == True,  # noqa: E712
                )
                .first()
            )
            if source:
                unit_cost = source.vendor_cost

        line = POLine(
            po_id=po_id,
            product_id=product_id,
            description=data.get("description", ""),
            qty_ordered=int(data.get("qty_ordered", 1)),
            unit_cost=unit_cost,
            core_charge_per_unit=float(data.get("core_charge_per_unit", 0.0)),
            notes=data.get("notes", ""),
        )
        self.db.add(line)
        self.db.commit()
        return line

    def send_to_vendor(self, po_id: int) -> None:
        """
        Mark PO as sent and increment Product.qty_on_order for all product lines.

        Distinction: PO created ≠ inventory on order.  PO sent = inventory on order.
        This matches real-world purchasing: a draft PO is not a commitment until sent.
        """
        po = self._get_po_or_404(po_id)
        old_status = po.status
        po.status = POStatus.SENT
        po.ordered_at = datetime.utcnow()

        for line in po.lines:
            if line.product_id and line.qty_ordered > 0:
                product = self.db.query(Product).filter(Product.id == line.product_id).first()
                if product:
                    product.qty_on_order += line.qty_ordered

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=POStatus.SENT,
        )
        self.db.commit()

    # ── Receiving ─────────────────────────────────────────────────────────────

    def create_receipt(
        self,
        vendor_id: int,
        po_line_quantities: dict[int, int],  # {po_line_id: qty_received}
        data: dict,
    ) -> POReceipt:
        """
        Record goods receipt against one or more PO lines.

        For each line (R6, R7, R11):
          1. Creates POReceiptLine
          2. Detects over-receipt (qty_received > qty_ordered) → flags line
          3. Writes InventoryTransaction (PO_RECEIPT) unless drop-ship
          4. Updates Product.qty_on_hand cache + qty_on_order
          5. Updates Product.cost via moving-weighted-average + last_cost
          6. FIFO-allocates received qty to linked SO lines (any qty leftover
             goes to general available stock)
          7. Records ProductCostHistory if vendor source cost differs

        Marks PO RECEIVED if all lines fully received (qty_received + qty_cancelled
        >= qty_ordered), PARTIAL otherwise.
        """
        receipt = POReceipt(
            vendor_id=vendor_id,
            received_by_id=self.current_user_id,
            tracking_number=data.get("tracking_number"),
            carrier=data.get("carrier"),
            notes=data.get("notes", ""),
        )
        self.db.add(receipt)
        self.db.flush()

        product_svc = ProductService(self.db, self.current_user_id)
        # Lazy import to avoid circular reference at module load time
        from app.services.inventory_service import InventoryService
        inv_svc = InventoryService(self.db, self.current_user_id)

        po = None  # populated on first processed line; used for status update after loop
        for po_line_id, qty in po_line_quantities.items():
            if qty <= 0:
                continue

            po_line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
            if po_line is None:
                raise ValueError(f"POLine {po_line_id} not found")

            po = po_line.po  # all lines belong to the same PO; captured for post-loop status update
            is_drop_ship = po.is_drop_ship

            # Record receipt line (with optional per-line condition notes)
            condition_notes_map: dict[int, str] = data.get("condition_notes_map") or {}
            receipt_line = POReceiptLine(
                receipt_id=receipt.id,
                po_id=po_line.po_id,
                po_line_id=po_line_id,
                qty_received=qty,
                condition_notes=condition_notes_map.get(po_line_id),
            )
            self.db.add(receipt_line)

            # Update PO line received qty
            po_line.qty_received += qty

            # R6 — Over-receipt detection (do NOT silently inflate qty_ordered)
            if po_line.qty_received > po_line.qty_ordered:
                po_line.over_received = True
                po_line.over_received_qty = po_line.qty_received - po_line.qty_ordered
                from app.services.notification_service import NotificationService
                NotificationService.build_po_over_receipt(
                    self.db,
                    po_id=po.id,
                    po_number=po.po_number,
                    sku=po_line.description or str(po_line.product_id),
                    qty_ordered=po_line.qty_ordered,
                    qty_received=po_line.qty_received,
                )

            # Update product inventory cache + ledger (stock receipts only)
            if not is_drop_ship and po_line.product_id:
                product = self.db.query(Product).filter(Product.id == po_line.product_id).first()
                if product:
                    product.qty_on_hand += qty
                    product.qty_on_order = max(0, product.qty_on_order - qty)

                    # R11 — moving weighted average cost update
                    if po_line.unit_cost and po_line.unit_cost > 0:
                        inv_svc._apply_moving_average_cost(product, qty, po_line.unit_cost)

                    txn = InventoryTransaction(
                        product_id=product.id,
                        transaction_type=InventoryTxnType.PO_RECEIPT,
                        qty_change=qty,
                        qty_after=product.qty_on_hand,
                        reference_type=EntityType.PO_RECEIPT,
                        reference_id=receipt.id,
                        performed_by_id=self.current_user_id,
                        notes=f"PO {po.po_number}, line {po_line_id}",
                    )
                    self.db.add(txn)

                # Record cost change if the PO cost differs from vendor source
                if po_line.unit_cost > 0:
                    product_svc.compare_and_record_cost_change(
                        product_id=po_line.product_id,
                        vendor_id=vendor_id,
                        new_cost=po_line.unit_cost,
                        po_id=po_line.po_id,
                    )

                # R7 — FIFO-allocate to linked SO lines before excess goes to stock
                self._allocate_to_linked_sos(po_line_id, qty, po.po_number)

        # Mark PO status once after all lines are processed.
        # Evaluated here (not inside the loop) to avoid redundant DB writes
        # on multi-line receipts. PO closes when every line is fully settled.
        if po is not None:
            self.db.flush()
            all_settled = all(
                (ln.qty_received + ln.qty_cancelled) >= ln.qty_ordered
                for ln in po.lines
            )
            po.status = POStatus.RECEIVED if all_settled else POStatus.PARTIAL

        self.audit(
            entity_type=EntityType.PO_RECEIPT,
            entity_id=receipt.id,
            action=AuditAction.CREATED,
            new_value={"vendor_id": vendor_id, "lines": list(po_line_quantities.keys())},
        )
        self.db.commit()
        return receipt

    def _allocate_to_linked_sos(
        self,
        po_line_id: int,
        qty_received: int,
        po_number: str,
    ) -> int:
        """
        R7 — Allocate newly-received qty to SO lines that link to this po_line,
        oldest SO first (FIFO).

        For each linked SOLine in FIFO order:
          - Increment qty_committed on the SO line and product (the new stock
            is immediately reserved for the customer it was ordered for).
          - Advance the line's status from AWAITING_PO_RECEIPT → RESERVED_STOCK
            when its full demand has been met (partial fulfillment keeps it
            in AWAITING state).
          - Reduce qty_backordered on the product if the line was backordered.

        Returns the qty consumed by SO allocation. Remaining qty is unallocated
        (lives in general qty_available).
        """
        linked_lines = (
            self.db.query(SOLine)
            .filter(SOLine.linked_po_line_id == po_line_id)
            .order_by(SOLine.id)  # FIFO: lowest id = oldest SO
            .all()
        )
        if not linked_lines:
            return 0

        remaining = qty_received
        allocated_total = 0

        for so_line in linked_lines:
            if remaining <= 0:
                break
            outstanding = so_line.qty_ordered - so_line.qty_committed - so_line.qty_invoiced
            if outstanding <= 0:
                continue

            take = min(remaining, outstanding)
            so_line.qty_committed += take
            allocated_total += take
            remaining -= take

            # Mirror the commitment on the product cache
            if so_line.product_id:
                product = self.db.query(Product).filter(Product.id == so_line.product_id).first()
                if product:
                    product.qty_committed += take
                    product.qty_backordered = max(0, product.qty_backordered - take)

            # Advance line_status when fully met
            if so_line.qty_committed >= so_line.qty_ordered - so_line.qty_invoiced:
                so_line.line_status = SOLineStatus.RESERVED_STOCK
            # else: stay in AWAITING_PO_RECEIPT for partial linked receipt

            # Write ledger row for the reservation
            txn = InventoryTransaction(
                product_id=so_line.product_id,
                transaction_type=InventoryTxnType.SO_COMMITTED,
                qty_change=-take,  # negative = removed from available
                qty_after=(self.db.query(Product)
                          .filter(Product.id == so_line.product_id).first().qty_on_hand
                          if so_line.product_id else 0),
                reference_type=EntityType.SALES_ORDER,
                reference_id=so_line.so_id,
                performed_by_id=self.current_user_id,
                notes=f"Linked-PO allocation from PO {po_number} (po_line {po_line_id})",
            )
            self.db.add(txn)

        self.db.flush()
        return allocated_total

    def cancel(self, po_id: int) -> None:
        """
        Cancel a PO. Raises ValueError if already BILLED (3-way match complete).
        If PO was SENT, reverses the qty_on_order increments for all product lines
        so inventory counts stay accurate.
        Idempotent: calling on an already-CANCELLED PO is a no-op.
        """
        po = self._get_po_or_404(po_id)
        if po.status == POStatus.BILLED:
            raise ValueError(
                "Cannot cancel a billed PO — the vendor bill has already been reconciled."
            )
        if po.status == POStatus.CANCELLED:
            return  # idempotent

        old_status = po.status

        # Reverse on-order counts only if PO had already been sent to vendor
        if old_status == POStatus.SENT:
            for line in po.lines:
                if line.product_id and line.qty_ordered > 0:
                    product = self.db.query(Product).filter(Product.id == line.product_id).first()
                    if product:
                        product.qty_on_order = max(0, product.qty_on_order - line.qty_ordered)

        po.status = POStatus.CANCELLED
        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=POStatus.CANCELLED,
        )
        self.db.commit()

    def cancel_line(self, po_line_id: int, reason: str = "") -> POLine:
        """
        Cancel the outstanding (unreceived) qty on a single PO line.

        Sets qty_cancelled = qty_ordered - qty_received so the line counts as
        settled for the "all received?" check. Reduces product.qty_on_order by
        the cancelled qty. If all PO lines are now settled, marks the PO RECEIVED.
        """
        po_line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
        if po_line is None:
            raise ValueError(f"POLine {po_line_id} not found")

        po = po_line.po
        if po.status not in (POStatus.SENT, POStatus.PARTIAL):
            raise ValueError(
                f"Cannot cancel a line on a PO with status '{po.status}'. "
                f"Only SENT or PARTIAL POs allow line cancellation."
            )

        outstanding = po_line.qty_ordered - po_line.qty_received - po_line.qty_cancelled
        if outstanding <= 0:
            raise ValueError("Line has no outstanding qty to cancel.")

        # Cancel the outstanding qty
        po_line.qty_cancelled += outstanding
        po_line.cancel_reason = reason or "cancelled"
        po_line.cancelled_at = datetime.utcnow()
        po_line.cancelled_by_id = self.current_user_id

        # Reduce on-order count for the product
        if po_line.product_id:
            product = self.db.query(Product).filter(Product.id == po_line.product_id).first()
            if product:
                product.qty_on_order = max(0, product.qty_on_order - outstanding)

        # Check if all lines are now settled
        self.db.flush()
        all_settled = all(
            (ln.qty_received + ln.qty_cancelled) >= ln.qty_ordered
            for ln in po.lines
        )
        if all_settled:
            po.status = POStatus.RECEIVED

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.EDITED,
            new_value={
                "action": "cancel_line",
                "po_line_id": po_line_id,
                "qty_cancelled": outstanding,
                "reason": reason,
            },
        )
        self.db.commit()
        return po_line

    def get_unreceived_lines(self, po_id: int) -> list[POLine]:
        """Return PO lines with qty_received < qty_ordered."""
        return (
            self.db.query(POLine)
            .filter(
                POLine.po_id == po_id,
                POLine.qty_received < POLine.qty_ordered,
            )
            .all()
        )

    # ── Billing (3-way match) ─────────────────────────────────────────────────

    def create_vendor_bill(
        self,
        po_id: int | None,
        vendor_id: int,
        bill_number: str,
        bill_date: datetime | None,
        due_date: datetime | None,
        lines: list[dict],  # [{po_line_id, qty_billed, unit_cost}]
    ) -> VendorBill:
        """
        Create a vendor bill from vendor invoice.
        Flags discrepancies: billed qty > received qty on any line.
        Auto-approves if no discrepancies.
        """
        bill = VendorBill(
            po_id=po_id,
            vendor_id=vendor_id,
            bill_number=bill_number,
            bill_date=bill_date,
            due_date=due_date,
            status=VendorBillStatus.PENDING,
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(bill)
        self.db.flush()

        has_discrepancy = False
        total = 0.0

        for line_data in lines:
            po_line_id = line_data.get("po_line_id")
            qty_billed = int(line_data.get("qty_billed", 0))
            unit_cost = float(line_data.get("unit_cost", 0.0))

            po_line = self.db.query(POLine).filter(POLine.id == po_line_id).first() if po_line_id else None

            bill_line = VendorBillLine(
                bill_id=bill.id,
                po_line_id=po_line_id,
                qty_billed=qty_billed,
                unit_cost=unit_cost,
            )
            self.db.add(bill_line)

            if po_line:
                po_line.qty_billed += qty_billed
                if qty_billed > po_line.qty_received:
                    has_discrepancy = True

            total += round(qty_billed * unit_cost, 2)

        bill.total_amount = round(total, 2)
        bill.status = VendorBillStatus.DISCREPANCY if has_discrepancy else VendorBillStatus.APPROVED

        if has_discrepancy:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() if po_id else None
            po_number = po.po_number if po else str(po_id)
            from app.services.notification_service import NotificationService
            NotificationService.build_bill_discrepancy(
                self.db,
                bill_id=bill.id,
                po_id=po_id or 0,
                po_number=po_number,
                detail="billed qty exceeds received qty on one or more lines",
            )
        elif po_id:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
            if po:
                self._advance_po_billed_if_done(po)

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id or 0,
            action=AuditAction.CREATED,
            new_value={"bill_number": bill_number, "total": total, "discrepancy": has_discrepancy},
        )
        self.db.commit()
        return bill

    def _advance_po_billed_if_done(self, po: PurchaseOrder) -> None:
        """Advance PO to BILLED when every received line has been fully billed."""
        if po.status not in (POStatus.RECEIVED, POStatus.PARTIAL):
            return
        received_lines = [ln for ln in po.lines if ln.qty_received > 0]
        if not received_lines:
            return
        if all(ln.qty_billed >= ln.qty_received for ln in received_lines):
            po.status = POStatus.BILLED

    def approve_bill(self, bill_id: int) -> None:
        """Approve vendor bill after discrepancy review. Marks for QBO sync."""
        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            raise ValueError(f"VendorBill {bill_id} not found")
        bill.status = VendorBillStatus.APPROVED
        bill.qbo_sync_status = QBOSyncStatus.PENDING
        if bill.po_id:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == bill.po_id).first()
            if po:
                self._advance_po_billed_if_done(po)
        self.db.commit()

    def get_bills_pending_approval(self) -> list[VendorBill]:
        return (
            self.db.query(VendorBill)
            .filter(VendorBill.status == VendorBillStatus.DISCREPANCY)
            .order_by(VendorBill.created_at)
            .all()
        )

    # ── QBO Sync ──────────────────────────────────────────────────────────────

    def mark_bill_synced(self, bill_id: int, qbo_id: str) -> None:
        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill:
            bill.qbo_id = qbo_id
            bill.qbo_sync_status = QBOSyncStatus.SYNCED
            bill.qbo_last_synced_at = datetime.utcnow()
            bill.qbo_sync_error = None
            self.db.commit()

    def mark_bill_sync_failed(self, bill_id: int, error: str) -> None:
        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill:
            bill.qbo_sync_status = QBOSyncStatus.ERROR
            bill.qbo_sync_error = error
            bill.qbo_sync_retry_count += 1
            self.db.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def save_header(self, po_id: int, data: dict, submitted_updated_at: str | None = None) -> None:
        """Autosave PO header fields. Blocked only on BILLED/CANCELLED."""
        po = self._get_po_or_404(po_id)
        self.check_version(po, submitted_updated_at)
        if po.status in (POStatus.BILLED, POStatus.CANCELLED):
            raise ValueError(f"Cannot edit a {po.status} PO")
        for field in ("notes", "internal_notes", "vendor_confirmation_number"):
            if field in data:
                setattr(po, field, data[field])
        if "freight_in_cost" in data:
            po.freight_in_cost = float(data["freight_in_cost"] or 0.0)
        if "expected_at" in data:
            po.expected_at = data["expected_at"]
        self.db.commit()

    def update_line(self, line_id: int, data: dict) -> POLine:
        """Update a line's description/qty/cost/core. Only on DRAFT or VERBAL_ORDER."""
        line = self.db.query(POLine).filter(POLine.id == line_id).first()
        if line is None:
            raise ValueError(f"Line {line_id} not found")
        po = self._get_po_or_404(line.po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot edit lines on a {po.status} PO")
        for field in ("description", "qty_ordered", "unit_cost", "core_charge_per_unit", "notes"):
            if field in data:
                setattr(line, field, data[field])
        self.db.commit()
        return line

    def delete_line(self, line_id: int) -> None:
        """Delete a line. Only on DRAFT or VERBAL_ORDER."""
        line = self.db.query(POLine).filter(POLine.id == line_id).first()
        if line is None:
            raise ValueError(f"Line {line_id} not found")
        po = self._get_po_or_404(line.po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot delete lines on a {po.status} PO")
        self.db.delete(line)
        self.db.commit()

    def _get_po_or_404(self, po_id: int) -> PurchaseOrder:
        po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        if po is None:
            raise ValueError(f"PurchaseOrder {po_id} not found")
        return po
