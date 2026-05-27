"""Migration 018: Add subcategory to products.

Allows two-level category hierarchy (Category → Subcategory).
"""


def migrate(conn):
    """Apply migration."""
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]

    if "subcategory" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN subcategory TEXT"
        )

    conn.commit()
