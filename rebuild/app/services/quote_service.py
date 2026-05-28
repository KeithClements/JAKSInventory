"""
app/services/quote_service.py
===============================
Quote lifecycle and conversion logic.

OWNERSHIP:
  QuoteService owns Quote and QuoteLine mutations.
  Conversion methods delegate to the target service's owner:
    convert_to_sales_order() → SalesOrderService.create_sales_order()
    convert_to_invoice()     → InvoiceService.create_invoice() + finalise()

Key rules:
  - Quote number: Q-[YEAR]-[NNNN], resets yearly
  - Validity: default_validity_days from quote.validity_days (set at create time)
  - Quote → SO: clones all lines; links via converted_to_so_id; marks won
  - Quote → Invoice: direct path; skips SO; marks won
  - Lost quotes write to LostSaleLog for reporting
  - Expired quotes can be reactivated; reactivated_at + reactivated_by_id set
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.constants import (
    AuditAction, EntityType, LineRole, LineType,
    QuoteOutcome, QuoteStatus, SOPaymentMode,
)
from app.models.quote import LostSaleLog, Quote, QuoteLine
from app.services.base import BaseService
from app.settings_utils import bump_counter


class QuoteService(BaseService):

    # ── Quote CRUD ────────────────────────────────────────────────────────────

    def create_quote(self, customer_id: int, data: dict) -> Quote:
        """
        Create a new draft quote. Generates Q-YEAR-NNNN.
        Sets valid_until = today + validity_days.
        """
        year = datetime.utcnow().year
        quote_number = bump_counter(self.db, "next_quote_number", "Q", year)
        validity_days = int(data.get("validity_days", 30))

        quote = Quote(
            quote_number=quote_number,
            customer_id=customer_id,
            status=QuoteStatus.DRAFT,
            outcome=QuoteOutcome.PENDING,
            discount_pct=float(data.get("discount_pct", 0.0)),
            validity_days=validity_days,
            valid_until=datetime.utcnow() + timedelta(days=validity_days),
            follow_up_date=data.get("follow_up_date"),
            notes=data.get("notes", ""),
            internal_notes=data.get("internal_notes", ""),
        )
        self.db.add(quote)
        self.db.flush()

        sort_order = 0
        for line_data in data.get("lines", []):
            self._add_line_internal(quote.id, line_data, sort_order)
            sort_order += 1

        self.audit(
            entity_type=EntityType.QUOTE,
            entity_id=quote.id,
            action=AuditAction.CREATED,
            new_value={"quote_number": quote_number, "customer_id": customer_id},
        )
        self.db.commit()
        return quote

    def add_line(self, quote_id: int, product_id: int | None, data: dict) -> list[QuoteLine]:
        """
        Add a line to a quote. Auto-populates cost from preferred vendor source.
        line_type: 'product' | 'note' | 'core_charge' | 'misc_charge'

        Returns a list of all newly-created lines: [primary_line] normally, or
        [primary_line, core_line] when the product carries a core charge and this
        is a top-level PRODUCT line.  Callers that only need the primary line can
        still do `lines[0]`.
        """
        from app.models.product import Product

        quote = self._get_or_404(quote_id)
        sort_order = max((ln.sort_order for ln in quote.lines), default=-1) + 1

        unit_cost = float(data.get("unit_cost", 0.0))
        if product_id is not None and unit_cost == 0.0:
            unit_cost = self._preferred_vendor_cost(product_id)

        # upgrade_option lines default to excluded unless caller explicitly set is_included
        merged = {**data, "product_id": product_id, "unit_cost": unit_cost}
        if merged.get("line_role") == LineRole.UPGRADE_OPTION and "is_included" not in data:
            merged["is_included"] = False

        line = self._add_line_internal(quote_id, merged, sort_order)
        added: list[QuoteLine] = [line]

        # Auto-add core charge child line for top-level PRODUCT lines whose product
        # has a core.  Child lines (parent_line_id set) and non-PRODUCT lines are
        # skipped so we never nest a core under an upgrade-option or similar.
        if (
            product_id is not None
            and merged.get("line_type", LineType.PRODUCT) == LineType.PRODUCT
            and not merged.get("parent_line_id")
        ):
            product = self.db.query(Product).filter(Product.id == product_id).first()
            if product and product.has_core and product.customer_core_charge > 0:
                core_line = self._add_line_internal(quote_id, {
                    "product_id": product_id,
                    "description": f"Core — {product.title or product.sku}",
                    "qty": int(merged.get("qty", 1)),
                    "unit_price": product.customer_core_charge,
                    "unit_cost": product.vendor_core_charge,
                    "line_type": LineType.CORE_CHARGE,
                    "line_role": LineRole.CORE,
                    "parent_line_id": line.id,
                    "is_included": True,
                    "discount_pct": 0.0,
                }, sort_order + 1)
                added.append(core_line)

        self.db.commit()
        return added

    def update_line(self, line_id: int, data: dict) -> QuoteLine:
        line = self.db.query(QuoteLine).filter(QuoteLine.id == line_id).first()
        if line is None:
            raise ValueError(f"QuoteLine {line_id} not found")
        updatable = ["description", "qty", "unit_price", "unit_cost", "discount_pct", "sort_order"]
        for field in updatable:
            if field in data:
                setattr(line, field, data[field])
        self.db.commit()
        return line

    def remove_line(self, line_id: int) -> bool:
        """
        Delete a quote line and all its children (core, warranty, upgrade options, etc.).
        Returns True if any children were also deleted (caller may want to refresh the
        full tbody instead of just removing the single row).
        """
        line = self.db.query(QuoteLine).filter(QuoteLine.id == line_id).first()
        if line is None:
            raise ValueError(f"QuoteLine {line_id} not found")
        had_children = bool(line.children)
        # Delete children first so FK constraints are not violated
        for child in list(line.children):
            self.db.delete(child)
        self.db.delete(line)
        self.db.commit()
        return had_children

    def reorder_lines(self, quote_id: int, line_id_order: list[int]) -> None:
        """Update sort_order on all lines to match supplied sequence. Single bulk pass."""
        lines = (
            self.db.query(QuoteLine)
            .filter(QuoteLine.quote_id == quote_id)
            .all()
        )
        index_map = {lid: idx for idx, lid in enumerate(line_id_order)}
        for line in lines:
            if line.id in index_map:
                line.sort_order = index_map[line.id]
        self.db.commit()

    # ── Status Transitions ────────────────────────────────────────────────────

    def send_quote(self, quote_id: int) -> None:
        """Mark quote as sent to customer."""
        quote = self._get_or_404(quote_id)
        quote.status = QuoteStatus.SENT
        self.audit(
            entity_type=EntityType.QUOTE,
            entity_id=quote_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=QuoteStatus.DRAFT,
            new_value=QuoteStatus.SENT,
        )
        self.db.commit()

    def mark_won(self, quote_id: int) -> None:
        """Mark quote won — called automatically by conversion methods."""
        quote = self._get_or_404(quote_id)
        quote.status = QuoteStatus.CONVERTED
        quote.outcome = QuoteOutcome.WON
        self.db.flush()  # caller (conversion) commits

    def mark_lost(self, quote_id: int, lost_reason: str) -> None:
        """Mark quote lost. Writes LostSaleLog row for pipeline reporting."""
        quote = self._get_or_404(quote_id)
        quote.status = QuoteStatus.DECLINED
        quote.outcome = QuoteOutcome.LOST
        quote.lost_reason = lost_reason

        for line in quote.lines:
            if line.line_type == LineType.PRODUCT and line.product_id:
                self.db.add(LostSaleLog(
                    quote_id=quote_id,
                    customer_id=quote.customer_id,
                    product_id=line.product_id,
                    reason=lost_reason,
                    notes=f"Quote {quote.quote_number} lost",
                ))

        self.audit(
            entity_type=EntityType.QUOTE,
            entity_id=quote_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=QuoteStatus.SENT,
            new_value=QuoteStatus.DECLINED,
            notes=lost_reason,
        )
        self.db.commit()

    def reactivate(self, quote_id: int, new_validity_days: int | None = None) -> None:
        """
        Reactivate an expired or lost quote.
        Resets status to SENT, extends valid_until if new_validity_days is given.
        """
        quote = self._get_or_404(quote_id)
        old_status = quote.status
        quote.status = QuoteStatus.SENT
        quote.outcome = QuoteOutcome.PENDING
        quote.reactivated_at = datetime.utcnow()
        quote.reactivated_by_id = self.current_user_id
        quote.original_expires_at = quote.original_expires_at or quote.valid_until

        if new_validity_days is not None:
            quote.validity_days = new_validity_days
            quote.valid_until = datetime.utcnow() + timedelta(days=new_validity_days)

        self.audit(
            entity_type=EntityType.QUOTE,
            entity_id=quote_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=QuoteStatus.SENT,
            notes="reactivated",
        )
        self.db.commit()

    # ── Follow-Up ─────────────────────────────────────────────────────────────

    def set_follow_up(
        self,
        quote_id: int,
        status: str,
        days_ahead: int | None,
    ) -> Quote:
        """
        Set the follow-up status and schedule the next follow-up date.

        days_ahead=None  → clears the follow_up_date (No Follow Up).
        days_ahead=0     → sets follow_up_date to today (Truck Down — urgent).
        days_ahead=N     → sets follow_up_date to today + N days.
        """
        quote = self._get_or_404(quote_id)
        quote.follow_up_status = status
        if days_ahead is None:
            quote.follow_up_date = None
        else:
            quote.follow_up_date = datetime.utcnow() + timedelta(days=days_ahead)
        self.db.commit()
        return quote

    # ── Conversion ────────────────────────────────────────────────────────────

    def convert_to_sales_order(self, quote_id: int, payment_mode: str) -> object:
        """
        Clone quote lines into a new SalesOrder.
        SalesOrderService owns SO creation — QuoteService delegates.
        quote.converted_to_so_id set; quote.outcome = 'won'.
        """
        from app.services.sales_order_service import SalesOrderService
        quote = self._get_or_404(quote_id)

        # Build SO line data — only included product lines convert
        so_lines = [
            {
                "product_id": ln.product_id,
                "line_type": ln.line_type,
                "description": ln.description,
                "qty_ordered": ln.qty,
                "unit_price": ln.unit_price,
                "unit_cost": ln.unit_cost,
                "discount_pct": ln.discount_pct,
                "core_charge": 0.0,
            }
            for ln in sorted(quote.lines, key=lambda l: l.sort_order)
            if ln.line_type == LineType.PRODUCT and ln.is_included
        ]

        so_svc = SalesOrderService(self.db, self.current_user_id)
        so = so_svc.create_sales_order(
            customer_id=quote.customer_id,
            payment_mode=payment_mode,
            data={
                "customer_po_number": None,
                "notes": quote.notes,
                "internal_notes": quote.internal_notes,
                "lines": so_lines,
            },
            quote_id=quote_id,
        )

        quote.converted_to_so_id = so.id
        self.mark_won(quote_id)  # flush-only
        self.db.commit()
        return so

    def convert_to_invoice(self, quote_id: int) -> object:
        """
        Direct quote → invoice (no SO).
        Used for walk-in / phone orders that ship immediately.
        InvoiceService owns invoice creation — QuoteService delegates.
        """
        from app.services.invoice_service import InvoiceService
        quote = self._get_or_404(quote_id)

        # Only included PRODUCT lines convert — excluded upgrade options are dropped.
        # CORE_CHARGE lines are intentionally excluded here: InvoiceService.create_invoice()
        # auto-adds core charge lines for each product that carries a core.  Including
        # them from the quote would (a) produce wrong parent_line_id references and
        # (b) double-count if the invoice service adds its own.
        inv_lines = [
            {
                "product_id": ln.product_id,
                "line_type": ln.line_type,
                "description": ln.description,
                "qty": ln.qty,
                "unit_price": ln.unit_price,
                "unit_cost": ln.unit_cost,
                "discount_pct": ln.discount_pct,
                "allow_zero_stock": True,  # quote-to-invoice: stock check at finalise
            }
            for ln in sorted(quote.lines, key=lambda l: l.sort_order)
            if ln.is_included and ln.line_type != LineType.CORE_CHARGE
        ]

        inv_svc = InvoiceService(self.db, self.current_user_id)
        invoice = inv_svc.create_invoice(
            customer_id=quote.customer_id,
            data={"quote_id": quote_id, "notes": quote.notes},
            lines=inv_lines,
        )

        quote.converted_to_invoice_id = invoice.id
        self.mark_won(quote_id)  # flush-only
        self.db.commit()
        return invoice

    # ── Upgrade options & optional lines ─────────────────────────────────────

    def add_upgrade_option(
        self,
        parent_line_id: int,
        product_id: int,
        data: dict | None = None,
    ) -> QuoteLine:
        """
        Add an upgrade/alternate version of a product as a child line.
        The new line is NOT included in the total by default — the customer must
        explicitly select it (select_upgrade_option / toggle_line_included).

        data keys: description, qty, unit_price, unit_cost, discount_pct, option_label
        """
        parent = self._get_line_or_404(parent_line_id)
        data = data or {}

        unit_cost = float(data.get("unit_cost", 0.0))
        if unit_cost == 0.0 and product_id:
            unit_cost = self._preferred_vendor_cost(product_id)

        sibling_sort = max(
            (c.sort_order for c in parent.children), default=parent.sort_order
        ) + 1

        line = self._add_line_internal(
            parent.quote_id,
            {
                **data,
                "product_id": product_id,
                "unit_cost": unit_cost,
                "line_role": LineRole.UPGRADE_OPTION,
                "is_included": False,
                "parent_line_id": parent_line_id,
                "line_type": LineType.PRODUCT,
            },
            sibling_sort,
        )
        self.db.commit()
        return line

    def select_upgrade_option(self, option_line_id: int) -> None:
        """
        Select an upgrade option as the active version for its parent line.
        - Sets selected option  → is_included=True
        - Sets parent primary   → is_included=False
        - Deselects all sibling upgrade options
        """
        option = self._get_line_or_404(option_line_id)
        if option.parent_line_id is None:
            raise ValueError(f"Line {option_line_id} has no parent — cannot select as upgrade")

        parent = self._get_line_or_404(option.parent_line_id)
        parent.is_included = False

        for sibling in parent.children:
            sibling.is_included = (sibling.id == option_line_id)

        self.db.commit()

    def add_optional_line(
        self,
        parent_line_id: int,
        product_id: int,
        data: dict | None = None,
    ) -> QuoteLine:
        """
        Add an optional add-on (bolts, install kit, freight, etc.) under a parent
        line.  Optional lines are included in the total by default — the customer
        may ask to remove them.

        data keys: description, qty, unit_price, unit_cost, discount_pct
        """
        parent = self._get_line_or_404(parent_line_id)
        data = data or {}

        unit_cost = float(data.get("unit_cost", 0.0))
        if unit_cost == 0.0 and product_id:
            unit_cost = self._preferred_vendor_cost(product_id)

        sibling_sort = max(
            (c.sort_order for c in parent.children), default=parent.sort_order
        ) + 1

        line = self._add_line_internal(
            parent.quote_id,
            {
                **data,
                "product_id": product_id,
                "unit_cost": unit_cost,
                "line_role": LineRole.OPTIONAL,
                "is_included": True,
                "parent_line_id": parent_line_id,
                "line_type": LineType.PRODUCT,
            },
            sibling_sort,
        )
        self.db.commit()
        return line

    def toggle_line_included(self, line_id: int) -> None:
        """
        Toggle is_included on a single line.

        Cascades when necessary:
        - Upgrade option toggled ON  → deselects parent + all sibling upgrade options.
        - Primary line toggled ON    → deselects all upgrade_option children.
        """
        line = self._get_line_or_404(line_id)
        new_state = not line.is_included
        line.is_included = new_state

        if new_state and line.line_role == LineRole.UPGRADE_OPTION and line.parent_line_id:
            # Upgrading: exclude original + competing siblings
            parent = self._get_line_or_404(line.parent_line_id)
            parent.is_included = False
            for sibling in parent.children:
                if sibling.id != line_id and sibling.line_role == LineRole.UPGRADE_OPTION:
                    sibling.is_included = False

        elif new_state and line.line_role == LineRole.PRIMARY:
            # Restoring original: deselect all upgrade options under this parent
            for child in line.children:
                if child.line_role == LineRole.UPGRADE_OPTION:
                    child.is_included = False

        self.db.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_or_404(self, quote_id: int) -> Quote:
        q = self.db.query(Quote).filter(Quote.id == quote_id).first()
        if q is None:
            raise ValueError(f"Quote {quote_id} not found")
        return q

    def _get_line_or_404(self, line_id: int) -> QuoteLine:
        line = self.db.query(QuoteLine).filter(QuoteLine.id == line_id).first()
        if line is None:
            raise ValueError(f"QuoteLine {line_id} not found")
        return line

    def _preferred_vendor_cost(self, product_id: int) -> float:
        """Return the preferred vendor cost for a product, or 0.0 if not found."""
        from app.models.product import ProductVendorSource
        src = (
            self.db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.product_id == product_id,
                ProductVendorSource.is_preferred == True,   # noqa: E712
                ProductVendorSource.is_active == True,      # noqa: E712
            )
            .first()
        )
        return float(src.vendor_cost) if src else 0.0

    def _add_line_internal(self, quote_id: int, data: dict, sort_order: int) -> QuoteLine:
        line = QuoteLine(
            quote_id=quote_id,
            product_id=data.get("product_id"),
            line_type=data.get("line_type", LineType.PRODUCT),
            line_role=data.get("line_role", LineRole.PRIMARY),
            is_included=bool(data.get("is_included", True)),
            option_label=data.get("option_label") or None,
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
