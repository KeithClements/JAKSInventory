"""Migration 087: add ``invoices.qbo_journal_entry_id``.

Stamped by :mod:`qbo.journal_entries` when a finalized invoice is pushed
to QuickBooks Online as a JournalEntry. Lets the integration screen
skip already-pushed rows on re-run.

Nullable TEXT, no backfill.
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
            logger.warning("087: ADD COLUMN %s.%s failed: %s", table, column, exc)


def migrate(conn) -> None:
    _add_column(conn, "invoices",
                "qbo_journal_entry_id",
                "qbo_journal_entry_id TEXT")
    conn.commit()
    logger.info("Migration 087 complete: invoices.qbo_journal_entry_id")
