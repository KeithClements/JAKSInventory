"""Migration 006: Add tables for Phase 2-5 features.

New tables:
- part_interchanges: Cross-reference / interchange numbers (Phase 2)
- inventory_audits + audit_lines: Cycle count / inventory audit (Phase 1C)
- qbo_exports + qbo_export_lines: QBO export file tracking (Phase 1D)
- work_orders + work_order_lines: Repair/work order tracking (Phase 5)
- vehicles: Fleet / vehicle registry (Phase 5)
- vehicle_service_history: Service history per vehicle (Phase 5)
- returns: Returns & warranty tracking (Phase 5)
- esn_cache: ESN engine lookup cache (Phase 4)
- engine_compatibility: Engine-to-part mapping (Phase 4)

Column additions:
- products.min_qty, max_qty, preferred_vendor_id
"""

import logging

logger = logging.getLogger(__name__)


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    try:
        # PostgreSQL
        cursor = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            (table_name,)
        )
        row = cursor.fetchone()
        if row:
            return bool(list(row.values())[0] if isinstance(row, dict) else row[0])
    except Exception:
        pass
    try:
        # SQLite
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cursor.fetchone() is not None
    except Exception:
        pass
    return False


def _get_existing_columns(conn, table_name: str) -> set:
    """Get existing column names for a table."""
    try:
        cursor = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table_name,)
        )
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
    """Safely add a column if it doesn't exist."""
    existing = _get_existing_columns(conn, table_name)
    if col_name not in existing:
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
            conn.commit()
            logger.info(f"Added column {table_name}.{col_name}")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                logger.info(f"Column {table_name}.{col_name} already exists")
            else:
                raise


def _safe_create_table(conn, sql: str, table_name: str):
    """Safely create a table."""
    if not _table_exists(conn, table_name):
        conn.execute(sql)
        conn.commit()
        logger.info(f"Created table: {table_name}")
    else:
        logger.info(f"Table {table_name} already exists, skipping")


