"""Migration 041 – Add private_label flag to products."""
from __future__ import annotations


def migrate(conn) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "private_label" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN private_label INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
