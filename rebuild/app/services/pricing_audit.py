"""
app/services/pricing_audit.py
=============================
Locked-price margin alert. When an operator hand-edits a sell price it is LOCKED
against the scraper feed (correct — the human's price wins). But vendor cost keeps
updating underneath a locked price, so a price set at a healthy margin can silently
sink toward — or below — cost when the vendor raises their price.

This scan flags every LOCKED price whose margin (vs the product's current effective
cost) has fallen below the floor, so it lands on a review list instead of quietly
losing money. Read-only by default; `apply=True` sets needs_review + records the
count for the Settings badge.
"""
from __future__ import annotations

from app.models.product import Product
from app.settings_utils import get_setting_value_db, set_setting_value_db

_DEFAULT_FLOOR_PCT = 15.0


def _floor_pct(db, override: float | None) -> float:
    if override is not None:
        return float(override)
    try:
        return float(get_setting_value_db(db, "shopify_margin_floor_pct", "15"))
    except (TypeError, ValueError):
        return _DEFAULT_FLOOR_PCT


def scan_locked_below_margin(db, floor_pct: float = _DEFAULT_FLOOR_PCT) -> list[dict]:
    """Active, locked-price products whose margin vs effective cost < floor_pct.
    (A below-cost locked price is margin < 0 < floor, so it is included too.)
    Products with no known cost yet (effective_cost 0 — never received/quoted) are
    skipped: their margin is unknowable, not below floor."""
    out: list[dict] = []
    q = (db.query(Product)
         .filter(Product.price_override_locked == True,   # noqa: E712
                 Product.price_override.isnot(None),
                 Product.is_active == True))              # noqa: E712
    for p in q.yield_per(500):
        price = float(p.price_override or 0)
        cost = float(p.effective_cost or 0)
        if price <= 0 or cost <= 0:
            continue
        margin = (price - cost) / price * 100.0
        if margin < floor_pct:
            out.append({"id": p.id, "sku": p.sku, "price": round(price, 2),
                        "cost": round(cost, 2), "margin_pct": round(margin, 1),
                        "below_cost": margin < 0})
    # Worst margins first.
    out.sort(key=lambda r: r["margin_pct"])
    return out


def audit_locked_margins(db, *, floor_pct: float | None = None,
                         apply: bool = False) -> dict:
    """Scan + (optionally) flag. apply=True sets needs_review on each flagged
    product and records shopify_locked_margin_alert_count for the Settings badge.
    Returns {floor_pct, count, below_cost, flagged, applied}."""
    fp = _floor_pct(db, floor_pct)
    flagged = scan_locked_below_margin(db, fp)
    if apply:
        ids = [f["id"] for f in flagged]
        if ids:
            (db.query(Product).filter(Product.id.in_(ids))
             .update({Product.needs_review: True}, synchronize_session=False))
        set_setting_value_db(db, "shopify_locked_margin_alert_count", str(len(flagged)))
        db.commit()
    return {"floor_pct": fp, "count": len(flagged),
            "below_cost": sum(1 for f in flagged if f["below_cost"]),
            "flagged": flagged[:200], "applied": apply}
