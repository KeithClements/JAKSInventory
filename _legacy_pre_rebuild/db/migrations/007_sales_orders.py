"""Migration 007: Replace work_orders with sales_orders for parts distribution.

Changes:
- Creates sales_orders table (replaces work_orders concept)
- Creates sales_order_lines table
- Adds qty_reserved column to products
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
        # Add qty_reserved to products
        _safe_add_column(conn, "products", "qty_reserved", "INTEGER DEFAULT 0")

        # Sales Orders table
        _safe_create_table(conn, """
            CREATE TABLE sales_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                so_number TEXT UNIQUE NOT NULL,
                customer_id INTEGER REFERENCES customers(id),
                status TEXT DEFAULT 'draft',
                priority TEXT DEFAULT 'normal',
                order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                promised_date DATE,
                ship_method TEXT,
                tracking_number TEXT,
                subtotal REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0,
                tax_amount REAL DEFAULT 0,
                shipping REAL DEFAULT 0,
                total REAL DEFAULT 0,
                notes TEXT,
                internal_notes TEXT,
                invoice_id INTEGER REFERENCES invoices(id),
                created_by TEXT,
                confirmed_at TIMESTAMP,
                picked_at TIMESTAMP,
                shipped_at TIMESTAMP,
                completed_at TIMESTAMP,
                cancelled_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "sales_orders")

        if _table_exists(conn, "sales_orders"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_so_number ON sales_orders(so_number)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_so_customer ON sales_orders(customer_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_so_status ON sales_orders(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_so_invoice ON sales_orders(invoice_id)")
                conn.commit()
            except Exception:
                pass

        # Sales Order Lines
        _safe_create_table(conn, """
            CREATE TABLE sales_order_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                so_id INTEGER NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                serial_id INTEGER REFERENCES serial_numbers(id),
                sku TEXT,
                description TEXT,
                quantity_ordered INTEGER NOT NULL DEFAULT 1,
                quantity_picked INTEGER DEFAULT 0,
                quantity_backordered INTEGER DEFAULT 0,
                unit_price REAL DEFAULT 0,
                line_total REAL DEFAULT 0,
                location_id INTEGER REFERENCES storage_locations(id),
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "sales_order_lines")

        if _table_exists(conn, "sales_order_lines"):
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sol_so ON sales_order_lines(so_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sol_product ON sales_order_lines(product_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sol_backorder ON sales_order_lines(quantity_backordered)")
                conn.commit()
            except Exception:
                pass

        logger.info("Migration 007 applied successfully")
        return True

    except Exception as e:
        logger.error(f"Migration 007 failed: {e}")
        return False
