"""Migration 038 – Add customer_type and on_hold to customers table."""


def migrate(conn):
    cur = conn.execute("PRAGMA table_info(customers)")
    cols = {row[1] for row in cur.fetchall()}

    if "customer_type" not in cols:
        conn.execute(
            "ALTER TABLE customers ADD COLUMN customer_type TEXT DEFAULT ''"
        )

    if "on_hold" not in cols:
        conn.execute(
            "ALTER TABLE customers ADD COLUMN on_hold INTEGER DEFAULT 0"
        )
