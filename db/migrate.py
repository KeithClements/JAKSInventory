"""Database migration script: SQLite to PostgreSQL.

Usage:
    1. Set up Supabase project at https://supabase.com
    2. Get your connection string from Project Settings > Database
    3. Run: DATABASE_URL=postgresql://... python -m db.migrate
    
This will:
    - Export all data from local SQLite (output/inventory.db)
    - Create tables in PostgreSQL
    - Import all products, part_numbers, xref_links, and images
"""

import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import parse_database_url, SQLiteConnection, PostgreSQLConnection
from db.schema import SQLITE_SCHEMA, POSTGRESQL_SCHEMA


def export_sqlite_data(sqlite_path: str) -> dict:
    """Export all data from SQLite database."""
    conn = SQLiteConnection(sqlite_path)
    
    data = {
        "products": [],
        "part_numbers": [],
        "xref_links": [],
        "product_images": [],
    }
    
    # Export products
    rows = conn.execute("SELECT * FROM products").fetchall()
    data["products"] = rows
    print(f"  Exported {len(rows)} products")
    
    # Export part_numbers
    rows = conn.execute("SELECT * FROM part_numbers").fetchall()
    data["part_numbers"] = rows
    print(f"  Exported {len(rows)} part numbers")
    
    # Export xref_links
    rows = conn.execute("SELECT * FROM xref_links").fetchall()
    data["xref_links"] = rows
    print(f"  Exported {len(rows)} xref links")
    
    # Export product_images
    rows = conn.execute("SELECT * FROM product_images").fetchall()
    data["product_images"] = rows
    print(f"  Exported {len(rows)} product images")
    
    conn.close()
    return data


