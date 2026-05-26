"""
app/services/invoice_service.py
=================================
Invoice creation, locking, voiding, and QBO sync staging.

Key rules:
  - Invoice number: INV-[YEAR]-[NNNN], resets yearly
  - Lock window: same day OR until QBO sync OR until paid (whichever is first)
  - Locked invoices: no edits — must void and reissue
  - Voiding: creates audit entry; does NOT delete; reverses inventory
  - Partial invoicing from SO: supported via so_id
  - QBO sync: invoices pushed to QBO after lock
  - amount_paid: computed from PaymentAllocations (not direct payment FK)
  - Core charges: separate line items; linked to CoreCharge record
  - Internal notes: never printed on customer documents
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, EntityType, InventoryTxnType,
    InvoiceLockReason, InvoiceStatus, LineType, QBOSyncStatus,
)
from app.models.inventory import InventoryTransaction
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.settings_utils import bump_counter
from app.services.base import BaseService


class InvoiceService(BaseService):

    # ── Invoice Creation ──────────────────────────────────────────────────────

    def create_invoice(
        self,
        customer_id: int,
        data: dict,
        so_id: int | None = None,
        lines: list[dict] | None = None,
    ) -> Invoice:
        """
        Create a new invoice (draft status).
        Generates invoice_number (INV-YEAR-NNNN).
        """
        year = datetime.utcnow().year
        inv_number = bump_counter(self.db, "next_invoice_number", "INV", year)

        invoice = Invoice(
            invoice_number=inv_number,
            customer_id=customer_id,
            status=InvoiceStatus.DRAFT,
            sales_order_id=so_id,
            quote_id=data.get("quote_id"),
            customer_po_number=data.get("customer_po_number"),
            customer_job_number=data.get("customer_job_number"),
            esn=data.get("esn"),
            engine_manufacturer=data.get("engine_manufacturer", ""),
            engine_model=data.get("engine_model", ""),
            discount_pct=float(data.get("discount_pct", 0.0)),
            apply_cc_surcharge=bool(data.get("apply_cc_surcharge", False)),
            cc_surcharge_pct=float(data.get("cc_surcharge_pct", 3.0)),
            is_taxable=bool(data.get("is_taxable", False)),
            tax_rate=float(data.get("tax_rate", 0.0)),
            due_date=data.get("due_date"),
            notes=data.get("notes", ""),
            internal_notes=data.get("internal_notes", ""),
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(invoice)
        self.db.flush()

        sort_order = 0
        for line_data in (lines or []):
            self._add_line_internal(invoice.id, line_data, sort_order)
            sort_order += 1

        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice.id,
            action=AuditAction.CREATED,
            new_value={"invoice_number": inv_number, "customer_id": customer_id},
        )
        self.db.commit()
        return invoice

    def add_line(self, invoice_id: int, product_id: int | None, data: dict) -> InvoiceLine:
        """
        Add a line to an unlocked invoice.
        Warns caller (via raised ValueError) if qty exceeds on-hand.
        Pass allow_zero_stock=True in data to override.
        """
        invoice = self._get_or_404(invoice_id)
        self._assert_editable(invoice)

        sort_order = max((ln.sort_order for ln in invoice.lines), default=-1) + 1

        # Stock check for product lines — warn but allow override
        if product_id is not None and data.get("line_type", LineType.PRODUCT) == LineType.PRODUCT:
            product = self.db.query(Product).filter(Product.id == product_id).first()
            qty_requested = int(data.get("qty", 1))
            if product and product.qty_available < qty_requested and not data.get("allow_zero_stock"):
                raise ValueError(
                    f"Only {product.qty_available} units available for {product.sku}. "
                    "Pass allow_zero_stock=True to override."
                )

        line = self._add_line_internal(invoice_id, {**data, "product_id": product_id}, sort_order)
        self.db.commit()
        return line

    def update_line(self, line_id: int, data: dict) -> InvoiceLine:
        line = self.db.query(InvoiceLine).filter(InvoiceLine.id == line_id).first()
        if line is None:
            raise ValueError(f"InvoiceLine {line_id} not found")
        self._assert_editable(line.invoice)

        updatable = ["description", "qty", "unit_price", "unit_cost", "discount_pct", "sort_order"]
        for field in updatable:
            if field in data:
                setattr(line, field, data[field])
        self.db.commit()
        return line

    def remove_line(self, line_id: int) -> None:
        line = self.db.query(InvoiceLine).filter(InvoiceLine.id == line_id).first()
        if line is None:
            raise ValueError(f"InvoiceLine {line_id} not found")
        self._assert_editable(line.invoice)
        self.db.delete(line)
        self.db.commit()

    # ── Finalise ──────────────────────────────────────────────────────────────

    def finalise(self, invoice_id: int) -> Invoice:
        """
        Finalise (post) an invoice:
          1. Validates all lines
          2. Snapshots unit_cost on each product line
          3. Writes InventoryTransaction (INVOICE_SALE) for product lines
          4. Creates CoreCharge records for core lines
          5. Sets status = 'open', marks for QBO sync
        """
        invoice = self._get_or_404(invoice_id)
        if invoice.status != InvoiceStatus.DRAFT:
            raise ValueError(f"Invoice {invoice.invoice_number} is already finalised (status={invoice.status})")
        if not invoice.lines:
            raise ValueError("Cannot finalise an invoice with no lines")

        product_lines = [ln for ln in invoice.lines if ln.line_type == LineType.PRODUCT]
        if not product_lines:
            raise ValueError("Invoice must have at least one product line")

        for ln in product_lines:
            if ln.qty <= 0:
                raise ValueError(f"Line {ln.id}: qty must be > 0")
            if ln.unit_price < 0:
                raise ValueError(f"Line {ln.id}: unit_price cannot be negative")

            product = self.db.query(Product).filter(Product.id == ln.product_id).first() if ln.product_id else None

            # Snapshot cost at time of sale — never changes after this point
            if product and ln.unit_cost == 0.0:
                ln.unit_cost = product.cost

            # Deduct inventory
            if product:
                product.qty_on_hand = max(0, product.qty_on_hand - ln.qty)
                txn = InventoryTransaction(
                    product_id=product.id,
                    transaction_type=InventoryTxnType.INVOICE_SALE,
                    qty_change=-ln.qty,
                    qty_after=product.qty_on_hand,
                    reference_type=EntityType.INVOICE,
                    reference_id=invoice_id,
                    performed_by_id=self.current_user_id,
                    notes=f"Invoice {invoice.invoice_number}",
                )
                self.db.add(txn)

        # Create core charges for core lines
        core_lines = [ln for ln in invoice.lines if ln.line_type == LineType.CORE_CHARGE]
        for core_ln in core_lines:
            if core_ln.parent_line_id and core_ln.product_id:
                from app.services.core_service import CoreService
                CoreService(self.db, self.current_user_id).create_core_charge(
                    invoice_id=invoice_id,
                    invoice_line_id=core_ln.id,
                    product_id=core_ln.product_id,
                    qty=core_ln.qty,
                    customer_unit_charge=core_ln.unit_price,
                    vendor_unit_charge=core_ln.unit_cost,
                )

        invoice.status = InvoiceStatus.OPEN
        invoice.qbo_sync_status = QBOSyncStatus.PENDING

        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=InvoiceStatus.DRAFT,
            new_value=InvoiceStatus.OPEN,
        )
        self.db.commit()
        return invoice

    # ── Locking ───────────────────────────────────────────────────────────────

    def lock(self, invoice_id: int, reason: str = InvoiceLockReason.END_OF_DAY) -> None:
        """Lock an invoice — no further edits allowed."""
        invoice = self._get_or_404(invoice_id)
        if invoice.is_locked:
            return  # already locked — idempotent
        invoice.locked_at = datetime.utcnow()
        invoice.lock_reason = reason
        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice_id,
            action=AuditAction.LOCKED,
            new_value={"reason": reason},
        )
        self.db.commit()

    def is_editable(self, invoice_id: int) -> bool:
        """Return True if the invoice can still be edited."""
        invoice = self._get_or_404(invoice_id)
        return not invoice.is_locked and invoice.status not in (InvoiceStatus.VOID, InvoiceStatus.PAID)

    def reopen_after_payment_reversal(self, invoice_id: int) -> None:
        """
        Re-open an invoice after its payment is reversed (NSF / stop-payment).
        Removes the payment lock so the balance reappears as collectible.
        Called by PaymentService.reverse_payment() — not called directly by routes.
        """
        invoice = self._get_or_404(invoice_id)
        if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.PARTIAL):
            invoice.status = InvoiceStatus.OPEN
        if invoice.lock_reason == InvoiceLockReason.PAID:
            invoice.locked_at = None
            invoice.lock_reason = None
        self.db.flush()

    def refresh_payment_status(self, invoice_id: int) -> None:
        """
        Recalculate and persist invoice payment status based on current allocations.

        Called by PaymentService after any allocation change.
        InvoiceService is the sole owner of invoice.status mutations.
        PaymentService must never write invoice.status directly.

        Caller is responsible for the surrounding commit (this method flushes only).
        """
        invoice = self._get_or_404(invoice_id)
        self.db.expire(invoice)  # reload allocations from DB — in-session cache is stale

        balance = invoice.balance_due   # uses the fixed amount_paid (reversed filtered out)
        if balance <= 0.001:
            invoice.status = InvoiceStatus.PAID
            # lock() commits — call it last so the status change is included
            self.lock(invoice.id, reason=InvoiceLockReason.PAID)
        elif invoice.amount_paid > 0:
            invoice.status = InvoiceStatus.PARTIAL
            self.db.flush()
        else:
            invoice.status = InvoiceStatus.OPEN
            self.db.flush()

    # ── Void ──────────────────────────────────────────────────────────────────

    def void_invoice(self, invoice_id: int, reason: str) -> None:
        """
        Void an invoice.
        Reverses inventory transactions (CORRECTION).
        Marks allocations as reversed.
        """
        invoice = self._get_or_404(invoice_id)
        if invoice.status == InvoiceStatus.VOID:
            raise ValueError(f"Invoice {invoice.invoice_number} is already void")

        # Reverse inventory for product lines
        for ln in invoice.lines:
            if ln.line_type == LineType.PRODUCT and ln.product_id:
                product = self.db.query(Product).filter(Product.id == ln.product_id).first()
                if product:
                    product.qty_on_hand += ln.qty
                    txn = InventoryTransaction(
                        product_id=product.id,
                        transaction_type=InventoryTxnType.CORRECTION,
                        qty_change=ln.qty,
                        qty_after=product.qty_on_hand,
                        reference_type=EntityType.INVOICE,
                        reference_id=invoice_id,
                        performed_by_id=self.current_user_id,
                        notes=f"Void: {invoice.invoice_number} — {reason}",
                    )
                    self.db.add(txn)

        # Mark all allocations reversed
        for allocation in invoice.allocations:
            allocation.is_reversed = True

        invoice.status = InvoiceStatus.VOID
        invoice.locked_at = datetime.utcnow()
        invoice.lock_reason = InvoiceLockReason.PAID  # reuse closest constant; void is permanent

        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice_id,
            action=AuditAction.VOIDED,
            new_value={"reason": reason},
        )
        self.db.commit()

    # ── Totals ────────────────────────────────────────────────────────────────

    def calculate_totals(self, invoice_id: int) -> dict:
        """Return totals breakdown for display. Does not write to DB."""
        invoice = self._get_or_404(invoice_id)
        return {
            "subtotal": invoice.subtotal,
            "tax_amount": invoice.tax_amount,
            "surcharge_amount": invoice.surcharge_amount,
            "total": invoice.total,
            "amount_paid": invoice.amount_paid,
            "balance_due": invoice.balance_due,
        }

    # ── QBO Sync ──────────────────────────────────────────────────────────────

    def mark_synced(self, invoice_id: int, qbo_id: str) -> None:
        invoice = self._get_or_404(invoice_id)
        invoice.qbo_invoice_id = qbo_id
        invoice.qbo_sync_status = QBOSyncStatus.SYNCED
        invoice.qbo_last_synced_at = datetime.utcnow()
        invoice.qbo_sync_error = None
        self.db.commit()
        # Lock after QBO push — bookkeeping source of truth owns it now
        self.lock(invoice_id, reason=InvoiceLockReason.QBO_SYNC)

    def mark_sync_failed(self, invoice_id: int, error: str) -> None:
        invoice = self._get_or_404(invoice_id)
        invoice.qbo_sync_status = QBOSyncStatus.ERROR
        invoice.qbo_sync_error = error
        invoice.qbo_sync_retry_count += 1
        self.db.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_or_404(self, invoice_id: int) -> Invoice:
        inv = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if inv is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        return inv

    def _assert_editable(self, invoice: Invoice) -> None:
        if invoice.is_locked:
            raise ValueError(f"Invoice {invoice.invoice_number} is locked and cannot be edited")
        if invoice.status in (InvoiceStatus.VOID, InvoiceStatus.PAID):
            raise ValueError(f"Invoice {invoice.invoice_number} has status '{invoice.status}' and cannot be edited")

    def _add_line_internal(self, invoice_id: int, data: dict, sort_order: int) -> InvoiceLine:
        line = InvoiceLine(
            invoice_id=invoice_id,
            product_id=data.get("product_id"),
            so_line_id=data.get("so_line_id"),
            line_type=data.get("line_type", LineType.PRODUCT),
            description=data.get("description", ""),
            qty=int(data.get("qty", 1)),
            unit_price=float(data.get("unit_price", 0.0)),
            unit_cost=float(data.get("unit_cost", 0.0)),
            discount_pct=float(data.get("discount_pct", 0.0)),
            is_core_line=bool(data.get("is_core_line", False)),
            parent_line_id=data.get("parent_line_id"),
            sort_order=sort_order,
        )
        self.db.add(line)
        self.db.flush()
        return line
