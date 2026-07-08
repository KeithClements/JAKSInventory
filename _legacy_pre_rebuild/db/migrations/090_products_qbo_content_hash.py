"""Migration 090: add ``qbo_content_hash`` to ``products``.

Stores a short hash of the QBO-relevant fields (name, price, cost,
description, qty, type, account mapping inputs) so the sync layer can
skip products whose payload hasn't changed since the last successful
sync. Massively reduces QBO API call volume on subsequent batch syncs.
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
            logger.warning("090: ADD COLUMN %s.%s failed: %s", table, column, exc)


def migrate(conn) -> None:
    _add_column(conn, "products", "qbo_content_hash", "qbo_content_hash TEXT")
    conn.commit()
    logger.info("Migration 090 complete: products.qbo_content_hash")
