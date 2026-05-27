"""Add pricing_updated_at timestamp to products."""

from __future__ import annotations


def migrate(conn) -> None:
    try:
        conn.execute("ALTER TABLE products ADD COLUMN pricing_updated_at TEXT")
    except Exception:
        pass
    conn.commit()
