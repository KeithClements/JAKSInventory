"""Migration 022: Add esn and vin columns to quotes table."""


def migrate(conn):
    """Add esn and vin columns to quotes for equipment tracking."""
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(quotes)").fetchall()
    ]

    if "esn" not in cols:
        conn.execute("ALTER TABLE quotes ADD COLUMN esn TEXT DEFAULT ''")

    if "vin" not in cols:
        conn.execute("ALTER TABLE quotes ADD COLUMN vin TEXT DEFAULT ''")

    conn.commit()
