"""
app/services/pricing_service.py
================================
Centralised pricing math — called by QuoteService, InvoiceService, SOService.

All markup / margin / surcharge calculations go here so the numbers are
consistent regardless of which screen the user is on.

Key rules (from schema interview):
  - Markup is applied to cost: sell_price = cost * (1 + markup_pct / 100)
  - Margin is derived: margin_pct = (sell - cost) / sell * 100
  - CC surcharge is a separate line item, never baked into unit price
  - Fuel/service charge is a separate line item (configurable %)
  - Restocking fees are calculated on the RETURN side (ReturnService)
  - Core charges are a pass-through, never marked up
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Sort sentinel: rules with a NULL created_at sort oldest (lowest) so a rule
# that DID record a timestamp wins the "newest" tiebreak.
_MIN_DT = datetime.min

from sqlalchemy.orm import Session

from app.models.product import Product, ProductVendorSource, ProductCategory
from app.models.pricing import MarkupTier, CustomerPriceRule
from app.constants import ScopeType, PriceMethod, target_margin_for_cost
from app.services.base import BaseService
from app.settings_utils import get_setting_value_db
from app.utils import calc_sell_price, calc_margin_pct

# Last-resort fallback only when the default_markup_pct setting row is missing or
# unparseable. The seeded settings value (30.0) is the real default; this literal
# exists so pricing never crashes on a corrupt/empty settings table.
_HARD_FALLBACK_MARKUP = 30.0


# ── Scope label (macro contract: rule.scope_label) ─────────────────────────────

def scope_label(db: Session, scope_type, scope_ref) -> str:
    """Human-readable label for a price-rule scope, for the UI deal row/chip.

    PRODUCT  → "<sku> — <short title>" (falls back to "SKU <ref>" if missing),
    CATEGORY → the ProductCategory.name (falls back to "Category <ref>"),
    BRAND    → the brand string as-is (e.g. "PAI"),
    CUSTOMER → "Whole account".

    Cheap — one lookup at most. Tolerates missing/garbled refs without raising
    so it can never break a render. Accepts either ScopeType enum members or the
    raw string values stored on the rule (case-insensitive)."""
    st = str(scope_type or "").strip().upper()
    ref = scope_ref

    if st == ScopeType.CUSTOMER:
        return "Whole account"

    if st == ScopeType.BRAND:
        return str(ref).strip() if ref not in (None, "") else "Brand"

    if st == ScopeType.PRODUCT:
        try:
            pid = int(ref)
        except (TypeError, ValueError):
            return f"SKU {ref}" if ref not in (None, "") else "Product"
        product = db.query(Product).filter(Product.id == pid).first()
        if product is None:
            return f"SKU {ref}"
        sku = (product.sku or "").strip()
        short = (product.title or "").strip()
        if short and len(short) > 48:
            short = short[:45].rstrip() + "…"
        if sku and short:
            return f"{sku} — {short}"
        return sku or short or f"SKU {ref}"

    if st == ScopeType.CATEGORY:
        try:
            cid = int(ref)
        except (TypeError, ValueError):
            return f"Category {ref}" if ref not in (None, "") else "Category"
        cat = db.query(ProductCategory).filter(ProductCategory.id == cid).first()
        if cat is not None and (cat.name or "").strip():
            return cat.name
        return f"Category {ref}"

    # Unknown scope type — degrade gracefully.
    return str(ref) if ref not in (None, "") else "—"


def _rule_chip_dict(rule, label_fn=None) -> dict | None:
    """Reshape a CustomerPriceRule ORM row into the chip-macro dict
    ``{scope_label, scope_type, price_method, price_value}`` (or None when rule is
    None). ``label_fn(scope_type, scope_ref) -> str`` resolves the human label;
    when absent, falls back to the raw scope_ref string (no DB lookup)."""
    if rule is None:
        return None
    if label_fn is not None:
        try:
            label = label_fn(rule.scope_type, rule.scope_ref)
        except Exception:  # noqa: BLE001 — label is presentation-only
            label = str(rule.scope_ref) if rule.scope_ref not in (None, "") else ""
    else:
        label = str(rule.scope_ref) if rule.scope_ref not in (None, "") else ""
    return {
        "scope_label": label,
        "scope_type": rule.scope_type,
        "price_method": rule.price_method,
        "price_value": rule.price_value,
    }


# ── Customer-price resolution result (CUSTOMER_PRICING_DESIGN.md §4) ────────────

@dataclass
class CustomerPriceResult:
    """Outcome of ``PricingService.resolve_customer_price``.

    ``price`` is None in three cases the caller must fall through on:
      - no rule matched (all flags False, ``source_rule`` None),
      - the product has no usable cost (``no_cost=True``).
    When ``price`` is not None a customer rule won and set the price; the chip /
    badge data (``source_rule``, ``overridden_rule``, ``margin_pct``,
    ``below_target``, ``below_cost``) describes it for the UI.
    """
    price: float | None = None
    source_rule: CustomerPriceRule | None = None
    overridden_rule: CustomerPriceRule | None = None
    margin_pct: float | None = None
    below_target: bool = False
    below_cost: bool = False
    no_cost: bool = False

    def as_dict(self, label_fn=None) -> dict:
        """Serialize for the line render-context (``render_ctx['customer_price']``).

        ``source_rule`` / ``overridden_rule`` are reshaped from raw ORM rows into
        the dict the ``price_rule_chip`` macro reads:
        ``{scope_label, scope_type, price_method, price_value}`` (None when there
        is no rule). The raw ORM rows are still exposed under ``source_rule_obj`` /
        ``overridden_rule_obj`` for any caller that needs them.

        ``label_fn(scope_type, scope_ref) -> str`` resolves the human scope label
        (pass ``CustomerPriceRuleService.scope_label``-style resolver). When None,
        ``scope_label`` falls back to the raw ``scope_ref`` string so the chip
        still renders without a DB lookup.
        """
        return {
            "price": self.price,
            "source_rule": _rule_chip_dict(self.source_rule, label_fn),
            "overridden_rule": _rule_chip_dict(self.overridden_rule, label_fn),
            # Raw ORM rows kept for callers that need the full object.
            "source_rule_obj": self.source_rule,
            "overridden_rule_obj": self.overridden_rule,
            "margin_pct": self.margin_pct,
            "below_target": self.below_target,
            "below_cost": self.below_cost,
            "no_cost": self.no_cost,
        }


class PricingService(BaseService):
    """
    All price calculation helpers.
    Stateless math — does NOT write to the database.
    """

    # ── Markup resolution (O5 — settings-backed default) ──────────────────────

    def default_markup_pct(self) -> float:
        """The configured global default markup %, from the default_markup_pct
        setting. Falls back to a hard literal only if the setting is missing or
        unparseable (so a corrupt settings table can't crash pricing)."""
        raw = get_setting_value_db(self.db, "default_markup_pct", str(_HARD_FALLBACK_MARKUP))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return _HARD_FALLBACK_MARKUP

    def markup_tiers_active(self) -> bool:
        """True when the cost-bracket pricing grid is enabled (markup_tiers_active
        setting = 'true'). Defaults FALSE — no re-pricing until owner flips it."""
        raw = get_setting_value_db(self.db, "markup_tiers_active", "false") or "false"
        return raw.strip().lower() == "true"

    def resolve_markup_pct_for_cost(self, cost: float) -> float:
        """Look up the tiered markup % for a COGS value from the active MarkupTier
        rows (ordered by min_cost asc so the first match wins). Falls back to
        default_markup_pct if no tier matches (e.g. table is empty)."""
        tiers = (
            self.db.query(MarkupTier)
            .filter(MarkupTier.is_active == True)  # noqa: E712
            .order_by(MarkupTier.sort_order, MarkupTier.min_cost)
            .all()
        )
        for tier in tiers:
            if tier.matches(cost):
                return tier.markup_pct
        return self.default_markup_pct()

    def resolve_markup_pct(self, product: Product) -> float:
        """The effective markup % for a product. Precedence (single source of truth):
          1. product.markup_pct — always wins when set (even 0 %).
          2. (IF markup_tiers_active) tiered-by-cost from MarkupTier table.
          3. Flat default_markup_pct setting.
        Callers must NOT fork this formula or re-implement the fallback literal."""
        if product.markup_pct is not None:
            return product.markup_pct
        if self.markup_tiers_active():
            return self.resolve_markup_pct_for_cost(product.cost or 0.0)
        return self.default_markup_pct()

    def sell_price_for(self, product: Product) -> float:
        """Settings-aware sell price for a product: honor price_override first,
        else basis × (1 + resolve_markup_pct/100). Use this everywhere a product's
        estimated sell price is shown (search results, CSV export, pickers) so the
        number always reflects the current default markup setting.

        ``basis`` is the moving-average COGS (``product.cost``); for an un-received
        part whose COGS is still 0, it falls back to the preferred/any-active vendor
        source cost so the estimate isn't $0 (QA HIGH #3). The fallback query only
        runs for the cost==0 case, so received-stock paths (e.g. the 31k-row CSV
        export) keep their zero-query behaviour."""
        if product.price_override and product.price_override > 0:
            return product.price_override
        basis = product.cost
        if not basis or basis <= 0:
            fallback = self.get_best_vendor_cost(product.id)
            if fallback and fallback > 0:
                basis = fallback
        return calc_sell_price(basis, self.resolve_markup_pct(product))

    # ── Customer-tier pricing (P2 — Customer.pricing_tier) ────────────────────

    def tier_discount_pct(self, pricing_tier: str) -> float:
        """Per-tier discount % applied after the normal sell-price calculation.
        'standard' always returns 0.0 — no adjustment.  Other tiers read from
        ``tier_<name>_discount_pct`` settings (default 0.0 = no change until the
        owner configures them via Settings → Pricing).
        Valid tiers: standard | wholesale | fleet | dealer."""
        if not pricing_tier or pricing_tier == "standard":
            return 0.0
        raw = get_setting_value_db(self.db, f"tier_{pricing_tier}_discount_pct", "0.0")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    def sell_price_for_tier(self, product: Product, pricing_tier: str | None) -> float | None:
        """Tier-adjusted sell price for a product.
        Returns ``None`` when the tier discount is 0 % (standard tier or
        not yet configured) so callers fall through to ``product.selling_price``
        unchanged — zero overhead for the common case.
        When a non-zero discount is configured: sell_price × (1 - discount/100)."""
        discount = self.tier_discount_pct(pricing_tier or "standard")
        if discount == 0.0:
            return None
        base = self.sell_price_for(product)
        return round(base * (1 - discount / 100), 2)

    def calculate_sell_price(self, cost: float, markup_pct: float) -> float:
        """Return sell price given cost and markup percent."""
        return calc_sell_price(cost, markup_pct)

    def calculate_margin_pct(self, cost: float, sell_price: float) -> float:
        """Return gross margin % given cost and sell price."""
        return calc_margin_pct(sell_price, cost)

    def calculate_markup_from_margin(self, margin_pct: float) -> float:
        """
        Convert a target margin % to the equivalent markup %.
        margin = (sell - cost) / sell  →  markup = margin / (1 - margin)
        """
        if margin_pct >= 100:
            raise ValueError("margin_pct must be less than 100")
        ratio = margin_pct / 100
        return round(ratio / (1 - ratio) * 100, 4)

    def apply_discount(self, unit_price: float, discount_pct: float) -> float:
        """Return discounted unit price."""
        return round(unit_price * (1 - discount_pct / 100), 2)

    def calculate_cc_surcharge(self, subtotal: float, surcharge_pct: float) -> float:
        """Return credit card surcharge amount (separate line item)."""
        return round(subtotal * (surcharge_pct / 100), 2)

    def calculate_fuel_service_charge(self, subtotal: float, fsc_pct: float) -> float:
        """Return fuel/service charge amount (separate line item)."""
        return round(subtotal * (fsc_pct / 100), 2)

    def calculate_tax(self, subtotal: float, tax_rate: float) -> float:
        """Return sales tax amount."""
        return round(subtotal * (tax_rate / 100), 2)

    def calculate_invoice_total(
        self,
        subtotal: float,
        tax_amount: float,
        core_total: float,
        shipping_charge: float,
        cc_surcharge: float,
        fsc_amount: float,
    ) -> float:
        """Sum all components into the invoice grand total."""
        return round(subtotal + tax_amount + core_total + shipping_charge + cc_surcharge + fsc_amount, 2)

    def get_best_vendor_cost(self, product_id: int) -> float | None:
        """
        Look up the preferred vendor's current cost for a product.
        Returns None if no vendor source exists.
        """
        source = (
            self.db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.product_id == product_id,
                ProductVendorSource.is_preferred == True,  # noqa: E712
                ProductVendorSource.is_active == True,     # noqa: E712
            )
            .first()
        )
        if source:
            return source.vendor_cost
        # Fall back to any active source
        any_source = (
            self.db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.product_id == product_id,
                ProductVendorSource.is_active == True,  # noqa: E712
            )
            .order_by(ProductVendorSource.vendor_cost)
            .first()
        )
        return any_source.vendor_cost if any_source else None

    def get_sell_price_for_product(self, product: Product, markup_pct_override: float | None = None) -> float:
        """
        Return the correct sell price for a product, respecting price_override.
        Uses markup_pct_override if supplied, else product.markup_pct, else default_markup_pct setting.
        Callers that need the setting-backed default should pass markup_pct_override from settings.
        """
        if product.price_override and product.price_override > 0:
            return product.price_override
        markup = (
            markup_pct_override
            if markup_pct_override is not None
            else self.resolve_markup_pct(product)
        )
        return calc_sell_price(product.cost, markup)

    # ── Customer-specific pricing (CUSTOMER_PRICING_DESIGN.md) ─────────────────

    @staticmethod
    def price_from_rule(rule: CustomerPriceRule, cost: float) -> float:
        """Compute a cost-plus price for a rule against ``cost``.
        markup → cost × (1 + value/100); margin → cost / (1 - value/100).
        Margin ≥ 100 % is clamped to a tiny positive denominator so it never
        divides by zero (defensive; rules are validated value < 100 on margin)."""
        v = rule.price_value or 0.0
        if rule.price_method == PriceMethod.MARGIN:
            denom = 1 - (v / 100.0)
            if denom <= 0:
                denom = 0.0001
            return round(cost / denom, 2)
        # default / markup
        return round(cost * (1 + v / 100.0), 2)

    def _descendant_category_ids(self, root_id: int) -> set[int]:
        """All category ids at-or-below ``root_id`` in the 3-level adjacency tree.
        A CATEGORY rule on a parent applies to every child/grandchild category."""
        all_cats = self.db.query(
            ProductCategory.id, ProductCategory.parent_id
        ).all()
        children: dict[int, list[int]] = {}
        for cid, pid in all_cats:
            if pid is not None:
                children.setdefault(pid, []).append(cid)
        out: set[int] = set()
        stack = [root_id]
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(children.get(cur, []))
        return out

    def _rule_matches_product(self, rule: CustomerPriceRule, product: Product) -> bool:
        """True when a rule's scope covers this product (scope match only —
        date/qty/active filtering happens in resolve_customer_price)."""
        st = rule.scope_type
        if st == ScopeType.CUSTOMER:
            return True
        if st == ScopeType.PRODUCT:
            try:
                return rule.scope_ref is not None and int(rule.scope_ref) == product.id
            except (TypeError, ValueError):
                return False
        if st == ScopeType.BRAND:
            return bool(rule.scope_ref) and (rule.scope_ref == (product.brand or ""))
        if st == ScopeType.CATEGORY:
            if product.category_id is None or rule.scope_ref is None:
                return False
            try:
                root = int(rule.scope_ref)
            except (TypeError, ValueError):
                return False
            return product.category_id in self._descendant_category_ids(root)
        return False

    # Specificity rank: higher number = more specific. Fixed ladder
    # PRODUCT > BRAND > CATEGORY > CUSTOMER — when a brand deal AND a category deal
    # both hit a part, the BRAND deal wins (owner-chosen 2026-06: predictable beats
    # "narrowest scope wins").
    _SCOPE_RANK = {
        ScopeType.PRODUCT: 4,
        ScopeType.BRAND: 3,
        ScopeType.CATEGORY: 2,
        ScopeType.CUSTOMER: 1,
    }

    def resolve_customer_price(
        self,
        product: Product,
        customer,
        qty: float = 1,
        as_of: date | None = None,
    ) -> CustomerPriceResult:
        """Step-0 of the pricing waterfall: the customer's best matching price rule.

        Returns a :class:`CustomerPriceResult`. ``price`` is None (caller falls
        through to the normal tier/markup waterfall) when no rule matches OR the
        product has no usable cost (``no_cost=True``). When a rule wins, ``price``
        is the cost-plus number and the chip/badge fields describe it.

        Matching: PRODUCT→product.id, CATEGORY→category + all descendant
        categories, BRAND→product.brand, CUSTOMER→all. Filters: is_active, the
        date window (NULLs open, ``as_of`` defaults to today), and qty_min ≤ qty
        (NULL = any). Ranking: PRODUCT > BRAND > CATEGORY > CUSTOMER (a fixed
        ladder — a brand deal beats a category deal when both hit a part), then
        higher qualifying qty_min, then newest created_at. ``overridden_rule`` is
        the next-most-specific qualifying rule (the "show both" runner-up).
        """
        if product is None or customer is None:
            return CustomerPriceResult()

        as_of = as_of or date.today()
        cost = float(product.cost or 0.0)

        rules = (
            self.db.query(CustomerPriceRule)
            .filter(CustomerPriceRule.customer_id == customer.id)
            .all()
        )

        qualifying: list[CustomerPriceRule] = []
        for r in rules:
            if not r.is_active:
                continue
            if not self._rule_matches_product(r, product):
                continue
            # Date window (NULLs open)
            if r.effective_from is not None and as_of < r.effective_from:
                continue
            if r.effective_to is not None and as_of > r.effective_to:
                continue
            # Qty break (NULL = any qty)
            if r.qty_min is not None and qty < r.qty_min:
                continue
            qualifying.append(r)

        if not qualifying:
            return CustomerPriceResult()

        # Sort by the precedence ladder. We rank descending so the winner is [0].
        #  1. specificity (PRODUCT > BRAND > CATEGORY > CUSTOMER — fixed ladder)
        #  2. higher qualifying qty_min wins  (None treated as -inf)
        #  3. newest created_at
        def _qty_key(r: CustomerPriceRule) -> float:
            return r.qty_min if r.qty_min is not None else float("-inf")

        def _created_key(r: CustomerPriceRule):
            return r.created_at or _MIN_DT

        def _sort_key(r: CustomerPriceRule):
            # Tuple sorted descending (reverse=True): more-specific scope first,
            # then higher qualifying qty_min, then newest. BRAND > CATEGORY is a
            # fixed ladder in _SCOPE_RANK, so the two never tie — no SKU-count
            # tiebreak needed.
            return (
                self._SCOPE_RANK.get(r.scope_type, 0),
                _qty_key(r),
                _created_key(r),
            )

        ordered = sorted(qualifying, key=_sort_key, reverse=True)
        winner = ordered[0]
        runner_up = ordered[1] if len(ordered) > 1 else None

        if cost <= 0:
            # Cost-plus can't compute — flag and fall through to normal pricing.
            return CustomerPriceResult(
                price=None, source_rule=winner, overridden_rule=runner_up,
                no_cost=True,
            )

        price = self.price_from_rule(winner, cost)
        margin_pct = calc_margin_pct(price, cost)
        target = target_margin_for_cost(cost)
        return CustomerPriceResult(
            price=price,
            source_rule=winner,
            overridden_rule=runner_up,
            margin_pct=margin_pct,
            below_target=(margin_pct < target),
            below_cost=(price < cost),
            no_cost=False,
        )

    def last_price_for(self, customer, product) -> dict | None:
        """Most-recent FINALIZED invoice line for this customer+product, as a
        display-only click-to-apply hint (NEVER auto-fills; never mutates).

        Returns ``{unit_price, margin_pct, doc_ref, date}`` or None. "Finalized"
        = the invoice is not DRAFT and not VOID (OPEN/PARTIAL/PAID). Uses the
        existing ix_invoice_lines_product_id index via the product_id filter.
        """
        if customer is None or product is None:
            return None
        from app.models.invoice import Invoice, InvoiceLine
        from app.constants import InvoiceStatus

        row = (
            self.db.query(InvoiceLine, Invoice)
            .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
            .filter(
                InvoiceLine.product_id == product.id,
                Invoice.customer_id == customer.id,
                Invoice.status.notin_([InvoiceStatus.DRAFT, InvoiceStatus.VOID]),
            )
            .order_by(Invoice.id.desc(), InvoiceLine.id.desc())
            .first()
        )
        if row is None:
            return None
        line, inv = row
        return {
            "unit_price": line.unit_price,
            "margin_pct": line.margin_pct,
            "doc_ref": inv.invoice_number,
            "date": inv.created_at,
        }


