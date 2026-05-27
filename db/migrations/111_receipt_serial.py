"""
Migration 111: Add serial_number column to purchase_receipt_lines.
"""


def migrate(conn):
    # Idempotent — add serial_number if it doesn't exist yet
    cols = {r[1] for r in conn.execute("PRAGMA table_info(purchase_receipt_lines)").fetchall()}
    if "serial_number" not in cols:
        conn.execute("ALTER TABLE purchase_receipt_lines ADD COLUMN serial_number TEXT")
    conn.commit()


# Aliases so the migration runner can find it regardless of naming convention
upgrade = migrate


def downgrade(conn):
    pass
