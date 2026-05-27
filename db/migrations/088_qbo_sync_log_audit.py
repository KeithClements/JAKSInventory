"""Migration 088: extend ``qbo_sync_log`` for full request/response audit.

Existing columns (from migration 051) stay: ``entity_type``,
``local_ref``, ``idempotency_key``, ``qbo_id``, ``status``,
``attempts``, ``last_error``, ``created_at``, ``updated_at``.

Adds richer audit-trail columns so the new QBO Audit Log UI can show
exactly what was sent, what came back, what QBO stored on read-back,
and let the user mark issues resolved.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
    except Exception as exc:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {ddl}")
        except Exception:
            logger.warning("088: ADD COLUMN %s.%s failed: %s", table, column, exc)


def _table_exists(conn, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return bool(row)
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name=%s",
                (table,),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def migrate(conn) -> None:
    if not _table_exists(conn, "qbo_sync_log"):
        # Migration 051 normally creates it; create it here too so the
        # audit layer never crashes when 051 hasn't run.
        conn.execute(
            """
            CREATE TABLE qbo_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT,
                local_ref TEXT,
                idempotency_key TEXT,
                qbo_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )

    cols: list[tuple[str, str]] = [
        ("source_type",       "source_type TEXT"),
        ("source_id",         "source_id INTEGER"),
        ("qbo_object_type",   "qbo_object_type TEXT"),
        ("request_method",    "request_method TEXT"),
        ("request_url",       "request_url TEXT"),
        ("request_json",      "request_json TEXT"),
        ("response_status",   "response_status INTEGER"),
        ("response_json",     "response_json TEXT"),
        ("error_message",     "error_message TEXT"),
        ("environment",       "environment TEXT"),
        ("readback_json",     "readback_json TEXT"),
        ("readback_at",       "readback_at TEXT"),
        ("resolved_at",       "resolved_at TEXT"),
    ]
    for col, ddl in cols:
        _add_column(conn, "qbo_sync_log", col, ddl)

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qbo_sync_log_source "
            "ON qbo_sync_log(source_type, source_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qbo_sync_log_created "
            "ON qbo_sync_log(created_at)"
        )
    except Exception as exc:
        logger.warning("088: index create failed: %s", exc)

    conn.commit()
    logger.info("Migration 088 complete: qbo_sync_log audit columns")
