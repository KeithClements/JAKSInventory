"""Migration 002: Add recommendation scoring columns.

This migration adds:
1. is_recommended column to product_alternates
2. recommendation_score column to product_alternates
3. variant_category column (enum-like for quality/size/manufacturer/package)

Run with: python -m db.migrations.002_recommendation_scoring
"""

import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


MIGRATION_UP = """
-- Add recommendation columns to product_alternates
ALTER TABLE product_alternates ADD COLUMN is_recommended BOOLEAN DEFAULT FALSE;
ALTER TABLE product_alternates ADD COLUMN recommendation_score REAL DEFAULT 0;
ALTER TABLE product_alternates ADD COLUMN variant_category TEXT;

-- Create index for finding recommended products
CREATE INDEX IF NOT EXISTS idx_alternates_recommended ON product_alternates(family_id, is_recommended);

-- Add variant category to products table too
ALTER TABLE products ADD COLUMN variant_category TEXT;
"""


def migrate(conn) -> None:
    """Idempotent entry point for the app-startup migration runner."""
    raw = conn if isinstance(conn, sqlite3.Connection) else getattr(conn, "_conn", conn)
    alt_cols = {row[1] for row in raw.execute("PRAGMA table_info(product_alternates)").fetchall()}
    prod_cols = {row[1] for row in raw.execute("PRAGMA table_info(products)").fetchall()}
    if "is_recommended" not in alt_cols:
        raw.execute("ALTER TABLE product_alternates ADD COLUMN is_recommended BOOLEAN DEFAULT FALSE")
    if "recommendation_score" not in alt_cols:
        raw.execute("ALTER TABLE product_alternates ADD COLUMN recommendation_score REAL DEFAULT 0")
    if "variant_category" not in alt_cols:
        raw.execute("ALTER TABLE product_alternates ADD COLUMN variant_category TEXT")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_alternates_recommended ON product_alternates(family_id, is_recommended)")
    if "variant_category" not in prod_cols:
        raw.execute("ALTER TABLE products ADD COLUMN variant_category TEXT")
    raw.commit()

def run_migration():
    """Execute the migration."""
    from core.paths import OUTPUT_ROOT
    
    db_path = OUTPUT_ROOT / "inventory.db"
    
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        return False
    
    print(f"\n{'='*60}")
    print("Migration 002: Recommendation Scoring")
    print(f"{'='*60}\n")
    
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.row_factory = sqlite3.Row
    
    try:
        # Check what columns already exist
        cols = [row[1] for row in raw_conn.execute("PRAGMA table_info(product_alternates)").fetchall()]
        
        if "is_recommended" in cols:
            print("  (columns already exist, skipping)")
            return True
        
        raw_conn.executescript(MIGRATION_UP)
        raw_conn.commit()
        print("✓ Migration completed successfully!")
        
        # Verify
        cols = [row[1] for row in raw_conn.execute("PRAGMA table_info(product_alternates)").fetchall()]
        new_cols = ["is_recommended", "recommendation_score", "variant_category"]
        for col in new_cols:
            status = "✓" if col in cols else "✗"
            print(f"  {status} {col}")
        
        return True
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        raw_conn.close()


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
