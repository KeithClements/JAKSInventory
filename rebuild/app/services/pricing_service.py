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

from app.models.product import Product, ProductVendorSource
from app.models.pricing import MarkupTier
from app.services.base import BaseService
from app.settings_utils import get_setting_value_db
from app.utils import calc_sell_price, calc_margin_pct

# Last-resort fallback only when the default_markup_pct setting row is missing or
# unparseable. The seeded settings value (30.0) is the real default; this literal
# exists so pricing never crashes on a corrupt/empty settings table.
_HARD_FALLBACK_MARKUP = 30.0


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
        else cost × (1 + resolve_markup_pct/100). Use this everywhere a product's
        estimated sell price is shown (search results, CSV export, pickers) so the
        number always reflects the current default markup setting."""
        if product.price_override and product.price_override > 0:
            return product.price_override
        return calc_sell_price(product.cost, self.resolve_markup_pct(product))

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
