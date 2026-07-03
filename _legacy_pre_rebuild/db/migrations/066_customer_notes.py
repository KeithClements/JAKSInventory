"""Migration 066: Customer-facing notes on sales orders + invoices.

Adds a ``customer_notes`` TEXT column to ``sales_orders`` and ``invoices``.
The existing ``notes`` column is treated as internal/operational; the new
column is what gets rendered onto the printed/emailed PDF for the
customer.
"""
from __future__ import annotations


def _has_column(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for r in rows:
        # PRAGMA returns: cid, name, type, notnull, dflt_value, pk
        name = r[1] if not hasattr(r, "keys") else r["name"]
        if str(name).lower() == column.lower():
            return True
    return False


def migrate(conn) -> None:
    if not _has_column(conn, "sales_orders", "customer_notes"):
        conn.execute("ALTER TABLE sales_orders ADD COLUMN customer_notes TEXT")
    if not _has_column(conn, "invoices", "customer_notes"):
        conn.execute("ALTER TABLE invoices ADD COLUMN customer_notes TEXT")
    conn.commit()
