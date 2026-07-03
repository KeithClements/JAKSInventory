"""Migration 096: QBO pull-side columns on invoices.

Lets the new "Pull from QBO" path stamp inbound invoices distinct from
locally-created rows:

    invoices
      + qbo_balance          REAL DEFAULT 0      -- open balance at last pull
      + qbo_last_pulled_at   TIMESTAMP           -- last successful import
      + source               TEXT DEFAULT 'local'-- 'local' | 'qbo'

``qbo_invoice_id`` already exists on the base schema (used by push),
so we don't re-add it here. The ``source`` discriminator lets the UI
decide which list to show ("All", "Local only", "QBO only").

Safe to re-run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_INVOICE_ADDS = [
    ("qbo_balance", "REAL DEFAULT 0"),
    ("qbo_last_pulled_at", "TIMESTAMP"),
    ("source", "TEXT DEFAULT 'local'"),
]


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
    try:
        cols = _existing_cols(conn, "invoices")
        for name, ddl in _INVOICE_ADDS:
            if name not in cols:
                try:
                    conn.execute(
                        f"ALTER TABLE invoices ADD COLUMN {name} {ddl}"
                    )
                    logger.info("096: added invoices.%s", name)
                except Exception as exc:
                    logger.warning("096: add invoices.%s failed: %s", name, exc)
        try:
            conn.commit()
        except Exception:
            pass
    except Exception as exc:
        logger.warning("096: migrate failed: %s", exc)
