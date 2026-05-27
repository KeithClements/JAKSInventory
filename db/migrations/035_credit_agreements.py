"""Migration 035: Credit agreements table."""


def migrate(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credit_agreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            credit_limit REAL DEFAULT 0,
            apr REAL DEFAULT 0,
            net_days INTEGER DEFAULT 30,
            issue_date TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            guarantor_name TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
