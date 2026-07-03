"""Migration 081: per-payment history table.

T1.1 (industry-comparison analysis). Today ``invoices.amount_paid`` is
the only surviving record of payment activity — there's no breakdown of
which tender paid what, who recorded it, when, or any chance of voiding
a single payment. Every comparable HD parts program (Karmak, EverLogic,
Tekion) keeps a payment-history child table.

This migration adds that table additively. The existing aggregate
fields on ``invoices`` keep working unchanged; new rows are written
alongside on each ``record_payment`` call.

Columns:
  id                    PK
  invoice_id            FK invoices.id (CASCADE)
  amount                payment amount (positive; voids handled by status)
  payment_method        cash | check | credit_card | ach | wire | store_credit | other
  reference             check #, auth code, last-4, etc.
  notes                 free-form
  status                'posted' | 'voided'
  overpayment_credit_id FK customer_credits.id (when this payment posted excess)
  recorded_by           user
  recorded_at           timestamp
  voided_at             timestamp (NULL when posted)
  voided_by             user (NULL when posted)
  void_reason           text
"""
from __future__ import annotations


def _table_exists(conn, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return bool(row)
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = %s",
                (name,),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def migrate(conn) -> None:
    if _table_exists(conn, "invoice_payments"):
        return
    try:
        conn.execute(
            """
            CREATE TABLE invoice_payments (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id            INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                amount                REAL NOT NULL DEFAULT 0,
                payment_method        TEXT,
                reference             TEXT,
                notes                 TEXT,
                status                TEXT NOT NULL DEFAULT 'posted',
                overpayment_credit_id INTEGER REFERENCES customer_credits(id),
                recorded_by           TEXT,
                recorded_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                voided_at             TIMESTAMP,
                voided_by             TEXT,
                void_reason           TEXT
            )
            """
        )
    except Exception:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invoice_payments (
                id                    SERIAL PRIMARY KEY,
                invoice_id            INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                amount                NUMERIC(10,2) NOT NULL DEFAULT 0,
                payment_method        TEXT,
                reference             TEXT,
                notes                 TEXT,
                status                TEXT NOT NULL DEFAULT 'posted',
                overpayment_credit_id INTEGER REFERENCES customer_credits(id),
                recorded_by           TEXT,
                recorded_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                voided_at             TIMESTAMP,
                voided_by             TEXT,
                void_reason           TEXT
            )
            """
        )

    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_invoice_payments_invoice ON invoice_payments(invoice_id)",
        "CREATE INDEX IF NOT EXISTS ix_invoice_payments_status ON invoice_payments(status)",
        "CREATE INDEX IF NOT EXISTS ix_invoice_payments_recorded ON invoice_payments(recorded_at)",
        "CREATE INDEX IF NOT EXISTS ix_invoice_payments_method ON invoice_payments(payment_method)",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass

    conn.commit()
