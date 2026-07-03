"""Migration: Create families for existing products.

This script creates product families based on primary_part / oem_part_number
for products that don't have a family assigned.

Run with: python -m db.migrate_create_families
"""

import sqlite3
from pathlib import Path
from typing import Optional

from db.database import get_connection
from db.product_family_service import (
    create_family,
    get_family_by_part_number,
    add_product_to_family,
    detect_variant_type,
    VARIANT_STANDARD,
)


def migrate_create_families(db_path: Optional[Path] = None, dry_run: bool = True):
    """Create families for existing products based on part numbers.
    
    Args:
        db_path: Path to database (uses default if None)
        dry_run: If True, print what would happen without making changes
    """
    conn = get_connection(db_path)
    
    print("=" * 60)
    print("Family Migration for Existing Products")
    print("=" * 60)
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (making changes)'}")
    print()
    
    # Get all products without a family, joined with their primary part number
    cursor = conn.execute("""
        SELECT p.id, p.sku, p.title, p.family_id,
               (SELECT pn.number FROM part_numbers pn 
                WHERE pn.product_id = p.id AND pn.is_primary = 1 
                LIMIT 1) as primary_part,
               (SELECT pn.number FROM part_numbers pn 
                WHERE pn.product_id = p.id AND pn.type = 'oem' 
                LIMIT 1) as oem_part_number
        FROM products p
        WHERE p.family_id IS NULL OR p.family_id = 0
        ORDER BY primary_part, p.sku
    """)
    products = cursor.fetchall()
    
    if not products:
        print("No products found without families.")
        return
    
    print(f"Found {len(products)} products without families")
    print()
    
    # Group products by their family key (primary_part or oem_part_number)
    family_groups = {}
    for row in products:
        prod_id = row["id"]
        sku = row["sku"] or ""
        title = row["title"] or ""
        primary_part = (row["primary_part"] or "").strip().upper()
        oem_pn = (row["oem_part_number"] or "").strip().upper()
        
        # Determine family key - try multiple sources
        family_key = oem_pn if oem_pn else primary_part
        
        # Fallback: extract from SKU (JAKS-XXXXX -> XXXXX)
        if not family_key and sku:
            if sku.upper().startswith("JAKS-"):
                family_key = sku[5:].upper()
            else:
                family_key = sku.upper()
        
        if not family_key:
            print(f"  SKIP: {sku} - no part number for family")
            continue
        
        if family_key not in family_groups:
            family_groups[family_key] = []
        family_groups[family_key].append({
            "id": prod_id,
            "sku": sku,
            "title": title,
            "primary_part": primary_part or family_key,
            "oem_pn": oem_pn,
        })
    
    print(f"Grouped into {len(family_groups)} potential families")
    print()
    
    # Create families
    created_count = 0
    linked_count = 0
    
    for family_key, members in sorted(family_groups.items()):
        print(f"Family: {family_key} ({len(members)} products)")
        
        # Check if family already exists
        existing = get_family_by_part_number(family_key)
        
        if existing:
            print(f"  → Existing family found: {existing.name} (ID: {existing.id})")
            family = existing
        else:
            # Create new family using first product's title
            first = members[0]
            family_name = first["title"].split(" - ")[0] if " - " in first["title"] else first["title"]
            family_name = family_name[:100]  # Truncate
            
            if dry_run:
                print(f"  → Would CREATE family: {family_name}")
                family = None
            else:
                family = create_family(
                    name=family_name,
                    base_part_number=family_key,
                    oem_part_number=first["oem_pn"] if first["oem_pn"] else None,
                )
                print(f"  → CREATED family: {family.name} (ID: {family.id})")
                created_count += 1
        
        # Link products to family
        for member in members:
            variant_type, variant_detail = detect_variant_type(
                member["primary_part"] or member["sku"],
                member["title"]
            )
            
            if dry_run:
                print(f"    • Would link: {member['sku']} [{variant_type}]")
            else:
                if family:
                    add_product_to_family(
                        family.id,
                        member["id"],
                        variant_type=variant_type,
                        variant_detail=variant_detail,
                        is_base_variant=(variant_type == VARIANT_STANDARD),
                    )
                    # Update product's family_id
                    conn.execute(
                        "UPDATE products SET family_id = ? WHERE id = ?",
                        (family.id, member["id"])
                    )
                    print(f"    • Linked: {member['sku']} [{variant_type}]")
                    linked_count += 1
        
        print()
    
    if not dry_run:
        conn.commit()
    
    conn.close()
    
    print("=" * 60)
    print("Summary:")
    if dry_run:
        print(f"  Would create {len(family_groups)} families")
        print(f"  Would link {sum(len(m) for m in family_groups.values())} products")
        print()
        print("Run with dry_run=False to apply changes")
    else:
        print(f"  Created {created_count} new families")
        print(f"  Linked {linked_count} products to families")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    
    dry_run = "--apply" not in sys.argv
    
    if dry_run:
        print("Running in DRY RUN mode. Use --apply to make changes.")
        print()
    
    migrate_create_families(dry_run=dry_run)
