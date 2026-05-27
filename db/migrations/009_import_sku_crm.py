"""Migration 009: Bulk import, auto-SKU generation, and CRM activity log.

Creates tables for:
- sku_prefixes: Manufacturer/vendor prefix config (PAI -> JAKS-PAI-XXXXXX)
- sku_sequences: Track next sequence number per prefix
- import_batches: Log of bulk import operations
- customer_activities: CRM activity log (visits, calls, notes, follow-ups)
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
        return False


def _get_existing_columns(conn, table_name: str) -> set:
    try:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return {row[1] if isinstance(row, (tuple, list)) else row["name"] for row in cursor.fetchall()}
    except Exception:
        return set()


def _safe_add_column(conn, table_name: str, col_name: str, col_def: str):
    cols = _get_existing_columns(conn, table_name)
    if col_name not in cols:
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
            logger.info(f"Added column {col_name} to {table_name}")
        except Exception as e:
            if "duplicate" not in str(e).lower():
                logger.warning(f"Could not add column {col_name}: {e}")


def _safe_create_table(conn, sql: str, table_name: str):
    if not _table_exists(conn, table_name):
        conn.execute(sql)
        logger.info(f"Created table: {table_name}")


def up(conn) -> bool:
    """Run migration 009."""

    # SKU prefix configuration per manufacturer/vendor
    _safe_create_table(conn, """
        CREATE TABLE sku_prefixes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prefix TEXT NOT NULL UNIQUE,
            manufacturer TEXT,
            vendor_id INTEGER,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        )
    """, "sku_prefixes")

    # Sequence tracker per prefix (JAKS-PAI-000001, JAKS-PAI-000002...)
    _safe_create_table(conn, """
        CREATE TABLE sku_sequences (
            prefix TEXT PRIMARY KEY,
            last_number INTEGER NOT NULL DEFAULT 0
        )
    """, "sku_sequences")

    # Import batch log
    _safe_create_table(conn, """
        CREATE TABLE import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            source_type TEXT DEFAULT 'csv',
            total_rows INTEGER DEFAULT 0,
            imported INTEGER DEFAULT 0,
            updated INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            error_details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """, "import_batches")

    # CRM activity log
    _safe_create_table(conn, """
        CREATE TABLE customer_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL DEFAULT 'note',
            subject TEXT,
            notes TEXT,
            follow_up_date TEXT,
            completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """, "customer_activities")

    # Indexes
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_sku_prefixes_mfg ON sku_prefixes(manufacturer)",
        "CREATE INDEX IF NOT EXISTS idx_sku_prefixes_vendor ON sku_prefixes(vendor_id)",
        "CREATE INDEX IF NOT EXISTS idx_import_batches_date ON import_batches(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_customer_activities_cust ON customer_activities(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_customer_activities_type ON customer_activities(activity_type)",
        "CREATE INDEX IF NOT EXISTS idx_customer_activities_followup ON customer_activities(follow_up_date)",
        "CREATE INDEX IF NOT EXISTS idx_customer_activities_completed ON customer_activities(completed)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    conn.commit()
    logger.info("Migration 009 complete: import/SKU/CRM tables")
    return True
