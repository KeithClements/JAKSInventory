"""Migration 034: Customer employees table."""


def migrate(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            title TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
