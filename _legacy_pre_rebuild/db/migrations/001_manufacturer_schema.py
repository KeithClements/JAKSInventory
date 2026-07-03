"""Migration 001: Add manufacturer/product family schema.

This migration adds:
1. manufacturers table - authoritative list of actual manufacturers
2. product_families table - groups related products (alternates/variants)
3. product_alternates table - links products within a family
4. New columns on products: oem_manufacturer, aftermarket_manufacturer, family_id, variant_type
5. Rename existing 'manufacturer' → keep for backward compat, add new structured columns

Run with: python -m db.migrations.001_manufacturer_schema
"""

import os
import sys
import shutil
from pathlib import Path
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from db.database import get_connection


# ============================================================================
# MIGRATION SQL
# ============================================================================

MIGRATION_UP = """
-- ============================================================================
-- 1. MANUFACTURERS TABLE
-- Authoritative list of actual manufacturers (not resellers)
-- ============================================================================
CREATE TABLE IF NOT EXISTS manufacturers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,           -- PAI, MCBEE, AFA, IPD, CUMMINS, CAT
    name TEXT NOT NULL,                  -- Full name: "PAI Industries"
    type TEXT NOT NULL DEFAULT 'aftermarket',  -- oem, aftermarket
    website TEXT,
    part_pattern TEXT,                   -- Regex pattern for their part numbers
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manufacturers_code ON manufacturers(code);
CREATE INDEX IF NOT EXISTS idx_manufacturers_type ON manufacturers(type);

-- Seed with known manufacturers
INSERT OR IGNORE INTO manufacturers (code, name, type, website, part_pattern) VALUES
    ('CUMMINS', 'Cummins Inc.', 'oem', 'https://www.cummins.com', '^[0-9]{7}$'),
    ('CAT', 'Caterpillar Inc.', 'oem', 'https://www.cat.com', '^(10R|20R|3E|1W)-[0-9]+$'),
    ('PAI', 'PAI Industries', 'aftermarket', 'https://www.pai-industries.com', '^(ISX|C15|N14|DT|ISB|ISM|ISL|S60|DD|ISC)[0-9]{3,}'),
    ('MCBEE', 'Interstate McBee', 'aftermarket', 'https://www.interstatemcbee.com', '^(MCB-|IM-|M-)[A-Z0-9]+'),
    ('AFA', 'AFA Industries', 'aftermarket', 'https://www.afaindustries.com', '^AFA[A-Z0-9]+'),
    ('IPD', 'IPD Parts', 'aftermarket', 'https://www.ipdparts.com', '^IPD[A-Z0-9]+');

-- ============================================================================
-- 2. PRODUCT FAMILIES TABLE
-- Groups related products (same base part, different variants)
-- ============================================================================
CREATE TABLE IF NOT EXISTS product_families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                  -- "ISX15 Inframe Kit" 
    base_part_number TEXT,               -- The base/standard part number
    oem_part_number TEXT,                -- Original OEM part this replaces
    aftermarket_manufacturer_id INTEGER REFERENCES manufacturers(id),
    oem_manufacturer_id INTEGER REFERENCES manufacturers(id),
    description TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_families_base_part ON product_families(base_part_number);
CREATE INDEX IF NOT EXISTS idx_families_oem_part ON product_families(oem_part_number);
CREATE INDEX IF NOT EXISTS idx_families_aftermarket_mfr ON product_families(aftermarket_manufacturer_id);

-- ============================================================================
-- 3. PRODUCT ALTERNATES TABLE
-- Links products within a family with relationship info
-- ============================================================================
CREATE TABLE IF NOT EXISTS product_alternates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES product_families(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    variant_type TEXT,                   -- standard, hp, economy, oversized, undersized
    variant_detail TEXT,                 -- "+0.50mm", "-0.25mm", "HP Upgrade"
    is_base_variant BOOLEAN DEFAULT FALSE,  -- TRUE for the standard/base product
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(family_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_alternates_family ON product_alternates(family_id);
CREATE INDEX IF NOT EXISTS idx_alternates_product ON product_alternates(product_id);
CREATE INDEX IF NOT EXISTS idx_alternates_variant_type ON product_alternates(variant_type);

-- ============================================================================
-- 4. ADD COLUMNS TO PRODUCTS TABLE
-- These complement the existing 'manufacturer' field (kept for backward compat)
-- ============================================================================
ALTER TABLE products ADD COLUMN oem_manufacturer TEXT;
ALTER TABLE products ADD COLUMN aftermarket_manufacturer TEXT;
ALTER TABLE products ADD COLUMN aftermarket_manufacturer_id INTEGER REFERENCES manufacturers(id);
ALTER TABLE products ADD COLUMN family_id INTEGER REFERENCES product_families(id);
ALTER TABLE products ADD COLUMN variant_type TEXT;
ALTER TABLE products ADD COLUMN variant_detail TEXT;

CREATE INDEX IF NOT EXISTS idx_products_oem_manufacturer ON products(oem_manufacturer);
CREATE INDEX IF NOT EXISTS idx_products_aftermarket_manufacturer ON products(aftermarket_manufacturer);
CREATE INDEX IF NOT EXISTS idx_products_aftermarket_mfr_id ON products(aftermarket_manufacturer_id);
CREATE INDEX IF NOT EXISTS idx_products_family ON products(family_id);

-- ============================================================================
-- 5. ADD COLUMNS TO VENDORS TABLE
-- Distinguish manufacturers from resellers
-- ============================================================================
ALTER TABLE vendors ADD COLUMN is_manufacturer BOOLEAN DEFAULT FALSE;
ALTER TABLE vendors ADD COLUMN manufacturer_id INTEGER REFERENCES manufacturers(id);

-- Update known vendors
UPDATE vendors SET is_manufacturer = FALSE WHERE name IN ('HHP', 'Highway and Heavy Parts', 'ATL Diesel', 'ATL');
UPDATE vendors SET is_manufacturer = TRUE WHERE name IN ('PAI', 'PAI Industries', 'Interstate McBee', 'AFA', 'IPD');

-- ============================================================================
-- 6. CROSS-REFERENCE CHAIN TABLE
-- Tracks verified cross-references with source
-- ============================================================================
CREATE TABLE IF NOT EXISTS xref_chains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    oem_part_number TEXT NOT NULL,
    oem_manufacturer TEXT,
    aftermarket_part_number TEXT NOT NULL,
    aftermarket_manufacturer TEXT,
    source TEXT,                         -- pai_portal, dpd_scrape, manual, vendor_import
    confidence TEXT DEFAULT 'medium',    -- high, medium, low
    verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(oem_part_number, aftermarket_part_number)
);

CREATE INDEX IF NOT EXISTS idx_xref_chains_oem ON xref_chains(oem_part_number);
CREATE INDEX IF NOT EXISTS idx_xref_chains_aftermarket ON xref_chains(aftermarket_part_number);
CREATE INDEX IF NOT EXISTS idx_xref_chains_source ON xref_chains(source);
"""

