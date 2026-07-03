"""Migration 031: Add optional parts and per-line shipping to quote_lines."""

def migrate(conn):
    """Add is_optional and line_shipping columns to quote_lines."""
    for col, typedef in [
        ("is_optional", "INTEGER DEFAULT 0"),
        ("line_shipping", "REAL DEFAULT 0"),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE quote_lines ADD COLUMN {col} {typedef}"
            )
        except Exception:
            pass  # Column already exists

    # Add shipping column to quotes table for total shipping
    try:
        conn.execute(
            "ALTER TABLE quotes ADD COLUMN shipping REAL DEFAULT 0"
        )
    except Exception:
        pass

    conn.commit()
