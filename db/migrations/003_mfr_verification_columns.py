"""Migration 003: Add manufacturer verification columns to products.

This migration adds:
1. mfr_verified - boolean flag if manufacturer has been verified
2. mfr_verified_source - which source(s) verified it
3. mfr_confidence - weighted confidence score (0.0-1.0)
4. source_vendor - renamed from supplier for clarity

Run with: python -m db.migrations.003_mfr_verification_columns
"""

import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


MIGRATION_UP = """
-- Add verification columns to products
ALTER TABLE products ADD COLUMN mfr_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE products ADD COLUMN mfr_verified_source TEXT;
ALTER TABLE products ADD COLUMN mfr_confidence REAL DEFAULT 0.0;

-- Create index for finding unverified products
CREATE INDEX IF NOT EXISTS idx_products_mfr_verified ON products(mfr_verified);
CREATE INDEX IF NOT EXISTS idx_products_mfr_confidence ON products(mfr_confidence);

-- Add is_oem column to part_numbers for clarity
ALTER TABLE part_numbers ADD COLUMN is_oem BOOLEAN DEFAULT FALSE;
"""


def migrate(conn) -> None:
    """Idempotent entry point for the app-startup migration runner."""
    raw = conn if isinstance(conn, sqlite3.Connection) else getattr(conn, "_conn", conn)
    prod_cols = {row[1] for row in raw.execute("PRAGMA table_info(products)").fetchall()}
    pn_cols = {row[1] for row in raw.execute("PRAGMA table_info(part_numbers)").fetchall()}
    if "mfr_verified" not in prod_cols:
        raw.execute("ALTER TABLE products ADD COLUMN mfr_verified BOOLEAN DEFAULT FALSE")
    if "mfr_verified_source" not in prod_cols:
        raw.execute("ALTER TABLE products ADD COLUMN mfr_verified_source TEXT")
    if "mfr_confidence" not in prod_cols:
        raw.execute("ALTER TABLE products ADD COLUMN mfr_confidence REAL DEFAULT 0.0")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_products_mfr_verified ON products(mfr_verified)")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_products_mfr_confidence ON products(mfr_confidence)")
    if "is_oem" not in pn_cols:
        raw.execute("ALTER TABLE part_numbers ADD COLUMN is_oem BOOLEAN DEFAULT FALSE")
    raw.commit()

def run_migration():
    """Execute the migration."""
    from core.paths import OUTPUT_ROOT
    
    db_path = OUTPUT_ROOT / "inventory.db"
    
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        return False
    
    print(f"\n{'='*60}")
    print("Migration 003: Manufacturer Verification Columns")
    print(f"{'='*60}\n")
    
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.row_factory = sqlite3.Row
    
    try:
        # Check what columns already exist
        cols = [row[1] for row in raw_conn.execute("PRAGMA table_info(products)").fetchall()]
        
        if "mfr_verified" in cols:
            print("  (columns already exist, skipping)")
            return True
        
        raw_conn.executescript(MIGRATION_UP)
        raw_conn.commit()
        print("✓ Migration completed successfully!")
        
        # Verify
        cols = [row[1] for row in raw_conn.execute("PRAGMA table_info(products)").fetchall()]
        new_cols = ["mfr_verified", "mfr_verified_source", "mfr_confidence"]
        for col in new_cols:
            status = "✓" if col in cols else "✗"
            print(f"  {status} products.{col}")
        
        # Check part_numbers
        pn_cols = [row[1] for row in raw_conn.execute("PRAGMA table_info(part_numbers)").fetchall()]
        status = "✓" if "is_oem" in pn_cols else "✗"
        print(f"  {status} part_numbers.is_oem")
        
        return True
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        raw_conn.close()


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
