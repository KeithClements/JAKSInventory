"""Migration 109: Core Return Receipt — accountability fields.

Adds fields needed by the Core Return Receipt enhancements so we can
clearly tell, on any reprint or audit, when a core was physically
received, who received it, and why it was voided (if applicable).

Schema additions
----------------
core_returns
    received_by TEXT
        Username / employee name / initials that processed the
        physical core return. Captured on the Receive Core dialog.
    void_reason TEXT
        Free-text reason captured when a core is voided. Separate from
        ``notes`` so the void reason is not overwritten by later edits.
"""
from __future__ import annotations


def _existing_cols(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_col(conn, table: str, name: str, decl: str) -> None:
    if name in _existing_cols(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    except Exception:
        pass


def migrate(conn) -> None:
    _add_col(conn, "core_returns", "received_by", "TEXT")
    _add_col(conn, "core_returns", "void_reason", "TEXT")
    conn.commit()
