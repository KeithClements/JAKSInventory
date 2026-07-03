"""Add messages table for two-way customer text conversations."""

from __future__ import annotations


def migrate(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            phone TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'outbound',
            body TEXT NOT NULL,
            twilio_sid TEXT,
            status TEXT DEFAULT 'queued',
            read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_customer
        ON messages(customer_id, created_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_phone
        ON messages(phone, created_at)
    """)
    conn.commit()
