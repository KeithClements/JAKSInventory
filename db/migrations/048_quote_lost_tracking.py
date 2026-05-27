"""Add lost reason and notes tracking to quotes table."""


def migrate(conn):
    # lost_reason: structured category for why the quote was lost
    # lost_notes: free-text detail the user can add
    existing = {row[1] for row in conn.execute("PRAGMA table_info(quotes)").fetchall()}
    if "lost_reason" not in existing:
        conn.execute("ALTER TABLE quotes ADD COLUMN lost_reason TEXT DEFAULT ''")
    if "lost_notes" not in existing:
        conn.execute("ALTER TABLE quotes ADD COLUMN lost_notes TEXT DEFAULT ''")
    conn.commit()
