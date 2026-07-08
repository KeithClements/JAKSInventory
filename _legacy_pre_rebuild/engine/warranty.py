"""Warranty helpers (engine layer — UI-free, IO-free).

Handles:
  * Converting a product's JAKS warranty fields to a (months, type, mileage)
    tuple suitable for a quote/SO/invoice line default.
  * Rendering a human string for PDFs and UI cells.

Warranty types:
  * ``parts_only``   — "X Month Parts Only Warranty"
  * ``parts_labor``  — "X Month Parts & Labor Warranty"

Mileage limit (int or None):
  * ``None`` → "Unlimited Mileage"
  * int     → "{n:,} Mile Limit"
"""
from __future__ import annotations

from typing import Any

WARRANTY_TYPE_PARTS_ONLY = "parts_only"
WARRANTY_TYPE_PARTS_LABOR = "parts_labor"
_VALID_TYPES = {WARRANTY_TYPE_PARTS_ONLY, WARRANTY_TYPE_PARTS_LABOR}


def _to_months(value: Any, unit: str | None) -> int:
    try:
        v = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    u = (unit or "months").strip().lower()
    if u in ("day", "days"):
        return max(1, round(v / 30))
    if u in ("year", "years", "yr", "yrs"):
        return v * 12
    # month(s) or anything else → treat as months
    return v


def default_warranty_for_product(product: dict | None) -> tuple[int, str, int | None]:
    """Return ``(months, warranty_type, mileage_limit)`` for a product row.

    Reads JAKS warranty fields first, falls back to supplier warranty. If the
    product has no warranty info, returns ``(0, 'parts_only', None)``.

    A product dict may come from SELECT on the products table and expose
    ``jaks_warranty_enabled``, ``jaks_warranty_value``, ``jaks_warranty_unit``
    (and the supplier_* equivalents).
    """
    if not product:
        return (0, WARRANTY_TYPE_PARTS_ONLY, None)

    # Prefer JAKS warranty (what we sell to the customer).
    if product.get("jaks_warranty_enabled"):
        months = _to_months(
            product.get("jaks_warranty_value"), product.get("jaks_warranty_unit")
        )
        if months > 0:
            # JAKS warranties default to parts + labor.
            return (months, WARRANTY_TYPE_PARTS_LABOR, None)

    if product.get("supplier_warranty_enabled"):
        months = _to_months(
            product.get("supplier_warranty_value"),
            product.get("supplier_warranty_unit"),
        )
        if months > 0:
            # Supplier warranties are typically parts-only.
            return (months, WARRANTY_TYPE_PARTS_ONLY, None)

    return (0, WARRANTY_TYPE_PARTS_ONLY, None)


def supplier_standard_warranty_for_product(product: dict | None) -> tuple[int, str, int | None]:
    """Return the included supplier warranty for a product.

    This is the standard warranty shown on quote product lines. It intentionally
    ignores JAKS extended-warranty fields because those are paid upsells.
    """
    if not product or not product.get("supplier_warranty_enabled"):
        return (0, WARRANTY_TYPE_PARTS_ONLY, None)
    months = _to_months(
        product.get("supplier_warranty_value"),
        product.get("supplier_warranty_unit"),
    )
    if months <= 0:
        return (0, WARRANTY_TYPE_PARTS_ONLY, None)
    return (months, WARRANTY_TYPE_PARTS_ONLY, None)


def format_standard_warranty(months: int | None) -> str:
    """Render included warranty text for product-line display."""
    m = int(months or 0)
    if m <= 0:
        return ""
    if m % 12 == 0:
        years = m // 12
        duration = "1 Year" if years == 1 else f"{years} Year"
    else:
        duration = "1 Month" if m == 1 else f"{m} Month"
    return f"{duration} Standard Warranty"


def normalize_warranty_type(value: Any) -> str:
    v = (str(value or "")).strip().lower()
    if v in _VALID_TYPES:
        return v
    return WARRANTY_TYPE_PARTS_ONLY


def format_warranty_short(
    months: int | None,
    warranty_type: str | None,
    mileage_limit: int | None,
) -> str:
    """Compact single-line renderer for table cells (e.g. ``24mo P&L · Unltd``)."""
    m = int(months or 0)
    if m <= 0:
        return ""
    t = normalize_warranty_type(warranty_type)
    type_short = "P&L" if t == WARRANTY_TYPE_PARTS_LABOR else "Parts"
    if mileage_limit in (None, 0):
        mileage = "Unltd"
    else:
        mileage = f"{int(mileage_limit):,} mi"
    return f"{m}mo {type_short} · {mileage}"


def format_warranty_long(
    months: int | None,
    warranty_type: str | None,
    mileage_limit: int | None,
) -> str:
    """Full renderer used on PDFs.

    Examples:
      * ``"2 Year Parts & Labor Warranty, Unlimited Mileage"``
      * ``"18 Month Parts Only Warranty, 100,000 Mile Limit"``
    """
    m = int(months or 0)
    if m <= 0:
        return ""
    # Duration
    if m % 12 == 0:
        years = m // 12
        duration = f"{years} Year" if years == 1 else f"{years} Year"
    else:
        duration = f"{m} Month"
    # Type
    t = normalize_warranty_type(warranty_type)
    type_str = "Parts & Labor" if t == WARRANTY_TYPE_PARTS_LABOR else "Parts Only"
    # Mileage
    if mileage_limit in (None, 0):
        mileage = "Unlimited Mileage"
    else:
        mileage = f"{int(mileage_limit):,} Mile Limit"
    return f"{duration} {type_str} Warranty, {mileage}"


def format_baseline_coverage(product: dict | None) -> str:
    """Render the *manufacturer-included* baseline coverage for a product.

    Used by the quote PDF when ``offer_extended_warranty=1`` so Option A
    (Standard) shows what the customer already gets at no extra charge.

    Reads ``manufacturer_warranty_months`` and (optionally)
    ``manufacturer_warranty_type``. Falls back to a generic phrase.
    """
    if not product:
        return "Standard manufacturer coverage"
    months = 0
    try:
        months = int(product.get("manufacturer_warranty_months") or 0)
    except (TypeError, ValueError):
        months = 0
    if months <= 0:
        return "Standard manufacturer coverage"
    wtype = normalize_warranty_type(product.get("manufacturer_warranty_type"))
    type_str = "Parts & Labor" if wtype == WARRANTY_TYPE_PARTS_LABOR else "Parts Only"
    if months % 12 == 0:
        years = months // 12
        duration = f"{years} Year"
    else:
        duration = f"{months} Month"
    return f"{duration} {type_str} — Manufacturer"

