"""Add audit review and last audited date fields to products."""

from __future__ import annotations


def migrate(conn) -> None:
    cols = [
        "needs_audit INTEGER DEFAULT 0",
        "last_audited_date TEXT",
    ]
    for col in cols:
        try:
            conn.execute(f"ALTER TABLE products ADD COLUMN {col}")
        except Exception:
            pass
    conn.commit()
