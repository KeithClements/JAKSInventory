"""Quote tier classification.

Single source of truth for the Option A / B / C bucketing used by both
the on-screen quote totals and the PDF. Tiering is **derived** from
existing ``is_optional`` and ``parent_line_id`` flags — no schema change.

Tier rules
----------
- **A** — required parts: ``is_selected and not is_optional and not parent_line_id``.
- **B** — suggested add-ons: ``is_selected and is_optional and not parent_line_id``.
- **C** — extended warranty: ``is_selected and parent_line_id`` (regardless of ``is_optional``).

A line that is not selected is excluded.

Subtotal semantics
------------------
- ``a_total``  = sum of tier_a ``line_total`` + per-line shipping
- ``b_addons`` = sum of tier_b ``line_total`` + per-line shipping
- ``c_warranty`` = sum of tier_c ``line_total`` (warranty children
  rarely carry shipping, but include it if present)
- ``b_total`` = ``a_total + b_addons``
- ``c_total`` = ``b_total + c_warranty``

Tax, core deposit, fuel surcharge and CC fee are *not* included here —
callers add them to ``a_total`` as today.
"""
from __future__ import annotations

from typing import Iterable


def _line_amount(ln: dict) -> float:
    return float(ln.get("line_total") or 0) + float(ln.get("line_shipping") or 0)


def classify_lines(lines: Iterable[dict]) -> dict:
    """Bucket quote lines into tiers A/B/C and return their subtotals.

    Returns a dict with keys ``tier_a``, ``tier_b``, ``tier_c`` (lists),
    and ``a_total``, ``b_addons``, ``b_total``, ``c_warranty``, ``c_total``.
    """
    tier_a: list[dict] = []
    tier_b: list[dict] = []
    tier_c: list[dict] = []

    for ln in lines or []:
        if not ln.get("is_selected"):
            continue
        if ln.get("parent_line_id"):
            tier_c.append(ln)
        elif ln.get("is_optional"):
            tier_b.append(ln)
        else:
            tier_a.append(ln)

    a_total = sum(_line_amount(l) for l in tier_a)
    b_addons = sum(_line_amount(l) for l in tier_b)
    c_warranty = sum(_line_amount(l) for l in tier_c)
    b_total = a_total + b_addons
    c_total = b_total + c_warranty

    return {
        "tier_a": tier_a,
        "tier_b": tier_b,
        "tier_c": tier_c,
        "a_total": round(a_total, 2),
        "b_addons": round(b_addons, 2),
        "b_total": round(b_total, 2),
        "c_warranty": round(c_warranty, 2),
        "c_total": round(c_total, 2),
        "has_b": bool(tier_b),
        "has_c": bool(tier_c),
    }
