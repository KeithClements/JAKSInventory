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
    AuditAction, EntityType, InventoryTxnType,
    POStatus, QBOSyncStatus, VendorBillStatus,
)
from app.models.inventory import InventoryTransaction
from app.models.product import Product
from app.models.purchase_order import (
    POLine, POReceipt, POReceiptLine,
    PurchaseOrder, VendorBill, VendorBillLine,
)
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
        For each line:
          - Creates POReceiptLine
          - Writes InventoryTransaction (PO_RECEIPT) unless drop-ship
          - Updates POLine.qty_received and Product.qty_on_order/qty_on_hand cache
          - Calls ProductService.compare_and_record_cost_change() if cost differs
        Marks PO RECEIVED if all lines fully received.
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

        for po_line_id, qty in po_line_quantities.items():
            if qty <= 0:
                continue

            po_line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
            if po_line is None:
                raise ValueError(f"POLine {po_line_id} not found")

            po = po_line.po
            is_drop_ship = po.is_drop_ship

            # Record receipt line
            receipt_line = POReceiptLine(
                receipt_id=receipt.id,
                po_id=po_line.po_id,
                po_line_id=po_line_id,
                qty_received=qty,
            )
            self.db.add(receipt_line)

            # Update PO line received qty
            po_line.qty_received += qty

            # Update product inventory cache + ledger (stock receipts only)
            if not is_drop_ship and po_line.product_id:
                product = self.db.query(Product).filter(Product.id == po_line.product_id).first()
                if product:
                    qty_before = product.qty_on_hand
                    product.qty_on_hand += qty
                    product.qty_on_order = max(0, product.qty_on_order - qty)

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

            # Mark PO status: PARTIAL or RECEIVED
            self.db.flush()
            po_line_fresh = self.db.query(POLine).filter(POLine.id == po_line_id).first()
            all_received = all(ln.qty_received >= ln.qty_ordered for ln in po.lines)
            po.status = POStatus.RECEIVED if all_received else POStatus.PARTIAL

        self.audit(
            entity_type=EntityType.PO_RECEIPT,
            entity_id=receipt.id,
            action=AuditAction.CREATED,
            new_value={"vendor_id": vendor_id, "lines": list(po_line_quantities.keys())},
        )
        self.db.commit()
        return receipt

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

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id or 0,
            action=AuditAction.CREATED,
            new_value={"bill_number": bill_number, "total": total, "discrepancy": has_discrepancy},
        )
        self.db.commit()
        return bill

    def approve_bill(self, bill_id: int) -> None:
        """Approve vendor bill after discrepancy review. Marks for QBO sync."""
        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            raise ValueError(f"VendorBill {bill_id} not found")
        bill.status = VendorBillStatus.APPROVED
        bill.qbo_sync_status = QBOSyncStatus.PENDING
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

    def _get_po_or_404(self, po_id: int) -> PurchaseOrder:
        po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        if po is None:
            raise ValueError(f"PurchaseOrder {po_id} not found")
        return po
