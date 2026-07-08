"""Migration 103: extend ``qbo_sync_queue`` and ``qbo_sync_events``.

Adds the cross-link columns required by the Sync Event Log + Pending
Queue refresh:

* ``qbo_sync_queue.local_ref`` — human-friendly identifier surfaced to
  the operator (SKU, invoice number, etc.) alongside the numeric
  ``local_record_id``.
* ``qbo_sync_queue.failure_id`` — back-pointer to the matching
  ``qbo_sync_log`` row when the queue row has failed.
* ``qbo_sync_events.queue_id``, ``failure_id``, ``local_ref``,
  ``risk_level`` — every event row can now be traced back to the
  originating queue entry / failure for the Sync Log "Details" view.

All ``ALTER TABLE`` statements are wrapped so re-runs on already-
migrated databases are no-ops.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _existing_cols(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    return {r[1] for r in rows}


def _add_col(conn, table: str, col: str, decl: str) -> None:
    if col in _existing_cols(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    except Exception as exc:
        logger.warning("103: ADD %s.%s failed: %s", table, col, exc)


def migrate(conn) -> None:
    _add_col(conn, "qbo_sync_queue", "local_ref",  "TEXT")
    _add_col(conn, "qbo_sync_queue", "failure_id", "INTEGER")

    _add_col(conn, "qbo_sync_events", "queue_id",   "INTEGER")
    _add_col(conn, "qbo_sync_events", "failure_id", "INTEGER")
    _add_col(conn, "qbo_sync_events", "local_ref",  "TEXT")
    _add_col(conn, "qbo_sync_events", "risk_level", "TEXT")

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qbo_sync_events_queue "
            "ON qbo_sync_events(queue_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qbo_sync_events_failure "
            "ON qbo_sync_events(failure_id)"
        )
    except Exception as exc:
        logger.warning("103: index create failed: %s", exc)

    try:
        conn.commit()
    except Exception:
        pass
    logger.info("Migration 103 complete: queue/events cross-link columns")
