"""Migration 056: Vendor lead-time/transit columns + PO communication fields.

Adds to vendors:
  - lead_time_days   INTEGER DEFAULT 14   -- already referenced by code, never created
  - avg_transit_days INTEGER DEFAULT 0    -- expected shipping transit time

Adds to purchase_orders:
  - vendor_message TEXT             -- comment/instructions sent TO the vendor on PO
  - drop_ship      BOOLEAN DEFAULT 0
  - no_tags        BOOLEAN DEFAULT 0
"""
from __future__ import annotations


def migrate(conn) -> None:
    vendor_cols = {row[1] for row in conn.execute("PRAGMA table_info(vendors)").fetchall()}
    for col, typedef in (
        ("lead_time_days",   "INTEGER DEFAULT 14"),
        ("avg_transit_days", "INTEGER DEFAULT 0"),
    ):
        if col not in vendor_cols:
            try:
                conn.execute(f"ALTER TABLE vendors ADD COLUMN {col} {typedef}")
            except Exception:
                pass

    po_cols = {row[1] for row in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
    for col, typedef in (
        ("vendor_message", "TEXT"),
        ("drop_ship",      "BOOLEAN DEFAULT 0"),
        ("no_tags",        "BOOLEAN DEFAULT 0"),
    ):
        if col not in po_cols:
            try:
                conn.execute(f"ALTER TABLE purchase_orders ADD COLUMN {col} {typedef}")
            except Exception:
                pass

    conn.commit()
