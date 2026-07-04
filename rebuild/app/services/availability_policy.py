"""
app/services/availability_policy.py
===================================
The single source of truth for "what should this product look like on the
storefront right now?" — a PURE, DB-free decision so it is fully unit-testable
and identical whether it is called by the nightly reconcile, the instant
single-product push, or the staged migration script.

Two storefront availability MODELS (chosen per store via the
``shopify_availability_mode`` setting):

  • "hide"     — LEGACY. A part the vendor can't supply is flipped to DRAFT and
                 disappears from the storefront (404). Simple, but every
                 out-of-stock cycle churns indexed URLs and throws away SEO.

  • "sold_out" — the model the owner chose 2026-07-04. A part is BUYABLE when we
                 have it on the shelf OR any vendor stocks it; a part that is out
                 everywhere keeps a LIVE "Sold out" page (tracked, qty 0, can't
                 oversell) instead of 404-ing; only a genuinely discontinued /
                 deactivated part is truly hidden.

``desired_state(product)`` returns the sold-out-model target for one product.
The buyability rule is **own stock OR any vendor in stock**, and — critically —
an UNKNOWN vendor status ('' — the ~22k parts that never got an availability
import) is treated as *can still supply* so we never silently stop selling a
part just because we don't know the vendor's stock. Only an EXPLICIT vendor
out_of_stock with zero own stock produces a DENY / "Sold out".
"""
from __future__ import annotations

from dataclasses import dataclass

from app.constants import ProductStatus, VendorAvailability

# Storefront availability states (also the value pushed to the
# custom.availability_state metafield the theme reads for its badge/messaging).
STATE_IN_STOCK = "in_stock"                     # own shelf stock — "ships today"
STATE_AVAILABLE_TO_ORDER = "available_to_order"  # own 0, vendor supplies — drop-ship
STATE_SOLD_OUT = "sold_out"                      # out everywhere — live page, unbuyable
STATE_HIDDEN = "hidden"                          # discontinued / deactivated — DRAFT

# Shopify variant inventory policy values.
POLICY_CONTINUE = "CONTINUE"   # buyable past on-hand 0 (vendor drop-ships)
POLICY_DENY = "DENY"           # blocked at on-hand 0 (own stock is the only source)

_MODE_HIDE = "hide"
_MODE_SOLD_OUT = "sold_out"


def availability_mode(db) -> str:
    """The active storefront availability model ('hide' legacy | 'sold_out').
    Defaults to 'hide' so existing behavior (and every existing test) is unchanged
    until the owner runs the staged migration, which flips this setting."""
    from app.settings_utils import get_setting_value_db
    val = (get_setting_value_db(db, "shopify_availability_mode", _MODE_HIDE) or "").strip().lower()
    return _MODE_SOLD_OUT if val == _MODE_SOLD_OUT else _MODE_HIDE


@dataclass(frozen=True)
class DesiredState:
    state: str            # STATE_* — also the availability_state metafield value
    shopify_status: str   # "ACTIVE" | "DRAFT"
    inventory_policy: str  # POLICY_CONTINUE | POLICY_DENY
    tracked: bool         # variant inventory tracked (always True in this model)
    qty: int              # sellable OWN stock in whole packs (0 unless we hold it)
    buyable: bool         # can a shopper add it to cart right now?

    @property
    def hidden(self) -> bool:
        return self.state == STATE_HIDDEN


def _own_packs(product) -> int:
    """Sellable OWN stock in whole vendor packs (mirrors ShopifyService._sellable_qty)."""
    qty = max(0, int(getattr(product, "qty_available", 0) or 0))
    return qty // max(1, int(getattr(product, "pack_qty", 1) or 1))


def desired_state(product) -> DesiredState:
    """PURE sold-out-model target for one product. See module docstring for the
    buyability rule (own OR vendor stock; unknown vendor = can still supply)."""
    va = (getattr(product, "vendor_availability", "") or "").strip()
    own = _own_packs(product)

    # HIDDEN: only a genuinely discontinued or deactivated part leaves the
    # storefront entirely. (A part that is merely out of stock stays as a live
    # "Sold out" page — that is the whole point of this model.)
    if (not getattr(product, "is_active", True)
            or (getattr(product, "status", "") or "") == ProductStatus.DISCONTINUED
            or va == VendorAvailability.DISCONTINUED):
        return DesiredState(STATE_HIDDEN, "DRAFT", POLICY_DENY, True, own, False)

    # Can the vendor still supply it? In stock == yes; UNKNOWN ('') == treat as yes
    # (never block a sale just because we lack an availability reading); an EXPLICIT
    # out_of_stock == no.
    vendor_can_supply = va != VendorAvailability.OUT_OF_STOCK
    policy = POLICY_CONTINUE if vendor_can_supply else POLICY_DENY

    if own > 0:
        state = STATE_IN_STOCK
    elif vendor_can_supply:
        state = STATE_AVAILABLE_TO_ORDER
    else:
        state = STATE_SOLD_OUT

    buyable = own > 0 or vendor_can_supply
    return DesiredState(state, "ACTIVE", policy, True, own, buyable)
