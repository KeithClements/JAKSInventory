"""Offline fuel-surcharge suggester keyed off ZIP-3 distance approximation.

The lookup is intentionally rough — it's a hint the user can override.
Returns ``(amount, reason)`` for display in the quote dialog.
"""
from __future__ import annotations

from typing import Tuple

# Default HQ ZIP — overridden by the ``business_hq_zip`` setting if present.
DEFAULT_HQ_ZIP = "82001"


def _resolve_hq_zip3() -> str:
    """Return the configured HQ ZIP-3, falling back to ``DEFAULT_HQ_ZIP``."""
    try:
        from db.inventory import get_setting
        z = (get_setting("business_hq_zip", DEFAULT_HQ_ZIP) or DEFAULT_HQ_ZIP).strip()
    except Exception:
        z = DEFAULT_HQ_ZIP
    return (z or DEFAULT_HQ_ZIP)[:3]


# Backwards-compat constant: some callers import HQ_ZIP3 directly.
HQ_ZIP3 = _resolve_hq_zip3()


def suggest_fuel_surcharge(ship_to_zip: str | None) -> Tuple[float, str]:
    """Return (amount, reason) for the given ship-to ZIP.

    Tiers:
      * same ZIP-3            → $15 (Local)
      * adjacent ZIP-3 (±1)   → $45 (<100mi)
      * farther in same Z1    → $95 (<250mi)
      * cross-region          → $175 (Long-haul)
    """
    z = (ship_to_zip or "").strip()[:5]
    if not z or not z[:3].isdigit():
        return 0.0, "Unknown ZIP"
    z3 = int(z[:3])
    hq_str = _resolve_hq_zip3()
    try:
        hq = int(hq_str)
    except ValueError:
        hq = z3
    delta = abs(z3 - hq)
    if delta == 0:
        return 15.0, "Local (same ZIP-3)"
    if delta <= 1:
        return 45.0, "<100 mi (adjacent ZIP-3)"
    if delta <= 5:
        return 95.0, "<250 mi (regional)"
    return 175.0, "Long-haul (cross-region)"


def suggest_for_quote(quote: dict) -> Tuple[float, str, str]:
    """Resolve a fuel-surcharge suggestion for a loaded quote dict.

    Returns ``(amount, reason, ship_to_zip)``. ``ship_to_zip`` is the ZIP
    used for the lookup (empty string if none could be resolved).
    """
    zip_code = ""
    # Prefer an explicit ship-to ZIP on the quote if present, else the
    # customer's primary ZIP.
    for key in ("ship_to_zip", "shipping_zip"):
        v = (quote or {}).get(key)
        if v:
            zip_code = str(v).strip()
            break
    if not zip_code:
        cust_id = (quote or {}).get("customer_id")
        if cust_id:
            try:
                from db.inventory import get_customer
                cust = get_customer(int(cust_id)) or {}
                zip_code = str(cust.get("zip") or "").strip()
            except Exception:
                zip_code = ""
    amount, reason = suggest_fuel_surcharge(zip_code)
    return amount, reason, zip_code

