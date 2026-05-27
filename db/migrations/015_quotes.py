"""Migration 015: Create quotes and quote_lines tables.

Adds the quoting system for the Quote → Sales Order → Invoice workflow.
Quotes support multiple line items, customer association, status tracking,
and conversion to sales orders or invoices.
"""


def migrate(conn):
    """Apply migration."""
    # Check if quotes table already exists
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]

    if "quotes" not in tables:
        conn.execute("""
            CREATE TABLE quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_number TEXT UNIQUE NOT NULL,
                customer_id INTEGER,
                status TEXT DEFAULT 'draft',
                quote_date TEXT,
                expiration_date TEXT,
                terms TEXT,
                notes TEXT,
                subtotal REAL DEFAULT 0,
                core_total REAL DEFAULT 0,
                tax REAL DEFAULT 0,
                total REAL DEFAULT 0,
                converted_so_id INTEGER,
                converted_invoice_id INTEGER,
                created_by TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
        """)

    if "quote_lines" not in tables:
        conn.execute("""
            CREATE TABLE quote_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id INTEGER NOT NULL,
                product_id INTEGER,
                description TEXT,
                supplier TEXT,
                qty INTEGER DEFAULT 1,
                unit_price REAL DEFAULT 0,
                unit_cost REAL DEFAULT 0,
                core_charge REAL DEFAULT 0,
                line_total REAL DEFAULT 0,
                margin_pct REAL DEFAULT 0,
                is_selected INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                notes TEXT,
                FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        """)

    conn.commit()
