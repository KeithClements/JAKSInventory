"""Migration 019: Add scraper tables for cross-reference and competitor pricing.

Creates three tables:
  - scrape_runs: Log of every scraper execution
  - scraped_cross_references: Staged cross-reference suggestions (review-first)
  - competitor_prices: Staged competitor pricing data (review-first)
"""


def migrate(conn):
    """Apply migration."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]

    if "scrape_runs" not in tables:
        conn.execute("""
            CREATE TABLE scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                total_searched INTEGER DEFAULT 0,
                total_found INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                notes TEXT
            )
        """)

    if "scraped_cross_references" not in tables:
        conn.execute("""
            CREATE TABLE scraped_cross_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER REFERENCES products(id),
                source_part_number TEXT NOT NULL,
                found_alternate_number TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence TEXT DEFAULT 'medium',
                match_type TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_id INTEGER REFERENCES scrape_runs(id),
                status TEXT NOT NULL DEFAULT 'pending',
                accepted_at TIMESTAMP,
                UNIQUE(source_part_number, found_alternate_number, source)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraped_xref_status "
            "ON scraped_cross_references(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraped_xref_product "
            "ON scraped_cross_references(product_id)"
        )

    if "competitor_prices" not in tables:
        conn.execute("""
            CREATE TABLE competitor_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER REFERENCES products(id),
                sku_searched TEXT NOT NULL,
                competitor_name TEXT NOT NULL,
                competitor_part_number TEXT,
                competitor_price REAL,
                availability TEXT,
                source_url TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_id INTEGER REFERENCES scrape_runs(id),
                status TEXT NOT NULL DEFAULT 'pending',
                accepted_at TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_price_status "
            "ON competitor_prices(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_price_product "
            "ON competitor_prices(product_id)"
        )
