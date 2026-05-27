"""Migration 074: add ``product_type`` column to products.

Introduces a discriminator that controls whether a product participates
in inventory tracking:

* ``'inventoried'``    — default; stock levels tracked, qty_on_hand
                         decremented on sale / incremented on receive.
* ``'non_inventoried'`` — sold on quotes/invoices at any quantity; no
                          stock tracking or reservation.  Useful for
                          extended warranties, freight, services, etc.
* ``'service'``        — reserved for future labour/flat-fee items.

All existing rows default to ``'inventoried'`` so there is no data migration.
"""
from __future__ import annotations


def _has_column(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            name = r[1] if not hasattr(r, "keys") else r["name"]
            if str(name) == column:
                return True
        return False
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def migrate(conn) -> None:
    if not _has_column(conn, "products", "product_type"):
        try:
            conn.execute(
                "ALTER TABLE products ADD COLUMN product_type TEXT NOT NULL DEFAULT 'inventoried'"
            )
        except Exception:
            conn.execute(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS product_type "
                "TEXT NOT NULL DEFAULT 'inventoried'"
            )
    conn.commit()
