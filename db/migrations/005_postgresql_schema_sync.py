"""Migration 005: Sync PostgreSQL schema with SQLite schema.

Adds missing columns to various tables that exist in SQLite but were missing
in the PostgreSQL (Supabase) database due to schema drift.

Tables fixed:
- customers: city, state, zip, country, payment_terms, tax_exempt, credit_limit, is_active, etc.
- invoices: total, tax, paid_date, payment_method, payment_reference, shipping_address, etc.
- vendors: code, contact_email, contact_phone, payment_terms, is_active, qbo_vendor_id, etc.
- products: shopify_status, shopify_published_at, local_modified_at, reorder_point, qty_on_hand
- purchase_orders: received_date, shipping, created_by
- serial_numbers: condition, created_by
"""

import logging

logger = logging.getLogger(__name__)


def _get_existing_columns(conn, table_name: str) -> set:
    """Get existing column names for a table, supporting both SQLite and PostgreSQL."""
    try:
        # Try PostgreSQL first
        cursor = conn.execute(f"""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = '{table_name}'
        """)
        rows = cursor.fetchall()
        if rows:
            if isinstance(rows[0], dict):
                return {row['column_name'] for row in rows}
            else:
                return {row[0] for row in rows}
    except Exception:
        pass
    
    # Fall back to SQLite
    try:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
        if rows and isinstance(rows[0], dict):
            return {row['name'] for row in rows}
        else:
            return {row[1] for row in rows}
    except Exception:
        pass
    
    return set()


def _add_column_safe(conn, table_name: str, col_name: str, col_def: str) -> bool:
    """Safely add a column if it doesn't exist."""
    try:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
        conn.commit()
        logger.info(f"Added column {table_name}.{col_name}")
        return True
    except Exception as e:
        conn.rollback()
        if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
            logger.debug(f"Column {table_name}.{col_name} already exists")
            return True
        else:
            logger.error(f"Failed to add {table_name}.{col_name}: {e}")
            return False


def up(conn) -> bool:
    """Apply the migration."""
    try:
        # Define all missing columns by table
        schema_fixes = {
            'products': [
                ('shopify_status', "TEXT DEFAULT 'draft'"),
                ('shopify_published_at', 'TIMESTAMP'),
                ('local_modified_at', 'TIMESTAMP'),
                ('reorder_point', 'INTEGER DEFAULT 0'),
                ('qty_on_hand', 'INTEGER DEFAULT 0'),
                ('uploaded_at', 'TIMESTAMP'),
                ('hhp_last_price_check', 'TIMESTAMP'),
                ('hhp_price_history', 'TEXT'),
            ],
            'customers': [
                ('city', 'TEXT'),
                ('state', 'TEXT'),
                ('zip', 'TEXT'),
                ('country', "TEXT DEFAULT 'USA'"),
                ('payment_terms', "TEXT DEFAULT 'Net 30'"),
                ('tax_exempt', 'BOOLEAN DEFAULT FALSE'),
                ('credit_limit', 'DECIMAL(10,2) DEFAULT 0'),
                ('is_active', 'BOOLEAN DEFAULT TRUE'),
                ('qbo_customer_id', 'TEXT'),
                ('shopify_customer_id', 'TEXT'),
                ('updated_at', 'TIMESTAMP'),
            ],
            'vendors': [
                ('code', 'TEXT UNIQUE'),
                ('contact_email', 'TEXT'),
                ('contact_phone', 'TEXT'),
                ('payment_terms', 'TEXT'),
                ('is_active', 'BOOLEAN DEFAULT TRUE'),
                ('qbo_vendor_id', 'TEXT'),
                ('updated_at', 'TIMESTAMP'),
            ],
            'invoices': [
                ('tax', 'DECIMAL(10,2) DEFAULT 0'),
                ('total', 'DECIMAL(10,2) DEFAULT 0'),
                ('paid_date', 'DATE'),
                ('payment_method', 'TEXT'),
                ('payment_reference', 'TEXT'),
                ('shipping_address', 'TEXT'),
                ('qbo_invoice_id', 'TEXT'),
                ('created_by', 'TEXT'),
            ],
            'purchase_orders': [
                ('received_date', 'DATE'),
                ('shipping', 'DECIMAL(10,2) DEFAULT 0'),
                ('created_by', 'TEXT'),
            ],
            'serial_numbers': [
                ('condition', 'TEXT'),
                ('notes', 'TEXT'),
                ('created_by', 'TEXT'),
            ],
        }
        
        total_added = 0
        total_failed = 0
        
        for table_name, columns in schema_fixes.items():
            existing = _get_existing_columns(conn, table_name)
            if not existing:
                logger.warning(f"Table {table_name} not found or has no columns")
                continue
                
            for col_name, col_def in columns:
                if col_name not in existing:
                    if _add_column_safe(conn, table_name, col_name, col_def):
                        total_added += 1
                    else:
                        total_failed += 1
        
        # Copy data from legacy column names if they exist
        try:
            conn.execute("UPDATE invoices SET total = total_amount WHERE total IS NULL OR total = 0")
            conn.commit()
        except Exception:
            conn.rollback()
        
        logger.info(f"Migration 005 complete: {total_added} columns added, {total_failed} failed")
        return total_failed == 0
        
    except Exception as e:
        logger.error(f"Migration 005 failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def down(conn) -> bool:
    """Rollback the migration (not implemented - columns cannot be easily dropped)."""
    logger.warning("Migration 005 rollback not implemented")
    return False
