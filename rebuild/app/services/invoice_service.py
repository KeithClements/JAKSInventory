"""
app/services/invoice_service.py
=================================
Invoice creation, line editing, finalization, locking, voiding, QBO sync staging.

Phase A — Transaction Workspace rules:
  - Invoice number: INV-[YEAR]-[NNNN], resets yearly
  - **Lifecycle:** DRAFT (fully editable) → OPEN (locked content) → PARTIAL/PAID/VOID
  - **Editing rule:** ONLY DRAFT invoices accept line/header edits. OPEN+ requires void/reissue.
  - **Auto-core linkage:** when product has core, auto-add child CORE_CHARGE line with
    is_auto_generated=True, is_locked_to_parent=True, parent_line_id=parent.id.
    Editing parent qty cascades to locked children. Deleting parent cascades to auto children.
  - **Finalize:** validate → check inventory → snapshot cost → decrement stock → status=OPEN
  - **Customer change in draft:** allowed; UI confirms recalc-vs-keep with user
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.constants import (
    AuditAction, EntityType, InventoryTxnType,
    InvoiceLockReason, InvoiceStatus, LineType, NON_TAXABLE_LINE_TYPES,
    PaymentTerms, Permission, QBOSyncStatus,
)
from app.models.customer import Customer
from app.models.inventory import InventoryTransaction
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.settings_utils import bump_counter, get_setting_value_db
from app.services.base import BaseService


# Line types treated as billable parts (count toward Parts Subtotal, taxable).
_PARTS_LINE_TYPES = {LineType.PRODUCT, LineType.MISC, LineType.WARRANTY}
# Line types treated as freight/delivery (separate totals bucket).
_FREIGHT_LINE_TYPES = {
    LineType.SHIPPING, LineType.FREIGHT, LineType.LOCAL_DELIVERY, LineType.FUEL_SERVICE_CHARGE,
}


class InvoiceService(BaseService):

    # ── Draft creation (workspace entry point) ────────────────────────────────

    def create_draft(self, customer_id: int) -> Invoice:
        """
        Minimal draft invoice for the workspace flow.

        Inherits sensible defaults from the customer:
          - tax_rate, is_taxable (from is_tax_exempt + tax_rate)
          - due_date based on payment_terms
          - cc_surcharge_pct from system settings
        Caller redirects to /invoices/{id} where the full workspace handles editing.
        """
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            raise ValueError(f"Customer {customer_id} not found")
        if not customer.is_active:
            raise ValueError(f"Customer {customer.company_name} is inactive")

        year = datetime.utcnow().year
        inv_number = bump_counter(self.db, "next_invoice_number", "INV", year)

        # Tax defaults — tax_exempt customers force is_taxable=False regardless of rate
        is_taxable = (not customer.is_tax_exempt) and (customer.tax_rate > 0)
        tax_rate = customer.tax_rate if is_taxable else 0.0

        # Due date from payment terms
        due_date = None
        now = datetime.utcnow()
        if customer.payment_terms == PaymentTerms.NET_30:
            due_date = now + timedelta(days=30)
        elif customer.payment_terms == PaymentTerms.NET_60:
            due_date = now + timedelta(days=60)
        # COD: due_date = None (paid at pickup)

        cc_raw = get_setting_value_db(self.db, "cc_surcharge_pct", "3.0") or "3.0"
        try:
            cc_surcharge_pct = float(cc_raw)
        except (TypeError, ValueError):
            cc_surcharge_pct = 3.0

        invoice = Invoice(
            invoice_number=inv_number,
            customer_id=customer_id,
            status=InvoiceStatus.DRAFT,
            discount_pct=customer.discount_pct or 0.0,
            apply_cc_surcharge=False,
            cc_surcharge_pct=cc_surcharge_pct,
            is_taxable=is_taxable,
            tax_rate=tax_rate,
            # R1 — snapshot customer tax settings AT CREATION. These freeze on
            # finalize and are the source of truth for everything downstream.
            tax_rate_snapshot=tax_rate,
            tax_exempt_snapshot=customer.is_tax_exempt,
            due_date=due_date,
            notes="",
            internal_notes="",
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(invoice)
        self.db.flush()

        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice.id,
            action=AuditAction.CREATED,
            new_value={"invoice_number": inv_number, "customer_id": customer_id, "source": "workspace"},
        )
        self.db.commit()
        return invoice

    # ── Legacy full create (preserved for backward compat — quote→invoice etc.) ─

    def create_invoice(
        self,
        customer_id: int,
        data: dict,
        so_id: int | None = None,
        lines: list[dict] | None = None,
    ) -> Invoice:
        """Bulk-create invoice from quote/SO conversion or imports."""
        year = datetime.utcnow().year
        inv_number = bump_counter(self.db, "next_invoice_number", "INV", year)

        # R1 — derive tax snapshot from the customer record, unless caller
        # supplied an explicit override (rare; tests / data migration).
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            raise ValueError(f"Customer {customer_id} not found")
        cust_tax_exempt = customer.is_tax_exempt
        cust_tax_rate = (
            0.0 if cust_tax_exempt else (customer.tax_rate or 0.0)
        )
        is_taxable_legacy = data.get("is_taxable", not cust_tax_exempt and cust_tax_rate > 0)
        tax_rate_legacy = data.get("tax_rate", cust_tax_rate)

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
            is_taxable=bool(is_taxable_legacy),
            tax_rate=float(tax_rate_legacy),
            # R1 — snapshot at creation
            tax_rate_snapshot=float(cust_tax_rate),
            tax_exempt_snapshot=cust_tax_exempt,
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

        # Auto-add core child lines for any PRODUCT lines whose product carries a core
        auto_core_sort = sort_order
        product_lines = (
            self.db.query(InvoiceLine)
            .filter(
                InvoiceLine.invoice_id == invoice.id,
                InvoiceLine.line_type == LineType.PRODUCT,
                InvoiceLine.parent_line_id.is_(None),
            )
            .all()
        )
        for pl in product_lines:
            if pl.product_id:
                product = self.db.query(Product).filter(Product.id == pl.product_id).first()
                if product and product.has_core and product.customer_core_charge > 0:
                    self._add_line_internal(invoice.id, {
                        "product_id": pl.product_id,
                        "description": f"Core — {product.title or product.sku}",
                        "qty": pl.qty,
                        "unit_price": product.customer_core_charge,
                        "unit_cost": product.vendor_core_charge,
                        "line_type": LineType.CORE_CHARGE,
                        "parent_line_id": pl.id,
                        "is_core_line": True,
                        "is_auto_generated": True,
                        "is_locked_to_parent": True,
                        "discount_pct": 0.0,
                    }, auto_core_sort)
                    auto_core_sort += 1

        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice.id,
            action=AuditAction.CREATED,
            new_value={"invoice_number": inv_number, "customer_id": customer_id},
        )
        self.db.commit()
        return invoice

    # ── Header editing ────────────────────────────────────────────────────────

    def update_header(self, invoice_id: int, data: dict) -> Invoice:
        """Update header fields on a draft invoice (customer, PO, ESN, tax, etc.)."""
        invoice = self._get_or_404(invoice_id)
        self._assert_editable(invoice)

        # Whitelist of fields the workspace can write
        for field in (
            "customer_po_number", "customer_job_number", "esn",
            "engine_manufacturer", "engine_model",
            "discount_pct", "apply_cc_surcharge", "cc_surcharge_pct",
            "is_taxable", "tax_rate", "due_date",
            "notes", "internal_notes",
        ):
            if field in data:
                setattr(invoice, field, data[field])
        self.db.commit()
        return invoice

    def change_customer(self, invoice_id: int, new_customer_id: int, recalc_pricing: bool) -> Invoice:
        """
        Reassign a draft invoice to a different customer.
        recalc_pricing=True re-applies the new customer's discount and tax defaults
        across existing lines; False keeps line prices as-is and only swaps account.
        """
        invoice = self._get_or_404(invoice_id)
        self._assert_editable(invoice)
        new_customer = self.db.query(Customer).filter(Customer.id == new_customer_id).first()
        if new_customer is None:
            raise ValueError(f"Customer {new_customer_id} not found")
        if not new_customer.is_active:
            raise ValueError(f"Customer {new_customer.company_name} is inactive")

        old_customer_id = invoice.customer_id
        invoice.customer_id = new_customer_id

        # Always update terms / tax defaults / due date — these reflect the account
        invoice.is_taxable = (not new_customer.is_tax_exempt) and (new_customer.tax_rate > 0)
        invoice.tax_rate = new_customer.tax_rate if invoice.is_taxable else 0.0
        if new_customer.payment_terms == PaymentTerms.NET_30:
            invoice.due_date = datetime.utcnow() + timedelta(days=30)
        elif new_customer.payment_terms == PaymentTerms.NET_60:
            invoice.due_date = datetime.utcnow() + timedelta(days=60)
        else:
            invoice.due_date = None

        if recalc_pricing:
            # Apply new customer discount across lines, re-snap unit_cost from product
            invoice.discount_pct = new_customer.discount_pct or 0.0
            for ln in invoice.lines:
                ln.discount_pct = invoice.discount_pct
                if ln.product_id and ln.line_type == LineType.PRODUCT:
                    product = self.db.query(Product).filter(Product.id == ln.product_id).first()
                    if product and product.suggested_sell_price > 0:
                        ln.unit_price = product.suggested_sell_price

        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice_id,
            action=AuditAction.UPDATED,
            old_value={"customer_id": old_customer_id},
            new_value={"customer_id": new_customer_id, "recalc_pricing": recalc_pricing},
        )
        self.db.commit()
        return invoice

    # ── Line editing ──────────────────────────────────────────────────────────

    def add_line(self, invoice_id: int, product_id: int | None, data: dict) -> InvoiceLine:
        """
        Add a line to a DRAFT invoice. For PRODUCT lines with a core charge on the
        product, also auto-add a locked child CORE_CHARGE line.
        """
        invoice = self._get_or_404(invoice_id)
        self._assert_editable(invoice)

        sort_order = max((ln.sort_order for ln in invoice.lines), default=-1) + 1

        merged = {**data, "product_id": product_id}
        # Apply invoice-level default discount for new product lines
        if (
            merged.get("line_type", LineType.PRODUCT) == LineType.PRODUCT
            and "discount_pct" not in data
            and invoice.discount_pct
        ):
            merged["discount_pct"] = invoice.discount_pct

        line = self._add_line_internal(invoice_id, merged, sort_order)

        # Auto-add core child line for top-level PRODUCT lines whose product has core
        if (
            product_id is not None
            and merged.get("line_type", LineType.PRODUCT) == LineType.PRODUCT
            and not merged.get("parent_line_id")
        ):
            product = self.db.query(Product).filter(Product.id == product_id).first()
            if product and product.has_core and product.customer_core_charge > 0:
                self._add_line_internal(invoice_id, {
                    "product_id": product_id,
                    "description": f"Core — {product.title or product.sku}",
                    "qty": int(merged.get("qty", 1)),
                    "unit_price": product.customer_core_charge,
                    "unit_cost": product.vendor_core_charge,
                    "line_type": LineType.CORE_CHARGE,
                    "parent_line_id": line.id,
                    "is_core_line": True,
                    "is_auto_generated": True,
                    "is_locked_to_parent": True,
                    "discount_pct": 0.0,
                }, sort_order + 1)

        self.db.commit()
        return line

    def update_line(self, line_id: int, data: dict) -> InvoiceLine:
        """
        Update a draft invoice line. If qty changes on a parent line, any linked
        children with is_locked_to_parent=True have their qty synced.
        """
        line = self.db.query(InvoiceLine).filter(InvoiceLine.id == line_id).first()
        if line is None:
            raise ValueError(f"InvoiceLine {line_id} not found")
        self._assert_editable(line.invoice)

        updatable = ("description", "qty", "unit_price", "unit_cost",
                     "discount_pct", "sort_order", "line_type")
        qty_changed = False
        for field in updatable:
            if field in data:
                if field == "qty" and int(data[field]) != line.qty:
                    qty_changed = True
                setattr(line, field, data[field])

        # Cascade qty change to locked children (auto-cores typically)
        if qty_changed and line.parent_line_id is None:
            for child in line.children:
                if child.is_locked_to_parent:
                    child.qty = line.qty

        self.db.commit()
        return line

    def unlink_line_from_parent(self, line_id: int) -> InvoiceLine:
        """User explicitly detaches an auto-generated child from its parent.
        After unlinking, parent edits no longer cascade to this line."""
        line = self.db.query(InvoiceLine).filter(InvoiceLine.id == line_id).first()
        if line is None:
            raise ValueError(f"InvoiceLine {line_id} not found")
        self._assert_editable(line.invoice)
        line.is_locked_to_parent = False
        line.is_auto_generated = False  # demoted to user-owned
        self.db.commit()
        return line

    def remove_line(self, line_id: int) -> None:
        """Delete a draft invoice line. Auto-generated children of the parent
        are cascade-deleted with it; manually unlinked children survive."""
        line = self.db.query(InvoiceLine).filter(InvoiceLine.id == line_id).first()
        if line is None:
            raise ValueError(f"InvoiceLine {line_id} not found")
        self._assert_editable(line.invoice)

        # Cascade-delete auto-generated children
        for child in list(line.children):
            if child.is_auto_generated:
                self.db.delete(child)

        self.db.delete(line)
        self.db.commit()

    # ── Finalize (hard validation + inventory check + lock) ───────────────────

    def validate_for_finalise(self, invoice_id: int) -> list[str]:
        """
        Return list of hard-block error messages (empty = ready to finalize).
        Does not write — call this from the route to surface errors before commit.
        """
        invoice = self._get_or_404(invoice_id)
        errors: list[str] = []

        if invoice.status != InvoiceStatus.DRAFT:
            errors.append(f"Invoice is not in draft (status = {invoice.status}).")
            return errors  # No point checking content if state is wrong

        if invoice.customer_id is None:
            errors.append("Customer is required.")

        if not invoice.lines:
            errors.append("Invoice has no lines.")
            return errors

        billable_lines = [
            ln for ln in invoice.lines
            if ln.line_type != LineType.NOTE  # notes don't need price/qty validation
        ] if hasattr(LineType, "NOTE") else invoice.lines

        for i, ln in enumerate(invoice.lines, start=1):
            label = f"Line {i} ({ln.description[:40] or ln.line_type})"

            # Product lines need qty > 0
            if ln.line_type == LineType.PRODUCT:
                if ln.qty <= 0:
                    errors.append(f"{label}: quantity must be greater than zero.")
                if not ln.description and ln.product_id is None:
                    errors.append(f"{label}: description is required when no product is selected.")
                if ln.unit_price is None:
                    errors.append(f"{label}: unit price is missing.")
                if ln.unit_price < 0:
                    errors.append(f"{label}: unit price cannot be negative.")

            # Core lines need positive qty and price
            elif ln.line_type == LineType.CORE_CHARGE:
                if ln.qty <= 0:
                    errors.append(f"{label}: core qty must be greater than zero.")
                if ln.unit_price < 0:
                    errors.append(f"{label}: core charge cannot be negative.")

            # Freight / fee / misc — qty optional, price must be set
            elif ln.line_type in _FREIGHT_LINE_TYPES or ln.line_type == LineType.MISC:
                if ln.unit_price is None or ln.unit_price < 0:
                    errors.append(f"{label}: amount is missing or negative.")

        # Tax sanity: if taxable, rate must be > 0
        if invoice.is_taxable and (invoice.tax_rate is None or invoice.tax_rate <= 0):
            errors.append("Invoice is marked taxable but no tax rate is set.")

        # Total sanity
        if invoice.total < 0:
            errors.append("Invoice total cannot be negative.")

        return errors

    def check_inventory_for_finalise(self, invoice_id: int) -> list[dict]:
        """
        Returns shortages as a list of dicts:
          [{product_id, sku, title, requested, available, shortage}, ...]
        Empty list = no shortages, safe to finalize.
        """
        invoice = self._get_or_404(invoice_id)
        # Aggregate qty per product across all PRODUCT lines on this invoice
        per_product: dict[int, int] = {}
        for ln in invoice.lines:
            if ln.line_type == LineType.PRODUCT and ln.product_id and ln.qty > 0:
                per_product[ln.product_id] = per_product.get(ln.product_id, 0) + ln.qty

        shortages: list[dict] = []
        for pid, qty_needed in per_product.items():
            product = self.db.query(Product).filter(Product.id == pid).first()
            if not product:
                continue
            available = product.qty_available  # reserves committed stock
            if qty_needed > available:
                shortages.append({
                    "product_id": pid,
                    "sku": product.sku,
                    "title": product.title or "",
                    "requested": qty_needed,
                    "available": available,
                    "shortage": qty_needed - available,
                })
        return shortages

    def finalise(self, invoice_id: int, allow_negative_inventory: bool = False) -> Invoice:
        """
        Finalize a DRAFT invoice:
          1. Run validate_for_finalise() — raises if any hard block
          2. Run check_inventory_for_finalise() — HARD BLOCK on shortage unless
             caller has NEGATIVE_INVENTORY_OVERRIDE permission AND passes
             allow_negative_inventory=True. Override is audited (R6).
          3. Snapshot unit_cost on each product line
          4. Decrement inventory + write InventoryTransaction(INVOICE_SALE)
             - For SO-derived lines, release qty_committed (one net decrement,
               not double)
          5. Create CoreCharge records for core child lines
          6. Set status = OPEN, mark for QBO sync
        """
        errors = self.validate_for_finalise(invoice_id)
        if errors:
            raise ValueError("; ".join(errors))

        shortages = self.check_inventory_for_finalise(invoice_id)
        if shortages:
            if not allow_negative_inventory:
                pretty = ", ".join(
                    f"{s['sku']} (need {s['requested']}, have {s['available']})"
                    for s in shortages
                )
                raise ValueError(
                    f"Insufficient stock: {pretty}. "
                    f"Admin may override with NEGATIVE_INVENTORY_OVERRIDE permission."
                )
            # Override requested — caller must have explicit permission
            self.assert_can(Permission.NEGATIVE_INVENTORY_OVERRIDE)
            # Audit the override BEFORE the mutation so it's recorded even if write fails
            self.audit(
                entity_type=EntityType.INVOICE,
                entity_id=invoice_id,
                action=AuditAction.INVENTORY_ADJUSTED,
                old_value=None,
                new_value={"override": "allow_negative_inventory", "shortages": shortages},
                notes="Negative inventory override at invoice finalize",
            )

        invoice = self._get_or_404(invoice_id)

        from app.models.quote import SOLine  # local import to avoid cycle

        # R1 — ensure tax snapshot is set (in case caller passed a draft with
        # missing snapshot). Re-read from customer if blank.
        if invoice.tax_rate_snapshot == 0.0 and invoice.tax_exempt_snapshot is False:
            cust = self.db.query(Customer).filter(Customer.id == invoice.customer_id).first()
            if cust and not cust.is_tax_exempt and cust.tax_rate > 0:
                invoice.tax_rate_snapshot = cust.tax_rate
            elif cust:
                invoice.tax_exempt_snapshot = cust.is_tax_exempt

        # R1 — freeze per-line tax_amount based on snapshot at finalize time.
        # After this, line.tax_amount is the source of truth (calculate_totals reads it).
        tax_rate = 0.0 if invoice.tax_exempt_snapshot else invoice.tax_rate_snapshot
        for ln in invoice.lines:
            if ln.is_taxable and tax_rate > 0:
                # Discount-adjusted line subtotal
                discounted = ln.unit_price * ln.qty * (1 - (ln.discount_pct or 0) / 100)
                ln.tax_amount = round(discounted * tax_rate / 100, 2)
            else:
                ln.tax_amount = 0.0

        for ln in invoice.lines:
            if ln.line_type != LineType.PRODUCT:
                continue
            product = self.db.query(Product).filter(Product.id == ln.product_id).first() if ln.product_id else None
            # Snapshot cost (immutable after this point)
            if product and ln.unit_cost == 0.0:
                ln.unit_cost = product.cost
            # Decrement inventory (allow going negative only when explicitly overridden)
            if product:
                if allow_negative_inventory:
                    product.qty_on_hand = product.qty_on_hand - ln.qty
                else:
                    product.qty_on_hand = max(0, product.qty_on_hand - ln.qty)

                # R6 — if this line traces back to an SO line, release the matching
                # qty_committed so we don't double-decrement (qty_committed was
                # bumped when the SO was created; SalesOrderService.fulfill_and_invoice
                # may have already released — only release residual here).
                if ln.so_line_id:
                    so_line = self.db.query(SOLine).filter(SOLine.id == ln.so_line_id).first()
                    if so_line and so_line.qty_committed > 0:
                        release = min(ln.qty, so_line.qty_committed)
                        so_line.qty_committed = max(0, so_line.qty_committed - release)
                        product.qty_committed = max(0, product.qty_committed - release)

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

        # Create CoreCharge records for core child lines
        for core_ln in invoice.lines:
            if core_ln.line_type == LineType.CORE_CHARGE and core_ln.parent_line_id and core_ln.product_id:
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
        """Lock an invoice — sticky timestamp; does not change status."""
        invoice = self._get_or_404(invoice_id)
        if invoice.is_locked:
            return
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
        """True iff invoice is DRAFT (the only editable state per Phase A spec)."""
        invoice = self._get_or_404(invoice_id)
        return invoice.status == InvoiceStatus.DRAFT

    def reopen_after_payment_reversal(self, invoice_id: int) -> None:
        """Re-open after NSF/stop-payment. Called by PaymentService only."""
        invoice = self._get_or_404(invoice_id)
        if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.PARTIAL):
            invoice.status = InvoiceStatus.OPEN
        if invoice.lock_reason == InvoiceLockReason.PAID:
            invoice.locked_at = None
            invoice.lock_reason = None
        self.db.flush()

    # ── Credit Memo Issuance (R8 — locked-invoice correction) ────────────────

    def issue_credit_memo(
        self,
        invoice_id: int,
        lines: list[dict] | None = None,
        reason: str = "",
        auto_apply: bool = False,
        auto_close_to_credit: bool = False,
    ) -> object:
        """
        R8 — Manual credit memo issuance against a locked invoice.

        This is the "correct a locked invoice" path. Once an invoice is OPEN
        (locked) or QBO-pushed, edits aren't allowed — instead, issue a CM that
        zeros out the bad amount, then create a fresh invoice if needed.

        Args:
            invoice_id:           the invoice being corrected
            lines:                CM line dicts. If None, defaults to a single
                                  line for the full invoice subtotal
            reason:               short reason text (required for audit)
            auto_apply:           if True, immediately apply the CM to the same
                                  invoice (reduces its balance_due)
            auto_close_to_credit: if True AND not auto_apply, push the CM
                                  amount to customer.credit_balance immediately

        Permission: ISSUE_CREDIT_MEMO. Audit row is always written.
        """
        from app.constants import CreditMemoTrigger
        from app.services.credit_memo_service import CreditMemoService

        invoice = self._get_or_404(invoice_id)
        if invoice.status == InvoiceStatus.VOID:
            raise ValueError(
                f"Invoice {invoice.invoice_number} is void — no credit memo needed"
            )
        if not reason.strip():
            raise ValueError("A reason is required when issuing a credit memo")

        # Default lines: one line crediting the full subtotal
        if not lines:
            lines = [{
                "description": f"Credit for invoice {invoice.invoice_number}: {reason}",
                "qty": 1,
                "unit_price": invoice.subtotal + (invoice.tax_amount or 0.0),
                "original_invoice_line_id": None,
            }]

        cm_svc = CreditMemoService(self.db, self.current_user_id)
        cm = cm_svc.create_credit_memo(
            customer_id=invoice.customer_id,
            trigger_type=CreditMemoTrigger.LOCKED_INVOICE_CORRECTION,
            lines=lines,
            original_invoice_id=invoice.id,
            reason=reason,
            auto_close_to_credit=auto_close_to_credit and not auto_apply,
        )

        # Optionally apply to the same invoice
        if auto_apply:
            self.db.expire(invoice)
            target = min(cm.unapplied_amount, invoice.balance_due)
            if target > 0:
                cm_svc.apply_credit_memo(cm.id, invoice.id, target)

        return cm

    # ── Customer Credit Application (R2 — "Apply Available Credit") ──────────

    def apply_customer_credit(
        self,
        invoice_id: int,
        amount: float | None = None,
    ) -> object:
        """
        R2 — Apply customer.credit_balance to an open invoice.

        This is the "Apply Available Credit" button workflow. Validates:
          - Invoice is not VOID
          - Customer has sufficient credit_balance (cannot go below 0)
          - amount <= invoice.balance_due (no overpayment from credit)
          - If amount is None, applies min(credit_balance, balance_due)

        Delegates to PaymentService.apply_account_credit which creates the
        Payment + Allocation + decrements credit_balance via CRMService.

        Returns the resulting PaymentAllocation.
        """
        from app.services.payment_service import PaymentService

        invoice = self._get_or_404(invoice_id)
        if invoice.status == InvoiceStatus.VOID:
            raise ValueError(
                f"Invoice {invoice.invoice_number} is voided — cannot apply credit"
            )

        customer = self.db.query(Customer).filter(Customer.id == invoice.customer_id).first()
        if customer is None:
            raise ValueError(f"Customer #{invoice.customer_id} not found")

        # Refresh balance from DB so we use the current state
        self.db.expire(invoice)
        balance = invoice.balance_due
        if balance <= 0.001:
            raise ValueError(
                f"Invoice {invoice.invoice_number} has no balance due — nothing to apply"
            )
        if customer.credit_balance <= 0.001:
            raise ValueError(
                f"Customer has no credit balance available "
                f"(currently ${customer.credit_balance:.2f})"
            )

        # Default to the lesser of credit available and balance due
        if amount is None:
            amount = min(customer.credit_balance, balance)
        amount = round(amount, 2)

        if amount <= 0:
            raise ValueError("Amount to apply must be positive")
        if amount > customer.credit_balance + 0.001:
            raise ValueError(
                f"Amount ${amount:.2f} exceeds available credit "
                f"${customer.credit_balance:.2f}"
            )
        if amount > balance + 0.001:
            raise ValueError(
                f"Amount ${amount:.2f} exceeds invoice balance ${balance:.2f}"
            )

        pay_svc = PaymentService(self.db, self.current_user_id)
        return pay_svc.apply_account_credit(
            customer_id=invoice.customer_id,
            invoice_id=invoice_id,
            amount=amount,
        )

    def refresh_payment_status(self, invoice_id: int) -> None:
        """
        Recalculate and persist invoice.status from current allocations.
        Sole owner of invoice.status mutations after finalize.
        """
        invoice = self._get_or_404(invoice_id)
        self.db.expire(invoice)

        balance = invoice.balance_due
        if balance <= 0.001:
            invoice.status = InvoiceStatus.PAID
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
        Void an invoice. Reverses inventory and marks allocations reversed.

        R4 — Hard rules:
          - Already VOID → reject (idempotency / explicit failure)
          - QBO-pushed (qbo_invoice_id set) → reject; use credit memo instead
            (admin can override via VOID_LOCKED_INVOICE permission for emergency
            corrections, but the audit trail makes that visible)
          - Has applied payments → reject; reverse payments first
        """
        invoice = self._get_or_404(invoice_id)
        if invoice.status == InvoiceStatus.VOID:
            raise ValueError(f"Invoice {invoice.invoice_number} is already void")

        # R4 — QBO-pushed invoices must be corrected via credit memo
        if invoice.qbo_invoice_id:
            # Allow admin override via permission
            try:
                self.assert_can(Permission.VOID_LOCKED_INVOICE)
            except Exception:
                raise ValueError(
                    f"Invoice {invoice.invoice_number} has been pushed to QBO "
                    f"(qbo_id={invoice.qbo_invoice_id}). Issue a credit memo to "
                    f"correct it instead of voiding."
                )
            # Override granted — audit it before mutation
            self.audit(
                entity_type=EntityType.INVOICE,
                entity_id=invoice_id,
                action=AuditAction.VOIDED,
                old_value={"qbo_invoice_id": invoice.qbo_invoice_id},
                new_value={"override": "void_qbo_pushed", "reason": reason},
                notes="Admin override voided QBO-pushed invoice",
            )

        # R4 — invoices with applied payments must reverse payments first
        applied_payments = [a for a in invoice.allocations if not a.is_reversed]
        if applied_payments:
            total_applied = sum(a.amount_applied for a in applied_payments)
            raise ValueError(
                f"Invoice {invoice.invoice_number} has ${total_applied:.2f} in "
                f"applied payments. Reverse payments first, then void."
            )

        # Use original sale transactions to know exactly how much to put back
        sale_txns: dict[int, int] = {}
        for orig in (
            self.db.query(InventoryTransaction)
            .filter(
                InventoryTransaction.transaction_type == InventoryTxnType.INVOICE_SALE,
                InventoryTransaction.reference_type == EntityType.INVOICE,
                InventoryTransaction.reference_id == invoice_id,
            )
            .all()
        ):
            sale_txns[orig.product_id] = sale_txns.get(orig.product_id, 0) + abs(orig.qty_change)

        if not sale_txns:
            for ln in invoice.lines:
                if ln.line_type == LineType.PRODUCT and ln.product_id and ln.qty > 0:
                    sale_txns[ln.product_id] = sale_txns.get(ln.product_id, 0) + ln.qty

        already_reversed: set[int] = set()
        for ln in invoice.lines:
            if ln.line_type != LineType.PRODUCT or not ln.product_id:
                continue
            pid = ln.product_id
            if pid in already_reversed:
                continue
            actual_qty = sale_txns.get(pid)
            already_reversed.add(pid)
            if not actual_qty:
                continue
            product = self.db.query(Product).filter(Product.id == pid).first()
            if product:
                product.qty_on_hand += actual_qty
                txn = InventoryTransaction(
                    product_id=product.id,
                    transaction_type=InventoryTxnType.CORRECTION,
                    qty_change=actual_qty,
                    qty_after=product.qty_on_hand,
                    reference_type=EntityType.INVOICE,
                    reference_id=invoice_id,
                    performed_by_id=self.current_user_id,
                    notes=f"Void: {invoice.invoice_number} — {reason}",
                )
                self.db.add(txn)

        for allocation in invoice.allocations:
            allocation.is_reversed = True

        invoice.status = InvoiceStatus.VOID
        invoice.locked_at = datetime.utcnow()
        invoice.lock_reason = InvoiceLockReason.PAID  # closest existing constant

        self.audit(
            entity_type=EntityType.INVOICE,
            entity_id=invoice_id,
            action=AuditAction.VOIDED,
            new_value={"reason": reason},
        )
        self.db.commit()

    # ── Totals (full breakdown for workspace totals panel) ────────────────────

    def calculate_totals(self, invoice_id: int) -> dict:
        """
        Full totals breakdown per Phase A spec — cores are ALWAYS surfaced as
        a separate subtotal (refundable liability, never silently rolled into parts).

        Returns:
          parts_subtotal   — sum of product / misc / warranty lines (pre-discount)
          core_subtotal    — sum of all CORE_CHARGE lines (separately tracked)
          freight_subtotal — sum of shipping/freight/delivery/fuel service
          discount_amount  — invoice-level % discount applied to parts only
          taxable_amount   — what tax is calculated on (after discount, taxable lines only)
          tax_amount       — taxable_amount × tax_rate
          cc_fee_amount    — CC surcharge on (subtotal + tax) if enabled
          total            — grand total
          amount_paid, balance_due
        """
        invoice = self._get_or_404(invoice_id)

        parts_subtotal = 0.0
        core_subtotal = 0.0
        freight_subtotal = 0.0
        for ln in invoice.lines:
            if ln.line_type in _PARTS_LINE_TYPES:
                parts_subtotal += ln.line_total
            elif ln.line_type == LineType.CORE_CHARGE:
                core_subtotal += ln.line_total
            elif ln.line_type in _FREIGHT_LINE_TYPES:
                freight_subtotal += ln.line_total

        parts_subtotal = round(parts_subtotal, 2)
        core_subtotal = round(core_subtotal, 2)
        freight_subtotal = round(freight_subtotal, 2)

        # Invoice-level discount applies to parts only (cores are pass-through)
        discount_amount = round(parts_subtotal * (invoice.discount_pct / 100), 2) if invoice.discount_pct else 0.0
        parts_after_discount = round(parts_subtotal - discount_amount, 2)

        # R1 — Tax computation: prefer frozen per-line tax_amount (set at finalize),
        # fall back to legacy invoice-level rate for drafts that haven't been finalized.
        sum_line_tax = round(sum((ln.tax_amount or 0.0) for ln in invoice.lines), 2)
        if sum_line_tax > 0:
            # Frozen path: use snapshotted per-line tax_amounts
            tax_rate_display = invoice.tax_rate_snapshot or invoice.tax_rate
            is_taxable_display = not invoice.tax_exempt_snapshot
            taxable_amount = round(
                sum(
                    ln.unit_price * ln.qty * (1 - (ln.discount_pct or 0) / 100)
                    for ln in invoice.lines
                    if ln.is_taxable
                ),
                2,
            )
            tax_amount = sum_line_tax
        else:
            # Draft / legacy fallback: live calc from invoice-level rate
            tax_rate_display = invoice.tax_rate_snapshot or invoice.tax_rate
            is_taxable_display = (
                not invoice.tax_exempt_snapshot
                and (invoice.is_taxable or tax_rate_display > 0)
            )
            taxable_amount = (
                round(parts_after_discount + freight_subtotal, 2)
                if is_taxable_display
                else 0.0
            )
            tax_amount = (
                round(taxable_amount * (tax_rate_display / 100), 2)
                if is_taxable_display
                else 0.0
            )

        # R1 — Invoice total does NOT include CC surcharge anymore.
        # Surcharge is computed AT PAYMENT TIME on the card portion only.
        # `cc_fee_amount` is now an INFORMATIONAL estimate ("if customer pays
        # the full balance by card, this is what the surcharge would be") and
        # is NOT added to the invoice total or balance_due.
        total = round(parts_after_discount + core_subtotal + freight_subtotal + tax_amount, 2)

        cc_surcharge_estimate = (
            round(total * (invoice.cc_surcharge_pct / 100), 2)
            if invoice.apply_cc_surcharge or (invoice.cc_surcharge_pct or 0) > 0
            else 0.0
        )

        return {
            "parts_subtotal":   parts_subtotal,
            "core_subtotal":    core_subtotal,
            "freight_subtotal": freight_subtotal,
            "discount_amount":  discount_amount,
            "taxable_amount":   taxable_amount,
            "tax_amount":       tax_amount,
            # Informational only — surcharge isn't added to total. Templates can
            # show "If paid by card: total + estimated surcharge = $X.XX".
            "cc_fee_amount":    cc_surcharge_estimate,
            "cc_surcharge_estimate": cc_surcharge_estimate,
            "total":            total,
            "amount_paid":      invoice.amount_paid,
            "balance_due":      round(total - invoice.amount_paid, 2),
        }

    # ── QBO Sync ──────────────────────────────────────────────────────────────

    def mark_synced(self, invoice_id: int, qbo_id: str) -> None:
        invoice = self._get_or_404(invoice_id)
        invoice.qbo_invoice_id = qbo_id
        invoice.qbo_sync_status = QBOSyncStatus.SYNCED
        invoice.qbo_last_synced_at = datetime.utcnow()
        invoice.qbo_sync_error = None
        self.db.commit()
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
        """
        R4 — only DRAFT invoices accept content edits.
        Once OPEN (finalized), corrections require a credit memo.
        Once VOID, no changes are possible.
        """
        if invoice.status == InvoiceStatus.DRAFT:
            return
        if invoice.status == InvoiceStatus.VOID:
            raise ValueError(
                f"Invoice {invoice.invoice_number} is voided — no edits allowed."
            )
        raise ValueError(
            f"Invoice {invoice.invoice_number} is locked. "
            f"Create a credit memo or void/reissue to correct it."
        )

    def _add_line_internal(self, invoice_id: int, data: dict, sort_order: int) -> InvoiceLine:
        line_type = data.get("line_type", LineType.PRODUCT)
        # R1 — per-line tax default: non-taxable for fee/credit line types, else
        # follow the invoice-level taxability snapshot. Caller may override via
        # data["is_taxable"] for jurisdictional edge cases.
        if "is_taxable" in data:
            is_taxable = bool(data["is_taxable"])
        elif line_type in NON_TAXABLE_LINE_TYPES:
            is_taxable = False
        else:
            # Look up the invoice's snapshot to default per-line
            inv = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if inv and inv.tax_exempt_snapshot:
                is_taxable = False
            elif inv and (inv.tax_rate_snapshot or 0) > 0:
                is_taxable = True
            else:
                is_taxable = False  # no rate set — line stays non-taxable

        line = InvoiceLine(
            invoice_id=invoice_id,
            product_id=data.get("product_id"),
            so_line_id=data.get("so_line_id"),
            line_type=line_type,
            description=data.get("description", ""),
            qty=int(data.get("qty", 1)),
            unit_price=float(data.get("unit_price", 0.0)),
            unit_cost=float(data.get("unit_cost", 0.0)),
            discount_pct=float(data.get("discount_pct", 0.0)),
            is_taxable=is_taxable,
            tax_amount=0.0,  # frozen at finalize time
            is_core_line=bool(data.get("is_core_line", False)),
            parent_line_id=data.get("parent_line_id"),
            is_auto_generated=bool(data.get("is_auto_generated", False)),
            is_locked_to_parent=bool(data.get("is_locked_to_parent", False)),
            sort_order=sort_order,
        )
        self.db.add(line)
        self.db.flush()
        return line
