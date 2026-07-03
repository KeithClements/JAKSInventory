"""Migration 094: QBO inventory adjustment crosswalk + idempotency table.

Phase 9 — Inventory idempotency.

Local inventory adjustments may be retried (network, throttle, etc.).
This crosswalk records the QBO ``InventoryAdjustment`` id keyed by
the local ``(item_id, qty_delta, txn_date)`` triple so re-running the
same adjustment never double-counts in QBO.

Also stores QBO-originated adjustments coming back through the
webhook handler so we can reconcile against ``inventory_transactions``
(see ``qbo.webhook_worker._handle_inventory_adjustment``).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def migrate(conn) -> None:
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qbo_inventory_adjustments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sku             TEXT,
                qbo_item_id     TEXT,
                qbo_adj_id      TEXT,
                qty_diff        REAL    NOT NULL DEFAULT 0,
                txn_date        TEXT,
                idempotency_key TEXT UNIQUE,
                source          TEXT    NOT NULL DEFAULT 'local',
                notes           TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for ix in (
            "CREATE INDEX IF NOT EXISTS idx_qbo_inv_adj_sku "
            "ON qbo_inventory_adjustments(sku)",
            "CREATE INDEX IF NOT EXISTS idx_qbo_inv_adj_qbo_id "
            "ON qbo_inventory_adjustments(qbo_adj_id)",
            "CREATE INDEX IF NOT EXISTS idx_qbo_inv_adj_idem "
            "ON qbo_inventory_adjustments(idempotency_key)",
        ):
            try:
                conn.execute(ix)
            except Exception as exc:
                logger.warning("094: index create failed: %s", exc)
        conn.commit()
    except Exception as exc:
        logger.warning("094: migrate failed: %s", exc)
