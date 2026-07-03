"""Migration 053: Add private_label_part_number column to products table."""


def migrate(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "private_label_part_number" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN private_label_part_number TEXT DEFAULT ''"
        )
    conn.commit()
