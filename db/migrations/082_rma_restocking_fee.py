"""Migration 082: restocking fee on RMA returns.

T1.2 (industry-comparison Tier 1). Adds three columns to ``returns``:
  * ``restocking_fee``     — dollar amount withheld from the refund
  * ``restocking_pct``     — percentage (informational; the fee column
                             is the source of truth for the credit math)
  * ``net_refund``         — derived: refund_amount - restocking_fee

Storing both gross (``refund_amount``) and net (``net_refund``) keeps
the audit trail intact (the customer was charged $X, we kept $Y, we
refunded $Z) without breaking any existing accounting.
"""
from __future__ import annotations


def _column_exists(conn, table: str, column: str) -> bool:
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
    if _column_exists(conn, table, column):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except Exception:
        # column already exists or vendor-specific failure — keep going
        pass


def migrate(conn) -> None:
    _add_column(conn, "returns", "restocking_fee", "REAL DEFAULT 0")
    _add_column(conn, "returns", "restocking_pct", "REAL DEFAULT 0")
    _add_column(conn, "returns", "net_refund",     "REAL DEFAULT 0")
    conn.commit()
