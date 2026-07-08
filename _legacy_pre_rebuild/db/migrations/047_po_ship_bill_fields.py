"""Add ship_to and bill_to fields to purchase_orders."""


def migrate(conn):
    cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
    changed = False

    if "ship_to" not in cols:
        conn.execute("ALTER TABLE purchase_orders ADD COLUMN ship_to TEXT")
        changed = True

    if "bill_to" not in cols:
        conn.execute("ALTER TABLE purchase_orders ADD COLUMN bill_to TEXT")
        changed = True

    if changed:
        conn.commit()
