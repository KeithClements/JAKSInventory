"""Migration 079: MAP (Minimum Advertised Price) on products.

Adds ``products.map_price REAL DEFAULT 0`` so the UI can warn (per F4
spec: warn-and-confirm, not blocking) when a line's unit price is being
discounted below the manufacturer's minimum advertised price.

Zero / NULL means "no MAP enforced" — the default state. Manufacturer-
level MAP is out of scope; this lives on the product row.
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


def _add_column(conn, table: str, column: str, ddl: str) -> None:
    if _has_column(conn, table, column):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    except Exception:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {ddl}")


def migrate(conn) -> None:
    _add_column(conn, "products", "map_price", "map_price REAL DEFAULT 0")
    conn.commit()
