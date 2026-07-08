"""Migration 099: Phase C QBO sync columns + bill_payments table.

Adds support for three new bidirectional transaction types:

    invoices                  (Sales Receipts share the table; discriminator
                               on ``invoice_kind``)
      + invoice_kind                TEXT    DEFAULT 'invoice'
      + qbo_sales_receipt_id        TEXT
      + qbo_sales_receipt_pulled_at TIMESTAMP
      + deposit_to_account          TEXT    -- QBO account name/id (cash sales)

    customer_credits          (Refund Receipts coexist with credit memos)
      + qbo_refund_receipt_id        TEXT
      + qbo_refund_pulled_at         TIMESTAMP

    bill_payments             (new table — push/pull of QBO BillPayment)
      + standard ledger columns + qbo_bill_payment_id idempotency key

Safe to re-run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_ADDS = {
    "invoices": [
        ("invoice_kind", "TEXT DEFAULT 'invoice'"),
        ("qbo_sales_receipt_id", "TEXT"),
        ("qbo_sales_receipt_pulled_at", "TIMESTAMP"),
        ("deposit_to_account", "TEXT"),
    ],
    "customer_credits": [
        ("qbo_refund_receipt_id", "TEXT"),
        ("qbo_refund_pulled_at", "TIMESTAMP"),
    ],
}


_BILL_PAYMENTS_DDL = """
CREATE TABLE IF NOT EXISTS bill_payments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id           INTEGER REFERENCES vendors(id),
    purchase_order_id   INTEGER REFERENCES purchase_orders(id),
    payment_date        DATE,
    amount              REAL NOT NULL DEFAULT 0,
    payment_method      TEXT,
    reference           TEXT,
    notes               TEXT,
    pay_type            TEXT,
    bank_account        TEXT,
    status              TEXT NOT NULL DEFAULT 'posted',
    qbo_bill_payment_id TEXT,
    qbo_last_synced_at  TIMESTAMP,
    qbo_pulled_at       TIMESTAMP,
    created_by          TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_BILL_PAYMENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_bill_payments_vendor "
    "ON bill_payments(vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_bill_payments_po "
    "ON bill_payments(purchase_order_id)",
    "CREATE INDEX IF NOT EXISTS ix_bill_payments_qbo_id "
    "ON bill_payments(qbo_bill_payment_id)",
    "CREATE INDEX IF NOT EXISTS ix_bill_payments_status "
    "ON bill_payments(status)",
]


def _existing_cols(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    out: set[str] = set()
    for r in rows:
        name = r[1] if not hasattr(r, "keys") else r["name"]
        if name:
            out.add(name)
    return out


def migrate(conn) -> None:
    # 1. Additive column adds.
    for table, adds in _ADDS.items():
        cols = _existing_cols(conn, table)
        if not cols:
            logger.info("099: table %s missing, skipping", table)
            continue
        for name, ddl in adds:
            if name in cols:
                continue
            try:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                )
                logger.info("099: added %s.%s", table, name)
            except Exception as exc:
                logger.warning(
                    "099: add %s.%s failed: %s", table, name, exc
                )

    # 2. bill_payments table.
    try:
        conn.execute(_BILL_PAYMENTS_DDL)
        for idx in _BILL_PAYMENTS_INDEXES:
            conn.execute(idx)
        logger.info("099: bill_payments table + indexes ensured")
    except Exception as exc:
        logger.warning("099: bill_payments create failed: %s", exc)

    try:
        conn.commit()
    except Exception:
        pass
    logger.info("Migration 099 complete: QBO Phase C columns + bill_payments")
