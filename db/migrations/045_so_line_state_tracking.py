"""Migration 045 – Add fulfilment-state columns to sales_order_lines.

New columns:
    allocated_qty   – reserved from on-hand stock
    on_po_qty       – assigned to a purchase order
    received_qty    – received from PO
    invoiced_qty    – already invoiced

Existing columns retained:
    quantity_ordered, quantity_picked, quantity_backordered

Also adds an `activity_log` table for SO activity timeline.
"""
from __future__ import annotations


def migrate(conn) -> None:
    cols = {c[1] for c in conn.execute("PRAGMA table_info(sales_order_lines)").fetchall()}

    new_cols = {
        "allocated_qty": "INTEGER DEFAULT 0",
        "on_po_qty": "INTEGER DEFAULT 0",
        "received_qty": "INTEGER DEFAULT 0",
        "invoiced_qty": "INTEGER DEFAULT 0",
    }

    for col, typedef in new_cols.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE sales_order_lines ADD COLUMN {col} {typedef}")

    # Back-fill: treat existing quantity_picked as allocated_qty for pre-existing rows
    conn.execute("""
        UPDATE sales_order_lines
        SET allocated_qty = quantity_picked
        WHERE allocated_qty = 0 AND quantity_picked > 0
    """)

    # Activity log for order timeline
    conn.execute("""
        CREATE TABLE IF NOT EXISTS so_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            so_id INTEGER NOT NULL REFERENCES sales_orders(id),
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
