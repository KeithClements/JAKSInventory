"""Migration 020: Add quote_id to sales_orders for Quote → SO conversion."""


def migrate(conn):
    """Add quote_id column to sales_orders table."""
    # Check existing columns
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sales_orders)").fetchall()]

    if "quote_id" not in cols:
        conn.execute("ALTER TABLE sales_orders ADD COLUMN quote_id INTEGER REFERENCES quotes(id)")
        conn.commit()

    # Add index for quote_id lookups
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_so_quote ON sales_orders(quote_id)"
        )
        conn.commit()
    except Exception:
        pass
