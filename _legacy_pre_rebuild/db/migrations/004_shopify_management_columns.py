"""Migration 004: Add Shopify Management Screen columns.

Adds new columns to products table for Shopify Management Screen:
- shopify_status: Track product status (active/draft/archived)
- shopify_published_at: When product was published
- local_modified_at: Track local modifications
- reorder_point: Inventory reorder threshold
- hhp_last_price_check: Last HHP price check timestamp
- hhp_price_history: JSON array of price history
"""

import logging

logger = logging.getLogger(__name__)


def _get_existing_columns(conn) -> set:
    """Get existing column names, supporting both SQLite and PostgreSQL."""
    try:
        # Try PostgreSQL first
        cursor = conn.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'products'
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
        cursor = conn.execute("PRAGMA table_info(products)")
        rows = cursor.fetchall()
        if rows and isinstance(rows[0], dict):
            return {row['name'] for row in rows}
        else:
            return {row[1] for row in rows}
    except Exception:
        pass
    
    return set()


def up(conn) -> bool:
    """Apply the migration."""
    try:
        existing_columns = _get_existing_columns(conn)
        logger.info(f"Found existing columns: {existing_columns}")
        
        new_columns = [
            ("shopify_status", "TEXT DEFAULT 'draft'"),
            ("shopify_published_at", "TIMESTAMP"),
            ("local_modified_at", "TIMESTAMP"),
            ("reorder_point", "INTEGER DEFAULT 0"),
            ("hhp_last_price_check", "TIMESTAMP"),
            ("hhp_price_history", "TEXT"),
        ]
        
        for col_name, col_def in new_columns:
            if col_name not in existing_columns:
                logger.info(f"Adding column {col_name}...")
                try:
                    conn.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_def}")
                    conn.commit()  # Commit each column add separately for PostgreSQL
                except Exception as e:
                    if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                        logger.info(f"Column {col_name} already exists, skipping")
                    else:
                        raise
            else:
                logger.info(f"Column {col_name} already exists, skipping")
        
        conn.commit()
        logger.info("Migration 004 applied successfully")
        return True
        
    except Exception as e:
        logger.error(f"Migration 004 failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def down(conn) -> bool:
    """Rollback the migration (SQLite doesn't support DROP COLUMN easily)."""
    logger.warning("Migration 004 rollback not implemented (SQLite limitation)")
    return False
