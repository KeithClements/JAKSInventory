"""Migration 037: Create pre_inventory staging table and add status to products."""


def migrate(conn):
    # ── pre_inventory table ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pre_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT DEFAULT 'manual',
            esn TEXT,
            manufacturer TEXT,
            oem_part_number TEXT,
            description TEXT,
            kit_number TEXT,
            kit_description TEXT,
            image_url TEXT,
            cost REAL DEFAULT 0,
            selling_price REAL DEFAULT 0,
            core_charge REAL DEFAULT 0,
            pai_part_number TEXT,
            sampa_part_number TEXT,
            cross_refs TEXT,
            notes TEXT,
            grok_recommendation TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            reviewed_by TEXT,
            product_id INTEGER,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)

    # ── Add inventory_status to products (active / pre_inventory) ──
    prod_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]
    if "inventory_status" not in prod_cols:
        try:
            conn.execute(
                "ALTER TABLE products ADD COLUMN inventory_status TEXT DEFAULT 'active'"
            )
        except Exception:
            pass

    conn.commit()
