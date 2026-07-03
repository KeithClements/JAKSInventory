"""Migration 013: Add shipping_cost, core_sell_price, and truck fields to products.

Adds columns needed by the overhauled Edit Product Dialog:
- shipping_cost: estimated per-unit shipping (for landed cost calc)
- core_sell_price: what we charge the customer for the core
- truck_manufacturer, truck_model, truck_system: truck-side invoice description fields
"""


def migrate(conn):
    """Apply migration."""
    cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]

    new_cols = {
        "shipping_cost": "REAL DEFAULT 0",
        "core_sell_price": "REAL DEFAULT 0",
        "truck_manufacturer": "TEXT",
        "truck_model": "TEXT",
        "truck_system": "TEXT",
    }

    for col_name, col_type in new_cols.items():
        if col_name not in cols:
            conn.execute(
                f"ALTER TABLE products ADD COLUMN {col_name} {col_type}"
            )

    conn.commit()
