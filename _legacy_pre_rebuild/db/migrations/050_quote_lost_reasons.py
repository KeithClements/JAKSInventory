"""Migration 050: structured quote_lost_reasons table.

Seeded with the same labels used in the hard-coded _LOST_REASONS list
in jaks_inventory/ui/quotes_screen.py. The quotes.lost_reason column
remains a TEXT to preserve historic values; new code should look up
from this table so admins can edit the list.
"""

_SEED = [
    "Price too high",
    "Lead time too long",
    "Customer went with competitor",
    "Customer went direct to OEM",
    "Part no longer needed",
    "Could not source part",
    "No response from customer",
    "Other",
]


def migrate(conn):
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    if "quote_lost_reasons" not in tables:
        conn.execute("""
            CREATE TABLE quote_lost_reasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT UNIQUE NOT NULL,
                sort_order INTEGER DEFAULT 100,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    # Seed if empty (idempotent)
    count = conn.execute("SELECT COUNT(*) FROM quote_lost_reasons").fetchone()
    count_val = count[0] if not hasattr(count, "keys") else list(count.values())[0]
    if not count_val:
        for i, label in enumerate(_SEED):
            conn.execute(
                "INSERT INTO quote_lost_reasons (label, sort_order, active) "
                "VALUES (%s, %s, 1)",
                (label, (i + 1) * 10),
            )
    conn.commit()
