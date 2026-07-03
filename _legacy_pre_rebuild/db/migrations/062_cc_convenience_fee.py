"""Migration 062: credit-card convenience fee columns.

Adds an opt-in convenience fee (default 3%) to quotes, sales_orders,
and invoices. When ``cc_fee_enabled`` is 1, totals include
``round((subtotal+core+tax+ship+fuel) * cc_fee_rate, 2)`` as an extra
line. Default is OFF on all existing rows.
"""
from __future__ import annotations


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate(conn) -> None:
    for table in ("quotes", "sales_orders", "invoices"):
        _ensure_column(conn, table, "cc_fee_enabled", "INTEGER DEFAULT 0")
        _ensure_column(conn, table, "cc_fee_rate", "REAL DEFAULT 0.03")
        _ensure_column(conn, table, "cc_fee_amount", "REAL DEFAULT 0")
    conn.commit()
