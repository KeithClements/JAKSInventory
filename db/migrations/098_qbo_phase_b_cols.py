"""Migration 098: Phase B QBO sync columns.

Adds tracking columns for:

    purchase_orders
      + qbo_po_pulled_at   TIMESTAMP   -- last QBO PurchaseOrder pull

    core_returns
      + qbo_credit_id      TEXT        -- link to QBO CreditMemo that
                                       --  cleared this core deposit
      + qbo_last_synced_at TIMESTAMP

    quotes
      + qbo_estimate_pulled_at TIMESTAMP

Safe to re-run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_ADDS = {
    "purchase_orders": [
        ("qbo_po_pulled_at", "TIMESTAMP"),
    ],
    "core_returns": [
        ("qbo_credit_id", "TEXT"),
        ("qbo_last_synced_at", "TIMESTAMP"),
    ],
    "quotes": [
        ("qbo_estimate_pulled_at", "TIMESTAMP"),
    ],
}


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
    for table, adds in _ADDS.items():
        cols = _existing_cols(conn, table)
        if not cols:
            logger.info("098: table %s missing, skipping", table)
            continue
        for name, ddl in adds:
            if name in cols:
                continue
            try:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                )
                logger.info("098: added %s.%s", table, name)
            except Exception as exc:
                logger.warning("098: add %s.%s failed: %s", table, name, exc)
    try:
        conn.commit()
    except Exception:
        pass
    logger.info("Migration 098 complete: QBO Phase B columns")
