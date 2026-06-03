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

from datetime import datetime, timezone

from app.constants import (
    AuditAction, EntityType, FulfillmentSource, InventoryTxnType, MatchResolution,
    Permission, POStatus, QBOSyncStatus, SOLineSource, SOLineStatus, VendorBillStatus,
    VendorCreditMemoTrigger,
)
from app.models.inventory import InventoryTransaction
from app.models.product import Product
from app.models.purchase_order import (
    COST_VARIANCE_TOLERANCE,
    POLine, POReceipt, POReceiptLine,
    PurchaseOrder, VendorBill, VendorBillLine,
)
from app.models.quote import SOLine
from app.settings_utils import bump_counter
from app.services.base import BaseService, apply_product_line_defaults
from app.services.product_service import ProductService


class POService(BaseService):

    # ── 3-Way Match: pure computation (no DB writes) ──────────────────────────
    # Kept here (not the router) so the resolution methods below can call them
    # without creating a circular import.  The router delegates to these.

    # Line states that require AP review.
    _MATCH_FLAG_STATES = frozenset({"over_billed", "cost_variance"})

    # Terminal resolutions that clear the is_flag display on a line.
    _TERMINAL_RESOLUTIONS = frozenset({
        MatchResolution.ACCEPTED,
        MatchResolution.CREDITED,
        MatchResolution.CLEARED,
    })

    # Decisions that require a non-empty reason.
    _REASON_REQUIRED = frozenset({MatchResolution.REJECTED, MatchResolution.CLEARED})

    @staticmethod
    def _billed_unit_cost(line: POLine) -> float | None:
        """Average billed unit cost across all bill_lines (None if unbilled)."""
        qty = sum(bl.qty_billed for bl in line.bill_lines)
        if qty <= 0:
            return None
        amount = sum(bl.qty_billed * bl.unit_cost for bl in line.bill_lines)
        return round(amount / qty, 2)

    @classmethod
    def compute_match_line(cls, line: POLine) -> dict:
        """
        Compute the match state for a single PO line.

        Stable row-dict keys (UI contract — lane/ui-builder depends on these):
          line, ordered_qty, ordered_cost, received_qty, billed_qty, billed_cost,
          qty_var, cost_var, state, is_flag,
          resolution, resolution_reason, resolved_by_id, resolved_at,
          resolution_vcm_id, suggested_credit, can_resolve
        """
        open_qty = max(0, line.qty_ordered - line.qty_cancelled)
        billed_cost = cls._billed_unit_cost(line)
        cost_var = round((billed_cost - line.unit_cost), 2) if billed_cost is not None else 0.0

        if line.qty_billed > line.qty_received:
            raw_state = "over_billed"
        elif billed_cost is not None and abs(cost_var) >= COST_VARIANCE_TOLERANCE:
            raw_state = "cost_variance"
        elif line.qty_received < open_qty:
            raw_state = "awaiting_receipt"
        elif line.qty_billed < line.qty_received:
            raw_state = "awaiting_bill"
        else:
            raw_state = "matched"

        resolution = line.match_resolution

        # A flag is live only when raw state is a variance AND AP hasn't resolved
        # it terminally.  on_hold de-prioritises (is_flag=False).
        if raw_state in cls._MATCH_FLAG_STATES:
            if resolution in cls._TERMINAL_RESOLUTIONS:
                state = f"resolved_{resolution}"   # e.g. "resolved_accepted"
                is_flag = False
            elif resolution == MatchResolution.ON_HOLD:
                state = "on_hold"
                is_flag = False
            elif resolution == MatchResolution.REJECTED:
                state = "rejected"
                is_flag = True   # still needs AP action (waiting corrected bill)
            else:
                state = raw_state
                is_flag = True
        else:
            state = raw_state
            is_flag = False

        # Suggested credit amount for the Create-Credit shortcut in the UI
        suggested_credit: float | None = None
        if raw_state == "over_billed" and billed_cost is not None:
            qty_var = line.qty_billed - line.qty_received
            suggested_credit = round(qty_var * billed_cost, 2)
        elif raw_state == "cost_variance" and cost_var > 0 and billed_cost is not None:
            suggested_credit = round(cost_var * line.qty_billed, 2)

        return {
            "line":              line,
            "ordered_qty":       line.qty_ordered,
            "ordered_cost":      line.unit_cost,
            "received_qty":      line.qty_received,
            "billed_qty":        line.qty_billed,
            "billed_cost":       billed_cost,
            "qty_var":           line.qty_billed - line.qty_received,
            "cost_var":          cost_var,
            "state":             state,
            "is_flag":           is_flag,
            # resolution metadata
            "resolution":        resolution,
            "resolution_reason": line.match_resolution_reason,
            "resolved_by_id":    line.match_resolved_by_id,
            "resolved_at":       line.match_resolved_at,
            "resolution_vcm_id": line.match_resolution_vcm_id,
            "suggested_credit":  suggested_credit,
            "can_resolve":       raw_state in cls._MATCH_FLAG_STATES,
        }

    @classmethod
    def compute_match_summary(cls, po: PurchaseOrder) -> dict:
        """Compute match summary for all lines on a PO."""
        rows = [cls.compute_match_line(ln) for ln in po.lines]
        flagged = [r for r in rows if r["is_flag"]]
        has_activity = any(r["received_qty"] or r["billed_qty"] for r in rows)
        return {
            "rows":          rows,
            "flag_count":    len(flagged),
            "has_activity":  has_activity,
            "matched_count": sum(1 for r in rows if r["state"] == "matched"),
        }

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

        product = None
        if product_id is not None:
            product = self.db.query(Product).filter(Product.id == product_id).first()
            # Auto-fill cost from THIS PO's vendor source (vendor-specific cost)
            if unit_cost == 0.0:
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

        # Shared product-derived defaults: description (+ cost if still 0). POs are
        # cost-only, so include_price=False. Core charge backfills from the product.
        data["unit_cost"] = unit_cost
        apply_product_line_defaults(product, data, include_price=False)
        core_charge = float(data.get("core_charge_per_unit", 0.0) or 0.0)
        if product is not None and core_charge == 0.0 and product.vendor_core_charge:
            core_charge = product.vendor_core_charge

        line = POLine(
            po_id=po_id,
            product_id=product_id,
            description=data.get("description", ""),
            qty_ordered=int(data.get("qty_ordered", 1)),
            unit_cost=float(data.get("unit_cost", 0.0)),
            core_charge_per_unit=core_charge,
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

            # Advance line_status when fully met. Once the ordered qty is reserved
            # the line is no longer a backorder — clear the legacy `source` flag so
            # is_backordered / SO.has_backorder stop reporting it as outstanding.
            if so_line.qty_committed >= so_line.qty_ordered - so_line.qty_invoiced:
                so_line.line_status = SOLineStatus.RESERVED_STOCK
                so_line.source = SOLineSource.STOCK
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
        Flags discrepancies on any line: billed qty > received qty, cumulative
        billed qty > PO-ordered qty (D-4b — over-receipt can't authorise paying
        beyond the order), or billed unit cost varying from the PO/receipt cost.
        Auto-approves only when no discrepancies.
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

        has_qty_over_received = False
        has_qty_over_ordered = False
        has_cost_discrepancy = False
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
                    has_qty_over_received = True
                # D-4b — over-receipt (R6, allow-with-warning) can lift
                # qty_received above the PO ceiling, so the received check alone
                # lets a bill over-pay what the PO authorized. Compare the
                # CUMULATIVE billed qty (after the += above) against qty_ordered —
                # cumulative, not this-bill, so split bills can't each slip under
                # the ceiling while together they over-bill the order.
                if po_line.qty_billed > po_line.qty_ordered:
                    has_qty_over_ordered = True
                # Money bug fix — a billed unit cost that differs from the PO/
                # receipt cost beyond tolerance is a discrepancy too (vendor
                # overcharge/undercharge). Must NOT silently auto-approve.
                if abs(unit_cost - po_line.unit_cost) >= COST_VARIANCE_TOLERANCE:
                    has_cost_discrepancy = True

            total += round(qty_billed * unit_cost, 2)

        has_qty_discrepancy = has_qty_over_received or has_qty_over_ordered
        has_discrepancy = has_qty_discrepancy or has_cost_discrepancy
        bill.total_amount = round(total, 2)
        bill.status = VendorBillStatus.DISCREPANCY if has_discrepancy else VendorBillStatus.APPROVED

        if has_discrepancy:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() if po_id else None
            po_number = po.po_number if po else str(po_id)
            # Describe whichever discrepancy/-ies fired so AP knows what to review.
            _reasons = []
            if has_qty_over_received:
                _reasons.append("billed qty exceeds received qty")
            if has_qty_over_ordered:
                _reasons.append("billed qty exceeds PO-ordered qty")
            if has_cost_discrepancy:
                _reasons.append("billed unit cost differs from PO/receipt cost")
            from app.services.notification_service import NotificationService
            NotificationService.build_bill_discrepancy(
                self.db,
                bill_id=bill.id,
                po_id=po_id or 0,
                po_number=po_number,
                detail=" and ".join(_reasons) + " on one or more lines",
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
        """
        Approve vendor bill after discrepancy review. Marks for QBO sync.

        Gate rules:
        - Requires APPROVE_VENDOR_BILL permission.
        - Bills in DISCREPANCY status cannot be approved: all flagged match lines
          must first be resolved via resolve_match_line() or create_match_vendor_credit(),
          which transitions the bill DISCREPANCY → PENDING when the gate opens.
        - Resolving lines opens the gate; it does NOT approve the bill.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)

        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            raise ValueError(f"VendorBill {bill_id} not found")
        if bill.status == VendorBillStatus.DISCREPANCY:
            raise ValueError(
                "This bill has unresolved match discrepancies. "
                "Resolve each flagged line (accept, reject, credit, or clear) "
                "before approving."
            )
        if bill.status in (VendorBillStatus.APPROVED, VendorBillStatus.PAID):
            raise ValueError(f"Bill is already {bill.status}.")

        bill.status = VendorBillStatus.APPROVED
        bill.qbo_sync_status = QBOSyncStatus.PENDING
        if bill.po_id:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == bill.po_id).first()
            if po:
                self._advance_po_billed_if_done(po)

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=bill.po_id or 0,
            action=AuditAction.STATUS_CHANGED,
            new_value={"bill_id": bill_id, "status": VendorBillStatus.APPROVED},
        )
        self.db.commit()

    # ── 3-Way Match: AP resolution ────────────────────────────────────────────

    def resolve_match_line(
        self,
        po_line_id: int,
        decision: str,           # MatchResolution value
        reason: str = "",
    ) -> POLine:
        """
        Record an AP resolution decision on a flagged PO line.

        Valid decisions: accepted, rejected, on_hold, cleared.
        (Use create_match_vendor_credit for the 'credited' path — it sets
        match_resolution=CREDITED after creating the VCM.)

        Rules:
        - Requires Permission.APPROVE_VENDOR_BILL.
        - 'rejected' and 'cleared' require a non-empty reason.
        - Cannot set to 'credited' directly; use create_match_vendor_credit.
        - Re-deciding an already-resolved line is allowed (AP may change mind
          as long as the bill hasn't been approved yet).
        - If resolving leaves no unresolved flags on the associated bill and
          that bill is DISCREPANCY, opens the bill for explicit AP approval
          (does NOT auto-approve — _advance_bill_after_match only drops the
          block; AP still clicks Approve).
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)

        valid_direct = {
            MatchResolution.ACCEPTED,
            MatchResolution.REJECTED,
            MatchResolution.ON_HOLD,
            MatchResolution.CLEARED,
        }
        if decision not in valid_direct:
            raise ValueError(
                f"Invalid resolution '{decision}'. "
                f"Use one of {sorted(valid_direct)}. "
                f"For vendor credit, call create_match_vendor_credit()."
            )
        if decision in self._REASON_REQUIRED and not reason.strip():
            raise ValueError(f"A reason is required when resolution is '{decision}'.")

        line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
        if line is None:
            raise ValueError(f"POLine {po_line_id} not found")

        po = line.po
        old_resolution = line.match_resolution

        # Capture the raw match state before we write (for audit old_value)
        raw = self.compute_match_line(line)

        line.match_resolution = decision
        line.match_resolution_reason = reason.strip() or None
        line.match_resolved_by_id = self.current_user_id
        line.match_resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.MATCH_RESOLVED,
            old_value={
                "resolution": old_resolution,
                "raw_state": raw["state"],
                "qty_var": raw["qty_var"],
                "cost_var": raw["cost_var"],
            },
            new_value={
                "po_line_id": po_line_id,
                "resolution": decision,
                "reason": reason.strip() or None,
            },
            notes=reason.strip() or None,
        )

        self.db.flush()

        # Check whether all flagged lines on associated DISCREPANCY bills are
        # now resolved so the explicit-approve gate can open.
        for bill in po.bills:
            if bill.status == VendorBillStatus.DISCREPANCY:
                self._advance_bill_after_match(bill)

        self.db.commit()
        return line

    def create_match_vendor_credit(
        self,
        po_line_id: int,
        amount: float | None = None,
        trigger: str = VendorCreditMemoTrigger.OVERCHARGE,
        reason: str = "",
        apply_now: bool = False,
    ) -> object:
        """
        Create a VendorCreditMemo for a flagged PO line and mark the line
        as match_resolution=CREDITED.

        Requires Permission.APPROVE_VENDOR_BILL + Permission.ISSUE_CREDIT_MEMO.

        If amount is None, computes the suggested overage:
          - over_billed: (qty_billed - qty_received) × billed_unit_cost
          - cost_variance (vendor overcharged): (billed_cost - ordered_cost) × qty_billed
        Caller may pass an explicit amount to override.

        If apply_now=True, immediately allocates the VCM against the first
        DISCREPANCY bill on the PO (requires a bill to exist).

        Returns the created VendorCreditMemo.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)
        self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
        if line is None:
            raise ValueError(f"POLine {po_line_id} not found")

        po = line.po

        # Compute suggested amount if not supplied
        raw = self.compute_match_line(line)

        if amount is None:
            if raw["state"] == "over_billed":
                billed_cost = raw["billed_cost"] or 0.0
                amount = round((raw["qty_var"]) * billed_cost, 2)
            elif raw["state"] == "cost_variance" and raw["cost_var"] > 0:
                amount = round(raw["cost_var"] * raw["billed_qty"], 2)
            else:
                raise ValueError(
                    "Cannot auto-compute credit amount: line is not over_billed or "
                    "cost_variance (vendor-overcharged). Pass an explicit amount."
                )

        if amount <= 0:
            raise ValueError("Vendor credit amount must be positive.")

        # Find the related bill for back-reference + optional apply_now
        related_bill = next(
            (b for b in po.bills if b.status == VendorBillStatus.DISCREPANCY),
            None,
        )
        original_bill_id = related_bill.id if related_bill else None

        from app.services.vendor_credit_service import VendorCreditService
        vcm_svc = VendorCreditService(self.db, self.current_user_id)
        vcm = vcm_svc.create_vendor_credit_memo(
            vendor_id=po.vendor_id,
            trigger_type=trigger,
            amount=amount,
            original_vendor_bill_id=original_bill_id,
            reason=reason or f"Match variance on PO {po.po_number}, line {po_line_id}",
        )

        if apply_now and related_bill is not None:
            vcm_svc.apply_vendor_credit_memo(
                vcm_id=vcm.id,
                vendor_bill_id=related_bill.id,
                amount=amount,
            )

        # Mark the line as credited and link the VCM
        old_resolution = line.match_resolution
        line.match_resolution = MatchResolution.CREDITED
        line.match_resolution_vcm_id = vcm.id
        line.match_resolution_reason = reason.strip() or None
        line.match_resolved_by_id = self.current_user_id
        line.match_resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.MATCH_RESOLVED,
            old_value={
                "resolution": old_resolution,
                "raw_state": raw["state"],
                "qty_var": raw["qty_var"],
                "cost_var": raw["cost_var"],
            },
            new_value={
                "po_line_id": po_line_id,
                "resolution": MatchResolution.CREDITED,
                "vcm_id": vcm.id,
                "vcm_number": vcm.vcm_number,
                "amount": amount,
                "apply_now": apply_now,
            },
            notes=reason.strip() or f"VCM {vcm.vcm_number} created for match variance",
        )

        self.db.flush()

        for bill in po.bills:
            if bill.status == VendorBillStatus.DISCREPANCY:
                self._advance_bill_after_match(bill)

        self.db.commit()
        return vcm

    def _advance_bill_after_match(self, bill: VendorBill) -> None:
        """
        Open the explicit-approve gate when every flagged line on a DISCREPANCY
        bill has been resolved to a terminal or on_hold state.

        Does NOT auto-approve. AP still calls approve_bill() as the final step.
        Transitions bill DISCREPANCY → PENDING (ready-to-approve) only.

        Terminal: accepted, credited, cleared.
        on_hold counts as resolved for gating purposes (AP has parked it).
        rejected: AP is still disputing — bill stays DISCREPANCY.
        unresolved: flag still live — bill stays DISCREPANCY.
        """
        if bill.status != VendorBillStatus.DISCREPANCY:
            return

        # Collect all PO lines whose bill_lines are on this bill
        flagged_line_ids = {
            bl.po_line_id for bl in bill.lines if bl.po_line_id is not None
        }
        if not flagged_line_ids:
            return

        po_lines = (
            self.db.query(POLine)
            .filter(POLine.id.in_(flagged_line_ids))
            .all()
        )

        gate_resolutions = self._TERMINAL_RESOLUTIONS | {MatchResolution.ON_HOLD}
        all_gated = all(ln.match_resolution in gate_resolutions for ln in po_lines)

        if all_gated:
            # Transition to PENDING so approve_bill() becomes available
            bill.status = VendorBillStatus.PENDING
            self.db.flush()

    def get_bills_pending_approval(self) -> list[VendorBill]:
        return (
            self.db.query(VendorBill)
            .filter(VendorBill.status.in_([
                VendorBillStatus.DISCREPANCY,
                VendorBillStatus.PENDING,
            ]))
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
