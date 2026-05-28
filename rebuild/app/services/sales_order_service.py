"""
app/services/sales_order_service.py
=====================================
Sales Order management and partial fulfillment tracking.

OWNERSHIP:
  SalesOrderService owns SalesOrder and SOLine mutations.
  Inventory commitment: SalesOrderService writes SO_COMMITTED / SO_RELEASED
    transactions and mutates Product.qty_committed.
  Invoice creation: delegates to InvoiceService.create_invoice() + finalise().
    InvoiceService.finalise() owns the INVOICE_SALE transaction and qty_on_hand
    deduction — SalesOrderService must not write those directly.
  Deposit collection: delegates to PaymentService.record_payment().

Key rules:
  - SO number: SO-[YEAR]-[NNNN], resets yearly
  - Payment modes: full | deposit | none
      full:    collect full payment now via collect_deposit(); invoice on fulfillment
      deposit: collect partial now; balance billed on invoice
      none:    net terms — no payment at SO stage
  - Committed inventory: SO_COMMITTED reduces qty_available without touching
    qty_on_hand.  SO_RELEASED restores availability on cancel or fulfillment.
  - Partial fulfillment: qty_invoiced tracks progress; SO status = PARTIAL until
    all lines are fully invoiced.
  - Backorder: SOLine.source = BACKORDER when qty_remaining > 0 after partial invoice.
  - Hold: status = HOLD blocks fulfillment without releasing committed inventory.
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, EntityType, FulfillmentSource, InventoryTxnType,
    LineType, Permission, SOLineSource, SOLineStatus, SOPaymentMode, SOStatus,
)
from app.models.inventory import InventoryTransaction
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.settings_utils import bump_counter
from app.services.base import BaseService


class SalesOrderService(BaseService):

    # ── SO CRUD ───────────────────────────────────────────────────────────────

    def create_sales_order(
        self,
        customer_id: int,
        payment_mode: str,
        data: dict,
        quote_id: int | None = None,
    ) -> SalesOrder:
        """
        Create a new sales order. Generates SO-YEAR-NNNN.
        Writes SO_COMMITTED inventory transactions for each product line.
        Links to originating quote if quote_id supplied.
        """
        year = datetime.utcnow().year
        so_number = bump_counter(self.db, "next_so_number", "SO", year)

        so = SalesOrder(
            so_number=so_number,
            customer_id=customer_id,
            quote_id=quote_id,
            status=SOStatus.OPEN,
            payment_mode=payment_mode,
            deposit_amount=0.0,
            customer_po_number=data.get("customer_po_number"),
            customer_job_number=data.get("customer_job_number"),
            esn=data.get("esn"),
            engine_manufacturer=data.get("engine_manufacturer", ""),
            engine_model=data.get("engine_model", ""),
            notes=data.get("notes", ""),
            internal_notes=data.get("internal_notes", ""),
        )
        self.db.add(so)
        self.db.flush()

        sort_order = 0
        for line_data in data.get("lines", []):
            self._add_line_internal(so.id, line_data, sort_order)
            sort_order += 1

        self.audit(
            entity_type=EntityType.SALES_ORDER,
            entity_id=so.id,
            action=AuditAction.CREATED,
            new_value={
                "so_number": so_number,
                "customer_id": customer_id,
                "quote_id": quote_id,
                "payment_mode": payment_mode,
            },
        )
        self.db.commit()
        return so

    def add_line(
        self,
        so_id: int,
        product_id: int | None,
        data: dict,
        allow_negative_inventory: bool = False,
    ) -> SOLine:
        """
        Add a line to an open or partial SO.

        Determines fulfillment_source automatically when not provided in data:
          - If product has enough qty_available → STOCK (auto-commit)
          - Otherwise → BACKORDER (qty_backordered++, qty_committed stays 0)

        Override `data["fulfillment_source"]` to force LINKED_PO / SPECIAL_ORDER /
        DROPSHIP. When LINKED_PO is set, data["linked_po_line_id"] is required.

        R6 — Negative inventory hard block: if fulfillment_source=STOCK and
        requested qty exceeds qty_available, this raises unless caller has
        NEGATIVE_INVENTORY_OVERRIDE permission AND passes allow_negative_inventory=True.
        Writes SO_COMMITTED inventory transaction for committed product lines.
        """
        so = self._get_so_or_404(so_id)
        if so.status in (SOStatus.CANCELLED, SOStatus.INVOICED):
            raise ValueError(f"Cannot add lines to a {so.status} sales order")

        sort_order = max((ln.sort_order for ln in so.lines), default=-1) + 1
        line = self._add_line_internal(
            so.id,
            {**data, "product_id": product_id},
            sort_order,
            allow_negative_inventory=allow_negative_inventory,
        )
        self.db.commit()
        return line

    def update_line(self, line_id: int, data: dict) -> SOLine:
        """
        Update line qty or price. Adjusts SO_COMMITTED / SO_RELEASED inventory
        delta when qty_ordered changes.
        """
        line = self.db.query(SOLine).filter(SOLine.id == line_id).first()
        if line is None:
            raise ValueError(f"SOLine {line_id} not found")
        so = self._get_so_or_404(line.so_id)
        if so.status in (SOStatus.CANCELLED, SOStatus.INVOICED):
            raise ValueError(f"Cannot edit lines on a {so.status} sales order")

        if "qty_ordered" in data and line.line_type == LineType.PRODUCT and line.product_id:
            new_qty_ordered = int(data["qty_ordered"])
            delta = new_qty_ordered - line.qty_ordered
            if delta != 0:
                product = self.db.query(Product).filter(Product.id == line.product_id).first()
                if product:
                    if delta > 0:
                        # Committing additional qty
                        product.qty_committed += delta
                        line.qty_committed += delta
                        self._write_so_txn(
                            product_id=product.id,
                            txn_type=InventoryTxnType.SO_COMMITTED,
                            qty_change=-delta,  # negative = reserved out of available
                            qty_after=product.qty_on_hand,
                            so_id=so.id,
                            notes=f"SO {so.so_number} line qty increase",
                        )
                    else:
                        # Releasing excess commitment (delta is negative)
                        release = min(abs(delta), line.qty_committed)
                        product.qty_committed = max(0, product.qty_committed - release)
                        line.qty_committed = max(0, line.qty_committed - release)
                        self._write_so_txn(
                            product_id=product.id,
                            txn_type=InventoryTxnType.SO_RELEASED,
                            qty_change=release,  # positive = back to available
                            qty_after=product.qty_on_hand,
                            so_id=so.id,
                            notes=f"SO {so.so_number} line qty decrease",
                        )

        updatable = [
            "qty_ordered", "unit_price", "unit_cost",
            "discount_pct", "sort_order", "description",
        ]
        for field in updatable:
            if field in data:
                setattr(line, field, data[field])
        self.db.commit()
        return line

    def cancel_line(self, line_id: int) -> None:
        """
        Cancel a single line — releases its committed inventory.
        Freezes qty_ordered at qty_invoiced so the line is closed.
        """
        line = self.db.query(SOLine).filter(SOLine.id == line_id).first()
        if line is None:
            raise ValueError(f"SOLine {line_id} not found")
        so = self._get_so_or_404(line.so_id)
        if so.status == SOStatus.CANCELLED:
            raise ValueError(f"Sales order {so.so_number} is already cancelled")

        self._release_line_commitment(line, so.so_number)
        # Freeze at what's already invoiced — remaining qty is gone
        line.qty_ordered = line.qty_invoiced
        self.db.commit()

    # ── Fulfillment ───────────────────────────────────────────────────────────

    def fulfill_and_invoice(
        self,
        so_id: int,
        line_quantities: dict[int, int],  # {so_line_id: qty_to_invoice}
    ) -> object:
        """
        Create an invoice for the specified SO line quantities.

        Sequence (R3, R7):
          1. For each line: validate qty, release committed inventory (SO_RELEASED),
             update qty_fulfilled / qty_invoiced, advance line_status.
          2. Delegate to InvoiceService.create_invoice() — commits invoice + SO changes.
          3. Delegate to InvoiceService.finalise() — writes INVOICE_SALE, deducts
             qty_on_hand, sets invoice status = OPEN.
          4. R3 — Auto-allocate any unapplied SO deposit Payments to the new invoice.
             For partial fulfillment, deposits allocate proportionally based on the
             fraction of SO value being invoiced now.
          5. Update SO status and commit.

        InvoiceService is the sole owner of invoice.status and INVOICE_SALE writes.
        SalesOrderService must not touch those fields directly.
        """
        from app.services.invoice_service import InvoiceService
        from app.services.payment_service import PaymentService

        so = self._get_so_or_404(so_id)
        if so.status == SOStatus.CANCELLED:
            raise ValueError(f"Sales order {so.so_number} is cancelled")
        if so.status == SOStatus.INVOICED:
            raise ValueError(f"Sales order {so.so_number} is already fully invoiced")
        if so.status == SOStatus.HOLD:
            raise ValueError(f"Sales order {so.so_number} is on hold — release hold before invoicing")

        # Snapshot SO subtotal BEFORE fulfillment for proportional deposit allocation
        so_total_before = sum(
            (ln.unit_price * (1 - (ln.discount_pct or 0) / 100)) * ln.qty_ordered
            for ln in so.lines
            if ln.line_type == LineType.PRODUCT
        )

        inv_lines: list[dict] = []
        fulfilled_value = 0.0  # value of THIS fulfillment (for proportional deposit alloc)

        for so_line_id, qty in line_quantities.items():
            if qty <= 0:
                continue

            line = (
                self.db.query(SOLine)
                .filter(SOLine.id == so_line_id, SOLine.so_id == so_id)
                .first()
            )
            if line is None:
                raise ValueError(f"SOLine {so_line_id} not found on SO {so_id}")
            if qty > line.qty_remaining:
                raise ValueError(
                    f"SOLine {so_line_id}: requested {qty}, "
                    f"only {line.qty_remaining} remaining"
                )

            # Release the committed reservation for this qty
            if line.line_type == LineType.PRODUCT and line.product_id:
                product = self.db.query(Product).filter(Product.id == line.product_id).first()
                if product:
                    release = min(qty, line.qty_committed)
                    if release > 0:
                        product.qty_committed = max(0, product.qty_committed - release)
                        line.qty_committed = max(0, line.qty_committed - release)
                        self._write_so_txn(
                            product_id=product.id,
                            txn_type=InventoryTxnType.SO_RELEASED,
                            qty_change=release,   # positive = available again
                            qty_after=product.qty_on_hand,
                            so_id=so.id,
                            notes=f"Fulfill SO {so.so_number} → invoice",
                        )

            inv_lines.append({
                "product_id": line.product_id,
                "so_line_id": line.id,
                "line_type": line.line_type,
                "description": line.description,
                "qty": qty,
                "unit_price": line.unit_price,
                "unit_cost": line.unit_cost,
                "discount_pct": line.discount_pct,
            })

            line.qty_fulfilled += qty
            line.qty_invoiced += qty

            # Accumulate value being fulfilled (for proportional deposit allocation)
            if line.line_type == LineType.PRODUCT:
                fulfilled_value += line.unit_price * (1 - (line.discount_pct or 0) / 100) * qty

            # R7 — advance line_status
            if line.qty_invoiced >= line.qty_ordered:
                line.line_status = SOLineStatus.INVOICED
            else:
                # partial: dropship lines stay SHIPPED_DIRECT, else BACKORDER
                if line.fulfillment_source == FulfillmentSource.DROPSHIP:
                    line.line_status = SOLineStatus.SHIPPED_DIRECT
                else:
                    line.source = SOLineSource.BACKORDER  # legacy
                    line.line_status = SOLineStatus.AWAITING_STOCK

        if not inv_lines:
            raise ValueError("No lines selected for invoicing")

        # Delegate invoice creation — create_invoice() commits (includes SO_RELEASED
        # txns and SOLine qty updates that were flushed above).
        inv_svc = InvoiceService(self.db, self.current_user_id)
        invoice = inv_svc.create_invoice(
            customer_id=so.customer_id,
            data={
                "customer_po_number": so.customer_po_number,
                "customer_job_number": so.customer_job_number,
                "esn": so.esn,
                "engine_manufacturer": so.engine_manufacturer or "",
                "engine_model": so.engine_model or "",
                "notes": so.notes,
                "internal_notes": so.internal_notes,
            },
            so_id=so_id,
            lines=inv_lines,
        )
        # InvoiceService.finalise() writes INVOICE_SALE txns, decrements qty_on_hand,
        # sets invoice.status = OPEN.  Must not be called after a failed create_invoice.
        inv_svc.finalise(invoice.id)

        # R3 — auto-allocate SO deposits to this invoice
        self._allocate_so_deposits_to_invoice(
            so=so,
            invoice=invoice,
            fulfilled_value=fulfilled_value,
            so_total_before=so_total_before,
            payment_svc=PaymentService(self.db, self.current_user_id),
        )

        # Reload SO from DB so is_fully_invoiced sees updated qty_invoiced values.
        self.db.expire(so)
        old_status = so.status
        so.status = SOStatus.INVOICED if so.is_fully_invoiced else SOStatus.PARTIAL

        self.audit(
            entity_type=EntityType.SALES_ORDER,
            entity_id=so_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=so.status,
            notes=f"invoice {invoice.invoice_number} created",
        )
        self.db.commit()
        return invoice

    def _allocate_so_deposits_to_invoice(
        self,
        so: SalesOrder,
        invoice,
        fulfilled_value: float,
        so_total_before: float,
        payment_svc,
    ) -> float:
        """
        R3 — Auto-allocate unapplied SO Payments to the new invoice.

        Allocation rules:
          - Full fulfillment (only one invoice expected): apply ALL unapplied SO
            deposits up to invoice balance.
          - Partial fulfillment: apply a PROPORTIONAL share of each unapplied
            deposit based on the fraction of SO value being fulfilled now.
            (fulfilled_value / so_total_before)
          - Capped at invoice.balance_due so we never over-allocate.

        Triggers invoice status refresh (PAID / PARTIAL / OPEN) via PaymentService.
        Returns total allocated.
        """
        from app.models.invoice import Payment

        unapplied_so_payments = (
            self.db.query(Payment)
            .filter(
                Payment.sales_order_id == so.id,
                Payment.status == "applied",  # PaymentStatus.APPLIED
            )
            .order_by(Payment.payment_date)  # oldest first
            .all()
        )
        unapplied_so_payments = [p for p in unapplied_so_payments if p.amount_unallocated > 0.001]
        if not unapplied_so_payments:
            return 0.0

        # Determine proportional fraction for partial fulfillment.
        # Each invoice's fair share = (its_value / SO_total) × original_deposit_amount
        # — NOT × current_unapplied, which would compound the fraction incorrectly
        # across multiple partial fulfillments.
        if so_total_before > 0 and fulfilled_value < so_total_before - 0.001:
            fraction = fulfilled_value / so_total_before
        else:
            fraction = 1.0  # full fulfillment (or only batch)

        allocated_total = 0.0
        for payment in unapplied_so_payments:
            # Refresh invoice balance each iteration (prior allocation reduced it)
            self.db.expire(invoice)
            balance = invoice.balance_due
            if balance <= 0.001:
                break

            # Fair-share target = fraction × ORIGINAL amount_received.
            # Capped by current unapplied balance (can't double-allocate) and
            # by invoice balance (can't over-pay).
            target = payment.amount_received * fraction
            apply = min(target, payment.amount_unallocated, balance)
            if apply <= 0.001:
                continue
            payment_svc.allocate(payment.id, invoice.id, round(apply, 2))
            allocated_total += apply

        return round(allocated_total, 2)

    def get_open_lines(self, so_id: int) -> list[SOLine]:
        """Return SO lines with qty_remaining > 0."""
        so = self._get_so_or_404(so_id)
        return [ln for ln in so.lines if ln.qty_remaining > 0]

    # ── Status Transitions ────────────────────────────────────────────────────

    def cancel_order(
        self,
        so_id: int,
        reason: str,
        deposit_resolution: str | None = None,
    ) -> None:
        """
        Cancel entire SO. Releases all committed inventory (SO_RELEASED).
        Cannot cancel a fully invoiced SO.

        R3 — If the SO has collected unapplied deposits, caller MUST specify
        deposit_resolution:
          - "refund"        — create REFUND_TO_CUSTOMER Payment for each deposit
          - "credit"        — convert unapplied deposit balance to customer.credit_balance
          - "leave_open"    — leave deposit Payments unapplied (manual follow-up)
        Cancellation is rejected if deposits exist and no resolution is given.
        """
        from app.models.invoice import Payment

        so = self._get_so_or_404(so_id)
        if so.status == SOStatus.CANCELLED:
            raise ValueError(f"Sales order {so.so_number} is already cancelled")
        if so.status == SOStatus.INVOICED:
            raise ValueError(
                f"Sales order {so.so_number} is fully invoiced and cannot be cancelled. "
                "Void the invoice instead."
            )

        # R3 — find unapplied SO deposits and require resolution
        unapplied_deposits = [
            p for p in self.db.query(Payment)
            .filter(Payment.sales_order_id == so_id, Payment.status == "applied")
            .all()
            if p.amount_unallocated > 0.001
        ]
        if unapplied_deposits and not deposit_resolution:
            total_unapplied = sum(p.amount_unallocated for p in unapplied_deposits)
            raise ValueError(
                f"SO {so.so_number} has ${total_unapplied:.2f} in unapplied deposits. "
                f"Cancellation requires a deposit_resolution: 'refund' | 'credit' | 'leave_open'."
            )
        if unapplied_deposits:
            self._resolve_cancelled_deposits(
                so=so,
                unapplied_deposits=unapplied_deposits,
                resolution=deposit_resolution,
                reason=reason,
            )

        for line in so.lines:
            self._release_line_commitment(line, so.so_number)
            line.line_status = SOLineStatus.CANCELLED

        old_status = so.status
        so.status = SOStatus.CANCELLED
        self.audit(
            entity_type=EntityType.SALES_ORDER,
            entity_id=so_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=SOStatus.CANCELLED,
            notes=f"{reason}" + (f" (deposit_resolution={deposit_resolution})" if deposit_resolution else ""),
        )
        self.db.commit()

    def _resolve_cancelled_deposits(
        self,
        so: SalesOrder,
        unapplied_deposits: list,
        resolution: str,
        reason: str,
    ) -> None:
        """R3 — handle the unapplied SO deposits when cancelling."""
        from app.constants import PaymentDirection
        from app.services.crm_service import CRMService

        valid = {"refund", "credit", "leave_open"}
        if resolution not in valid:
            raise ValueError(f"deposit_resolution must be one of {valid}, got '{resolution}'")

        if resolution == "leave_open":
            # No action — Payments stay unapplied on customer account.
            for p in unapplied_deposits:
                self.audit(
                    entity_type=EntityType.PAYMENT,
                    entity_id=p.id,
                    action=AuditAction.STATUS_CHANGED,
                    new_value={"resolution": "left_open_after_cancel",
                               "so_number": so.so_number, "reason": reason},
                )
            return

        if resolution == "credit":
            # Convert unapplied deposit into customer credit_balance.
            crm = CRMService(self.db, self.current_user_id)
            for p in unapplied_deposits:
                amount = round(p.amount_unallocated, 2)
                # Add full payment amount to credit_balance, then create a "consumed"
                # allocation so the Payment shows 0 unapplied.
                crm.add_credit(
                    customer_id=p.customer_id,
                    amount=amount,
                    reason=f"SO {so.so_number} cancelled — deposit converted to credit",
                )
                # Mark the payment as fully allocated via a sentinel: bump amount_received
                # cannot be changed retroactively, so we leave it unapplied but flag in audit.
                # The credit_balance now holds the value.
                p.notes = (p.notes or "") + f"\n[Cancelled SO {so.so_number} — moved ${amount:.2f} to customer credit]"
                self.audit(
                    entity_type=EntityType.PAYMENT,
                    entity_id=p.id,
                    action=AuditAction.STATUS_CHANGED,
                    new_value={"resolution": "converted_to_credit",
                               "amount": amount, "so_number": so.so_number},
                )
            return

        if resolution == "refund":
            # Create a REFUND_TO_CUSTOMER payment for each unapplied deposit.
            # We model refunds as a separate Payment row with negative amount and
            # direction=REFUND_TO_CUSTOMER. Wife will issue the actual check/transfer
            # manually; the system just records the AR side.
            from app.constants import PaymentStatus, QBOSyncStatus
            from app.models.invoice import Payment
            for p in unapplied_deposits:
                amount = round(p.amount_unallocated, 2)
                refund = Payment(
                    customer_id=p.customer_id,
                    sales_order_id=so.id,
                    payment_date=datetime.utcnow(),
                    payment_method=p.payment_method,
                    direction=PaymentDirection.REFUND_TO_CUSTOMER,
                    amount_received=-amount,  # negative = refund going out
                    status=PaymentStatus.APPLIED,
                    notes=f"Refund for cancelled SO {so.so_number}: {reason}",
                    qbo_sync_status=QBOSyncStatus.PENDING,
                )
                self.db.add(refund)
                self.db.flush()
                # Mark the original deposit's notes so the audit trail is clear
                p.notes = (p.notes or "") + f"\n[Refunded via Payment #{refund.id} on cancel]"
                self.audit(
                    entity_type=EntityType.PAYMENT,
                    entity_id=p.id,
                    action=AuditAction.STATUS_CHANGED,
                    new_value={"resolution": "refunded", "refund_payment_id": refund.id,
                               "amount": amount, "so_number": so.so_number},
                )
            return

    def hold_order(self, so_id: int, reason: str) -> None:
        """
        Place SO on hold — blocks fulfillment without releasing committed inventory.
        Committed inventory remains reserved until the hold is released or the SO
        is cancelled.
        """
        so = self._get_so_or_404(so_id)
        if so.status in (SOStatus.CANCELLED, SOStatus.INVOICED):
            raise ValueError(f"Cannot hold a {so.status} sales order")
        if so.status == SOStatus.HOLD:
            return  # idempotent
        old_status = so.status
        so.status = SOStatus.HOLD
        self.audit(
            entity_type=EntityType.SALES_ORDER,
            entity_id=so_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=SOStatus.HOLD,
            notes=reason,
        )
        self.db.commit()

    def release_hold(self, so_id: int) -> None:
        """Release a held SO — restores it to OPEN (or PARTIAL if partially invoiced)."""
        so = self._get_so_or_404(so_id)
        if so.status != SOStatus.HOLD:
            raise ValueError(f"Sales order {so.so_number} is not on hold")
        old_status = so.status
        # Restore to PARTIAL if some lines were already invoiced, otherwise OPEN
        so.status = SOStatus.PARTIAL if any(ln.qty_invoiced > 0 for ln in so.lines) else SOStatus.OPEN
        self.audit(
            entity_type=EntityType.SALES_ORDER,
            entity_id=so_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=so.status,
            notes="hold released",
        )
        self.db.commit()

    # ── Deposit ───────────────────────────────────────────────────────────────

    def collect_deposit(self, so_id: int, amount: float, payment_method: str) -> object:
        """
        Record a deposit payment against the SO.
        Creates an unapplied Payment (no invoice allocation).
        The deposit sits as unapplied credit to be applied when the invoice is created.
        Updates SO.deposit_amount for display / reporting.
        """
        from app.services.payment_service import PaymentService

        so = self._get_so_or_404(so_id)
        if so.status == SOStatus.CANCELLED:
            raise ValueError(f"Cannot collect deposit on a cancelled sales order")

        pay_svc = PaymentService(self.db, self.current_user_id)
        payment = pay_svc.record_payment(
            customer_id=so.customer_id,
            amount_received=amount,
            payment_method=payment_method,
            data={"notes": f"Deposit for SO {so.so_number}"},
            invoice_ids=None,  # unapplied — will be applied when invoice is generated
            sales_order_id=so.id,  # R3 — link payment to SO for fulfill-time allocation
        )
        # PaymentService committed above; now update SO and commit separately.
        so.deposit_amount += amount
        self.audit(
            entity_type=EntityType.SALES_ORDER,
            entity_id=so_id,
            action=AuditAction.PAYMENT_APPLIED,
            new_value={
                "deposit_amount": amount,
                "payment_method": payment_method,
                "payment_id": payment.id,
                "running_deposit_total": so.deposit_amount,
            },
            notes=f"Deposit collected for SO {so.so_number}",
        )
        self.db.commit()
        return payment

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_so_or_404(self, so_id: int) -> SalesOrder:
        so = self.db.query(SalesOrder).filter(SalesOrder.id == so_id).first()
        if so is None:
            raise ValueError(f"SalesOrder {so_id} not found")
        return so

    def _add_line_internal(
        self,
        so_id: int,
        data: dict,
        sort_order: int,
        allow_negative_inventory: bool = False,
    ) -> SOLine:
        qty_ordered = int(data.get("qty_ordered", 1))

        # ── R7 — determine fulfillment source ────────────────────────────────
        # Caller may explicitly set; otherwise auto-derive from stock availability.
        explicit_source = data.get("fulfillment_source")
        linked_po_line_id = data.get("linked_po_line_id")

        product = None
        product_id = data.get("product_id")
        if product_id and data.get("line_type", LineType.PRODUCT) == LineType.PRODUCT:
            product = self.db.query(Product).filter(Product.id == product_id).first()

        if explicit_source:
            fulfillment_source = explicit_source
        elif product is None or qty_ordered <= 0:
            fulfillment_source = FulfillmentSource.STOCK   # non-product / zero-qty: no-op
        elif qty_ordered <= product.qty_available:
            fulfillment_source = FulfillmentSource.STOCK
        else:
            fulfillment_source = FulfillmentSource.BACKORDER

        # ── R6 — negative inventory hard block on STOCK commit ──────────────
        if (
            fulfillment_source == FulfillmentSource.STOCK
            and product is not None
            and qty_ordered > product.qty_available
        ):
            if not allow_negative_inventory:
                raise ValueError(
                    f"Cannot commit {qty_ordered} of {product.sku}: only "
                    f"{product.qty_available} available. Override requires "
                    f"NEGATIVE_INVENTORY_OVERRIDE permission, or add as backorder."
                )
            self.assert_can(Permission.NEGATIVE_INVENTORY_OVERRIDE)
            self.audit(
                entity_type=EntityType.SALES_ORDER,
                entity_id=so_id,
                action=AuditAction.INVENTORY_ADJUSTED,
                new_value={
                    "override": "allow_negative_inventory",
                    "product_id": product_id,
                    "qty_requested": qty_ordered,
                    "qty_available": product.qty_available,
                },
                notes="Negative inventory override at SO commit",
            )

        # Map fulfillment_source → initial line_status + legacy `source` value
        initial_status = self._initial_status_for(fulfillment_source)
        legacy_source = (
            SOLineSource.STOCK if fulfillment_source == FulfillmentSource.STOCK
            else SOLineSource.BACKORDER
        )

        line = SOLine(
            so_id=so_id,
            product_id=product_id,
            line_type=data.get("line_type", LineType.PRODUCT),
            description=data.get("description", ""),
            qty_ordered=qty_ordered,
            qty_committed=0,
            qty_fulfilled=0,
            qty_invoiced=0,
            unit_price=float(data.get("unit_price", 0.0)),
            unit_cost=float(data.get("unit_cost", 0.0)),
            discount_pct=float(data.get("discount_pct", 0.0)),
            core_charge=float(data.get("core_charge", 0.0)),
            source=legacy_source,
            fulfillment_source=fulfillment_source,
            line_status=initial_status,
            linked_po_line_id=linked_po_line_id,
            sort_order=sort_order,
        )
        self.db.add(line)
        self.db.flush()

        # Inventory accounting — only STOCK commits qty immediately
        if (
            line.line_type == LineType.PRODUCT
            and line.product_id
            and qty_ordered > 0
            and product is not None
        ):
            if fulfillment_source == FulfillmentSource.STOCK:
                product.qty_committed += qty_ordered
                line.qty_committed = qty_ordered
                self._write_so_txn(
                    product_id=product.id,
                    txn_type=InventoryTxnType.SO_COMMITTED,
                    qty_change=-qty_ordered,  # negative = reserved, reduces available
                    qty_after=product.qty_on_hand,
                    so_id=so_id,
                    notes="SO committed (stock)",
                )
            elif fulfillment_source == FulfillmentSource.BACKORDER:
                product.qty_backordered += qty_ordered
            # LINKED_PO / SPECIAL_ORDER / DROPSHIP: commit happens on PO receipt,
            # not here. Demand tracking on product.qty_backordered as desired.

        return line

    # ── Mapping helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _initial_status_for(fulfillment_source: str) -> str:
        """R7 — map a fulfillment_source to the SO line's initial line_status."""
        mapping = {
            FulfillmentSource.STOCK:         SOLineStatus.RESERVED_STOCK,
            FulfillmentSource.BACKORDER:     SOLineStatus.AWAITING_STOCK,
            FulfillmentSource.LINKED_PO:     SOLineStatus.AWAITING_PO_RECEIPT,
            FulfillmentSource.SPECIAL_ORDER: SOLineStatus.AWAITING_SPECIAL_ORDER_PO,
            FulfillmentSource.DROPSHIP:      SOLineStatus.VENDOR_CONFIRMED,
        }
        return mapping.get(fulfillment_source, SOLineStatus.STOCK)

    def _release_line_commitment(self, line: SOLine, so_number: str) -> None:
        """Release the committed inventory for a single line (cancel or cancel_order)."""
        if line.line_type == LineType.PRODUCT and line.product_id and line.qty_committed > 0:
            product = self.db.query(Product).filter(Product.id == line.product_id).first()
            if product:
                released = line.qty_committed
                product.qty_committed = max(0, product.qty_committed - released)
                self._write_so_txn(
                    product_id=product.id,
                    txn_type=InventoryTxnType.SO_RELEASED,
                    qty_change=released,   # positive = back to available
                    qty_after=product.qty_on_hand,
                    so_id=line.so_id,
                    notes=f"SO {so_number} cancelled",
                )
                line.qty_committed = 0

    def _write_so_txn(
        self,
        product_id: int,
        txn_type: str,
        qty_change: int,
        qty_after: int,
        so_id: int,
        notes: str = "",
    ) -> None:
        """Write an InventoryTransaction for a SO_COMMITTED or SO_RELEASED event."""
        txn = InventoryTransaction(
            product_id=product_id,
            transaction_type=txn_type,
            qty_change=qty_change,
            qty_after=qty_after,
            reference_type=EntityType.SALES_ORDER,
            reference_id=so_id,
            performed_by_id=self.current_user_id,
            notes=notes,
        )
        self.db.add(txn)
        self.db.flush()
