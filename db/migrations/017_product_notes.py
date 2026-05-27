"""Migration 017: Add notes_public and notes_internal to products.

Customer-visible notes (invoices/quotes) and internal-only notes.
"""


def migrate(conn):
    """Apply migration."""
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]

    if "notes_public" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN notes_public TEXT"
        )

    if "notes_internal" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN notes_internal TEXT"
        )

    conn.commit()
