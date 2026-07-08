"""Migration 061: fuel_surcharge column on quotes + sales_orders.

Stored as USD; rendered as a dedicated PDF/email line between Freight
and Tax. UI surfaces the spinbox + 4 quick presets on the Shipping tab.
"""
from __future__ import annotations


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate(conn) -> None:
    _ensure_column(conn, "quotes", "fuel_surcharge", "REAL DEFAULT 0")
    _ensure_column(conn, "sales_orders", "fuel_surcharge", "REAL DEFAULT 0")
    conn.commit()
