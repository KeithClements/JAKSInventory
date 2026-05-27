"""Shared display formatters (dates & money).

Keeps every screen showing the same date style (MM/DD/YYYY) and
the same money style ($1,234.56) without scattering format strings.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def format_date(value: Any, *, fmt: str = "%m/%d/%Y", default: str = "") -> str:
    """Return ``value`` rendered as a US-style date.

    Accepts ``None``, ``datetime``, ``date``, or ISO/US strings.
    """
    if value in (None, ""):
        return default
    if isinstance(value, datetime):
        return value.strftime(fmt)
    if isinstance(value, date):
        return value.strftime(fmt)
    s = str(value).strip()
    if not s:
        return default
    # Try common patterns.
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                    "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s[: len(pattern) + 6].split(".")[0], pattern).strftime(fmt)
        except ValueError:
            continue
    # Fallback: trim to first 10 chars if it looks like YYYY-MM-DD prefix.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").strftime(fmt)
        except ValueError:
            pass
    return s


def format_money(value: Any, *, symbol: str = "$", default: str = "") -> str:
    """Return ``value`` rendered as currency (``$1,234.56``)."""
    if value in (None, ""):
        return default
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    negative = d < 0
    d = abs(d).quantize(Decimal("0.01"))
    whole, _, frac = f"{d:.2f}".partition(".")
    whole_fmt = f"{int(whole):,}"
    out = f"{symbol}{whole_fmt}.{frac}"
    return f"-{out}" if negative else out
