"""Migration 044 – Add warranty_percentage to products for per-product warranty rate."""
from __future__ import annotations


def migrate(conn) -> None:
    # Default 10% matches the existing hardcoded rate
    cols = {c[1] for c in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "warranty_percentage" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN warranty_percentage REAL DEFAULT 10.0"
        )
        conn.commit()
