"""Migration 042 – Add shipping info, vendor availability, vendor alternates, cost date columns."""
from __future__ import annotations


def migrate(conn) -> None:
    stmts = [
        # Shipping / weight / dimensions
        "ALTER TABLE products ADD COLUMN weight REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN weight_unit TEXT DEFAULT 'LBS'",
        "ALTER TABLE products ADD COLUMN length REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN width REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN height REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN dimension_unit TEXT DEFAULT 'IN'",
        # Vendor stock / availability (e.g. "ATL: 5, DFW: 3, LAX: Out")
        "ALTER TABLE products ADD COLUMN vendor_availability TEXT DEFAULT ''",
        # Vendor alternate part numbers (comma-separated)
        "ALTER TABLE products ADD COLUMN vendor_alternates TEXT DEFAULT ''",
        # Cost date tracking
        "ALTER TABLE products ADD COLUMN cost_updated_at TIMESTAMP",
        "ALTER TABLE products ADD COLUMN pricing_updated_at TIMESTAMP",
    ]
    for sql in stmts:
        try:
            conn.execute(sql)
        except Exception:
            pass  # Column may already exist
    conn.commit()