def up(conn) -> bool:
    """Apply the migration."""
    try:
        # --- Product columns for Min/Max Restock (Phase 1B) ---
        _safe_add_column(conn, "products", "min_qty", "INTEGER DEFAULT 0")
        _safe_add_column(conn, "products", "max_qty", "INTEGER DEFAULT 0")
        _safe_add_column(conn, "products", "preferred_vendor_id",
                         "INTEGER REFERENCES vendors(id)")

        # --- Part Interchanges (Phase 2) ---
        _safe_create_table(conn, """
            CREATE TABLE part_interchanges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                interchange_number TEXT NOT NULL,
                interchange_type TEXT NOT NULL,
                manufacturer TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_id, interchange_number)
            )
        """, "part_interchanges")

        if _table_exists(conn, "part_interchanges"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_interchange_number ON part_interchanges(interchange_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_interchange_product ON part_interchanges(product_id)")
                conn.commit()
            except Exception:
                pass

        # --- Inventory Audits (Phase 1C) ---
        _safe_create_table(conn, """
            CREATE TABLE inventory_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id INTEGER REFERENCES storage_locations(id),
                audit_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                count_type TEXT DEFAULT 'full',
                status TEXT DEFAULT 'in_progress',
                notes TEXT,
                total_items INTEGER DEFAULT 0,
                total_variances INTEGER DEFAULT 0,
                variance_value REAL DEFAULT 0,
                created_by TEXT,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "inventory_audits")

        _safe_create_table(conn, """
            CREATE TABLE audit_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER NOT NULL REFERENCES inventory_audits(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                system_qty INTEGER DEFAULT 0,
                counted_qty INTEGER,
                variance INTEGER DEFAULT 0,
                variance_value REAL DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "audit_lines")

        if _table_exists(conn, "audit_lines"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_lines_audit ON audit_lines(audit_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_lines_product ON audit_lines(product_id)")
                conn.commit()
            except Exception:
                pass

        # --- QBO Export Tracking (Phase 1D) ---
        _safe_create_table(conn, """
            CREATE TABLE qbo_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                export_type TEXT NOT NULL,
                export_format TEXT DEFAULT 'csv',
                record_count INTEGER DEFAULT 0,
                file_path TEXT,
                notes TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "qbo_exports")

        # Add exported flag to invoices and purchase_orders
        _safe_add_column(conn, "invoices", "qbo_exported", "BOOLEAN DEFAULT FALSE")
        _safe_add_column(conn, "invoices", "qbo_exported_at", "TIMESTAMP")
        _safe_add_column(conn, "purchase_orders", "qbo_exported", "BOOLEAN DEFAULT FALSE")
        _safe_add_column(conn, "purchase_orders", "qbo_exported_at", "TIMESTAMP")

        # --- ESN Cache (Phase 4) ---
        _safe_create_table(conn, """
            CREATE TABLE esn_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                esn TEXT UNIQUE NOT NULL,
                oem TEXT NOT NULL,
                engine_model TEXT,
                horsepower TEXT,
                build_date TEXT,
                cpl TEXT,
                application TEXT,
                emission_level TEXT,
                raw_data TEXT,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "esn_cache")

        _safe_create_table(conn, """
            CREATE TABLE engine_compatibility (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                engine_model TEXT NOT NULL,
                category TEXT NOT NULL,
                product_id INTEGER REFERENCES products(id),
                oem_part_number TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "engine_compatibility")

        if _table_exists(conn, "esn_cache"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_esn_esn ON esn_cache(esn)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_esn_oem ON esn_cache(oem)")
                conn.commit()
            except Exception:
                pass

        if _table_exists(conn, "engine_compatibility"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_engine_compat_model ON engine_compatibility(engine_model)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_engine_compat_product ON engine_compatibility(product_id)")
                conn.commit()
            except Exception:
                pass

        # --- Work Orders (Phase 5A) ---
        _safe_create_table(conn, """
            CREATE TABLE work_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wo_number TEXT UNIQUE NOT NULL,
                customer_id INTEGER REFERENCES customers(id),
                vehicle_id INTEGER,
                status TEXT DEFAULT 'draft',
                priority TEXT DEFAULT 'normal',
                complaint TEXT,
                diagnosis TEXT,
                technician TEXT,
                estimated_hours REAL DEFAULT 0,
                actual_hours REAL DEFAULT 0,
                labor_rate REAL DEFAULT 0,
                parts_total REAL DEFAULT 0,
                labor_total REAL DEFAULT 0,
                total REAL DEFAULT 0,
                invoice_id INTEGER REFERENCES invoices(id),
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                notes TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "work_orders")

        _safe_create_table(conn, """
            CREATE TABLE work_order_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wo_id INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
                line_type TEXT NOT NULL DEFAULT 'part',
                product_id INTEGER REFERENCES products(id),
                serial_id INTEGER REFERENCES serial_numbers(id),
                description TEXT,
                quantity INTEGER DEFAULT 1,
                unit_price REAL DEFAULT 0,
                line_total REAL DEFAULT 0,
                hours REAL DEFAULT 0,
                technician TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "work_order_lines")

        if _table_exists(conn, "work_orders"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wo_number ON work_orders(wo_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wo_customer ON work_orders(customer_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wo_status ON work_orders(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wo_vehicle ON work_orders(vehicle_id)")
                conn.commit()
            except Exception:
                pass

        if _table_exists(conn, "work_order_lines"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wol_wo ON work_order_lines(wo_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wol_product ON work_order_lines(product_id)")
                conn.commit()
            except Exception:
                pass

        # --- Vehicle / Fleet Registry (Phase 5B) ---
        _safe_create_table(conn, """
            CREATE TABLE vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER REFERENCES customers(id),
                unit_number TEXT,
                vin TEXT,
                year INTEGER,
                make TEXT,
                model TEXT,
                engine_make TEXT,
                engine_model TEXT,
                esn TEXT,
                mileage INTEGER DEFAULT 0,
                license_plate TEXT,
                dot_number TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "vehicles")

        if _table_exists(conn, "vehicles"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_customer ON vehicles(customer_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_vin ON vehicles(vin)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_unit ON vehicles(unit_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_esn ON vehicles(esn)")
                conn.commit()
            except Exception:
                pass

        # --- Returns & Warranty (Phase 5E) ---
        _safe_create_table(conn, """
            CREATE TABLE returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rma_number TEXT UNIQUE NOT NULL,
                invoice_id INTEGER REFERENCES invoices(id),
                customer_id INTEGER REFERENCES customers(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                serial_id INTEGER REFERENCES serial_numbers(id),
                quantity INTEGER DEFAULT 1,
                reason TEXT,
                condition TEXT,
                status TEXT DEFAULT 'pending',
                action_taken TEXT,
                refund_amount REAL DEFAULT 0,
                vendor_claim_id TEXT,
                vendor_claim_status TEXT,
                warranty_expiry DATE,
                notes TEXT,
                received_date DATE,
                resolved_date DATE,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "returns")

        if _table_exists(conn, "returns"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_rma ON returns(rma_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_customer ON returns(customer_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_product ON returns(product_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_returns_status ON returns(status)")
                conn.commit()
            except Exception:
                pass

        logger.info("Migration 006 applied successfully")
        return True

    except Exception as e:
        logger.error(f"Migration 006 failed: {e}")
        return False
