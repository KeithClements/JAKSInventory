"""Sales-tax rate lookup helpers.

Reads from the ``tax_rates`` table (migration 057). Rates are stored as
decimals (``0.029`` = 2.9%) but quote/invoice UIs work in *percent*
(``2.9``). This module returns *percent* to match those UIs.

Lookup precedence:
  1. Exact ZIP match (most specific)
  2. State default (``zip IS NULL``)
  3. ``None`` if state has no nexus row → caller treats as 0%
"""
from __future__ import annotations

from typing import Optional

from db.database import get_connection


def lookup_tax_for_zip(
    zip_code: Optional[str],
    state: Optional[str] = None,
) -> Optional[float]:
    """Return tax rate as a *percent* (e.g. ``2.9``) or ``None`` if no nexus.

    Args:
        zip_code: 5-digit ZIP. May be ``None``.
        state: 2-char state code; required if ``zip_code`` is not provided.
    """
    z = (zip_code or "").strip()[:5] or None
    s = (state or "").strip().upper()[:2] or None
    if not z and not s:
        return None

    conn = get_connection()
    try:
        # 1. Most-specific ZIP match.
        if z:
            cur = conn.execute(
                "SELECT rate FROM tax_rates WHERE zip = %s AND active = 1 LIMIT 1",
                (z,),
            )
            row = cur.fetchone()
            if row is not None:
                return float(row[0]) * 100.0

        # 2. State default.
        if s:
            cur = conn.execute(
                "SELECT rate FROM tax_rates "
                "WHERE state = %s AND zip IS NULL AND active = 1 LIMIT 1",
                (s,),
            )
            row = cur.fetchone()
            if row is not None:
                return float(row[0]) * 100.0
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return None
