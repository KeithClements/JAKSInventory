"""Migration 065: Vendor → Price Category mapping.

Replaces the manufacturer-keyed mapping introduced in migration 058 with a
vendor-keyed mapping. The legacy ``manufacturer_categories`` table is left
in place for back-compat but is no longer the primary source for the
product category resolver — vendor mapping wins, and manufacturer mapping
is consulted only as a fallback.
"""
from __future__ import annotations


def migrate(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_categories (
            vendor_id   INTEGER PRIMARY KEY REFERENCES vendors(id) ON DELETE CASCADE,
            category_id INTEGER NOT NULL  REFERENCES price_categories(id) ON DELETE CASCADE,
            updated_at  TEXT    DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vendor_categories_cat ON vendor_categories(category_id)"
    )
    conn.commit()
