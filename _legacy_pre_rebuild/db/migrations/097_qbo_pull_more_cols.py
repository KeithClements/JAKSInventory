"""Migration 097: QBO pull-side columns for payments, bills, credits.

Adds tracking columns so QBO Payment / Bill / CreditMemo pulls can
upsert without creating duplicates:

    invoice_payments
      + qbo_payment_id      TEXT      -- QBO Payment.Id
      + qbo_last_pulled_at  TIMESTAMP
      + source              TEXT DEFAULT 'local'

    customer_credits
      + qbo_last_pulled_at  TIMESTAMP
      + source              TEXT DEFAULT 'local'
      (qbo_credit_id already exists from migration 078)

    purchase_orders
      + qbo_bill_pulled_at  TIMESTAMP    -- last time we pulled the
                                          -- linked QBO Bill back
      + bill_balance        REAL DEFAULT 0  -- AP balance from QBO
      (qbo_bill_id + qbo_po_id already exist from migration 093)

Safe to re-run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_ADDS = {
    "invoice_payments": [
        ("qbo_payment_id", "TEXT"),
        ("qbo_last_pulled_at", "TIMESTAMP"),
        ("source", "TEXT DEFAULT 'local'"),
    ],
    "customer_credits": [
        ("qbo_last_pulled_at", "TIMESTAMP"),
        ("source", "TEXT DEFAULT 'local'"),
    ],
    "purchase_orders": [
        ("qbo_bill_pulled_at", "TIMESTAMP"),
        ("bill_balance", "REAL DEFAULT 0"),
    ],
}


def _existing_cols(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    out = set()
    for r in rows:
        name = r[1] if not hasattr(r, "keys") else r["name"]
        if name:
            out.add(name)
    return out


def migrate(conn) -> None:
    for table, adds in _ADDS.items():
        cols = _existing_cols(conn, table)
        if not cols:
            logger.info("097: table %s missing, skipping", table)
            continue
        for name, ddl in adds:
            if name in cols:
                continue
            try:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                )
                logger.info("097: added %s.%s", table, name)
            except Exception as exc:
                logger.warning("097: add %s.%s failed: %s", table, name, exc)
    try:
        conn.commit()
    except Exception:
        pass
    logger.info("Migration 097 complete: QBO pull cols for payments/credits/POs")
