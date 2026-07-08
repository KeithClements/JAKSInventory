"""Migration 012: Add invoice description fields to products.

Adds engine_manufacturer, engine_model (uses existing 'engine' column),
brief_description, oem_part_number, xref_part_number, and invoice_description
columns to support invoice line formatting.
"""


def migrate(conn):
    """Apply migration."""
    # Check existing columns
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]

    new_cols = {
        "oem_manufacturer": "TEXT",
        "brief_description": "TEXT",
        "oem_part_number": "TEXT",
        "xref_part_number": "TEXT",
        "invoice_description": "TEXT",
    }

    for col_name, col_type in new_cols.items():
        if col_name not in cols:
            conn.execute(
                f"ALTER TABLE products ADD COLUMN {col_name} {col_type}"
            )

    conn.commit()
