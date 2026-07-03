"""Add quote_sms_log table for Twilio SMS quote delivery and reply tracking."""

from __future__ import annotations


def migrate(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quote_sms_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id INTEGER NOT NULL,
            customer_id INTEGER,
            phone TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'outbound',
            twilio_sid TEXT,
            message_body TEXT,
            status TEXT DEFAULT 'queued',
            reply_body TEXT,
            replied_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)
    conn.commit()
