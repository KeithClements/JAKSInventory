"""Migration 008: Parts vendor feature tables.

Creates tables for:
- price_tiers + customer_pricing: Customer-specific pricing tiers
- vendor_returns + vendor_return_lines: RGA tracking back to vendors
- kits + kit_components: Kit/assembly builder
- shipments: Inbound/outbound delivery tracking with ETA
- supersessions: Part number supersession chains
"""

import logging

logger = logging.getLogger(__name__)


def _table_exists(conn, table_name: str) -> bool:
    try:
        cursor = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            (table_name,))
        row = cursor.fetchone()
        if row:
            return bool(list(row.values())[0] if isinstance(row, dict) else row[0])
    except Exception:
        pass
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,))
        return cursor.fetchone() is not None
    except Exception:
        pass
    return False


def _get_existing_columns(conn, table_name: str) -> set:
    try:
        cursor = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table_name,))
        rows = cursor.fetchall()
        if rows:
            return {(row['column_name'] if isinstance(row, dict) else row[0]) for row in rows}
    except Exception:
        pass
    try:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
        return {(row['name'] if isinstance(row, dict) else row[1]) for row in rows}
    except Exception:
        pass
    return set()


def _safe_add_column(conn, table_name: str, col_name: str, col_def: str):
    existing = _get_existing_columns(conn, table_name)
    if col_name not in existing:
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
            conn.commit()
            logger.info(f"Added column {table_name}.{col_name}")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                pass
            else:
                raise


def _safe_create_table(conn, sql: str, table_name: str):
    if not _table_exists(conn, table_name):
        conn.execute(sql)
        conn.commit()
        logger.info(f"Created table: {table_name}")


def up(conn) -> bool:
    try:
        # ── Price Tiers ──
        _safe_create_table(conn, """
            CREATE TABLE price_tiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                markup_pct REAL DEFAULT 0,
                discount_pct REAL DEFAULT 0,
                priority INTEGER DEFAULT 0,
                is_default INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "price_tiers")

        # ── Customer Pricing ──
        _safe_create_table(conn, """
            CREATE TABLE customer_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                tier_id INTEGER REFERENCES price_tiers(id),
                product_id INTEGER REFERENCES products(id),
                custom_price REAL,
                effective_date DATE DEFAULT CURRENT_DATE,
                expiry_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "customer_pricing")

        if _table_exists(conn, "customer_pricing"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_customer ON customer_pricing(customer_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_product ON customer_pricing(product_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_tier ON customer_pricing(tier_id)")
                conn.commit()
            except Exception:
                pass

        # Add default_tier_id to customers
        _safe_add_column(conn, "customers", "default_tier_id", "INTEGER REFERENCES price_tiers(id)")

        # ── Vendor Returns ──
        _safe_create_table(conn, """
            CREATE TABLE vendor_returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rga_number TEXT UNIQUE NOT NULL,
                vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                status TEXT DEFAULT 'pending',
                return_date DATE DEFAULT CURRENT_DATE,
                reason TEXT,
                notes TEXT,
                credit_amount REAL DEFAULT 0,
                credit_received INTEGER DEFAULT 0,
                credit_memo_number TEXT,
                shipping_cost REAL DEFAULT 0,
                tracking_number TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                shipped_at TIMESTAMP,
                received_at TIMESTAMP,
                credited_at TIMESTAMP
            )
        """, "vendor_returns")

        _safe_create_table(conn, """
            CREATE TABLE vendor_return_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id INTEGER NOT NULL REFERENCES vendor_returns(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                serial_id INTEGER REFERENCES serial_numbers(id),
                quantity INTEGER NOT NULL DEFAULT 1,
                unit_cost REAL DEFAULT 0,
                line_total REAL DEFAULT 0,
                reason TEXT,
                condition TEXT DEFAULT 'defective',
                po_number TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "vendor_return_lines")

        if _table_exists(conn, "vendor_returns"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vr_vendor ON vendor_returns(vendor_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vr_status ON vendor_returns(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vr_rga ON vendor_returns(rga_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vrl_return ON vendor_return_lines(return_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vrl_product ON vendor_return_lines(product_id)")
                conn.commit()
            except Exception:
                pass

        # ── Kits / Assemblies ──
        _safe_create_table(conn, """
            CREATE TABLE kits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                kit_price REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "kits")

        _safe_create_table(conn, """
            CREATE TABLE kit_components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_id INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "kit_components")

        if _table_exists(conn, "kits"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kit_sku ON kits(sku)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kc_kit ON kit_components(kit_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kc_product ON kit_components(product_id)")
                conn.commit()
            except Exception:
                pass

        # ── Shipments / Delivery Tracking ──
        _safe_create_table(conn, """
            CREATE TABLE shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_type TEXT NOT NULL DEFAULT 'outbound',
                reference_type TEXT,
                reference_id INTEGER,
                so_id INTEGER REFERENCES sales_orders(id),
                po_id INTEGER REFERENCES purchase_orders(id),
                customer_id INTEGER REFERENCES customers(id),
                vendor_id INTEGER REFERENCES vendors(id),
                carrier TEXT,
                service_level TEXT,
                tracking_number TEXT,
                origin_address TEXT,
                destination_address TEXT,
                ship_date DATE,
                estimated_arrival DATE,
                actual_arrival DATE,
                delivery_confirmed INTEGER DEFAULT 0,
                weight_lbs REAL,
                freight_cost REAL DEFAULT 0,
                freight_charge REAL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                notes TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "shipments")

        if _table_exists(conn, "shipments"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_type ON shipments(shipment_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_so ON shipments(so_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_po ON shipments(po_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_customer ON shipments(customer_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_status ON shipments(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_tracking ON shipments(tracking_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ship_eta ON shipments(estimated_arrival)")
                conn.commit()
            except Exception:
                pass

        # ── Supersessions ──
        _safe_create_table(conn, """
            CREATE TABLE supersessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                old_sku TEXT NOT NULL,
                new_sku TEXT NOT NULL,
                supersession_date DATE DEFAULT CURRENT_DATE,
                reason TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(old_sku, new_sku)
            )
        """, "supersessions")

        if _table_exists(conn, "supersessions"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ss_old ON supersessions(old_sku)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ss_new ON supersessions(new_sku)")
                conn.commit()
            except Exception:
                pass

        logger.info("Migration 008 applied successfully")
        return True

    except Exception as e:
        logger.error(f"Migration 008 failed: {e}")
        return False
