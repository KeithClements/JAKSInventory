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
    AuditAction, EntityType, LineRole, LineType, LostReason,
    NON_DISCOUNTABLE_LINE_TYPES,
    QuoteOutcome, QuoteStatus, SOPaymentMode,
)
from app.models.quote import LostSaleLog, Quote, QuoteLine
from app.services.base import BaseService, apply_product_line_defaults
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

        Per plan: auto-applies customer.discount_pct when the caller does not
        explicitly supply discount_pct AND the line type is discountable.
        Non-discountable line types (core charges, freight, surcharges, etc.) are
        forced to discount_pct=0 regardless of the caller's value.

        Returns a list of all newly-created lines: [primary_line] normally, or
        [primary_line, core_line] when the product carries a core charge and this
        is a top-level PRODUCT line.  Callers that only need the primary line can
        still do `lines[0]`.
        """
        from app.models.product import Product
        from app.models.customer import Customer

        quote = self._get_or_404(quote_id)
        sort_order = max((ln.sort_order for ln in quote.lines), default=-1) + 1

        # Fetch customer once — used for both tier pricing and discount default below.
        customer = self.db.query(Customer).filter(Customer.id == quote.customer_id).first()

        unit_cost = float(data.get("unit_cost", 0.0))
        if product_id is not None and unit_cost == 0.0:
            unit_cost = self._preferred_vendor_cost(product_id)

        # upgrade_option lines default to excluded unless caller explicitly set is_included
        merged = {**data, "product_id": product_id, "unit_cost": unit_cost}
        # Backfill description / price from the product so an immediate-add POST of
        # just product_id + qty yields a complete line (unit_cost resolved above).
        if product_id is not None:
            _product = self.db.query(Product).filter(Product.id == product_id).first()
            # Tier-adjusted price: wholesale/fleet/dealer customers get a configured
            # discount off the normal sell price; standard customers get None (no-op).
            _tier_price = None
            if _product and customer:
                from app.services.pricing_service import PricingService as _PS
                _tier_price = _PS(self.db, self.current_user_id).sell_price_for_tier(
                    _product, customer.pricing_tier
                )
            apply_product_line_defaults(_product, merged, include_price=True, tier_price=_tier_price)
        # Optionals AND upgrade-options default to EXCLUDED from the quote total — the
        # customer opts in. (Owner decision 2026-05-31 "A": optional add-ons are quoted
        # separately, not baked into the base total.)
        if merged.get("line_role") in (LineRole.UPGRADE_OPTION, LineRole.OPTIONAL) and "is_included" not in data:
            merged["is_included"] = False

        # Auto-apply customer discount / enforce non-discountable rules
        line_type = merged.get("line_type", LineType.PRODUCT)
        if line_type in NON_DISCOUNTABLE_LINE_TYPES:
            # Force zero regardless of what the caller passed
            merged["discount_pct"] = 0.0
        elif "discount_pct" not in data:
            # Auto-apply customer default when caller didn't specify
            merged["discount_pct"] = float(customer.discount_pct) if customer else 0.0

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

    @staticmethod
    def _normalize_lost_reason(value: str) -> tuple[str, str]:
        """Map a submitted reason to (LostReason value, leftover free-text).

        A recognised LostReason returns (reason, ""). Anything else maps to OTHER
        and returns the raw text as leftover so it can be preserved in the note —
        this also keeps legacy callers that passed a free string lossless."""
        raw = str(value or "").strip()
        try:
            return LostReason(raw.lower()), ""
        except ValueError:
            return LostReason.OTHER, raw

    def mark_lost(
        self,
        quote_id: int,
        lost_reason: str,
        *,
        note: str = "",
        competitor_name: str | None = None,
        competitor_price: float | None = None,
    ) -> None:
        """Mark a quote lost with a structured reason (P2-D7).

        ``lost_reason`` is a LostReason value; an unrecognised string maps to
        OTHER and is preserved in the note (legacy callers stay lossless). Writes
        one LostSaleLog row per product line (for product-level lost analysis),
        and a single quote-level row when the quote has no product lines, so a
        lost sale is ALWAYS captured. For COMPETITOR, the optional competitor
        name/price ride along on the log rows."""
        quote = self._get_or_404(quote_id)
        quote.status = QuoteStatus.DECLINED
        quote.outcome = QuoteOutcome.LOST

        reason, leftover = self._normalize_lost_reason(lost_reason)
        note = (note or "").strip() or leftover
        # Human-readable reason on the quote record: code + optional note.
        quote.lost_reason = f"{reason} — {note}" if note else reason

        # Competitor fields only meaningful for the COMPETITOR reason.
        comp_name = (competitor_name or "").strip() or None
        if reason != LostReason.COMPETITOR:
            comp_name = None
            competitor_price = None

        row_notes = note or f"Quote {quote.quote_number} lost"

        def _log(product_id: int | None) -> None:
            self.db.add(LostSaleLog(
                quote_id=quote_id,
                customer_id=quote.customer_id,
                product_id=product_id,
                reason=reason,
                competitor_name=comp_name,
                competitor_price=competitor_price,
                notes=row_notes,
            ))

        logged_any = False
        for line in quote.lines:
            if line.line_type == LineType.PRODUCT and line.product_id:
                _log(line.product_id)
                logged_any = True
        if not logged_any:
            _log(None)  # no product lines — still capture the lost sale

        self.audit(
            entity_type=EntityType.QUOTE,
            entity_id=quote_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=QuoteStatus.SENT,
            new_value=QuoteStatus.DECLINED,
            notes=quote.lost_reason,
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

        # Build SO line data — every included line EXCEPT core charges, mirroring
        # convert_to_invoice (R1-1 fix: the old PRODUCT-only filter silently
        # dropped MISC/WARRANTY/freight/note revenue on the quote→SO path).
        # Exclusions:
        #   - CORE_CHARGE (Bug 1 fix): SalesOrderService.create_sales_order
        #     re-derives discrete CORE_CHARGE child SOLines from the product's
        #     has_core / customer_core_charge fields — carrying the quote's core
        #     line would double-count the deposit.
        #   - is_included=False: optional / upgrade-option lines the customer
        #     did not opt into stay off the order.
        so_lines = [
            {
                "product_id": ln.product_id,
                "line_type": ln.line_type,
                "description": ln.description,
                "qty_ordered": ln.qty,
                "unit_price": ln.unit_price,
                "unit_cost": ln.unit_cost,
                "discount_pct": ln.discount_pct,
            }
            for ln in sorted(quote.lines, key=lambda l: l.sort_order)
            if ln.is_included and ln.line_type != LineType.CORE_CHARGE
        ]

        so_svc = SalesOrderService(self.db, self.current_user_id)
        so = so_svc.create_sales_order(
            customer_id=quote.customer_id,
            payment_mode=payment_mode,
            data={
                # Carry the engine/job reference fields forward (SO already
                # supports them; create_sales_order reads these keys).
                "customer_po_number": quote.customer_po_number,
                "customer_job_number": quote.customer_job_number,
                "esn": quote.esn,
                "engine_manufacturer": quote.engine_manufacturer or "",
                "engine_model": quote.engine_model or "",
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
            data={
                "quote_id": quote_id,
                "notes": quote.notes,
                # Direct quote→invoice (no SO) must still carry the engine/job refs;
                # create_invoice reads these keys (see invoice_service.py:144-148).
                "customer_po_number": quote.customer_po_number,
                "customer_job_number": quote.customer_job_number,
                "esn": quote.esn,
                "engine_manufacturer": quote.engine_manufacturer or "",
                "engine_model": quote.engine_model or "",
            },
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

        merged = {
            **data,
            "product_id": product_id,
            "unit_cost": unit_cost,
            "line_role": LineRole.UPGRADE_OPTION,
            "is_included": False,
            "parent_line_id": parent_line_id,
            "line_type": LineType.PRODUCT,
        }
        # Bug 7 — child lines get the same product backfill as main lines: an
        # immediate-add of just product_id yields description + price from the
        # product (apply_product_line_defaults only fills blanks, so an explicit
        # description/price from the caller still wins).
        if product_id is not None:
            from app.models.product import Product
            from app.models.customer import Customer
            _product = self.db.query(Product).filter(Product.id == product_id).first()
            # Tier pricing: derive customer from parent's quote, same as main add_line.
            _tier_price = None
            if _product:
                _parent_quote = self.db.query(Quote).filter(Quote.id == parent.quote_id).first()
                _cust = (
                    self.db.query(Customer).filter(Customer.id == _parent_quote.customer_id).first()
                    if _parent_quote else None
                )
                if _cust:
                    from app.services.pricing_service import PricingService as _PS
                    _tier_price = _PS(self.db, self.current_user_id).sell_price_for_tier(
                        _product, _cust.pricing_tier
                    )
            apply_product_line_defaults(_product, merged, include_price=True, tier_price=_tier_price)

        line = self._add_line_internal(parent.quote_id, merged, sibling_sort)
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
        line.  Optional lines are EXCLUDED from the total by default (owner decision
        2026-05-31 "A") — the customer opts in; they are quoted separately as add-ons.

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

        merged = {
            **data,
            "product_id": product_id,
            "unit_cost": unit_cost,
            "line_role": LineRole.OPTIONAL,
            "is_included": False,  # (A) optionals excluded from total — customer opts in
            "parent_line_id": parent_line_id,
            "line_type": LineType.PRODUCT,
        }
        # Bug 7 — same product backfill as main lines (description + price from the
        # product when the caller didn't supply them; explicit values still win).
        if product_id is not None:
            from app.models.product import Product
            from app.models.customer import Customer
            _product = self.db.query(Product).filter(Product.id == product_id).first()
            # Tier pricing: derive customer from parent's quote.
            _tier_price = None
            if _product:
                _parent_quote = self.db.query(Quote).filter(Quote.id == parent.quote_id).first()
                _cust = (
                    self.db.query(Customer).filter(Customer.id == _parent_quote.customer_id).first()
                    if _parent_quote else None
                )
                if _cust:
                    from app.services.pricing_service import PricingService as _PS
                    _tier_price = _PS(self.db, self.current_user_id).sell_price_for_tier(
                        _product, _cust.pricing_tier
                    )
            apply_product_line_defaults(_product, merged, include_price=True, tier_price=_tier_price)

        line = self._add_line_internal(parent.quote_id, merged, sibling_sort)
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

    # ── Duplicate ─────────────────────────────────────────────────────────────

    def duplicate_quote(self, quote_id: int) -> Quote:
        """
        R5 — Copy all lines from quote_id into a new quote with a fresh Q-YYYY-NNNN
        number. Sets is_duplicate_of_quote_id on the new quote. The original quote
        is unchanged. The new quote starts as DRAFT so it can be reassigned to a
        different customer before sending.

        Does NOT copy: outcome, lost_reason, converted_to_*, follow_up data.
        DOES copy: all lines (including upgrade options / optional lines), discount_pct,
                   notes, internal_notes, validity_days.
        """
        original = self._get_or_404(quote_id)
        year = datetime.utcnow().year
        new_number = bump_counter(self.db, "next_quote_number", "Q", year)
        validity_days = original.validity_days

        dup = Quote(
            quote_number=new_number,
            customer_id=original.customer_id,
            status=QuoteStatus.DRAFT,
            outcome=QuoteOutcome.PENDING,
            discount_pct=original.discount_pct,
            validity_days=validity_days,
            valid_until=datetime.utcnow() + timedelta(days=validity_days),
            notes=original.notes,
            internal_notes=original.internal_notes,
            is_duplicate_of_quote_id=quote_id,
        )
        self.db.add(dup)
        self.db.flush()

        # Clone lines — two-pass to guarantee parents exist before children regardless
        # of sort_order (a child could theoretically have a lower sort_order).
        old_id_to_new: dict[int, int] = {}

        def _clone_line(ln: QuoteLine, new_parent_id: int | None) -> None:
            new_line = QuoteLine(
                quote_id=dup.id,
                product_id=ln.product_id,
                line_type=ln.line_type,
                line_role=ln.line_role,
                is_included=ln.is_included,
                option_label=ln.option_label,
                is_optional=ln.is_optional,
                option_group=ln.option_group,
                parent_line_id=new_parent_id,
                description=ln.description,
                qty=ln.qty,
                unit_price=ln.unit_price,
                unit_cost=ln.unit_cost,
                discount_pct=ln.discount_pct,
                discount_overridden=ln.discount_overridden,
                is_core_line=ln.is_core_line,
                is_auto_generated=ln.is_auto_generated,
                is_locked_to_parent=ln.is_locked_to_parent,
                sort_order=ln.sort_order,
            )
            self.db.add(new_line)
            self.db.flush()
            old_id_to_new[ln.id] = new_line.id

        # Pass 1: top-level lines (no parent)
        for ln in sorted(original.lines, key=lambda l: l.sort_order):
            if ln.parent_line_id is None:
                _clone_line(ln, None)

        # Pass 2: child lines (re-map to new parent id)
        for ln in sorted(original.lines, key=lambda l: l.sort_order):
            if ln.parent_line_id is not None:
                _clone_line(ln, old_id_to_new.get(ln.parent_line_id))

        self.audit(
            entity_type=EntityType.QUOTE,
            entity_id=dup.id,
            action=AuditAction.CREATED,
            new_value={
                "quote_number": new_number,
                "duplicated_from_quote_id": quote_id,
                "original_quote_number": original.quote_number,
            },
        )
        self.db.commit()
        return dup

    # ── Line Discount Override ────────────────────────────────────────────────

    def update_line_discount(self, line_id: int, new_pct: float) -> QuoteLine:
        """
        R5 — Change the discount % on a single quote line.

        If new_pct differs from customer.discount_pct, sets discount_overridden=True
        so the UI can flag it and so that duplicate_quote preserves the override.
        Non-discountable line types (CORE_CHARGE, FREIGHT, etc.) are rejected.

        Audit logged when discount_overridden becomes True.
        """
        line = self._get_line_or_404(line_id)

        if line.line_type in NON_DISCOUNTABLE_LINE_TYPES:
            raise ValueError(
                f"Line type '{line.line_type}' is non-discountable — discount cannot be set"
            )

        # Load customer discount for override detection
        quote = self._get_or_404(line.quote_id)
        from app.models.customer import Customer
        customer = self.db.query(Customer).filter(Customer.id == quote.customer_id).first()
        customer_default = float(customer.discount_pct) if customer else 0.0

        old_pct = line.discount_pct
        line.discount_pct = round(float(new_pct), 4)
        line.discount_overridden = abs(new_pct - customer_default) > 0.0001

        if line.discount_overridden:
            self.audit(
                entity_type=EntityType.QUOTE,
                entity_id=line.quote_id,
                action=AuditAction.EDITED,
                old_value={"line_id": line_id, "discount_pct": old_pct},
                new_value={
                    "line_id": line_id,
                    "discount_pct": line.discount_pct,
                    "discount_overridden": True,
                    "customer_default_pct": customer_default,
                },
            )

        self.db.commit()
        return line

    # ── Private helpers ───────────────────────────────────────────────────────

    def update_header(self, quote_id: int, data: dict, submitted_updated_at: str | None = None) -> Quote:
        """Update quote-level notes and settings. Active (non-converted) quotes only."""
        quote = self._get_or_404(quote_id)
        self.check_version(quote, submitted_updated_at)
        if quote.status in (QuoteStatus.CONVERTED, QuoteStatus.DECLINED):
            raise ValueError("Cannot edit a converted or declined quote")
        # Whitelist of quote header fields the workspace can write. ESN/engine and
        # the customer reference fields mirror InvoiceService.update_header so the
        # same data the user enters on a quote persists (and later carries to SO).
        for field in (
            "notes", "internal_notes", "discount_pct", "validity_days",
            "customer_po_number", "customer_job_number", "esn",
            "engine_manufacturer", "engine_model",
        ):
            if field in data:
                setattr(quote, field, data[field])
        self.db.commit()
        return quote

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
