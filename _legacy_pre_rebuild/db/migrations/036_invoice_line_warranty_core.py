"""Migration 036: Add warranty + core_charge columns to invoice_lines."""


def migrate(conn):
    il_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(invoice_lines)").fetchall()
    ]
    new_cols = [
        ("warranty_years", "INTEGER DEFAULT 0"),
        ("warranty_price", "REAL DEFAULT 0"),
        ("core_charge", "REAL DEFAULT 0"),
    ]
    for col, typedef in new_cols:
        if col not in il_cols:
            try:
                conn.execute(f"ALTER TABLE invoice_lines ADD COLUMN {col} {typedef}")
            except Exception:
                pass
    conn.commit()