# ── Customer price-rule CRUD (CUSTOMER_PRICING_DESIGN.md §5) ────────────────────

class CustomerPriceRuleValidationError(ValueError):
    """Raised when a rule payload fails validation (router maps to HTTP 400)."""


class CustomerPriceRuleBulkValidationError(CustomerPriceRuleValidationError):
    """Raised by ``create_rules_bulk`` when one or more rows fail validation.

    Carries ``errors`` = ``[{"index": i, "error": "..."}, ...]`` naming each
    offending row so the bulk route can return the per-index error list. Subclasses
    the single-row error so callers that catch the base type still behave."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"{len(errors)} invalid rule(s) in bulk request.")


class CustomerPriceRuleService(BaseService):
    """Create / read / update / soft-deactivate customer price rules.

    Thin business layer behind the price-rules router. Validation lives here so
    the router stays a view layer. Resolution math lives in PricingService.
    """

    def list_rules(self, customer_id: int, *, include_inactive: bool = True):
        """All rules for a customer, newest first. ``include_inactive=False``
        hides soft-deactivated rules (the live deals view)."""
        q = self.db.query(CustomerPriceRule).filter(
            CustomerPriceRule.customer_id == customer_id
        )
        if not include_inactive:
            q = q.filter(CustomerPriceRule.is_active == True)  # noqa: E712
        return q.order_by(CustomerPriceRule.id.desc()).all()

    def get_rule(self, rule_id: int) -> CustomerPriceRule | None:
        return (
            self.db.query(CustomerPriceRule)
            .filter(CustomerPriceRule.id == rule_id)
            .first()
        )

    # ── Validation ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_date(value) -> date | None:
        if value in (None, ""):
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise CustomerPriceRuleValidationError(
                f"Invalid date '{value}' — expected YYYY-MM-DD."
            ) from exc

    def _validate(self, data: dict) -> dict:
        """Validate + normalize a create/update payload. Returns the cleaned dict
        of column values; raises CustomerPriceRuleValidationError on bad input."""
        scope_type = str(data.get("scope_type", "") or "").strip().upper()
        if scope_type not in {s.value for s in ScopeType}:
            raise CustomerPriceRuleValidationError(
                f"scope_type must be one of {[s.value for s in ScopeType]}."
            )

        method = str(data.get("price_method", "") or "").strip().lower()
        if method not in {m.value for m in PriceMethod}:
            raise CustomerPriceRuleValidationError(
                "price_method must be 'markup' or 'margin'."
            )

        try:
            price_value = float(data.get("price_value"))
        except (TypeError, ValueError) as exc:
            raise CustomerPriceRuleValidationError("price_value must be a number.") from exc
        if price_value < 0:
            raise CustomerPriceRuleValidationError("price_value must be >= 0.")
        if method == PriceMethod.MARGIN and price_value >= 100:
            raise CustomerPriceRuleValidationError(
                "A margin price_value must be < 100 (margin can't be 100%+)."
            )

        # scope_ref required unless whole-customer.
        scope_ref = data.get("scope_ref")
        if scope_type == ScopeType.CUSTOMER:
            scope_ref = None
        else:
            if scope_ref in (None, ""):
                raise CustomerPriceRuleValidationError(
                    f"scope_ref is required for a {scope_type} rule."
                )
            scope_ref = str(scope_ref).strip()

        qty_min = data.get("qty_min")
        if qty_min in (None, ""):
            qty_min = None
        else:
            try:
                qty_min = float(qty_min)
            except (TypeError, ValueError) as exc:
                raise CustomerPriceRuleValidationError("qty_min must be a number.") from exc
            if qty_min < 0:
                raise CustomerPriceRuleValidationError("qty_min must be >= 0.")

        eff_from = self._parse_date(data.get("effective_from"))
        eff_to = self._parse_date(data.get("effective_to"))
        if eff_from and eff_to and eff_to < eff_from:
            raise CustomerPriceRuleValidationError(
                "effective_to cannot be before effective_from."
            )

        return {
            "scope_type": scope_type,
            "scope_ref": scope_ref,
            "price_method": method,
            "price_value": price_value,
            "qty_min": qty_min,
            "effective_from": eff_from,
            "effective_to": eff_to,
            "note": str(data.get("note", "") or "").strip(),
        }

    # ── Writers ───────────────────────────────────────────────────────────────

    def create_rule(self, customer_id: int, data: dict) -> CustomerPriceRule:
        clean = self._validate(data)
        rule = CustomerPriceRule(
            customer_id=customer_id,
            created_by=str(data.get("created_by", "") or ""),
            is_active=True,
            **clean,
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def create_rules_bulk(self, customer_id: int, rows: list[dict]) -> list[CustomerPriceRule]:
        """Transactional bulk create — all-or-nothing.

        Validates EVERY row first (same ``_validate`` as the single-create path).
        If ANY row fails, raises ``CustomerPriceRuleBulkValidationError`` carrying a
        per-row error list and writes NOTHING (no partial commit). On success all
        rows are flushed into ONE transaction and committed together; any failure
        during the write rolls the whole batch back.

        Returns the freshly-persisted rules (refreshed, with ids) in input order.
        """
        if not rows:
            raise CustomerPriceRuleValidationError("No rules supplied.")

        # Phase 1 — validate every row up front, collecting ALL errors so the
        # caller can report each offending index. Nothing is written yet.
        cleaned: list[dict] = []
        errors: list[dict] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append({"index": i, "error": "Each rule must be an object."})
                continue
            try:
                cleaned.append(self._validate(row))
            except CustomerPriceRuleValidationError as exc:
                errors.append({"index": i, "error": str(exc)})
        if errors:
            raise CustomerPriceRuleBulkValidationError(errors)

        # Phase 2 — single transaction. Flush all (assigns ids without committing),
        # commit once. Roll the whole batch back on any failure so the
        # all-or-nothing guarantee holds even on a late DB-level error.
        created: list[CustomerPriceRule] = []
        try:
            for clean, row in zip(cleaned, rows):
                rule = CustomerPriceRule(
                    customer_id=customer_id,
                    created_by=str(row.get("created_by", "") or ""),
                    is_active=True,
                    **clean,
                )
                self.db.add(rule)
                created.append(rule)
            self.db.flush()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        for rule in created:
            self.db.refresh(rule)
        return created

    def update_rule(self, rule_id: int, data: dict) -> CustomerPriceRule:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise CustomerPriceRuleValidationError(f"Price rule {rule_id} not found.")
        clean = self._validate(data)
        for key, value in clean.items():
            setattr(rule, key, value)
        if "is_active" in data:
            rule.is_active = bool(data["is_active"])
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def deactivate_rule(self, rule_id: int) -> CustomerPriceRule:
        """Soft-deactivate (is_active=False) — kept for history/audit."""
        rule = self.get_rule(rule_id)
        if rule is None:
            raise CustomerPriceRuleValidationError(f"Price rule {rule_id} not found.")
        rule.is_active = False
        self.db.commit()
        self.db.refresh(rule)
        return rule

    # ── List preview (margin-at-current-cost) ─────────────────────────────────

    def scope_label(self, rule: CustomerPriceRule) -> str:
        """Human-readable scope label for a rule (macro contract: rule.scope_label).
        Thin pass-through to the module-level ``scope_label`` helper."""
        return scope_label(self.db, rule.scope_type, rule.scope_ref)

    def rule_preview(self, rule: CustomerPriceRule) -> dict:
        """Per-rule list-row preview. For a PRODUCT rule we can show the exact
        price + margin at the product's current cost. For CATEGORY/BRAND/CUSTOMER
        rules the price is cost-relative (varies per SKU) — we return a
        ``cost_relative`` note instead of a single number, plus the entered
        method/value so the UI can render '30% margin' / 'cost +12%'."""
        out: dict = {
            "rule_id": rule.id,
            "scope_type": rule.scope_type,
            "scope_ref": rule.scope_ref,
            # scope_label — macro contract (customer_price_rule_row / price_rule_chip).
            "scope_label": scope_label(self.db, rule.scope_type, rule.scope_ref),
            "price_method": rule.price_method,
            "price_value": rule.price_value,
            "qty_min": rule.qty_min,
            "is_active": rule.is_active,
            "effective_from": rule.effective_from,
            "effective_to": rule.effective_to,
            "note": rule.note,
            "cost_relative": rule.scope_type != ScopeType.PRODUCT,
            "margin_at_current_cost": None,
            "price_at_current_cost": None,
            "below_target": None,
            # below_cost — macro contract (margin_badge red "loss" tone). Only a
            # PRODUCT rule can compute it (others are cost-relative); None until then.
            "below_cost": None,
        }
        if rule.scope_type == ScopeType.PRODUCT and rule.scope_ref:
            try:
                pid = int(rule.scope_ref)
            except (TypeError, ValueError):
                # margin_pct alias still present (None) for the macro contract.
                out["margin_pct"] = out["margin_at_current_cost"]
                return out
            product = self.db.query(Product).filter(Product.id == pid).first()
            if product and (product.cost or 0) > 0:
                cost = float(product.cost)
                price = PricingService.price_from_rule(rule, cost)
                margin = calc_margin_pct(price, cost)
                out["price_at_current_cost"] = price
                out["margin_at_current_cost"] = margin
                out["below_target"] = margin < target_margin_for_cost(cost)
                out["below_cost"] = price < cost
        # margin_pct — macro alias for the existing margin value (None for
        # cost-relative scopes; that's fine — the row shows the method label).
        out["margin_pct"] = out["margin_at_current_cost"]
        return out
