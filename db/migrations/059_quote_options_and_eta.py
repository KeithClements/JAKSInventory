"""Migration 059: A/B/C option groups and per-line ETA tracking.

Adds:
- quote_lines.option_group TEXT NULL          (e.g. 'A','B','C' or custom; NULL=required)
- quote_lines.eta_date DATE NULL              (computed availability date for the line)
- quotes.selected_option_group TEXT NULL      (which group the customer chose)
- sales_order_lines.eta_date DATE NULL        (mirrored on the SO so emails show latest ETA)
"""


def migrate(conn):
    statements = [
        ("quote_lines", "option_group", "TEXT"),
        ("quote_lines", "eta_date", "DATE"),
        ("quotes", "selected_option_group", "TEXT"),
        ("sales_order_lines", "eta_date", "DATE"),
    ]
    for table, col, typedef in statements:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # already exists or table missing
    conn.commit()
