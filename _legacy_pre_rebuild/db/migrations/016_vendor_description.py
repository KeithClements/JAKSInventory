"""Migration 016: Add vendor_description to products.

Separate vendor-facing description field independent of
brief_description and invoice_description.
"""


def migrate(conn):
    """Apply migration."""
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]

    if "vendor_description" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN vendor_description TEXT"
        )

    conn.commit()
