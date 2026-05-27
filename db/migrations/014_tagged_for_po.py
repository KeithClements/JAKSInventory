"""Migration 014: Add tagged_for_po flag to products.

Adds a boolean flag so users can tag items for purchase order creation
directly from the inventory screen.
"""


def migrate(conn):
    """Apply migration."""
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]

    if "tagged_for_po" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN tagged_for_po BOOLEAN DEFAULT 0"
        )

    conn.commit()
