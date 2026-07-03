"""Migration 073: track refund disbursement on customer credits.

Background
----------
Migration 069 introduced ``customer_credits`` to capture overpayments,
goodwill credits, and rebates that aren't tied to a specific invoice
line. The status enum at the time was ``open / applied / voided``.

We now need to support paying a credit back out to the customer (cut a
check, issue a card refund, ACH, cash from the till). To keep the credit
ledger as the single source of truth — instead of spinning up a parallel
``customer_refunds`` table — we add a new lifecycle status ``refunded``
plus three columns describing the disbursement:

* ``refund_method``    — text: ``check`` / ``cash`` / ``card_refund`` / ``ach``
* ``refund_reference`` — text: check number, ACH trace, processor txn id, …
* ``refund_date``      — timestamp the refund was issued

``status`` itself is plain ``TEXT`` so no constraint update is needed
to start storing the new value.
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


def _add_column(conn, table: str, column: str, ddl_type: str) -> None:
    if _has_column(conn, table, column):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
    except Exception:
        # PG fallback
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}"
        )


def migrate(conn) -> None:
    _add_column(conn, "customer_credits", "refund_method", "TEXT")
    _add_column(conn, "customer_credits", "refund_reference", "TEXT")
    _add_column(conn, "customer_credits", "refund_date", "TIMESTAMP")
    conn.commit()