# Rollback SQL (for development/testing)
MIGRATION_DOWN = """
DROP TABLE IF EXISTS xref_chains;
DROP TABLE IF EXISTS product_alternates;
DROP TABLE IF EXISTS product_families;
DROP TABLE IF EXISTS manufacturers;

-- Note: SQLite doesn't support DROP COLUMN directly
-- These would require table recreation in production
-- ALTER TABLE products DROP COLUMN oem_manufacturer;
-- ALTER TABLE products DROP COLUMN aftermarket_manufacturer;
-- ALTER TABLE products DROP COLUMN aftermarket_manufacturer_id;
-- ALTER TABLE products DROP COLUMN family_id;
-- ALTER TABLE products DROP COLUMN variant_type;
-- ALTER TABLE products DROP COLUMN variant_detail;
-- ALTER TABLE vendors DROP COLUMN is_manufacturer;
-- ALTER TABLE vendors DROP COLUMN manufacturer_id;
"""


def migrate(conn) -> None:
    """Idempotent entry point used by the app-startup migration runner.

    Runs the same schema work as ``run_migration()`` but without CLI
    prints (which use unicode that crashes on the Windows cp1252 console),
    and skips ``ALTER TABLE ADD COLUMN`` statements for columns that
    already exist.
    """
    import sqlite3

    raw = conn if isinstance(conn, sqlite3.Connection) else getattr(conn, "_conn", conn)

    def _cols(table: str) -> set[str]:
        try:
            rows = raw.execute(f"PRAGMA table_info({table})").fetchall()
        except Exception:
            return set()
        return {row[1] for row in rows}

    products_cols = _cols("products")
    vendors_cols = _cols("vendors")

    safe_lines: list[str] = []
    for line in MIGRATION_UP.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("ALTER TABLE") and "ADD COLUMN" in stripped:
            parts = line.strip().split()
            if len(parts) >= 6:
                tbl = parts[2].lower()
                col = parts[5].rstrip(";")
                existing = (
                    products_cols if tbl == "products"
                    else vendors_cols if tbl == "vendors"
                    else set()
                )
                if col.lower() in {c.lower() for c in existing}:
                    continue
        safe_lines.append(line)

    raw.executescript("\n".join(safe_lines))
    raw.commit()


