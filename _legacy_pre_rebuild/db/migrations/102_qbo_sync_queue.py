"""Migration 102: create ``qbo_sync_queue`` + ``qbo_sync_events`` tables.

The **Pending Queue** is the staging buffer between local edits and
QuickBooks. Every time a product / customer / vendor / invoice / bill /
payment / inventory-qty change needs to round-trip to QBO, a row is
inserted into ``qbo_sync_queue`` with ``status='pending'``. A worker
(or operator from the Pending Queue dialog) drains it.

``qbo_sync_events`` is an append-only audit log — each push attempt
(successful or not) appends one row. The Failure Center reads the
*failed* slice; the Pending Queue uses it to render Sync History.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _exec(conn, sql: str) -> None:
    try:
        conn.execute(sql)
    except Exception as exc:
        logger.warning("102: %s failed: %s", sql.split("(", 1)[0].strip(), exc)


def migrate(conn) -> None:
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS qbo_sync_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type     TEXT NOT NULL,
            local_record_id TEXT,
            qbo_id          TEXT,
            action          TEXT NOT NULL DEFAULT 'update',
            direction       TEXT NOT NULL DEFAULT 'app_to_qbo',
            risk_level      TEXT NOT NULL DEFAULT 'low',
            status          TEXT NOT NULL DEFAULT 'pending',
            message         TEXT,
            payload_json    TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            last_attempt_at TEXT,
            synced_at       TEXT,
            ignored_at      TEXT
        )
    """)
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS qbo_sync_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type     TEXT NOT NULL,
            local_record_id TEXT,
            qbo_id          TEXT,
            action          TEXT,
            direction       TEXT,
            status          TEXT NOT NULL,
            message         TEXT,
            raw_details     TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # De-dupe index so the same local entity can't get a second pending
    # row queued while one is already waiting.
    _exec(conn, """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_qbo_sync_queue_pending
        ON qbo_sync_queue(entity_type, local_record_id, action)
        WHERE status = 'pending'
    """)
    _exec(conn, "CREATE INDEX IF NOT EXISTS idx_qbo_sync_queue_status "
                "ON qbo_sync_queue(status)")
    _exec(conn, "CREATE INDEX IF NOT EXISTS idx_qbo_sync_queue_entity "
                "ON qbo_sync_queue(entity_type, local_record_id)")
    _exec(conn, "CREATE INDEX IF NOT EXISTS idx_qbo_sync_events_entity "
                "ON qbo_sync_events(entity_type, local_record_id)")
    _exec(conn, "CREATE INDEX IF NOT EXISTS idx_qbo_sync_events_created "
                "ON qbo_sync_events(created_at DESC)")
    try:
        conn.commit()
    except Exception:
        pass
    logger.info("Migration 102 complete: qbo_sync_queue + qbo_sync_events")
