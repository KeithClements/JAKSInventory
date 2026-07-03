"""Migration 049: quote status change log.

Tracks status transitions (especially reopen-from-lost) for auditing
and future win-rate analytics.
"""


def migrate(conn):
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    if "quote_status_log" not in tables:
        conn.execute("""
            CREATE TABLE quote_status_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id INTEGER NOT NULL,
                old_status TEXT,
                new_status TEXT,
                reason TEXT DEFAULT '',
                changed_by TEXT DEFAULT '',
                changed_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quote_status_log_quote "
            "ON quote_status_log(quote_id)"
        )
    conn.commit()
