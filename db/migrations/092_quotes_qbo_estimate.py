"""Migration 092: add ``qbo_estimate_id`` / ``qbo_estimate_synced_at`` to ``quotes``.

Adds a direct crosswalk from local quote rows to QBO Estimate Id, so the
Phase 5 Estimate sync can stamp the QBO id after a successful push and
detect prior pushes on re-export (idempotency).

Idempotent. Safe on SQLite and PostgreSQL.
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
            logger.warning("092: ADD COLUMN %s.%s failed: %s", table, column, exc)


def migrate(conn) -> None:
    _add_column(conn, "quotes", "qbo_estimate_id",        "qbo_estimate_id TEXT")
    _add_column(conn, "quotes", "qbo_estimate_synced_at", "qbo_estimate_synced_at TEXT")

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quotes_qbo_estimate_id "
            "ON quotes(qbo_estimate_id)"
        )
    except Exception as exc:
        logger.warning("092: index create failed: %s", exc)

    conn.commit()
