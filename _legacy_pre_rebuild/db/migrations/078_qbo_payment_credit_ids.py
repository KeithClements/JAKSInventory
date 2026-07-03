"""Migration 078: Per-row QBO Payment / CreditMemo IDs (D4).

D3 wired live-API push for invoices and bills. D4 extends that to
customer payments and credit memos via new
``QBOIntegrationService.create_payment`` and ``create_credit_memo``
methods.

We need a place to stamp the QBO-issued ID after a successful push so
re-pushes are correctly recognised as already-synced:

* ``invoices.qbo_payment_id``     — the ``Payment`` Id returned when
  the invoice's ``amount_paid`` is pushed as a ReceivePayment.
  (JAKS doesn't have a separate ``invoice_payments`` table; the paid
  amount and method live on the invoice row, so the pointer does too.)
* ``customer_credits.qbo_credit_id`` — the ``CreditMemo`` Id returned
  when a credit is pushed.

Both columns are nullable TEXT and have no backfill.
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
    _add_column(conn, "invoices", "qbo_payment_id", "qbo_payment_id TEXT")
    _add_column(conn, "customer_credits", "qbo_credit_id", "qbo_credit_id TEXT")
    conn.commit()
