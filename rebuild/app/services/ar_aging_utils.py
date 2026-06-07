"""
app/services/ar_aging_utils.py
================================
Shared AR-aging bucket helpers.

Used by ReportService (all-customer aging report), StatementService
(per-customer statement) and CRMService (balance widget on the customer
detail page).  All three surfaces must agree on bucket boundaries so the
numbers a customer sees on their statement match what AP sees on the
aging report.

Bucket thresholds (days since due_date; negative = not yet due):
  current  : not yet past due (days_late <= 0)
  1_30     : 1–30 days overdue
  31_60    : 31–60 days overdue
  61_90    : 61–90 days overdue
  over_90  : more than 90 days overdue

Kept as pure functions — no DB, no ORM — so they can be imported in any
layer without pulling in SQLAlchemy or FastAPI dependencies.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

# Canonical bucket keys in display order.
# ALL bucketing code must reference this tuple so renaming a bucket key
# or changing display order happens in exactly one place.
AGING_BUCKETS: tuple[str, ...] = ("current", "1_30", "31_60", "61_90", "over_90")


def zero_buckets() -> dict[str, float]:
    """Return a fresh ``{bucket: 0.0}`` dict in canonical display order."""
    return {k: 0.0 for k in AGING_BUCKETS}


def as_date(value: Any) -> date | None:
    """Normalise a datetime / date / None to a plain ``date`` for day-diff math.
    Returns ``None`` when ``value`` is ``None`` so callers can safely do
    ``as_date(inv.due_date) or as_date(inv.created_at)``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value  # already a date


def bucket_for(days_late: int) -> str:
    """Map days-past-due to a canonical bucket key.

    Positive means the invoice is overdue; zero or negative means it is not
    yet due (or due today) → "current".
    """
    if days_late <= 0:
        return "current"
    if days_late <= 30:
        return "1_30"
    if days_late <= 60:
        return "31_60"
    if days_late <= 90:
        return "61_90"
    return "over_90"
