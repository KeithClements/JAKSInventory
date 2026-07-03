"""Migration 011 — Product enhancements.

Adds:
- cost_updated_at TIMESTAMP on products  (tracks when vendor cost was last changed)
- requires_serial BOOLEAN on products    (flag for serial-tracked products)
"""
from __future__ import annotations


def _safe_add_column(conn, table: str, column: str, col_type: str) -> None:
    """Add a column only if it doesn't already exist (SQLite-safe)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass  # Column already exists


def run(conn) -> None:
    """Run migration 011."""
    _safe_add_column(conn, "products", "cost_updated_at", "TIMESTAMP")
    _safe_add_column(conn, "products", "requires_serial", "BOOLEAN DEFAULT 0")
