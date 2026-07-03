"""Migration 032: Add manufacturer and job_number to quotes."""


def migrate(conn):
    for col, typedef in [
        ("manufacturer", "TEXT DEFAULT ''"),
        ("job_number", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE quotes ADD COLUMN {col} {typedef}"
            )
        except Exception:
            pass  # Column already exists

    conn.commit()
