"""
app/utils.py
============
Pure-math utility functions shared across models and services.
No database access, no imports from app modules — safe to import anywhere.
"""
from __future__ import annotations

import re


def calc_line_total(unit_price: float, qty: int, discount_pct: float = 0.0) -> float:
    """
    Standard line total with optional discount.
    Used by QuoteLine, SOLine, InvoiceLine, POLine, VendorBillLine.
    """
    discounted = unit_price * (1 - discount_pct / 100)
    return round(discounted * qty, 2)


def calc_margin_pct(unit_price: float, unit_cost: float) -> float:
    """
    Gross margin % = (sell - cost) / sell * 100.
    Returns 0.0 when either value is zero to avoid division errors.
    Used by QuoteLine and InvoiceLine.
    """
    if unit_price == 0 or unit_cost == 0:
        return 0.0
    return round((unit_price - unit_cost) / unit_price * 100, 1)


def tree_sort_lines(lines: list) -> list:
    """
    Return lines sorted parent-first, children inserted immediately after their parent.

    Handles "orphaned" children whose parent is not in the provided list
    (e.g. a selected upgrade option whose primary parent is excluded from the
    invoice total) — those children are promoted to the top level.

    Args:
        lines: Any iterable of objects with .id, .parent_line_id, .sort_order

    Returns:
        Flat list in display order.
    """
    line_ids = {ln.id for ln in lines}
    # Top-level: no parent, OR parent not in the working set (orphaned child promoted)
    top = sorted(
        [ln for ln in lines if ln.parent_line_id is None or ln.parent_line_id not in line_ids],
        key=lambda l: l.sort_order,
    )
    by_parent: dict[int, list] = {}
    for ln in lines:
        if ln.parent_line_id is not None and ln.parent_line_id in line_ids:
            by_parent.setdefault(ln.parent_line_id, []).append(ln)
    for v in by_parent.values():
        v.sort(key=lambda l: l.sort_order)

    result: list = []
    for ln in top:
        result.append(ln)
        result.extend(by_parent.get(ln.id, []))
    return result


def calc_sell_price(cost: float, markup_pct: float) -> float:
    """
    Sell price from cost + markup percentage.
    markup_pct = 30 means sell = cost * 1.30
    """
    return round(cost * (1 + markup_pct / 100), 2)


def normalize_part(s: str) -> str:
    """
    Fold a part / OEM / cross-reference number for matching: strip every
    non-alphanumeric character and lower-case the rest.  Lets a user find a
    part regardless of how separators or case were entered:

        normalize_part("OK-1")  == "ok1"
        normalize_part("ok 1")  == "ok1"
        normalize_part("OK.1")  == "ok1"

    Used by SearchService to match SKUs, cross-references, and vendor part
    numbers.  Pure function — safe to import anywhere.
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())
