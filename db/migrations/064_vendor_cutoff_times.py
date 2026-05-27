"""Migration 064: vendor cutoff times.

Adds editable cutoff time fields to ``vendors`` so PO expected-date logic
and operational messaging can warn when an order is past the vendor's
daily order cutoff.

Columns added (idempotent):
    cutoff_time_local  TEXT      -- e.g. "14:00" (24h local cutoff)
    cutoff_timezone    TEXT      -- IANA tz, default America/Denver
    cutoff_notes       TEXT      -- free-form vendor cutoff notes
"""
from __future__ import annotations


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate(conn) -> None:
    _ensure_column(conn, "vendors", "cutoff_time_local", "TEXT")
    _ensure_column(
        conn, "vendors", "cutoff_timezone",
        "TEXT DEFAULT 'America/Denver'",
    )
    _ensure_column(conn, "vendors", "cutoff_notes", "TEXT")
    conn.commit()
