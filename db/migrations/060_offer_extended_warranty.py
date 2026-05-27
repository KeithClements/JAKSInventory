"""Migration 060: quotes.offer_extended_warranty flag.

Tristate-ish: 0 = single-table PDF (current behavior), 1 = render
side-by-side Option A (Standard) / Option B (Extended) in PDF + email.
Per-line warranty data is unchanged; this flag only toggles
*presentation*.
"""
from __future__ import annotations


def migrate(conn) -> None:
    cur = conn.execute("PRAGMA table_info(quotes)")
    cols = {row[1] for row in cur.fetchall()}
    if "offer_extended_warranty" not in cols:
        conn.execute(
            "ALTER TABLE quotes ADD COLUMN offer_extended_warranty INTEGER DEFAULT 0"
        )
    conn.commit()