def backup_database(db_path: Path) -> Path:
    """Create timestamped backup before migration."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    
    backup_path = backup_dir / f"inventory_{timestamp}_pre_mfr_schema.db"
    shutil.copy2(db_path, backup_path)
    print(f"✓ Backup created: {backup_path}")
    return backup_path


def run_migration():
    """Execute the migration."""
    from core.paths import OUTPUT_ROOT
    import sqlite3
    
    db_path = OUTPUT_ROOT / "inventory.db"
    
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        print("  Run the application first to create the database.")
        return False
    
    print(f"\n{'='*60}")
    print("Migration 001: Manufacturer/Product Family Schema")
    print(f"{'='*60}\n")
    
    # Create backup
    print("Step 1: Creating backup...")
    backup_path = backup_database(db_path)
    
    # Run migration using direct sqlite3 connection for executescript
    print("\nStep 2: Running migration...")
    
    # Use raw sqlite3 connection for executescript support
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.row_factory = sqlite3.Row
    
    try:
        # Helper: get existing column names for a table
        def _existing_cols(table: str) -> set[str]:
            rows = raw_conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {row[1] for row in rows}

        # Split migration into CREATE TABLE (idempotent via IF NOT EXISTS)
        # and ALTER TABLE (need column-existence checks).
        # We process the migration SQL line-by-line, skipping ALTER TABLE
        # ADD COLUMN when the column already exists.
        products_cols = _existing_cols("products")
        vendors_cols = _existing_cols("vendors")

        safe_lines: list[str] = []
        skipped = 0
        for line in MIGRATION_UP.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("ALTER TABLE") and "ADD COLUMN" in stripped:
                # Parse: ALTER TABLE <table> ADD COLUMN <col> ...
                parts = line.strip().split()
                # parts: ['ALTER', 'TABLE', '<table>', 'ADD', 'COLUMN', '<col>', ...]
                if len(parts) >= 6:
                    tbl = parts[2].lower()
                    col = parts[5].rstrip(";")
                    existing = products_cols if tbl == "products" else vendors_cols if tbl == "vendors" else set()
                    if col.lower() in {c.lower() for c in existing}:
                        skipped += 1
                        continue
            safe_lines.append(line)

        safe_sql = "\n".join(safe_lines)
        raw_conn.executescript(safe_sql)
        raw_conn.commit()
        if skipped:
            print(f"✓ Migration completed (skipped {skipped} existing columns)")
        else:
            print("✓ Migration completed successfully!")
        
        # Verify tables were created
        print("\nStep 3: Verifying schema...")
        
        # Check new tables
        tables = ["manufacturers", "product_families", "product_alternates", "xref_chains"]
        for table in tables:
            result = raw_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()
            if result:
                count = raw_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  ✓ {table}: {count} rows")
            else:
                print(f"  ✗ {table}: NOT FOUND")
        
        # Check new columns on products
        print("\n  Checking products columns:")
        cols_result = raw_conn.execute("PRAGMA table_info(products)").fetchall()
        col_names = [row[1] for row in cols_result]
        
        new_cols = ["oem_manufacturer", "aftermarket_manufacturer", "aftermarket_manufacturer_id", 
                    "family_id", "variant_type", "variant_detail"]
        for col in new_cols:
            status = "✓" if col in col_names else "✗"
            print(f"    {status} {col}")
        
        # Check manufacturers were seeded
        print("\n  Seeded manufacturers:")
        mfrs = raw_conn.execute("SELECT code, name, type FROM manufacturers ORDER BY type, code").fetchall()
        for mfr in mfrs:
            print(f"    - {mfr[0]}: {mfr[1]} ({mfr[2]})")
        
        print(f"\n{'='*60}")
        print("Migration 001 Complete!")
        print(f"Backup at: {backup_path}")
        print(f"{'='*60}\n")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        print(f"  Database restored from backup is available at: {backup_path}")
        raw_conn.rollback()
        return False
    finally:
        raw_conn.close()


def rollback_migration():
    """Rollback the migration (development only)."""
    print("\n⚠ WARNING: Rollback will drop tables and lose data!")
    confirm = input("Type 'ROLLBACK' to confirm: ")
    
    if confirm != "ROLLBACK":
        print("Rollback cancelled.")
        return False
    
    conn = get_connection()
    
    try:
        for statement in MIGRATION_DOWN.split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    conn.execute(stmt)
                except Exception as e:
                    print(f"  Warning: {e}")
        
        conn.commit()
        print("✓ Rollback completed")
        return True
        
    except Exception as e:
        print(f"✗ Rollback failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run manufacturer schema migration")
    parser.add_argument("--rollback", action="store_true", help="Rollback the migration")
    args = parser.parse_args()
    
    if args.rollback:
        success = rollback_migration()
    else:
        success = run_migration()
    
    sys.exit(0 if success else 1)