def import_to_postgresql(pg_config: dict, data: dict) -> None:
    """Import data into PostgreSQL database."""
    conn = PostgreSQLConnection(
        user=pg_config["user"],
        password=pg_config["password"],
        host=pg_config["host"],
        port=pg_config["port"],
        database=pg_config["database"],
    )
    
    # Create schema
    print("  Creating schema...")
    for statement in POSTGRESQL_SCHEMA.split(";"):
        stmt = statement.strip()
        if stmt and not stmt.startswith("--"):
            try:
                conn.execute(stmt)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"    Warning: {e}")
    conn.commit()
    
    # Track ID mappings (SQLite ID -> PostgreSQL ID)
    product_id_map = {}
    part_number_id_map = {}
    
    # Import products
    print(f"  Importing {len(data['products'])} products...")
    for row in data["products"]:
        old_id = row.get("id")
        cols = [k for k in row.keys() if k != "id"]
        values = [row[k] for k in cols]
        
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        
        cursor = conn.execute(
            f"INSERT INTO products ({col_names}) VALUES ({placeholders}) RETURNING id",
            tuple(values)
        )
        result = cursor.fetchone()
        if result:
            new_id = result.get("id") if isinstance(result, dict) else result[0]
            product_id_map[old_id] = new_id
    conn.commit()
    print(f"    Mapped {len(product_id_map)} product IDs")
    
    # Import part_numbers
    print(f"  Importing {len(data['part_numbers'])} part numbers...")
    for row in data["part_numbers"]:
        old_id = row.get("id")
        old_product_id = row.get("product_id")
        
        # Skip if product wasn't migrated
        if old_product_id not in product_id_map:
            continue
            
        new_product_id = product_id_map[old_product_id]
        
        cols = [k for k in row.keys() if k not in ("id", "product_id")]
        values = [row[k] for k in cols]
        
        cols.insert(0, "product_id")
        values.insert(0, new_product_id)
        
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        
        try:
            cursor = conn.execute(
                f"INSERT INTO part_numbers ({col_names}) VALUES ({placeholders}) RETURNING id",
                tuple(values)
            )
            result = cursor.fetchone()
            if result:
                new_id = result.get("id") if isinstance(result, dict) else result[0]
                part_number_id_map[old_id] = new_id
        except Exception as e:
            if "duplicate" not in str(e).lower():
                print(f"    Warning part_number: {e}")
    conn.commit()
    print(f"    Mapped {len(part_number_id_map)} part number IDs")
    
    # Import xref_links
    print(f"  Importing {len(data['xref_links'])} xref links...")
    imported_xrefs = 0
    for row in data["xref_links"]:
        old_a = row.get("part_a_id")
        old_b = row.get("part_b_id")
        
        # Skip if parts weren't migrated
        if old_a not in part_number_id_map or old_b not in part_number_id_map:
            continue
        
        new_a = part_number_id_map[old_a]
        new_b = part_number_id_map[old_b]
        
        try:
            conn.execute(
                """INSERT INTO xref_links (part_a_id, part_b_id, source, confidence)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (new_a, new_b, row.get("source"), row.get("confidence"))
            )
            imported_xrefs += 1
        except Exception as e:
            if "duplicate" not in str(e).lower():
                print(f"    Warning xref: {e}")
    conn.commit()
    print(f"    Imported {imported_xrefs} xref links")
    
    # Import product_images
    print(f"  Importing {len(data['product_images'])} product images...")
    imported_images = 0
    for row in data["product_images"]:
        old_product_id = row.get("product_id")
        
        # Skip if product wasn't migrated
        if old_product_id not in product_id_map:
            continue
        
        new_product_id = product_id_map[old_product_id]
        
        cols = [k for k in row.keys() if k not in ("id", "product_id")]
        values = [row[k] for k in cols]
        
        cols.insert(0, "product_id")
        values.insert(0, new_product_id)
        
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        
        try:
            conn.execute(
                f"INSERT INTO product_images ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                tuple(values)
            )
            imported_images += 1
        except Exception as e:
            if "duplicate" not in str(e).lower():
                print(f"    Warning image: {e}")
    conn.commit()
    print(f"    Imported {imported_images} product images")
    
    conn.close()


def main():
    """Run migration from SQLite to PostgreSQL."""
    # Check for DATABASE_URL
    pg_url = os.environ.get("DATABASE_URL", "")
    
    if not pg_url or not pg_url.startswith("postgres"):
        print("Error: Set DATABASE_URL environment variable to PostgreSQL connection string")
        print("Example: DATABASE_URL=postgresql://user:pass@host:5432/dbname python -m db.migrate")
        print("\nFor Supabase:")
        print("  1. Go to https://supabase.com and create a project")
        print("  2. Go to Project Settings > Database > Connection string")
        print("  3. Copy the URI and set it as DATABASE_URL")
        return 1
    
    # Parse PostgreSQL URL
    try:
        pg_config = parse_database_url(pg_url)
    except ValueError as e:
        print(f"Error parsing DATABASE_URL: {e}")
        return 1
    
    # Find SQLite database
    sqlite_path = Path(__file__).parent.parent / "output" / "inventory.db"
    if not sqlite_path.exists():
        print(f"SQLite database not found: {sqlite_path}")
        print("Run some scrapes first to populate the database.")
        return 1
    
    print("=" * 60)
    print("JAKS Scraper Database Migration: SQLite -> PostgreSQL")
    print("=" * 60)
    print(f"\nSource: {sqlite_path}")
    print(f"Target: {pg_config['host']}:{pg_config['port']}/{pg_config['database']}")
    print()
    
    # Confirm
    response = input("Continue with migration? (y/n): ").strip().lower()
    if response != "y":
        print("Migration cancelled.")
        return 0
    
    print("\n[1/2] Exporting from SQLite...")
    data = export_sqlite_data(str(sqlite_path))
    
    print("\n[2/2] Importing to PostgreSQL...")
    import_to_postgresql(pg_config, data)
    
    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Add DATABASE_URL to your .env file")
    print("  2. Test with: python -c \"from db import get_db_stats; print(get_db_stats())\"")
    print("  3. Your inventory is now accessible from anywhere!")
    
    return 0


def apply_schema_updates():
    """Apply incremental schema updates (Phase J: add serial_id to invoice_lines)."""
    from db.database import get_db
    
    migrations = [
        # Phase J: Add serial_id column to invoice_lines for serial tracking on sales
        ("invoice_lines_serial_id", """
            ALTER TABLE invoice_lines ADD COLUMN serial_id INTEGER REFERENCES serial_numbers(id)
        """),
        # Phase K: Shopify sync tracking
        ("products_shopify_variant_id", """
            ALTER TABLE products ADD COLUMN shopify_variant_id INTEGER
        """),
        ("products_shopify_synced_at", """
            ALTER TABLE products ADD COLUMN shopify_synced_at TIMESTAMP
        """),
        ("products_needs_shopify_update", """
            ALTER TABLE products ADD COLUMN needs_shopify_update BOOLEAN DEFAULT FALSE
        """),
        ("products_shopify_inventory_item_id", """
            ALTER TABLE products ADD COLUMN shopify_inventory_item_id INTEGER
        """),
        ("products_uploaded_at", """
            ALTER TABLE products ADD COLUMN uploaded_at TIMESTAMP
        """),
        # App settings key-value store
        ("app_settings_table", """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """),
        # Quote ESN / VIN tracking
        ("quotes_esn", "ALTER TABLE quotes ADD COLUMN esn TEXT DEFAULT ''"),
        ("quotes_vin", "ALTER TABLE quotes ADD COLUMN vin TEXT DEFAULT ''"),
        # Quote follow-up tracking
        ("quotes_follow_up_date", "ALTER TABLE quotes ADD COLUMN follow_up_date TEXT"),
        ("quotes_next_action", "ALTER TABLE quotes ADD COLUMN next_action TEXT DEFAULT ''"),
        ("quotes_priority", "ALTER TABLE quotes ADD COLUMN priority TEXT DEFAULT 'normal'"),
    ]
    
    with get_db() as conn:
        # Check which migrations have been applied
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        except:
            pass
        
        for name, sql in migrations:
            # Check if already applied
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE name = %s", (name,)
            ).fetchone()
            
            if row:
                print(f"  ✓ {name} (already applied)")
                continue
            
            try:
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES (%s)", (name,)
                )
                conn.commit()
                print(f"  ✅ {name} applied")
            except Exception as e:
                if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                    # Column already exists, mark as applied
                    conn.execute(
                        "INSERT INTO schema_migrations (name) VALUES (%s)", (name,)
                    )
                    conn.commit()
                    print(f"  ✓ {name} (column exists)")
                else:
                    print(f"  ❌ {name} failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
