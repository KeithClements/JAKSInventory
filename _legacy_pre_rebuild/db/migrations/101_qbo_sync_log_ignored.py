"""Migration 101: extend ``qbo_sync_log`` with ``ignored_at`` + ``sync_action``.

Adds two columns to support the Sync Failure Center:

* ``ignored_at``  — when set, the row is hidden from active failure
  lists (operator clicked “Mark Ignored”). Separate from
  ``resolved_at`` which means “re-pushed successfully”.
* ``sync_action`` — short verb (``create`` / ``update`` / ``delete`` /
  ``import`` / ``push``) so the UI can show what was being attempted.

Both columns are nullable; existing rows keep working unchanged.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _has_column(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            name = r[1] if isinstance(r, tuple) else r["name"]
            if name == column:
                return True
        return False
    except Exception:
        # Postgres fallback
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=%s AND column_name=%s",
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
        logger.warning("101: ADD COLUMN %s.%s failed: %s", table, column, exc)


def migrate(conn) -> None:
    cols = [
        ("ignored_at",  "ignored_at TEXT"),
        ("sync_action", "sync_action TEXT"),
    ]
    for name, ddl in cols:
        _add_column(conn, "qbo_sync_log", name, ddl)
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qbo_sync_log_ignored "
            "ON qbo_sync_log(ignored_at)"
        )
    except Exception as exc:
        logger.warning("101: index create failed: %s", exc)
    conn.commit()
    logger.info("Migration 101 complete: qbo_sync_log ignored_at + sync_action")
