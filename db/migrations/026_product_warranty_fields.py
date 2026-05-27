"""Add supplier warranty and JAKS additional warranty fields to products."""

from __future__ import annotations


def migrate(conn) -> None:
    cols = [
        "supplier_warranty_enabled INTEGER DEFAULT 0",
        "supplier_warranty_value INTEGER DEFAULT 0",
        "supplier_warranty_unit TEXT DEFAULT 'days'",
        "jaks_warranty_enabled INTEGER DEFAULT 0",
        "jaks_warranty_value INTEGER DEFAULT 0",
        "jaks_warranty_unit TEXT DEFAULT 'days'",
        "jaks_warranty_charge REAL DEFAULT 0",
    ]
    for col in cols:
        try:
            conn.execute(f"ALTER TABLE products ADD COLUMN {col}")
        except Exception:
            pass
    conn.commit()
