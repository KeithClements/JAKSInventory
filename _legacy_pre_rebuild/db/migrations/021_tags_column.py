"""Migration 021: Add tags column to products for Shopify tags."""


def migrate(conn):
    """Add tags column to products table."""
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]

    if "tags" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN tags TEXT DEFAULT ''")

    conn.commit()
