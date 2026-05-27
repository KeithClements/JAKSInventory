"""Migration 024: Add quote_comments table and warranty columns.

- quote_comments: timestamped comment thread per quote
- products.is_warrantable: flag for warranty-eligible products
- products.manufacturer_warranty_months: OEM warranty duration
- quote_lines.warranty_years: additional warranty sold (0-3)
- quote_lines.warranty_price: calculated warranty price for this line
"""


def migrate(conn):
    """Apply migration."""
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]

    if "quote_comments" not in tables:
        conn.execute("""
            CREATE TABLE quote_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id INTEGER NOT NULL,
                author TEXT DEFAULT 'User',
                comment TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qc_quote ON quote_comments(quote_id)"
        )

    # Products: warranty eligibility
    prod_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]
    if "is_warrantable" not in prod_cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN is_warrantable INTEGER DEFAULT 0"
        )
    if "manufacturer_warranty_months" not in prod_cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN manufacturer_warranty_months INTEGER DEFAULT 0"
        )

    # Quote lines: warranty add-on
    ql_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(quote_lines)").fetchall()
    ]
    if "warranty_years" not in ql_cols:
        conn.execute(
            "ALTER TABLE quote_lines ADD COLUMN warranty_years INTEGER DEFAULT 0"
        )
    if "warranty_price" not in ql_cols:
        conn.execute(
            "ALTER TABLE quote_lines ADD COLUMN warranty_price REAL DEFAULT 0"
        )

    conn.commit()
