"""Migration 046 - add purchase_orders.linked_so_id.

Adds a formal purchase-order to sales-order linkage column and back-fills
existing rows from the legacy `notes` pattern: `From SO #<id>`.
"""
from __future__ import annotations

import re


def migrate(conn) -> None:
    cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}

    if "linked_so_id" not in cols:
        conn.execute(
            "ALTER TABLE purchase_orders ADD COLUMN linked_so_id INTEGER REFERENCES sales_orders(id)"
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_orders_linked_so_id ON purchase_orders(linked_so_id)"
    )

    rows = conn.execute(
        "SELECT id, notes FROM purchase_orders WHERE linked_so_id IS NULL AND notes IS NOT NULL"
    ).fetchall()
    for row in rows:
        po_id = row[0] if hasattr(row, "__getitem__") else None
        notes = (row[1] if hasattr(row, "__getitem__") else "") or ""
        match = re.search(r"From SO #(\d+)", str(notes))
        if not match:
            continue
        conn.execute(
            "UPDATE purchase_orders SET linked_so_id = ? WHERE id = ?",
            (int(match.group(1)), po_id),
        )

    conn.commit()