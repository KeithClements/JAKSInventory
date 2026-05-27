"""Migration 054: Purchase Order lifecycle enhancements.

Adds to purchase_orders:
  - sent_at         TIMESTAMP  — when PO was sent
  - sent_by         TEXT       — user who sent it
  - sent_method     TEXT       — 'email' | 'print' | 'manual'
  - confirmed_at    TIMESTAMP  — when vendor confirmed
  - vendor_confirm_number TEXT — vendor PO / confirmation #
  - close_reason    TEXT       — reason if closed with outstanding qty
  - tracking_number TEXT
  - resend_count    INTEGER DEFAULT 0
  - last_resent_at  TIMESTAMP
"""
from __future__ import annotations


def migrate(conn) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
    additions = [
        ("sent_at",               "TIMESTAMP"),
        ("sent_by",               "TEXT"),
        ("sent_method",           "TEXT DEFAULT 'manual'"),
        ("confirmed_at",          "TIMESTAMP"),
        ("vendor_confirm_number", "TEXT"),
        ("close_reason",          "TEXT"),
        ("tracking_number",       "TEXT"),
        ("resend_count",          "INTEGER DEFAULT 0"),
        ("last_resent_at",        "TIMESTAMP"),
    ]
    for col, typedef in additions:
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE purchase_orders ADD COLUMN {col} {typedef}")
            except Exception:
                pass
    conn.commit()
