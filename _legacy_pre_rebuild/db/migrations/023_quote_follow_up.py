"""Add follow-up tracking columns to quotes table."""


def _cols(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate(conn):
    existing = _cols(conn, "quotes")
    if "follow_up_date" not in existing:
        conn.execute("ALTER TABLE quotes ADD COLUMN follow_up_date TEXT")
    if "next_action" not in existing:
        conn.execute("ALTER TABLE quotes ADD COLUMN next_action TEXT DEFAULT ''")
    if "priority" not in existing:
        conn.execute("ALTER TABLE quotes ADD COLUMN priority TEXT DEFAULT 'normal'")
    conn.commit()
