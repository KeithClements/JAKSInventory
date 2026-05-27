"""Migration 055: Core charges on Purchase Orders + vendor core return tracking.

Adds to purchase_order_lines:
  - core_charge   REAL DEFAULT 0   — per-unit core deposit charged by vendor
  - core_total    REAL DEFAULT 0   — qty * core_charge (cached)

Adds to purchase_orders:
  - core_subtotal REAL DEFAULT 0   — sum of all line core_total values

Creates vendor_core_returns table — mirrors customer-side core_returns but
tracks cores WE owe back to a vendor (so we can recover the deposit).
"""
from __future__ import annotations


def migrate(conn) -> None:
    # purchase_order_lines columns
    line_cols = {row[1] for row in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
    for col, typedef in (
        ("core_charge", "REAL DEFAULT 0"),
        ("core_total", "REAL DEFAULT 0"),
    ):
        if col not in line_cols:
            try:
                conn.execute(f"ALTER TABLE purchase_order_lines ADD COLUMN {col} {typedef}")
            except Exception:
                pass

    # purchase_orders core subtotal
    po_cols = {row[1] for row in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
    if "core_subtotal" not in po_cols:
        try:
            conn.execute("ALTER TABLE purchase_orders ADD COLUMN core_subtotal REAL DEFAULT 0")
        except Exception:
            pass

    # vendor_core_returns table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_core_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_line_id INTEGER REFERENCES purchase_order_lines(id) ON DELETE SET NULL,
            po_id INTEGER REFERENCES purchase_orders(id) ON DELETE SET NULL,
            vendor_id INTEGER REFERENCES vendors(id),
            product_id INTEGER REFERENCES products(id),
            qty INTEGER NOT NULL DEFAULT 1,
            core_charge REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            -- pending | shipped | credited | written_off | voided
            due_date DATE,
            shipped_date DATE,
            tracking_number TEXT,
            credit_received_date DATE,
            vendor_credit_number TEXT,
            credit_amount REAL DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vcr_po ON vendor_core_returns(po_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vcr_vendor ON vendor_core_returns(vendor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vcr_status ON vendor_core_returns(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vcr_product ON vendor_core_returns(product_id)")

    # Backfill core_total for any existing lines that have core_charge set.
    try:
        conn.execute("""
            UPDATE purchase_order_lines
               SET core_total = COALESCE(quantity_ordered, 0) * COALESCE(core_charge, 0)
             WHERE COALESCE(core_charge, 0) > 0
        """)
    except Exception:
        pass

    conn.commit()
