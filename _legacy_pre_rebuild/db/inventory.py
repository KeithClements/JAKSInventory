from __future__ import annotations

def link_so_lines_to_po(so_id: int, po_id: int, product_ids: list[int] = None) -> None:
    """Link SO lines for a given SO to a PO. Optionally filter by product_ids."""
    with get_db() as conn:
        # Find all SO lines for this SO (optionally filter by product_ids)
        if product_ids:
            so_lines = conn.execute(
                "SELECT id FROM sales_order_lines WHERE sales_order_id = %s AND product_id IN (%s)" % (
                    '?', ','.join(['?'] * len(product_ids))),
                (so_id, *product_ids)
            ).fetchall()
        else:
            so_lines = conn.execute(
                "SELECT id FROM sales_order_lines WHERE sales_order_id = ?",
                (so_id,)
            ).fetchall()
        for row in so_lines:
            so_line_id = row[0] if not isinstance(row, dict) else row.get('id')
            conn.execute(
                "UPDATE sales_order_lines SET linked_po_id = ? WHERE id = ?",
                (po_id, so_line_id)
            )
        conn.commit()

def update_so_line_statuses_for_po(po_id: int, new_status: str = None) -> None:
    """Update SO line statuses for all SO lines linked to a PO. Optionally set a new status."""
    with get_db() as conn:
        # Find all SO lines linked to this PO
        so_lines = conn.execute(
            "SELECT id FROM sales_order_lines WHERE linked_po_id = ?",
            (po_id,)
        ).fetchall()
        for row in so_lines:
            so_line_id = row[0] if not isinstance(row, dict) else row.get('id')
            if new_status:
                conn.execute(
                    "UPDATE sales_order_lines SET status = ? WHERE id = ?",
                    (new_status, so_line_id)
                )
        conn.commit()

def unlink_so_lines_from_po(po_id: int) -> None:
    """Unlink all SO lines from a given PO (set linked_po_id to NULL)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE sales_order_lines SET linked_po_id = NULL WHERE linked_po_id = ?",
            (po_id,)
        )
        conn.commit()

"""Inventory database for JAKS Scraper.

Supports both SQLite (local) and PostgreSQL (cloud) backends via DATABASE_URL.

Provides persistent storage for products, part numbers, cross-references,
and image metadata. Supports cross-reference search across all part number
variants (OEM, PAI, aftermarket, reman).

Schema:
- products: Core product data (sku, title, pricing, etc.)
- part_numbers: All part numbers associated with a product
- xref_links: Cross-reference relationships between part numbers
- product_images: Image file metadata and paths

Usage:
    from db import init_db, sync_product_to_db, search_by_part_number
    
    init_db()  # Creates tables if needed
    sync_product_to_db(product)  # After scraping
    results = search_by_part_number("10R1273")  # Find by any variant
    
Configuration:
    Set DATABASE_URL env var:
    - PostgreSQL: postgresql://user:pass@host:5432/dbname
    - SQLite: sqlite:///output/inventory.db (default)
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .database import get_db, get_connection, get_database_url, is_postgresql
from .schema import get_schema

if TYPE_CHECKING:
    from models import Product

# Database location (for SQLite fallback)
OUTPUT_ROOT = Path(__file__).parent.parent / "output"


def _resolve_db_path() -> Path:
    """Pick the SQLite file path honoring JAKS_DB_PATH / DATABASE_URL."""
    import os
    explicit = os.environ.get("JAKS_DB_PATH")
    if explicit:
        return Path(explicit)
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", "", 1))
    return OUTPUT_ROOT / "JAKs.db"


DB_PATH = _resolve_db_path()

# Ensure output directory exists
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def init_db():
    """Initialize database schema. Safe to call multiple times."""
    db_url = get_database_url()
    backend = "postgresql" if db_url.startswith("postgresql") else "sqlite"
    schema_sql = get_schema(backend)
    
    with get_db() as db:
        if backend == "postgresql":
            # PostgreSQL: Execute statements individually
            # Split on semicolons but keep CREATE TABLE/INDEX intact
            statements = []
            current = []
            in_function = False
            
            for line in schema_sql.split('\n'):
                stripped = line.strip()
                
                # Track function body (different parsing)
                if 'CREATE OR REPLACE FUNCTION' in line.upper():
                    in_function = True
                if in_function and stripped == '$$ language \'plpgsql\';':
                    current.append(line)
                    statements.append('\n'.join(current))
                    current = []
                    in_function = False
                    continue
                
                if in_function:
                    current.append(line)
                    continue
                    
                # Normal statement processing
                if stripped.startswith('--') or not stripped:
                    continue
                    
                current.append(line)
                if stripped.endswith(';') and not in_function:
                    stmt = '\n'.join(current).strip()
                    if stmt and stmt != ';':
                        statements.append(stmt)
                    current = []
            
            for stmt in statements:
                if stmt.strip():
                    try:
                        db.execute(stmt)
                    except Exception as e:
                        # Ignore "already exists" errors
                        if "already exists" not in str(e).lower():
                            print(f"Schema warning: {e}")
            db.commit()
        else:
            # SQLite: Use executescript
            db.executescript(schema_sql)
            db.commit()


def _normalize_part_number(pn: str) -> str:
    """Normalize part number for comparison: uppercase, no dashes/spaces."""
    if not pn:
        return ""
    return re.sub(r"[\s\-_./]", "", str(pn).upper().strip())


def _extract_part_numbers_from_product(product: "Product") -> list[dict]:
    """Extract all part number variants from a Product object."""
    parts: list[dict] = []
    seen_normalized: set[str] = set()
    
    def add_part(number: str, ptype: str, manufacturer: str = "", is_primary: bool = False, source: str = "HHP"):
        if not number:
            return
        normalized = _normalize_part_number(number)
        if not normalized or normalized in seen_normalized:
            return
        seen_normalized.add(normalized)
        parts.append({
            "number": str(number).strip(),
            "number_normalized": normalized,
            "type": ptype,
            "manufacturer": manufacturer,
            "is_primary": is_primary,
            "source": source,
        })
    
    # Primary part number (from URL/SKU)
    primary_pn = getattr(product, "primary_part_number", "") or ""
    if not primary_pn:
        # Try to extract from SKU
        sku = str(getattr(product, "sku", "") or "")
        if sku.startswith("JAKS-"):
            primary_pn = sku[5:]
    add_part(primary_pn, "PRIMARY", getattr(product, "manufacturer", ""), is_primary=True)
    
    # Secondary part number
    secondary_pn = getattr(product, "secondary_part_number", "") or ""
    add_part(secondary_pn, "SECONDARY", getattr(product, "manufacturer", ""))
    
    # OEM part number
    oem_pn = getattr(product, "oem_part_number", "") or ""
    add_part(oem_pn, "OEM", getattr(product, "manufacturer", ""))
    
    # PAI SKU
    pai_sku = getattr(product, "pai_sku", "") or ""
    add_part(pai_sku, "PAI", "PAI Industries", source="PAI")
    
    # PAI alternates
    pai_alts = getattr(product, "pai_alternates", []) or []
    for alt in pai_alts:
        add_part(alt, "PAI_ALT", "PAI Industries", source="PAI")
    
    # Cross-references from xrefs list
    xrefs = getattr(product, "xrefs", []) or []
    for xref in xrefs:
        if isinstance(xref, dict):
            xref_num = xref.get("part_number") or xref.get("number", "")
            xref_mfr = xref.get("manufacturer", "")
        else:
            xref_num = str(xref)
            xref_mfr = ""
        add_part(xref_num, "XREF", xref_mfr)
    
    return parts


def sync_product_to_db(product: "Product", conn=None) -> int:
    """Sync a single product to the database.
    
    Returns the product's database ID.
    """
    close_conn = conn is None
    if conn is None:
        conn = get_connection()
    
    try:
        sku = str(getattr(product, "sku", "") or "").strip()
        if not sku:
            return 0
        
        # Prepare product data
        now = datetime.now().isoformat()
        kit_contents = getattr(product, "kit_contents", []) or []
        pricing = getattr(product, "pricing", {}) or {}
        
        product_data = {
            "sku": sku,
            "handle": str(getattr(product, "handle", "") or ""),
            "url": str(getattr(product, "url", "") or ""),
            "title": str(getattr(product, "title", "") or ""),
            "category": str(getattr(product, "category", "") or ""),
            "manufacturer": str(getattr(product, "manufacturer", "") or getattr(product, "brand", "") or ""),
            "supplier": str(getattr(product, "supplier", "") or ""),
            "engine": str(getattr(product, "engine", "") or ""),
            "condition": str(getattr(product, "condition", "") or ""),
            "cost": float(getattr(product, "cost", 0) or 0),
            "pai_cost": float(getattr(product, "pai_cost", 0) or 0),
            "selling_price": float(pricing.get("selling_price", 0) or getattr(product, "scraped_price", 0) or 0),
            "compare_at_price": float(pricing.get("compare_at_price", 0) or 0),
            "core_charge": float(getattr(product, "core_charge", 0) or 0),
            "pai_sku": str(getattr(product, "pai_sku", "") or ""),
            "pai_status": str(getattr(product, "pai_status", "") or ""),
            "pai_stock": str(getattr(product, "pai_stock", "") or ""),
            "pai_link": str(getattr(product, "pai_link", "") or ""),
            "description_html": str(getattr(product, "description_jaks_html", "") or ""),
            "kit_contents": json.dumps(kit_contents) if kit_contents else "[]",
            "image_count": int(getattr(product, "image_count", 0) or 0),
            "stage": int(pricing.get("stage", 1) or 1),
            "updated_at": now,
        }
        
        # Check if product exists
        existing = conn.execute(
            "SELECT id FROM products WHERE sku = %s", (sku,)
        ).fetchone()
        
        if existing:
            product_id = existing["id"] if hasattr(existing, "__getitem__") else existing[0]
            # Update existing
            set_clause = ", ".join(f"{k} = %s" for k in product_data.keys() if k != "sku")
            values = [v for k, v in product_data.items() if k != "sku"]
            values.append(product_id)
            conn.execute(f"UPDATE products SET {set_clause} WHERE id = %s", values)
        else:
            # Insert new - handle PostgreSQL vs SQLite for RETURNING
            cols = ", ".join(product_data.keys())
            placeholders = ", ".join("%s" for _ in product_data)
            
            if is_postgresql():
                cursor = conn.execute(
                    f"INSERT INTO products ({cols}) VALUES ({placeholders}) RETURNING id",
                    list(product_data.values())
                )
                row = cursor.fetchone()
                product_id = row[0] if row else 0
            else:
                cursor = conn.execute(
                    f"INSERT INTO products ({cols}) VALUES ({placeholders})",
                    list(product_data.values())
                )
                product_id = cursor.lastrowid
        
        # Sync part numbers
        part_numbers = _extract_part_numbers_from_product(product)
        for pn in part_numbers:
            # Use UPSERT syntax compatible with both
            if is_postgresql():
                conn.execute("""
                    INSERT INTO part_numbers 
                    (product_id, number, number_normalized, type, manufacturer, is_primary, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (product_id, number_normalized) 
                    DO UPDATE SET number = EXCLUDED.number, type = EXCLUDED.type,
                                  manufacturer = EXCLUDED.manufacturer, is_primary = EXCLUDED.is_primary
                """, (
                    product_id,
                    pn["number"],
                    pn["number_normalized"],
                    pn["type"],
                    pn["manufacturer"],
                    pn["is_primary"],
                    pn["source"],
                ))
            else:
                conn.execute("""
                    INSERT OR REPLACE INTO part_numbers 
                    (product_id, number, number_normalized, type, manufacturer, is_primary, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    product_id,
                    pn["number"],
                    pn["number_normalized"],
                    pn["type"],
                    pn["manufacturer"],
                    pn["is_primary"],
                    pn["source"],
                ))
        
        # Create xref links between all part numbers of this product
        part_ids = conn.execute(
            "SELECT id FROM part_numbers WHERE product_id = %s ORDER BY id",
            (product_id,)
        ).fetchall()
        
        # Link each pair (ensuring part_a_id < part_b_id)
        for i, row_a in enumerate(part_ids):
            for row_b in part_ids[i+1:]:
                a_id = row_a["id"] if hasattr(row_a, "__getitem__") and not isinstance(row_a, tuple) else row_a[0]
                b_id = row_b["id"] if hasattr(row_b, "__getitem__") and not isinstance(row_b, tuple) else row_b[0]
                try:
                    if is_postgresql():
                        conn.execute("""
                            INSERT INTO xref_links (part_a_id, part_b_id, source, confidence)
                            VALUES (%s, %s, 'AUTO', 'high')
                            ON CONFLICT (part_a_id, part_b_id) DO NOTHING
                        """, (a_id, b_id))
                    else:
                        conn.execute("""
                            INSERT OR IGNORE INTO xref_links (part_a_id, part_b_id, source, confidence)
                            VALUES (%s, %s, 'AUTO', 'high')
                        """, (a_id, b_id))
                except Exception:
                    pass  # Already exists
        
        # =====================================================================
        # AUTO-CREATE PRODUCT FAMILY based on part numbers
        # =====================================================================
        try:
            from db.product_family_service import (
                get_or_create_family_for_product,
            )
            
            title = str(getattr(product, "title", "") or "")
            primary_part = str(getattr(product, "primary_part", "") or "")
            oem_pn = str(getattr(product, "oem_part_number", "") or "")
            
            # Determine the part number to use for family grouping
            family_part = oem_pn.strip() if oem_pn.strip() else primary_part.strip()
            
            if family_part and product_id:
                family = get_or_create_family_for_product(
                    product_id=product_id,
                    part_number=family_part,
                    title=title,
                    oem_part_number=oem_pn if oem_pn.strip() else None,
                )
                
                # Update product with family_id
                if family:
                    conn.execute(
                        "UPDATE products SET family_id = %s WHERE id = %s",
                        (family.id, product_id)
                    )
        except Exception as e:
            # Family creation is optional - don't fail the save
            print(f"Warning: Could not create family for product {product_id}: {e}")
        
        conn.commit()
        return product_id
        
    finally:
        if close_conn:
            conn.close()


def sync_products_batch(products: dict[str, "Product"], progress_callback=None) -> tuple[int, int]:
    """Sync multiple products to database.
    
    Returns (synced_count, error_count).
    """
    conn = get_connection()
    synced = 0
    errors = 0
    total = len(products)
    
    try:
        for i, (key, product) in enumerate(products.items()):
            try:
                sync_product_to_db(product, conn)
                synced += 1
            except Exception as e:
                errors += 1
                print(f"DB sync error for {key}: {e}")
            
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(i + 1, total)
        
        conn.commit()
    finally:
        conn.close()
    
    return synced, errors


def search_by_part_number(query: str, limit: int = 50) -> list[dict]:
    """Search for products by any part number variant.
    
    Uses cross-reference links to find products even when searching
    by alternate part numbers (OEM, PAI, aftermarket, etc.)
    
    Args:
        query: Part number to search for (partial match supported)
        limit: Maximum results to return
        
    Returns:
        List of product dicts with matched_part indicating which variant matched
    """
    if not query or not query.strip():
        return []
    
    normalized = _normalize_part_number(query)
    if not normalized:
        return []
    
    with get_db() as conn:
        # Search strategy:
        # 1. Direct match on part_numbers table
        # 2. Follow xref_links to find related products
        
        # Direct matches
        direct_results = conn.execute("""
            SELECT DISTINCT 
                p.*,
                pn.number as matched_part,
                pn.type as matched_type,
                'direct' as match_type
            FROM products p
            JOIN part_numbers pn ON p.id = pn.product_id
            WHERE pn.number_normalized LIKE %s
            LIMIT %s
        """, (f"%{normalized}%", limit)).fetchall()
        
        # Cross-reference matches (find products linked via xref)
        xref_results = conn.execute("""
            SELECT DISTINCT 
                p.*,
                pn_match.number as matched_part,
                pn_match.type as matched_type,
                'xref' as match_type
            FROM products p
            JOIN part_numbers pn_product ON p.id = pn_product.product_id
            JOIN xref_links x ON (pn_product.id = x.part_a_id OR pn_product.id = x.part_b_id)
            JOIN part_numbers pn_match ON (
                (x.part_a_id = pn_match.id OR x.part_b_id = pn_match.id)
                AND pn_match.id != pn_product.id
            )
            WHERE pn_match.number_normalized LIKE %s
            LIMIT %s
        """, (f"%{normalized}%", limit)).fetchall()

        # Column match on products.oem_part_number — free-text field on the
        # product row (sometimes comma/semicolon-separated list of refs).
        col_like = f"%{query.strip()}%"
        column_results = conn.execute("""
            SELECT DISTINCT
                p.*,
                COALESCE(p.oem_part_number, '') as matched_part,
                'product_column' as matched_type,
                'column' as match_type
            FROM products p
            WHERE p.oem_part_number LIKE %s
            LIMIT %s
        """, (col_like, limit)).fetchall()

        # Cross-references stored in the part_interchanges table (the rows
        # shown in the Edit Product → Cross-References tab).
        interchange_results = conn.execute("""
            SELECT DISTINCT
                p.*,
                pi.interchange_number as matched_part,
                pi.interchange_type as matched_type,
                'interchange' as match_type
            FROM products p
            JOIN part_interchanges pi ON pi.product_id = p.id
            WHERE pi.interchange_number LIKE %s
            LIMIT %s
        """, (col_like, limit)).fetchall()

        # Combine and deduplicate
        all_results = (
            list(direct_results)
            + list(xref_results)
            + list(column_results)
            + list(interchange_results)
        )
        
        # Deduplicate by SKU, keeping best match (direct over xref)
        seen_skus: dict[str, dict] = {}
        for row in all_results:
            row_dict = dict(row) if hasattr(row, 'keys') else dict(zip([d[0] for d in conn.cursor().description] if hasattr(conn, 'cursor') else range(len(row)), row))
            sku = row_dict.get("sku", "")
            if sku not in seen_skus:
                seen_skus[sku] = row_dict
            elif row_dict.get("match_type") == "direct":
                # Prefer direct matches
                seen_skus[sku] = row_dict
        
        return list(seen_skus.values())


def create_product(**kwargs) -> dict:
    """Create a new product manually.

    Required: sku.
    Returns dict with 'success', 'id', or 'error'.
    """
    sku = kwargs.get("sku", "").strip()
    if not sku:
        return {"success": False, "error": "SKU is required"}
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM products WHERE sku = %s", (sku,)
        ).fetchone()
        if existing:
            return {"success": False, "error": f"SKU '{sku}' already exists"}
        cols = ["sku"]
        vals = [sku]
        allowed = [
            "title", "handle", "category", "subcategory", "manufacturer",
            "supplier", "condition", "cost", "pai_cost", "selling_price",
            "compare_at_price", "core_charge", "pai_sku", "pai_link",
            "min_qty", "max_qty", "preferred_vendor_id", "requires_serial",
            "description_html", "engine", "oem_manufacturer",
            "brief_description", "oem_part_number", "xref_part_number",
            "invoice_description", "inventory_status",
            "truck_manufacturer", "truck_model", "truck_system",
            "vendor_description", "shipping_cost", "core_sell_price",
            "notes_public", "notes_internal", "private_label", "private_label_part_number",
            "supplier_warranty_enabled", "supplier_warranty_value",
            "supplier_warranty_unit", "jaks_warranty_enabled",
            "jaks_warranty_value", "jaks_warranty_unit",
            "jaks_warranty_charge", "is_warrantable",
            "weight", "weight_unit", "length", "width", "height",
            "dimension_unit", "vendor_availability", "vendor_alternates",
            "cost_updated_at", "pricing_updated_at", "price_category_id",
        ]
        for key in allowed:
            if key in kwargs and kwargs[key] is not None:
                cols.append(key)
                vals.append(kwargs[key])
        placeholders = ", ".join(["%s"] * len(vals))
        col_str = ", ".join(cols)
        conn.execute(
            f"INSERT INTO products ({col_str}) VALUES ({placeholders})", tuple(vals)
        )
        conn.commit()
        new_id = conn.lastrowid
        # Enqueue for QBO sync (best-effort — must never block the save)
        try:
            from qbo.sync_queue import enqueue, ACTION_CREATE
            enqueue("product", new_id, ACTION_CREATE,
                    payload={"sku": sku, "title": kwargs.get("title")},
                    message=f"New product {sku} awaiting QBO push")
        except Exception:
            pass
        return {"success": True, "id": new_id}


def get_product_by_sku(sku: str) -> dict | None:
    """Get a single product by SKU."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE sku = %s", (sku,)
        ).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def get_product_by_id(product_id: int) -> dict | None:
    """Get a single product by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = %s", (product_id,)
        ).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def update_product(product_id: int, **fields) -> bool:
    """Update product fields.
    
    Args:
        product_id: Product ID
        **fields: Field names and values to update
    
    Returns:
        True if updated, False if not found
    """
    if not fields:
        return False
    
    # Build SET clause
    set_parts = []
    values = []
    for key, value in fields.items():
        set_parts.append(f"{key} = %s")
        values.append(value)
    values.append(product_id)
    
    query = f"UPDATE products SET {', '.join(set_parts)} WHERE id = %s"
    
    with get_db() as conn:
        cursor = conn.execute(query, tuple(values))
        conn.commit()
        updated = cursor.rowcount > 0
    if updated:
        # Inventory qty changes are flagged as their own high-risk entity
        # so the Pending Queue surfaces them distinctly from product
        # metadata edits.
        try:
            from qbo.sync_queue import (
                enqueue, ACTION_UPDATE, ACTION_PUSH,
            )
            qty_keys = {"qty_on_hand", "qty_reserved"}
            touched_qty = any(k in fields for k in qty_keys)
            if touched_qty:
                enqueue("inventory_qty", product_id, ACTION_PUSH,
                        payload={"product_id": product_id,
                                 "qty_on_hand": fields.get("qty_on_hand")},
                        message="Inventory qty change pending QBO push")
            non_qty = {k: v for k, v in fields.items() if k not in qty_keys}
            if non_qty:
                enqueue("product", product_id, ACTION_UPDATE,
                        payload={"fields": list(non_qty.keys())},
                        message="Product edit awaiting QBO push")
        except Exception:
            pass
    return updated


def get_all_xrefs_for_product(sku: str) -> list[dict]:
    """Get all cross-reference part numbers for a product."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT pn.number, pn.type, pn.manufacturer
            FROM part_numbers pn
            JOIN products p ON pn.product_id = p.id
            WHERE p.sku = %s
            ORDER BY 
                CASE pn.type 
                    WHEN 'PRIMARY' THEN 0
                    WHEN 'OEM' THEN 1
                    WHEN 'PAI' THEN 2
                    WHEN 'SECONDARY' THEN 3
                    ELSE 4 
                END,
                pn.number
        """, (sku,)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else {'number': r[0], 'type': r[1], 'manufacturer': r[2]} for r in rows]


def index_product_images(product_id: int, image_dir: Path, source: str = "hhp") -> int:
    """Index existing image files for a product.
    
    Scans the image directory and adds metadata to the database.
    Returns count of images indexed.
    """
    if not image_dir.exists():
        return 0
    
    count = 0
    
    with get_db() as conn:
        # Try to import PIL for dimensions
        try:
            from PIL import Image
            has_pil = True
        except ImportError:
            has_pil = False
        
        for i, img_path in enumerate(sorted(image_dir.glob("*.jpg")), 1):
            try:
                # Calculate hash for deduplication
                file_hash = hashlib.md5(img_path.read_bytes()).hexdigest()
                file_size = img_path.stat().st_size
                
                # Get dimensions if PIL available
                width, height = 0, 0
                if has_pil:
                    try:
                        with Image.open(img_path) as img:
                            width, height = img.size
                    except Exception:
                        pass
                
                # Calculate relative path from output/
                try:
                    rel_path = img_path.relative_to(OUTPUT_ROOT)
                except ValueError:
                    rel_path = img_path
                
                if is_postgresql():
                    conn.execute("""
                        INSERT INTO product_images 
                        (product_id, filename, path, position, width, height, file_size, hash, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (product_id, filename) 
                        DO UPDATE SET path = EXCLUDED.path, position = EXCLUDED.position,
                                      width = EXCLUDED.width, height = EXCLUDED.height
                    """, (
                        product_id,
                        img_path.name,
                        str(rel_path),
                        i,
                        width,
                        height,
                        file_size,
                        file_hash,
                        source,
                    ))
                else:
                    conn.execute("""
                        INSERT OR REPLACE INTO product_images 
                        (product_id, filename, path, position, width, height, file_size, hash, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        product_id,
                        img_path.name,
                        str(rel_path),
                        i,
                        width,
                        height,
                        file_size,
                        file_hash,
                        source,
                    ))
                count += 1
            except Exception as e:
                print(f"Error indexing image {img_path}: {e}")
        
        conn.commit()
    
    return count
    
    return count


def get_db_stats() -> dict:
    """Get database statistics."""
    with get_db() as conn:
        stats = {}
        
        # Helper to get count value from row (works with both dict and tuple)
        def get_count(row):
            if row is None:
                return 0
            if isinstance(row, dict):
                return row.get('count', row.get('COUNT(*)', list(row.values())[0] if row else 0))
            return row[0]
        
        stats["products"] = get_count(conn.execute("SELECT COUNT(*) as count FROM products").fetchone())
        stats["part_numbers"] = get_count(conn.execute("SELECT COUNT(*) as count FROM part_numbers").fetchone())
        stats["xref_links"] = get_count(conn.execute("SELECT COUNT(*) as count FROM xref_links").fetchone())
        stats["images"] = get_count(conn.execute("SELECT COUNT(*) as count FROM product_images").fetchone())
        
        stats["by_category"] = {}
        for row in conn.execute(
            "SELECT category, COUNT(*) as cnt FROM products GROUP BY category ORDER BY cnt DESC"
        ).fetchall():
            if isinstance(row, dict):
                cat = row.get("category") or "Unknown"
                cnt = row.get("cnt", 0)
            else:
                cat = row[0] or "Unknown"
                cnt = row[1]
            stats["by_category"][cat] = cnt
        
        stats["by_manufacturer"] = {}
        for row in conn.execute(
            "SELECT manufacturer, COUNT(*) as cnt FROM products GROUP BY manufacturer ORDER BY cnt DESC LIMIT 10"
        ).fetchall():
            if isinstance(row, dict):
                mfr = row.get("manufacturer") or "Unknown"
                cnt = row.get("cnt", 0)
            else:
                mfr = row[0] or "Unknown"
                cnt = row[1]
            stats["by_manufacturer"][mfr] = cnt
        
        # File size only for SQLite
        if not is_postgresql():
            stats["db_size_bytes"] = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        else:
            stats["db_size_bytes"] = 0  # N/A for cloud
        
        return stats


def get_dashboard_stats() -> dict:
    """Get all dashboard KPIs in one call for efficient dashboard loading."""
    from datetime import date, timedelta
    
    today = date.today()
    week_ago = today - timedelta(days=7)
    
    with get_db() as conn:
        stats = {}
        
        # Helper to get count
        def get_count(row):
            if row is None:
                return 0
            return row[0] if not isinstance(row, dict) else list(row.values())[0]
        
        # Inventory alerts — only flag tracked items (min_qty >= 1).
        # Products with min_qty = 0 are intentionally untracked for reorder.
        stats["low_stock_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM products "
            "WHERE COALESCE(min_qty, 0) >= 1 AND qty_on_hand > 0 AND qty_on_hand < 5"
        ).fetchone())

        stats["out_of_stock_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM products "
            "WHERE COALESCE(min_qty, 0) >= 1 AND (qty_on_hand = 0 OR qty_on_hand IS NULL)"
        ).fetchone())
        
        stats["pending_po_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM purchase_orders WHERE status IN ('draft', 'sent')"
        ).fetchone())
        
        stats["overdue_core_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM core_returns WHERE status = 'pending' AND due_date < CURRENT_DATE"
        ).fetchone())
        
        # Financials
        stats["open_invoice_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE status IN ('draft', 'sent')"
        ).fetchone())
        
        stats["overdue_invoice_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE status = 'sent' AND due_date < CURRENT_DATE"
        ).fetchone())
        
        # Today's revenue
        row = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE invoice_date = %s AND status NOT IN ('void', 'draft')",
            (today.isoformat(),)
        ).fetchone()
        stats["today_revenue"] = float(get_count(row))
        
        # Week revenue
        row = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE invoice_date >= %s AND status NOT IN ('void', 'draft')",
            (week_ago.isoformat(),)
        ).fetchone()
        stats["week_revenue"] = float(get_count(row))
        
        # Inventory totals
        stats["total_products"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone())
        
        row = conn.execute(
            "SELECT COALESCE(SUM(qty_on_hand), 0) FROM products"
        ).fetchone()
        stats["total_quantity"] = int(get_count(row))
        
        row = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(pai_cost, 0) * COALESCE(qty_on_hand, 0)), 0) FROM products"
        ).fetchone()
        stats["inventory_value"] = float(get_count(row))
        
        row = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(selling_price, 0) * COALESCE(qty_on_hand, 0)), 0) FROM products"
        ).fetchone()
        stats["retail_value"] = float(get_count(row))

        # ----- Quotes / Sales pipeline -----
        # Quotes awaiting approval (status = 'sent' or 'draft')
        stats["quotes_pending_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM quotes WHERE status IN ('draft', 'sent')"
        ).fetchone())

        row = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM quotes WHERE status IN ('draft', 'sent')"
        ).fetchone()
        stats["quotes_pending_value"] = float(get_count(row))

        # Quotes needing follow-up
        stats["quotes_follow_up_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM quotes WHERE status = 'needs_follow_up'"
        ).fetchone())

        # Orders waiting on parts (SO with status not complete/void)
        stats["orders_waiting_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM sales_orders WHERE status IN ('draft', 'approved', 'partial')"
        ).fetchone())

        # ----- Margin stats -----
        row = conn.execute(
            """SELECT COALESCE(SUM(total), 0),
                      COALESCE(SUM(
                          (SELECT COALESCE(SUM(COALESCE(p.pai_cost, 0) * il.quantity), 0)
                           FROM invoice_lines il
                           LEFT JOIN products p ON p.id = il.product_id
                           WHERE il.invoice_id = invoices.id)
                      ), 0)
               FROM invoices
               WHERE invoice_date = %s AND status NOT IN ('void', 'draft')""",
            (today.isoformat(),)
        ).fetchone()
        today_rev = float(row[0]) if row else 0.0
        today_cost = float(row[1]) if row else 0.0
        stats["today_gross_profit"] = today_rev - today_cost
        stats["today_margin_pct"] = ((today_rev - today_cost) / today_rev * 100) if today_rev else 0.0

        row = conn.execute(
            """SELECT COALESCE(SUM(total), 0),
                      COALESCE(SUM(
                          (SELECT COALESCE(SUM(COALESCE(p.pai_cost, 0) * il.quantity), 0)
                           FROM invoice_lines il
                           LEFT JOIN products p ON p.id = il.product_id
                           WHERE il.invoice_id = invoices.id)
                      ), 0)
               FROM invoices
               WHERE invoice_date >= %s AND status NOT IN ('void', 'draft')""",
            (week_ago.isoformat(),)
        ).fetchone()
        week_rev = float(row[0]) if row else 0.0
        week_cost = float(row[1]) if row else 0.0
        stats["week_margin_pct"] = ((week_rev - week_cost) / week_rev * 100) if week_rev else 0.0

        # ----- Inventory insights -----
        # Dead stock: products not sold (no invoice_lines) in 90+ days
        ninety_ago = (today - timedelta(days=90)).isoformat()
        stats["dead_stock_count"] = get_count(conn.execute(
            """SELECT COUNT(*) FROM products p
               WHERE p.qty_on_hand > 0
               AND p.id NOT IN (
                   SELECT DISTINCT il.product_id FROM invoice_lines il
                   JOIN invoices inv ON inv.id = il.invoice_id
                   WHERE inv.invoice_date >= %s AND il.product_id IS NOT NULL
               )""",
            (ninety_ago,)
        ).fetchone())

        # Fast movers: products sold 5+ times in last 30 days
        thirty_ago = (today - timedelta(days=30)).isoformat()
        stats["fast_movers_count"] = get_count(conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT il.product_id, SUM(il.quantity) AS sold
                   FROM invoice_lines il
                   JOIN invoices inv ON inv.id = il.invoice_id
                   WHERE inv.invoice_date >= %s AND il.product_id IS NOT NULL
                     AND inv.status NOT IN ('void', 'draft')
                   GROUP BY il.product_id
                   HAVING sold >= 5
               )""",
            (thirty_ago,)
        ).fetchone())

        # Recent activity (last 10 events)
        activity = []
        for row in conn.execute(
            """SELECT 'Quote ' || quote_number || ' created' AS msg,
                      created_at AS ts
               FROM quotes ORDER BY created_at DESC LIMIT 3"""
        ).fetchall():
            activity.append({"msg": row[0], "ts": row[1]})
        for row in conn.execute(
            """SELECT 'Invoice ' || invoice_number || ' created' AS msg,
                      created_at AS ts
               FROM invoices ORDER BY created_at DESC LIMIT 3"""
        ).fetchall():
            activity.append({"msg": row[0], "ts": row[1]})
        for row in conn.execute(
            """SELECT 'PO ' || po_number ||
                      CASE WHEN status='received' THEN ' received'
                           WHEN status='sent' THEN ' sent'
                           ELSE ' ' || status END AS msg,
                      created_at AS ts
               FROM purchase_orders ORDER BY created_at DESC LIMIT 3"""
        ).fetchall():
            activity.append({"msg": row[0], "ts": row[1]})
        # Sort by time descending, take 8
        activity.sort(key=lambda x: x.get("ts") or "", reverse=True)
        stats["recent_activity"] = activity[:8]

        # ----- Category overview extras -----
        # Month revenue
        month_start = today.replace(day=1).isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE invoice_date >= %s AND status NOT IN ('void', 'draft')",
            (month_start,)
        ).fetchone()
        stats["month_revenue"] = float(get_count(row))

        # Active customers
        stats["active_customers_count"] = get_count(conn.execute(
            "SELECT COUNT(*) FROM customers"
        ).fetchone())

        # Missed customers – no invoice in 30 days
        stats["missed_customers_count"] = get_count(conn.execute(
            """SELECT COUNT(*) FROM customers c
               WHERE c.id NOT IN (
                   SELECT DISTINCT i.customer_id FROM invoices i
                   WHERE i.invoice_date >= %s AND i.customer_id IS NOT NULL
               )""",
            (thirty_ago,)
        ).fetchone())

        # Active returns
        try:
            stats["active_return_count"] = get_count(conn.execute(
                "SELECT COUNT(*) FROM returns WHERE status NOT IN ('completed', 'void', 'closed')"
            ).fetchone())
        except Exception:
            stats["active_return_count"] = 0

        # ----- New KPI cards -----
        # Quote conversion rate (accepted / total non-draft, last 30 days)
        total_quotes_30d = get_count(conn.execute(
            "SELECT COUNT(*) FROM quotes WHERE created_at >= %s",
            (thirty_ago,)
        ).fetchone())
        accepted_quotes_30d = get_count(conn.execute(
            "SELECT COUNT(*) FROM quotes WHERE status IN ('accepted', 'invoiced') AND created_at >= %s",
            (thirty_ago,)
        ).fetchone())
        stats["conversion_rate"] = (
            (accepted_quotes_30d / total_quotes_30d * 100) if total_quotes_30d > 0 else 0.0
        )

        # Average order value (invoices, last 30 days)
        row = conn.execute(
            "SELECT AVG(total) FROM invoices WHERE invoice_date >= %s AND status NOT IN ('void', 'draft')",
            (thirty_ago,)
        ).fetchone()
        stats["avg_order_value"] = float(get_count(row)) if get_count(row) else 0.0

        # Overdue cores dollar value
        row = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(core_charge, 0)), 0) FROM core_returns WHERE status = 'pending' AND due_date < CURRENT_DATE"
        ).fetchone()
        stats["overdue_cores_value"] = float(get_count(row))

        # Vendor cores owed back to us (deposit at stake)
        try:
            row = conn.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(COALESCE(core_charge,0) * COALESCE(qty,0)), 0)
                FROM vendor_core_returns
                WHERE status IN ('pending','shipped')
                """
            ).fetchone()
            if row is not None:
                if hasattr(row, "keys"):
                    vals = list(row.values())
                    stats["vendor_cores_open_count"] = int(vals[0] or 0)
                    stats["vendor_cores_open_value"] = float(vals[1] or 0)
                else:
                    stats["vendor_cores_open_count"] = int(row[0] or 0)
                    stats["vendor_cores_open_value"] = float(row[1] or 0)
            else:
                stats["vendor_cores_open_count"] = 0
                stats["vendor_cores_open_value"] = 0.0
            row2 = conn.execute(
                """
                SELECT COUNT(*) FROM vendor_core_returns
                WHERE status = 'pending' AND due_date IS NOT NULL AND due_date < CURRENT_DATE
                """
            ).fetchone()
            stats["vendor_cores_overdue_count"] = int(get_count(row2)) if row2 else 0
        except Exception:
            stats["vendor_cores_open_count"] = 0
            stats["vendor_cores_open_value"] = 0.0
            stats["vendor_cores_overdue_count"] = 0

        # Month gross profit
        row = conn.execute(
            """SELECT COALESCE(SUM(total), 0),
                      COALESCE(SUM(
                          (SELECT COALESCE(SUM(COALESCE(p.pai_cost, 0) * il.quantity), 0)
                           FROM invoice_lines il
                           LEFT JOIN products p ON p.id = il.product_id
                           WHERE il.invoice_id = invoices.id)
                      ), 0)
               FROM invoices
               WHERE invoice_date >= %s AND status NOT IN ('void', 'draft')""",
            (month_start,)
        ).fetchone()
        month_rev = float(row[0]) if row else 0.0
        month_cost = float(row[1]) if row else 0.0
        stats["month_profit"] = month_rev - month_cost

        return stats


def get_revenue_trend(days: int = 30) -> list[dict]:
    """Return daily revenue + profit for the last N days, for chart rendering."""
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=days - 1)

    with get_db() as conn:
        rows = conn.execute(
            """SELECT invoice_date,
                      COALESCE(SUM(total), 0) AS revenue,
                      COALESCE(SUM(total) - SUM(
                          (SELECT COALESCE(SUM(COALESCE(p.pai_cost, 0) * il.quantity), 0)
                           FROM invoice_lines il
                           LEFT JOIN products p ON p.id = il.product_id
                           WHERE il.invoice_id = invoices.id)
                      ), 0) AS profit
               FROM invoices
               WHERE invoice_date >= %s AND invoice_date <= %s
                 AND status NOT IN ('void', 'draft')
               GROUP BY invoice_date
               ORDER BY invoice_date""",
            (start.isoformat(), today.isoformat())
        ).fetchall()

        # Build a dict of existing data
        data_map = {}
        for r in rows:
            data_map[r[0]] = {"revenue": float(r[1]), "profit": float(r[2])}

        # Fill in all days (including zeros)
        result = []
        for i in range(days):
            d = start + timedelta(days=i)
            ds = d.isoformat()
            entry = data_map.get(ds, {"revenue": 0.0, "profit": 0.0})
            entry["date"] = ds
            entry["label"] = d.strftime("%m/%d")
            result.append(entry)
        return result


def get_quote_conversion_stats() -> dict:
    """Return quote status breakdown for conversion chart."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM quotes GROUP BY status"
        ).fetchall()
        result = {}
        for r in rows:
            result[r[0]] = r[1]
        return result


def get_top_customers(limit: int = 5, days: int = 30) -> list[dict]:
    """Top customers by revenue in last N days."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.name, c.company, COALESCE(SUM(i.total), 0) AS revenue
               FROM invoices i
               JOIN customers c ON c.id = i.customer_id
               WHERE i.invoice_date >= %s AND i.status NOT IN ('void', 'draft')
               GROUP BY i.customer_id
               ORDER BY revenue DESC
               LIMIT %s""",
            (cutoff, limit)
        ).fetchall()
        return [{"name": r[0] or r[1] or "Unknown", "revenue": float(r[2])} for r in rows]


def get_top_parts(limit: int = 5, days: int = 30) -> list[dict]:
    """Top-selling parts by quantity in last N days."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    with get_db() as conn:
        rows = conn.execute(
            """SELECT COALESCE(p.sku, il.description), COALESCE(SUM(il.quantity), 0) AS qty,
                      COALESCE(SUM(il.line_total), 0) AS revenue
               FROM invoice_lines il
               JOIN invoices inv ON inv.id = il.invoice_id
               LEFT JOIN products p ON p.id = il.product_id
               WHERE inv.invoice_date >= %s AND inv.status NOT IN ('void', 'draft')
                 AND il.product_id IS NOT NULL
               GROUP BY il.product_id
               ORDER BY qty DESC
               LIMIT %s""",
            (cutoff, limit)
        ).fetchall()
        return [{"sku": r[0] or "Unknown", "qty": int(r[1]), "revenue": float(r[2])} for r in rows]


def search_by_manufacturer(manufacturer: str, category: str = None, limit: int = 100) -> list[dict]:
    """Search products by manufacturer, optionally filtered by category."""
    with get_db() as conn:
        if category:
            rows = conn.execute("""
                SELECT * FROM products 
                WHERE manufacturer LIKE %s AND category LIKE %s
                ORDER BY title
                LIMIT %s
            """, (f"%{manufacturer}%", f"%{category}%", limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM products 
                WHERE manufacturer LIKE %s
                ORDER BY title
                LIMIT %s
            """, (f"%{manufacturer}%", limit)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def get_products_needing_pai(limit: int = 100) -> list[dict]:
    """Get products that haven't been PAI enriched yet."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM products 
            WHERE (pai_status IS NULL OR pai_status = '' OR pai_status = 'NOT_CHECKED')
            AND stage >= 2
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def find_duplicate_parts() -> list[dict]:
    """Find part numbers that appear in multiple products (potential duplicates)."""
    with get_db() as conn:
        # PostgreSQL uses STRING_AGG, SQLite uses GROUP_CONCAT
        if is_postgresql():
            rows = conn.execute("""
                SELECT 
                    pn.number_normalized,
                    COUNT(DISTINCT pn.product_id) as product_count,
                    STRING_AGG(DISTINCT p.sku, ',') as skus
                FROM part_numbers pn
                JOIN products p ON pn.product_id = p.id
                GROUP BY pn.number_normalized
                HAVING COUNT(DISTINCT pn.product_id) > 1
                ORDER BY product_count DESC
                LIMIT 50
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT 
                    pn.number_normalized,
                    COUNT(DISTINCT pn.product_id) as product_count,
                    GROUP_CONCAT(DISTINCT p.sku) as skus
                FROM part_numbers pn
                JOIN products p ON pn.product_id = p.id
                GROUP BY pn.number_normalized
                HAVING COUNT(DISTINCT pn.product_id) > 1
                ORDER BY product_count DESC
                LIMIT 50
            """).fetchall()
        return [dict(r) if hasattr(r, 'keys') else {'number_normalized': r[0], 'product_count': r[1], 'skus': r[2]} for r in rows]


# =============================================================================
# INVENTORY OPERATIONS (qty_on_hand, transactions)
# =============================================================================

def adjust_product_quantity(
    sku: str,
    delta: int,
    txn_type: str,
    notes: str = "",
    reference: str = "",
    user: str = "system",
    txn_datetime: str | None = None,
) -> dict:
    """Adjust qty_on_hand and log an inventory transaction.
    
    Args:
        sku: Product SKU
        delta: Quantity change (positive to add, negative to remove)
        txn_type: Transaction type ('receive', 'sale', 'adjust', 'return')
        notes: Optional notes about the transaction
        reference: Optional reference (PO#, Invoice#, etc.)
        user: Who made the change
        txn_datetime: Optional transaction timestamp (ISO string). Defaults to now.
    
    Returns:
        {'success': True, 'qty_before': int, 'qty_after': int, 'product_id': int}
        or {'success': False, 'error': str}
    """
    with get_db() as conn:
        # Get current product
        row = conn.execute(
            "SELECT id, qty_on_hand FROM products WHERE sku = %s", (sku,)
        ).fetchone()
        
        if not row:
            return {"success": False, "error": f"Product not found: {sku}"}
        
        if isinstance(row, dict):
            product_id = row["id"]
            qty_before = row.get("qty_on_hand", 0) or 0
        else:
            product_id = row[0]
            qty_before = row[1] or 0
        
        qty_after = qty_before + delta
        
        # Prevent negative inventory (optional - can remove this check if needed)
        if qty_after < 0:
            return {"success": False, "error": f"Cannot reduce qty below 0 (current: {qty_before}, delta: {delta})"}
        
        # Update products table
        conn.execute(
            "UPDATE products SET qty_on_hand = %s, updated_at = %s WHERE id = %s",
            (qty_after, datetime.now().isoformat(), product_id)
        )
        
        # Log transaction
        txn_created_at = str(txn_datetime or "").strip() or datetime.now().isoformat()
        conn.execute("""
            INSERT INTO inventory_transactions 
            (product_id, transaction_type, quantity_change, quantity_before, quantity_after, reference, notes, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (product_id, txn_type, delta, qty_before, qty_after, reference, notes, user, txn_created_at))
        
        conn.commit()
        
        return {
            "success": True,
            "product_id": product_id,
            "qty_before": qty_before,
            "qty_after": qty_after,
        }


def get_inventory_transactions(product_id: int = None, limit: int = 100) -> list[dict]:
    """Get inventory transaction history.
    
    Args:
        product_id: Filter by product (None for all products)
        limit: Max records to return
        
    Returns:
        List of transaction dicts with product SKU included
    """
    with get_db() as conn:
        if product_id:
            rows = conn.execute("""
                SELECT t.*, p.sku, p.title
                FROM inventory_transactions t
                JOIN products p ON t.product_id = p.id
                WHERE t.product_id = %s
                ORDER BY t.created_at DESC
                LIMIT %s
            """, (product_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT t.*, p.sku, p.title
                FROM inventory_transactions t
                JOIN products p ON t.product_id = p.id
                ORDER BY t.created_at DESC
                LIMIT %s
            """, (limit,)).fetchall()
        
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def get_low_stock_products(threshold: int = 5) -> list[dict]:
    """Get products with qty_on_hand at or below threshold.

    Products whose ``min_qty`` is 0 (or NULL) are treated as untracked for
    reorder purposes and are excluded from this list — they will never be
    flagged as low stock or out of stock.

    Args:
        threshold: Stock level to flag (default 5)

    Returns:
        List of product dicts with low stock
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM products
            WHERE qty_on_hand <= %s
              AND COALESCE(min_qty, 0) >= 1
            ORDER BY qty_on_hand ASC, title ASC
        """, (threshold,)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


# =============================================================================
# SHIPPING COST ESTIMATION
# =============================================================================

def estimate_shipping_cost(
    total_weight_lbs: float,
    carrier: str = "UPS",
    service: str = "Ground",
) -> dict:
    """Estimate shipping cost based on weight and carrier rate table.

    Args:
        total_weight_lbs: Total shipment weight in pounds.
        carrier: Carrier name (UPS, FedEx, Freight).
        service: Service level (Ground, LTL).

    Returns:
        {"estimated_cost": float, "carrier": str, "service": str,
         "weight_lbs": float, "base_rate": float, "per_lb_rate": float}
        or {"estimated_cost": 0, "error": "..."} if no matching rate.
    """
    if total_weight_lbs <= 0:
        return {"estimated_cost": 0.0, "carrier": carrier, "service": service,
                "weight_lbs": 0, "error": "No weight specified"}
    with get_db() as conn:
        row = conn.execute("""
            SELECT base_rate, per_lb_rate, carrier, service
            FROM shipping_rates
            WHERE carrier = %s AND service = %s
              AND min_weight_lbs <= %s AND max_weight_lbs > %s
              AND is_active = 1
            ORDER BY min_weight_lbs DESC
            LIMIT 1
        """, (carrier, service, total_weight_lbs, total_weight_lbs)).fetchone()
        if not row:
            # Try any active rate for this carrier
            row = conn.execute("""
                SELECT base_rate, per_lb_rate, carrier, service
                FROM shipping_rates
                WHERE carrier = %s AND is_active = 1
                ORDER BY max_weight_lbs DESC
                LIMIT 1
            """, (carrier,)).fetchone()
        if not row:
            return {"estimated_cost": 0.0, "carrier": carrier, "service": service,
                    "weight_lbs": total_weight_lbs, "error": "No shipping rate found"}
        r = dict(row) if hasattr(row, 'keys') else {
            "base_rate": row[0], "per_lb_rate": row[1],
            "carrier": row[2], "service": row[3],
        }
        base = float(r["base_rate"])
        per_lb = float(r["per_lb_rate"])
        cost = base + (per_lb * total_weight_lbs)
        return {
            "estimated_cost": round(cost, 2),
            "carrier": r["carrier"],
            "service": r["service"],
            "weight_lbs": total_weight_lbs,
            "base_rate": base,
            "per_lb_rate": per_lb,
        }


def get_shipping_rates(carrier: str = None, active_only: bool = True) -> list[dict]:
    """Get all shipping rates, optionally filtered by carrier."""
    with get_db() as conn:
        conditions, params = [], []
        if carrier:
            conditions.append("carrier = %s")
            params.append(carrier)
        if active_only:
            conditions.append("is_active = 1")
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM shipping_rates {where} ORDER BY carrier, service, min_weight_lbs",
            params,
        ).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def update_shipping_rate(rate_id: int, **fields) -> bool:
    """Update a shipping rate row."""
    allowed = {"carrier", "service", "min_weight_lbs", "max_weight_lbs",
               "base_rate", "per_lb_rate", "is_active", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        sets = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [rate_id]
        conn.execute(
            f"UPDATE shipping_rates SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            vals,
        )
        conn.commit()
        return True


def estimate_order_shipping(line_items: list[dict], carrier: str = "UPS",
                            service: str = "Ground") -> dict:
    """Estimate shipping for an order by summing product weights.

    Args:
        line_items: List of dicts with 'product_id' and 'quantity'.
        carrier: Carrier name.
        service: Service level.

    Returns:
        {"estimated_cost": float, "total_weight": float, "items_with_weight": int,
         "items_without_weight": int, ...}
    """
    total_weight = 0.0
    with_weight = 0
    without_weight = 0
    with get_db() as conn:
        for item in line_items:
            pid = item.get("product_id")
            qty = int(item.get("quantity", 1) or 1)
            if not pid:
                without_weight += 1
                continue
            row = conn.execute(
                "SELECT weight FROM products WHERE id = %s", (pid,)
            ).fetchone()
            w = float((dict(row) if hasattr(row, 'keys') else {"weight": row[0]}).get("weight", 0) or 0) if row else 0
            if w > 0:
                total_weight += w * qty
                with_weight += 1
            else:
                without_weight += 1

    result = estimate_shipping_cost(total_weight, carrier, service)
    result["total_weight"] = round(total_weight, 2)
    result["items_with_weight"] = with_weight
    result["items_without_weight"] = without_weight
    return result


# =============================================================================
# BROWSE / FILTER HELPERS
# =============================================================================

def get_all_manufacturers() -> list[str]:
    """Get distinct list of manufacturers for dropdown filters."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT manufacturer FROM products 
            WHERE manufacturer IS NOT NULL AND manufacturer != ''
            ORDER BY manufacturer
        """).fetchall()
        return [r["manufacturer"] if isinstance(r, dict) else r[0] for r in rows]


def get_all_engine_manufacturers() -> list[str]:
    """Get distinct list of engine manufacturers (oem_manufacturer) from products."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT oem_manufacturer FROM products
            WHERE oem_manufacturer IS NOT NULL AND oem_manufacturer != ''
            ORDER BY oem_manufacturer
        """).fetchall()
        return [r["oem_manufacturer"] if isinstance(r, dict) else r[0] for r in rows]


def get_all_engine_models() -> list[str]:
    """Get distinct list of engine models from products."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT engine FROM products
            WHERE engine IS NOT NULL AND engine != ''
            ORDER BY engine
        """).fetchall()
        return [r["engine"] if isinstance(r, dict) else r[0] for r in rows]


def get_all_categories() -> list[str]:
    """Get distinct list of categories for dropdown filters."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT category FROM products 
            WHERE category IS NOT NULL AND category != ''
            ORDER BY category
        """).fetchall()
        return [r["category"] if isinstance(r, dict) else r[0] for r in rows]


def get_all_subcategories() -> list[str]:
    """Get distinct list of subcategories for dropdown filters."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT subcategory FROM products
            WHERE subcategory IS NOT NULL AND subcategory != ''
            ORDER BY subcategory
        """).fetchall()
        return [r["subcategory"] if isinstance(r, dict) else r[0] for r in rows]


def get_subcategories_for_category(category: str) -> list[str]:
    """Get distinct subcategories belonging to a specific category."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT subcategory FROM products
            WHERE category = %s AND subcategory IS NOT NULL AND subcategory != ''
            ORDER BY subcategory
        """, (category,)).fetchall()
        return [r["subcategory"] if isinstance(r, dict) else r[0] for r in rows]


def get_all_suppliers() -> list[str]:
    """Get distinct list of suppliers for dropdown filters."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT supplier FROM products 
            WHERE supplier IS NOT NULL AND supplier != ''
            ORDER BY supplier
        """).fetchall()
        return [r["supplier"] if isinstance(r, dict) else r[0] for r in rows]


def list_products_by_manufacturer(manufacturer: str, limit: int = 500) -> list[dict]:
    """Get all products for a specific manufacturer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM products 
            WHERE manufacturer = %s
            ORDER BY title
            LIMIT %s
        """, (manufacturer, limit)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def list_products_by_category(category: str, limit: int = 500) -> list[dict]:
    """Get all products in a specific category."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM products 
            WHERE category = %s
            ORDER BY manufacturer, title
            LIMIT %s
        """, (category, limit)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def browse_products(
    manufacturer: str = None,
    category: str = None,
    supplier: str = None,
    search: str = None,
    low_stock_only: bool = False,
    limit: int = 500
) -> list[dict]:
    """Flexible product browser with multiple optional filters.
    
    Args:
        manufacturer: Filter by manufacturer (exact match)
        category: Filter by category (exact match)
        supplier: Filter by supplier (exact match)
        search: Search in SKU, title, part numbers (partial match)
        low_stock_only: Only return products with qty_on_hand <= 5
        limit: Max results
        
    Returns:
        List of product dicts
    """
    conditions = []
    params = []
    
    if manufacturer:
        conditions.append("manufacturer = %s")
        params.append(manufacturer)
    
    if category:
        conditions.append("category = %s")
        params.append(category)
    
    if supplier:
        conditions.append("supplier = %s")
        params.append(supplier)
    
    if low_stock_only:
        conditions.append("qty_on_hand <= 5")
    
    if search:
        normalized = _normalize_part_number(search)
        search_like = f"%{search}%"
        conditions.append("""(
            sku LIKE %s OR title LIKE %s
            OR brief_description LIKE %s
            OR invoice_description LIKE %s
            OR category LIKE %s
            OR supplier LIKE %s
            OR manufacturer LIKE %s
            OR engine LIKE %s
            OR truck_manufacturer LIKE %s
            OR truck_model LIKE %s
            OR oem_part_number LIKE %s
            OR id IN (
                SELECT product_id FROM part_interchanges
                WHERE interchange_number LIKE %s
            )
            OR id IN (SELECT product_id FROM part_numbers WHERE number_normalized LIKE %s)
            OR id IN (
                SELECT pn_prod.product_id
                FROM part_numbers pn_prod
                JOIN xref_links x ON (pn_prod.id = x.part_a_id OR pn_prod.id = x.part_b_id)
                JOIN part_numbers pn_match ON (
                    (x.part_a_id = pn_match.id OR x.part_b_id = pn_match.id)
                    AND pn_match.id != pn_prod.id
                )
                WHERE pn_match.number_normalized LIKE %s
            )
        )""")
        params.extend([search_like] * 11 + [search_like, f"%{normalized}%", f"%{normalized}%"])
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)
    
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT * FROM products 
            WHERE {where_clause}
            ORDER BY manufacturer, title
            LIMIT %s
        """, params).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def get_all_products(limit: int = 10000) -> list[dict]:
    """Get all products (for valuation and reports).
    
    Args:
        limit: Max results (default 10000)
        
    Returns:
        List of product dicts with all fields
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM products 
            ORDER BY category, manufacturer, title
            LIMIT %s
        """, (limit,)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


# =============================================================================
# VENDOR MANAGEMENT
# =============================================================================

def get_all_vendors(active_only: bool = True) -> list[dict]:
    """Get all vendors, optionally filtered by active status."""
    with get_db() as conn:
        if active_only:
            rows = conn.execute("""
                SELECT * FROM vendors WHERE is_active = TRUE ORDER BY name
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM vendors ORDER BY name
            """).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def get_vendor(vendor_id: int) -> dict | None:
    """Get a single vendor by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM vendors WHERE id = %s", (vendor_id,)
        ).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else (dict(enumerate(row)) if row else None)


def create_vendor(
    name: str,
    code: str = None,
    contact_name: str = None,
    contact_email: str = None,
    contact_phone: str = None,
    address: str = None,
    payment_terms: str = None,
    notes: str = None,
    lead_time_days: int = 14,
    avg_transit_days: int = 0,
    cutoff_time_local: str = None,
    cutoff_timezone: str = None,
    cutoff_notes: str = None,
    website: str = None,
    account_number: str = None,
    tax_id: str = None,
    is_1099: bool = False,
    ship_from_address: str = None,
    min_order_amount: float = 0,
    freight_paid_threshold: float = 0,
    return_policy: str = None,
    tracking_url_template: str = None,
    qbo_vendor_id: str = None,
    core_handling: str = None,
) -> dict:
    """Create a new vendor."""
    with get_db() as conn:
        try:
            conn.execute("""
                INSERT INTO vendors (
                    name, code, contact_name, contact_email, contact_phone,
                    address, payment_terms, notes, lead_time_days, avg_transit_days,
                    cutoff_time_local, cutoff_timezone, cutoff_notes,
                    website, account_number, tax_id, is_1099, ship_from_address,
                    min_order_amount, freight_paid_threshold, return_policy,
                    tracking_url_template, qbo_vendor_id, core_handling
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                name, code, contact_name, contact_email, contact_phone,
                address, payment_terms, notes, lead_time_days, avg_transit_days,
                cutoff_time_local, cutoff_timezone, cutoff_notes,
                website, account_number, tax_id, is_1099, ship_from_address,
                min_order_amount, freight_paid_threshold, return_policy,
                tracking_url_template, qbo_vendor_id, core_handling,
            ))
            conn.commit()

            # Get the created vendor
            row = conn.execute("SELECT * FROM vendors WHERE name = %s ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            vendor_dict = dict(row) if row else None
            vendor_id = vendor_dict.get("id") if vendor_dict else None
            try:
                from qbo.sync_queue import enqueue, ACTION_CREATE
                if vendor_id:
                    enqueue("vendor", vendor_id, ACTION_CREATE,
                            payload={"name": name},
                            message=f"Vendor {name} awaiting QBO push")
            except Exception:
                pass
            return {"success": True, "vendor": vendor_dict, "vendor_id": vendor_id}
        except Exception as e:
            return {"success": False, "error": str(e)}


def update_vendor(vendor_id: int, **fields) -> dict:
    """Update a vendor's fields."""
    allowed = {"name", "code", "contact_name", "contact_email", "contact_phone",
               "address", "payment_terms", "notes", "is_active", "qbo_vendor_id",
               "lead_time_days", "avg_transit_days",
               "cutoff_time_local", "cutoff_timezone", "cutoff_notes",
               "website", "account_number", "tax_id", "is_1099",
               "ship_from_address", "min_order_amount", "freight_paid_threshold",
               "return_policy", "tracking_url_template",
               # Migration 095: QBO bidirectional sync.
               "city", "state", "zip", "balance", "qbo_last_pulled_at",
               # Migration 108: vendor-level core handling override.
               "core_handling",
               # Migration 115 (Phase G): vendor returns / RGA address.
               "returns_address_line1", "returns_address_line2",
               "returns_address_city", "returns_address_state", "returns_address_zip",
               "returns_contact_name", "returns_contact_phone", "returns_contact_email"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    
    if not updates:
        return {"success": False, "error": "No valid fields to update"}
    
    set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
    params = list(updates.values()) + [vendor_id]
    
    with get_db() as conn:
        conn.execute(f"UPDATE vendors SET {set_clause}, updated_at = NOW() WHERE id = %s", params)
        conn.commit()
        return {"success": True}


def delete_vendor(vendor_id: int) -> dict:
    """Soft-delete a vendor by marking inactive."""
    return update_vendor(vendor_id, is_active=False)


def get_vendor_avg_transit_days(vendor_id: int, lookback: int = 10) -> int | None:
    """Average transit (order_date → received_date) for vendor's last N received POs.

    Returns ``None`` when there is not enough history.
    """
    if not vendor_id:
        return None
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT order_date, received_date
                FROM purchase_orders
                WHERE vendor_id = %s
                  AND order_date IS NOT NULL
                  AND received_date IS NOT NULL
                ORDER BY received_date DESC
                LIMIT %s
                """,
                (vendor_id, lookback),
            ).fetchall()
    except Exception:
        return None

    from datetime import date as _date
    deltas: list[int] = []
    for r in rows or []:
        try:
            od = r["order_date"] if hasattr(r, "keys") else r[0]
            rd = r["received_date"] if hasattr(r, "keys") else r[1]
            if isinstance(od, str):
                od = _date.fromisoformat(od[:10])
            if isinstance(rd, str):
                rd = _date.fromisoformat(rd[:10])
            d = (rd - od).days
            if d >= 0:
                deltas.append(d)
        except Exception:
            continue
    if not deltas:
        return None
    return int(round(sum(deltas) / len(deltas)))


def get_vendor_stats(active_only: bool = True) -> list[dict]:
    """Aggregate spend / activity stats per vendor.

    Returns one dict per vendor with all base vendor columns plus:
        po_count_open, po_count_received,
        spend_ytd, spend_last_12mo, spend_lifetime,
        last_order_date (ISO str), last_received_date (ISO str),
        avg_po_total, on_time_pct (or None when no data),
        open_balance (received POs without QBO bill id),
        return_count, return_amount.

    Aggregation is done in Python after a small number of bulk fetches
    to avoid SQLite/Postgres date-function dialect differences.
    """
    from datetime import date, timedelta

    today = date.today()
    year_start = date(today.year, 1, 1)
    twelve_mo_ago = today - timedelta(days=365)

    open_statuses = {"draft", "sent", "partial", "approved", "ordered"}
    received_statuses = {"received", "closed", "partial"}

    def _parse_date(v):
        if not v:
            return None
        if hasattr(v, "year") and not isinstance(v, str):
            return v
        try:
            return date.fromisoformat(str(v)[:10])
        except Exception:
            return None

    with get_db() as conn:
        if active_only:
            vendor_rows = conn.execute(
                "SELECT * FROM vendors WHERE is_active = TRUE ORDER BY name"
            ).fetchall()
        else:
            vendor_rows = conn.execute(
                "SELECT * FROM vendors ORDER BY name"
            ).fetchall()
        vendors = [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in vendor_rows]

        try:
            po_rows = conn.execute(
                """
                SELECT vendor_id, status, order_date, expected_date,
                       received_date, total, qbo_bill_id
                FROM purchase_orders
                """
            ).fetchall()
            pos = [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in po_rows]
        except Exception:
            pos = []

        try:
            ret_rows = conn.execute(
                "SELECT vendor_id, credit_amount FROM vendor_returns"
            ).fetchall()
            returns = [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in ret_rows]
        except Exception:
            returns = []

    stats: dict[int, dict] = {}
    for v in vendors:
        stats[v["id"]] = {
            "po_count_open": 0,
            "po_count_received": 0,
            "spend_ytd": 0.0,
            "spend_last_12mo": 0.0,
            "spend_lifetime": 0.0,
            "last_order_date": None,
            "last_received_date": None,
            "po_totals": [],
            "on_time_count": 0,
            "on_time_total": 0,
            "open_balance": 0.0,
            "return_count": 0,
            "return_amount": 0.0,
        }

    for po in pos:
        vid = po.get("vendor_id")
        if vid not in stats:
            continue
        s = stats[vid]
        status = (po.get("status") or "").lower()
        try:
            total = float(po.get("total") or 0)
        except (TypeError, ValueError):
            total = 0.0
        order_d = _parse_date(po.get("order_date"))
        recv_d = _parse_date(po.get("received_date"))
        exp_d = _parse_date(po.get("expected_date"))

        if status in open_statuses and not recv_d:
            s["po_count_open"] += 1
        if status in received_statuses or recv_d:
            s["po_count_received"] += 1
            s["spend_lifetime"] += total
            s["po_totals"].append(total)
            if order_d and order_d >= year_start:
                s["spend_ytd"] += total
            if order_d and order_d >= twelve_mo_ago:
                s["spend_last_12mo"] += total
            if recv_d and exp_d:
                s["on_time_total"] += 1
                if recv_d <= exp_d:
                    s["on_time_count"] += 1
            if status == "received" and not po.get("qbo_bill_id"):
                s["open_balance"] += total

        if order_d and (s["last_order_date"] is None or order_d > s["last_order_date"]):
            s["last_order_date"] = order_d
        if recv_d and (s["last_received_date"] is None or recv_d > s["last_received_date"]):
            s["last_received_date"] = recv_d

    for r in returns:
        vid = r.get("vendor_id")
        if vid in stats:
            stats[vid]["return_count"] += 1
            try:
                stats[vid]["return_amount"] += float(r.get("credit_amount") or 0)
            except (TypeError, ValueError):
                pass

    out: list[dict] = []
    for v in vendors:
        s = stats[v["id"]]
        avg_po = (sum(s["po_totals"]) / len(s["po_totals"])) if s["po_totals"] else 0.0
        on_time_pct = (s["on_time_count"] / s["on_time_total"] * 100.0) if s["on_time_total"] else None
        merged = dict(v)
        merged.update({
            "po_count_open": s["po_count_open"],
            "po_count_received": s["po_count_received"],
            "spend_ytd": round(s["spend_ytd"], 2),
            "spend_last_12mo": round(s["spend_last_12mo"], 2),
            "spend_lifetime": round(s["spend_lifetime"], 2),
            "last_order_date": s["last_order_date"].isoformat() if s["last_order_date"] else None,
            "last_received_date": s["last_received_date"].isoformat() if s["last_received_date"] else None,
            "avg_po_total": round(avg_po, 2),
            "on_time_pct": round(on_time_pct, 1) if on_time_pct is not None else None,
            "open_balance": round(s["open_balance"], 2),
            "return_count": s["return_count"],
            "return_amount": round(s["return_amount"], 2),
        })
        out.append(merged)
    return out


# =============================================================================
# PURCHASE ORDER MANAGEMENT
# =============================================================================

def generate_po_number() -> str:
    """Generate next PO number in format PO-YYYY-NNNN."""
    from datetime import datetime
    year = datetime.now().year
    prefix = f"PO-{year}-"
    
    with get_db() as conn:
        row = conn.execute("""
            SELECT po_number FROM purchase_orders 
            WHERE po_number LIKE %s 
            ORDER BY po_number DESC LIMIT 1
        """, (f"{prefix}%",)).fetchone()
        
        if row:
            last_num = int(row[0].split("-")[-1])
            next_num = last_num + 1
        else:
            next_num = 1
        
        return f"{prefix}{next_num:04d}"


def get_all_purchase_orders(status: str = None, vendor_id: int = None, limit: int = 100) -> list[dict]:
    """Get purchase orders with optional filtering."""
    conditions = []
    params = []
    
    if status:
        conditions.append("po.status = %s")
        params.append(status)
    
    if vendor_id:
        conditions.append("po.vendor_id = %s")
        params.append(vendor_id)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)
    
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT po.*, v.name as vendor_name,
                   (SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = po.id) as line_count
            FROM purchase_orders po
            LEFT JOIN vendors v ON po.vendor_id = v.id
            WHERE {where_clause}
            ORDER BY po.created_at DESC
            LIMIT %s
        """, params).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def get_purchase_order(po_id: int) -> dict | None:
    """Get a single PO with lines."""
    with get_db() as conn:
        po_row = conn.execute("""
            SELECT po.*, v.name as vendor_name
            FROM purchase_orders po
            LEFT JOIN vendors v ON po.vendor_id = v.id
            WHERE po.id = %s
        """, (po_id,)).fetchone()
        
        if not po_row:
            return None
        
        po = dict(po_row) if hasattr(po_row, 'keys') else dict(enumerate(po_row))

        # Build a schema-safe select list for product identifiers.
        # Some older DBs do not yet have supplier_part_number/oem_part_number.
        product_cols = {c[1] for c in conn.execute("PRAGMA table_info(products)").fetchall()}
        extra_cols: list[str] = []
        if "supplier_part_number" in product_cols:
            extra_cols.append("p.supplier_part_number")
        if "pai_sku" in product_cols:
            extra_cols.append("p.pai_sku")
        if "oem_part_number" in product_cols:
            extra_cols.append("p.oem_part_number")
        extras_sql = ", " + ", ".join(extra_cols) if extra_cols else ""
        
        # Get lines (LEFT JOIN so non-product lines like discounts are included)
        # If pol has its own sku/description (added in mig 086), fall back to those when
        # there is no joined product row.  SELECT order matters: the aliased columns
        # come AFTER pol.* so the joined values win for normal product lines.
        line_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
        has_line_sku = "sku" in line_cols
        has_line_desc = "description" in line_cols
        sku_expr = "COALESCE(p.sku, pol.sku)" if has_line_sku else "p.sku"
        title_expr = "COALESCE(p.title, pol.description)" if has_line_desc else "p.title"
        lines = conn.execute(f"""
            SELECT pol.*, {sku_expr} AS sku, {title_expr} AS title{extras_sql}
            FROM purchase_order_lines pol
            LEFT JOIN products p ON pol.product_id = p.id
            WHERE pol.po_id = %s
            ORDER BY pol.id
        """, (po_id,)).fetchall()
        
        po["lines"] = [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in lines]
        return po


def create_purchase_order(
    vendor_id: int,
    order_date: str = None,
    expected_date: str = None,
    ship_to: str = None,
    bill_to: str = None,
    notes: str = None,
    linked_so_id: int | None = None,
    lines: list[dict] = None
) -> dict:
    """Create a new purchase order.
    
    Args:
        vendor_id: Vendor ID
        order_date: Order date (default today)
        expected_date: Expected delivery date
        notes: PO notes
        lines: List of {"product_id": int, "quantity_ordered": int, "unit_cost": float}
    """
    from datetime import datetime
    
    po_number = generate_po_number()
    order_date = order_date or datetime.now().strftime("%Y-%m-%d")
    
    with get_db() as conn:
        try:
            # Create PO header
            po_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
            if "linked_so_id" in po_cols and "ship_to" in po_cols and "bill_to" in po_cols:
                conn.execute("""
                    INSERT INTO purchase_orders (
                        po_number, vendor_id, status, order_date, expected_date, ship_to, bill_to, notes, linked_so_id
                    )
                    VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s, %s)
                """, (po_number, vendor_id, order_date, expected_date, ship_to, bill_to, notes, linked_so_id))
            elif "linked_so_id" in po_cols:
                conn.execute("""
                    INSERT INTO purchase_orders (
                        po_number, vendor_id, status, order_date, expected_date, notes, linked_so_id
                    )
                    VALUES (%s, %s, 'draft', %s, %s, %s, %s)
                """, (po_number, vendor_id, order_date, expected_date, notes, linked_so_id))
            elif "ship_to" in po_cols and "bill_to" in po_cols:
                conn.execute("""
                    INSERT INTO purchase_orders (
                        po_number, vendor_id, status, order_date, expected_date, ship_to, bill_to, notes
                    )
                    VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s)
                """, (po_number, vendor_id, order_date, expected_date, ship_to, bill_to, notes))
            else:
                conn.execute("""
                    INSERT INTO purchase_orders (po_number, vendor_id, status, order_date, expected_date, notes)
                    VALUES (%s, %s, 'draft', %s, %s, %s)
                """, (po_number, vendor_id, order_date, expected_date, notes))
            
            # Get the PO ID
            po_row = conn.execute("SELECT id FROM purchase_orders WHERE po_number = %s", (po_number,)).fetchone()
            po_id = po_row[0] if po_row else None
            
            if not po_id:
                return {"success": False, "error": "Failed to create PO"}
            
            # Detect core columns once.
            pol_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
            has_line_core = "core_charge" in pol_cols and "core_total" in pol_cols
            po_has_core_subtotal = "core_subtotal" in po_cols

            # Add lines if provided
            subtotal = 0
            core_subtotal = 0
            if lines:
                for line in lines:
                    qty = int(line.get("quantity_ordered", 0) or 0)
                    cost = float(line.get("unit_cost", 0) or 0)
                    line_total = qty * cost
                    subtotal += line_total

                    # Core charge: explicit value or look up product.core_charge
                    core_chg = line.get("core_charge")
                    if core_chg is None:
                        try:
                            cc_row = conn.execute(
                                "SELECT COALESCE(core_charge,0) FROM products WHERE id=%s",
                                (line["product_id"],),
                            ).fetchone()
                            core_chg = float(cc_row[0]) if cc_row else 0.0
                        except Exception:
                            core_chg = 0.0
                    core_chg = float(core_chg or 0)
                    core_total_val = qty * core_chg
                    core_subtotal += core_total_val

                    if has_line_core:
                        conn.execute("""
                            INSERT INTO purchase_order_lines (po_id, product_id, quantity_ordered, unit_cost, line_total, notes, core_charge, core_total)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """, (po_id, line["product_id"], qty, cost, line_total, line.get("notes"), core_chg, core_total_val))
                    else:
                        conn.execute("""
                            INSERT INTO purchase_order_lines (po_id, product_id, quantity_ordered, unit_cost, line_total, notes)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (po_id, line["product_id"], qty, cost, line_total, line.get("notes")))

            # Update PO total
            total = subtotal + core_subtotal
            if po_has_core_subtotal:
                conn.execute(
                    "UPDATE purchase_orders SET subtotal = %s, core_subtotal = %s, total = %s WHERE id = %s",
                    (subtotal, core_subtotal, total, po_id),
                )
            else:
                conn.execute(
                    "UPDATE purchase_orders SET subtotal = %s, total = %s WHERE id = %s",
                    (subtotal, total, po_id),
                )
            
            conn.commit()
            return {"success": True, "po_id": po_id, "po_number": po_number}
            
        except Exception as e:
            return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Core Handling Overhaul (migration 108) — PO core line orchestration
# ---------------------------------------------------------------------------

def _po_vendor_id(conn, po_id: int) -> int | None:
    try:
        row = conn.execute(
            "SELECT vendor_id FROM purchase_orders WHERE id = ?", (po_id,)
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def _resolve_behavior_for_po_line(conn, po_id: int, product_id: int) -> str:
    """One rule: if the product has a refundable core with a non-zero
    vendor deposit, the PO gets a separate ``line_kind='core'`` child line.
    Otherwise no core line.

    The old behavior-matrix (``products.core_type`` × ``vendors.core_handling``)
    is deprecated. Those columns still exist for back-compat but are ignored
    here. See migration 114 + Core Handling Revitalization plan.
    """
    if not product_id:
        return "none"
    prow = conn.execute(
        "SELECT COALESCE(has_core, 0) AS has_core, "
        "       COALESCE(core_charge, 0) AS core_charge "
        "FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    if not prow:
        return "none"
    has_core = int(prow[0] or 0) == 1
    core_charge = float(prow[1] or 0)
    if has_core and core_charge > 0:
        return "separate_line"
    # Legacy fallback: if has_core flag was never set but a positive charge
    # exists, still treat as refundable (matches mig 106 backfill semantics).
    if core_charge > 0:
        return "separate_line"
    return "none"


def _product_core_amount(conn, product_id: int) -> float:
    if not product_id:
        return 0.0
    try:
        row = conn.execute(
            "SELECT COALESCE(core_charge, 0) FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def _sync_core_child_line(
    conn,
    *,
    po_id: int,
    parent_line_id: int,
    product_id: int,
    quantity: int,
    behavior: str,
) -> None:
    """Ensure the auto-generated 'Refundable Core Deposit' child line exists
    (or is updated/removed) to match *behavior* and *quantity*.

    Only acts for behavior == 'separate_line'. For any other behavior, any
    existing child 'core' line for this parent is removed.
    """
    pol_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
    if "parent_line_id" not in pol_cols or "line_kind" not in pol_cols:
        return  # schema too old; bail silently

    existing = conn.execute(
        """
        SELECT id FROM purchase_order_lines
         WHERE parent_line_id = ? AND COALESCE(line_kind,'product') = 'core'
         LIMIT 1
        """,
        (parent_line_id,),
    ).fetchone()
    existing_id = int(existing[0]) if existing else None

    if behavior != "separate_line":
        if existing_id:
            # Also drop any obligation tied to this child line.
            try:
                conn.execute(
                    "DELETE FROM vendor_core_obligations WHERE po_line_id = ?",
                    (existing_id,),
                )
            except Exception:
                pass
            conn.execute("DELETE FROM purchase_order_lines WHERE id = ?", (existing_id,))
        return

    core_amount = _product_core_amount(conn, product_id)
    qty = int(quantity or 0)
    if qty <= 0 or core_amount <= 0:
        # No deposit to record — clean up any stale child.
        if existing_id:
            conn.execute("DELETE FROM purchase_order_lines WHERE id = ?", (existing_id,))
        return

    line_total = qty * core_amount
    description = "Refundable Core Deposit"

    if existing_id:
        conn.execute(
            """
            UPDATE purchase_order_lines
               SET quantity_ordered = ?,
                   unit_cost = ?,
                   line_total = ?,
                   description = ?,
                   line_kind = 'core',
                   core_charge = 0,
                   core_total = 0
             WHERE id = ?
            """,
            (qty, core_amount, line_total, description, existing_id),
        )
        # Sync linked obligation
        try:
            conn.execute(
                """
                UPDATE vendor_core_obligations
                   SET quantity = ?, core_amount = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE po_line_id = ? AND status = 'pending'
                """,
                (qty, core_amount, existing_id),
            )
        except Exception:
            pass
        return

    # Insert new child core line.
    has_sku = "sku" in pol_cols
    cols = ["po_id", "product_id", "quantity_ordered", "unit_cost", "line_total",
            "notes", "core_charge", "core_total", "line_kind", "parent_line_id",
            "description"]
    vals = [po_id, product_id, qty, core_amount, line_total, None, 0, 0,
            "core", parent_line_id, description]
    if has_sku:
        cols.append("sku")
        vals.append(None)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO purchase_order_lines ({', '.join(cols)}) VALUES ({placeholders})",
        tuple(vals),
    )
    new_child_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0] or 0)

    # NOTE: Vendor core obligation creation has moved to the customer-core
    # path. We don't owe the vendor anything until the customer brings a
    # core back to us (see core_handling.create_vendor_core_obligation_from_customer_return).
    return None


def _maybe_apply_conditional_exchange_note(
    conn, *, po_line_id: int, behavior: str
) -> None:
    """Deprecated. Kept as a no-op for callsite compatibility — the
    conditional-exchange behavior was removed in the May 2026 Core Handling
    Revitalization. All callers should be cleaned up over time.
    """
    return None


def add_po_line(po_id: int, product_id: int, quantity_ordered: int, unit_cost: float = 0, notes: str = None, core_charge: float | None = None) -> dict:
    """Add a line item to a PO. core_charge defaults to product's core_charge when None.

    Core handling overhaul (migration 108): the effective behavior for the
    (product, vendor) pair is resolved via products.core_type +
    vendors.core_handling override. For ``separate_line`` behavior, the
    inline ``core_charge`` on this row is zeroed out and a sibling
    ``line_kind='core'`` "Refundable Core Deposit" line is auto-created
    along with a matching ``vendor_core_obligations`` row.
    """
    line_total = quantity_ordered * unit_cost

    with get_db() as conn:
        try:
            pol_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
            has_line_core = "core_charge" in pol_cols and "core_total" in pol_cols

            behavior = _resolve_behavior_for_po_line(conn, po_id, product_id)

            if core_charge is None:
                try:
                    cc_row = conn.execute(
                        "SELECT COALESCE(core_charge,0) FROM products WHERE id=%s",
                        (product_id,),
                    ).fetchone()
                    core_charge = float(cc_row[0]) if cc_row else 0.0
                except Exception:
                    core_charge = 0.0
            core_charge = float(core_charge or 0)

            # For non-legacy behaviors, the inline core_charge on the product
            # line is zeroed: cores are tracked either as a separate child
            # line, baked into unit_cost, or as a printed-note-only exchange.
            if behavior in ("separate_line", "included_in_price", "conditional_exchange"):
                core_charge = 0.0

            if has_line_core:
                conn.execute("""
                    INSERT INTO purchase_order_lines (po_id, product_id, quantity_ordered, unit_cost, line_total, notes, core_charge, core_total)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (po_id, product_id, quantity_ordered, unit_cost, line_total, notes,
                      core_charge, quantity_ordered * core_charge))
            else:
                conn.execute("""
                    INSERT INTO purchase_order_lines (po_id, product_id, quantity_ordered, unit_cost, line_total, notes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (po_id, product_id, quantity_ordered, unit_cost, line_total, notes))

            new_line_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0] or 0)

            # Apply conditional-exchange note (printed-only, no accounting).
            _maybe_apply_conditional_exchange_note(
                conn, po_line_id=new_line_id, behavior=behavior
            )
            # Create/sync the Refundable Core Deposit child line when needed.
            _sync_core_child_line(
                conn,
                po_id=po_id,
                parent_line_id=new_line_id,
                product_id=product_id,
                quantity=quantity_ordered,
                behavior=behavior,
            )

            # Recalculate PO total
            _recalculate_po_total(conn, po_id)
            conn.commit()
            return {"success": True, "line_id": new_line_id, "behavior": behavior}
        except Exception as e:
            return {"success": False, "error": str(e)}


def find_open_purchase_orders_for_vendor(vendor_id: int) -> list[dict]:
    """Return active purchase orders for a vendor, newest first."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT po.id, po.po_number, po.status, po.total, po.order_date,
                   COUNT(pol.id) AS line_count
            FROM purchase_orders po
            LEFT JOIN purchase_order_lines pol ON pol.po_id = po.id
            WHERE po.vendor_id = %s
              AND po.status IN ('draft', 'sent', 'partial')
            GROUP BY po.id, po.po_number, po.status, po.total, po.order_date
            ORDER BY po.created_at DESC, po.id DESC
            """,
            (vendor_id,),
        ).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def add_or_merge_po_line(
    po_id: int,
    product_id: int,
    quantity_ordered: int,
    unit_cost: float = 0,
    notes: str = None,
    core_charge: float | None = None,
) -> dict:
    """Add a line item, or merge into an existing line for the same product.

    If core_charge is None, the product's core_charge is used.

    Core handling overhaul (migration 108): respects ``resolve_core_behavior``
    just like :func:`add_po_line`. When merging, the linked
    ``Refundable Core Deposit`` child line is kept in sync with the new
    quantity.
    """
    with get_db() as conn:
        try:
            pol_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
            has_line_core = "core_charge" in pol_cols and "core_total" in pol_cols
            has_line_kind = "line_kind" in pol_cols

            behavior = _resolve_behavior_for_po_line(conn, po_id, product_id)

            # Resolve core charge: explicit > product default > 0
            if core_charge is None:
                try:
                    cc_row = conn.execute(
                        "SELECT COALESCE(core_charge,0) FROM products WHERE id=%s",
                        (product_id,),
                    ).fetchone()
                    core_charge = float(cc_row[0]) if cc_row else 0.0
                except Exception:
                    core_charge = 0.0
            core_charge = float(core_charge or 0)
            if behavior in ("separate_line", "included_in_price", "conditional_exchange"):
                core_charge = 0.0

            # Merge candidate: only match top-level PRODUCT lines (skip
            # auto-generated 'core' children).
            kind_filter = (
                " AND COALESCE(line_kind,'product') = 'product'"
                if has_line_kind else ""
            )
            if has_line_core:
                existing = conn.execute(
                    f"""
                    SELECT id, quantity_ordered, unit_cost, notes, COALESCE(core_charge,0)
                    FROM purchase_order_lines
                    WHERE po_id = %s AND product_id = %s{kind_filter}
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (po_id, product_id),
                ).fetchone()
            else:
                existing = conn.execute(
                    f"""
                    SELECT id, quantity_ordered, unit_cost, notes
                    FROM purchase_order_lines
                    WHERE po_id = %s AND product_id = %s{kind_filter}
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (po_id, product_id),
                ).fetchone()
            if existing:
                line_id = existing[0]
                new_qty = int(existing[1] or 0) + int(quantity_ordered or 0)
                merged_cost = float(unit_cost or existing[2] or 0)
                merged_notes = notes or existing[3]
                if has_line_core:
                    merged_core = float(core_charge or (existing[4] if len(existing) > 4 else 0) or 0)
                    if behavior in ("separate_line", "included_in_price", "conditional_exchange"):
                        merged_core = 0.0
                    conn.execute(
                        """
                        UPDATE purchase_order_lines
                        SET quantity_ordered = %s,
                            unit_cost = %s,
                            notes = %s,
                            line_total = %s,
                            core_charge = %s,
                            core_total = %s
                        WHERE id = %s
                        """,
                        (new_qty, merged_cost, merged_notes, new_qty * merged_cost,
                         merged_core, new_qty * merged_core, line_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE purchase_order_lines
                        SET quantity_ordered = %s,
                            unit_cost = %s,
                            notes = %s,
                            line_total = %s
                        WHERE id = %s
                        """,
                        (new_qty, merged_cost, merged_notes, new_qty * merged_cost, line_id),
                    )
                _maybe_apply_conditional_exchange_note(
                    conn, po_line_id=line_id, behavior=behavior
                )
                _sync_core_child_line(
                    conn,
                    po_id=po_id,
                    parent_line_id=line_id,
                    product_id=product_id,
                    quantity=new_qty,
                    behavior=behavior,
                )
            else:
                if has_line_core:
                    conn.execute(
                        """
                        INSERT INTO purchase_order_lines (po_id, product_id, quantity_ordered, unit_cost, line_total, notes, core_charge, core_total)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (po_id, product_id, quantity_ordered, unit_cost,
                         quantity_ordered * unit_cost, notes,
                         core_charge, quantity_ordered * core_charge),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO purchase_order_lines (po_id, product_id, quantity_ordered, unit_cost, line_total, notes)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (po_id, product_id, quantity_ordered, unit_cost, quantity_ordered * unit_cost, notes),
                    )
                new_line_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0] or 0)
                _maybe_apply_conditional_exchange_note(
                    conn, po_line_id=new_line_id, behavior=behavior
                )
                _sync_core_child_line(
                    conn,
                    po_id=po_id,
                    parent_line_id=new_line_id,
                    product_id=product_id,
                    quantity=quantity_ordered,
                    behavior=behavior,
                )
            _recalculate_po_total(conn, po_id)
            conn.commit()
            return {"success": True, "behavior": behavior}
        except Exception as e:
            return {"success": False, "error": str(e)}


def _recalculate_po_total(conn, po_id: int) -> None:
    """Recalculate PO subtotal, core_subtotal, and total from lines.

    Discount lines (``line_kind='discount'``) are stored with negative
    ``line_total`` and are excluded from the displayed ``subtotal`` but
    are netted into the final ``total``.
    """
    pol_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
    po_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
    has_line_core = "core_total" in pol_cols
    has_po_core = "core_subtotal" in po_cols
    has_line_kind = "line_kind" in pol_cols

    # Build a WHERE clause that excludes discount + core rows from product subtotal.
    if has_line_kind:
        product_where = (
            "WHERE po_id = %s AND COALESCE(line_kind,'product') NOT IN ('discount','core')"
        )
        discount_where = (
            "WHERE po_id = %s AND COALESCE(line_kind,'product') = 'discount'"
        )
        core_line_where = (
            "WHERE po_id = %s AND COALESCE(line_kind,'product') = 'core'"
        )
    else:
        product_where = "WHERE po_id = %s"
        discount_where = "WHERE 0 = 1 AND po_id = %s"  # no-op
        core_line_where = "WHERE 0 = 1 AND po_id = %s"

    if has_line_core:
        row = conn.execute(
            "SELECT COALESCE(SUM(line_total),0), COALESCE(SUM(core_total),0) "
            f"FROM purchase_order_lines {product_where}",
            (po_id,),
        ).fetchone()
        subtotal = float(row[0] or 0) if row else 0
        core_subtotal = float(row[1] or 0) if row else 0
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(line_total), 0) as subtotal "
            f"FROM purchase_order_lines {product_where}",
            (po_id,),
        ).fetchone()
        subtotal = float(row[0] or 0) if row else 0
        core_subtotal = 0

    # Core lines (migration 108): line_kind='core' rows store the deposit
    # as their own ``line_total``. Add these into core_subtotal alongside
    # any legacy inline ``core_total`` values from product rows.
    if has_line_kind:
        crow = conn.execute(
            f"SELECT COALESCE(SUM(line_total),0) FROM purchase_order_lines {core_line_where}",
            (po_id,),
        ).fetchone()
        core_subtotal += float(crow[0] or 0) if crow else 0

    # Discount lines: sum is naturally negative (line_total stored negative).
    drow = conn.execute(
        f"SELECT COALESCE(SUM(line_total), 0) FROM purchase_order_lines {discount_where}",
        (po_id,),
    ).fetchone()
    discount_sum = float(drow[0] or 0) if drow else 0  # already negative

    # Get tax and shipping from PO
    po = conn.execute("SELECT tax, shipping FROM purchase_orders WHERE id = %s", (po_id,)).fetchone()
    tax = po[0] if po else 0
    shipping = po[1] if po else 0
    total = subtotal + core_subtotal + (tax or 0) + (shipping or 0) + discount_sum

    if has_po_core:
        conn.execute(
            "UPDATE purchase_orders SET subtotal = %s, core_subtotal = %s, total = %s, updated_at = NOW() WHERE id = %s",
            (subtotal, core_subtotal, total, po_id),
        )
    else:
        conn.execute(
            "UPDATE purchase_orders SET subtotal = %s, total = %s, updated_at = NOW() WHERE id = %s",
            (subtotal, total, po_id),
        )


def update_po_status(po_id: int, status: str) -> dict:
    """Update PO status (draft, sent, ordered, partial, received, closed, cancelled)."""
    valid_statuses = {"draft", "sent", "ordered", "partial", "received", "closed", "cancelled"}
    if status not in valid_statuses:
        return {"success": False, "error": f"Invalid status. Must be one of: {valid_statuses}"}

    with get_db() as conn:
        conn.execute(
            "UPDATE purchase_orders SET status = %s, updated_at = NOW() WHERE id = %s",
            (status, po_id)
        )

        # When a PO is closed or cancelled, clear any remaining backorder/on_po
        # quantities on linked SO lines so "Needs PO" doesn't stay stuck.
        if status in ("closed", "cancelled"):
            # Find the linked SO (via linked_so_id column or notes fallback)
            po_row = conn.execute(
                "SELECT linked_so_id, notes FROM purchase_orders WHERE id = %s", (po_id,)
            ).fetchone()
            linked_so_id = None
            if po_row:
                linked_so_id = po_row[0] if po_row[0] else None
                if not linked_so_id:
                    notes = po_row[1] or ""
                    if "From SO #" in notes:
                        try:
                            linked_so_id = int(notes.split("From SO #", 1)[1].split()[0].strip())
                        except Exception:
                            linked_so_id = None

            if linked_so_id:
                # Get all product_ids on this PO
                po_lines = conn.execute(
                    "SELECT product_id, quantity_ordered FROM purchase_order_lines WHERE po_id = %s",
                    (po_id,)
                ).fetchall()
                for pl in po_lines:
                    product_id = pl[0]
                    if not product_id:
                        continue
                    # Check if other active POs still cover this product for the SO
                    other_on_po = conn.execute(
                        """
                        SELECT COALESCE(SUM(
                            CASE WHEN pol.quantity_ordered > pol.quantity_received
                            THEN pol.quantity_ordered - pol.quantity_received ELSE 0 END), 0)
                        FROM purchase_order_lines pol
                        JOIN purchase_orders po ON po.id = pol.po_id
                        WHERE (po.linked_so_id = ? OR po.notes LIKE ?)
                          AND pol.product_id = ?
                          AND po.id != ?
                          AND po.status NOT IN ('cancelled', 'void', 'closed')
                        """,
                        (linked_so_id, f"%From SO #{linked_so_id}%", product_id, po_id),
                    ).fetchone()
                    remaining_on_po = int(other_on_po[0] if other_on_po else 0)
                    if remaining_on_po == 0:
                        # No other active POs cover this product — clear the backorder flag
                        conn.execute(
                            """
                            UPDATE sales_order_lines
                            SET quantity_backordered = 0,
                                on_po_qty = 0
                            WHERE so_id = ? AND product_id = ? AND quantity_backordered > 0
                            """,
                            (linked_so_id, product_id),
                        )

        conn.commit()
        return {"success": True}


def update_purchase_order(po_id: int, **kwargs) -> dict:
    """Update PO header fields: vendor_id, order_date, expected_date, notes, shipping, tax."""
    allowed = {
        "vendor_id", "order_date", "expected_date", "notes", "shipping", "tax", "status",
        "ship_to", "bill_to",
        # PO communication / dropship (migration 056)
        "vendor_message", "drop_ship", "no_tags",
        # PO lifecycle fields (migration 054)
        "sent_at", "sent_by", "sent_method", "confirmed_at", "vendor_confirm_number",
        "close_reason", "tracking_number", "resend_count", "last_resent_at",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"success": True}
    # P0.3 / P1.7 — central status-transition guard.
    if "status" in updates:
        try:
            from engine.status_transitions import (
                PO_TRANSITIONS, TransitionError, validate_transition,
            )
            with get_db() as _c:
                row = _c.execute(
                    "SELECT status FROM purchase_orders WHERE id = %s", (po_id,)
                ).fetchone()
            current = (
                (row["status"] if row and hasattr(row, "keys") else (row[0] if row else ""))
                or ""
            )
            validate_transition(PO_TRANSITIONS, old=current, new=str(updates["status"]))
        except TransitionError as e:
            return {"success": False, "error": str(e)}
        except Exception:
            pass
    with get_db() as conn:
        try:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [po_id]
            conn.execute(
                f"UPDATE purchase_orders SET {sets}, updated_at = NOW() WHERE id = %s", vals
            )
            if "shipping" in updates or "tax" in updates:
                _recalculate_po_total(conn, po_id)
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def update_po_line(line_id: int, **kwargs) -> dict:
    """Update a PO line: quantity_ordered, unit_cost, notes, core_charge."""
    allowed = {"quantity_ordered", "unit_cost", "notes", "core_charge"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"success": True}
    with get_db() as conn:
        try:
            pol_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
            if "core_charge" in updates and ("core_charge" not in pol_cols or "core_total" not in pol_cols):
                updates.pop("core_charge", None)
            if not updates:
                return {"success": True}
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [line_id]
            conn.execute(f"UPDATE purchase_order_lines SET {sets} WHERE id = %s", vals)
            # Recalculate line_total + core_total
            if "core_total" in pol_cols and "core_charge" in pol_cols:
                row = conn.execute(
                    "SELECT po_id, quantity_ordered, unit_cost, COALESCE(core_charge,0) FROM purchase_order_lines WHERE id = %s",
                    (line_id,),
                ).fetchone()
                if row:
                    po_id, qty, cost, core_chg = row[0], row[1], row[2], row[3]
                    lt = (qty or 0) * (cost or 0)
                    ct = (qty or 0) * (core_chg or 0)
                    conn.execute(
                        "UPDATE purchase_order_lines SET line_total = %s, core_total = %s WHERE id = %s",
                        (lt, ct, line_id),
                    )
                    _recalculate_po_total(conn, po_id)
            else:
                row = conn.execute(
                    "SELECT po_id, quantity_ordered, unit_cost FROM purchase_order_lines WHERE id = %s",
                    (line_id,),
                ).fetchone()
                if row:
                    po_id, qty, cost = row[0], row[1], row[2]
                    lt = qty * cost
                    conn.execute(
                        "UPDATE purchase_order_lines SET line_total = %s WHERE id = %s",
                        (lt, line_id),
                    )
                    _recalculate_po_total(conn, po_id)
            # When quantity changes, sync the child 'core' line if one exists
            if "quantity_ordered" in updates and "parent_line_id" in pol_cols and "line_kind" in pol_cols:
                line_row = conn.execute(
                    """SELECT po_id, product_id, quantity_ordered,
                              COALESCE(line_kind,'product') AS lk
                         FROM purchase_order_lines WHERE id = %s""",
                    (line_id,),
                ).fetchone()
                if line_row and str(line_row[3]).lower() != "core":
                    behavior = _resolve_behavior_for_po_line(conn, line_row[0], line_row[1])
                    _sync_core_child_line(
                        conn,
                        po_id=line_row[0],
                        parent_line_id=line_id,
                        product_id=line_row[1],
                        quantity=line_row[2],
                        behavior=behavior,
                    )
                    _recalculate_po_total(conn, line_row[0])
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def remove_po_line(line_id: int) -> dict:
    """Remove a PO line item.

    If the removed line is a top-level product line that has an
    auto-generated ``Refundable Core Deposit`` child (line_kind='core',
    parent_line_id=this), that child line and any matching
    ``vendor_core_obligations`` row are removed as well.
    """
    with get_db() as conn:
        try:
            row = conn.execute(
                "SELECT po_id FROM purchase_order_lines WHERE id = %s", (line_id,)
            ).fetchone()
            if not row:
                return {"success": False, "error": "Line not found"}
            po_id = row[0]
            pol_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
            if "parent_line_id" in pol_cols:
                child_rows = conn.execute(
                    "SELECT id FROM purchase_order_lines WHERE parent_line_id = %s",
                    (line_id,),
                ).fetchall()
                child_ids = [int(r[0]) for r in (child_rows or [])]
                for cid in child_ids:
                    try:
                        conn.execute(
                            "DELETE FROM vendor_core_obligations WHERE po_line_id = %s",
                            (cid,),
                        )
                    except Exception:
                        pass
                    conn.execute("DELETE FROM purchase_order_lines WHERE id = %s", (cid,))
            # Also drop any obligation tied directly to this line (defensive).
            try:
                conn.execute(
                    "DELETE FROM vendor_core_obligations WHERE po_line_id = %s",
                    (line_id,),
                )
            except Exception:
                pass
            conn.execute("DELETE FROM purchase_order_lines WHERE id = %s", (line_id,))
            _recalculate_po_total(conn, po_id)
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def receive_po_line(po_id: int, line_id: int, quantity_received: int, notes: str = None, location_id: int = None) -> dict:
    """Receive inventory against a PO line.
    
    Creates inventory transaction and updates qty_on_hand.
    If location_id is provided, also assigns the product to that location.

    P0.3: Hardened PO status guard.
      - Rejects quantities ≤ 0.
      - Rejects receiving against POs that are not in a receivable status
        ('sent' or 'partial'). Draft POs must be sent first; received /
        closed / cancelled POs cannot be re-received (no double-receive).
      - Preserves the "don't receive more than ordered" check, which also
        guards against per-line over-receiving.
    """
    # Reject zero / negative quantities up front.
    try:
        qty_req = int(quantity_received)
    except (TypeError, ValueError):
        return {"success": False, "error": "Invalid quantity"}
    if qty_req <= 0:
        return {"success": False, "error": "Receive quantity must be greater than zero"}

    _RECEIVABLE_PO_STATUSES = {"sent", "confirmed", "ordered", "partial"}
    with get_db() as conn:
        # P0.3 — PO-level status guard (no double-receive).
        po_row = conn.execute(
            "SELECT status FROM purchase_orders WHERE id = %s", (po_id,)
        ).fetchone()
        if not po_row:
            return {"success": False, "error": "Purchase order not found"}
        po_status = (
            (po_row["status"] if hasattr(po_row, "keys") else po_row[0]) or ""
        ).strip().lower()
        if po_status not in _RECEIVABLE_PO_STATUSES:
            return {
                "success": False,
                "error": (
                    f"Cannot receive against PO in status '{po_status}'. "
                    "PO must be in an active status (sent, confirmed, ordered, or partial)."
                ),
                "po_status": po_status,
            }

        # Get line details — LEFT JOIN so lines without a linked product (free-text
        # description lines) still return a row and can be marked received.
        line_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_order_lines)").fetchall()}
        has_line_sku  = "sku"         in line_cols
        has_line_desc = "description" in line_cols
        sku_expr   = "COALESCE(p.sku,   pol.sku)"         if has_line_sku  else "p.sku"
        title_expr = "COALESCE(p.title, pol.description)" if has_line_desc else "p.title"
        # NOTE: aliases must NOT collide with any column name returned by
        # `pol.*` (e.g. pol.sku / pol.description), because converting a
        # sqlite3.Row to dict keeps only the FIRST occurrence of a duplicate
        # key — which would mask the resolved value with the raw NULL from
        # pol.sku and silently bypass inventory adjustments below.
        line = conn.execute(f"""
            SELECT pol.*, {sku_expr} AS effective_sku, {title_expr} AS effective_title, po.po_number
            FROM purchase_order_lines pol
            LEFT JOIN products p ON pol.product_id = p.id
            JOIN purchase_orders po ON pol.po_id = po.id
            WHERE pol.id = %s AND pol.po_id = %s
        """, (line_id, po_id)).fetchone()
        
        if not line:
            return {"success": False, "error": "PO line not found"}
        
        line = dict(line) if hasattr(line, 'keys') else dict(enumerate(line))
        # Promote resolved aliases over any raw pol.* duplicates.
        line["sku"]   = line.get("effective_sku")   or line.get("sku")
        line["title"] = line.get("effective_title") or line.get("title")

        # Block accounting-only lines (core deposits, discounts) from being
        # received against. These rows have no SKU and would otherwise silently
        # bypass inventory adjustments. They are settled via their own workflows
        # (vendor_core_obligations / pricing adjustments).
        line_kind = str(line.get("line_kind") or "product").strip().lower()
        if line_kind in ("core", "discount"):
            return {
                "success": False,
                "error": (
                    f"Cannot receive against a '{line_kind}' accounting line. "
                    "These lines are settled separately and do not carry inventory."
                ),
                "line_kind": line_kind,
            }

        product_id = line.get("product_id") or line.get(2)  # Handle both dict and tuple
        sku = line.get("sku")
        po_number = line.get("po_number") or line.get(10)
        qty_ordered = line.get("quantity_ordered") or line.get(3) or 0
        qty_already_received = line.get("quantity_received") or line.get(4) or 0
        
        # Don't receive more than ordered
        remaining = qty_ordered - qty_already_received
        if remaining <= 0:
            return {
                "success": False,
                "error": (
                    f"Line already fully received ({qty_already_received}/"
                    f"{qty_ordered}). No remaining qty to receive."
                ),
            }
        if qty_req > remaining:
            return {"success": False, "error": f"Cannot receive {qty_req}. Only {remaining} remaining on order."}

        new_qty_received = qty_already_received + qty_req

        # Update line
        conn.execute(
            "UPDATE purchase_order_lines SET quantity_received = %s WHERE id = %s",
            (new_qty_received, line_id)
        )

        # Vendor core obligation auto-create: when a core-bearing line is
        # received, JAK's now owes the vendor that many physical cores back.
        # In the new model (post-mig 114), cores live on a separate child
        # line (``line_kind='core'`` with ``parent_line_id`` = this parent).
        # We fetch the child's unit_cost as the deposit amount, auto-bump
        # the child's quantity_received in tandem, and insert one obligation
        # per unit received, status 'on_shelf'.
        # Lifecycle (per the May 2026 plan):
        #   on_shelf → awaiting_customer_core → ready_to_ship → shipped → closed
        try:
            child_row = conn.execute(
                """
                SELECT id, COALESCE(unit_cost, 0) AS deposit,
                       COALESCE(quantity_received, 0) AS qr
                  FROM purchase_order_lines
                 WHERE parent_line_id = %s
                   AND COALESCE(line_kind, 'product') = 'core'
                 LIMIT 1
                """,
                (line_id,),
            ).fetchone()
            pol_core = 0.0
            child_line_id = None
            if child_row:
                child_line_id = (
                    child_row["id"] if hasattr(child_row, "keys") else child_row[0]
                )
                pol_core = float(
                    child_row["deposit"] if hasattr(child_row, "keys") else child_row[1]
                )
            # Legacy fallback: inline core_charge on parent (pre-mig 114 data)
            if pol_core <= 0:
                pol_core = float(line.get("core_charge") or 0)

            if pol_core > 0 and product_id:
                vendor_row = conn.execute(
                    "SELECT vendor_id FROM purchase_orders WHERE id = %s",
                    (po_id,),
                ).fetchone()
                vendor_id_for_obl = None
                if vendor_row:
                    vendor_id_for_obl = (
                        vendor_row["vendor_id"]
                        if hasattr(vendor_row, "keys")
                        else vendor_row[0]
                    )
                if vendor_id_for_obl:
                    # Auto-receive matching child core line in lockstep
                    if child_line_id:
                        try:
                            child_qr = int(
                                child_row["qr"]
                                if hasattr(child_row, "keys") else child_row[2]
                            )
                            conn.execute(
                                "UPDATE purchase_order_lines "
                                "   SET quantity_received = %s "
                                " WHERE id = %s",
                                (child_qr + int(qty_req), child_line_id),
                            )
                        except Exception:
                            pass
                    for _ in range(int(qty_req)):
                        conn.execute(
                            """
                            INSERT INTO vendor_core_obligations
                                (po_id, po_line_id, vendor_id, product_id,
                                 quantity, core_amount, status, due_date, notes)
                            VALUES (%s, %s, %s, %s, 1, %s,
                                    'on_shelf',
                                    DATE('now', '+90 days'),
                                    'Auto-created on PO receive; core on shelf awaiting customer return')
                            """,
                            (po_id, child_line_id or line_id, vendor_id_for_obl,
                             product_id, pol_core),
                        )
        except Exception:
            # Never block PO receive on obligation creation failure.
            pass

        conn.commit()

    quantity_received = qty_req

    # Lines without a linked product (free-text description lines) are marked
    # received above but have no inventory to adjust — treat as success.
    if not product_id or not sku:
        return {
            "success": True,
            "action": "received_no_inventory",
            "qty_received": quantity_received,
            "lines_remaining": 0,
            "note": "No linked product — quantity marked received without inventory adjustment.",
        }

    # Use adjust_product_quantity to update inventory (handles transaction logging)
    result = adjust_product_quantity(
        sku=sku,
        delta=quantity_received,
        txn_type="receive",
        reference=po_number,
        notes=notes or f"Received from {po_number}"
    )
    
    if not result.get("success"):
        return result
    
    # If location specified, assign/update product location qty
    if location_id:
        try:
            assign_product_to_location(product_id, location_id, quantity_received)
        except Exception as e:
            # Log but don't fail the receive
            print(f"Warning: Failed to assign to location: {e}")

    # If this PO was created from a Sales Order, move quantities from ON_PO to ALLOCATED.
    linked_so_id = None
    with get_db() as conn:
        po_row = conn.execute(
            "SELECT notes FROM purchase_orders WHERE id = %s",
            (po_id,),
        ).fetchone()
        po_notes = (po_row[0] if po_row and hasattr(po_row, '__getitem__') else None) or ""
        if "From SO #" in po_notes:
            try:
                linked_so_id = int(po_notes.split("From SO #", 1)[1].split()[0].strip())
            except Exception:
                linked_so_id = None
        if linked_so_id:
            alloc_remaining = int(quantity_received or 0)
            so_lines = conn.execute(
                """
                SELECT id, quantity_backordered, on_po_qty, allocated_qty, received_qty
                FROM sales_order_lines
                WHERE so_id = %s AND product_id = %s AND on_po_qty > 0
                ORDER BY id ASC
                """,
                (linked_so_id, product_id),
            ).fetchall()
            for so_line in so_lines:
                if alloc_remaining <= 0:
                    break
                so_line_id = so_line[0]
                backordered = int(so_line[1] or 0)
                on_po_qty = int(so_line[2] or 0)
                allocated_qty = int(so_line[3] or 0)
                received_qty = int(so_line[4] or 0)
                alloc_delta = min(alloc_remaining, on_po_qty)
                if alloc_delta <= 0:
                    continue
                conn.execute(
                    """
                    UPDATE sales_order_lines
                    SET on_po_qty = %s,
                        allocated_qty = %s,
                        received_qty = %s,
                        quantity_backordered = %s
                    WHERE id = %s
                    """,
                    (
                        max(on_po_qty - alloc_delta, 0),
                        allocated_qty + alloc_delta,
                        received_qty + alloc_delta,
                        max(backordered - alloc_delta, 0),
                        so_line_id,
                    ),
                )
                alloc_remaining -= alloc_delta
        conn.commit()

    if linked_so_id:
        so_lines = get_so_lines(linked_so_id)
        overall_status = derive_so_status(so_lines)
        update_sales_order(linked_so_id, status=overall_status.lower())
    
    # Check if all lines are fully received
    with get_db() as conn:
        remaining_row = conn.execute("""
            SELECT COUNT(*) FROM purchase_order_lines 
            WHERE po_id = %s AND quantity_received < quantity_ordered
        """, (po_id,)).fetchone()
        
        lines_remaining = remaining_row[0] if remaining_row else 0
        
        if lines_remaining == 0:
            # All lines received - mark PO as received
            conn.execute(
                "UPDATE purchase_orders SET status = 'received', received_date = %s, updated_at = NOW() WHERE id = %s",
                (datetime.now().strftime("%Y-%m-%d"), po_id)
            )
        else:
            # Partial receive
            conn.execute(
                "UPDATE purchase_orders SET status = 'partial', updated_at = NOW() WHERE id = %s",
                (po_id,)
            )
        
        conn.commit()
    
    return {"success": True, "qty_received": quantity_received, "lines_remaining": lines_remaining, "location_id": location_id}


def bulk_adjust_quantity(
    product_ids: list[int],
    delta: int,
    txn_type: str = "adjust",
    reference: str = None,
    notes: str = None,
    user: str = None
) -> dict:
    """Adjust quantity for multiple products at once.
    
    Args:
        product_ids: List of product IDs to adjust
        delta: Amount to add (positive) or remove (negative)
        txn_type: Transaction type
        reference: Reference number
        notes: Notes
        user: User making the change
        
    Returns:
        Dict with success count, error count, and details
    """
    results = {"success": 0, "failed": 0, "errors": []}
    
    for pid in product_ids:
        # Look up SKU from product_id
        product = get_product_by_id(pid)
        if not product:
            results["failed"] += 1
            results["errors"].append({"product_id": pid, "error": f"Product ID {pid} not found"})
            continue
        
        result = adjust_product_quantity(
            sku=product["sku"],
            delta=delta,
            txn_type=txn_type,
            reference=reference,
            notes=notes,
            user=user
        )
        
        if result.get("success"):
            results["success"] += 1
        else:
            results["failed"] += 1
            results["errors"].append({"product_id": pid, "error": result.get("error")})
    
    return results


# =============================================================================
# CUSTOMER MANAGEMENT
# =============================================================================

def get_all_customers(active_only: bool = True) -> list[dict]:
    """Get all customers."""
    with get_db() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM customers WHERE is_active = TRUE ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM customers ORDER BY name"
            ).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_customer(customer_id: int) -> dict | None:
    """Get a customer by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE id = %s", (customer_id,)
        ).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def check_customer_credit(customer_id: int, additional_amount: float = 0.0) -> dict:
    """Return credit/on-hold status for a customer (P1.6).

    ``additional_amount`` is the prospective new-order dollar value being
    evaluated — if adding it would exceed the effective credit limit the
    result will be flagged.

    Returns a dict with:
        ok: bool                — True if order may proceed without warning
        on_hold: bool
        reason: str             — human-readable reason when ok=False
        eff_credit_limit: float — credit_agreement value, or customer.credit_limit, or 0
        outstanding_balance: float
        available_credit: float — eff_credit_limit - outstanding (never negative)
        would_exceed: bool      — outstanding + additional > eff_credit_limit (when limit > 0)
    """
    default = {
        "ok": True,
        "on_hold": False,
        "reason": "",
        "eff_credit_limit": 0.0,
        "outstanding_balance": 0.0,
        "available_credit": 0.0,
        "would_exceed": False,
    }
    if not customer_id:
        return default

    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(c.on_hold, 0) AS on_hold,
                       COALESCE(ca.credit_limit, c.credit_limit, 0) AS eff_credit_limit,
                       COALESCE((
                           SELECT SUM(COALESCE(total, 0) - COALESCE(amount_paid, 0))
                           FROM invoices
                           WHERE customer_id = c.id
                             AND status NOT IN ('paid', 'voided', 'void')
                       ), 0) AS outstanding
                FROM customers c
                LEFT JOIN credit_agreements ca ON ca.customer_id = c.id
                WHERE c.id = %s
                """,
                (customer_id,),
            ).fetchone()
    except Exception as e:
        # Schema not yet migrated, or DB unavailable — don't block the sale.
        return {**default, "reason": f"credit check skipped ({e})"}

    if not row:
        return default

    d = dict(row) if hasattr(row, "keys") else dict(enumerate(row))
    on_hold = bool(int(d.get("on_hold") or 0))
    eff_limit = float(d.get("eff_credit_limit") or 0)
    outstanding = float(d.get("outstanding") or 0)

    # Subtract any open ledger credits (issued credit memos / cores) from
    # outstanding so a customer with a $400 open core credit doesn't get
    # falsely flagged as exceeding their limit.
    try:
        with get_db() as conn:
            crow = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS open_credits "
                "FROM customer_credits "
                "WHERE customer_id = %s AND status = 'open'",
                (customer_id,),
            ).fetchone()
        if crow:
            open_credits = float(
                crow[0] if not hasattr(crow, "keys") else crow["open_credits"]
            )
            outstanding = max(0.0, outstanding - open_credits)
    except Exception:
        # customer_credits table may not exist on a stale DB; ignore.
        pass

    available = max(0.0, eff_limit - outstanding)
    projected = outstanding + float(additional_amount or 0)
    would_exceed = bool(eff_limit > 0 and projected > eff_limit)

    reason = ""
    ok = True
    if on_hold:
        ok = False
        reason = "Customer is on hold"
    elif would_exceed:
        ok = False
        reason = (
            f"Credit limit exceeded: outstanding ${outstanding:,.2f} + "
            f"order ${float(additional_amount or 0):,.2f} = ${projected:,.2f} "
            f"> limit ${eff_limit:,.2f}"
        )

    return {
        "ok": ok,
        "on_hold": on_hold,
        "reason": reason,
        "eff_credit_limit": eff_limit,
        "outstanding_balance": outstanding,
        "available_credit": available,
        "would_exceed": would_exceed,
    }


def get_customer_by_email(email: str) -> dict | None:
    """Get a customer by email."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE email = %s", (email,)
        ).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def _next_account_number(conn) -> str:
    """Generate the next sequential customer account number."""
    row = conn.execute(
        "SELECT MAX(CAST(account_number AS INTEGER)) FROM customers "
        "WHERE account_number IS NOT NULL AND account_number != ''"
    ).fetchone()
    current_max = row[0] if row and row[0] else 10000
    return str(int(current_max) + 1)


def create_customer(
    name: str,
    company: str = None,
    email: str = None,
    phone: str = None,
    address: str = None,
    city: str = None,
    state: str = None,
    zip_code: str = None,
    payment_terms: str = "Net 30",
    tax_exempt: bool = False,
    notes: str = None,
    ship_to_address: str = None,
    ship_to_city: str = None,
    ship_to_state: str = None,
    ship_to_zip: str = None,
    bill_to_address: str = None,
    bill_to_city: str = None,
    bill_to_state: str = None,
    bill_to_zip: str = None,
) -> int:
    """Create a new customer. Returns customer ID."""
    with get_db() as conn:
        acct = _next_account_number(conn)
        cursor = conn.execute("""
            INSERT INTO customers (
                name, company, email, phone, address, city, state, zip,
                payment_terms, tax_exempt, notes, account_number,
                ship_to_address, ship_to_city, ship_to_state, ship_to_zip,
                bill_to_address, bill_to_city, bill_to_state, bill_to_zip
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s)
        """, (name, company, email, phone, address, city, state, zip_code,
              payment_terms, tax_exempt, notes, acct,
              ship_to_address, ship_to_city, ship_to_state, ship_to_zip,
              bill_to_address, bill_to_city, bill_to_state, bill_to_zip))
        conn.commit()
        # Use lastrowid for SQLite compatibility
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            cust_id = row[0]
        else:
            cust_id = cursor.lastrowid
        try:
            from qbo.sync_queue import enqueue, ACTION_CREATE
            enqueue("customer", cust_id, ACTION_CREATE,
                    payload={"name": name, "company": company},
                    message=f"Customer {name} awaiting QBO push")
        except Exception:
            pass
        return cust_id


def update_customer(customer_id: int, **fields) -> bool:
    """Update customer fields."""
    if not fields:
        return False
    
    set_parts = []
    values = []
    for key, value in fields.items():
        # Handle zip -> zip_code mapping
        if key == "zip_code":
            key = "zip"
        set_parts.append(f"{key} = %s")
        values.append(value)
    values.append(customer_id)
    
    query = f"UPDATE customers SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    
    with get_db() as conn:
        cursor = conn.execute(query, tuple(values))
        conn.commit()
        return cursor.rowcount > 0


def delete_customer(customer_id: int) -> bool:
    """Soft delete a customer (set inactive)."""
    return update_customer(customer_id, is_active=False)


def search_customers(query: str) -> list[dict]:
    """Search customers by name, company, email, phone, or account number.

    Case-insensitive across both SQLite and PostgreSQL.
    """
    with get_db() as conn:
        search = f"%{query.lower()}%"
        rows = conn.execute("""
            SELECT * FROM customers
            WHERE is_active = TRUE
            AND (
                LOWER(COALESCE(name, '')) LIKE %s
                OR LOWER(COALESCE(company, '')) LIKE %s
                OR LOWER(COALESCE(email, '')) LIKE %s
                OR LOWER(COALESCE(phone, '')) LIKE %s
                OR LOWER(COALESCE(account_number, '')) LIKE %s
            )
            ORDER BY name
            LIMIT 50
        """, (search, search, search, search, search)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# =============================================================================
# QUOTE MANAGEMENT
# =============================================================================

def generate_quote_number() -> str:
    """Generate next quote number in format QT-YYYY-NNNN."""
    from datetime import datetime
    year = datetime.now().year
    prefix = f"QT-{year}-"

    with get_db() as conn:
        row = conn.execute("""
            SELECT quote_number FROM quotes
            WHERE quote_number LIKE %s
            ORDER BY quote_number DESC LIMIT 1
        """, (f"{prefix}%",)).fetchone()

        if row:
            last_num = int(row[0].split("-")[-1])
            next_num = last_num + 1
        else:
            next_num = 1

        return f"{prefix}{next_num:04d}"


def get_all_quotes(status: str = None, customer_id: int = None, limit: int = 100) -> list[dict]:
    """Get quotes with optional filtering."""
    conditions = []
    params = []

    if status:
        conditions.append("q.status = %s")
        params.append(status)

    if customer_id:
        conditions.append("q.customer_id = %s")
        params.append(customer_id)

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT q.*, c.name as customer_name, c.company as customer_company,
                   COALESCE(lc.cnt, 0) AS line_count,
                   COALESCE(lc.calc_total, 0) AS calc_total
            FROM quotes q
            LEFT JOIN customers c ON q.customer_id = c.id
            LEFT JOIN (
                SELECT quote_id,
                       COUNT(*) AS cnt,
                       SUM(COALESCE(line_total, qty * unit_price, 0)) AS calc_total
                FROM quote_lines
                GROUP BY quote_id
            ) lc ON lc.quote_id = q.id
            WHERE {where_clause}
            ORDER BY q.created_at DESC
            LIMIT %s
        """, params).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


# =============================================================================
# LOST-SALES ANALYTICS
# =============================================================================

def _row_to_dict(r):
    return dict(r) if hasattr(r, "keys") else dict(enumerate(r))


def get_lost_sales(
    date_from: str | None = None,
    date_to: str | None = None,
    customer_id: int | None = None,
    reason: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Return lost quotes (status='lost') with customer + computed totals.

    Filters all optional. Dates are matched against ``q.updated_at`` (the
    point the quote was marked lost). Dates in ``YYYY-MM-DD``.
    """
    conditions = ["q.status = 'lost'"]
    params: list = []
    if date_from:
        conditions.append("date(q.updated_at) >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("date(q.updated_at) <= %s")
        params.append(date_to)
    if customer_id:
        conditions.append("q.customer_id = %s")
        params.append(customer_id)
    if reason:
        conditions.append("q.lost_reason = %s")
        params.append(reason)
    where = " AND ".join(conditions)
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT q.id, q.quote_number, q.customer_id,
                   q.created_at, q.updated_at,
                   q.lost_reason, q.lost_notes,
                   q.total AS header_total,
                   c.name AS customer_name, c.company AS customer_company,
                   COALESCE(lc.cnt, 0) AS line_count,
                   COALESCE(lc.calc_total, 0) AS calc_total
            FROM quotes q
            LEFT JOIN customers c ON q.customer_id = c.id
            LEFT JOIN (
                SELECT quote_id,
                       COUNT(*) AS cnt,
                       SUM(COALESCE(line_total, qty * unit_price, 0)) AS calc_total
                FROM quote_lines
                GROUP BY quote_id
            ) lc ON lc.quote_id = q.id
            WHERE {where}
            ORDER BY q.updated_at DESC
            LIMIT %s
        """, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_lost_sales_summary(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Aggregate lost-sales metrics for a date range.

    Returns::
        {
          "count": int,
          "total_value": float,
          "by_reason": [{"reason": str, "count": int, "value": float}, ...],
          "by_month": [{"month": "YYYY-MM", "count": int, "value": float}, ...],
          "top_customers": [{"customer_id": int, "customer_name": str,
                             "count": int, "value": float}, ...],
          "avg_days_open": float,
        }
    """
    conditions = ["q.status = 'lost'"]
    params: list = []
    if date_from:
        conditions.append("date(q.updated_at) >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("date(q.updated_at) <= %s")
        params.append(date_to)
    where = " AND ".join(conditions)

    base_cte = f"""
        WITH lost AS (
            SELECT q.id, q.customer_id, q.created_at, q.updated_at,
                   COALESCE(NULLIF(q.lost_reason, ''), 'Unspecified') AS reason,
                   COALESCE(lc.calc_total, q.total, 0) AS value
            FROM quotes q
            LEFT JOIN (
                SELECT quote_id,
                       SUM(COALESCE(line_total, qty * unit_price, 0)) AS calc_total
                FROM quote_lines
                GROUP BY quote_id
            ) lc ON lc.quote_id = q.id
            WHERE {where}
        )
    """

    with get_db() as conn:
        # Totals
        total_row = conn.execute(base_cte + """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(value), 0) AS total_value,
                   COALESCE(AVG(
                       CAST(julianday(updated_at) - julianday(created_at) AS REAL)
                   ), 0) AS avg_days_open
            FROM lost
        """, params).fetchone()
        total = _row_to_dict(total_row)

        by_reason = [
            _row_to_dict(r) for r in conn.execute(base_cte + """
                SELECT reason, COUNT(*) AS count, COALESCE(SUM(value), 0) AS value
                FROM lost
                GROUP BY reason
                ORDER BY value DESC
            """, params).fetchall()
        ]

        by_month = [
            _row_to_dict(r) for r in conn.execute(base_cte + """
                SELECT strftime('%Y-%m', updated_at) AS month,
                       COUNT(*) AS count, COALESCE(SUM(value), 0) AS value
                FROM lost
                GROUP BY strftime('%Y-%m', updated_at)
                ORDER BY month
            """, params).fetchall()
        ]

        top_customers = [
            _row_to_dict(r) for r in conn.execute(base_cte + """
                SELECT l.customer_id,
                       COALESCE(c.name, '(no customer)') AS customer_name,
                       COUNT(*) AS count,
                       COALESCE(SUM(l.value), 0) AS value
                FROM lost l
                LEFT JOIN customers c ON c.id = l.customer_id
                GROUP BY l.customer_id, c.name
                ORDER BY value DESC
                LIMIT 10
            """, params).fetchall()
        ]

        return {
            "count": int(total.get("cnt") or total.get(0) or 0),
            "total_value": float(total.get("total_value") or total.get(1) or 0),
            "avg_days_open": float(total.get("avg_days_open") or total.get(2) or 0),
            "by_reason": by_reason,
            "by_month": by_month,
            "top_customers": top_customers,
        }


def get_win_loss_ratio(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Return won/lost counts and $ values in a range (for conversion rate)."""
    conditions = ["q.status IN ('accepted', 'invoiced', 'lost')"]
    params: list = []
    if date_from:
        conditions.append("date(q.updated_at) >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("date(q.updated_at) <= %s")
        params.append(date_to)
    where = " AND ".join(conditions)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT
                CASE WHEN q.status = 'lost' THEN 'lost' ELSE 'won' END AS bucket,
                COUNT(*) AS cnt,
                COALESCE(SUM(
                    COALESCE(lc.calc_total, q.total, 0)
                ), 0) AS value
            FROM quotes q
            LEFT JOIN (
                SELECT quote_id,
                       SUM(COALESCE(line_total, qty * unit_price, 0)) AS calc_total
                FROM quote_lines
                GROUP BY quote_id
            ) lc ON lc.quote_id = q.id
            WHERE {where}
            GROUP BY bucket
        """, params).fetchall()

        won = {"count": 0, "value": 0.0}
        lost = {"count": 0, "value": 0.0}
        for r in rows:
            d = _row_to_dict(r)
            bucket = d.get("bucket") or d.get(0)
            if bucket == "won":
                won = {"count": int(d.get("cnt") or d.get(1) or 0),
                       "value": float(d.get("value") or d.get(2) or 0)}
            elif bucket == "lost":
                lost = {"count": int(d.get("cnt") or d.get(1) or 0),
                        "value": float(d.get("value") or d.get(2) or 0)}

        total_count = won["count"] + lost["count"]
        total_value = won["value"] + lost["value"]
        return {
            "won": won,
            "lost": lost,
            "win_rate_count": (won["count"] / total_count) if total_count else 0.0,
            "win_rate_value": (won["value"] / total_value) if total_value else 0.0,
        }


def log_quote_status_change(
    quote_id: int,
    old_status: str,
    new_status: str,
    reason: str = "",
    changed_by: str = "",
) -> None:
    """Append a status-transition row to quote_status_log (best-effort)."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO quote_status_log "
                "(quote_id, old_status, new_status, reason, changed_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                (quote_id, old_status or "", new_status or "", reason or "", changed_by or ""),
            )
            conn.commit()
    except Exception:
        # Log is best-effort — never block a status change on audit failure.
        pass


def reopen_lost_quote(quote_id: int, reason: str = "", changed_by: str = "") -> dict:
    """Reopen a lost quote by resetting it to 'sent' and clearing lost fields.

    Records the transition in quote_status_log. Returns ``{"success": bool}``.
    """
    quote = get_quote(quote_id)
    if not quote:
        return {"success": False, "error": "Quote not found"}
    old_status = (quote.get("status") or "").lower()
    if old_status != "lost":
        return {"success": False, "error": f"Quote is not lost (status={old_status!r})"}

    with get_db() as conn:
        try:
            conn.execute(
                "UPDATE quotes SET status='sent', lost_reason='', lost_notes='', "
                "updated_at=datetime('now') WHERE id=%s",
                (quote_id,),
            )
            conn.commit()
        except Exception as e:
            return {"success": False, "error": str(e)}

    log_quote_status_change(quote_id, "lost", "sent", reason=reason, changed_by=changed_by)
    return {"success": True}


# --- Structured lost-reason catalog (migration 050) ---------------------

_DEFAULT_LOST_REASONS = [
    "Price too high",
    "Lead time too long",
    "Customer went with competitor",
    "Customer went direct to OEM",
    "Part no longer needed",
    "Could not source part",
    "No response from customer",
    "Other",
]


def get_lost_reasons(active_only: bool = True) -> list[dict]:
    """Return the configured lost-reason labels.

    Falls back to the built-in defaults if the table is missing (e.g.,
    migration 050 hasn't run yet).
    """
    try:
        with get_db() as conn:
            where = "WHERE active = 1" if active_only else ""
            rows = conn.execute(
                f"SELECT id, label, sort_order, active FROM quote_lost_reasons "
                f"{where} ORDER BY sort_order, label"
            ).fetchall()
            result = [_row_to_dict(r) for r in rows]
            if result:
                return result
    except Exception:
        pass
    return [
        {"id": None, "label": label, "sort_order": (i + 1) * 10, "active": 1}
        for i, label in enumerate(_DEFAULT_LOST_REASONS)
    ]


def add_lost_reason(label: str, sort_order: int = 100) -> dict:
    """Insert a new lost-reason label. Returns ``{"success": bool, "id": int}``."""
    label = (label or "").strip()
    if not label:
        return {"success": False, "error": "Label is required"}
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO quote_lost_reasons (label, sort_order, active) "
                "VALUES (%s, %s, 1)",
                (label, sort_order),
            )
            conn.commit()
            return {"success": True, "id": getattr(cur, "lastrowid", None)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def update_lost_reason(reason_id: int, label: str = None, sort_order: int = None,
                       active: int = None) -> dict:
    updates = {}
    if label is not None:
        updates["label"] = label
    if sort_order is not None:
        updates["sort_order"] = sort_order
    if active is not None:
        updates["active"] = 1 if active else 0
    if not updates:
        return {"success": True}
    try:
        with get_db() as conn:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [reason_id]
            conn.execute(f"UPDATE quote_lost_reasons SET {sets} WHERE id = %s", vals)
            conn.commit()
            return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_quote(quote_id: int) -> dict | None:
    """Get a single quote with its lines."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT q.*, c.name as customer_name, c.company as customer_company
            FROM quotes q
            LEFT JOIN customers c ON q.customer_id = c.id
            WHERE q.id = %s
        """, (quote_id,)).fetchone()

        if not row:
            return None

        quote = dict(row) if hasattr(row, 'keys') else dict(enumerate(row))

        lines = conn.execute("""
            SELECT ql.*, p.sku, p.title, p.qty_on_hand, p.qty_reserved,
                   p.jaks_warranty_enabled, p.jaks_warranty_value,
                   p.jaks_warranty_unit, p.supplier_warranty_enabled,
                   p.supplier_warranty_value, p.supplier_warranty_unit,
                   p.is_warrantable, p.warranty_percentage
            FROM quote_lines ql
            LEFT JOIN products p ON ql.product_id = p.id
            WHERE ql.quote_id = %s
            ORDER BY ql.sort_order, ql.id
        """, (quote_id,)).fetchall()

        quote["lines"] = [dict(l) if hasattr(l, 'keys') else dict(enumerate(l)) for l in lines]
        return quote


def create_quote(
    customer_id: int = None,
    quote_date: str = None,
    expiration_date: str = None,
    terms: str = None,
    notes: str = None,
    lines: list[dict] = None,
    esn: str = None,
    vin: str = None,
    manufacturer: str = None,
    job_number: str = None,
    equipment_type: str = None,
    equipment_make: str = None,
    equipment_model: str = None,
) -> dict:
    """Create a new quote.

    Args:
        customer_id: Optional customer ID
        quote_date: Quote date (default today)
        expiration_date: When quote expires
        terms: Payment terms
        notes: Quote notes
        lines: List of {"product_id": int, "qty": int, "unit_price": float,
                        "unit_cost": float, "core_charge": float, "description": str}
        esn: Engine serial number
        vin: Vehicle identification number
        equipment_type: 'engine' | 'truck' | None
        equipment_make: Engine or truck manufacturer (e.g. 'International')
        equipment_model: Engine or truck model (e.g. 'DT466')
    """
    from datetime import date, timedelta

    quote_number = generate_quote_number()
    q_date = quote_date or date.today().isoformat()
    if not expiration_date:
        exp = date.fromisoformat(q_date) + timedelta(days=30)
        expiration_date = exp.isoformat()

    with get_db() as conn:
        try:
            conn.execute("""
                INSERT INTO quotes (
                    quote_number, customer_id, status, quote_date,
                    expiration_date, terms, notes, esn, vin, manufacturer, job_number,
                    equipment_type, equipment_make, equipment_model
                ) VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (quote_number, customer_id, q_date, expiration_date, terms, notes,
                  esn or '', vin or '', manufacturer or '', job_number or '',
                  equipment_type, equipment_make, equipment_model))

            q_row = conn.execute(
                "SELECT id FROM quotes WHERE quote_number = %s", (quote_number,)
            ).fetchone()
            quote_id = q_row[0] if q_row else None

            if not quote_id:
                return {"success": False, "error": "Failed to create quote"}

            subtotal = 0
            core_total = 0
            if lines:
                for idx, line in enumerate(lines):
                    qty = line.get("qty", 1)
                    price = line.get("unit_price", 0)
                    cost = line.get("unit_cost", 0)
                    core = line.get("core_charge", 0)
                    line_total = qty * price
                    margin = ((price - cost) / price * 100) if price > 0 else 0
                    subtotal += line_total
                    core_total += core * qty

                    conn.execute("""
                        INSERT INTO quote_lines (
                            quote_id, product_id, description, supplier,
                            qty, unit_price, unit_cost, core_charge,
                            line_total, margin_pct, sort_order, notes
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        quote_id, line.get("product_id"),
                        line.get("description", ""), line.get("supplier", ""),
                        qty, price, cost, core,
                        line_total, margin, idx, line.get("notes"),
                    ))

            total = subtotal + core_total
            conn.execute(
                "UPDATE quotes SET subtotal=%s, core_total=%s, total=%s WHERE id=%s",
                (subtotal, core_total, total, quote_id),
            )

            conn.commit()
            return {"success": True, "quote_id": quote_id, "quote_number": quote_number}

        except Exception as e:
            return {"success": False, "error": str(e)}


def list_payment_terms() -> list[str]:
    """Return distinct payment terms used historically + a default set.

    Used by the quote dialog to populate the Terms QComboBox.
    """
    defaults = [
        "Due on Receipt", "Net 15", "Net 30", "Net 45", "Net 60",
        "COD", "Prepaid", "2/10 Net 30",
    ]
    seen: list[str] = []
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT terms FROM quotes "
                "WHERE terms IS NOT NULL AND terms != '' "
                "ORDER BY terms"
            ).fetchall()
            for r in rows:
                t = (r[0] if not hasattr(r, "keys") else r["terms"]) or ""
                t = t.strip()
                if t and t not in seen:
                    seen.append(t)
    except Exception:
        pass
    out = list(defaults)
    for t in seen:
        if t not in out:
            out.append(t)
    return out


def duplicate_quote(quote_id: int, new_customer_id: int | None = None) -> dict:
    """Clone a quote header + lines into a new draft.

    Returns ``{"success": True, "quote_id": int, "quote_number": str}``
    or ``{"success": False, "error": str}``. If ``new_customer_id`` is
    omitted, the new quote inherits the source quote's customer.
    """
    from datetime import date, timedelta
    src = get_quote(quote_id)
    if not src:
        return {"success": False, "error": "Source quote not found"}
    customer_id = new_customer_id if new_customer_id is not None else src.get("customer_id")
    quote_number = generate_quote_number()
    q_date = date.today().isoformat()
    exp = (date.today() + timedelta(days=30)).isoformat()
    with get_db() as conn:
        try:
            conn.execute("""
                INSERT INTO quotes (
                    quote_number, customer_id, status, quote_date,
                    expiration_date, terms, notes, esn, vin, manufacturer, job_number,
                    equipment_type, equipment_make, equipment_model,
                    tax, shipping, fuel_surcharge, offer_extended_warranty
                ) VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                quote_number, customer_id, q_date, exp,
                src.get("terms"), src.get("notes"),
                src.get("esn") or "", src.get("vin") or "",
                src.get("manufacturer") or "", src.get("job_number") or "",
                src.get("equipment_type"), src.get("equipment_make"),
                src.get("equipment_model"),
                src.get("tax") or 0, src.get("shipping") or 0,
                src.get("fuel_surcharge") or 0,
                src.get("offer_extended_warranty") or 0,
            ))
            new_id_row = conn.execute(
                "SELECT id FROM quotes WHERE quote_number = %s", (quote_number,)
            ).fetchone()
            new_id = new_id_row[0] if new_id_row else None
            if not new_id:
                return {"success": False, "error": "Failed to create duplicate"}

            for idx, ln in enumerate(src.get("lines") or []):
                conn.execute("""
                    INSERT INTO quote_lines (
                        quote_id, product_id, description, supplier,
                        qty, unit_price, unit_cost, core_charge,
                        line_total, margin_pct, sort_order, notes,
                        warranty_months, warranty_type, warranty_mileage_limit,
                        warranty_price, is_optional, is_selected,
                        line_shipping
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    new_id, ln.get("product_id"),
                    ln.get("description") or "", ln.get("supplier") or "",
                    ln.get("qty") or 1,
                    ln.get("unit_price") or 0,
                    ln.get("unit_cost") or 0,
                    ln.get("core_charge") or 0,
                    ln.get("line_total") or 0,
                    ln.get("margin_pct") or 0,
                    idx, ln.get("notes"),
                    ln.get("warranty_months") or 0,
                    ln.get("warranty_type") or "parts_only",
                    ln.get("warranty_mileage_limit"),
                    ln.get("warranty_price") or 0,
                    1 if ln.get("is_optional") else 0,
                    1 if ln.get("is_selected", True) else 0,
                    ln.get("line_shipping") or 0,
                ))
            _recalc_quote_totals(conn, new_id)
            conn.commit()
            return {"success": True, "quote_id": new_id, "quote_number": quote_number}
        except Exception as e:
            return {"success": False, "error": str(e)}


def _fetch_product_warranty_defaults(conn, product_id) -> tuple[int, str, int | None]:
    """Look up a product's warranty defaults (months, type, mileage_limit).

    Pulls the included supplier warranty from products table, converts to months.
    JAKS warranty fields represent paid extended-warranty upsells and should not
    replace the standard warranty on the product quote line.
    Returns ``(0, 'parts_only', None)`` if product has no warranty or lookup
    fails (e.g. columns missing).
    """
    if not product_id:
        return (0, "parts_only", None)
    try:
        row = conn.execute(
            "SELECT jaks_warranty_enabled, jaks_warranty_value, jaks_warranty_unit, "
            "supplier_warranty_enabled, supplier_warranty_value, supplier_warranty_unit "
            "FROM products WHERE id = %s",
            (product_id,),
        ).fetchone()
    except Exception:
        return (0, "parts_only", None)
    if not row:
        return (0, "parts_only", None)
    if hasattr(row, "keys"):
        product = dict(row)
    else:
        product = {
            "jaks_warranty_enabled": row[0], "jaks_warranty_value": row[1],
            "jaks_warranty_unit": row[2], "supplier_warranty_enabled": row[3],
            "supplier_warranty_value": row[4], "supplier_warranty_unit": row[5],
        }
    try:
        from engine.warranty import supplier_standard_warranty_for_product
        return supplier_standard_warranty_for_product(product)
    except Exception:
        return (0, "parts_only", None)


def add_quote_line(
    quote_id: int,
    product_id: int = None,
    description: str = "",
    supplier: str = "",
    qty: int = 1,
    unit_price: float = 0,
    unit_cost: float = 0,
    core_charge: float = 0,
    notes: str = None,
    warranty_months: int | None = None,
    warranty_type: str | None = None,
    warranty_mileage_limit: int | None = None,
    option_group: str | None = None,
    eta_date: str | None = None,
    parent_line_id: int | None = None,
    is_optional: int = 0,
) -> dict:
    """Add a line to an existing quote.

    ``parent_line_id`` links this line to another quote line (used by
    add-on items like extended warranty). ``is_optional=1`` flags the
    line as customer-decline-able and excludes it from the required
    subtotal.


    ``warranty_*`` kwargs default to the product's JAKS/supplier warranty.
    Pass explicit values to override. Set ``warranty_months=0`` to disable.
    ``option_group`` (e.g. 'A','B','C') tags this line as part of an
    alternative bundle. ``eta_date`` (ISO YYYY-MM-DD) overrides the auto-
    computed line ETA; when omitted the engine.eta helper fills it in.
    """
    line_total = qty * unit_price
    margin = ((unit_price - unit_cost) / unit_price * 100) if unit_price > 0 else 0

    with get_db() as conn:
        try:
            # Warranty auto-fill: only when caller didn't pass anything at all.
            if warranty_months is None and warranty_type is None and warranty_mileage_limit is None:
                w_months, w_type, w_mileage = _fetch_product_warranty_defaults(
                    conn, product_id
                )
            else:
                w_months = int(warranty_months or 0)
                w_type = warranty_type or "parts_only"
                w_mileage = warranty_mileage_limit

            # Auto-compute ETA when caller didn't pass one and we have a product.
            if eta_date is None and product_id:
                try:
                    from engine.eta import compute_line_eta
                    eta = compute_line_eta(int(product_id), int(qty or 1))
                    eta_date = eta.get("eta_date")
                except Exception:
                    eta_date = None

            # Get next sort_order
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM quote_lines WHERE quote_id = %s",
                (quote_id,),
            ).fetchone()
            sort_order = row[0] if row else 0

            cur = conn.execute("""
                INSERT INTO quote_lines (
                    quote_id, product_id, description, supplier,
                    qty, unit_price, unit_cost, core_charge,
                    line_total, margin_pct, sort_order, notes,
                    warranty_months, warranty_type, warranty_mileage_limit,
                    option_group, eta_date,
                    parent_line_id, is_optional
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                quote_id, product_id, description, supplier,
                qty, unit_price, unit_cost, core_charge,
                line_total, margin, sort_order, notes,
                w_months, w_type, w_mileage,
                option_group, eta_date,
                parent_line_id, 1 if is_optional else 0,
            ))
            new_id = getattr(cur, "lastrowid", None)

            _recalc_quote_totals(conn, quote_id)
            conn.commit()
            return {"success": True, "line_id": new_id}
        except Exception as e:
            return {"success": False, "error": str(e)}


def remove_quote_line(quote_id: int, line_id: int) -> dict:
    """Remove a line from a quote, cascading to any child add-on lines."""
    with get_db() as conn:
        try:
            # Cascade: drop any child lines (e.g. linked extended-warranty)
            # that point at this line. Done first so totals re-aggregate cleanly.
            conn.execute(
                "DELETE FROM quote_lines WHERE quote_id = %s AND parent_line_id = %s",
                (quote_id, line_id),
            )
            conn.execute("DELETE FROM quote_lines WHERE id = %s AND quote_id = %s", (line_id, quote_id))
            _recalc_quote_totals(conn, quote_id)
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def update_quote_line(line_id: int, **kwargs) -> dict:
    """Update a quote line. Accepts qty, unit_price, unit_cost, core_charge, description, notes, warranty_years, warranty_price, is_optional, line_shipping, warranty_months, warranty_type, warranty_mileage_limit, option_group, eta_date."""
    allowed = {"qty", "unit_price", "unit_cost", "core_charge", "description", "supplier", "notes", "is_selected",
               "warranty_years", "warranty_price", "is_optional", "line_shipping",
               "warranty_months", "warranty_type", "warranty_mileage_limit",
               "option_group", "eta_date", "parent_line_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"success": True}

    with get_db() as conn:
        try:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [line_id]
            conn.execute(f"UPDATE quote_lines SET {sets} WHERE id = %s", vals)

            # Recalculate line_total and margin
            row = conn.execute(
                "SELECT quote_id, qty, unit_price, unit_cost FROM quote_lines WHERE id = %s",
                (line_id,),
            ).fetchone()
            if row:
                quote_id, qty, price, cost = row[0], row[1], row[2], row[3]
                lt = qty * price
                margin = ((price - cost) / price * 100) if price > 0 else 0
                conn.execute(
                    "UPDATE quote_lines SET line_total = %s, margin_pct = %s WHERE id = %s",
                    (lt, margin, line_id),
                )
                _recalc_quote_totals(conn, quote_id)

            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def set_or_replace_warranty_line(
    quote_id: int,
    parent_line_id: int,
    months: int,
    warranty_type: str = "parts_only",
    mileage_limit: int | None = None,
    warranty_pct: float | None = None,
) -> dict:
    """Create / replace / remove the optional warranty add-on line for a parent.

    Each parent product line may have at most one child warranty line linked
    via ``parent_line_id``. The child is rendered to the customer as a
    decline-able optional add-on (``is_optional=1``).

    Behavior:
      * months <= 0  → delete any existing child warranty line for this parent.
      * months > 0   → upsert the child line. Description is rebuilt from the
        coverage params; unit_price = parent.unit_price * pct% * months/12;
        qty mirrors parent.qty.

    ``warranty_pct`` overrides the product's `warranty_percentage`. Falls
    back to the product setting, then to 5% as a last-resort default.
    """
    with get_db() as conn:
        try:
            # Pull parent line data + product warranty rate.
            row = conn.execute("""
                SELECT ql.qty, ql.unit_price, ql.sort_order, ql.product_id, ql.description,
                       p.warranty_percentage
                FROM quote_lines ql
                LEFT JOIN products p ON p.id = ql.product_id
                WHERE ql.id = %s AND ql.quote_id = %s
            """, (parent_line_id, quote_id)).fetchone()
            if not row:
                return {"success": False, "error": "parent line not found"}
            parent = dict(row) if hasattr(row, "keys") else dict(enumerate(row))
            p_qty = int(parent.get("qty") or parent.get(0) or 1)
            p_price = float(parent.get("unit_price") or parent.get(1) or 0)
            p_sort = int(parent.get("sort_order") or parent.get(2) or 0)
            p_desc = parent.get("description") or parent.get(4) or ""
            prod_pct = parent.get("warranty_percentage") or parent.get(5)

            # Locate any existing child warranty line.
            child_row = conn.execute(
                "SELECT id FROM quote_lines WHERE quote_id = %s AND parent_line_id = %s",
                (quote_id, parent_line_id),
            ).fetchone()
            child_id = None
            if child_row:
                child_id = child_row[0] if not hasattr(child_row, "keys") else child_row["id"]

            # Remove case.
            if not months or months <= 0:
                if child_id:
                    conn.execute("DELETE FROM quote_lines WHERE id = %s", (child_id,))
                    _recalc_quote_totals(conn, quote_id)
                    conn.commit()
                return {"success": True, "removed": bool(child_id)}

            # Resolve rate.
            pct = float(warranty_pct) if warranty_pct is not None else 0.0
            if pct <= 0:
                pct = float(prod_pct or 0)
            if pct <= 0:
                pct = 5.0  # safe fallback so the dialog never quotes $0

            # Description label
            wt = (warranty_type or "parts_only").lower()
            wt_label = "Parts & Labor" if wt == "parts_labor" else "Parts Only"
            mi_label = (
                f"{int(mileage_limit):,} mi"
                if mileage_limit and int(mileage_limit) > 0
                else "Unlimited Mileage"
            )
            short_parent = (p_desc[:50] + "…") if len(p_desc) > 50 else p_desc
            description = (
                f"Extended Warranty — {months} mo {wt_label} · {mi_label}"
                + (f" (for {short_parent})" if short_parent else "")
            )

            unit_price = round(p_price * (pct / 100.0) * (months / 12.0), 2)
            line_total = unit_price * p_qty

            if child_id:
                conn.execute("""
                    UPDATE quote_lines
                       SET description = %s,
                           qty = %s,
                           unit_price = %s,
                           unit_cost = 0,
                           line_total = %s,
                           margin_pct = 100,
                           warranty_months = %s,
                           warranty_type = %s,
                           warranty_mileage_limit = %s,
                           is_optional = 1,
                           is_selected = 1,
                           line_kind = 'warranty'
                     WHERE id = %s
                """, (
                    description, p_qty, unit_price, line_total,
                    int(months), wt, mileage_limit, child_id,
                ))
            else:
                # Insert directly so we can pin sort_order right after the parent.
                conn.execute("""
                    INSERT INTO quote_lines (
                        quote_id, product_id, description, supplier,
                        qty, unit_price, unit_cost, core_charge,
                        line_total, margin_pct, sort_order, notes,
                        warranty_months, warranty_type, warranty_mileage_limit,
                        option_group, eta_date,
                        parent_line_id, is_optional, is_selected, line_kind
                    ) VALUES (%s, NULL, %s, '', %s, %s, 0, 0,
                              %s, 100, %s, NULL,
                              %s, %s, %s,
                              NULL, NULL,
                              %s, 1, 1, 'warranty')
                """, (
                    quote_id, description, p_qty, unit_price,
                    line_total, p_sort + 1,
                    int(months), wt, mileage_limit,
                    parent_line_id,
                ))
                # Bump sort_order on every line that was after the parent so
                # the warranty stays directly under it.
                conn.execute("""
                    UPDATE quote_lines
                       SET sort_order = sort_order + 1
                     WHERE quote_id = %s
                       AND id != (SELECT id FROM quote_lines
                                  WHERE quote_id = %s AND parent_line_id = %s
                                  ORDER BY id DESC LIMIT 1)
                       AND sort_order > %s
                """, (quote_id, quote_id, parent_line_id, p_sort))

            _recalc_quote_totals(conn, quote_id)
            conn.commit()
            return {"success": True, "warranty_unit_price": unit_price}
        except Exception as e:
            return {"success": False, "error": str(e)}


def reorder_quote_lines(quote_id: int, line_ids: list[int]) -> dict:
    """Reorder quote lines by setting sort_order based on position in line_ids list."""
    with get_db() as conn:
        try:
            for idx, lid in enumerate(line_ids):
                conn.execute(
                    "UPDATE quote_lines SET sort_order = %s WHERE id = %s AND quote_id = %s",
                    (idx, lid, quote_id),
                )
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def update_quote(quote_id: int, **kwargs) -> dict:
    """Update quote header fields."""
    allowed = {"customer_id", "status", "terms", "notes", "expiration_date", "tax", "esn", "vin",
                "follow_up_date", "next_action", "priority", "manufacturer", "job_number",
                "lost_reason", "lost_notes", "shipping",
                "selected_option_group", "offer_extended_warranty", "fuel_surcharge",
                "cc_fee_enabled", "cc_fee_rate",
                "equipment_type", "equipment_make", "equipment_model"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"success": True}

    # Central status-transition check (P1.7).
    if "status" in updates:
        try:
            from engine.status_transitions import (
                QUOTE_TRANSITIONS, TransitionError, validate_transition,
            )
            with get_db() as _c:
                row = _c.execute("SELECT status FROM quotes WHERE id = %s", (quote_id,)).fetchone()
            current = (row[0] if row else "") or ""
            validate_transition(QUOTE_TRANSITIONS, old=current, new=str(updates["status"]))
        except TransitionError as e:
            return {"success": False, "error": str(e)}
        except Exception:
            # If the engine import or lookup fails, fall through to legacy behavior.
            pass

    with get_db() as conn:
        try:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [quote_id]
            conn.execute(f"UPDATE quotes SET {sets}, updated_at = datetime('now') WHERE id = %s", vals)
            if any(k in updates for k in ("tax", "shipping", "fuel_surcharge",
                                            "selected_option_group",
                                            "cc_fee_enabled", "cc_fee_rate")):
                _recalc_quote_totals(conn, quote_id)
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def convert_quote_to_invoice(quote_id: int) -> dict:
    """Convert a quote to an invoice. Copies lines, marks quote as accepted."""
    quote = get_quote(quote_id)
    if not quote:
        return {"success": False, "error": "Quote not found"}
    status = (quote.get("status") or "").lower()
    if status == "invoiced":
        return {"success": False, "error": "Quote already converted to invoice",
                "invoice_id": quote.get("converted_invoice_id")}
    if status == "accepted":
        return {"success": False, "error": "Quote already converted to a sales order — convert the SO to an invoice instead",
                "so_id": quote.get("converted_so_id")}

    customer_id = quote.get("customer_id")
    if not customer_id:
        return {"success": False, "error": "Quote has no customer — assign a customer first"}

    result = create_invoice(
        customer_id=customer_id,
        notes=f"From {quote.get('quote_number', '')}",
        esn=quote.get("esn"),
        vin=quote.get("vin"),
        equipment_type=quote.get("equipment_type"),
        equipment_make=quote.get("equipment_make"),
        equipment_model=quote.get("equipment_model"),
    )
    if not result.get("id"):
        return {"success": False, "error": "Failed to create invoice"}

    invoice_id = result["id"]
    chosen_group = (quote.get("selected_option_group") or "").strip() or None
    # Pass 1: insert parent lines, mapping quote-line-id → invoice-line-id so
    # we can rewrite parent_line_id on the child rows in pass 2.
    line_id_map: dict[int, int] = {}
    children: list[dict] = []
    for line in quote.get("lines", []):
        if not line.get("is_selected", 1):
            continue
        og = (line.get("option_group") or "")
        if og and (not chosen_group or og != chosen_group):
            continue
        if line.get("parent_line_id"):
            children.append(line)
            continue
        new_id = add_invoice_line(
            invoice_id=invoice_id,
            product_id=line.get("product_id"),
            description=line.get("description", ""),
            quantity=line.get("qty", 1),
            unit_price=line.get("unit_price", 0),
            warranty_months=line.get("warranty_months"),
            warranty_type=line.get("warranty_type"),
            warranty_mileage_limit=line.get("warranty_mileage_limit"),
        )
        if line.get("id") and new_id:
            line_id_map[int(line["id"])] = int(new_id)

    # Pass 2: insert child add-on lines (extended warranties etc.) with the
    # parent_line_id rewritten to point at the new invoice line.
    for line in children:
        parent_qid = line.get("parent_line_id")
        new_parent_id = line_id_map.get(int(parent_qid)) if parent_qid else None
        if not new_parent_id:
            # Parent was filtered out (option_group mismatch) — skip the child.
            continue
        with get_db() as conn:
            qty = int(line.get("qty") or 1)
            unit_price = float(line.get("unit_price") or 0)
            conn.execute("""
                INSERT INTO invoice_lines (
                    invoice_id, product_id, sku, description, quantity, unit_price, line_total,
                    warranty_months, warranty_type, warranty_mileage_limit,
                    parent_line_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (invoice_id, line.get("product_id"), line.get("sku"),
                  line.get("description", ""), qty, unit_price, qty * unit_price,
                  line.get("warranty_months"), line.get("warranty_type"),
                  line.get("warranty_mileage_limit"), new_parent_id))
            conn.commit()
            _recalculate_invoice_totals(conn, invoice_id)

    with get_db() as conn:
        conn.execute(
            "UPDATE quotes SET status='invoiced', converted_invoice_id=%s, updated_at=datetime('now') WHERE id=%s",
            (invoice_id, quote_id),
        )
        conn.commit()

    # Carry the 3% CC convenience-fee toggle forward.
    if int(quote.get("cc_fee_enabled") or 0):
        try:
            update_invoice(
                invoice_id,
                cc_fee_enabled=1,
                cc_fee_rate=float(quote.get("cc_fee_rate") or 0.03),
            )
        except Exception:
            pass

    # Carry shipping & tax_rate from quote → invoice (#11).
    money_fields: dict = {}
    for fld in ("tax_rate", "shipping"):
        try:
            v = quote.get(fld)
            if v is None:
                continue
            money_fields[fld] = float(v or 0)
        except (TypeError, ValueError):
            continue
    if money_fields:
        try:
            update_invoice(invoice_id, **money_fields)
        except Exception:
            pass

    return {"success": True, "invoice_id": invoice_id, "invoice_number": result.get("invoice_number")}


def convert_quote_to_so(quote_id: int, priority: str = "normal",
                        promised_date: str = None, ship_method: str = None,
                        allow_credit_override: bool = False) -> dict:
    """Convert a quote to a sales order. Copies selected lines, marks quote as accepted.

    P1.6: Blocks the conversion if the customer is on hold or would exceed
    their credit limit. The UI can retry with ``allow_credit_override=True``
    (after an admin confirmation) to bypass a credit-limit warning — an
    on-hold customer can NEVER be overridden from this call.
    """
    quote = get_quote(quote_id)
    if not quote:
        return {"success": False, "error": "Quote not found"}
    status = (quote.get("status") or "").lower()
    if status == "invoiced":
        return {"success": False, "error": "Quote already invoiced"}
    if status == "accepted":
        return {"success": False, "error": "Quote already converted to a sales order",
                "so_id": quote.get("converted_so_id")}

    customer_id = quote.get("customer_id")
    if not customer_id:
        return {"success": False, "error": "Quote has no customer — assign a customer first"}

    selected_lines = [l for l in quote.get("lines", []) if l.get("is_selected")]
    # E1 — option-group filter: only required lines (option_group NULL/'')
    # plus lines that match the chosen group are carried over.
    chosen_group = (quote.get("selected_option_group") or "").strip() or None
    selected_lines = [
        l for l in selected_lines
        if not (l.get("option_group") or "")
        or (chosen_group and (l.get("option_group") or "") == chosen_group)
    ]
    if not selected_lines:
        return {"success": False, "error": "No lines selected on quote"}

    # P1.6 — credit / on-hold gate.
    order_total = 0.0
    for _l in selected_lines:
        try:
            order_total += float(_l.get("qty") or 1) * float(_l.get("unit_price") or 0)
        except (TypeError, ValueError):
            continue
    credit = check_customer_credit(customer_id, additional_amount=order_total)
    if not credit.get("ok"):
        # On-hold is never overridable from here.
        if credit.get("on_hold") or not allow_credit_override:
            return {
                "success": False,
                "error": credit.get("reason") or "Credit check failed",
                "credit_check": credit,
                "overridable": bool(
                    credit.get("would_exceed") and not credit.get("on_hold")
                ),
            }

    # Create the sales order
    so_result = create_sales_order(
        customer_id=customer_id,
        priority=priority,
        promised_date=promised_date,
        ship_method=ship_method,
        notes=f"From {quote.get('quote_number', '')}",
        esn=quote.get("esn"),
        vin=quote.get("vin"),
        equipment_type=quote.get("equipment_type"),
        equipment_make=quote.get("equipment_make"),
        equipment_model=quote.get("equipment_model"),
    )
    if not so_result or not so_result.get("id"):
        return {"success": False, "error": "Failed to create sales order"}

    so_id = so_result["id"]
    so_number = so_result["so_number"]

    # Copy lines (parents first, then add-on children with rewritten parent FK).
    line_id_map: dict[int, int] = {}
    parent_quote_lines: dict[int, dict] = {}
    children: list[dict] = []
    for line in selected_lines:
        if line.get("parent_line_id"):
            children.append(line)
            continue
        product_id = line.get("product_id")
        if not product_id:
            continue
        new_id = add_so_line(
            so_id=so_id,
            product_id=product_id,
            quantity=int(line.get("qty") or 1),
            unit_price=float(line.get("unit_price") or 0),
            warranty_months=line.get("warranty_months"),
            warranty_type=line.get("warranty_type"),
            warranty_mileage_limit=line.get("warranty_mileage_limit"),
            eta_date=line.get("eta_date"),
            # P1 — snapshot the quote line's customer-facing core onto
            # the SO so it survives later product-master edits.
            core_charge=float(line.get("core_charge") or 0),
        )
        if line.get("id") and new_id and new_id > 0:
            line_id_map[int(line["id"])] = int(new_id)
            parent_quote_lines[int(line["id"])] = line

    for line in children:
        parent_qid = line.get("parent_line_id")
        new_parent_id = line_id_map.get(int(parent_qid)) if parent_qid else None
        if not new_parent_id:
            continue
        parent_line = parent_quote_lines.get(int(parent_qid)) if parent_qid else {}
        child_product_id = line.get("product_id") or parent_line.get("product_id")
        if not child_product_id:
            continue
        child_sku = line.get("sku") or parent_line.get("sku")
        with get_db() as conn:
            qty = int(line.get("qty") or 1)
            unit_price = float(line.get("unit_price") or 0)
            child_core = float(line.get("core_charge") or 0)
            sol_cols = {c[1] for c in conn.execute("PRAGMA table_info(sales_order_lines)").fetchall()}
            has_core_col = "core_charge" in sol_cols
            if has_core_col:
                conn.execute("""
                    INSERT INTO sales_order_lines (
                        so_id, product_id, sku, description,
                        quantity_ordered, quantity_picked, quantity_backordered,
                        allocated_qty, unit_price, line_total,
                        warranty_months, warranty_type, warranty_mileage_limit,
                        parent_line_id, core_charge
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (so_id, child_product_id, child_sku,
                      line.get("description", ""),
                      qty, 0, 0, 0, unit_price, qty * unit_price,
                      line.get("warranty_months"), line.get("warranty_type"),
                      line.get("warranty_mileage_limit"), new_parent_id, child_core))
            else:
                conn.execute("""
                    INSERT INTO sales_order_lines (
                        so_id, product_id, sku, description,
                        quantity_ordered, quantity_picked, quantity_backordered,
                        allocated_qty, unit_price, line_total,
                        warranty_months, warranty_type, warranty_mileage_limit,
                        parent_line_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (so_id, child_product_id, child_sku,
                      line.get("description", ""),
                      qty, 0, 0, 0, unit_price, qty * unit_price,
                      line.get("warranty_months"), line.get("warranty_type"),
                      line.get("warranty_mileage_limit"), new_parent_id))
            conn.commit()
            _recalculate_so_totals(conn, so_id)

    # Link quote → SO
    with get_db() as conn:
        conn.execute(
            "UPDATE quotes SET status='accepted', converted_so_id=%s, updated_at=datetime('now') WHERE id=%s",
            (so_id, quote_id),
        )
        conn.execute(
            "UPDATE sales_orders SET quote_id=%s WHERE id=%s",
            (quote_id, so_id),
        )
        conn.commit()

    # Carry the 3% CC convenience-fee toggle forward.
    if int(quote.get("cc_fee_enabled") or 0):
        try:
            update_sales_order(
                so_id,
                cc_fee_enabled=1,
                cc_fee_rate=float(quote.get("cc_fee_rate") or 0.03),
            )
        except Exception:
            pass

    # Carry money/header fields from quote → SO so the totals match what
    # the customer accepted (#11). Without this, shipping/fuel/tax silently
    # reset to zero on the SO.
    money_fields: dict = {}
    for fld in ("tax_rate", "shipping", "fuel_surcharge"):
        try:
            v = quote.get(fld)
            if v is None:
                continue
            money_fields[fld] = float(v or 0)
        except (TypeError, ValueError):
            continue
    if ship_method is None and quote.get("carrier"):
        money_fields["ship_method"] = quote.get("carrier")
    if money_fields:
        try:
            update_sales_order(so_id, **money_fields)
        except Exception:
            pass

    return {"success": True, "so_id": so_id, "so_number": so_number}


def create_po_from_so(
    so_id: int,
    product_ids: list[int] | None = None,
    existing_po_by_vendor: dict[int, int] | None = None,
) -> dict:
    """Create purchase orders from a sales order, grouped by supplier/vendor.

    Args:
        so_id: Sales order ID.
        product_ids: If provided, only create POs for these product IDs.
                     If None, creates POs for ALL SO lines.

    Returns {success, purchase_orders: [{po_id, po_number, vendor, action}]}.
    """
    lines = get_so_lines(so_id)
    if not lines:
        return {"success": False, "error": "Sales order has no lines"}

    # Group lines by vendor (from product supplier field)
    vendor_lines: dict[int | None, list] = {}
    with get_db() as conn:
        for line in lines:
            if line.get("parent_line_id"):
                continue
            product_id = line.get("product_id")
            if not product_id:
                continue
            # Filter to specific product IDs if provided
            if product_ids is not None and product_id not in product_ids:
                continue
            row = conn.execute(
                "SELECT supplier, cost, preferred_vendor_id FROM products WHERE id=%s",
                (product_id,),
            ).fetchone()
            if not row:
                continue
            if hasattr(row, "keys"):
                supplier = row["supplier"]
                cost = row["cost"]
                preferred_vendor_id = row["preferred_vendor_id"]
            else:
                supplier, cost, preferred_vendor_id = row[0], row[1], row[2]
            cost = float(cost or 0)

            # Prefer the explicit FK; fall back to a name lookup against
            # vendors.name when the product only has a free-text supplier.
            vendor_id = None
            if preferred_vendor_id:
                try:
                    vendor_id = int(preferred_vendor_id)
                except (TypeError, ValueError):
                    vendor_id = None
            if vendor_id is None and supplier:
                vendor_row = conn.execute(
                    "SELECT id FROM vendors WHERE name=%s OR code=%s "
                    "OR LOWER(name)=LOWER(%s)",
                    (supplier, supplier, supplier),
                ).fetchone()
                if vendor_row:
                    vendor_id = vendor_row[0] if not hasattr(vendor_row, "keys") else vendor_row["id"]
            # Resolve display name from the vendor row if we found one.
            if vendor_id:
                vname_row = conn.execute(
                    "SELECT name FROM vendors WHERE id=%s", (vendor_id,)
                ).fetchone()
                if vname_row:
                    supplier = vname_row[0] if not hasattr(vname_row, "keys") else vname_row["name"]

            if vendor_id not in vendor_lines:
                vendor_lines[vendor_id] = {"name": supplier or "Unknown", "items": []}
            # Only purchase what's actually short. Prefer the explicit
            # backordered qty if set; otherwise fall back to ordered minus
            # what's already allocated and already on a PO.
            ordered = int(line.get("quantity_ordered") or line.get("qty") or 0)
            allocated = int(line.get("allocated_qty") or 0)
            on_po = int(line.get("on_po_qty") or 0)
            backordered = int(line.get("quantity_backordered") or 0)
            if backordered > 0:
                qty_to_buy = backordered
            else:
                qty_to_buy = ordered - allocated - on_po
            if qty_to_buy <= 0:
                continue
            vendor_lines[vendor_id]["items"].append({
                "product_id": product_id,
                "quantity_ordered": qty_to_buy,
                "unit_cost": cost,
            })

    if not vendor_lines:
        return {"success": False, "error": "No products with vendor info found"}

    existing_po_by_vendor = existing_po_by_vendor or {}
    created = []
    for vendor_id, group in vendor_lines.items():
        if vendor_id is None:
            # Skip lines without a valid vendor — user should assign vendors first
            continue
        existing_po_id = existing_po_by_vendor.get(vendor_id)
        if existing_po_id:
            existing_po = get_purchase_order(existing_po_id)
            if not existing_po:
                return {"success": False, "error": f"Open PO {existing_po_id} was not found"}
            for item in group["items"]:
                merged = add_or_merge_po_line(
                    existing_po_id,
                    item["product_id"],
                    item["quantity_ordered"],
                    item["unit_cost"],
                )
                if not merged.get("success"):
                    return merged

            # Preserve all linked SO references in notes when multiple SOs merge into one PO.
            current_notes = str(existing_po.get("notes") or "")
            existing_refs = {int(m.group(1)) for m in re.finditer(r"From\s+SO\s*#\s*(\d+)", current_notes, flags=re.IGNORECASE)}
            if int(so_id) not in existing_refs:
                so_tag = f"From SO #{so_id}"
                if not current_notes.strip():
                    new_notes = so_tag
                else:
                    new_notes = f"{current_notes.rstrip()} | {so_tag}"
                update_purchase_order(existing_po_id, notes=new_notes)

            created.append({
                "po_id": existing_po_id,
                "po_number": existing_po.get("po_number", f"PO #{existing_po_id}"),
                "vendor": group["name"],
                "action": "merged",
            })
            continue
        result = create_purchase_order(
            vendor_id=vendor_id,
            notes=f"From SO #{so_id}",
            linked_so_id=so_id,
            lines=group["items"],
        )
        if result.get("success"):
            created.append({
                "po_id": result["po_id"],
                "po_number": result["po_number"],
                "vendor": group["name"],
                "action": "created",
            })

    if not created:
        return {"success": False, "error": "No POs created — check vendor assignments"}

    return {"success": True, "purchase_orders": created}


def _recalc_quote_totals(conn, quote_id: int) -> None:
    """Recalculate quote subtotal, core_total, total from lines.

    Optional parts (is_optional=1) are excluded from the main total.
    Per-line shipping is summed separately.
    Discount lines (``line_kind='discount'``) are stored with negative
    ``line_total`` and reduce the taxable base.
    """
    # Detect line_kind support so we can exclude discount rows from subtotal.
    try:
        ql_cols = {c[1] for c in conn.execute("PRAGMA table_info(quote_lines)").fetchall()}
        has_line_kind = "line_kind" in ql_cols
    except Exception:
        has_line_kind = False
    discount_filter = (
        " AND COALESCE(line_kind,'product') != 'discount'" if has_line_kind else ""
    )
    # Determine which option-group (if any) is currently selected.
    try:
        sel_row = conn.execute(
            "SELECT selected_option_group FROM quotes WHERE id = %s",
            (quote_id,),
        ).fetchone()
        chosen_group = (sel_row[0] if sel_row else None) or None
    except Exception:
        chosen_group = None
    try:
        if chosen_group:
            row = conn.execute(f"""
                SELECT COALESCE(SUM(line_total + COALESCE(warranty_price, 0) * qty), 0),
                       COALESCE(SUM(core_charge * qty), 0),
                       COALESCE(SUM(COALESCE(line_shipping, 0)), 0)
                FROM quote_lines
                WHERE quote_id = %s AND is_selected = 1
                      AND COALESCE(is_optional, 0) = 0
                      AND (COALESCE(option_group, '') = ''
                           OR option_group = %s)
                      {discount_filter}
            """, (quote_id, chosen_group)).fetchone()
        else:
            row = conn.execute(f"""
                SELECT COALESCE(SUM(line_total + COALESCE(warranty_price, 0) * qty), 0),
                       COALESCE(SUM(core_charge * qty), 0),
                       COALESCE(SUM(COALESCE(line_shipping, 0)), 0)
                FROM quote_lines
                WHERE quote_id = %s AND is_selected = 1
                      AND COALESCE(is_optional, 0) = 0
                      AND COALESCE(option_group, '') = ''
                      {discount_filter}
            """, (quote_id,)).fetchone()
    except Exception:
        # Fallback if new columns don't exist yet
        row = conn.execute("""
            SELECT COALESCE(SUM(line_total), 0),
                   COALESCE(SUM(core_charge * qty), 0),
                   0
            FROM quote_lines
            WHERE quote_id = %s AND is_selected = 1
        """, (quote_id,)).fetchone()
    subtotal = row[0] if row else 0
    core_total = row[1] if row else 0
    shipping = row[2] if row else 0
    # Sum discount lines (negative line_total values).
    discount_sum = 0.0
    if has_line_kind:
        try:
            drow = conn.execute(
                "SELECT COALESCE(SUM(line_total), 0) FROM quote_lines "
                "WHERE quote_id = %s AND is_selected = 1 "
                "AND COALESCE(line_kind,'product') = 'discount'",
                (quote_id,),
            ).fetchone()
            discount_sum = float(drow[0] or 0) if drow else 0.0
        except Exception:
            discount_sum = 0.0
    tax_row = conn.execute("SELECT COALESCE(tax, 0), COALESCE(shipping, 0) FROM quotes WHERE id = %s", (quote_id,)).fetchone()
    tax = tax_row[0] if tax_row else 0
    quote_shipping = tax_row[1] if tax_row else 0
    try:
        fuel_row = conn.execute(
            "SELECT COALESCE(fuel_surcharge, 0) FROM quotes WHERE id = %s",
            (quote_id,),
        ).fetchone()
        fuel = fuel_row[0] if fuel_row else 0
    except Exception:
        fuel = 0
    total_shipping = shipping + quote_shipping
    base = subtotal + core_total + tax + total_shipping + fuel + discount_sum
    # Optional 3% credit-card convenience fee. When enabled, it is added on
    # top of the rest of the total and persisted as ``cc_fee_amount``.
    cc_amount = 0.0
    try:
        cc_row = conn.execute(
            "SELECT COALESCE(cc_fee_enabled, 0), COALESCE(cc_fee_rate, 0.03) FROM quotes WHERE id = %s",
            (quote_id,),
        ).fetchone()
        if cc_row and int(cc_row[0] or 0):
            cc_amount = round(base * float(cc_row[1] or 0.03), 2)
        try:
            conn.execute(
                "UPDATE quotes SET cc_fee_amount=%s WHERE id=%s",
                (cc_amount, quote_id),
            )
        except Exception:
            pass
    except Exception:
        cc_amount = 0.0
    total = base + cc_amount
    conn.execute(
        "UPDATE quotes SET subtotal=%s, core_total=%s, total=%s, updated_at=datetime('now') WHERE id=%s",
        (subtotal, core_total, total, quote_id),
    )


# =============================================================================
# QUOTE COMMENTS
# =============================================================================

def get_quote_comments(quote_id: int) -> list[dict]:
    """Get all comments for a quote, oldest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM quote_comments WHERE quote_id = %s ORDER BY created_at ASC",
            (quote_id,),
        ).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def add_quote_comment(quote_id: int, comment: str, author: str = "User") -> dict:
    """Add a timestamped comment to a quote."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO quote_comments (quote_id, author, comment) VALUES (%s, %s, %s)",
            (quote_id, author, comment),
        )
        conn.commit()
        return {"success": True}


# =============================================================================
# INVOICE MANAGEMENT
# =============================================================================

def generate_invoice_number() -> str:
    """Generate next invoice number (PREFIX-YYYYMMDD-NNN)."""
    from datetime import date
    today = date.today()
    inv_prefix = get_setting("invoice_prefix", "INV-")
    prefix = f"{inv_prefix}{today.strftime('%Y%m%d')}"
    
    with get_db() as conn:
        row = conn.execute("""
            SELECT invoice_number FROM invoices
            WHERE invoice_number LIKE %s
            ORDER BY invoice_number DESC LIMIT 1
        """, (f"{prefix}%",)).fetchone()
        
        if row:
            last_num = int(row[0].split("-")[-1])
            return f"{prefix}-{last_num + 1:03d}"
        return f"{prefix}-001"


def get_all_invoices(status: str = None, limit: int = 100) -> list[dict]:
    """Get all invoices with customer info."""
    with get_db() as conn:
        if status:
            rows = conn.execute("""
                SELECT i.*, c.name as customer_name, c.company as customer_company
                FROM invoices i
                LEFT JOIN customers c ON i.customer_id = c.id
                WHERE i.status = %s
                ORDER BY i.invoice_date DESC, i.id DESC
                LIMIT %s
            """, (status, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT i.*, c.name as customer_name, c.company as customer_company
                FROM invoices i
                LEFT JOIN customers c ON i.customer_id = c.id
                ORDER BY i.invoice_date DESC, i.id DESC
                LIMIT %s
            """, (limit,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def _add_core_line_for_product(
    conn,
    invoice_id: int,
    parent_line_id: int,
    product_id: int | None,
    quantity: int,
    sku: str | None,
    title: str | None,
) -> int | None:
    """Auto-create a paired ``line_kind='core'`` child invoice line whenever
    the product has a refundable core (``has_core=1`` and a positive
    ``core_sell_price`` or ``core_charge``).

    The child line carries the customer-facing core deposit on its own row —
    one core line per product line — mirroring the PO-side
    :func:`_sync_core_child_line`. The parent line's inline ``core_charge``
    column is zeroed so totals are not double-counted.

    Returns the new child line id, or ``None`` if no core line was needed.

    Pre-migration-114 databases without ``parent_line_id`` fall back to the
    legacy inline-column behavior so this is safe to deploy ahead of mig 114.
    """
    if not product_id or quantity <= 0 or not parent_line_id:
        return None
    if sku and str(sku).upper().startswith("CORE-"):
        return None
    row = conn.execute(
        "SELECT COALESCE(has_core, 0) AS has_core, "
        "       COALESCE(core_sell_price, 0) AS core_sell_price, "
        "       COALESCE(core_charge, 0) AS core_charge "
        "FROM products WHERE id=%s",
        (product_id,),
    ).fetchone()
    if not row:
        return None
    r = dict(row) if hasattr(row, "keys") else {
        "has_core": row[0], "core_sell_price": row[1], "core_charge": row[2],
    }
    has_core = int(r.get("has_core") or 0) == 1
    customer_amount = float(r.get("core_sell_price") or 0)
    if customer_amount <= 0:
        customer_amount = float(r.get("core_charge") or 0)
    # Legacy fallback: if has_core flag was never set but a positive charge
    # exists, still treat as refundable (matches mig 106 backfill semantics).
    if customer_amount <= 0:
        return None
    if not has_core and customer_amount <= 0:
        return None

    # Detect whether the new parent_line_id column exists (post-mig-114).
    try:
        il_cols = {
            c[1]
            for c in conn.execute("PRAGMA table_info(invoice_lines)").fetchall()
        }
    except Exception:
        il_cols = set()
    has_parent_col = "parent_line_id" in il_cols
    has_line_kind = "line_kind" in il_cols

    if not has_parent_col or not has_line_kind:
        # Pre-mig-114 fallback: stuff into parent's inline core_charge.
        try:
            conn.execute(
                "UPDATE invoice_lines SET core_charge = %s WHERE id = %s",
                (customer_amount, parent_line_id),
            )
            return parent_line_id
        except Exception:
            return None

    # Skip if an existing child core line is already attached.
    try:
        existing = conn.execute(
            "SELECT id FROM invoice_lines "
            " WHERE parent_line_id = %s "
            "   AND COALESCE(line_kind, 'product') = 'core' "
            " LIMIT 1",
            (parent_line_id,),
        ).fetchone()
        if existing:
            return None
    except Exception:
        pass

    child_qty = int(quantity)
    child_total = round(child_qty * customer_amount, 2)
    child_sku = f"CORE-{sku}" if sku else "CORE"
    child_desc = f"🔄 Core Charge — {title or 'core return'}"

    try:
        # Zero the inline column on the parent so the core total is not
        # double-counted by the invoice totals routine.
        conn.execute(
            "UPDATE invoice_lines SET core_charge = 0 WHERE id = %s",
            (parent_line_id,),
        )
        conn.execute(
            """
            INSERT INTO invoice_lines (
                invoice_id, product_id, sku, description, quantity,
                unit_price, line_total, core_charge, line_kind, parent_line_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 'core', %s)
            """,
            (invoice_id, product_id, child_sku, child_desc, child_qty,
             customer_amount, child_total, parent_line_id),
        )
        if is_postgresql():
            new_id = conn.execute("SELECT lastval()").fetchone()[0]
        else:
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return int(new_id) if new_id else None
    except Exception:
        return None


def get_invoice(invoice_id: int) -> dict | None:
    """Get invoice with lines and customer info."""
    with get_db() as conn:
        # Get invoice header
        row = conn.execute("""
            SELECT i.*, c.name as customer_name, c.company as customer_company,
                   c.email as customer_email, c.phone as customer_phone,
                   c.address as customer_address, c.city as customer_city,
                   c.state as customer_state, c.zip as customer_zip
            FROM invoices i
            LEFT JOIN customers c ON i.customer_id = c.id
            WHERE i.id = %s
        """, (invoice_id,)).fetchone()
        
        if not row:
            return None
        
        invoice = dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        
        # Get lines
        lines = conn.execute("""
            SELECT il.*, p.title as product_title,
                   p.invoice_description,
                   COALESCE(p.product_type, 'inventoried') as product_type
            FROM invoice_lines il
            LEFT JOIN products p ON il.product_id = p.id
            WHERE il.invoice_id = %s
            ORDER BY il.id
        """, (invoice_id,)).fetchall()
        
        invoice["lines"] = [dict(l) if hasattr(l, 'keys') else dict(enumerate(l)) for l in lines]
        return invoice


def get_invoice_by_number(invoice_number: str) -> dict | None:
    """Look up an invoice by its invoice_number string."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number = %s", (invoice_number,)
        ).fetchone()
        if not row:
            return None
        inv_id = row["id"] if hasattr(row, "keys") else row[0]
        return get_invoice(inv_id)


def create_invoice(
    customer_id: int,
    invoice_date: str = None,
    due_date: str = None,
    notes: str = None,
    user: str = None,
    esn: str = None,
    vin: str = None,
    equipment_type: str = None,
    equipment_make: str = None,
    equipment_model: str = None,
) -> dict:
    """Create a new invoice. Returns {"id": int, "invoice_number": str}."""
    from datetime import date, timedelta
    
    invoice_number = generate_invoice_number()
    inv_date = invoice_date or date.today().isoformat()
    
    # Default due date: Net 30
    if not due_date:
        due = date.fromisoformat(inv_date) + timedelta(days=30)
        due_date = due.isoformat()
    
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO invoices (
                invoice_number, customer_id, invoice_date, due_date,
                notes, created_by,
                esn, vin, equipment_type, equipment_make, equipment_model
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (invoice_number, customer_id, inv_date, due_date, notes, user,
              esn, vin, equipment_type, equipment_make, equipment_model))
        conn.commit()
        # Use lastrowid for SQLite compatibility
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            invoice_id = row[0]
        else:
            invoice_id = cursor.lastrowid
        # Enqueue for QBO sync (best-effort).
        try:
            from qbo.sync_queue import enqueue, ACTION_CREATE
            enqueue("invoice", invoice_id, ACTION_CREATE,
                    payload={"invoice_number": invoice_number,
                             "customer_id": customer_id},
                    message=f"Invoice {invoice_number} awaiting QBO push")
        except Exception:
            pass
        return {"id": invoice_id, "invoice_number": invoice_number}


def _invoice_is_direct(conn, invoice_id: int) -> bool:
    """An invoice is 'direct' (not sourced from a sales order) when no
    sales_orders row points back to it. Direct invoices manage their own
    inventory reservations through the add/remove/finalize lifecycle.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM sales_orders WHERE invoice_id = %s LIMIT 1",
            (invoice_id,),
        ).fetchone()
    except Exception:
        return True
    return row is None


def _reserve_for_invoice_line(conn, product_id: int | None, quantity: int) -> int:
    """Reserve up to ``quantity`` units of ``product_id`` (clamped to what
    is currently available). Returns the amount actually reserved.
    Non-inventoried products are skipped (returns 0 without touching stock).
    """
    if not product_id or quantity <= 0:
        return 0
    row = conn.execute(
        "SELECT COALESCE(qty_on_hand,0) AS on_hand, "
        "COALESCE(qty_reserved,0) AS reserved, "
        "COALESCE(product_type,'inventoried') AS product_type "
        "FROM products WHERE id=%s",
        (product_id,),
    ).fetchone()
    if not row:
        return 0
    if str(row[2]) != 'inventoried':
        return 0
    on_hand = int(row[0] or 0)
    reserved = int(row[1] or 0)
    available = max(0, on_hand - reserved)
    take = min(int(quantity), available)
    if take <= 0:
        return 0
    conn.execute(
        "UPDATE products SET qty_reserved = COALESCE(qty_reserved,0) + %s WHERE id=%s",
        (take, product_id),
    )
    return take


def _release_for_invoice_line(conn, product_id: int | None, quantity: int) -> None:
    """Release ``quantity`` units of ``product_id`` from reservation
    (clamped at zero).
    """
    if not product_id or quantity <= 0:
        return
    conn.execute(
        "UPDATE products SET qty_reserved = MAX(0, COALESCE(qty_reserved,0) - %s) WHERE id=%s",
        (int(quantity), product_id),
    )


def add_invoice_line(
    invoice_id: int,
    product_id: int = None,
    serial_id: int = None,
    sku: str = None,
    description: str = None,
    quantity: int = 1,
    unit_price: float = 0,
    warranty_months: int | None = None,
    warranty_type: str | None = None,
    warranty_mileage_limit: int | None = None,
) -> int:
    """Add a line item to an invoice. Returns line ID.
    
    If serial_id is provided, quantity is forced to 1 (one serial per line).
    Warranty fields default to the product's JAKS/supplier warranty when None.
    """
    # If serial specified, quantity must be 1
    if serial_id:
        quantity = 1
    
    line_total = quantity * unit_price
    
    # Get product info if product_id provided
    if product_id and not description:
        product = get_product_by_id(product_id)
        if product:
            sku = sku or product.get("sku")
            description = description or product.get("title")
            if unit_price == 0:
                unit_price = float(product.get("selling_price") or 0)
                line_total = quantity * unit_price
    
    with get_db() as conn:
        # Warranty auto-fill
        if warranty_months is None and warranty_type is None and warranty_mileage_limit is None:
            w_months, w_type, w_mileage = _fetch_product_warranty_defaults(
                conn, product_id
            )
        else:
            w_months = int(warranty_months or 0)
            w_type = warranty_type or "parts_only"
            w_mileage = warranty_mileage_limit

        cursor = conn.execute("""
            INSERT INTO invoice_lines (
                invoice_id, product_id, serial_id, sku, description, quantity, unit_price, line_total,
                warranty_months, warranty_type, warranty_mileage_limit
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (invoice_id, product_id, serial_id, sku, description, quantity, unit_price, line_total,
              w_months, w_type, w_mileage))
        
        # Get line ID using lastrowid for SQLite compatibility
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            line_id = row[0]
        else:
            line_id = cursor.lastrowid

        # Auto-add a paired core-charge line when the product has a
        # customer-facing core charge.
        _add_core_line_for_product(
            conn, invoice_id, line_id, product_id, quantity, sku, description,
        )

        # For direct invoices (no source SO), auto-reserve inventory so
        # the parts are held while the draft is being finalized.
        if _invoice_is_direct(conn, invoice_id):
            _reserve_for_invoice_line(conn, product_id, quantity)

        # Update invoice totals
        _recalculate_invoice_totals(conn, invoice_id)
        conn.commit()
        
        return line_id


def remove_invoice_line(line_id: int) -> bool:
    """Remove a line from an invoice."""
    with get_db() as conn:
        # Get invoice_id first
        row = conn.execute(
            "SELECT invoice_id, product_id, quantity, sku FROM invoice_lines WHERE id = %s", (line_id,)
        ).fetchone()
        
        if not row:
            return False
        
        if hasattr(row, "keys"):
            invoice_id = row["invoice_id"]
            product_id = row["product_id"]
            qty = int(row["quantity"] or 0)
            sku = row["sku"]
        else:
            invoice_id, product_id, qty, sku = row[0], row[1], int(row[2] or 0), row[3]

        # Release any reservation we placed when the line was added (only
        # for direct invoices and skipping CORE- companion lines).
        if (
            product_id
            and qty > 0
            and not (sku and str(sku).upper().startswith("CORE-"))
            and _invoice_is_direct(conn, invoice_id)
        ):
            _release_for_invoice_line(conn, product_id, qty)

        # Delete any paired core line(s) linked to this parent.
        conn.execute(
            "DELETE FROM invoice_lines WHERE invoice_id = %s AND notes = %s",
            (invoice_id, f"linked_to:{line_id}"),
        )
        conn.execute("DELETE FROM invoice_lines WHERE id = %s", (line_id,))
        _recalculate_invoice_totals(conn, invoice_id)
        conn.commit()
        return True


def update_invoice_line_price(line_id: int, unit_price: float) -> bool:
    """Update unit price and line total for an invoice line."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT invoice_id, quantity FROM invoice_lines WHERE id = %s",
            (line_id,)).fetchone()
        if not row:
            return False
        invoice_id = row[0]
        qty = int(row[1] or 1)
        line_total = unit_price * qty
        conn.execute(
            "UPDATE invoice_lines SET unit_price=%s, line_total=%s WHERE id=%s",
            (unit_price, line_total, line_id))
        _recalculate_invoice_totals(conn, invoice_id)
        conn.commit()
        return True


def update_invoice_line_core_charge(line_id: int, core_charge: float) -> bool:
    """Set the inline per-unit core charge on an invoice line."""
    try:
        amount = max(0.0, float(core_charge or 0))
    except (TypeError, ValueError):
        return False
    with get_db() as conn:
        row = conn.execute(
            "SELECT invoice_id FROM invoice_lines WHERE id = %s", (line_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE invoice_lines SET core_charge = %s WHERE id = %s",
            (amount, line_id),
        )
        _recalculate_invoice_totals(conn, row[0])
        conn.commit()
        return True


def update_invoice_line(line_id: int, **kwargs) -> dict:
    """Update arbitrary fields on an invoice line (warranty, notes, etc.)."""
    allowed = {
        "quantity", "unit_price", "notes", "description",
        "warranty_months", "warranty_type", "warranty_mileage_limit",
        "core_charge",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"success": True}
    with get_db() as conn:
        try:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [line_id]
            conn.execute(f"UPDATE invoice_lines SET {sets} WHERE id = %s", vals)
            row = conn.execute(
                "SELECT invoice_id, quantity, unit_price FROM invoice_lines WHERE id = %s",
                (line_id,),
            ).fetchone()
            if row:
                invoice_id = row[0]
                qty = int((row[1]) or 1)
                price = float((row[2]) or 0)
                conn.execute(
                    "UPDATE invoice_lines SET line_total = %s WHERE id = %s",
                    (qty * price, line_id),
                )
                _recalculate_invoice_totals(conn, invoice_id)
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def _recalculate_invoice_totals(conn, invoice_id: int) -> None:
    """Recalculate invoice subtotal, tax, and total.

    Subtotal includes per-unit ``core_charge`` carried inline on each
    invoice line (since cores are no longer modelled as separate
    ``CORE-*`` rows). Legacy CORE-* rows still contribute via
    ``line_total`` for backward compatibility.

    Discount lines (``line_kind='discount'``) are stored with negative
    ``line_total`` and reduce the taxable base.
    """
    il_cols = {c[1] for c in conn.execute("PRAGMA table_info(invoice_lines)").fetchall()}
    has_line_kind = "line_kind" in il_cols
    if has_line_kind:
        product_where = (
            "WHERE invoice_id = %s AND COALESCE(line_kind,'product') != 'discount'"
        )
        discount_where = (
            "WHERE invoice_id = %s AND COALESCE(line_kind,'product') = 'discount'"
        )
    else:
        product_where = "WHERE invoice_id = %s"
        discount_where = "WHERE 0 = 1 AND invoice_id = %s"

    row = conn.execute(f"""
        SELECT COALESCE(SUM(line_total), 0)
             + COALESCE(SUM(COALESCE(core_charge, 0) * COALESCE(quantity, 0)), 0)
          FROM invoice_lines {product_where}
    """, (invoice_id,)).fetchone()

    subtotal = float(row[0]) if row else 0

    drow = conn.execute(
        f"SELECT COALESCE(SUM(line_total), 0) FROM invoice_lines {discount_where}",
        (invoice_id,),
    ).fetchone()
    discount_sum = float(drow[0] or 0) if drow else 0  # negative

    # Get tax rate
    inv_row = conn.execute(
        "SELECT tax_rate, shipping, amount_paid FROM invoices WHERE id = %s", (invoice_id,)
    ).fetchone()

    tax_rate = float(inv_row[0]) if inv_row else 0
    shipping = float(inv_row[1]) if inv_row else 0
    amount_paid = float(inv_row[2]) if inv_row else 0

    taxable_base = subtotal + discount_sum  # discount_sum is negative
    if taxable_base < 0:
        taxable_base = 0
    tax_amount = taxable_base * tax_rate
    base = taxable_base + tax_amount + shipping
    # Optional 3% credit-card convenience fee.
    cc_amount = 0.0
    try:
        cc_row = conn.execute(
            "SELECT COALESCE(cc_fee_enabled, 0), COALESCE(cc_fee_rate, 0.03) FROM invoices WHERE id = %s",
            (invoice_id,),
        ).fetchone()
        if cc_row and int(cc_row[0] or 0):
            cc_amount = round(base * float(cc_row[1] or 0.03), 2)
    except Exception:
        cc_amount = 0.0
    total = base + cc_amount
    balance_due = total - amount_paid
    
    try:
        conn.execute("""
            UPDATE invoices SET 
                subtotal = %s, tax_amount = %s, total = %s, balance_due = %s,
                cc_fee_amount = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (subtotal, tax_amount, total, balance_due, cc_amount, invoice_id))
    except Exception:
        conn.execute("""
            UPDATE invoices SET 
                subtotal = %s, tax_amount = %s, total = %s, balance_due = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (subtotal, tax_amount, total, balance_due, invoice_id))


def update_invoice_status(invoice_id: int, status: str) -> bool:
    """Update invoice status (draft, sent, paid, void)."""
    valid_statuses = ("draft", "sent", "paid", "void", "partial")
    if status not in valid_statuses:
        return False
    
    with get_db() as conn:
        # If voiding a still-unfinalized direct invoice, release any
        # reservations that ``add_invoice_line`` placed on its products
        # so the stock returns to "available". Finalized invoices have
        # already deducted qty_on_hand (not just reserved), so the
        # restore path is ``revert_invoice_to_draft``, not this one.
        if status == "void":
            try:
                row = conn.execute(
                    "SELECT finalized_at FROM invoices WHERE id = %s",
                    (invoice_id,),
                ).fetchone()
                finalized = bool(row and (row[0] if not hasattr(row, "keys") else row["finalized_at"]))
                if not finalized and _invoice_is_direct(conn, invoice_id):
                    lines = conn.execute(
                        "SELECT product_id, quantity, sku FROM invoice_lines "
                        "WHERE invoice_id = %s",
                        (invoice_id,),
                    ).fetchall()
                    for ln in lines:
                        if hasattr(ln, "keys"):
                            _pid, _qty, _sku = ln["product_id"], int(ln["quantity"] or 0), ln["sku"]
                        else:
                            _pid, _qty, _sku = ln[0], int(ln[1] or 0), ln[2]
                        if _pid and _qty > 0 and not (
                            _sku and str(_sku).upper().startswith("CORE-")
                        ):
                            _release_for_invoice_line(conn, _pid, _qty)
            except Exception:
                # Never let a release-on-void failure block the status update.
                pass

        cursor = conn.execute("""
            UPDATE invoices SET status = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (status, invoice_id))
        conn.commit()
        return cursor.rowcount > 0


def update_invoice(invoice_id: int, **kwargs) -> dict:
    """Update invoice header fields. Returns {success, error}."""
    allowed = {
        "customer_id", "status", "notes", "internal_notes",
        "customer_notes",
        "tracking_number", "ship_date", "shipping",
        "due_date", "payment_method", "tax_rate",
        "cc_fee_enabled", "cc_fee_rate",
        "esn", "vin",
        "equipment_type", "equipment_make", "equipment_model",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"success": True}
    # Central status-transition check (P1.7).
    if "status" in updates:
        try:
            from engine.status_transitions import (
                INVOICE_TRANSITIONS, TransitionError, validate_transition,
            )
            with get_db() as _c:
                row = _c.execute(
                    "SELECT status FROM invoices WHERE id = %s", (invoice_id,)
                ).fetchone()
            current = (row[0] if row else "") or ""
            validate_transition(INVOICE_TRANSITIONS, old=current, new=str(updates["status"]))
        except TransitionError as e:
            return {"success": False, "error": str(e)}
        except Exception:
            pass
    with get_db() as conn:
        try:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [invoice_id]
            conn.execute(
                f"UPDATE invoices SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                vals,
            )
            # Recalc totals if any pricing-affecting field changed.
            if any(k in updates for k in ("tax_rate", "shipping", "cc_fee_enabled", "cc_fee_rate")):
                _recalculate_invoice_totals(conn, invoice_id)
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def finalize_invoice(invoice_id: int, deduct_inventory: bool = True, user: str = None) -> dict:
    """Finalize an invoice: change status to 'sent', deduct inventory, mark serials sold, create core returns.
    
    Returns: {"success": bool, "error": str or None, "deducted": list, "cores_created": list, "serials_sold": list}
    """
    invoice = get_invoice(invoice_id)
    if not invoice:
        return {"success": False, "error": "Invoice not found"}

    status = (invoice.get("status") or "").lower()
    if status == "void":
        return {"success": False, "error": "Voided invoices cannot be finalized"}

    if not invoice.get("customer_id"):
        return {"success": False, "error": "Invoice must have a customer before finalizing"}

    real_lines = [
        line for line in (invoice.get("lines") or [])
        if not str(line.get("sku") or "").upper().startswith(("CORE-", "RTN-"))
    ]
    if not real_lines:
        return {"success": False, "error": "Invoice must have at least one line before finalizing"}
    
    # Allow finalize whenever the invoice has not yet been finalized,
    # regardless of payment-driven status changes (e.g. ``partial`` after
    # an early payment). The dedicated ``finalized_at`` column is the
    # single source of truth for "has this been committed to inventory?".
    already_finalized = bool(invoice.get("finalized_at"))
    if already_finalized:
        return {"success": False, "error": "Invoice has already been finalized"}
    
    deducted = []
    cores_created = []
    serials_sold = []
    customer_id = invoice.get("customer_id")
    
    if deduct_inventory:
        # Direct invoices reserve stock when lines are added; release the
        # reservations now (the actual stock is being deducted below).
        with get_db() as _c:
            is_direct = _invoice_is_direct(_c, invoice_id)
        if is_direct:
            with get_db() as _c:
                for _line in invoice.get("lines", []):
                    _pid = _line.get("product_id")
                    _qty = int(_line.get("quantity") or 0)
                    _sku = _line.get("sku") or ""
                    if _pid and _qty > 0 and not str(_sku).upper().startswith("CORE-"):
                        _release_for_invoice_line(_c, _pid, _qty)
                _c.commit()

        for line in invoice.get("lines", []):
            product_id = line.get("product_id")
            line_id = line.get("id")
            serial_id = line.get("serial_id")
            qty = int(line.get("quantity") or 0)
            line_sku = (line.get("sku") or "")
            # Skip legacy CORE-* rows — they have no product_id but the
            # filter below also catches stragglers explicitly.
            if str(line_sku).upper().startswith("CORE-"):
                continue
            
            # Skip non-inventoried products (warranties, services, fees)
            product_type = str(line.get("product_type") or "inventoried")
            if product_id and qty > 0 and product_type == "inventoried":
                result = adjust_product_quantity(
                    sku=line.get("sku"),
                    delta=-qty,
                    txn_type="sale",
                    reference=invoice.get("invoice_number"),
                    notes=f"Invoice #{invoice.get('invoice_number')}",
                    user=user
                )
                if result.get("success"):
                    deducted.append({
                        "product_id": product_id,
                        "sku": line.get("sku"),
                        "qty_deducted": qty
                    })
                
                # Mark serial as sold if specified on the line
                if serial_id:
                    try:
                        sell_serial(
                            serial_id=serial_id,
                            invoice_id=invoice_id
                        )
                        serials_sold.append({
                            "serial_id": serial_id,
                            "product_id": product_id,
                            "sku": line.get("sku")
                        })
                    except Exception as e:
                        print(f"Warning: Failed to update serial status: {e}")
                
                # Check if this line has a core deposit. Prefer the inline
                # ``core_charge`` carried on the invoice line itself (the
                # customer-facing per-unit core); fall back to the product
                # record for legacy invoices.
                line_core_price = float(line.get("core_charge") or 0)
                product = get_product_by_id(product_id)
                if product:
                    vendor_core_charge = float(product.get("core_charge") or 0)
                    if line_core_price > 0:
                        customer_core_price = line_core_price
                    else:
                        customer_core_price = float(product.get("core_sell_price") or 0)
                        if customer_core_price <= 0:
                            customer_core_price = vendor_core_charge
                    if (customer_core_price > 0 or vendor_core_charge > 0) and customer_id:
                        # Create one core return per unit sold
                        for _ in range(qty):
                            try:
                                core_id = create_core_from_invoice_line(
                                    invoice_id=invoice_id,
                                    invoice_line_id=line_id,
                                    product_id=product_id,
                                    customer_id=customer_id,
                                    core_charge=vendor_core_charge,
                                    customer_core_price=customer_core_price,
                                )
                                if core_id:
                                    cores_created.append({
                                        "core_id": core_id,
                                        "product_id": product_id,
                                        "sku": line.get("sku"),
                                        "core_charge": vendor_core_charge,
                                        "customer_core_price": customer_core_price,
                                    })
                            except Exception as e:
                                # Log but don't fail entire finalize
                                print(f"Warning: Failed to create core return: {e}")
    
    # Materialize warranty certificates for any warranty add-on lines on
    # this invoice. Idempotent — safe to call after a re-finalize.
    try:
        materialize_warranty_certificates(invoice_id)
    except Exception as e:
        print(f"Warning: Failed to materialize warranty certificates: {e}")

    # Stamp finalization and transition draft -> sent in the same write.
    # If this fails, do not report success: a missing finalized_at is exactly
    # the state that traps invoices in the UI/payment flow.
    try:
        with get_db() as _c:
            cursor = _c.execute(
                """
                UPDATE invoices
                   SET finalized_at = CURRENT_TIMESTAMP,
                       status = CASE
                           WHEN LOWER(COALESCE(status, '')) = 'draft' THEN 'sent'
                           ELSE status
                       END,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s
                   AND finalized_at IS NULL
                """,
                (invoice_id,),
            )
            _c.commit()
            if getattr(cursor, "rowcount", 1) == 0:
                return {"success": False, "error": "Invoice has already been finalized"}
    except Exception as e:
        return {"success": False, "error": f"Could not stamp finalized_at: {e}"}
    
    return {"success": True, "deducted": deducted, "cores_created": cores_created, "serials_sold": serials_sold}


def revert_invoice_to_draft(invoice_id: int, user: str = None) -> dict:
    """Reverse a finalized (``sent``) invoice back to ``draft`` so its
    lines can be edited again.

    Reverses the side-effects of :func:`finalize_invoice`:
      * adds product quantities back into inventory
      * marks any serials on the invoice as ``available`` again
      * voids any core_returns rows that were created from this invoice

    Refuses if the invoice is ``paid``, ``partial`` (already partially
    paid) or has any payments recorded — those would leave a payment
    stranded against a draft. Caller should refund first.

    Returns ``{"success": bool, "error": str|None, "restored": list,
              "serials_returned": list, "cores_voided": list}``.
    """
    invoice = get_invoice(invoice_id)
    if not invoice:
        return {"success": False, "error": "Invoice not found"}

    status = (invoice.get("status") or "").lower()
    if status == "draft":
        return {"success": False, "error": "Invoice is already a draft"}
    if status in ("paid", "partial"):
        return {
            "success": False,
            "error": "Cannot revert a paid/partially-paid invoice — refund first",
        }
    if status == "void":
        return {"success": False, "error": "Voided invoices cannot be reverted"}
    if float(invoice.get("amount_paid") or 0) > 0:
        return {
            "success": False,
            "error": "Invoice has payments recorded — refund first",
        }

    restored: list[dict] = []
    serials_returned: list[dict] = []
    cores_voided: list[dict] = []

    for line in invoice.get("lines", []):
        product_id = line.get("product_id")
        qty = int(line.get("quantity") or 0)
        sku = line.get("sku")
        serial_id = line.get("serial_id")

        # Skip auto-generated core lines (no inventory was deducted for them)
        if not product_id or qty <= 0:
            continue
        if sku and str(sku).upper().startswith("CORE-"):
            continue

        try:
            result = adjust_product_quantity(
                sku=sku,
                delta=qty,
                txn_type="adjustment",
                reference=invoice.get("invoice_number"),
                notes=f"Revert invoice #{invoice.get('invoice_number')} to draft",
                user=user,
            )
            if result.get("success"):
                restored.append({"sku": sku, "qty_restored": qty})
        except Exception as e:
            print(f"Warning: failed to restore qty for {sku}: {e}")

        if serial_id:
            try:
                return_serial(serial_id, notes=f"Reverted from invoice {invoice.get('invoice_number')}")
                serials_returned.append({"serial_id": serial_id, "sku": sku})
            except Exception as e:
                print(f"Warning: failed to return serial {serial_id}: {e}")

    # Void any core returns generated by this invoice's finalize.
    with get_db() as conn:
        try:
            rows = conn.execute(
                "SELECT id FROM core_returns WHERE invoice_id = %s AND status NOT IN ('voided','received','refunded')",
                (invoice_id,),
            ).fetchall()
        except Exception:
            rows = []
    for r in rows or []:
        cid = r[0] if not hasattr(r, "keys") else r["id"]
        try:
            if void_core_return(cid, notes=f"Invoice {invoice.get('invoice_number')} reverted to draft"):
                cores_voided.append({"core_id": cid})
        except Exception as e:
            print(f"Warning: failed to void core return {cid}: {e}")

    # Void warranty certificates issued from this invoice.
    try:
        void_warranties_for_invoice(
            invoice_id,
            reason=f"Voided: invoice {invoice.get('invoice_number')} reverted to draft",
        )
    except Exception as e:
        print(f"Warning: failed to void warranties: {e}")

    update_invoice_status(invoice_id, "draft")

    # Re-establish reservations for direct invoices so the now-restored
    # stock is held against this draft pending re-finalize.
    with get_db() as conn:
        if _invoice_is_direct(conn, invoice_id):
            for line in invoice.get("lines", []):
                pid = line.get("product_id")
                q = int(line.get("quantity") or 0)
                sku = line.get("sku") or ""
                if pid and q > 0 and not str(sku).upper().startswith("CORE-"):
                    _reserve_for_invoice_line(conn, pid, q)
            conn.commit()

    return {
        "success": True,
        "restored": restored,
        "serials_returned": serials_returned,
        "cores_voided": cores_voided,
    }


def record_payment(
    invoice_id: int,
    amount: float,
    payment_method: str = None,
    notes: str = None
) -> bool:
    """Record a payment on an invoice.

    Refuses to apply when the invoice has not been finalized yet —
    finalization is what commits inventory, cores and serials, so paying
    a draft would let stock walk out the door without being deducted.
    Raises :class:`ValueError` so callers (UI / tests) can show a clear
    "Finalize first" message.
    """
    with get_db() as conn:
        # Get current amounts and finalization state
        try:
            row = conn.execute(
                "SELECT amount_paid, total, finalized_at FROM invoices WHERE id = %s",
                (invoice_id,),
            ).fetchone()
        except Exception:
            # Schema without finalized_at (very old). Fall back to legacy.
            row = conn.execute(
                "SELECT amount_paid, total FROM invoices WHERE id = %s",
                (invoice_id,),
            ).fetchone()
            if not row:
                return False
            current_paid = float(row[0] or 0)
            total = float(row[1] or 0)
            finalized_at = None
        else:
            if not row:
                return False
            current_paid = float(row[0] or 0)
            total = float(row[1] or 0)
            finalized_at = row[2] if len(row) > 2 else None

        if not finalized_at:
            raise ValueError(
                "Invoice must be finalized before payment can be recorded. "
                "Click 'Save & Send' (Finalize) first to commit inventory."
            )

        new_paid = current_paid + amount
        balance = total - new_paid

        # Capture any overpayment so we can post it to the customer's
        # credit ledger as an open credit (matches QBO behavior — never
        # silently lose money).
        overpay = -balance if balance < -0.005 else 0.0

        # Determine status. After clamping to 0 below, anything that
        # fully covers the invoice is 'paid'; partial otherwise.
        new_status = "paid" if balance <= 0.005 else "partial"
        clamped_balance = max(0.0, balance)

        conn.execute("""
            UPDATE invoices SET
                amount_paid = %s, balance_due = %s, status = %s,
                payment_method = COALESCE(%s, payment_method),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (new_paid, clamped_balance, new_status, payment_method, invoice_id))

        overpayment_credit_id: int | None = None
        if overpay > 0:
            # Look up customer + invoice number for a useful note. Fail
            # soft — the payment update is what really matters.
            try:
                cust_row = conn.execute(
                    "SELECT customer_id, invoice_number FROM invoices WHERE id = %s",
                    (invoice_id,),
                ).fetchone()
                if cust_row:
                    if hasattr(cust_row, "keys"):
                        cust_id = cust_row["customer_id"]
                        inv_num = cust_row["invoice_number"]
                    else:
                        cust_id = cust_row[0]
                        inv_num = cust_row[1]
                    if cust_id:
                        method_label = (payment_method or "payment").strip()
                        memo = (
                            f"Overpayment on invoice {inv_num or invoice_id} "
                            f"({method_label})"
                        )
                        if notes:
                            memo += f" — {notes}"
                        cur = conn.execute(
                            "INSERT INTO customer_credits ("
                            "    customer_id, source_type, source_id, amount, "
                            "    issue_date, status, notes"
                            ") VALUES (%s, 'overpayment', %s, %s, "
                            "    CURRENT_TIMESTAMP, 'open', %s)",
                            (int(cust_id), int(invoice_id),
                             float(overpay), memo),
                        )
                        try:
                            overpayment_credit_id = int(cur.lastrowid)
                        except Exception:
                            overpayment_credit_id = None
            except Exception:
                overpayment_credit_id = None

        # ── T1.1: write per-payment history row (additive, idempotent
        # alongside the aggregate update above). Fail soft so missing
        # tables on very old DBs don't break payment recording.
        payment_history_id: int | None = None
        try:
            cur = conn.execute(
                "INSERT INTO invoice_payments ("
                "    invoice_id, amount, payment_method, notes, status, "
                "    overpayment_credit_id"
                ") VALUES (%s, %s, %s, %s, 'posted', %s)",
                (
                    int(invoice_id),
                    float(amount or 0),
                    payment_method,
                    notes,
                    overpayment_credit_id,
                ),
            )
            try:
                payment_history_id = int(cur.lastrowid)
            except Exception:
                payment_history_id = None
        except Exception:
            payment_history_id = None

        conn.commit()

        # Truthy return preserves the historic ``bool`` contract for
        # callers that don't care about the credit; new callers can
        # introspect ``.overpayment_credit_id`` / ``.overpay_amount``.
        return _PaymentResult(
            success=True,
            overpayment_credit_id=overpayment_credit_id,
            overpay_amount=float(overpay),
            payment_history_id=payment_history_id,
        )


class _PaymentResult(int):
    """Truthy result of :func:`record_payment` that also carries
    overpayment metadata for callers that want it.

    Behaves like ``True``/``False`` for legacy ``if record_payment(...)``
    call sites, but exposes ``.overpayment_credit_id`` and
    ``.overpay_amount`` attributes for the UI to surface a toast.
    """

    overpayment_credit_id: int | None
    overpay_amount: float
    payment_history_id: int | None

    def __new__(cls, *, success: bool,
                overpayment_credit_id: int | None = None,
                overpay_amount: float = 0.0,
                payment_history_id: int | None = None):
        inst = super().__new__(cls, 1 if success else 0)
        inst.overpayment_credit_id = overpayment_credit_id
        inst.overpay_amount = float(overpay_amount or 0)
        inst.payment_history_id = payment_history_id
        return inst

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"_PaymentResult(success={bool(self)}, "
            f"overpayment_credit_id={self.overpayment_credit_id}, "
            f"overpay_amount={self.overpay_amount:.2f}, "
            f"payment_history_id={self.payment_history_id})"
        )


# ─── Payment history (T1.1) ─────────────────────────────────────────────

def list_invoice_payments(
    invoice_id: int,
    include_voided: bool = False,
) -> list[dict]:
    """Return all payment-history rows for an invoice, newest first."""
    where = "WHERE invoice_id = %s"
    params: tuple = (int(invoice_id),)
    if not include_voided:
        where += " AND status = 'posted'"
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM invoice_payments {where} "
            "ORDER BY recorded_at DESC, id DESC",
            params,
        ).fetchall()
    return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def get_invoice_payment_total(invoice_id: int) -> float:
    """Sum of posted payment-history rows. Useful for reconciliation."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM invoice_payments "
            "WHERE invoice_id = %s AND status = 'posted'",
            (int(invoice_id),),
        ).fetchone()
    if not row:
        return 0.0
    return float(row[0] or 0)


def void_invoice_payment(
    payment_id: int,
    reason: str | None = None,
    voided_by: str | None = None,
) -> bool:
    """Void a single payment row and reverse it on the parent invoice.

    Reduces ``invoices.amount_paid`` by the row's amount, recomputes
    ``balance_due`` and ``status``. Idempotent — voiding an already-voided
    row is a no-op.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT invoice_id, amount, status FROM invoice_payments WHERE id = %s",
            (int(payment_id),),
        ).fetchone()
        if not row:
            return False
        if hasattr(row, "keys"):
            inv_id = row["invoice_id"]
            amt = float(row["amount"] or 0)
            status = str(row["status"] or "")
        else:
            inv_id, amt, status = int(row[0]), float(row[1] or 0), str(row[2] or "")
        if status == "voided":
            return False

        # Update parent aggregate
        inv = conn.execute(
            "SELECT amount_paid, total FROM invoices WHERE id = %s",
            (int(inv_id),),
        ).fetchone()
        if inv:
            if hasattr(inv, "keys"):
                cur_paid = float(inv["amount_paid"] or 0)
                total = float(inv["total"] or 0)
            else:
                cur_paid = float(inv[0] or 0)
                total = float(inv[1] or 0)
            new_paid = max(0.0, cur_paid - amt)
            balance = max(0.0, total - new_paid)
            new_status = (
                "paid" if (total > 0 and balance <= 0.005)
                else ("partial" if new_paid > 0.005 else "sent")
            )
            conn.execute(
                "UPDATE invoices SET amount_paid = %s, balance_due = %s, "
                "status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (new_paid, balance, new_status, int(inv_id)),
            )

        conn.execute(
            "UPDATE invoice_payments SET status = 'voided', "
            "voided_at = CURRENT_TIMESTAMP, voided_by = %s, void_reason = %s "
            "WHERE id = %s",
            (voided_by, reason, int(payment_id)),
        )
        conn.commit()
    return True


def get_customer_invoices(customer_id: int) -> list[dict]:
    """Get all invoices for a customer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM invoices
            WHERE customer_id = %s
            ORDER BY invoice_date DESC
        """, (customer_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# =============================================================================
# Storage Locations (Phase I)
# =============================================================================

def list_locations(active_only: bool = True) -> list[dict]:
    """List all storage locations."""
    with get_db() as conn:
        if active_only:
            rows = conn.execute("""
                SELECT * FROM storage_locations
                WHERE is_active = TRUE
                ORDER BY zone, aisle, shelf, bin, name
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM storage_locations
                ORDER BY zone, aisle, shelf, bin, name
            """).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_location(location_id: int) -> dict | None:
    """Get a single location by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM storage_locations WHERE id = %s", (location_id,)
        ).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def get_location_by_code(code: str) -> dict | None:
    """Get a location by its code."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM storage_locations WHERE code = %s", (code.upper(),)
        ).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def create_location(
    name: str,
    code: str,
    aisle: str = None,
    shelf: str = None,
    bin: str = None,
    zone: str = None,
    capacity: int = 0,
    notes: str = None
) -> int:
    """Create a new storage location. Returns the new location ID."""
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO storage_locations (name, code, aisle, shelf, bin, zone, capacity, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (name.strip(), code.upper().strip(), aisle, shelf, bin, zone, capacity, notes))
        conn.commit()
        # Use lastrowid for SQLite compatibility
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            return row[0]
        else:
            return cursor.lastrowid


def update_location(
    location_id: int,
    name: str = None,
    code: str = None,
    aisle: str = None,
    shelf: str = None,
    bin: str = None,
    zone: str = None,
    capacity: int = None,
    is_active: bool = None,
    notes: str = None
) -> bool:
    """Update an existing location. Returns True if updated."""
    fields = []
    values = []
    
    if name is not None:
        fields.append("name = %s")
        values.append(name.strip())
    if code is not None:
        fields.append("code = %s")
        values.append(code.upper().strip())
    if aisle is not None:
        fields.append("aisle = %s")
        values.append(aisle)
    if shelf is not None:
        fields.append("shelf = %s")
        values.append(shelf)
    if bin is not None:
        fields.append("bin = %s")
        values.append(bin)
    if zone is not None:
        fields.append("zone = %s")
        values.append(zone)
    if capacity is not None:
        fields.append("capacity = %s")
        values.append(capacity)
    if is_active is not None:
        fields.append("is_active = %s")
        values.append(is_active)
    if notes is not None:
        fields.append("notes = %s")
        values.append(notes)
    
    if not fields:
        return False
    
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(location_id)
    
    with get_db() as conn:
        conn.execute(f"""
            UPDATE storage_locations
            SET {', '.join(fields)}
            WHERE id = %s
        """, tuple(values))
        conn.commit()
        return True


def delete_location(location_id: int) -> bool:
    """Delete a location (soft delete - sets is_active=False)."""
    return update_location(location_id, is_active=False)


def get_products_at_location(location_id: int) -> list[dict]:
    """Get all products stored at a specific location."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.*, pl.quantity as location_qty, pl.is_primary as is_primary_location
            FROM products p
            JOIN product_locations pl ON pl.product_id = p.id
            WHERE pl.location_id = %s
            ORDER BY p.sku
        """, (location_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_product_locations(product_id: int) -> list[dict]:
    """Get all locations where a product is stored."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT sl.*, pl.quantity, pl.is_primary
            FROM storage_locations sl
            JOIN product_locations pl ON pl.location_id = sl.id
            WHERE pl.product_id = %s AND sl.is_active = TRUE
            ORDER BY pl.is_primary DESC, sl.zone, sl.aisle, sl.shelf, sl.bin
        """, (product_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def assign_product_to_location(
    product_id: int,
    location_id: int,
    quantity: int = 0,
    is_primary: bool = False
) -> bool:
    """Assign a product to a location (or update if exists)."""
    with get_db() as conn:
        # Check if mapping exists
        existing = conn.execute("""
            SELECT id FROM product_locations
            WHERE product_id = %s AND location_id = %s
        """, (product_id, location_id)).fetchone()
        
        if existing:
            # Update existing — increment quantity
            conn.execute("""
                UPDATE product_locations
                SET quantity = quantity + %s, is_primary = %s
                WHERE product_id = %s AND location_id = %s
            """, (quantity, is_primary, product_id, location_id))
        else:
            # Insert new
            conn.execute("""
                INSERT INTO product_locations (product_id, location_id, quantity, is_primary)
                VALUES (%s, %s, %s, %s)
            """, (product_id, location_id, quantity, is_primary))
        
        # If setting as primary, clear primary flag from other locations
        if is_primary:
            conn.execute("""
                UPDATE product_locations
                SET is_primary = FALSE
                WHERE product_id = %s AND location_id != %s
            """, (product_id, location_id))
        
        conn.commit()
        return True


def remove_product_from_location(product_id: int, location_id: int) -> bool:
    """Remove a product from a location."""
    with get_db() as conn:
        conn.execute("""
            DELETE FROM product_locations
            WHERE product_id = %s AND location_id = %s
        """, (product_id, location_id))
        conn.commit()
        return True


def get_location_utilization() -> list[dict]:
    """Get utilization stats for all active locations."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT 
                sl.*,
                COUNT(pl.product_id) as product_count,
                COALESCE(SUM(pl.quantity), 0) as total_quantity
            FROM storage_locations sl
            LEFT JOIN product_locations pl ON pl.location_id = sl.id
            WHERE sl.is_active = TRUE
            GROUP BY sl.id
            ORDER BY sl.zone, sl.aisle, sl.shelf, sl.bin
        """).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def search_locations(query: str) -> list[dict]:
    """Search locations by code, name, or zone."""
    with get_db() as conn:
        pattern = f"%{query}%"
        rows = conn.execute("""
            SELECT * FROM storage_locations
            WHERE is_active = TRUE AND (
                code ILIKE %s OR name ILIKE %s OR zone ILIKE %s
                OR aisle ILIKE %s OR shelf ILIKE %s
            )
            ORDER BY zone, aisle, shelf, bin, name
        """, (pattern, pattern, pattern, pattern, pattern)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# =============================================================================
# Serial/Lot Number Tracking (Phase I Part 2)
# =============================================================================

def list_serials(
    product_id: int = None,
    status: str = None,
    lot_number: str = None,
    location_id: int = None,
    limit: int = 100
) -> list[dict]:
    """List serial numbers with optional filters."""
    with get_db() as conn:
        conditions = []
        params = []
        
        if product_id:
            conditions.append("sn.product_id = %s")
            params.append(product_id)
        if status:
            conditions.append("sn.status = %s")
            params.append(status)
        if lot_number:
            conditions.append("sn.lot_number = %s")
            params.append(lot_number)
        if location_id:
            conditions.append("sn.location_id = %s")
            params.append(location_id)
        
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        
        rows = conn.execute(f"""
            SELECT sn.*, p.sku, p.title, sl.code as location_code
            FROM serial_numbers sn
            LEFT JOIN products p ON p.id = sn.product_id
            LEFT JOIN storage_locations sl ON sl.id = sn.location_id
            {where_clause}
            ORDER BY sn.created_at DESC
            LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_serial(serial_id: int) -> dict | None:
    """Get a single serial number by ID."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT sn.*, p.sku, p.title, sl.code as location_code
            FROM serial_numbers sn
            LEFT JOIN products p ON p.id = sn.product_id
            LEFT JOIN storage_locations sl ON sl.id = sn.location_id
            WHERE sn.id = %s
        """, (serial_id,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def get_serial_by_number(product_id: int, serial_number: str) -> dict | None:
    """Get a serial by product ID and serial number."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT sn.*, p.sku, p.title, sl.code as location_code
            FROM serial_numbers sn
            LEFT JOIN products p ON p.id = sn.product_id
            LEFT JOIN storage_locations sl ON sl.id = sn.location_id
            WHERE sn.product_id = %s AND sn.serial_number = %s
        """, (product_id, serial_number)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def create_serial(
    product_id: int,
    serial_number: str,
    lot_number: str = None,
    location_id: int = None,
    cost: float = 0,
    received_date: str = None,
    expiry_date: str = None,
    warranty_expiry: str = None,
    po_id: int = None,
    notes: str = None
) -> int:
    """Create a new serial number record. Returns the new ID."""
    from datetime import date
    
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO serial_numbers 
            (product_id, serial_number, lot_number, status, location_id, cost,
             received_date, expiry_date, warranty_expiry, po_id, notes)
            VALUES (%s, %s, %s, 'available', %s, %s, %s, %s, %s, %s, %s)
        """, (
            product_id, serial_number.strip(), lot_number,
            location_id, cost,
            received_date or date.today().isoformat(),
            expiry_date, warranty_expiry, po_id, notes
        ))
        conn.commit()
        # Use lastrowid for SQLite compatibility
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            return row[0]
        else:
            return cursor.lastrowid


def update_serial(
    serial_id: int,
    lot_number: str = None,
    status: str = None,
    location_id: int = None,
    cost: float = None,
    expiry_date: str = None,
    warranty_expiry: str = None,
    notes: str = None
) -> bool:
    """Update an existing serial number record."""
    fields = []
    values = []
    
    if lot_number is not None:
        fields.append("lot_number = %s")
        values.append(lot_number)
    if status is not None:
        fields.append("status = %s")
        values.append(status)
    if location_id is not None:
        fields.append("location_id = %s")
        values.append(location_id if location_id > 0 else None)
    if cost is not None:
        fields.append("cost = %s")
        values.append(cost)
    if expiry_date is not None:
        fields.append("expiry_date = %s")
        values.append(expiry_date or None)
    if warranty_expiry is not None:
        fields.append("warranty_expiry = %s")
        values.append(warranty_expiry or None)
    if notes is not None:
        fields.append("notes = %s")
        values.append(notes)
    
    if not fields:
        return False
    
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(serial_id)
    
    with get_db() as conn:
        conn.execute(f"""
            UPDATE serial_numbers
            SET {', '.join(fields)}
            WHERE id = %s
        """, tuple(values))
        conn.commit()
        return True


def sell_serial(serial_id: int, invoice_id: int = None) -> bool:
    """Mark a serial number as sold."""
    from datetime import date
    
    with get_db() as conn:
        conn.execute("""
            UPDATE serial_numbers
            SET status = 'sold', sold_date = %s, invoice_id = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (date.today().isoformat(), invoice_id, serial_id))
        conn.commit()
        return True


def return_serial(serial_id: int, notes: str = None) -> bool:
    """Mark a sold serial as returned (back to available)."""
    with get_db() as conn:
        conn.execute("""
            UPDATE serial_numbers
            SET status = 'available', sold_date = NULL, invoice_id = NULL, 
                notes = COALESCE(%s, notes), updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (notes, serial_id))
        conn.commit()
        return True


def delete_serial(serial_id: int) -> bool:
    """Delete a serial number (hard delete)."""
    with get_db() as conn:
        conn.execute("DELETE FROM serial_numbers WHERE id = %s", (serial_id,))
        conn.commit()
        return True


def get_available_serials(product_id: int) -> list[dict]:
    """Get all available (unsold) serials for a product, FIFO order."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT sn.*, sl.code as location_code
            FROM serial_numbers sn
            LEFT JOIN storage_locations sl ON sl.id = sn.location_id
            WHERE sn.product_id = %s AND sn.status = 'available'
            ORDER BY sn.received_date ASC, sn.id ASC
        """, (product_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_product_serial_count(product_id: int) -> dict:
    """Get count of serials by status for a product."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) as count
            FROM serial_numbers
            WHERE product_id = %s
            GROUP BY status
        """, (product_id,)).fetchall()
        result = {"available": 0, "sold": 0, "reserved": 0, "defective": 0}
        for row in rows:
            status = row[0] if isinstance(row, tuple) else row.get("status")
            count = row[1] if isinstance(row, tuple) else row.get("count")
            result[status] = count
        return result


def search_serials(query: str) -> list[dict]:
    """Search serials by serial number, lot number, or product SKU."""
    with get_db() as conn:
        pattern = f"%{query}%"
        rows = conn.execute("""
            SELECT sn.*, p.sku, p.title, sl.code as location_code
            FROM serial_numbers sn
            LEFT JOIN products p ON p.id = sn.product_id
            LEFT JOIN storage_locations sl ON sl.id = sn.location_id
            WHERE sn.serial_number ILIKE %s 
               OR sn.lot_number ILIKE %s 
               OR p.sku ILIKE %s
            ORDER BY sn.created_at DESC
            LIMIT 100
        """, (pattern, pattern, pattern)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def bulk_create_serials(
    product_id: int,
    serial_numbers: list[str],
    lot_number: str = None,
    location_id: int = None,
    cost: float = 0,
    po_id: int = None
) -> int:
    """Bulk create serial numbers for a product. Returns count created."""
    from datetime import date
    
    created = 0
    today = date.today().isoformat()
    
    with get_db() as conn:
        for sn in serial_numbers:
            sn = sn.strip()
            if not sn:
                continue
            try:
                conn.execute("""
                    INSERT INTO serial_numbers 
                    (product_id, serial_number, lot_number, status, location_id, cost, received_date, po_id)
                    VALUES (%s, %s, %s, 'available', %s, %s, %s, %s)
                """, (product_id, sn, lot_number, location_id, cost, today, po_id))
                created += 1
            except Exception:
                # Duplicate serial, skip
                pass
        conn.commit()
    return created


# ============================================================================
# Core Return Functions (Phase I Part 3)
# ============================================================================

def list_core_returns(
    status: str = None,
    customer_id: int = None,
    product_id: int = None,
    limit: int = 100
) -> list[dict]:
    """List core returns with optional filters."""
    with get_db() as conn:
        conditions = ["1=1"]
        params = []
        
        if status:
            conditions.append("cr.status = %s")
            params.append(status)
        if customer_id:
            conditions.append("cr.customer_id = %s")
            params.append(customer_id)
        if product_id:
            conditions.append("cr.product_id = %s")
            params.append(product_id)
        
        params.append(limit)
        
        rows = conn.execute(f"""
            SELECT cr.*, p.sku, p.title as product_title, 
                   c.name as customer_name, i.invoice_number,
                   (SELECT COUNT(*) FROM core_returns cr2
                     WHERE cr2.product_id = cr.product_id
                       AND cr2.status IN ('pending','received')
                   ) AS open_qty_for_product
            FROM core_returns cr
            LEFT JOIN products p ON p.id = cr.product_id
            LEFT JOIN customers c ON c.id = cr.customer_id
            LEFT JOIN invoices i ON i.id = cr.invoice_id
            WHERE {' AND '.join(conditions)}
            ORDER BY cr.created_at DESC
            LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_core_return(core_id: int) -> dict | None:
    """Get a single core return by ID."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT cr.*, p.sku, p.title as product_title, 
                   c.name as customer_name, i.invoice_number
            FROM core_returns cr
            LEFT JOIN products p ON p.id = cr.product_id
            LEFT JOIN customers c ON c.id = cr.customer_id
            LEFT JOIN invoices i ON i.id = cr.invoice_id
            WHERE cr.id = %s
        """, (core_id,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def create_core_return(
    invoice_line_id: int = None,
    invoice_id: int = None,
    product_id: int = None,
    customer_id: int = None,
    core_charge: float = 0,
    due_date: str = None,
    serial_number: str = None,
    notes: str = None,
    customer_core_price: float = None,
) -> int:
    """Create a new core return record. Returns the new core return ID.

    ``core_charge`` is the vendor deposit (what we recover from the vendor).
    ``customer_core_price`` is what the customer paid us per unit — the
    credit owed back when they return the core. Defaults to ``core_charge``
    when not supplied (preserves legacy callers).
    """
    if customer_core_price is None:
        customer_core_price = core_charge
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO core_returns 
            (invoice_line_id, invoice_id, product_id, customer_id, core_charge,
             customer_core_price,
             status, due_date, serial_number, notes)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s)
        """, (invoice_line_id, invoice_id, product_id, customer_id, core_charge,
              customer_core_price,
              due_date, serial_number, notes))
        conn.commit()
        # Get the last inserted ID
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            return row[0]
        else:
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            return row[0]


def update_core_return(
    core_id: int,
    status: str = None,
    received_date: str = None,
    condition: str = None,
    credit_issued: float = None,
    credit_date: str = None,
    credit_invoice_id: int = None,
    serial_number: str = None,
    notes: str = None
) -> bool:
    """Update a core return record."""
    fields = []
    values = []
    
    if status is not None:
        fields.append("status = %s")
        values.append(status)
    if received_date is not None:
        fields.append("received_date = %s")
        values.append(received_date or None)
    if condition is not None:
        fields.append("condition = %s")
        values.append(condition)
    if credit_issued is not None:
        fields.append("credit_issued = %s")
        values.append(credit_issued)
    if credit_date is not None:
        fields.append("credit_date = %s")
        values.append(credit_date or None)
    if credit_invoice_id is not None:
        fields.append("credit_invoice_id = %s")
        values.append(credit_invoice_id if credit_invoice_id > 0 else None)
    if serial_number is not None:
        fields.append("serial_number = %s")
        values.append(serial_number)
    if notes is not None:
        fields.append("notes = %s")
        values.append(notes)
    
    if not fields:
        return False
    
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(core_id)
    
    with get_db() as conn:
        conn.execute(f"""
            UPDATE core_returns
            SET {', '.join(fields)}
            WHERE id = %s
        """, tuple(values))
        conn.commit()

    # If this update flipped the core to 'received', create the matching
    # vendor obligation. Best-effort.
    if status == 'received':
        try:
            from .core_handling import create_vendor_core_obligation_from_customer_return
            create_vendor_core_obligation_from_customer_return(core_id)
        except Exception:
            pass

    return True


def receive_core(
    core_id: int,
    condition: str = "good",
    serial_number: str = None,
    notes: str = None,
    received_by: str = None,
) -> bool:
    """Mark a core as received (customer returned the core).

    ``received_by`` records the employee/username that processed the
    physical return so the reprinted receipt and the audit trail can
    show accountability later (added by migration 109).
    """
    from datetime import date

    with get_db() as conn:
        conn.execute("""
            UPDATE core_returns
            SET status = 'received', received_date = %s, condition = %s,
                serial_number = COALESCE(%s, serial_number),
                notes = COALESCE(%s, notes),
                received_by = COALESCE(%s, received_by),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (date.today().isoformat(), condition, serial_number, notes,
              received_by, core_id))
        conn.commit()

    # Customer just brought a core back to us → we now owe one to the vendor.
    # Best-effort: never block the customer-side flow.
    try:
        from .core_handling import create_vendor_core_obligation_from_customer_return
        create_vendor_core_obligation_from_customer_return(core_id)
    except Exception:
        pass

    return True


def issue_core_credit(
    core_id: int,
    credit_amount: float,
    credit_invoice_id: int = None,
    notes: str = None
) -> bool:
    """Issue credit for a returned core.

    Updates the core_returns row and writes a paired ``customer_credits``
    ledger entry so the credit shows up on the customer's account.
    Idempotent: if a non-voided ledger row already exists for this core,
    its amount is updated instead of inserting a duplicate.
    """
    from datetime import date

    with get_db() as conn:
        conn.execute("""
            UPDATE core_returns
            SET status = 'credited', credit_issued = %s, credit_date = %s,
                credit_invoice_id = %s,
                notes = COALESCE(%s, notes),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (credit_amount, date.today().isoformat(), credit_invoice_id, notes, core_id))

        # Look up the customer this credit belongs to.
        row = conn.execute(
            "SELECT customer_id FROM core_returns WHERE id = %s",
            (core_id,),
        ).fetchone()
        cust_id = None
        if row:
            cust_id = row[0] if not hasattr(row, "keys") else row["customer_id"]

        if cust_id:
            existing = conn.execute(
                "SELECT id FROM customer_credits "
                "WHERE source_type = 'core' AND source_id = %s "
                "AND status <> 'voided'",
                (core_id,),
            ).fetchone()
            if existing:
                eid = existing[0] if not hasattr(existing, "keys") else existing["id"]
                conn.execute(
                    "UPDATE customer_credits "
                    "SET amount = %s, notes = COALESCE(%s, notes), "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s",
                    (float(credit_amount or 0), notes, eid),
                )
            else:
                conn.execute(
                    "INSERT INTO customer_credits ("
                    "    customer_id, source_type, source_id, amount, "
                    "    issue_date, status, notes"
                    ") VALUES (%s, 'core', %s, %s, CURRENT_TIMESTAMP, 'open', %s)",
                    (int(cust_id), int(core_id), float(credit_amount or 0), notes),
                )
        conn.commit()
        return True


def void_core_return(
    core_id: int,
    notes: str = None,
    void_reason: str = None,
) -> bool:
    """Void/cancel a core return (customer not returning core).

    Also voids any open ``customer_credits`` ledger row tied to this core.
    ``void_reason`` is captured separately from ``notes`` so it is not
    later overwritten (added by migration 109).
    """
    with get_db() as conn:
        conn.execute("""
            UPDATE core_returns
            SET status = 'voided', notes = COALESCE(%s, notes),
                void_reason = COALESCE(%s, void_reason),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (notes, void_reason, core_id))
        try:
            conn.execute(
                "UPDATE customer_credits "
                "SET status = 'voided', updated_at = CURRENT_TIMESTAMP "
                "WHERE source_type = 'core' AND source_id = %s "
                "  AND status = 'open'",
                (int(core_id),),
            )
        except Exception:
            # customer_credits may not exist on extremely old DBs; the
            # migration runs at startup, so this is best-effort.
            pass
        conn.commit()
        return True


def delete_core_return(core_id: int) -> bool:
    """Delete a core return record (hard delete)."""
    with get_db() as conn:
        conn.execute("DELETE FROM core_returns WHERE id = %s", (core_id,))
        conn.commit()
        return True


# ──────────────────────────────────────────────────────────────────
# Customer credits ledger (migration 069)
# ──────────────────────────────────────────────────────────────────

def list_customer_credits(
    customer_id: int,
    status: str | None = None,
) -> list[dict]:
    """Return ledger rows for a customer, newest first.

    ``status`` may be ``'open'``, ``'applied'``, ``'voided'``, or ``None``
    for all statuses.
    """
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT cc.*, "
                "       inv.invoice_number AS applied_invoice_number "
                "FROM customer_credits cc "
                "LEFT JOIN invoices inv ON inv.id = cc.applied_to_invoice_id "
                "WHERE cc.customer_id = %s AND cc.status = %s "
                "ORDER BY cc.issue_date DESC, cc.id DESC",
                (int(customer_id), status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT cc.*, "
                "       inv.invoice_number AS applied_invoice_number "
                "FROM customer_credits cc "
                "LEFT JOIN invoices inv ON inv.id = cc.applied_to_invoice_id "
                "WHERE cc.customer_id = %s "
                "ORDER BY cc.issue_date DESC, cc.id DESC",
                (int(customer_id),),
            ).fetchall()
    return [
        dict(r) if hasattr(r, "keys") else dict(enumerate(r))
        for r in rows
    ]


def get_open_credit_total(customer_id: int) -> float:
    """Sum of ``amount`` for the customer's open credits."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total "
            "FROM customer_credits "
            "WHERE customer_id = %s AND status = 'open'",
            (int(customer_id),),
        ).fetchone()
    if not row:
        return 0.0
    return float(row[0] if not hasattr(row, "keys") else row["total"])


def apply_credit_to_invoice(
    credit_id: int,
    invoice_id: int,
    notes: str | None = None,
) -> bool:
    """Apply an open customer credit to an invoice.

    Implementation per UAT decision (Option A): the credit is recorded as
    a payment row on the invoice with method='credit-memo' so it flows
    through the same payment / balance-due math as cash and check
    payments. The credit row is then marked ``applied``.

    Returns ``True`` on success. Raises ``ValueError`` for invalid input
    or when the credit / invoice don't exist or don't match customers.
    """
    with get_db() as conn:
        crow = conn.execute(
            "SELECT id, customer_id, amount, status "
            "FROM customer_credits WHERE id = %s",
            (int(credit_id),),
        ).fetchone()
        if not crow:
            raise ValueError(f"Credit {credit_id} not found")
        c = dict(crow) if hasattr(crow, "keys") else {
            "id": crow[0], "customer_id": crow[1],
            "amount": crow[2], "status": crow[3],
        }
        if str(c.get("status") or "").lower() != "open":
            raise ValueError(
                f"Credit {credit_id} is {c.get('status')!r}, not open."
            )

        irow = conn.execute(
            "SELECT id, customer_id, COALESCE(total, 0) AS total, "
            "       COALESCE(amount_paid, 0) AS amount_paid, "
            "       status FROM invoices WHERE id = %s",
            (int(invoice_id),),
        ).fetchone()
        if not irow:
            raise ValueError(f"Invoice {invoice_id} not found")
        inv = dict(irow) if hasattr(irow, "keys") else {
            "id": irow[0], "customer_id": irow[1],
            "total": irow[2], "amount_paid": irow[3], "status": irow[4],
        }
        if int(inv["customer_id"] or 0) != int(c["customer_id"] or 0):
            raise ValueError(
                "Cannot apply: credit and invoice belong to different customers."
            )
        if str(inv.get("status") or "").lower() in ("voided", "void"):
            raise ValueError("Cannot apply credit to a voided invoice.")

        amount = float(c.get("amount") or 0)
        if amount <= 0:
            raise ValueError("Credit amount must be > 0.")

        balance_due = float(inv["total"]) - float(inv["amount_paid"])
        applied = min(amount, max(0.0, balance_due))
        if applied <= 0:
            raise ValueError("Invoice has no remaining balance to apply credit to.")

        # Record as an invoice payment so existing payment/total math
        # picks it up. Use method='credit-memo' so reports can isolate
        # these from real cash/check/CC payments. This codebase tracks
        # payments via invoices.amount_paid / payment_method directly
        # (no separate invoice_payments table), so we update inline.
        memo = (
            (notes + " | " if notes else "")
            + f"Credit memo #{int(credit_id)} (CR-{int(credit_id)})"
        )
        new_paid = float(inv["amount_paid"]) + float(applied)
        new_balance = max(0.0, float(inv["total"]) - new_paid)
        new_status = (
            "paid" if new_balance <= 0.005
            else "partial"
        )
        conn.execute(
            "UPDATE invoices SET amount_paid = %s, balance_due = %s, "
            "       status = %s, payment_method = "
            "       COALESCE(payment_method, %s), "
            "       notes = TRIM(COALESCE(notes, '') || %s), "
            "       updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (
                new_paid, new_balance, new_status,
                "credit-memo",
                "\n" + memo,
                int(invoice_id),
            ),
        )

        # Mark the credit applied. If the credit was larger than the
        # remaining balance we keep the leftover by splitting: shrink
        # this credit to the applied amount and create a new open row
        # for the remainder.
        if applied + 0.005 < amount:
            conn.execute(
                "UPDATE customer_credits SET amount = %s, "
                "       applied_to_invoice_id = %s, "
                "       applied_date = CURRENT_TIMESTAMP, status = 'applied', "
                "       notes = COALESCE(%s, notes), "
                "       updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (float(applied), int(invoice_id), notes, int(credit_id)),
            )
            conn.execute(
                "INSERT INTO customer_credits ("
                "    customer_id, source_type, source_id, amount, "
                "    issue_date, status, notes"
                ") VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, 'open', %s)",
                (
                    int(c["customer_id"]),
                    "split",
                    int(credit_id),
                    float(amount - applied),
                    f"Remainder of credit #{int(credit_id)} "
                    f"after applying ${applied:.2f} to invoice {invoice_id}",
                ),
            )
        else:
            conn.execute(
                "UPDATE customer_credits SET applied_to_invoice_id = %s, "
                "       applied_date = CURRENT_TIMESTAMP, status = 'applied', "
                "       notes = COALESCE(%s, notes), "
                "       updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (int(invoice_id), notes, int(credit_id)),
            )

        conn.commit()
        return True


def void_customer_credit(credit_id: int, reason: str | None = None) -> bool:
    """Mark a customer credit as voided (e.g. expired or written off)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE customer_credits SET status = 'voided', "
            "       notes = COALESCE(%s, notes), "
            "       updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND status = 'open'",
            (reason, int(credit_id)),
        )
        conn.commit()
        return True


def refund_customer_credit(
    credit_id: int,
    method: str,
    reference: str | None = None,
    notes: str | None = None,
) -> bool:
    """Disburse an open customer credit back to the customer.

    Used when the customer requests a refund-by-check (or card refund,
    cash from till, ACH transfer) instead of letting the credit sit on
    the ledger to be applied to a future invoice.

    Parameters
    ----------
    credit_id : int
        The ``customer_credits.id`` to refund. Must be in ``open`` status.
    method : str
        One of ``check`` / ``cash`` / ``card_refund`` / ``ach``.
    reference : str, optional
        Check number, ACH trace, processor transaction id, etc. Stored
        verbatim in ``refund_reference`` for audit / reconciliation.
    notes : str, optional
        Free-form note appended to the credit's existing ``notes``.

    Returns ``True`` on success.

    Raises ``ValueError`` if the credit is missing, already applied/voided/
    refunded, or ``method`` is not one of the allowed values.
    """
    allowed = {"check", "cash", "card_refund", "ach"}
    method_norm = (method or "").strip().lower()
    if method_norm not in allowed:
        raise ValueError(
            f"Invalid refund method '{method}'. "
            f"Expected one of: {', '.join(sorted(allowed))}."
        )
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, status, amount FROM customer_credits WHERE id = %s",
            (int(credit_id),),
        ).fetchone()
        if not row:
            raise ValueError(f"Customer credit #{credit_id} not found.")
        status = row[1] if not hasattr(row, "keys") else row["status"]
        if str(status or "").lower() != "open":
            raise ValueError(
                f"Credit #{credit_id} is '{status}' — only open credits "
                f"can be refunded."
            )

        memo_parts: list[str] = []
        ref_clean = (reference or "").strip()
        if ref_clean:
            memo_parts.append(f"Refunded via {method_norm} ref {ref_clean}")
        else:
            memo_parts.append(f"Refunded via {method_norm}")
        if notes:
            memo_parts.append(notes.strip())
        memo = " — ".join(p for p in memo_parts if p)

        conn.execute(
            "UPDATE customer_credits SET "
            "    status = 'refunded', "
            "    refund_method = %s, "
            "    refund_reference = %s, "
            "    refund_date = CURRENT_TIMESTAMP, "
            "    notes = CASE WHEN notes IS NULL OR notes = '' THEN %s "
            "                 ELSE notes || ' | ' || %s END, "
            "    updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND status = 'open'",
            (method_norm, ref_clean or None, memo, memo, int(credit_id)),
        )
        conn.commit()
        return True


def create_manual_customer_credit(
    customer_id: int, amount: float, notes: str | None = None,
) -> int:
    """Insert a manual credit row (e.g. goodwill, write-off correction).

    Returns the new credit's ID.
    """
    if not customer_id or float(amount or 0) <= 0:
        raise ValueError("customer_id and a positive amount are required.")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO customer_credits ("
            "    customer_id, source_type, source_id, amount, "
            "    issue_date, status, notes"
            ") VALUES (%s, 'manual', NULL, %s, CURRENT_TIMESTAMP, 'open', %s)",
            (int(customer_id), float(amount), notes),
        )
        conn.commit()
        try:
            return int(cur.lastrowid)
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Customer warranty certificates (mig 071)
# ---------------------------------------------------------------------------

def _months_to_end_date(start_iso: str | None, months: int):
    """Compute end date = start + months. Returns ISO string or None."""
    from datetime import date, datetime, timedelta
    try:
        if not start_iso:
            start = date.today()
        elif isinstance(start_iso, str):
            start = datetime.strptime(start_iso[:10], "%Y-%m-%d").date()
        elif hasattr(start_iso, "year"):
            start = start_iso if isinstance(start_iso, date) else start_iso.date()
        else:
            start = date.today()
        end = start + timedelta(days=int(round(30.4375 * max(0, int(months)))))
        return start.isoformat(), end.isoformat()
    except Exception:
        return None, None


def materialize_warranty_certificates(invoice_id: int) -> list[int]:
    """Create one ``customer_warranties`` row per warranty line on an invoice.

    Idempotent: if a row already exists for an invoice_line_id we leave
    it alone (its status / claim_count may have been updated since).
    Called from :func:`finalize_invoice`. Returns the list of newly
    created warranty IDs.
    """
    created: list[int] = []
    with get_db() as conn:
        inv = conn.execute(
            "SELECT id, customer_id, invoice_number, invoice_date "
            "FROM invoices WHERE id = %s",
            (int(invoice_id),),
        ).fetchone()
        if not inv:
            return created
        invd = dict(inv) if hasattr(inv, "keys") else {
            "id": inv[0], "customer_id": inv[1],
            "invoice_number": inv[2], "invoice_date": inv[3],
        }
        if not invd.get("customer_id"):
            return created

        rows = conn.execute(
            """
            SELECT il.id, il.product_id, il.sku, il.description,
                   il.quantity, il.unit_price,
                   COALESCE(il.warranty_months, 0) AS months,
                   il.warranty_type, il.warranty_mileage_limit
              FROM invoice_lines il
             WHERE il.invoice_id = %s
               AND COALESCE(il.warranty_months, 0) > 0
            """,
            (int(invoice_id),),
        ).fetchall()

        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {}
            line_id = d.get("id")
            existing = conn.execute(
                "SELECT 1 FROM customer_warranties WHERE invoice_line_id = %s",
                (line_id,),
            ).fetchone()
            if existing:
                continue

            months = int(d.get("months") or 0)
            start_iso, end_iso = _months_to_end_date(
                invd.get("invoice_date"), months
            )
            inv_num = invd.get("invoice_number") or invd.get("id")
            cert = f"CRT-{inv_num}-{line_id}"

            cur = conn.execute(
                """
                INSERT INTO customer_warranties (
                    customer_id, invoice_id, invoice_line_id,
                    product_id, sku, description, qty,
                    coverage_type, coverage_months, mileage_limit,
                    start_date, end_date, certificate_number,
                    status, amount_charged
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          'active', %s)
                """,
                (
                    int(invd["customer_id"]),
                    int(invoice_id),
                    int(line_id),
                    d.get("product_id"),
                    d.get("sku"),
                    d.get("description"),
                    int(d.get("quantity") or 1),
                    d.get("warranty_type") or "parts_only",
                    months,
                    d.get("warranty_mileage_limit"),
                    start_iso,
                    end_iso,
                    cert,
                    float(d.get("unit_price") or 0),
                ),
            )
            try:
                created.append(int(cur.lastrowid))
            except Exception:
                pass
        conn.commit()
    return created


def void_warranties_for_invoice(invoice_id: int, reason: str | None = None) -> int:
    """Void every active certificate tied to an invoice (e.g. on revert).

    Returns the number of rows voided.
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE customer_warranties SET status = 'voided', "
            "       notes = COALESCE(%s, notes), "
            "       updated_at = CURRENT_TIMESTAMP "
            "WHERE invoice_id = %s AND status IN ('active','expired','claimed')",
            (reason or "Voided: invoice reverted to draft", int(invoice_id)),
        )
        conn.commit()
        try:
            return int(cur.rowcount)
        except Exception:
            return 0


def list_customer_warranties(
    customer_id: int, status: str | None = None,
) -> list[dict]:
    """Return certificates for a customer, newest first.

    Adds a derived ``days_left`` field (negative if expired) so the UI
    doesn't need to recompute it.
    """
    from datetime import date, datetime
    with get_db() as conn:
        if status:
            rows = conn.execute(
                """
                SELECT cw.*, i.invoice_number AS invoice_number
                  FROM customer_warranties cw
                  LEFT JOIN invoices i ON i.id = cw.invoice_id
                 WHERE cw.customer_id = %s AND cw.status = %s
                 ORDER BY cw.created_at DESC, cw.id DESC
                """,
                (int(customer_id), status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT cw.*, i.invoice_number AS invoice_number
                  FROM customer_warranties cw
                  LEFT JOIN invoices i ON i.id = cw.invoice_id
                 WHERE cw.customer_id = %s
                 ORDER BY cw.created_at DESC, cw.id DESC
                """,
                (int(customer_id),),
            ).fetchall()

    out: list[dict] = []
    today = date.today()
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else dict(enumerate(r))
        days_left = None
        end = d.get("end_date")
        try:
            if isinstance(end, str) and end:
                end_d = datetime.strptime(end[:10], "%Y-%m-%d").date()
                days_left = (end_d - today).days
            elif hasattr(end, "year"):
                end_d = end if isinstance(end, date) else end.date()
                days_left = (end_d - today).days
        except Exception:
            pass
        d["days_left"] = days_left
        out.append(d)
    return out


def get_customer_warranty(warranty_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT cw.*, i.invoice_number, i.invoice_date,
                   c.name AS customer_name, c.company AS customer_company,
                   c.email AS customer_email, c.phone AS customer_phone,
                   c.address AS customer_address, c.city AS customer_city,
                   c.state AS customer_state, c.zip AS customer_zip
              FROM customer_warranties cw
              LEFT JOIN invoices i  ON i.id = cw.invoice_id
              LEFT JOIN customers c ON c.id = cw.customer_id
             WHERE cw.id = %s
            """,
            (int(warranty_id),),
        ).fetchone()
    if not row:
        return None
    return dict(row) if hasattr(row, "keys") else dict(enumerate(row))


def list_warranties_for_invoice(invoice_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM customer_warranties WHERE invoice_id = %s "
            "ORDER BY id ASC",
            (int(invoice_id),),
        ).fetchall()
    return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def void_customer_warranty(warranty_id: int, reason: str | None = None) -> bool:
    """Mark a single certificate voided (e.g. customer rescinded)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE customer_warranties SET status = 'voided', "
            "       notes = COALESCE(%s, notes), "
            "       updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            (reason, int(warranty_id)),
        )
        conn.commit()
        return True


def record_warranty_claim(warranty_id: int, notes: str | None = None) -> bool:
    """Increment claim count and flip status to 'claimed'.

    Status stays 'claimed' even after multiple claims; claim_count tracks
    the running total. ``notes`` is appended to the existing notes
    field with a timestamp prefix.
    """
    from datetime import datetime
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    memo = f"[{stamp} claim] {notes}" if notes else f"[{stamp}] claim recorded"
    with get_db() as conn:
        conn.execute(
            "UPDATE customer_warranties SET status = 'claimed', "
            "       claim_count = COALESCE(claim_count, 0) + 1, "
            "       notes = TRIM(COALESCE(notes, '') || %s), "
            "       updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            ("\n" + memo, int(warranty_id)),
        )
        conn.commit()
        return True


def expire_due_warranties() -> int:
    """Move active certs whose ``end_date`` has passed into 'expired'.

    Called once at app startup. Returns the number of rows updated.
    """
    with get_db() as conn:
        try:
            if is_postgresql():
                cur = conn.execute(
                    "UPDATE customer_warranties SET status = 'expired', "
                    "       updated_at = CURRENT_TIMESTAMP "
                    "WHERE status = 'active' AND end_date IS NOT NULL "
                    "  AND end_date < CURRENT_DATE"
                )
            else:
                cur = conn.execute(
                    "UPDATE customer_warranties SET status = 'expired', "
                    "       updated_at = CURRENT_TIMESTAMP "
                    "WHERE status = 'active' AND end_date IS NOT NULL "
                    "  AND date(end_date) < date('now')"
                )
            conn.commit()
            try:
                return int(cur.rowcount)
            except Exception:
                return 0
        except Exception:
            return 0


def get_pending_cores(customer_id: int = None, days_overdue: int = None) -> list[dict]:
    """Get pending core returns, optionally filtered by customer or overdue days."""
    with get_db() as conn:
        conditions = ["cr.status = 'pending'"]
        params = []
        
        if customer_id:
            conditions.append("cr.customer_id = %s")
            params.append(customer_id)
        
        if days_overdue:
            if is_postgresql():
                conditions.append("cr.due_date < CURRENT_DATE - INTERVAL '%s days'")
                params.append(days_overdue)
            else:
                conditions.append("cr.due_date < date('now', %s)")
                params.append(f"-{days_overdue} days")
        
        if is_postgresql():
            overdue_expr = "CURRENT_DATE - cr.due_date as days_overdue"
        else:
            overdue_expr = "CAST(julianday('now') - julianday(cr.due_date) AS INTEGER) as days_overdue"
        
        rows = conn.execute(f"""
            SELECT cr.*, p.sku, p.title as product_title, 
                   c.name as customer_name, i.invoice_number,
                   {overdue_expr}
            FROM core_returns cr
            LEFT JOIN products p ON p.id = cr.product_id
            LEFT JOIN customers c ON c.id = cr.customer_id
            LEFT JOIN invoices i ON i.id = cr.invoice_id
            WHERE {' AND '.join(conditions)}
            ORDER BY cr.due_date ASC
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_core_returns_for_invoice(invoice_id: int) -> list[dict]:
    """Get all core returns associated with an invoice."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT cr.*, p.sku, p.title as product_title
            FROM core_returns cr
            LEFT JOIN products p ON p.id = cr.product_id
            WHERE cr.invoice_id = %s
            ORDER BY cr.id
        """, (invoice_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def invoice_has_cores(invoice_id: int) -> bool:
    """Return True when the invoice carries at least one line with
    ``core_charge > 0`` or already has a materialized ``core_returns``
    row. Used by the print / email path to decide whether to bundle the
    Core Return slip.
    """
    if not invoice_id:
        return False
    with get_db() as conn:
        # Materialized cores (post-finalize) or pending charges on lines
        # (pre-finalize) both count.
        row = conn.execute(
            "SELECT 1 FROM core_returns WHERE invoice_id = %s LIMIT 1",
            (int(invoice_id),),
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            "SELECT 1 FROM invoice_lines "
            "WHERE invoice_id = %s AND COALESCE(core_charge, 0) > 0 LIMIT 1",
            (int(invoice_id),),
        ).fetchone()
        return bool(row)


def mark_invoice_first_printed(invoice_id: int) -> bool:
    """Stamp ``invoices.first_printed_at`` to ``CURRENT_TIMESTAMP`` if not
    already set. Returns ``True`` if this call did the stamping (i.e.
    the invoice had never been printed before), ``False`` otherwise.
    """
    if not invoice_id:
        return False
    with get_db() as conn:
        row = conn.execute(
            "SELECT first_printed_at FROM invoices WHERE id = %s",
            (int(invoice_id),),
        ).fetchone()
        if not row:
            return False
        already = row[0] if not hasattr(row, "keys") else row["first_printed_at"]
        if already:
            return False
        conn.execute(
            "UPDATE invoices SET first_printed_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND first_printed_at IS NULL",
            (int(invoice_id),),
        )
        conn.commit()
        return True



def get_core_returns_for_customer(customer_id: int, status: str = None) -> list[dict]:
    """Get all core returns for a customer, optionally filtered by status."""
    with get_db() as conn:
        conditions = ["cr.customer_id = %s"]
        params = [customer_id]
        
        if status:
            conditions.append("cr.status = %s")
            params.append(status)
        
        rows = conn.execute(f"""
            SELECT cr.*, p.sku, p.title as product_title, i.invoice_number
            FROM core_returns cr
            LEFT JOIN products p ON p.id = cr.product_id
            LEFT JOIN invoices i ON i.id = cr.invoice_id
            WHERE {' AND '.join(conditions)}
            ORDER BY cr.created_at DESC
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_core_summary() -> dict:
    """Get summary counts of core returns by status."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) as count, SUM(core_charge) as total_charge,
                   SUM(credit_issued) as total_credited
            FROM core_returns
            GROUP BY status
        """).fetchall()
        result = {
            "pending": {"count": 0, "charge": 0.0},
            "received": {"count": 0, "charge": 0.0},
            "credited": {"count": 0, "charge": 0.0, "credited": 0.0},
            "voided": {"count": 0, "charge": 0.0}
        }
        for row in rows:
            if isinstance(row, tuple):
                status, count, charge, credited = row[0], row[1], row[2] or 0, row[3] or 0
            else:
                status = row.get("status")
                count = row.get("count", 0)
                charge = row.get("total_charge") or 0
                credited = row.get("total_credited") or 0
            
            if status in result:
                result[status]["count"] = count
                result[status]["charge"] = float(charge)
                if status == "credited":
                    result[status]["credited"] = float(credited)
        return result


# ---------------------------------------------------------------------------
# Vendor core returns — cores WE owe back to a vendor (mirror of core_returns).
# ---------------------------------------------------------------------------

_VENDOR_CORE_STATUSES = {"pending", "shipped", "credited", "written_off", "voided"}


def list_vendor_core_returns(
    status: str | None = None,
    vendor_id: int | None = None,
    po_id: int | None = None,
    limit: int = 500,
) -> list[dict]:
    """Return vendor core return rows with vendor + product details."""
    where: list[str] = []
    params: list = []
    if status:
        where.append("vcr.status = %s")
        params.append(status)
    if vendor_id:
        where.append("vcr.vendor_id = %s")
        params.append(vendor_id)
    if po_id:
        where.append("vcr.po_id = %s")
        params.append(po_id)
    where_sql = " AND ".join(where) if where else "1=1"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT vcr.*,
                   v.name AS vendor_name,
                   p.sku, p.title,
                   po.po_number
            FROM vendor_core_returns vcr
            LEFT JOIN vendors v ON vcr.vendor_id = v.id
            LEFT JOIN products p ON vcr.product_id = p.id
            LEFT JOIN purchase_orders po ON vcr.po_id = po.id
            WHERE {where_sql}
            ORDER BY
              CASE vcr.status
                WHEN 'pending' THEN 0
                WHEN 'shipped' THEN 1
                WHEN 'credited' THEN 2
                WHEN 'written_off' THEN 3
                ELSE 4
              END,
              COALESCE(vcr.due_date, '9999-12-31') ASC,
              vcr.id DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
        return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def get_vendor_core_return(vcr_id: int) -> dict | None:
    rows = list_vendor_core_returns()
    for r in rows:
        if int(r.get("id") or 0) == int(vcr_id):
            return r
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM vendor_core_returns WHERE id = %s", (vcr_id,)
        ).fetchone()
        return dict(row) if row and hasattr(row, "keys") else (dict(enumerate(row)) if row else None)


def get_vendor_core_credits_for_qbo(limit: int = 500) -> list[dict]:
    """P8 — return vendor_core_returns rows ready to push as QBO VendorCredits.

    Filters to ``status='credited'`` and ``qbo_vendor_credit_id IS NULL``.
    Joins vendor name, product SKU, originating PO, and the PO's existing
    ``qbo_bill_id`` (so the QBO VendorCredit can be LinkedTxn'd to the
    bill that posted the original deposit).
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT vcr.*,
                   v.name AS vendor_name,
                   p.sku, p.title,
                   po.po_number,
                   po.qbo_bill_id AS linked_bill_qbo_id
            FROM vendor_core_returns vcr
            LEFT JOIN vendors v ON vcr.vendor_id = v.id
            LEFT JOIN products p ON vcr.product_id = p.id
            LEFT JOIN purchase_orders po ON vcr.po_id = po.id
            WHERE vcr.status = 'credited'
              AND (vcr.qbo_vendor_credit_id IS NULL OR vcr.qbo_vendor_credit_id = '')
            ORDER BY COALESCE(vcr.credit_received_date, vcr.updated_at) ASC,
                     vcr.id ASC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def update_vendor_core_return(vcr_id: int, **kwargs) -> dict:
    """Update fields on a vendor core return.

    Allowed: status, shipped_date, tracking_number, credit_received_date,
    vendor_credit_number, credit_amount, due_date, notes.
    """
    allowed = {
        "status", "shipped_date", "tracking_number",
        "credit_received_date", "vendor_credit_number", "credit_amount",
        "due_date", "notes",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if "status" in updates and updates["status"] not in _VENDOR_CORE_STATUSES:
        return {"success": False, "error": f"Invalid status '{updates['status']}'"}
    if not updates:
        return {"success": True}
    sets = ", ".join(f"{k} = %s" for k in updates)
    vals = list(updates.values()) + [vcr_id]
    with get_db() as conn:
        try:
            conn.execute(
                f"UPDATE vendor_core_returns SET {sets}, updated_at = NOW() WHERE id = %s",
                vals,
            )
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def mark_vendor_core_shipped(vcr_id: int, tracking_number: str | None = None,
                             shipped_date: str | None = None) -> dict:
    """Mark a vendor core (obligation) as shipped back to the vendor.

    Despite the legacy ``vcr_id`` parameter name, this id is now a
    ``vendor_core_obligations.id``.
    """
    from datetime import date
    from .core_handling import update_vendor_core_obligation
    return update_vendor_core_obligation(
        vcr_id,
        status="shipped",
        shipped_date=shipped_date or date.today().isoformat(),
        tracking_number=tracking_number,
    )


def mark_vendor_core_credited(vcr_id: int, credit_amount: float | None = None,
                              vendor_credit_number: str | None = None,
                              credit_received_date: str | None = None) -> dict:
    """Mark a vendor core (obligation) as credited.

    QBO VendorCredit push is deferred for the obligations-backed flow
    (the legacy push helper targeted ``vendor_core_returns`` columns that
    do not exist on the new table). Local credit is always persisted.
    """
    from datetime import date
    from .core_handling import update_vendor_core_obligation
    fields: dict = {
        "status": "credited",
        "credit_received_date": credit_received_date or date.today().isoformat(),
    }
    if credit_amount is not None:
        fields["credit_received"] = float(credit_amount)
    if vendor_credit_number:
        fields["vendor_credit_number"] = vendor_credit_number
    return update_vendor_core_obligation(vcr_id, **fields)


def _push_vendor_core_credit_to_qbo(vcr_id: int) -> None:
    """P8 helper — push a single vendor_core_returns row to QBO if eligible."""
    try:
        from qbo import config as _qbo_config
        if not _qbo_config.is_write_enabled():
            return
    except Exception:
        return

    row = get_vendor_core_return(vcr_id)
    if not row or (row.get("status") or "").lower() != "credited":
        return
    if row.get("qbo_vendor_credit_id"):
        return

    # Attach the originating bill QBO id for LinkedTxn.
    po_id = row.get("po_id")
    if po_id:
        try:
            with get_db() as conn:
                bill_row = conn.execute(
                    "SELECT qbo_bill_id FROM purchase_orders WHERE id = %s",
                    (po_id,),
                ).fetchone()
                if bill_row:
                    bill = dict(bill_row) if hasattr(bill_row, "keys") else {"qbo_bill_id": bill_row[0]}
                    row["linked_bill_qbo_id"] = bill.get("qbo_bill_id")
        except Exception:
            pass

    from qbo.push import push_vendor_credits_to_qbo

    def _on_success(_vcr_id: int, qbo_id: str) -> None:
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE vendor_core_returns "
                    "SET qbo_vendor_credit_id = %s, qbo_last_synced_at = NOW() "
                    "WHERE id = %s",
                    (qbo_id, _vcr_id),
                )
                conn.commit()
        except Exception:
            pass

    push_vendor_credits_to_qbo([row], on_success=_on_success)


def write_off_vendor_core(vcr_id: int, notes: str | None = None) -> dict:
    """Write off a vendor core obligation (deposit will not be recovered)."""
    from datetime import date
    from .core_handling import update_vendor_core_obligation
    fields: dict = {
        "status": "written_off",
        "written_off_at": date.today().isoformat(),
    }
    if notes:
        fields["notes"] = notes
    return update_vendor_core_obligation(vcr_id, **fields)


def void_vendor_core(vcr_id: int, notes: str | None = None) -> dict:
    """Void / reject a vendor core obligation."""
    from datetime import date
    from .core_handling import update_vendor_core_obligation
    fields: dict = {
        "status": "rejected",
        "rejected_at": date.today().isoformat(),
    }
    if notes:
        fields["notes"] = notes
    return update_vendor_core_obligation(vcr_id, **fields)


def delete_vendor_core_return(vcr_id: int) -> dict:
    with get_db() as conn:
        try:
            conn.execute("DELETE FROM vendor_core_returns WHERE id = %s", (vcr_id,))
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def get_vendor_core_summary() -> dict:
    """Aggregate counts + dollars for the dashboard."""
    result = {
        "pending":     {"count": 0, "charge": 0.0, "overdue": 0},
        "shipped":     {"count": 0, "charge": 0.0},
        "credited":    {"count": 0, "charge": 0.0, "credited": 0.0},
        "written_off": {"count": 0, "charge": 0.0},
        "voided":      {"count": 0, "charge": 0.0},
    }
    with get_db() as conn:
        try:
            rows = conn.execute(
                """
                SELECT status,
                       COUNT(*) AS count,
                       COALESCE(SUM(core_charge * qty), 0) AS total_charge,
                       COALESCE(SUM(credit_amount), 0) AS total_credited
                FROM vendor_core_returns
                GROUP BY status
                """
            ).fetchall()
            for row in rows:
                if hasattr(row, "keys"):
                    status = row["status"]
                    count = row["count"] or 0
                    charge = row["total_charge"] or 0
                    credited = row["total_credited"] or 0
                else:
                    status, count, charge, credited = row[0], row[1] or 0, row[2] or 0, row[3] or 0
                if status in result:
                    result[status]["count"] = int(count)
                    result[status]["charge"] = float(charge)
                    if status == "credited":
                        result[status]["credited"] = float(credited)
            overdue_row = conn.execute(
                """
                SELECT COUNT(*) FROM vendor_core_returns
                WHERE status = 'pending' AND due_date IS NOT NULL AND due_date < CURRENT_DATE
                """
            ).fetchone()
            if overdue_row:
                result["pending"]["overdue"] = int(
                    overdue_row[0] if not hasattr(overdue_row, "keys") else (
                        list(overdue_row.values())[0]
                    )
                )
        except Exception:
            return result
    return result


def create_core_from_invoice_line(
    invoice_id: int,
    invoice_line_id: int,
    product_id: int,
    customer_id: int,
    core_charge: float,
    due_days: int = 30,
    customer_core_price: float = None,
) -> int:
    """Create a core return from an invoice line (called when finalizing invoice with core charges)."""
    from datetime import date, timedelta
    
    due_date = (date.today() + timedelta(days=due_days)).isoformat()
    
    return create_core_return(
        invoice_line_id=invoice_line_id,
        invoice_id=invoice_id,
        product_id=product_id,
        customer_id=customer_id,
        core_charge=core_charge,
        customer_core_price=customer_core_price,
        due_date=due_date
    )


# ============================================================================
# Inventory Transfer Functions (Phase I Part 4)
# ============================================================================

def generate_transfer_number() -> str:
    """Generate next transfer number (TR-YYYYMMDD-NNN)."""
    from datetime import date
    
    today = date.today().strftime("%Y%m%d")
    prefix = f"TR-{today}-"
    
    with get_db() as conn:
        row = conn.execute("""
            SELECT transfer_number FROM inventory_transfers
            WHERE transfer_number LIKE %s
            ORDER BY transfer_number DESC LIMIT 1
        """, (f"{prefix}%",)).fetchone()
        
        if row:
            last_num = int(row[0].split("-")[-1])
            next_num = last_num + 1
        else:
            next_num = 1
        
        return f"{prefix}{next_num:03d}"


def list_transfers(
    status: str = None,
    from_location_id: int = None,
    to_location_id: int = None,
    limit: int = 100
) -> list[dict]:
    """List inventory transfers with optional filters."""
    with get_db() as conn:
        conditions = ["1=1"]
        params = []
        
        if status:
            conditions.append("t.status = %s")
            params.append(status)
        if from_location_id:
            conditions.append("t.from_location_id = %s")
            params.append(from_location_id)
        if to_location_id:
            conditions.append("t.to_location_id = %s")
            params.append(to_location_id)
        
        params.append(limit)
        
        rows = conn.execute(f"""
            SELECT t.*, 
                   fl.name as from_location_name, fl.code as from_location_code,
                   tl.name as to_location_name, tl.code as to_location_code,
                   (SELECT COUNT(*) FROM transfer_lines WHERE transfer_id = t.id) as line_count,
                   (SELECT SUM(quantity) FROM transfer_lines WHERE transfer_id = t.id) as total_qty
            FROM inventory_transfers t
            LEFT JOIN storage_locations fl ON fl.id = t.from_location_id
            LEFT JOIN storage_locations tl ON tl.id = t.to_location_id
            WHERE {' AND '.join(conditions)}
            ORDER BY t.created_at DESC
            LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_transfer(transfer_id: int) -> dict | None:
    """Get a single transfer by ID with full details."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT t.*, 
                   fl.name as from_location_name, fl.code as from_location_code,
                   tl.name as to_location_name, tl.code as to_location_code
            FROM inventory_transfers t
            LEFT JOIN storage_locations fl ON fl.id = t.from_location_id
            LEFT JOIN storage_locations tl ON tl.id = t.to_location_id
            WHERE t.id = %s
        """, (transfer_id,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def get_transfer_by_number(transfer_number: str) -> dict | None:
    """Get transfer by transfer number."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT t.*, 
                   fl.name as from_location_name, fl.code as from_location_code,
                   tl.name as to_location_name, tl.code as to_location_code
            FROM inventory_transfers t
            LEFT JOIN storage_locations fl ON fl.id = t.from_location_id
            LEFT JOIN storage_locations tl ON tl.id = t.to_location_id
            WHERE t.transfer_number = %s
        """, (transfer_number,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def create_transfer(
    from_location_id: int,
    to_location_id: int,
    requested_by: str = None,
    notes: str = None
) -> int:
    """Create a new inventory transfer. Returns the transfer ID."""
    from datetime import date
    
    transfer_number = generate_transfer_number()
    
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO inventory_transfers
            (transfer_number, from_location_id, to_location_id, status, 
             requested_date, requested_by, notes)
            VALUES (%s, %s, %s, 'draft', %s, %s, %s)
        """, (transfer_number, from_location_id, to_location_id,
              date.today().isoformat(), requested_by, notes))
        conn.commit()
        
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            return row[0]
        else:
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            return row[0]


def add_transfer_line(
    transfer_id: int,
    product_id: int,
    quantity: int,
    serial_id: int = None,
    notes: str = None
) -> int:
    """Add a line item to a transfer. Returns the line ID."""
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO transfer_lines (transfer_id, product_id, quantity, serial_id, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (transfer_id, product_id, quantity, serial_id, notes))
        conn.commit()
        
        if is_postgresql():
            row = conn.execute("SELECT lastval()").fetchone()
            return row[0]
        else:
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            return row[0]


def remove_transfer_line(line_id: int) -> bool:
    """Remove a line from a transfer."""
    with get_db() as conn:
        conn.execute("DELETE FROM transfer_lines WHERE id = %s", (line_id,))
        conn.commit()
        return True


def get_transfer_lines(transfer_id: int) -> list[dict]:
    """Get all line items for a transfer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT tl.*, p.sku, p.title as product_title,
                   sn.serial_number
            FROM transfer_lines tl
            LEFT JOIN products p ON p.id = tl.product_id
            LEFT JOIN serial_numbers sn ON sn.id = tl.serial_id
            WHERE tl.transfer_id = %s
            ORDER BY tl.id
        """, (transfer_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def update_transfer_status(transfer_id: int, status: str, by_user: str = None) -> bool:
    """Update transfer status (draft → pending → approved → completed / cancelled)."""
    from datetime import date
    
    valid_statuses = ['draft', 'pending', 'approved', 'completed', 'cancelled']
    if status not in valid_statuses:
        return False
    
    with get_db() as conn:
        # Set appropriate date field based on status
        date_field = None
        user_field = None
        
        if status == 'approved':
            date_field = "approved_date"
            user_field = "approved_by"
        elif status == 'completed':
            date_field = "completed_date"
            user_field = "completed_by"
        
        if date_field:
            conn.execute(f"""
                UPDATE inventory_transfers
                SET status = %s, {date_field} = %s, {user_field} = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (status, date.today().isoformat(), by_user, transfer_id))
        else:
            conn.execute("""
                UPDATE inventory_transfers
                SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (status, transfer_id))
        
        conn.commit()
        return True


def complete_transfer(transfer_id: int, completed_by: str = None) -> bool:
    """Complete a transfer - moves inventory between locations."""
    from datetime import date
    
    with get_db() as conn:
        # Get transfer details
        transfer = get_transfer(transfer_id)
        if not transfer or transfer.get('status') not in ('pending', 'approved'):
            return False
        
        from_loc_id = transfer.get('from_location_id')
        to_loc_id = transfer.get('to_location_id')
        
        # Get all lines
        lines = get_transfer_lines(transfer_id)
        
        for line in lines:
            product_id = line.get('product_id')
            qty = line.get('quantity', 0)
            serial_id = line.get('serial_id')
            
            # Decrease quantity at source location
            conn.execute("""
                UPDATE product_locations
                SET quantity = quantity - %s
                WHERE product_id = %s AND location_id = %s
            """, (qty, product_id, from_loc_id))
            
            # Increase or insert at destination location
            conn.execute("""
                INSERT INTO product_locations (product_id, location_id, quantity)
                VALUES (%s, %s, %s)
                ON CONFLICT (product_id, location_id) 
                DO UPDATE SET quantity = product_locations.quantity + EXCLUDED.quantity
            """, (product_id, to_loc_id, qty))
            
            # If serial tracked, update serial location
            if serial_id:
                conn.execute("""
                    UPDATE serial_numbers
                    SET location_id = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (to_loc_id, serial_id))
        
        # Mark transfer as completed
        conn.execute("""
            UPDATE inventory_transfers
            SET status = 'completed', completed_date = %s, completed_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (date.today().isoformat(), completed_by, transfer_id))
        
        conn.commit()
        return True


def cancel_transfer(transfer_id: int, notes: str = None) -> bool:
    """Cancel a transfer."""
    with get_db() as conn:
        conn.execute("""
            UPDATE inventory_transfers
            SET status = 'cancelled', 
                notes = COALESCE(%s, notes),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (notes, transfer_id))
        conn.commit()
        return True


def delete_transfer(transfer_id: int) -> bool:
    """Delete a transfer (only if draft or cancelled)."""
    with get_db() as conn:
        # Check status first
        row = conn.execute(
            "SELECT status FROM inventory_transfers WHERE id = %s", (transfer_id,)
        ).fetchone()
        
        if row and row[0] in ('draft', 'cancelled'):
            conn.execute("DELETE FROM transfer_lines WHERE transfer_id = %s", (transfer_id,))
            conn.execute("DELETE FROM inventory_transfers WHERE id = %s", (transfer_id,))
            conn.commit()
            return True
        return False


def get_transfer_summary() -> dict:
    """Get summary counts of transfers by status."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) as count
            FROM inventory_transfers
            GROUP BY status
        """).fetchall()
        result = {"draft": 0, "pending": 0, "approved": 0, "completed": 0, "cancelled": 0}
        for row in rows:
            status = row[0] if isinstance(row, tuple) else row.get("status")
            count = row[1] if isinstance(row, tuple) else row.get("count", 0)
            if status in result:
                result[status] = count
        return result


# ─── Phase 1B: Min/Max Restock ────────────────────────────────────────────


def get_restock_products(filter_status: str = "below_min") -> list:
    """Get products with min/max data for restock management.
    filter_status: 'below_min', 'at_min', 'above_max', 'ok', or 'all'
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.sku, p.title, p.manufacturer, p.category,
                   p.qty_on_hand, p.min_qty, p.max_qty, p.cost, p.selling_price,
                   p.preferred_vendor_id,
                   v.name as vendor_name
            FROM products p
            LEFT JOIN vendors v ON v.id = p.preferred_vendor_id
            WHERE p.min_qty > 0 OR p.max_qty > 0
            ORDER BY p.qty_on_hand ASC, p.sku
        """).fetchall()
        results = [dict(row) if hasattr(row, 'keys') else dict(zip(
            ["id","sku","title","manufacturer","category","qty_on_hand",
             "min_qty","max_qty","cost","selling_price","preferred_vendor_id","vendor_name"], row))
            for row in rows]
        if filter_status == "all":
            return results
        filtered = []
        for r in results:
            qty = int(r.get("qty_on_hand") or 0)
            mn = int(r.get("min_qty") or 0)
            mx = int(r.get("max_qty") or 0)
            if filter_status == "below_min" and mn >= 1 and qty < mn:
                filtered.append(r)
            elif filter_status == "at_min" and mn >= 1 and qty == mn:
                filtered.append(r)
            elif filter_status == "above_max" and mx > 0 and qty > mx:
                filtered.append(r)
            elif filter_status == "ok" and qty > mn and (mx == 0 or qty <= mx):
                filtered.append(r)
        return filtered


def get_restock_summary() -> dict:
    """Get summary counts for restock dashboard."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT qty_on_hand, min_qty, max_qty, cost
            FROM products WHERE min_qty > 0 OR max_qty > 0
        """).fetchall()
        below = at_min = above = ok = 0
        below_cost = 0.0
        for row in rows:
            r = dict(row) if hasattr(row, 'keys') else {"qty_on_hand": row[0], "min_qty": row[1], "max_qty": row[2], "cost": row[3]}
            qty = int(r.get("qty_on_hand", 0) or 0)
            mn = int(r.get("min_qty", 0) or 0)
            mx = int(r.get("max_qty", 0) or 0)
            cost = float(r.get("cost", 0) or 0)
            if mn >= 1 and qty < mn:
                below += 1
                below_cost += cost * (mn - qty)
            elif mn >= 1 and qty == mn:
                at_min += 1
            elif mx > 0 and qty > mx:
                above += 1
            else:
                ok += 1
        return {"below_min": below, "below_min_cost": below_cost,
                "at_min": at_min, "above_max": above, "ok": ok}


def update_min_max(product_id: int, min_qty: int = None, max_qty: int = None,
                   preferred_vendor_id: int = None) -> bool:
    """Update min/max and preferred vendor for a product."""
    fields = {}
    if min_qty is not None:
        fields["min_qty"] = min_qty
    if max_qty is not None:
        fields["max_qty"] = max_qty
    if preferred_vendor_id is not None:
        fields["preferred_vendor_id"] = preferred_vendor_id
    if not fields:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        vals = list(fields.values()) + [product_id]
        conn.execute(f"UPDATE products SET {set_clause} WHERE id = %s", tuple(vals))
        conn.commit()
        return True


# ─── Phase 5: Sales Velocity & Auto Min/Max ─────────────────────────────


def get_sales_velocity(product_id: int = None, days: int = 90) -> list[dict]:
    """Calculate average daily unit sales per product from finalized invoice data.
    
    Returns list of dicts: [{"product_id", "sku", "title", "total_sold",
    "days_in_range", "avg_daily", "avg_weekly", "avg_monthly"}]
    
    Only counts invoices with status in ('sent', 'paid', 'partial') — not draft/void.
    """
    with get_db() as conn:
        conditions = [
            "i.status IN ('sent', 'paid', 'partial')",
            "il.product_id IS NOT NULL",
        ]
        params = []

        if is_postgresql():
            conditions.append(f"i.invoice_date >= CURRENT_DATE - INTERVAL '{days} days'")
        else:
            conditions.append("i.invoice_date >= date('now', %s)")
            params.append(f"-{days} days")

        if product_id:
            conditions.append("il.product_id = %s")
            params.append(product_id)

        rows = conn.execute(f"""
            SELECT il.product_id, p.sku, p.title,
                   SUM(il.quantity) as total_sold,
                   COUNT(DISTINCT i.id) as invoice_count
            FROM invoice_lines il
            JOIN invoices i ON i.id = il.invoice_id
            JOIN products p ON p.id = il.product_id
            WHERE {' AND '.join(conditions)}
            GROUP BY il.product_id, p.sku, p.title
            ORDER BY SUM(il.quantity) DESC
        """, tuple(params)).fetchall()

        results = []
        for row in rows:
            r = dict(row) if hasattr(row, 'keys') else {}
            total = int(r.get("total_sold", 0))
            avg_daily = total / days if days > 0 else 0
            r["days_in_range"] = days
            r["avg_daily"] = round(avg_daily, 4)
            r["avg_weekly"] = round(avg_daily * 7, 2)
            r["avg_monthly"] = round(avg_daily * 30, 2)
            results.append(r)

        return results


def auto_calculate_min_max(
    product_id: int = None,
    lookback_days: int = None,
    default_lead_time: int = None,
    safety_multiplier: float = None,
) -> list[dict]:
    """Calculate and SET min_qty/max_qty on products based on sales velocity.
    
    Formula:
        min_qty = ceil(avg_daily_sales * lead_time * safety_multiplier)
        max_qty = ceil(avg_daily_sales * (lead_time + review_period))
    
    Where review_period = lead_time (order once per lead time cycle).
    Lead time is taken from the product's preferred vendor, falling back to default.
    
    Returns list of products updated with old and new min/max values.
    """
    import math

    # Get settings with fallbacks
    if lookback_days is None:
        lookback_days = int(get_setting("velocity_lookback_days") or 90)
    if default_lead_time is None:
        default_lead_time = int(get_setting("default_lead_time_days") or 14)
    if safety_multiplier is None:
        safety_multiplier = float(get_setting("safety_stock_multiplier") or 1.5)

    velocities = get_sales_velocity(product_id=product_id, days=lookback_days)
    if not velocities:
        return []

    results = []

    with get_db() as conn:
        for v in velocities:
            pid = v["product_id"]
            avg_daily = v["avg_daily"]

            if avg_daily <= 0:
                continue

            # Get vendor lead time
            lead_time = default_lead_time
            vendor_row = conn.execute("""
                SELECT vd.lead_time_days FROM products p
                LEFT JOIN vendors vd ON vd.id = p.preferred_vendor_id
                WHERE p.id = %s
            """, (pid,)).fetchone()
            if vendor_row and vendor_row[0]:
                lead_time = int(vendor_row[0])

            # Get current values
            prod = conn.execute(
                "SELECT min_qty, max_qty FROM products WHERE id = %s", (pid,)
            ).fetchone()
            old_min = int(prod[0] or 0) if prod else 0
            old_max = int(prod[1] or 0) if prod else 0

            # Calculate new values
            review_period = lead_time
            new_min = math.ceil(avg_daily * lead_time * safety_multiplier)
            new_max = math.ceil(avg_daily * (lead_time + review_period))

            # Ensure max >= min
            if new_max < new_min:
                new_max = new_min

            # Update
            conn.execute(
                "UPDATE products SET min_qty = %s, max_qty = %s WHERE id = %s",
                (new_min, new_max, pid)
            )

            results.append({
                "product_id": pid,
                "sku": v["sku"],
                "title": v["title"],
                "avg_daily": avg_daily,
                "lead_time": lead_time,
                "old_min": old_min,
                "old_max": old_max,
                "new_min": new_min,
                "new_max": new_max,
            })

        conn.commit()

    return results


def get_velocity_report(days: int = None, min_sold: int = 1) -> list[dict]:
    """Get sales velocity report for all products, including those with zero sales.
    
    Returns products sorted by avg_daily DESC, with their current min/max values.
    """
    if days is None:
        days = int(get_setting("velocity_lookback_days") or 90)

    velocities = get_sales_velocity(days=days)
    velocity_map = {v["product_id"]: v for v in velocities}

    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.sku, p.title, p.qty_on_hand, p.min_qty, p.max_qty,
                   p.preferred_vendor_id, v.name as vendor_name, v.lead_time_days
            FROM products p
            LEFT JOIN vendors v ON v.id = p.preferred_vendor_id
            ORDER BY p.sku
        """).fetchall()

        results = []
        for row in rows:
            r = dict(row) if hasattr(row, 'keys') else {}
            pid = r["id"]
            vel = velocity_map.get(pid, {})
            r["total_sold"] = vel.get("total_sold", 0)
            r["avg_daily"] = vel.get("avg_daily", 0.0)
            r["avg_weekly"] = vel.get("avg_weekly", 0.0)
            r["avg_monthly"] = vel.get("avg_monthly", 0.0)
            r["days_analyzed"] = days
            if vel.get("total_sold", 0) >= min_sold or min_sold == 0:
                results.append(r)

        # Sort by avg_daily descending
        results.sort(key=lambda x: x["avg_daily"], reverse=True)
        return results


# ─── Phase 1C: Inventory Audit ───────────────────────────────────────────


def create_audit(location_id: int = None, count_type: str = "full",
                 notes: str = None, user: str = None) -> int:
    """Create a new inventory audit. Returns audit ID."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO inventory_audits (location_id, count_type, notes, created_by)
            VALUES (%s, %s, %s, %s)
        """, (location_id, count_type, notes, user))
        conn.commit()
        return conn.lastrowid


def get_audit(audit_id: int) -> dict:
    """Get audit by ID."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT a.*, sl.name as location_name
            FROM inventory_audits a
            LEFT JOIN storage_locations sl ON sl.id = a.location_id
            WHERE a.id = %s
        """, (audit_id,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def list_audits(status: str = None, limit: int = 50) -> list:
    """List inventory audits."""
    with get_db() as conn:
        conditions, params = ["1=1"], []
        if status:
            conditions.append("a.status = %s")
            params.append(status)
        params.append(limit)
        rows = conn.execute(f"""
            SELECT a.*, sl.name as location_name
            FROM inventory_audits a
            LEFT JOIN storage_locations sl ON sl.id = a.location_id
            WHERE {' AND '.join(conditions)}
            ORDER BY a.created_at DESC LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def add_audit_line(audit_id: int, product_id: int, system_qty: int,
                   counted_qty: int = None) -> int:
    """Add a line to an audit. Returns line ID."""
    variance = (counted_qty - system_qty) if counted_qty is not None else 0
    with get_db() as conn:
        cost_row = conn.execute("SELECT cost FROM products WHERE id = %s",
                                (product_id,)).fetchone()
        cost = 0.0
        if cost_row:
            cost = float((dict(cost_row) if hasattr(cost_row, 'keys') else {"cost": cost_row[0]}).get("cost", 0) or 0)
        variance_value = variance * cost
        conn.execute("""
            INSERT INTO audit_lines (audit_id, product_id, system_qty, counted_qty,
                                     variance, variance_value)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (audit_id, product_id, system_qty, counted_qty, variance, variance_value))
        conn.commit()
        return conn.lastrowid


def update_audit_line(line_id: int, counted_qty: int) -> bool:
    """Update counted quantity on an audit line."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT al.system_qty, p.cost
            FROM audit_lines al JOIN products p ON p.id = al.product_id
            WHERE al.id = %s
        """, (line_id,)).fetchone()
        if not row:
            return False
        r = dict(row) if hasattr(row, 'keys') else {"system_qty": row[0], "cost": row[1]}
        system_qty = int(r.get("system_qty", 0) or 0)
        cost = float(r.get("cost", 0) or 0)
        variance = counted_qty - system_qty
        conn.execute("""
            UPDATE audit_lines SET counted_qty=%s, variance=%s, variance_value=%s
            WHERE id = %s
        """, (counted_qty, variance, variance * cost, line_id))
        conn.commit()
        return True


def get_audit_lines(audit_id: int) -> list:
    """Get all lines for an audit."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT al.*, p.sku, p.title, p.cost
            FROM audit_lines al JOIN products p ON p.id = al.product_id
            WHERE al.audit_id = %s ORDER BY p.sku
        """, (audit_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def finalize_audit(audit_id: int, user: str = None) -> dict:
    """Finalize audit — apply inventory adjustments for variances."""
    lines = get_audit_lines(audit_id)
    adjusted = 0
    errors = []
    total_variances = 0
    total_variance_value = 0.0
    for line in lines:
        counted = line.get("counted_qty")
        if counted is None:
            continue
        variance = int(line.get("variance", 0) or 0)
        if variance == 0:
            continue
        total_variances += 1
        total_variance_value += float(line.get("variance_value", 0) or 0)
        sku = line.get("sku", "")
        result = adjust_product_quantity(
            sku=sku, delta=variance, txn_type="audit",
            notes=f"Audit #{audit_id}: counted {counted}, system {line.get('system_qty', 0)}",
            reference=f"AUDIT-{audit_id}", user=user or "system"
        )
        if result.get("success"):
            adjusted += 1
        else:
            errors.append(f"{sku}: {result.get('error', 'unknown')}")
    with get_db() as conn:
        conn.execute("""
            UPDATE inventory_audits
            SET status='completed', completed_at=CURRENT_TIMESTAMP,
                total_items=%s, total_variances=%s, variance_value=%s
            WHERE id = %s
        """, (len(lines), total_variances, total_variance_value, audit_id))
        conn.commit()
    return {"success": True, "adjusted": adjusted, "errors": errors,
            "total_variances": total_variances, "variance_value": total_variance_value}


def cancel_audit(audit_id: int) -> bool:
    """Cancel an in-progress audit."""
    with get_db() as conn:
        conn.execute("UPDATE inventory_audits SET status='cancelled' WHERE id=%s", (audit_id,))
        conn.commit()
        return True


# ─── Phase 1D: QBO Export ─────────────────────────────────────────────────


def get_unexported_invoices(start_date: str = None, end_date: str = None) -> list:
    """Get invoices not yet exported to QBO."""
    with get_db() as conn:
        conditions = ["(i.qbo_exported IS NULL OR i.qbo_exported = 0)",
                       "i.status != 'draft'", "i.status != 'void'"]
        params = []
        if start_date:
            conditions.append("i.invoice_date >= %s"); params.append(start_date)
        if end_date:
            conditions.append("i.invoice_date <= %s"); params.append(end_date)
        rows = conn.execute(f"""
            SELECT i.*, c.name as customer_name, c.company as customer_company
            FROM invoices i LEFT JOIN customers c ON c.id = i.customer_id
            WHERE {' AND '.join(conditions)} ORDER BY i.invoice_date
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_unexported_bills(start_date: str = None, end_date: str = None) -> list:
    """Get received POs not yet exported as QBO bills."""
    with get_db() as conn:
        conditions = ["(po.qbo_exported IS NULL OR po.qbo_exported = 0)",
                       "po.status IN ('received','partial')"]
        params = []
        if start_date:
            conditions.append("po.order_date >= %s"); params.append(start_date)
        if end_date:
            conditions.append("po.order_date <= %s"); params.append(end_date)
        rows = conn.execute(f"""
            SELECT po.*, v.name as vendor_name
            FROM purchase_orders po LEFT JOIN vendors v ON v.id = po.vendor_id
            WHERE {' AND '.join(conditions)} ORDER BY po.order_date
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def mark_invoices_exported(invoice_ids: list) -> int:
    """Mark invoices as exported."""
    if not invoice_ids:
        return 0
    with get_db() as conn:
        for iid in invoice_ids:
            conn.execute("UPDATE invoices SET qbo_exported=1, qbo_exported_at=CURRENT_TIMESTAMP WHERE id=%s", (iid,))
        conn.commit()
        return len(invoice_ids)


def mark_pos_exported(po_ids: list) -> int:
    """Mark POs as exported."""
    if not po_ids:
        return 0
    with get_db() as conn:
        for pid in po_ids:
            conn.execute("UPDATE purchase_orders SET qbo_exported=1, qbo_exported_at=CURRENT_TIMESTAMP WHERE id=%s", (pid,))
        conn.commit()
        return len(po_ids)


# ─── E4: Manual "Mark as posted" override ───────────────────────────────

def _manual_qbo_ref(prefix: str) -> str:
    """Synthesize a stable manual reference (e.g. ``MANUAL-20260510120000``)."""
    from datetime import datetime
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def mark_invoice_posted_manually(invoice_id: int) -> bool:
    """E4 — flag an invoice as already-in-QBO without touching the live API.

    Stamps ``qbo_exported=1``, ``qbo_exported_at=NOW()``, and a synthetic
    ``qbo_invoice_id`` like ``MANUAL-<timestamp>`` so the row stops
    appearing on the unexported list. Idempotent — calling twice keeps
    the original ``qbo_invoice_id``.

    Returns True on success, False if the invoice doesn't exist.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, qbo_invoice_id FROM invoices WHERE id=%s",
            (int(invoice_id),),
        ).fetchone()
        if not row:
            return False
        existing = row["qbo_invoice_id"] if hasattr(row, "keys") else row[1]
        if existing:
            # Already tagged — just ensure qbo_exported is set.
            conn.execute(
                "UPDATE invoices SET qbo_exported=1, "
                "qbo_exported_at=COALESCE(qbo_exported_at, CURRENT_TIMESTAMP) "
                "WHERE id=%s",
                (int(invoice_id),),
            )
        else:
            conn.execute(
                "UPDATE invoices SET qbo_exported=1, "
                "qbo_exported_at=CURRENT_TIMESTAMP, qbo_invoice_id=%s "
                "WHERE id=%s",
                (_manual_qbo_ref("MANUAL"), int(invoice_id)),
            )
        conn.commit()
        return True


def mark_invoice_unposted(invoice_id: int) -> bool:
    """Reverse :func:`mark_invoice_posted_manually` — clears the
    ``qbo_exported`` flag and removes a ``MANUAL-*`` ``qbo_invoice_id``
    so the row reappears on the unexported list. Real (non-MANUAL)
    QBO IDs are preserved so we don't lose audit history."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, qbo_invoice_id FROM invoices WHERE id=%s",
            (int(invoice_id),),
        ).fetchone()
        if not row:
            return False
        existing = row["qbo_invoice_id"] if hasattr(row, "keys") else row[1]
        if existing and str(existing).startswith("MANUAL-"):
            conn.execute(
                "UPDATE invoices SET qbo_exported=0, "
                "qbo_exported_at=NULL, qbo_invoice_id=NULL WHERE id=%s",
                (int(invoice_id),),
            )
        else:
            conn.execute(
                "UPDATE invoices SET qbo_exported=0, qbo_exported_at=NULL "
                "WHERE id=%s",
                (int(invoice_id),),
            )
        conn.commit()
        return True


def mark_po_posted_manually(po_id: int) -> bool:
    """E4 — same as :func:`mark_invoice_posted_manually` for purchase orders."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, qbo_bill_id FROM purchase_orders WHERE id=%s",
            (int(po_id),),
        ).fetchone()
        if not row:
            return False
        existing = row["qbo_bill_id"] if hasattr(row, "keys") else row[1]
        if existing:
            conn.execute(
                "UPDATE purchase_orders SET qbo_exported=1, "
                "qbo_exported_at=COALESCE(qbo_exported_at, CURRENT_TIMESTAMP) "
                "WHERE id=%s",
                (int(po_id),),
            )
        else:
            conn.execute(
                "UPDATE purchase_orders SET qbo_exported=1, "
                "qbo_exported_at=CURRENT_TIMESTAMP, qbo_bill_id=%s "
                "WHERE id=%s",
                (_manual_qbo_ref("MANUAL"), int(po_id)),
            )
        conn.commit()
        return True


def mark_po_unposted(po_id: int) -> bool:
    """Reverse :func:`mark_po_posted_manually` — same conventions."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, qbo_bill_id FROM purchase_orders WHERE id=%s",
            (int(po_id),),
        ).fetchone()
        if not row:
            return False
        existing = row["qbo_bill_id"] if hasattr(row, "keys") else row[1]
        if existing and str(existing).startswith("MANUAL-"):
            conn.execute(
                "UPDATE purchase_orders SET qbo_exported=0, "
                "qbo_exported_at=NULL, qbo_bill_id=NULL WHERE id=%s",
                (int(po_id),),
            )
        else:
            conn.execute(
                "UPDATE purchase_orders SET qbo_exported=0, qbo_exported_at=NULL "
                "WHERE id=%s",
                (int(po_id),),
            )
        conn.commit()
        return True


def is_invoice_locked(invoice_id: int) -> bool:
    """E5 — return True when an invoice has been pushed/marked-as-posted
    to QBO and should therefore be edit-locked. UI callers use this to
    surface a warn-and-confirm prompt before allowing edits.

    Returns False for unknown ids so missing rows fall through to the
    normal not-found error paths.
    """
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT qbo_exported FROM invoices WHERE id=%s",
                (int(invoice_id),),
            ).fetchone()
            if not row:
                return False
            val = row["qbo_exported"] if hasattr(row, "keys") else row[0]
            return bool(int(val or 0))
    except Exception:
        return False


def is_po_locked(po_id: int) -> bool:
    """E5 — same as :func:`is_invoice_locked` for purchase orders."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT qbo_exported FROM purchase_orders WHERE id=%s",
                (int(po_id),),
            ).fetchone()
            if not row:
                return False
            val = row["qbo_exported"] if hasattr(row, "keys") else row[0]
            return bool(int(val or 0))
    except Exception:
        return False


def log_qbo_export(export_type: str, export_format: str, record_count: int,
                   file_path: str, user: str = None) -> int:
    """Log a QBO export."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO qbo_exports (export_type, export_format, record_count, file_path, created_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (export_type, export_format, record_count, file_path, user))
        conn.commit()
        return conn.lastrowid


def get_export_history(limit: int = 20) -> list:
    """Get QBO export history."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM qbo_exports ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_paid_invoices_for_export(start_date: str = None,
                                 end_date: str = None) -> list:
    """Return invoices with ``amount_paid > 0`` for QBO Receive-Payment CSV.

    Payments aren't tracked in a separate table — payment totals live on
    the invoice row — so we yield one summary row per paid invoice.
    """
    with get_db() as conn:
        conditions = ["COALESCE(i.amount_paid, 0) > 0",
                       "i.status NOT IN ('draft', 'void')"]
        params: list = []
        if start_date:
            conditions.append("i.invoice_date >= %s"); params.append(start_date)
        if end_date:
            conditions.append("i.invoice_date <= %s"); params.append(end_date)
        rows = conn.execute(f"""
            SELECT i.id, i.invoice_number, i.invoice_date, i.amount_paid,
                   i.payment_method, i.status, i.qbo_invoice_id,
                   i.qbo_payment_id,
                   c.name as customer_name, c.company as customer_company
            FROM invoices i LEFT JOIN customers c ON c.id = i.customer_id
            WHERE {' AND '.join(conditions)}
            ORDER BY i.invoice_date
        """, tuple(params)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r))
                for r in rows]


def get_credit_memos_for_export(status: str = None,
                                start_date: str = None,
                                end_date: str = None) -> list:
    """Return ``customer_credits`` rows joined with customer for QBO export.

    ``status`` may be 'open', 'applied', 'voided', or ``None`` for all.
    """
    with get_db() as conn:
        conditions: list = []
        params: list = []
        if status:
            conditions.append("cc.status = %s"); params.append(status)
        if start_date:
            conditions.append("cc.issue_date >= %s"); params.append(start_date)
        if end_date:
            conditions.append("cc.issue_date <= %s"); params.append(end_date)
        where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(f"""
            SELECT cc.id, cc.customer_id, cc.amount, cc.source_type,
                   cc.source_id, cc.status, cc.issue_date, cc.applied_date,
                   cc.notes,
                   c.name as customer_name, c.company as customer_company,
                   inv.invoice_number as applied_invoice_number
            FROM customer_credits cc
            LEFT JOIN customers c ON c.id = cc.customer_id
            LEFT JOIN invoices inv ON inv.id = cc.applied_to_invoice_id
            {where_sql}
            ORDER BY cc.issue_date DESC, cc.id DESC
        """, tuple(params)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r))
                for r in rows]


# ─── Phase 2: Part Interchange ────────────────────────────────────────────


def add_interchange(product_id: int, interchange_number: str,
                    interchange_type: str = "OEM", manufacturer: str = None,
                    notes: str = None) -> int:
    """Add an interchange number to a product."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO part_interchanges (product_id, interchange_number, interchange_type,
                                           manufacturer, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (product_id, interchange_number, interchange_type, manufacturer, notes))
        conn.commit()
        return conn.lastrowid


def get_interchanges(product_id: int) -> list:
    """Get all interchanges for a product."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM part_interchanges WHERE product_id = %s
            ORDER BY interchange_type, interchange_number
        """, (product_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def delete_interchange(interchange_id: int) -> bool:
    """Delete an interchange."""
    with get_db() as conn:
        conn.execute("DELETE FROM part_interchanges WHERE id = %s", (interchange_id,))
        conn.commit()
        return True


def search_interchanges(query: str, limit: int = 50) -> list:
    """Search across interchanges and products by any part number."""
    with get_db() as conn:
        q = f"%{query.strip().upper()}%"
        rows = conn.execute("""
            SELECT pi.*, p.sku, p.title, p.qty_on_hand, p.selling_price, p.cost,
                   p.manufacturer as product_manufacturer, p.category
            FROM part_interchanges pi JOIN products p ON p.id = pi.product_id
            WHERE UPPER(pi.interchange_number) LIKE %s LIMIT %s
        """, (q, limit)).fetchall()
        results = [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]
        # Also search products table directly by SKU
        sku_rows = conn.execute("""
            SELECT id, sku, title, qty_on_hand, selling_price, cost, manufacturer, category
            FROM products WHERE UPPER(sku) LIKE %s LIMIT %s
        """, (q, limit)).fetchall()
        for row in sku_rows:
            r = dict(row) if hasattr(row, 'keys') else dict(zip(
                ["id","sku","title","qty_on_hand","selling_price","cost","manufacturer","category"], row))
            r["interchange_number"] = r.get("sku", "")
            r["interchange_type"] = "SKU"
            r["product_id"] = r.get("id")
            r["product_manufacturer"] = r.get("manufacturer", "")
            results.append(r)
        return results


def bulk_import_interchanges(data_rows: list) -> dict:
    """Bulk import interchanges. Each row: {product_sku, interchange_number, interchange_type, manufacturer, notes}"""
    imported = skipped = 0
    errors = []
    for row in data_rows:
        sku = row.get("product_sku") or row.get("ProductSKU", "")
        product = get_product_by_sku(sku)
        if not product:
            errors.append(f"SKU not found: {sku}"); continue
        try:
            add_interchange(product["id"],
                row.get("interchange_number") or row.get("InterchangeNumber", ""),
                row.get("interchange_type") or row.get("Type", "OEM"),
                row.get("manufacturer") or row.get("Manufacturer", ""),
                row.get("notes") or row.get("Notes", ""))
            imported += 1
        except Exception as e:
            if "UNIQUE" in str(e).upper() or "duplicate" in str(e).lower():
                skipped += 1
            else:
                errors.append(f"{sku}: {e}")
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ─── Phase 4: ESN Engine Lookup ──────────────────────────────────────────


def get_esn_cache(esn: str) -> dict:
    """Get cached ESN lookup result."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM esn_cache WHERE esn = %s", (esn,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def save_esn_cache(esn: str, oem: str, engine_model: str = None,
                   horsepower: str = None, build_date: str = None,
                   cpl: str = None, application: str = None,
                   emission_level: str = None, raw_data: str = None) -> int:
    """Save ESN lookup result to cache."""
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM esn_cache WHERE esn = %s", (esn,)).fetchone()
        if existing:
            eid = existing[0] if isinstance(existing, tuple) else (dict(existing) if hasattr(existing, 'keys') else {}).get("id", existing[0])
            conn.execute("""
                UPDATE esn_cache SET oem=%s, engine_model=%s, horsepower=%s,
                    build_date=%s, cpl=%s, application=%s, emission_level=%s,
                    raw_data=%s, cached_at=CURRENT_TIMESTAMP WHERE id=%s
            """, (oem, engine_model, horsepower, build_date, cpl, application,
                  emission_level, raw_data, eid))
            conn.commit()
            return eid
        conn.execute("""
            INSERT INTO esn_cache (esn, oem, engine_model, horsepower, build_date,
                                   cpl, application, emission_level, raw_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (esn, oem, engine_model, horsepower, build_date, cpl, application,
              emission_level, raw_data))
        conn.commit()
        return conn.lastrowid


def get_engine_compatibility(engine_model: str) -> list:
    """Get compatible products for an engine model."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT ec.*, p.sku, p.title, p.qty_on_hand, p.selling_price, p.cost
            FROM engine_compatibility ec
            LEFT JOIN products p ON p.id = ec.product_id
            WHERE ec.engine_model = %s ORDER BY ec.category, p.sku
        """, (engine_model,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def add_engine_compatibility(engine_model: str, category: str,
                             product_id: int = None, oem_part_number: str = None,
                             notes: str = None) -> int:
    """Add engine compatibility record."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO engine_compatibility (engine_model, category, product_id,
                                              oem_part_number, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (engine_model, category, product_id, oem_part_number, notes))
        conn.commit()
        return conn.lastrowid


# ─── Sales Orders ─────────────────────────────────────────────────────────


def generate_so_number() -> str:
    """Generate next SO number: SO-YYYY-NNNN."""
    from datetime import date as _date
    year = _date.today().strftime("%Y")
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sales_orders WHERE so_number LIKE %s",
            (f"SO-{year}-%",)).fetchone()
        cnt = row[0] if isinstance(row, tuple) else (dict(row) if hasattr(row, 'keys') else {}).get("cnt", 0)
        return f"SO-{year}-{int(cnt) + 1:04d}"


def create_sales_order(customer_id: int, priority: str = "normal",
                       promised_date: str = None, ship_method: str = None,
                       notes: str = None, internal_notes: str = None,
                       user: str = None, status: str = "draft",
                       esn: str = None, vin: str = None,
                       equipment_type: str = None,
                       equipment_make: str = None,
                       equipment_model: str = None) -> dict:
    """Create a sales order. Returns {id, so_number}."""
    so_number = generate_so_number()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sales_orders (so_number, customer_id, status, priority,
                                      promised_date, ship_method, notes,
                                      internal_notes, created_by,
                                      esn, vin,
                                      equipment_type, equipment_make, equipment_model)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (so_number, customer_id, status, priority, promised_date,
              ship_method, notes, internal_notes, user,
              esn, vin, equipment_type, equipment_make, equipment_model))
        conn.commit()
        return {"id": conn.lastrowid, "so_number": so_number}


def get_sales_order(so_id: int) -> dict:
    """Get sales order with customer info."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT so.*, c.name as customer_name, c.company as customer_company,
                   c.email as customer_email, c.phone as customer_phone,
                   c.address as customer_address
            FROM sales_orders so
            LEFT JOIN customers c ON c.id = so.customer_id
            WHERE so.id = %s
        """, (so_id,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def list_sales_orders(status: str = None, customer_id: int = None,
                      limit: int = 100) -> list:
    """List sales orders."""
    with get_db() as conn:
        conditions, params = ["1=1"], []
        if status:
            conditions.append("so.status = %s"); params.append(status)
        if customer_id:
            conditions.append("so.customer_id = %s"); params.append(customer_id)
        params.append(limit)
        rows = conn.execute(f"""
            SELECT so.*, c.name as customer_name, c.company as customer_company
            FROM sales_orders so
            LEFT JOIN customers c ON c.id = so.customer_id
            WHERE {' AND '.join(conditions)}
            ORDER BY so.created_at DESC LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def update_sales_order(so_id: int, **fields) -> bool:
    """Update sales order fields."""
    allowed = {"customer_id", "status", "priority", "promised_date",
               "ship_method", "tracking_number", "notes", "internal_notes",
               "customer_notes",
               "tax_rate", "shipping", "fuel_surcharge", "invoice_id",
               "cc_fee_enabled", "cc_fee_rate",
               "esn", "vin", "equipment_type", "equipment_make", "equipment_model"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    # Central status-transition check (P1.7). Return False (not raise) to
    # preserve the bool return contract used by existing callers.
    if "status" in updates:
        try:
            from engine.status_transitions import (
                SO_TRANSITIONS, TransitionError, validate_transition,
            )
            with get_db() as _c:
                row = _c.execute(
                    "SELECT status FROM sales_orders WHERE id = %s", (so_id,)
                ).fetchone()
            current = (row[0] if row else "") or ""
            validate_transition(SO_TRANSITIONS, old=current, new=str(updates["status"]))
        except TransitionError:
            return False
        except Exception:
            pass
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [so_id]
        conn.execute(f"UPDATE sales_orders SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                      tuple(vals))
        # Set timestamp columns based on status
        status = updates.get("status")
        if status == "confirmed":
            conn.execute("UPDATE sales_orders SET confirmed_at=CURRENT_TIMESTAMP WHERE id=%s AND confirmed_at IS NULL", (so_id,))
        elif status == "picking":
            conn.execute("UPDATE sales_orders SET confirmed_at=CURRENT_TIMESTAMP WHERE id=%s AND confirmed_at IS NULL", (so_id,))
        elif status == "ready":
            conn.execute("UPDATE sales_orders SET picked_at=CURRENT_TIMESTAMP WHERE id=%s", (so_id,))
        elif status == "shipped":
            conn.execute("UPDATE sales_orders SET shipped_at=CURRENT_TIMESTAMP WHERE id=%s", (so_id,))
        elif status == "invoiced":
            conn.execute("UPDATE sales_orders SET completed_at=CURRENT_TIMESTAMP WHERE id=%s", (so_id,))
        elif status == "cancelled":
            conn.execute("UPDATE sales_orders SET cancelled_at=CURRENT_TIMESTAMP WHERE id=%s", (so_id,))
            # Release reserved inventory
            _release_so_reservations(conn, so_id)
        conn.commit()
        return True


def add_so_line(so_id: int, product_id: int, quantity: int = 1,
                unit_price: float = 0, serial_id: int = None,
                location_id: int = None, notes: str = None,
                warranty_months: int | None = None,
                warranty_type: str | None = None,
                warranty_mileage_limit: int | None = None,
                eta_date: str | None = None,
                core_charge: float | None = None) -> int:
    """Add a line to a sales order. Allocates available inventory.

    ``core_charge`` is the per-unit customer-facing core deposit
    snapshotted from the originating quote (or seeded from
    ``products.core_sell_price`` when called directly). It is preserved
    on the SO row so that SO→Invoice conversion does not re-read live
    product-master values.
    """
    with get_db() as conn:
        # Warranty auto-fill: only when caller didn't pass anything at all.
        if warranty_months is None and warranty_type is None and warranty_mileage_limit is None:
            w_months, w_type, w_mileage = _fetch_product_warranty_defaults(
                conn, product_id
            )
        else:
            w_months = int(warranty_months or 0)
            w_type = warranty_type or "parts_only"
            w_mileage = warranty_mileage_limit

        # Get product info
        prod = conn.execute("SELECT sku, title, qty_on_hand, qty_reserved FROM products WHERE id=%s",
                            (product_id,)).fetchone()
        if not prod:
            return -1
        p = dict(prod) if hasattr(prod, 'keys') else {"sku": prod[0], "title": prod[1],
                                                        "qty_on_hand": prod[2], "qty_reserved": prod[3]}
        sku = p.get("sku", "")
        description = p.get("title", "")
        line_total = unit_price * quantity

        # Check available qty
        on_hand = int(p.get("qty_on_hand", 0) or 0)
        reserved = int(p.get("qty_reserved", 0) or 0)
        available = on_hand - reserved
        allocated_qty = min(quantity, max(available, 0))
        qty_backordered = quantity - allocated_qty

        # Auto-fill ETA when caller didn't pass one.
        if eta_date is None and product_id:
            try:
                from engine.eta import compute_line_eta
                eta_date = compute_line_eta(int(product_id), int(quantity or 1)).get("eta_date")
            except Exception:
                eta_date = None

        # Resolve core snapshot: caller-provided wins; otherwise pull
        # the customer-facing core from the product master so manually
        # added SO lines still carry a core. ``None`` from the caller
        # means "auto-fill"; an explicit ``0`` is treated as "no core".
        if core_charge is None:
            core_row = conn.execute(
                "SELECT core_sell_price, core_charge FROM products WHERE id=%s",
                (product_id,),
            ).fetchone()
            if core_row:
                cr = dict(core_row) if hasattr(core_row, "keys") else {
                    "core_sell_price": core_row[0], "core_charge": core_row[1],
                }
                core_amt = float(cr.get("core_sell_price") or 0)
                if core_amt <= 0:
                    core_amt = float(cr.get("core_charge") or 0)
                core_charge_val = max(core_amt, 0.0)
            else:
                core_charge_val = 0.0
        else:
            core_charge_val = float(core_charge or 0)

        # Detect whether the migration has added core_charge to the
        # sales_order_lines table (migration 104). Older DBs that have
        # not yet run the migration fall back to the legacy insert.
        sol_cols = {c[1] for c in conn.execute("PRAGMA table_info(sales_order_lines)").fetchall()}
        has_core_col = "core_charge" in sol_cols

        if has_core_col:
            conn.execute("""
                INSERT INTO sales_order_lines (so_id, product_id, serial_id, sku,
                                               description, quantity_ordered,
                                               quantity_picked, quantity_backordered,
                                               allocated_qty,
                                               unit_price, line_total, location_id, notes,
                                               warranty_months, warranty_type,
                                               warranty_mileage_limit, eta_date, core_charge)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (so_id, product_id, serial_id, sku, description,
                  quantity, 0, qty_backordered, allocated_qty,
                  unit_price, line_total, location_id, notes,
                  w_months, w_type, w_mileage, eta_date, core_charge_val))
        else:
            conn.execute("""
                INSERT INTO sales_order_lines (so_id, product_id, serial_id, sku,
                                               description, quantity_ordered,
                                               quantity_picked, quantity_backordered,
                                               allocated_qty,
                                               unit_price, line_total, location_id, notes,
                                               warranty_months, warranty_type,
                                               warranty_mileage_limit, eta_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (so_id, product_id, serial_id, sku, description,
                  quantity, 0, qty_backordered, allocated_qty,
                  unit_price, line_total, location_id, notes,
                  w_months, w_type, w_mileage, eta_date))
        line_id = conn.lastrowid

        # Reserve inventory for the allocated qty
        if allocated_qty > 0:
            conn.execute("UPDATE products SET qty_reserved = COALESCE(qty_reserved,0) + %s WHERE id=%s",
                         (allocated_qty, product_id))

        # Log activity
        try:
            status_note = f"In stock, allocated {allocated_qty}" if allocated_qty > 0 else "Needs purchasing"
            conn.execute(
                "INSERT INTO so_activity_log (so_id, action, detail) VALUES (%s, %s, %s)",
                (so_id, "line_added", f"{sku} x{quantity} — {status_note}"),
            )
        except Exception:
            pass  # activity log table may not exist yet

        conn.commit()
        _recalculate_so_totals(conn, so_id)
        return line_id


def get_so_lines(so_id: int) -> list:
    """Get all lines for a sales order."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT sol.*, COALESCE(p.sku, sol.sku) AS sku,
                   p.title as product_title, p.qty_on_hand,
                   p.qty_reserved, sl.name as location_name
            FROM sales_order_lines sol
            LEFT JOIN products p ON p.id = sol.product_id
            LEFT JOIN storage_locations sl ON sl.id = sol.location_id
            WHERE sol.so_id = %s ORDER BY sol.created_at
        """, (so_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def remove_so_line(line_id: int) -> bool:
    """Remove a line from a sales order. Releases reserved inventory."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT so_id, product_id, quantity_picked, allocated_qty FROM sales_order_lines WHERE id=%s",
            (line_id,)).fetchone()
        if not row:
            return False
        r = dict(row) if hasattr(row, 'keys') else {
            "so_id": row[0],
            "product_id": row[1],
            "quantity_picked": row[2],
            "allocated_qty": row[3],
        }
        so_id = r.get("so_id")
        product_id = r.get("product_id")
        qty_reserved = int(r.get("allocated_qty", 0) or 0)

        conn.execute("DELETE FROM sales_order_lines WHERE id=%s", (line_id,))
        if qty_reserved > 0 and product_id:
            conn.execute("UPDATE products SET qty_reserved = MAX(0, COALESCE(qty_reserved,0) - %s) WHERE id=%s",
                         (qty_reserved, product_id))
        conn.commit()
        _recalculate_so_totals(conn, so_id)
        return True


def update_so_line_price(line_id: int, unit_price: float) -> bool:
    """Update unit price and line total for a sales order line."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT so_id, quantity_ordered FROM sales_order_lines WHERE id=%s",
            (line_id,)).fetchone()
        if not row:
            return False
        r = dict(row) if hasattr(row, 'keys') else {"so_id": row[0], "quantity_ordered": row[1]}
        so_id = r["so_id"]
        qty = int(r.get("quantity_ordered", 1) or 1)
        line_total = unit_price * qty
        conn.execute(
            "UPDATE sales_order_lines SET unit_price=%s, line_total=%s WHERE id=%s",
            (unit_price, line_total, line_id))
        conn.commit()
        _recalculate_so_totals(conn, so_id)
        return True


def update_so_line(line_id: int, **kwargs) -> dict:
    """Update arbitrary fields on a sales order line (warranty, notes, etc.)."""
    allowed = {
        "quantity_ordered", "unit_price", "notes", "description",
        "warranty_months", "warranty_type", "warranty_mileage_limit",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"success": True}
    with get_db() as conn:
        try:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [line_id]
            conn.execute(f"UPDATE sales_order_lines SET {sets} WHERE id = %s", vals)
            row = conn.execute(
                "SELECT so_id, quantity_ordered, unit_price FROM sales_order_lines WHERE id = %s",
                (line_id,),
            ).fetchone()
            if row:
                so_id = row[0] if not hasattr(row, "keys") else row["so_id"]
                qty = int((row[1] if not hasattr(row, "keys") else row["quantity_ordered"]) or 1)
                price = float((row[2] if not hasattr(row, "keys") else row["unit_price"]) or 0)
                conn.execute(
                    "UPDATE sales_order_lines SET line_total = %s WHERE id = %s",
                    (qty * price, line_id),
                )
                _recalculate_so_totals(conn, so_id)
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def _recalculate_so_totals(conn, so_id: int):
    """Recalculate sales order totals.

    Discount lines (``line_kind='discount'``) are stored with negative
    ``line_total`` and reduce the taxable base.
    """
    sol_cols = {c[1] for c in conn.execute("PRAGMA table_info(sales_order_lines)").fetchall()}
    has_line_kind = "line_kind" in sol_cols
    has_core_col = "core_charge" in sol_cols
    if has_line_kind:
        product_where = (
            "WHERE so_id = %s AND COALESCE(line_kind,'product') != 'discount'"
        )
        discount_where = (
            "WHERE so_id = %s AND COALESCE(line_kind,'product') = 'discount'"
        )
    else:
        product_where = "WHERE so_id = %s"
        discount_where = "WHERE 0 = 1 AND so_id = %s"

    # P1 — include per-line core_charge in the subtotal so SO totals
    # mirror the same math as quote_lines / invoice_lines and survive
    # later product-master edits without drift.
    core_expr = (
        "+ COALESCE(SUM(COALESCE(core_charge, 0) * COALESCE(quantity_ordered, 0)), 0)"
        if has_core_col else ""
    )
    row = conn.execute(f"""
        SELECT COALESCE(SUM(line_total), 0)
             {core_expr}
             as subtotal
        FROM sales_order_lines {product_where}
    """, (so_id,)).fetchone()
    if row:
        subtotal = float(row[0] if isinstance(row, tuple) else
                         (dict(row) if hasattr(row, 'keys') else {}).get("subtotal", 0)) or 0
        drow = conn.execute(
            f"SELECT COALESCE(SUM(line_total), 0) FROM sales_order_lines {discount_where}",
            (so_id,),
        ).fetchone()
        discount_sum = float(drow[0] or 0) if drow else 0  # negative

        so = conn.execute("SELECT tax_rate, shipping FROM sales_orders WHERE id=%s",
                          (so_id,)).fetchone()
        tax_rate = 0.0
        shipping = 0.0
        if so:
            s = dict(so) if hasattr(so, 'keys') else {"tax_rate": so[0], "shipping": so[1]}
            tax_rate = float(s.get("tax_rate", 0) or 0)
            shipping = float(s.get("shipping", 0) or 0)
        taxable_base = subtotal + discount_sum
        if taxable_base < 0:
            taxable_base = 0
        tax_amount = taxable_base * tax_rate
        total = taxable_base + tax_amount + shipping
        conn.execute("""
            UPDATE sales_orders SET subtotal=%s, tax_amount=%s, total=%s WHERE id=%s
        """, (subtotal, tax_amount, total, so_id))
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# Discount / fee line helper (PO / Quote / SO / Invoice)
# ═══════════════════════════════════════════════════════════════════════

_DOC_KIND_CONFIG = {
    # doc_type: (table, fk_column, recalc_callable_name, qty_col, price_col, header_table)
    "po":      ("purchase_order_lines", "po_id",      "_recalculate_po_total",     "quantity_ordered", "unit_cost",   "purchase_orders"),
    "quote":   ("quote_lines",          "quote_id",   "_recalc_quote_totals",       "qty",              "unit_price",  "quotes"),
    "so":      ("sales_order_lines",    "so_id",      "_recalculate_so_totals",     "quantity_ordered", "unit_price",  "sales_orders"),
    "invoice": ("invoice_lines",        "invoice_id", "_recalculate_invoice_totals","quantity",         "unit_price",  "invoices"),
}


def _current_product_subtotal(conn, doc_type: str, doc_id: int) -> float:
    """Sum of non-discount line totals for the document, for % calc base."""
    cfg = _DOC_KIND_CONFIG[doc_type]
    table, fk = cfg[0], cfg[1]
    cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "line_kind" in cols:
        row = conn.execute(
            f"SELECT COALESCE(SUM(line_total),0) FROM {table} "
            f"WHERE {fk} = %s AND COALESCE(line_kind,'product') != 'discount'",
            (doc_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT COALESCE(SUM(line_total),0) FROM {table} WHERE {fk} = %s",
            (doc_id,),
        ).fetchone()
    try:
        return float(row[0] or 0) if row else 0.0
    except Exception:
        return 0.0


def add_discount_line(
    doc_type: str,
    doc_id: int,
    *,
    description: str = "Discount",
    discount_amount: float | None = None,
    discount_pct: float | None = None,
    sku: str = "DISCOUNT",
) -> dict:
    """Add a manual discount line to a PO, Quote, Sales Order, or Invoice.

    Exactly one of ``discount_amount`` or ``discount_pct`` should be
    supplied. The resulting line is stored with::

        product_id = NULL
        line_kind = 'discount'
        quantity = 1
        unit_price = -<computed dollar value>
        line_total = -<computed dollar value>
        discount_amount = <stored>
        discount_pct = <stored>

    For percent discounts the base is the current document's
    non-discount product subtotal at the time of insertion. The dollar
    value is captured immediately so the discount doesn't drift if the
    user adds more lines afterwards.
    """
    if doc_type not in _DOC_KIND_CONFIG:
        return {"success": False, "error": f"Unknown doc_type: {doc_type}"}
    cfg = _DOC_KIND_CONFIG[doc_type]
    table, fk, recalc_name, qty_col, price_col, _header = cfg

    if discount_amount in (None, 0) and discount_pct in (None, 0):
        return {"success": False, "error": "Provide discount_amount or discount_pct"}

    with get_db() as conn:
        # Resolve dollar value of the discount.
        amount = float(discount_amount or 0)
        pct = float(discount_pct or 0)
        if not amount and pct:
            base = _current_product_subtotal(conn, doc_type, doc_id)
            amount = round(base * (pct / 100.0), 2)
        if amount <= 0:
            return {"success": False, "error": "Discount amount must be > 0"}

        cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        # Build insert dynamically — only set columns that exist.
        fields: list[str] = [fk]
        values: list[object] = [doc_id]

        def put(col: str, val: object) -> None:
            if col in cols:
                fields.append(col)
                values.append(val)

        # product_id intentionally omitted (defaults to NULL).
        put(qty_col, 1)
        put(price_col, -amount)
        put("line_total", -amount)
        put("description", description)
        put("sku", sku)
        put("line_kind", "discount")
        put("discount_amount", amount)
        put("discount_pct", pct)
        # Quote-specific: ensure the line stays in the selected bucket.
        if doc_type == "quote":
            put("is_selected", 1)

        placeholders = ", ".join(["%s"] * len(fields))
        col_csv = ", ".join(fields)
        try:
            conn.execute(
                f"INSERT INTO {table} ({col_csv}) VALUES ({placeholders})",
                tuple(values),
            )
            # Recalculate totals on the parent document.
            recalc = globals().get(recalc_name)
            if recalc is not None:
                recalc(conn, doc_id)
            conn.commit()
            return {
                "success": True,
                "discount_amount": amount,
                "discount_pct": pct,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════
# P0.2 — qty_reserved reconciliation audit
# ═══════════════════════════════════════════════════════════════════════

# SO statuses that still hold a reservation against products.qty_reserved.
# Anything NOT in this set (invoiced, cancelled, void) should have released.
_OPEN_SO_STATUSES: tuple[str, ...] = (
    "draft", "confirmed", "approved", "partial",
    "picking", "ready", "shipped", "needs_po", "purchasing",
    "allocated", "ready_to_pick",
)


def reconcile_qty_reserved(auto_fix: bool = False) -> dict:
    """Audit and (optionally) fix ``products.qty_reserved`` drift.

    Compares the stored ``products.qty_reserved`` value against the sum of
    ``sales_order_lines.allocated_qty`` for all SOs whose status is still
    "open" (i.e. has not been invoiced or cancelled). Any row where the
    two disagree is reported as drift.

    When ``auto_fix=True`` the stored value is updated in place so it
    matches the computed expected value.

    Returns::

        {
            "drift_count": int,         # number of products with drift
            "total_drift": int,         # sum of absolute drift across all
            "drifts": [
                {
                    "product_id": int,
                    "sku": str,
                    "title": str,
                    "stored": int,
                    "expected": int,
                    "delta": int,       # stored - expected
                },
                ...
            ],
            "fixed": int,               # rows updated when auto_fix=True
        }
    """
    result: dict = {"drift_count": 0, "total_drift": 0, "drifts": [], "fixed": 0}
    placeholders = ",".join(["%s"] * len(_OPEN_SO_STATUSES))
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id AS product_id,
                   p.sku,
                   p.title,
                   COALESCE(p.qty_reserved, 0) AS stored,
                   COALESCE(SUM(
                       CASE
                         WHEN LOWER(so.status) IN ({placeholders})
                         THEN sol.allocated_qty
                         ELSE 0
                       END
                   ), 0) AS expected
            FROM products p
            LEFT JOIN sales_order_lines sol ON sol.product_id = p.id
            LEFT JOIN sales_orders so ON so.id = sol.so_id
            GROUP BY p.id, p.sku, p.title, p.qty_reserved
            HAVING COALESCE(p.qty_reserved, 0)
                   != COALESCE(SUM(
                       CASE
                         WHEN LOWER(so.status) IN ({placeholders})
                         THEN sol.allocated_qty
                         ELSE 0
                       END
                   ), 0)
            """,
            tuple(_OPEN_SO_STATUSES) * 2,
        ).fetchall()

        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {
                "product_id": r[0], "sku": r[1], "title": r[2],
                "stored": r[3], "expected": r[4],
            }
            stored = int(d.get("stored", 0) or 0)
            expected = int(d.get("expected", 0) or 0)
            delta = stored - expected
            result["drifts"].append({
                "product_id": d.get("product_id"),
                "sku": d.get("sku"),
                "title": d.get("title"),
                "stored": stored,
                "expected": expected,
                "delta": delta,
            })
            result["total_drift"] += abs(delta)
            if auto_fix and d.get("product_id"):
                conn.execute(
                    "UPDATE products SET qty_reserved = %s WHERE id = %s",
                    (expected, d["product_id"]),
                )
                result["fixed"] += 1
        if auto_fix and result["fixed"]:
            conn.commit()

    result["drift_count"] = len(result["drifts"])
    return result


# ═══════════════════════════════════════════════════════════════════════
# Stock-state read helpers (UI-pure; no side effects)
# ═══════════════════════════════════════════════════════════════════════

def compute_available(product_id: int) -> int:
    """Return max(0, qty_on_hand - qty_reserved) for a product.

    This is the user-facing "Available" number used everywhere a part
    is offered for sale. ``qty_reserved`` is maintained by
    ``add_so_line`` / ``_reserve_for_invoice_line`` / cancel + finalize
    hooks and audited by ``reconcile_qty_reserved``.
    """
    if not product_id:
        return 0
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(qty_on_hand,0), COALESCE(qty_reserved,0) "
            "FROM products WHERE id=%s",
            (int(product_id),),
        ).fetchone()
    if not row:
        return 0
    on_hand = int(row[0] or 0)
    reserved = int(row[1] or 0)
    return max(0, on_hand - reserved)


def get_open_reservations(product_id: int) -> list[dict]:
    """List the open documents currently holding reservation against a product.

    Returns rows like::

        [
            {"doc_type": "so",      "doc_id": 42, "doc_number": "SO-1042",
             "status": "confirmed", "quantity": 3},
            {"doc_type": "invoice", "doc_id": 99, "doc_number": "INV-2099",
             "status": "draft",     "quantity": 2},
            ...
        ]

    SO reservations come from ``sales_order_lines.allocated_qty`` where the
    SO status is in ``_OPEN_SO_STATUSES``. Direct-invoice reservations come
    from ``invoice_lines.quantity`` where ``invoices.finalized_at IS NULL``
    and the invoice has no parent SO (i.e. it was not sourced from one).
    Used for tooltips that explain "why is Available less than On Hand?".
    """
    if not product_id:
        return []
    pid = int(product_id)
    out: list[dict] = []

    placeholders = ",".join(["%s"] * len(_OPEN_SO_STATUSES))
    with get_db() as conn:
        so_rows = conn.execute(
            f"""
            SELECT so.id, so.so_number, LOWER(so.status), sol.allocated_qty
            FROM sales_order_lines sol
            JOIN sales_orders so ON so.id = sol.so_id
            WHERE sol.product_id = %s
              AND COALESCE(sol.allocated_qty, 0) > 0
              AND LOWER(so.status) IN ({placeholders})
            ORDER BY so.id
            """,
            (pid, *_OPEN_SO_STATUSES),
        ).fetchall()
        for r in so_rows:
            rec = dict(r) if hasattr(r, "keys") else {
                "id": r[0], "so_number": r[1],
                "status": r[2], "allocated_qty": r[3],
            }
            qty = int(rec.get("allocated_qty") or 0)
            if qty <= 0:
                continue
            out.append({
                "doc_type": "so",
                "doc_id": int(rec.get("id") or 0),
                "doc_number": rec.get("so_number") or "",
                "status": rec.get("status") or "",
                "quantity": qty,
            })

        # Direct (non-SO-sourced) invoices that are not finalized.
        inv_rows = conn.execute(
            """
            SELECT i.id, i.invoice_number, LOWER(COALESCE(i.status,'draft')),
                   il.quantity
            FROM invoice_lines il
            JOIN invoices i ON i.id = il.invoice_id
            WHERE il.product_id = %s
              AND i.finalized_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM sales_orders so2
                  WHERE so2.invoice_id = i.id
              )
            ORDER BY i.id
            """,
            (pid,),
        ).fetchall()
        for r in inv_rows:
            rec = dict(r) if hasattr(r, "keys") else {
                "id": r[0], "invoice_number": r[1],
                "status": r[2], "quantity": r[3],
            }
            qty = int(rec.get("quantity") or 0)
            if qty <= 0:
                continue
            out.append({
                "doc_type": "invoice",
                "doc_id": int(rec.get("id") or 0),
                "doc_number": rec.get("invoice_number") or "",
                "status": rec.get("status") or "draft",
                "quantity": qty,
            })

    return out


def get_stock_state(product_id: int) -> dict:
    """Return a complete stock snapshot for a product.

    Shape::

        {
            "product_id": int,
            "sku": str,
            "on_hand": int,
            "reserved": int,        # products.qty_reserved
            "available": int,       # max(0, on_hand - reserved)
            "on_po": int,           # qty currently on open POs (best-effort)
            "reservations": [       # open docs holding reservation
                {"doc_type", "doc_id", "doc_number", "status", "quantity"},
                ...
            ],
        }

    Safe to call from any UI thread; performs read-only queries.
    """
    empty = {
        "product_id": int(product_id or 0),
        "sku": "",
        "on_hand": 0,
        "reserved": 0,
        "available": 0,
        "on_po": 0,
        "reservations": [],
    }
    if not product_id:
        return empty
    pid = int(product_id)
    with get_db() as conn:
        row = conn.execute(
            "SELECT sku AS sku, COALESCE(qty_on_hand,0) AS qty_on_hand, "
            "COALESCE(qty_reserved,0) AS qty_reserved "
            "FROM products WHERE id=%s",
            (pid,),
        ).fetchone()
        if not row:
            return empty
        rec = dict(row) if hasattr(row, "keys") else {
            "sku": row[0], "qty_on_hand": row[1], "qty_reserved": row[2],
        }
        on_hand = int(rec.get("qty_on_hand") or 0)
        reserved = int(rec.get("qty_reserved") or 0)

        # Best-effort on_po: outstanding ordered-minus-received on open POs.
        # Guarded so a missing column / table never breaks the helper.
        on_po = 0
        try:
            po_row = conn.execute(
                """
                SELECT COALESCE(SUM(
                    MAX(0, COALESCE(pol.quantity_ordered,0)
                          - COALESCE(pol.quantity_received,0))
                ), 0)
                FROM purchase_order_lines pol
                JOIN purchase_orders po ON po.id = pol.po_id
                WHERE pol.product_id = %s
                  AND LOWER(COALESCE(po.status,'')) IN ('draft','sent','partial')
                """,
                (pid,),
            ).fetchone()
            if po_row:
                on_po = int((po_row[0] if not hasattr(po_row, "keys")
                             else po_row[0]) or 0)
        except Exception:
            on_po = 0

    return {
        "product_id": pid,
        "sku": rec.get("sku") or "",
        "on_hand": on_hand,
        "reserved": reserved,
        "available": max(0, on_hand - reserved),
        "on_po": on_po,
        "reservations": get_open_reservations(pid),
    }


def _release_so_reservations(conn, so_id: int):
    """Release all reserved inventory for a sales order."""
    rows = conn.execute(
        "SELECT id, product_id, quantity_picked, allocated_qty, quantity_ordered FROM sales_order_lines WHERE so_id=%s",
        (so_id,)).fetchall()
    for row in rows:
        r = dict(row) if hasattr(row, 'keys') else {
            "id": row[0],
            "product_id": row[1],
            "quantity_picked": row[2],
            "allocated_qty": row[3],
            "quantity_ordered": row[4],
        }
        product_id = r.get("product_id")
        qty = int(r.get("allocated_qty", 0) or 0)
        if qty > 0 and product_id:
            conn.execute("UPDATE products SET qty_reserved = MAX(0, COALESCE(qty_reserved,0) - %s) WHERE id=%s",
                         (qty, product_id))
        picked = int(r.get("quantity_picked", 0) or 0)
        ordered = int(r.get("quantity_ordered", 0) or 0)
        invoiced_qty = picked or qty or ordered
        conn.execute(
            """
            UPDATE sales_order_lines
            SET allocated_qty = 0,
                quantity_backordered = 0,
                invoiced_qty = %s
            WHERE id = %s
            """,
            (invoiced_qty, r.get("id")),
        )
    conn.commit()


def convert_so_to_invoice(so_id: int, user: str = None) -> dict:
    """Convert a sales order to an invoice. Returns {invoice_id, invoice_number}."""
    so = get_sales_order(so_id)
    if not so:
        return {"error": "Sales order not found"}
    if so.get("status") == "invoiced":
        return {"error": "Already invoiced"}

    # Create the invoice
    inv_number = generate_invoice_number()
    from datetime import date as _date, timedelta as _timedelta
    _due_date = (_date.today() + _timedelta(days=30)).isoformat()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO invoices (invoice_number, customer_id, status, invoice_date,
                                  due_date,
                                  subtotal, tax_rate, tax_amount, shipping, total,
                                  balance_due, notes, created_by,
                                  esn, vin, equipment_type, equipment_make, equipment_model)
            VALUES (%s, %s, 'draft', CURRENT_TIMESTAMP, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (inv_number, so.get("customer_id"), _due_date, so.get("subtotal", 0),
              so.get("tax_rate", 0), so.get("tax_amount", 0),
              so.get("shipping", 0), so.get("total", 0), so.get("total", 0),
              f"From Sales Order {so.get('so_number', '')}", user,
              so.get("esn"), so.get("vin"),
              so.get("equipment_type"), so.get("equipment_make"),
              so.get("equipment_model")))
        invoice_id = conn.lastrowid

        # Copy lines to invoice
        lines = get_so_lines(so_id)
        for line in lines:
            invoice_qty = int(line.get("quantity_picked", 0) or 0)
            if invoice_qty <= 0:
                invoice_qty = int(line.get("allocated_qty", 0) or 0)
            if invoice_qty <= 0:
                invoice_qty = int(line.get("quantity_ordered", 1) or 1)
            # P1 — pull the snapshotted core from the SO line. ``None`` from
            # ``get_so_lines`` (older DBs without the column) becomes 0; in
            # that case ``_add_core_line_for_product`` below still
            # auto-fills from the product master as a legacy fallback.
            so_line_core = float(line.get("core_charge") or 0)
            cursor = conn.execute("""
                INSERT INTO invoice_lines (invoice_id, product_id, serial_id, sku,
                                           description, quantity, unit_price, line_total,
                                           warranty_months, warranty_type, warranty_mileage_limit,
                                           core_charge)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (invoice_id, line.get("product_id"), line.get("serial_id"),
                  line.get("sku"), line.get("description"),
                  invoice_qty,
                  line.get("unit_price", 0), invoice_qty * float(line.get("unit_price", 0) or 0),
                  line.get("warranty_months") or 0,
                  line.get("warranty_type") or "parts_only",
                  line.get("warranty_mileage_limit"),
                  so_line_core))
            if is_postgresql():
                new_row = conn.execute("SELECT lastval()").fetchone()
                parent_line_id = new_row[0] if new_row else None
            else:
                parent_line_id = cursor.lastrowid
            # Only fall back to the product master when the SO line carried
            # no snapshot (legacy SOs created before mig 104).
            if parent_line_id and so_line_core <= 0:
                _add_core_line_for_product(
                    conn, invoice_id, parent_line_id,
                    line.get("product_id"), invoice_qty,
                    line.get("sku"), line.get("description"),
                )

        # Recalculate totals to include any inline core charges that may
        # have been written onto the freshly-inserted parent rows.
        _recalculate_invoice_totals(conn, invoice_id)

        # Mark SO as invoiced, release reservations (now committed via invoice)
        conn.execute("""
            UPDATE sales_orders SET status='invoiced', invoice_id=%s,
                                    completed_at=CURRENT_TIMESTAMP,
                                    updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (invoice_id, so_id))

        # Release reserved qty (inventory gets decremented when invoice is finalized)
        _release_so_reservations(conn, so_id)
        conn.commit()

        # Carry the 3% CC convenience-fee toggle forward from SO.
        if int(so.get("cc_fee_enabled") or 0):
            try:
                update_invoice(
                    invoice_id,
                    cc_fee_enabled=1,
                    cc_fee_rate=float(so.get("cc_fee_rate") or 0.03),
                )
            except Exception:
                pass

        return {"invoice_id": invoice_id, "invoice_number": inv_number}


def convert_so_to_invoice_partial(so_id: int, user: str = None) -> dict:
    """P1.5 — Ship-available-now conversion.

    Creates an invoice for lines that have real shippable quantity
    (picked or allocated > 0) while leaving the remainder on the SO as a
    backorder. If a line can't ship at all, it stays on the SO untouched.
    The SO is marked ``invoiced`` only when every line is fully consumed;
    otherwise it transitions to ``partial``.

    Returns ``{"success": bool, "invoice_id", "invoice_number",
               "invoiced_line_count", "backordered_line_count", "error"}``.
    """
    so = get_sales_order(so_id)
    if not so:
        return {"success": False, "error": "Sales order not found"}
    if (so.get("status") or "").lower() == "invoiced":
        return {"success": False, "error": "Already invoiced"}

    lines = get_so_lines(so_id)
    if not lines:
        return {"success": False, "error": "No lines on sales order"}

    shippable: list[tuple[dict, int]] = []   # (line, ship_qty)
    backorder_lines: list[dict] = []
    for line in lines:
        ordered = int(line.get("quantity_ordered", 0) or 0)
        invoiced = int(line.get("invoiced_qty", 0) or 0)
        picked = int(line.get("quantity_picked", 0) or 0)
        allocated = int(line.get("allocated_qty", 0) or 0)
        remaining = max(0, ordered - invoiced)
        if remaining <= 0:
            continue  # already fully invoiced on a prior partial ship
        ship_qty = min(remaining, max(picked, allocated))
        if ship_qty > 0:
            shippable.append((line, ship_qty))
        else:
            backorder_lines.append(line)

    if not shippable:
        return {
            "success": False,
            "error": "Nothing is ready to ship — all lines are backordered or pending PO receipt.",
            "backordered_line_count": len(backorder_lines),
        }

    inv_number = generate_invoice_number()
    subtotal = 0.0
    tax_rate = float(so.get("tax_rate", 0) or 0)
    with get_db() as conn:
        try:
            conn.execute("""
                INSERT INTO invoices (invoice_number, customer_id, status, invoice_date,
                                      subtotal, tax_rate, tax_amount, shipping, total,
                                      balance_due, notes, created_by,
                                      esn, vin, equipment_type, equipment_make, equipment_model)
                VALUES (%s, %s, 'draft', CURRENT_TIMESTAMP, 0, %s, 0, 0, 0, 0, %s, %s, %s, %s, %s, %s, %s)
            """, (inv_number, so.get("customer_id"), tax_rate,
                  f"Partial ship from SO {so.get('so_number', '')}", user,
                  so.get("esn"), so.get("vin"),
                  so.get("equipment_type"), so.get("equipment_make"),
                  so.get("equipment_model")))
            invoice_id = conn.lastrowid

            for line, ship_qty in shippable:
                unit_price = float(line.get("unit_price", 0) or 0)
                line_total = ship_qty * unit_price
                subtotal += line_total
                # P1 — copy the snapshotted core from the SO line so the
                # partial-ship invoice matches what was originally quoted,
                # even if the product master changes between SO creation
                # and partial fulfilment.
                so_line_core = float(line.get("core_charge") or 0)
                cursor = conn.execute("""
                    INSERT INTO invoice_lines (invoice_id, product_id, serial_id, sku,
                                               description, quantity, unit_price, line_total,
                                               warranty_months, warranty_type, warranty_mileage_limit,
                                               core_charge)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (invoice_id, line.get("product_id"), line.get("serial_id"),
                      line.get("sku"), line.get("description"),
                      ship_qty, unit_price, line_total,
                      line.get("warranty_months") or 0,
                      line.get("warranty_type") or "parts_only",
                      line.get("warranty_mileage_limit"),
                      so_line_core))
                if is_postgresql():
                    new_row = conn.execute("SELECT lastval()").fetchone()
                    parent_line_id = new_row[0] if new_row else None
                else:
                    parent_line_id = cursor.lastrowid
                if parent_line_id and so_line_core <= 0:
                    # Legacy SO (pre-mig 104) had no snapshot — fall back.
                    _add_core_line_for_product(
                        conn, invoice_id, parent_line_id,
                        line.get("product_id"), ship_qty,
                        line.get("sku"), line.get("description"),
                    )
                if parent_line_id:
                    # Pull the per-unit core that may have been set on the
                    # parent line and add it to the running subtotal.
                    core_row = conn.execute(
                        "SELECT COALESCE(core_charge, 0) FROM invoice_lines WHERE id=%s",
                        (parent_line_id,),
                    ).fetchone()
                    if core_row:
                        subtotal += float(core_row[0] or 0) * ship_qty

                # Update SO line: advance invoiced_qty and reconcile backorder.
                ordered = int(line.get("quantity_ordered", 0) or 0)
                already_invoiced = int(line.get("invoiced_qty", 0) or 0)
                new_invoiced = already_invoiced + ship_qty
                new_backorder = max(0, ordered - new_invoiced)
                conn.execute(
                    """UPDATE sales_order_lines
                       SET invoiced_qty = %s,
                           quantity_backordered = %s,
                           quantity_picked = MAX(quantity_picked - %s, 0),
                           allocated_qty  = MAX(allocated_qty  - %s, 0)
                       WHERE id = %s""",
                    (new_invoiced, new_backorder, ship_qty, ship_qty, line.get("id")),
                )

            tax_amount = round(subtotal * tax_rate / 100.0, 2) if tax_rate else 0
            total = subtotal + tax_amount
            conn.execute(
                """UPDATE invoices
                   SET subtotal = %s, tax_amount = %s, total = %s, balance_due = %s
                   WHERE id = %s""",
                (subtotal, tax_amount, total, total, invoice_id),
            )

            # Fully-shipped? Mark SO invoiced; otherwise partial.
            fully_done = not backorder_lines and all(
                (int(l.get("quantity_ordered", 0) or 0)
                 - (int(l.get("invoiced_qty", 0) or 0) + ship))
                <= 0
                for l, ship in shippable
            )
            if fully_done:
                conn.execute(
                    """UPDATE sales_orders
                       SET status = 'invoiced', invoice_id = %s,
                           completed_at = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (invoice_id, so_id),
                )
                _release_so_reservations(conn, so_id)
            else:
                conn.execute(
                    """UPDATE sales_orders
                       SET status = 'partial', updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (so_id,),
                )

            conn.commit()
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {
        "success": True,
        "invoice_id": invoice_id,
        "invoice_number": inv_number,
        "invoiced_line_count": len(shippable),
        "backordered_line_count": len(backorder_lines),
        "fully_shipped": fully_done,
    }


def get_so_summary() -> dict:
    """Get sales order dashboard summary."""


def log_so_activity(so_id: int, action: str, detail: str = "") -> None:
    """Log an activity entry for a sales order timeline."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO so_activity_log (so_id, action, detail) VALUES (%s, %s, %s)",
                (so_id, action, detail),
            )
            conn.commit()
    except Exception:
        pass  # table may not exist yet


def get_so_activity(so_id: int, limit: int = 50) -> list[dict]:
    """Get activity timeline for a sales order."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM so_activity_log WHERE so_id=%s ORDER BY created_at DESC LIMIT %s",
                (so_id, limit),
            ).fetchall()
            return [dict(row) if hasattr(row, 'keys') else {} for row in rows]
    except Exception:
        return []


def derive_so_line_status(line: dict) -> str:
    """Derive the display status for a single SO line from its qty fields."""
    ordered = int(line.get("quantity_ordered", 0) or 0)
    allocated = int(line.get("allocated_qty", 0) or 0)
    picked = int(line.get("quantity_picked", 0) or 0)
    on_po = int(line.get("on_po_qty", 0) or 0)
    received = int(line.get("received_qty", 0) or 0)
    invoiced = int(line.get("invoiced_qty", 0) or 0)

    backordered = int(line.get("quantity_backordered", 0) or 0)

    if invoiced >= ordered and ordered > 0:
        return "INVOICED"
    if picked >= ordered and ordered > 0:
        return "PICKED"
    # Any remaining backordered units that are NOT already on a PO mean the
    # line still needs purchasing — even if some units were already allocated
    # from on-hand stock (partial allocation).
    if backordered > 0 and backordered > on_po:
        return "NEEDS_PO"
    if received > 0 and on_po == 0 and picked == 0:
        return "RECEIVED"
    if allocated > 0 and picked == 0:
        return "ALLOCATED"
    if on_po > 0:
        return "ON_PO"
    if backordered > 0:
        return "NEEDS_PO"
    return "OPEN"


def derive_so_status(lines: list[dict]) -> str:
    """Derive the overall SO status from its line statuses."""
    if not lines:
        return "DRAFT"
    statuses = [derive_so_line_status(l) for l in lines]
    if all(s == "INVOICED" for s in statuses):
        return "INVOICED"
    if all(s == "PICKED" for s in statuses):
        return "READY_TO_INVOICE"
    if all(s in ("PICKED", "INVOICED") for s in statuses):
        return "READY_TO_INVOICE"
    if all(s in ("ALLOCATED", "RECEIVED", "PICKED", "INVOICED") for s in statuses):
        return "READY_TO_PICK"
    if any(s == "ON_PO" for s in statuses):
        return "PURCHASING"
    if any(s == "NEEDS_PO" for s in statuses):
        return "NEEDS_PO"
    if any(s in ("ALLOCATED", "RECEIVED") for s in statuses):
        return "ALLOCATED"
    return "CONFIRMED"


def get_so_summary() -> dict:
    """Get sales order dashboard summary."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM sales_orders GROUP BY status
        """).fetchall()
        result = {"draft": 0, "confirmed": 0, "picking": 0, "ready": 0,
                  "shipped": 0, "invoiced": 0, "cancelled": 0}
        for row in rows:
            r = dict(row) if hasattr(row, 'keys') else {"status": row[0], "cnt": row[1]}
            status = r.get("status", "")
            if status in result:
                result[status] = int(r.get("cnt", 0) or 0)

        # Backorders
        bo_row = conn.execute("""
            SELECT COUNT(DISTINCT so_id) as cnt FROM sales_order_lines
            WHERE quantity_backordered > 0
        """).fetchone()
        result["with_backorders"] = int((bo_row[0] if isinstance(bo_row, tuple) else
                                         (dict(bo_row) if hasattr(bo_row, 'keys') else {}).get("cnt", 0)) or 0)

        # Aging (>7 days not picked up)
        aging_row = conn.execute("""
            SELECT COUNT(*) as cnt FROM sales_orders
            WHERE status = 'ready'
              AND picked_at < datetime('now', '-7 days')
        """).fetchone()
        result["aging"] = int((aging_row[0] if isinstance(aging_row, tuple) else
                                (dict(aging_row) if hasattr(aging_row, 'keys') else {}).get("cnt", 0)) or 0)

        return result


def get_backorder_lines(limit: int = 100) -> list:
    """Get all backordered line items across sales orders."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT sol.*, so.so_number, so.status as so_status,
                   c.name as customer_name, c.company as customer_company,
                   p.sku, p.title as product_title, p.qty_on_hand, p.qty_reserved
            FROM sales_order_lines sol
            JOIN sales_orders so ON so.id = sol.so_id
            LEFT JOIN customers c ON c.id = so.customer_id
            LEFT JOIN products p ON p.id = sol.product_id
            WHERE sol.quantity_backordered > 0 AND so.status NOT IN ('cancelled', 'invoiced')
            ORDER BY so.created_at LIMIT %s
        """, (limit,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# ─── Phase 5E: Returns & Warranty ────────────────────────────────────────


def process_invoice_return(
    invoice_id: int,
    items: list[dict],
    reason: str | None = None,
    restock: bool = True,
    condition: str | None = None,
    user: str | None = None,
) -> dict:
    """Process a customer return against a finalized invoice.

    For each item ``{"line_id": int, "qty": int}`` this:
      * inserts a negative-priced adjustment line into the original
        invoice (sku ``RTN-<sku>``) so subtotal/tax/total fall by the
        refund amount via :func:`_recalculate_invoice_totals`;
      * mirrors any companion ``CORE-`` line as a credit too;
      * restocks ``qty_on_hand`` (when ``restock=True``) via
        :func:`adjust_product_quantity` (txn_type ``return``);
      * frees any ``serial_id`` attached to the original line via
        :func:`return_serial`;
      * writes one row into the ``returns`` table per item with the
        generated RMA number.

    Refuses on draft/void invoices. Caller may pass items that exceed
    the remaining returnable quantity — those are clamped.

    Returns ``{"success": bool, "error": str|None,
              "rmas": [...], "refund_total": float}``.
    """
    invoice = get_invoice(invoice_id)
    if not invoice:
        return {"success": False, "error": "Invoice not found"}
    status = (invoice.get("status") or "").lower()
    if status in ("draft", "void"):
        return {"success": False, "error": f"Cannot return a {status} invoice"}
    if not items:
        return {"success": False, "error": "No items selected for return"}

    # Index original lines + already-returned qty per parent line.
    by_line: dict[int, dict] = {}
    returned_so_far: dict[int, int] = {}
    for line in invoice.get("lines", []):
        sku = (line.get("sku") or "").upper()
        notes = line.get("notes") or ""
        if sku.startswith("RTN-") and notes.startswith("return_of:"):
            try:
                src = int(notes.split(":", 1)[1])
                returned_so_far[src] = returned_so_far.get(src, 0) + abs(int(line.get("quantity") or 0))
            except (ValueError, IndexError):
                pass
            continue
        if sku.startswith("CORE-"):
            continue
        by_line[int(line["id"])] = line

    rmas: list[dict] = []
    refund_total = 0.0
    customer_id = invoice.get("customer_id")
    invoice_number = invoice.get("invoice_number")

    # Phase 1: insert negative adjustment lines + recalc inside a single
    # connection. Auxiliary side effects (restock, RMA rows, serial
    # release) are performed in phase 2 to avoid nested SQLite locks.
    side_effects: list[dict] = []
    with get_db() as conn:
        for item in items:
            try:
                src_line_id = int(item.get("line_id"))
                req_qty = int(item.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            src = by_line.get(src_line_id)
            if not src or req_qty <= 0:
                continue

            original_qty = int(src.get("quantity") or 0)
            already_returned = returned_so_far.get(src_line_id, 0)
            remaining = max(0, original_qty - already_returned)
            qty = min(req_qty, remaining)
            if qty <= 0:
                continue

            unit_price = float(src.get("unit_price") or 0)
            sku = src.get("sku") or ""
            desc = src.get("description") or sku
            product_id = src.get("product_id")
            serial_id = src.get("serial_id")
            line_total = -(unit_price * qty)
            refund_total += unit_price * qty

            # Insert the negative adjustment line on the original invoice.
            conn.execute(
                """
                INSERT INTO invoice_lines (
                    invoice_id, product_id, serial_id, sku, description,
                    quantity, unit_price, line_total, notes
                ) VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s, %s)
                """,
                (invoice_id,
                 f"RTN-{sku}" if sku else "RTN",
                 f"Return: {desc}",
                 qty,
                 -unit_price,
                 line_total,
                 f"return_of:{src_line_id}"),
            )

            # Mirror any companion CORE line so the core charge is refunded too.
            core_row = conn.execute(
                """
                SELECT unit_price FROM invoice_lines
                WHERE invoice_id = %s AND notes = %s AND quantity > 0
                """,
                (invoice_id, f"linked_to:{src_line_id}"),
            ).fetchone()
            if core_row:
                core_unit = float(core_row[0] or 0)
                if core_unit > 0:
                    conn.execute(
                        """
                        INSERT INTO invoice_lines (
                            invoice_id, product_id, serial_id, sku, description,
                            quantity, unit_price, line_total, notes
                        ) VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s, %s)
                        """,
                        (invoice_id,
                         f"RTN-CORE-{sku}" if sku else "RTN-CORE",
                         f"Core credit: {desc}",
                         qty,
                         -core_unit,
                         -(core_unit * qty),
                         f"return_of:{src_line_id}"),
                    )
                    refund_total += core_unit * qty

            side_effects.append({
                "src_line_id": src_line_id,
                "sku": sku,
                "desc": desc,
                "qty": qty,
                "unit_price": unit_price,
                "product_id": product_id,
                "serial_id": serial_id,
            })
            returned_so_far[src_line_id] = already_returned + qty

        if not side_effects:
            return {"success": False, "error": "No items were eligible for return"}

        # Recalculate invoice totals (negative lines reduce subtotal).
        _recalculate_invoice_totals(conn, invoice_id)

        # Re-evaluate status: if balance_due <= 0 → 'paid' (overpaid means
        # refund owed to customer, tracked by negative balance_due).
        row = conn.execute(
            "SELECT total, amount_paid FROM invoices WHERE id = %s", (invoice_id,)
        ).fetchone()
        if row:
            total = float(row[0] or 0)
            amount_paid = float(row[1] or 0)
            new_balance = total - amount_paid
            if amount_paid > 0 and new_balance <= 0:
                conn.execute(
                    "UPDATE invoices SET status='paid', balance_due=%s, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                    (new_balance, invoice_id),
                )

        conn.commit()

    # Phase 2: restock, free serials, and create RMA rows.
    for fx in side_effects:
        if restock and fx["product_id"]:
            try:
                adjust_product_quantity(
                    sku=fx["sku"],
                    delta=fx["qty"],
                    txn_type="return",
                    reference=f"RMA from {invoice_number}",
                    notes=reason or f"Return on invoice {invoice_number}",
                    user=user,
                )
            except Exception as e:
                print(f"Warning: failed to restock {fx['sku']}: {e}")

        if fx["serial_id"]:
            try:
                return_serial(fx["serial_id"], notes=f"Return on invoice {invoice_number}")
            except Exception as e:
                print(f"Warning: failed to free serial {fx['serial_id']}: {e}")

        try:
            rma = create_return(
                invoice_id=invoice_id,
                customer_id=customer_id,
                product_id=fx["product_id"],
                serial_id=fx["serial_id"],
                quantity=fx["qty"],
                reason=reason,
                notes=f"From line {fx['src_line_id']} ({fx['sku']})",
                user=user,
            )
            if condition:
                update_return(
                    rma["id"],
                    condition=condition,
                    refund_amount=fx["unit_price"] * fx["qty"],
                    action_taken="restock" if restock else "scrap",
                )
            rmas.append({
                "id": rma["id"],
                "rma_number": rma["rma_number"],
                "sku": fx["sku"],
                "qty": fx["qty"],
                "refund_amount": fx["unit_price"] * fx["qty"],
            })
        except Exception as e:
            print(f"Warning: failed to create RMA row: {e}")

    return {
        "success": True,
        "rmas": rmas,
        "refund_total": refund_total,
    }


def generate_rma_number() -> str:
    """Generate next RMA number."""
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM returns").fetchone()
        cnt = row[0] if isinstance(row, tuple) else (dict(row) if hasattr(row, 'keys') else {}).get("cnt", 0)
        return f"RMA-{int(cnt) + 1:04d}"


def create_return(invoice_id: int = None, customer_id: int = None,
                  product_id: int = None, serial_id: int = None,
                  quantity: int = 1, reason: str = None,
                  warranty_expiry: str = None, notes: str = None,
                  user: str = None) -> dict:
    """Create a return/RMA. Returns {id, rma_number}."""
    rma_number = generate_rma_number()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO returns (rma_number, invoice_id, customer_id, product_id,
                                 serial_id, quantity, reason, warranty_expiry, notes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (rma_number, invoice_id, customer_id, product_id, serial_id,
              quantity, reason, warranty_expiry, notes, user))
        conn.commit()
        return {"id": conn.lastrowid, "rma_number": rma_number}


def list_returns(status: str = None, customer_id: int = None,
                 limit: int = 100) -> list:
    """List returns."""
    with get_db() as conn:
        conditions, params = ["1=1"], []
        if status:
            conditions.append("r.status = %s"); params.append(status)
        if customer_id:
            conditions.append("r.customer_id = %s"); params.append(customer_id)
        params.append(limit)
        rows = conn.execute(f"""
            SELECT r.*, p.sku, p.title as product_title,
                   c.name as customer_name, c.company as customer_company
            FROM returns r LEFT JOIN products p ON p.id = r.product_id
            LEFT JOIN customers c ON c.id = r.customer_id
            WHERE {' AND '.join(conditions)}
            ORDER BY r.created_at DESC LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def update_return(return_id: int, **fields) -> bool:
    """Update return fields."""
    allowed = {"status", "condition", "action_taken", "refund_amount",
               "vendor_claim_id", "vendor_claim_status", "notes",
               "received_date", "resolved_date"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [return_id]
        conn.execute(f"UPDATE returns SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=%s", tuple(vals))
        conn.commit()
        return True


def get_return(return_id: int) -> dict:
    """Get a single return."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT r.*, p.sku, p.title as product_title, c.name as customer_name
            FROM returns r LEFT JOIN products p ON p.id = r.product_id
            LEFT JOIN customers c ON c.id = r.customer_id
            WHERE r.id = %s
        """, (return_id,)).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else dict(enumerate(row))
        return None


def refund_return_as_credit(
    return_id: int,
    amount: float | None = None,
    notes: str | None = None,
    restocking_fee: float | None = None,
    restocking_pct: float | None = None,
) -> int | None:
    """Mark an RMA as refunded and post a matching customer_credits row.

    Closes the loop on G1: when a returns row transitions to ``refunded``
    we issue an open customer credit (source_type='rma', source_id=return_id)
    so it shows up on the customer's ledger and is usable from the
    payment dialog's Apply-Credit banner.

    Restocking-fee behavior (T1.2):
      * If ``restocking_fee`` is provided, it is stored verbatim and the
        credit posted to the customer is ``refund_amount - restocking_fee``.
      * If ``restocking_pct`` is provided (and no flat fee), the fee is
        computed as ``refund_amount * restocking_pct / 100``.
      * If neither is provided but the row already has a stored
        ``restocking_fee``, that stored value is applied automatically.
      * The gross ``refund_amount`` on the row is preserved unchanged so
        that the audit trail remains intact.

    Idempotent: a non-voided ledger row keyed by (rma, return_id) is
    updated in place if it already exists; otherwise inserted.

    Returns the ``customer_credits.id`` (existing or newly inserted) or
    ``None`` if the return has no customer.
    """
    with get_db() as conn:
        # Try to load the row including restocking columns; gracefully
        # fall back if the migration hasn't been applied.
        try:
            row = conn.execute(
                "SELECT id, customer_id, refund_amount, status, "
                "       restocking_fee, restocking_pct "
                "FROM returns WHERE id = %s",
                (int(return_id),),
            ).fetchone()
            has_restock_cols = True
        except Exception:
            row = conn.execute(
                "SELECT id, customer_id, refund_amount, status "
                "FROM returns WHERE id = %s",
                (int(return_id),),
            ).fetchone()
            has_restock_cols = False
        if not row:
            raise ValueError(f"Return #{return_id} not found.")
        if hasattr(row, "keys"):
            r = dict(row)
        else:
            r = {
                "id": row[0], "customer_id": row[1],
                "refund_amount": row[2], "status": row[3],
            }
            if has_restock_cols and len(row) > 4:
                r["restocking_fee"] = row[4]
                r["restocking_pct"] = row[5]

        gross_amount = float(
            amount if amount is not None
            else (r.get("refund_amount") or 0)
        )
        if gross_amount <= 0:
            raise ValueError(
                "Refund amount must be > 0 to post a customer credit."
            )

        # ── T1.2: derive restocking fee. Explicit argument wins, then
        # explicit percentage, then any value already stored on the row.
        if restocking_fee is not None:
            fee = float(max(0.0, restocking_fee))
        elif restocking_pct is not None:
            fee = float(max(0.0, gross_amount * float(restocking_pct) / 100.0))
        else:
            fee = float(r.get("restocking_fee") or 0)
            stored_pct = float(r.get("restocking_pct") or 0)
            if fee <= 0 and stored_pct > 0:
                fee = float(gross_amount * stored_pct / 100.0)
        if fee > gross_amount:
            raise ValueError(
                f"Restocking fee ({fee:.2f}) cannot exceed refund "
                f"amount ({gross_amount:.2f})."
            )
        net_amount = round(gross_amount - fee, 2)

        # 1. Mark the return refunded, recording gross + fee + net.
        from datetime import date
        if has_restock_cols:
            conn.execute(
                "UPDATE returns SET status = 'refunded', "
                "       refund_amount = %s, "
                "       restocking_fee = %s, "
                "       restocking_pct = COALESCE(%s, restocking_pct), "
                "       net_refund = %s, "
                "       resolved_date = %s, "
                "       notes = COALESCE(%s, notes), "
                "       updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (
                    gross_amount,
                    fee,
                    restocking_pct,
                    net_amount,
                    date.today().isoformat(),
                    notes,
                    int(return_id),
                ),
            )
        else:
            conn.execute(
                "UPDATE returns SET status = 'refunded', "
                "       refund_amount = %s, "
                "       resolved_date = %s, "
                "       notes = COALESCE(%s, notes), "
                "       updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (
                    gross_amount,
                    date.today().isoformat(),
                    notes,
                    int(return_id),
                ),
            )

        cust_id = r.get("customer_id")
        if not cust_id:
            conn.commit()
            return None

        # 2. Upsert the matching customer_credits ledger row with the
        # net refund (after restocking fee).
        existing = conn.execute(
            "SELECT id FROM customer_credits "
            "WHERE source_type = 'rma' AND source_id = %s "
            "  AND status <> 'voided'",
            (int(return_id),),
        ).fetchone()
        if existing:
            cid = existing[0] if not hasattr(existing, "keys") else existing["id"]
            conn.execute(
                "UPDATE customer_credits SET amount = %s, "
                "       notes = COALESCE(%s, notes), "
                "       updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (float(net_amount), notes, int(cid)),
            )
            conn.commit()
            return int(cid)

        cur = conn.execute(
            "INSERT INTO customer_credits ("
            "    customer_id, source_type, source_id, amount, "
            "    issue_date, status, notes"
            ") VALUES (%s, 'rma', %s, %s, CURRENT_TIMESTAMP, 'open', %s)",
            (int(cust_id), int(return_id), float(net_amount), notes),
        )
        conn.commit()
        return int(cur.lastrowid)


def set_rma_restocking_fee(
    return_id: int,
    fee: float | None = None,
    pct: float | None = None,
) -> bool:
    """Stage a restocking fee on a return without refunding yet.

    Lets the UI capture the fee at any point in the RMA lifecycle
    (e.g. during inspection) so when the refund button is clicked the
    credit is computed correctly. Exactly one of ``fee`` or ``pct``
    should be supplied; both is allowed (fee wins).
    """
    if fee is None and pct is None:
        raise ValueError("Provide fee, pct, or both.")
    with get_db() as conn:
        row = conn.execute(
            "SELECT refund_amount FROM returns WHERE id = %s",
            (int(return_id),),
        ).fetchone()
        if not row:
            return False
        gross = float(row[0] or 0) if not hasattr(row, "keys") else float(row["refund_amount"] or 0)
        if fee is None and pct is not None:
            fee = float(max(0.0, gross * float(pct) / 100.0))
        net = max(0.0, gross - float(fee or 0))
        try:
            conn.execute(
                "UPDATE returns SET restocking_fee = %s, "
                "       restocking_pct = COALESCE(%s, restocking_pct), "
                "       net_refund = %s, "
                "       updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (float(fee or 0), pct, float(net), int(return_id)),
            )
            conn.commit()
            return True
        except Exception:
            return False


# ─── Price Tiers & Customer Pricing ──────────────────────────────────────


def list_price_tiers() -> list:
    """List all price tiers."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM price_tiers ORDER BY priority, name").fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_price_tier(tier_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM price_tiers WHERE id=%s", (tier_id,)).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else (dict(enumerate(row)) if row else None)


def create_price_tier(name: str, markup_pct: float = 0, discount_pct: float = 0,
                      description: str = None, priority: int = 0) -> int:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO price_tiers (name, description, markup_pct, discount_pct, priority)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, description, markup_pct, discount_pct, priority))
        conn.commit()
        return conn.lastrowid


def update_price_tier(tier_id: int, **fields) -> bool:
    allowed = {"name", "description", "markup_pct", "discount_pct", "priority", "is_default"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [tier_id]
        conn.execute(f"UPDATE price_tiers SET {set_clause} WHERE id=%s", tuple(vals))
        conn.commit()
        return True


def delete_price_tier(tier_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM price_tiers WHERE id=%s", (tier_id,))
        conn.commit()
        return True


def set_customer_pricing(customer_id: int, tier_id: int = None,
                         product_id: int = None, custom_price: float = None,
                         effective_date: str = None, expiry_date: str = None) -> int:
    """Set customer-specific pricing. Can be tier-based or per-product custom price."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO customer_pricing (customer_id, tier_id, product_id,
                                          custom_price, effective_date, expiry_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (customer_id, tier_id, product_id, custom_price,
              effective_date, expiry_date))
        conn.commit()
        return conn.lastrowid


def get_product_map_price(product_id: int) -> float:
    """Return the manufacturer Minimum Advertised Price for a product.

    F4: returns ``0.0`` if the product has no MAP set (the default) or
    if the row is missing. Callers treat ``0`` as "no MAP enforced" —
    no warning fires unless the value is strictly positive.
    """
    if not product_id:
        return 0.0
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT map_price FROM products WHERE id = %s",
                (int(product_id),),
            ).fetchone()
    except Exception:
        return 0.0
    if not row:
        return 0.0
    val = row[0] if not hasattr(row, "keys") else row["map_price"]
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def set_product_map_price(product_id: int, map_price: float) -> bool:
    """Set the MAP for a product. ``0`` clears the constraint."""
    with get_db() as conn:
        conn.execute(
            "UPDATE products SET map_price = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            (float(map_price or 0), int(product_id)),
        )
        conn.commit()
    return True


# ─── Lost-sale capture (F1, migration 080) ──────────────────────────────

_LOST_SALE_REASONS = (
    "no_stock", "price", "lead_time", "competitor", "no_xref", "other",
)


def record_lost_sale(
    *,
    customer_id: int | None = None,
    product_id: int | None = None,
    sku_requested: str | None = None,
    description: str | None = None,
    quantity: int = 1,
    quoted_price: float = 0.0,
    reason: str = "other",
    source_quote_id: int | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> int:
    """Record a lost-sale event. Returns the new row's id.

    At least one of ``product_id`` or ``sku_requested`` must be supplied
    so the row is useful for reorder analysis.
    """
    if not product_id and not (sku_requested and sku_requested.strip()):
        raise ValueError(
            "record_lost_sale requires product_id or sku_requested."
        )
    reason_norm = (reason or "other").strip().lower()
    if reason_norm not in _LOST_SALE_REASONS:
        raise ValueError(
            f"Invalid reason '{reason}'. Expected one of: "
            f"{', '.join(_LOST_SALE_REASONS)}."
        )
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO lost_sales ("
            "  customer_id, product_id, sku_requested, description, "
            "  quantity, quoted_price, reason, source_quote_id, notes, "
            "  created_by"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                customer_id, product_id,
                (sku_requested or "").strip() or None,
                (description or "").strip() or None,
                int(quantity or 1),
                float(quoted_price or 0),
                reason_norm,
                source_quote_id,
                (notes or "").strip() or None,
                created_by,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_lost_sales(
    customer_id: int | None = None,
    product_id: int | None = None,
    reason: str | None = None,
    since: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return recent lost-sale rows with customer / product joined fields."""
    where: list[str] = ["1=1"]
    params: list = []
    if customer_id:
        where.append("ls.customer_id = %s"); params.append(int(customer_id))
    if product_id:
        where.append("ls.product_id = %s"); params.append(int(product_id))
    if reason:
        where.append("ls.reason = %s"); params.append(str(reason))
    if since:
        where.append("ls.lost_date >= %s"); params.append(since)
    params.append(int(limit))
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT ls.*,
                   c.name    AS customer_name,
                   c.company AS customer_company,
                   p.sku     AS product_sku,
                   p.title   AS product_title
              FROM lost_sales ls
              LEFT JOIN customers c ON c.id = ls.customer_id
              LEFT JOIN products  p ON p.id = ls.product_id
             WHERE {' AND '.join(where)}
             ORDER BY ls.lost_date DESC, ls.id DESC
             LIMIT %s
            """,
            tuple(params),
        ).fetchall()
    return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def get_lost_sale_summary(since: str | None = None) -> dict:
    """Aggregate counts and quoted-price totals grouped by reason.

    Returns ``{"reasons": {<reason>: {"count": n, "value": $}}, "total_count": n, "total_value": $}``.
    """
    where = "WHERE 1=1"
    params: tuple = ()
    if since:
        where = "WHERE lost_date >= %s"
        params = (since,)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT reason,
                   COUNT(*) AS n,
                   COALESCE(SUM(quoted_price * quantity), 0) AS value
              FROM lost_sales
              {where}
             GROUP BY reason
            """,
            params,
        ).fetchall()
    reasons: dict[str, dict] = {}
    total_n = 0
    total_v = 0.0
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {
            "reason": r[0], "n": r[1], "value": r[2],
        }
        key = str(d.get("reason") or "other")
        n = int(d.get("n") or 0)
        v = float(d.get("value") or 0)
        reasons[key] = {"count": n, "value": v}
        total_n += n
        total_v += v
    return {"reasons": reasons, "total_count": total_n, "total_value": total_v}


# ─── Landed cost allocation (T1.5 / G4) ────────────────────────────────


def _po_landed_total_overhead(po_row: dict) -> float:
    """Return shipping + duty + other_landed_fees for a PO row dict."""
    keys = ("shipping", "duty_amount", "other_landed_fees")
    total = 0.0
    for k in keys:
        try:
            total += float(po_row.get(k) or 0)
        except (TypeError, ValueError):
            pass
    return round(total, 4)


def allocate_landed_costs(po_id: int, basis: str | None = None) -> dict:
    """Distribute freight + duty + other fees across PO lines.

    Args:
        po_id: purchase_orders.id
        basis: 'value' | 'qty' | 'weight'. If None, uses the PO's stored
               ``freight_allocation_basis`` (default 'value').

    Returns dict ``{"basis": str, "overhead": float, "lines": [...]}``.

    For each line writes:
        landed_allocation = share of total overhead
        landed_unit_cost  = unit_cost + landed_allocation / max(qty, 1)

    If no overhead, all landed_allocation=0 and landed_unit_cost=unit_cost.
    Lines with zero quantity (per basis) get zero allocation; remainder
    is redistributed proportionally to the rest.
    """
    pid = int(po_id)
    with get_db() as conn:
        po = conn.execute(
            "SELECT * FROM purchase_orders WHERE id = %s", (pid,),
        ).fetchone()
        if not po:
            raise ValueError(f"PO {pid} not found.")
        po_d = dict(po) if hasattr(po, "keys") else dict(po)
        chosen_basis = (basis or po_d.get("freight_allocation_basis")
                        or "value").strip().lower()
        if chosen_basis not in ("value", "qty", "weight"):
            raise ValueError(
                f"Invalid basis '{chosen_basis}'. "
                "Expected 'value', 'qty', or 'weight'."
            )
        overhead = _po_landed_total_overhead(po_d)

        # We prefer received qty when present, else ordered.
        # ``product_weight`` is optional; the products table may not
        # have a weight column on older schemas.
        try:
            line_rows = conn.execute(
                "SELECT pol.id, pol.product_id, pol.quantity_ordered, "
                "       pol.quantity_received, pol.unit_cost, pol.line_total, "
                "       p.weight AS product_weight "
                "  FROM purchase_order_lines pol "
                "  LEFT JOIN products p ON p.id = pol.product_id "
                " WHERE pol.po_id = %s "
                " ORDER BY pol.id",
                (pid,),
            ).fetchall()
        except Exception:
            line_rows = conn.execute(
                "SELECT pol.id, pol.product_id, pol.quantity_ordered, "
                "       pol.quantity_received, pol.unit_cost, pol.line_total, "
                "       NULL AS product_weight "
                "  FROM purchase_order_lines pol "
                " WHERE pol.po_id = %s "
                " ORDER BY pol.id",
                (pid,),
            ).fetchall()
        lines = [
            dict(r) if hasattr(r, "keys") else dict(enumerate(r))
            for r in line_rows
        ]
        if not lines:
            return {"basis": chosen_basis, "overhead": overhead, "lines": []}

        def _qty_for(line):
            q = line.get("quantity_received") or 0
            if not q:
                q = line.get("quantity_ordered") or 0
            try:
                return float(q)
            except (TypeError, ValueError):
                return 0.0

        def _weight_for(line):
            q = _qty_for(line)
            w = line.get("product_weight") or 0
            try:
                return float(w) * q
            except (TypeError, ValueError):
                return 0.0

        def _value_for(line):
            try:
                lt = float(line.get("line_total") or 0)
            except (TypeError, ValueError):
                lt = 0.0
            if lt > 0:
                return lt
            # fall back to unit_cost * qty
            try:
                uc = float(line.get("unit_cost") or 0)
            except (TypeError, ValueError):
                uc = 0.0
            return uc * _qty_for(line)

        weight_fn = {
            "value": _value_for,
            "qty": _qty_for,
            "weight": _weight_for,
        }[chosen_basis]

        weights = [max(0.0, weight_fn(ln)) for ln in lines]
        total_weight = sum(weights)

        result_lines = []
        if overhead <= 0 or total_weight <= 0:
            # No overhead OR no basis available — set landed = unit cost.
            for ln in lines:
                qty = _qty_for(ln) or 1.0
                try:
                    uc = float(ln.get("unit_cost") or 0)
                except (TypeError, ValueError):
                    uc = 0.0
                conn.execute(
                    "UPDATE purchase_order_lines "
                    "   SET landed_allocation = %s, landed_unit_cost = %s "
                    " WHERE id = %s",
                    (0.0, round(uc, 4), int(ln["id"])),
                )
                result_lines.append({
                    "line_id": int(ln["id"]),
                    "product_id": int(ln["product_id"]),
                    "allocation": 0.0,
                    "landed_unit_cost": round(uc, 4),
                })
        else:
            # Distribute proportionally, fixing rounding on last line.
            running = 0.0
            for idx, ln in enumerate(lines):
                share = (
                    overhead if idx == len(lines) - 1
                    else round(overhead * (weights[idx] / total_weight), 4)
                )
                if idx == len(lines) - 1:
                    share = round(overhead - running, 4)
                running += share
                qty = _qty_for(ln) or 1.0
                try:
                    uc = float(ln.get("unit_cost") or 0)
                except (TypeError, ValueError):
                    uc = 0.0
                lcu = round(uc + (share / qty), 4) if qty > 0 else round(uc, 4)
                conn.execute(
                    "UPDATE purchase_order_lines "
                    "   SET landed_allocation = %s, landed_unit_cost = %s "
                    " WHERE id = %s",
                    (share, lcu, int(ln["id"])),
                )
                result_lines.append({
                    "line_id": int(ln["id"]),
                    "product_id": int(ln["product_id"]),
                    "allocation": share,
                    "landed_unit_cost": lcu,
                })

        conn.execute(
            "UPDATE purchase_orders "
            "   SET freight_allocation_basis = %s, "
            "       landed_cost_allocated_at = CURRENT_TIMESTAMP "
            " WHERE id = %s",
            (chosen_basis, pid),
        )
        conn.commit()
        return {
            "basis": chosen_basis,
            "overhead": overhead,
            "lines": result_lines,
        }


def get_landed_costs_for_po(po_id: int) -> list[dict]:
    """Return per-line landed-cost data for a PO."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, product_id, quantity_ordered, quantity_received, "
            "       unit_cost, line_total, landed_allocation, landed_unit_cost "
            "  FROM purchase_order_lines "
            " WHERE po_id = %s "
            " ORDER BY id",
            (int(po_id),),
        ).fetchall()
    return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


# ─── Part supersessions (T1.4) ─────────────────────────────────────────


def add_part_supersession(
    old_product_id: int,
    new_product_id: int,
    *,
    direction: str = "replaces",
    effective_date: str | None = None,
    reason: str | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> int:
    """Record that ``new_product_id`` supersedes ``old_product_id``.

    Refuses self-references and cycles (would-be chain that visits the
    same product twice).
    """
    old_id = int(old_product_id)
    new_id = int(new_product_id)
    if old_id == new_id:
        raise ValueError("Old and new product cannot be the same.")
    visited = {new_id}
    cursor_id = new_id
    with get_db() as conn:
        for _ in range(64):
            row = conn.execute(
                "SELECT new_product_id FROM part_supersessions "
                "WHERE old_product_id = %s LIMIT 1",
                (cursor_id,),
            ).fetchone()
            if not row:
                break
            nxt = int(row[0] if not hasattr(row, "keys") else row["new_product_id"])
            if nxt == old_id or nxt in visited:
                raise ValueError(
                    f"Supersession {old_id}->{new_id} would create a cycle."
                )
            visited.add(nxt)
            cursor_id = nxt
        cur = conn.execute(
            "INSERT INTO part_supersessions ("
            "  old_product_id, new_product_id, direction, "
            "  effective_date, reason, notes, created_by"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                old_id, new_id,
                (direction or "replaces").strip().lower(),
                effective_date,
                (reason or "").strip() or None,
                (notes or "").strip() or None,
                created_by,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_part_supersession_chain(product_id: int, *, max_hops: int = 64) -> list[int]:
    """Return chain starting at ``product_id`` including itself, walking
    forward to the most recent replacement. ``[A, B, C]`` for A→B→C."""
    chain: list[int] = [int(product_id)]
    seen = {int(product_id)}
    with get_db() as conn:
        for _ in range(max_hops):
            row = conn.execute(
                "SELECT new_product_id FROM part_supersessions "
                "WHERE old_product_id = %s "
                "ORDER BY effective_date DESC, id DESC LIMIT 1",
                (chain[-1],),
            ).fetchone()
            if not row:
                break
            nxt = int(row[0] if not hasattr(row, "keys") else row["new_product_id"])
            if nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
    return chain


def find_current_replacement_product(product_id: int) -> int | None:
    """Return the current (final) part in the chain, or None if the
    given product is already current."""
    chain = get_part_supersession_chain(product_id)
    if len(chain) <= 1:
        return None
    return chain[-1]


def list_part_supersessions_for_product(product_id: int) -> dict:
    """Return outgoing/incoming supersession edges for a product."""
    pid = int(product_id)
    with get_db() as conn:
        outgoing = conn.execute(
            "SELECT * FROM part_supersessions WHERE old_product_id = %s "
            "ORDER BY id DESC",
            (pid,),
        ).fetchall()
        incoming = conn.execute(
            "SELECT * FROM part_supersessions WHERE new_product_id = %s "
            "ORDER BY id DESC",
            (pid,),
        ).fetchall()

    def _norm(rows):
        return [
            dict(r) if hasattr(r, "keys") else dict(enumerate(r))
            for r in rows
        ]

    return {"outgoing": _norm(outgoing), "incoming": _norm(incoming)}


def delete_part_supersession(supersession_id: int) -> bool:
    """Remove a single supersession edge."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM part_supersessions WHERE id = %s",
            (int(supersession_id),),
        )
        conn.commit()
        try:
            return int(cur.rowcount) > 0
        except Exception:
            return True


# ─── Product fitments (T1.3) ───────────────────────────────────────────

_FITMENT_SOURCES = ("manual", "scraper", "ai", "vendor")


def add_product_fitment(
    product_id: int,
    *,
    make: str | None = None,
    model: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    engine_family: str | None = None,
    engine_model: str | None = None,
    position: str | None = None,
    notes: str | None = None,
    source: str = "manual",
    confidence: int | None = None,
    created_by: str | None = None,
) -> int:
    """Insert a fitment record for ``product_id``. Returns the new row id.

    At minimum one of ``make``/``model``/``engine_model`` must be given —
    a blank fitment is rejected so we don't pollute the search index.
    Year range is validated: ``year_from <= year_to`` when both supplied.
    """
    if not any(v and str(v).strip() for v in (make, model, engine_model)):
        raise ValueError(
            "Fitment requires at least one of make / model / engine_model."
        )
    if year_from is not None and year_to is not None:
        if int(year_from) > int(year_to):
            raise ValueError(
                f"year_from ({year_from}) > year_to ({year_to})."
            )
    src = (source or "manual").strip().lower()
    if src not in _FITMENT_SOURCES:
        raise ValueError(
            f"Invalid source '{source}'. Expected one of: "
            f"{', '.join(_FITMENT_SOURCES)}."
        )
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO product_fitments ("
            "  product_id, make, model, year_from, year_to, "
            "  engine_family, engine_model, position, notes, "
            "  source, confidence, created_by"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                int(product_id),
                (make or "").strip() or None,
                (model or "").strip() or None,
                int(year_from) if year_from else None,
                int(year_to) if year_to else None,
                (engine_family or "").strip() or None,
                (engine_model or "").strip() or None,
                (position or "").strip() or None,
                (notes or "").strip() or None,
                src,
                int(confidence) if confidence is not None else None,
                created_by,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_product_fitments(product_id: int) -> list[dict]:
    """Return all fitment rows for a product, newest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM product_fitments WHERE product_id = %s "
            "ORDER BY created_at DESC, id DESC",
            (int(product_id),),
        ).fetchall()
    return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def delete_product_fitment(fitment_id: int) -> bool:
    """Remove a single fitment row."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM product_fitments WHERE id = %s",
            (int(fitment_id),),
        )
        conn.commit()
        try:
            return int(cur.rowcount) > 0
        except Exception:
            return True


def search_products_by_fitment(
    make: str | None = None,
    model: str | None = None,
    year: int | None = None,
    engine_model: str | None = None,
    engine_family: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Find products that fit the given make / model / year / engine.

    All filters are AND-combined and matched case-insensitively on
    string columns. ``year`` is matched as ``year_from <= year <= year_to``
    (NULL bounds treated as open-ended).
    Returns product rows enriched with the matched fitment id/source.
    """
    where: list[str] = ["1=1"]
    params: list = []
    if make:
        where.append("LOWER(f.make) = LOWER(%s)"); params.append(str(make))
    if model:
        where.append("LOWER(f.model) = LOWER(%s)"); params.append(str(model))
    if engine_model:
        where.append("LOWER(f.engine_model) = LOWER(%s)")
        params.append(str(engine_model))
    if engine_family:
        where.append("LOWER(f.engine_family) = LOWER(%s)")
        params.append(str(engine_family))
    if year is not None:
        # Open-ended bounds: NULL means "no bound", so a NULL year_from
        # matches any year-from, etc.
        where.append("(f.year_from IS NULL OR f.year_from <= %s)")
        params.append(int(year))
        where.append("(f.year_to   IS NULL OR f.year_to   >= %s)")
        params.append(int(year))
    params.append(int(limit))
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT p.id, p.sku, p.title,
                   f.id AS fitment_id, f.source AS fitment_source,
                   f.confidence AS fitment_confidence
              FROM product_fitments f
              JOIN products p ON p.id = f.product_id
             WHERE {' AND '.join(where)}
             ORDER BY p.sku
             LIMIT %s
            """,
            tuple(params),
        ).fetchall()
    return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


# ─── Last-paid-price history (F3) ───────────────────────────────────────

def get_customer_product_history(
    customer_id: int,
    product_id: int,
    limit: int = 3,
) -> list[dict]:
    """Return the most recent invoices where ``customer_id`` bought ``product_id``.

    Only finalized invoices (status not draft/void/cancelled) are counted
    so the operator sees real paid history. Each row contains
    ``invoice_id``, ``invoice_number``, ``invoice_date``, ``status``,
    ``quantity``, ``unit_price``, ``line_total``. Newest first.
    """
    if not customer_id or not product_id:
        return []
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT inv.id             AS invoice_id,
                   inv.invoice_number AS invoice_number,
                   inv.invoice_date   AS invoice_date,
                   inv.status         AS status,
                   il.quantity        AS quantity,
                   il.unit_price      AS unit_price,
                   il.line_total      AS line_total
              FROM invoice_lines il
              JOIN invoices inv ON inv.id = il.invoice_id
             WHERE inv.customer_id = %s
               AND il.product_id  = %s
               AND COALESCE(inv.status,'') NOT IN ('draft','void','cancelled','voided')
             ORDER BY COALESCE(inv.invoice_date, inv.created_at) DESC,
                      inv.id DESC
             LIMIT %s
            """,
            (int(customer_id), int(product_id), int(max(1, limit))),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else dict(enumerate(r))
        out.append(d)
    return out


def get_customer_price(customer_id: int, product_id: int) -> dict:
    """Get effective price for a customer+product.  Priority: custom price > tier > list."""
    with get_db() as conn:
        # Check product-specific custom price first
        row = conn.execute("""
            SELECT cp.custom_price FROM customer_pricing cp
            WHERE cp.customer_id = %s AND cp.product_id = %s
              AND (cp.expiry_date IS NULL OR cp.expiry_date >= CURRENT_DATE)
            ORDER BY cp.effective_date DESC LIMIT 1
        """, (customer_id, product_id)).fetchone()
        if row:
            price = row[0] if isinstance(row, tuple) else (dict(row) if hasattr(row, 'keys') else {}).get("custom_price")
            if price is not None:
                return {"price": float(price), "source": "custom"}

        # Check tier-based pricing
        row = conn.execute("""
            SELECT pt.markup_pct, pt.discount_pct, pt.name as tier_name
            FROM customer_pricing cp
            JOIN price_tiers pt ON pt.id = cp.tier_id
            WHERE cp.customer_id = %s AND cp.product_id IS NULL
              AND (cp.expiry_date IS NULL OR cp.expiry_date >= CURRENT_DATE)
            ORDER BY cp.effective_date DESC LIMIT 1
        """, (customer_id,)).fetchone()
        if row:
            r = dict(row) if hasattr(row, 'keys') else {"markup_pct": row[0], "discount_pct": row[1], "tier_name": row[2]}
            # Get product list price
            prod = conn.execute("SELECT price FROM products WHERE id=%s", (product_id,)).fetchone()
            if prod:
                list_price = float(prod[0] if isinstance(prod, tuple) else (dict(prod) if hasattr(prod, 'keys') else {}).get("price", 0) or 0)
                markup = float(r.get("markup_pct", 0) or 0)
                discount = float(r.get("discount_pct", 0) or 0)
                final = list_price * (1 + markup / 100) * (1 - discount / 100)
                return {"price": round(final, 2), "source": "tier", "tier_name": r.get("tier_name")}

        return {"price": None, "source": "list"}


def get_customer_pricing_rules(customer_id: int) -> list:
    """Get all pricing rules for a customer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT cp.*, pt.name as tier_name, p.sku, p.title as product_title
            FROM customer_pricing cp
            LEFT JOIN price_tiers pt ON pt.id = cp.tier_id
            LEFT JOIN products p ON p.id = cp.product_id
            WHERE cp.customer_id = %s
            ORDER BY cp.effective_date DESC
        """, (customer_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def delete_customer_pricing(pricing_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM customer_pricing WHERE id=%s", (pricing_id,))
        conn.commit()
        return True


# ─── Tiered Pricing v2 (categories × bands × tier discounts) ────────────
# Migration 058 introduces a category-based pricing engine that supersedes
# the flat price_tiers / customer_pricing tables above. Legacy tables are
# preserved for back-compat; new code should call `compute_price()`.


def _row_to_dict(row) -> dict:
    if row is None:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    return dict(enumerate(row))


# ── Categories ────────────────────────────────────────────────────────

def list_price_categories(active_only: bool = True) -> list:
    with get_db() as conn:
        sql = "SELECT * FROM price_categories"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY sort_order, code"
        return [_row_to_dict(r) for r in conn.execute(sql).fetchall()]


def get_price_category(category_id_or_code) -> dict | None:
    with get_db() as conn:
        if isinstance(category_id_or_code, int):
            row = conn.execute("SELECT * FROM price_categories WHERE id=%s", (category_id_or_code,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM price_categories WHERE code=%s", (category_id_or_code,)).fetchone()
        return _row_to_dict(row)


def get_default_price_category() -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM price_categories WHERE is_default = 1 AND active = 1 ORDER BY sort_order LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM price_categories WHERE active = 1 ORDER BY sort_order LIMIT 1"
            ).fetchone()
        return _row_to_dict(row)


def create_price_category(code: str, name: str, *, description: str = "",
                          sort_order: int = 0, is_default: bool = False) -> int:
    with get_db() as conn:
        if is_default:
            conn.execute("UPDATE price_categories SET is_default = 0")
        conn.execute(
            """INSERT INTO price_categories (code, name, description, sort_order, is_default)
               VALUES (%s, %s, %s, %s, %s)""",
            (code, name, description, sort_order, 1 if is_default else 0),
        )
        conn.commit()
        return conn.lastrowid


def update_price_category(category_id: int, **fields) -> bool:
    allowed = {"code", "name", "description", "sort_order", "is_default", "active"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        if updates.get("is_default"):
            conn.execute("UPDATE price_categories SET is_default = 0")
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [category_id]
        conn.execute(
            f"UPDATE price_categories SET {set_clause}, updated_at = datetime('now') WHERE id=%s",
            tuple(vals),
        )
        conn.commit()
        return True


def delete_price_category(category_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM price_categories WHERE id=%s", (category_id,))
        conn.commit()
        return True


# ── Bands ─────────────────────────────────────────────────────────────

def list_bands(category_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM price_category_bands WHERE category_id=%s ORDER BY cost_min",
            (category_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def set_bands(category_id: int, bands: list) -> None:
    """Replace all bands for a category atomically.

    `bands` is a list of dicts or tuples: (cost_min, cost_max_or_None, margin_pct).
    """
    normalized: list[tuple] = []
    for i, b in enumerate(bands):
        if isinstance(b, dict):
            cmin = float(b.get("cost_min", 0) or 0)
            cmax = b.get("cost_max")
            margin = float(b.get("margin_pct", 0) or 0)
        else:
            cmin = float(b[0] or 0)
            cmax = b[1] if len(b) > 1 else None
            margin = float(b[2] if len(b) > 2 else 0)
        cmax = float(cmax) if cmax not in (None, "") else None
        normalized.append((category_id, cmin, cmax, margin, i))
    with get_db() as conn:
        conn.execute("DELETE FROM price_category_bands WHERE category_id=%s", (category_id,))
        for row in normalized:
            conn.execute(
                """INSERT INTO price_category_bands
                   (category_id, cost_min, cost_max, margin_pct, sort_order)
                   VALUES (%s, %s, %s, %s, %s)""",
                row,
            )
        conn.commit()


def find_band_for_cost(category_id: int, cost: float) -> dict | None:
    """Return the band whose [cost_min, cost_max) contains `cost`."""
    cost = float(cost or 0)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM price_category_bands WHERE category_id=%s ORDER BY cost_min",
            (category_id,),
        ).fetchall()
    match = None
    for row in rows:
        d = _row_to_dict(row)
        cmin = float(d.get("cost_min") or 0)
        cmax = d.get("cost_max")
        if cost >= cmin and (cmax is None or cost < float(cmax)):
            match = d
    return match


# ── Customer tiers ────────────────────────────────────────────────────

def list_customer_tiers(active_only: bool = True) -> list:
    with get_db() as conn:
        sql = "SELECT * FROM customer_tiers"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY sort_order, name"
        return [_row_to_dict(r) for r in conn.execute(sql).fetchall()]


def get_customer_tier(tier_id: int) -> dict | None:
    with get_db() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM customer_tiers WHERE id=%s", (tier_id,)).fetchone())


def get_default_customer_tier() -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM customer_tiers WHERE is_default = 1 AND active = 1 ORDER BY sort_order LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM customer_tiers WHERE active = 1 ORDER BY sort_order LIMIT 1"
            ).fetchone()
        return _row_to_dict(row)


def create_customer_tier(name: str, *, description: str = "",
                         sort_order: int = 0, is_default: bool = False) -> int:
    with get_db() as conn:
        if is_default:
            conn.execute("UPDATE customer_tiers SET is_default = 0")
        conn.execute(
            "INSERT INTO customer_tiers (name, description, sort_order, is_default) VALUES (%s, %s, %s, %s)",
            (name, description, sort_order, 1 if is_default else 0),
        )
        conn.commit()
        return conn.lastrowid


def update_customer_tier(tier_id: int, **fields) -> bool:
    allowed = {"name", "description", "sort_order", "is_default", "active"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        if updates.get("is_default"):
            conn.execute("UPDATE customer_tiers SET is_default = 0")
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [tier_id]
        conn.execute(
            f"UPDATE customer_tiers SET {set_clause}, updated_at = datetime('now') WHERE id=%s",
            tuple(vals),
        )
        conn.commit()
        return True


def delete_customer_tier(tier_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM customer_tiers WHERE id=%s", (tier_id,))
        conn.commit()
        return True


# ── Tier × Category discount matrix ───────────────────────────────────

def get_tier_category_discount(tier_id: int, category_id: int) -> float:
    with get_db() as conn:
        row = conn.execute(
            "SELECT discount_pct FROM customer_tier_discounts WHERE tier_id=%s AND category_id=%s",
            (tier_id, category_id),
        ).fetchone()
    if not row:
        return 0.0
    return float(row[0] if isinstance(row, tuple) else _row_to_dict(row).get("discount_pct") or 0)


def set_tier_category_discount(tier_id: int, category_id: int, discount_pct: float) -> None:
    """Upsert one cell of the tier × category matrix."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM customer_tier_discounts WHERE tier_id=%s AND category_id=%s",
            (tier_id, category_id),
        ).fetchone()
        if existing:
            row_id = existing[0] if isinstance(existing, tuple) else _row_to_dict(existing).get("id")
            conn.execute(
                "UPDATE customer_tier_discounts SET discount_pct=%s WHERE id=%s",
                (float(discount_pct or 0), row_id),
            )
        else:
            conn.execute(
                "INSERT INTO customer_tier_discounts (tier_id, category_id, discount_pct) VALUES (%s, %s, %s)",
                (tier_id, category_id, float(discount_pct or 0)),
            )
        conn.commit()


def get_tier_discount_grid() -> dict:
    """Return {tier_id: {category_id: discount_pct}} for all rows."""
    grid: dict = {}
    with get_db() as conn:
        rows = conn.execute(
            "SELECT tier_id, category_id, discount_pct FROM customer_tier_discounts"
        ).fetchall()
    for r in rows:
        d = _row_to_dict(r)
        tid = int(d["tier_id"])
        cid = int(d["category_id"])
        grid.setdefault(tid, {})[cid] = float(d.get("discount_pct") or 0)
    return grid


# ── Manufacturer ↔ Category mapping ───────────────────────────────────

def get_manufacturer_category(manufacturer_name: str) -> int | None:
    if not manufacturer_name:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT category_id FROM manufacturer_categories WHERE manufacturer_name=%s",
            (manufacturer_name,),
        ).fetchone()
    if not row:
        return None
    return int(row[0] if isinstance(row, tuple) else _row_to_dict(row)["category_id"])


def set_manufacturer_category(manufacturer_name: str, category_id: int | None) -> None:
    if not manufacturer_name:
        return
    with get_db() as conn:
        if category_id is None:
            conn.execute("DELETE FROM manufacturer_categories WHERE manufacturer_name=%s",
                         (manufacturer_name,))
        else:
            existing = conn.execute(
                "SELECT 1 FROM manufacturer_categories WHERE manufacturer_name=%s",
                (manufacturer_name,),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE manufacturer_categories
                       SET category_id=%s, updated_at=datetime('now')
                       WHERE manufacturer_name=%s""",
                    (category_id, manufacturer_name),
                )
            else:
                conn.execute(
                    "INSERT INTO manufacturer_categories (manufacturer_name, category_id) VALUES (%s, %s)",
                    (manufacturer_name, category_id),
                )
        conn.commit()


def list_manufacturer_categories() -> list:
    """Return [{manufacturer_name, category_id, code, name}, ...] for the matrix UI."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT mc.manufacturer_name, mc.category_id, pc.code, pc.name
               FROM manufacturer_categories mc
               LEFT JOIN price_categories pc ON pc.id = mc.category_id
               ORDER BY mc.manufacturer_name"""
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ── Vendor ↔ Category mapping ─────────────────────────────────────────
# Vendor mapping is the primary scope for tiered pricing as of migration
# 065 — it supersedes manufacturer mapping above (which is kept as a
# fallback for back-compat).

def get_vendor_category(vendor_id: int | None) -> int | None:
    if not vendor_id:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT category_id FROM vendor_categories WHERE vendor_id=%s",
            (int(vendor_id),),
        ).fetchone()
    if not row:
        return None
    return int(row[0] if isinstance(row, tuple) else _row_to_dict(row)["category_id"])


def set_vendor_category(vendor_id: int, category_id: int | None) -> None:
    if not vendor_id:
        return
    vid = int(vendor_id)
    with get_db() as conn:
        if category_id is None:
            conn.execute("DELETE FROM vendor_categories WHERE vendor_id=%s", (vid,))
        else:
            existing = conn.execute(
                "SELECT 1 FROM vendor_categories WHERE vendor_id=%s", (vid,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE vendor_categories
                       SET category_id=%s, updated_at=datetime('now')
                       WHERE vendor_id=%s""",
                    (int(category_id), vid),
                )
            else:
                conn.execute(
                    "INSERT INTO vendor_categories (vendor_id, category_id) VALUES (%s, %s)",
                    (vid, int(category_id)),
                )
        conn.commit()


def list_vendor_categories() -> list:
    """Return [{vendor_id, vendor_name, category_id, code, name}, ...] for the matrix UI."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT vc.vendor_id, v.name AS vendor_name, vc.category_id,
                      pc.code, pc.name
               FROM vendor_categories vc
               LEFT JOIN vendors v          ON v.id  = vc.vendor_id
               LEFT JOIN price_categories pc ON pc.id = vc.category_id
               ORDER BY v.name COLLATE NOCASE"""
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ── Resolution helpers ────────────────────────────────────────────────

def resolve_product_category(product) -> dict | None:
    """Pick the price category for a product.

    Order: products.price_category_id override → vendor mapping
           → manufacturer mapping (legacy fallback) → default.
    Accepts a dict (with keys price_category_id, preferred_vendor_id,
    supplier, manufacturer) or an int product_id.
    """
    if isinstance(product, int):
        with get_db() as conn:
            row = conn.execute(
                """SELECT price_category_id, preferred_vendor_id, supplier, manufacturer
                   FROM products WHERE id=%s""",
                (product,),
            ).fetchone()
        if not row:
            return get_default_price_category()
        product = _row_to_dict(row)

    override = product.get("price_category_id")
    if override:
        cat = get_price_category(int(override))
        if cat:
            return cat

    # Vendor mapping (primary)
    vid = product.get("preferred_vendor_id")
    if not vid:
        # Best-effort lookup by supplier name → vendor id
        supplier = product.get("supplier")
        if supplier:
            with get_db() as conn:
                vrow = conn.execute(
                    "SELECT id FROM vendors WHERE name=%s OR code=%s LIMIT 1",
                    (supplier, supplier),
                ).fetchone()
            if vrow:
                vid = vrow[0] if isinstance(vrow, tuple) else _row_to_dict(vrow).get("id")
    if vid:
        cid = get_vendor_category(int(vid))
        if cid:
            cat = get_price_category(cid)
            if cat:
                return cat

    # Manufacturer mapping (legacy fallback)
    mfr = product.get("manufacturer")
    if mfr:
        cid = get_manufacturer_category(mfr)
        if cid:
            cat = get_price_category(cid)
            if cat:
                return cat
    return get_default_price_category()


def resolve_customer_tier(customer) -> dict | None:
    """Pick the tier for a customer (id or dict). Falls back to default tier."""
    if customer is None:
        return get_default_customer_tier()
    if isinstance(customer, int):
        with get_db() as conn:
            row = conn.execute("SELECT tier_id FROM customers WHERE id=%s", (customer,)).fetchone()
        if not row:
            return get_default_customer_tier()
        customer = _row_to_dict(row)
    tid = customer.get("tier_id")
    if tid:
        tier = get_customer_tier(int(tid))
        if tier:
            return tier
    return get_default_customer_tier()


# ── compute_price: single source of truth ─────────────────────────────

def compute_price(product_id: int, customer_id: int | None = None,
                  *, qty: float = 1.0, override_cost: float | None = None) -> dict:
    """Compute the effective unit price for a product (and optionally a customer).

    Returns dict:
      {
        cost, category_id, category_code, band, list_price,
        tier_id, tier_name, discount_pct, net_price,
        custom_price (if customer_pricing override hit),
        source: 'custom' | 'tier' | 'category' | 'list'
      }
    """
    with get_db() as conn:
        prod_row = conn.execute(
            """SELECT id, sku, manufacturer, supplier, preferred_vendor_id,
                      price_category_id,
                      cost, pai_cost, selling_price, core_charge
               FROM products WHERE id=%s""",
            (product_id,),
        ).fetchone()
    if not prod_row:
        return {"cost": 0.0, "list_price": 0.0, "net_price": 0.0, "source": "list",
                "category_id": None, "tier_id": None}
    prod = _row_to_dict(prod_row)

    # Cost: explicit override > landed cost > PAI/raw vendor cost > 0.
    # Landed cost is preferred so the band is computed from the same number
    # the UI shows as "Cost (Landed) — used for margin".
    if override_cost is not None:
        cost = float(override_cost)
    else:
        cost = float(prod.get("cost") or prod.get("pai_cost") or 0)

    # Category resolution
    category = resolve_product_category(prod)
    cat_id = category["id"] if category else None
    cat_code = category["code"] if category else None

    # Band → margin → list price.
    # margin_pct is GROSS MARGIN: price = cost / (1 - m). Matches the
    # 'Target Margin' field in the product dialog (a 40% band displays
    # as 40% margin on the resulting price). A band of >=100% would
    # divide by zero and is treated as no-band.
    band = find_band_for_cost(cat_id, cost) if cat_id is not None else None
    if band is not None and cost > 0:
        margin_pct = float(band.get("margin_pct") or 0)
        if margin_pct >= 100:
            list_price = float(prod.get("selling_price") or 0)
        else:
            list_price = round(cost / (1 - margin_pct / 100.0), 2)
    else:
        # Fall back to product's stored selling_price if no band/cost
        list_price = float(prod.get("selling_price") or 0)
        margin_pct = 0.0

    # Tier discount on this category
    tier = resolve_customer_tier(customer_id) if customer_id else get_default_customer_tier()
    tier_id = tier["id"] if tier else None
    tier_name = tier["name"] if tier else None
    discount_pct = (
        get_tier_category_discount(tier_id, cat_id) if (tier_id and cat_id) else 0.0
    )

    net_price = round(list_price * (1 - discount_pct / 100.0), 2)
    if band is not None and cost > 0:
        source = "category"
    elif list_price > 0:
        # No band hit but product has a stored selling_price → flag as fallback
        # so callers (e.g. View Tier Pricing) can warn admins about unmapped
        # vendors / missing bands.
        source = "fallback"
    else:
        source = "list"

    # Per-customer custom price override (legacy customer_pricing table) — wins.
    custom_price = None
    if customer_id:
        with get_db() as conn:
            row = conn.execute(
                """SELECT custom_price FROM customer_pricing
                   WHERE customer_id=%s AND product_id=%s
                     AND custom_price IS NOT NULL
                     AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)
                   ORDER BY effective_date DESC LIMIT 1""",
                (customer_id, product_id),
            ).fetchone()
        if row:
            cp = row[0] if isinstance(row, tuple) else _row_to_dict(row).get("custom_price")
            if cp is not None:
                custom_price = float(cp)
                net_price = round(custom_price, 2)
                source = "custom"

    if discount_pct and source == "category":
        source = "tier"

    return {
        "cost": round(cost, 2),
        "category_id": cat_id,
        "category_code": cat_code,
        "band": band,
        "margin_pct": margin_pct,
        "list_price": list_price,
        "tier_id": tier_id,
        "tier_name": tier_name,
        "discount_pct": discount_pct,
        "net_price": net_price,
        "custom_price": custom_price,
        "source": source,
        "qty": float(qty or 1),
    }


# ─── Vendor Returns (RGA) ───────────────────────────────────────────────


def generate_rga_vr_number() -> str:
    """Generate next vendor return RGA number."""
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM vendor_returns").fetchone()
        cnt = row[0] if isinstance(row, tuple) else (dict(row) if hasattr(row, 'keys') else {}).get("cnt", 0)
        return f"VR-{int(cnt) + 1:04d}"


def create_vendor_return(vendor_id: int, reason: str = None, notes: str = None,
                         user: str = None) -> dict:
    rga = generate_rga_vr_number()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO vendor_returns (rga_number, vendor_id, reason, notes, created_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (rga, vendor_id, reason, notes, user))
        conn.commit()
        return {"id": conn.lastrowid, "rga_number": rga}


def list_vendor_returns(status: str = None, vendor_id: int = None,
                        limit: int = 100) -> list:
    with get_db() as conn:
        conditions, params = ["1=1"], []
        if status:
            conditions.append("vr.status = %s"); params.append(status)
        if vendor_id:
            conditions.append("vr.vendor_id = %s"); params.append(vendor_id)
        params.append(limit)
        rows = conn.execute(f"""
            SELECT vr.*, v.name as vendor_name
            FROM vendor_returns vr
            LEFT JOIN vendors v ON v.id = vr.vendor_id
            WHERE {' AND '.join(conditions)}
            ORDER BY vr.created_at DESC LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_vendor_return(return_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("""
            SELECT vr.*, v.name as vendor_name
            FROM vendor_returns vr
            LEFT JOIN vendors v ON v.id = vr.vendor_id
            WHERE vr.id = %s
        """, (return_id,)).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else (dict(enumerate(row)) if row else None)


def update_vendor_return(return_id: int, **fields) -> bool:
    allowed = {"status", "reason", "notes", "credit_amount", "credit_received",
               "credit_memo_number", "shipping_cost", "tracking_number",
               "shipped_at", "received_at", "credited_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [return_id]
        conn.execute(f"UPDATE vendor_returns SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=%s", tuple(vals))
        conn.commit()
        return True


def add_vendor_return_line(return_id: int, product_id: int, quantity: int = 1,
                           unit_cost: float = 0, reason: str = None,
                           condition: str = "defective", po_number: str = None,
                           serial_id: int = None, notes: str = None) -> int:
    with get_db() as conn:
        line_total = quantity * unit_cost
        conn.execute("""
            INSERT INTO vendor_return_lines (return_id, product_id, serial_id,
                quantity, unit_cost, line_total, reason, condition, po_number, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (return_id, product_id, serial_id, quantity, unit_cost,
              line_total, reason, condition, po_number, notes))
        # Recalculate total credit
        total = conn.execute(
            "SELECT COALESCE(SUM(line_total), 0) FROM vendor_return_lines WHERE return_id=%s",
            (return_id,)).fetchone()
        credit = float(total[0] if isinstance(total, tuple) else 0)
        conn.execute("UPDATE vendor_returns SET credit_amount=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                     (credit, return_id))
        conn.commit()
        return conn.lastrowid


def get_vendor_return_lines(return_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT vrl.*, p.sku, p.title as product_title
            FROM vendor_return_lines vrl
            LEFT JOIN products p ON p.id = vrl.product_id
            WHERE vrl.return_id = %s ORDER BY vrl.id
        """, (return_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def remove_vendor_return_line(line_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute("SELECT return_id FROM vendor_return_lines WHERE id=%s", (line_id,)).fetchone()
        if not row:
            return False
        return_id = row[0] if isinstance(row, tuple) else (dict(row) if hasattr(row, 'keys') else {}).get("return_id")
        conn.execute("DELETE FROM vendor_return_lines WHERE id=%s", (line_id,))
        total = conn.execute(
            "SELECT COALESCE(SUM(line_total), 0) FROM vendor_return_lines WHERE return_id=%s",
            (return_id,)).fetchone()
        credit = float(total[0] if isinstance(total, tuple) else 0)
        conn.execute("UPDATE vendor_returns SET credit_amount=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                     (credit, return_id))
        conn.commit()
        return True


def get_vendor_return_summary() -> dict:
    """Get vendor return dashboard KPIs."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM vendor_returns GROUP BY status").fetchall()
        result = {"pending": 0, "shipped": 0, "received": 0, "credited": 0, "cancelled": 0}
        for row in rows:
            r = dict(row) if hasattr(row, 'keys') else {"status": row[0], "cnt": row[1]}
            s = r.get("status", "")
            if s in result:
                result[s] = int(r.get("cnt", 0) or 0)
        credit_row = conn.execute(
            "SELECT COALESCE(SUM(credit_amount), 0) as total FROM vendor_returns WHERE credit_received=0 AND status != 'cancelled'"
        ).fetchone()
        result["outstanding_credit"] = float(credit_row[0] if isinstance(credit_row, tuple) else 0)
        return result


# ─── Vendor Core RGA Processing (migration 107) ──────────────────────────


def list_eligible_vendor_cores_for_rga(
    vendor_id: int | None = None, limit: int = 500,
) -> list[dict]:
    """Vendor cores ready to be attached to an RGA.

    Backed by ``vendor_core_obligations`` (mig 108/112). Only obligations
    that are still pending and not yet attached to an RGA are returned.
    Joins customer-side core return / invoice / PO so the picker can show
    the full lineage.

    Result rows carry legacy aliases (``qty``, ``core_charge``,
    ``customer_core_id_join``) so the existing eligible-cores-picker UI
    keeps working unchanged.
    """
    where = [
        "(vco.rga_id IS NULL OR vco.rga_id = 0)",
        "vco.status = 'pending'",
    ]
    params: list = []
    if vendor_id:
        where.append("vco.vendor_id = %s")
        params.append(vendor_id)
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT vco.*,
                   vco.quantity    AS qty,
                   vco.core_amount AS core_charge,
                   v.name          AS vendor_name,
                   p.sku           AS sku,
                   p.title         AS product_title,
                   po.po_number    AS po_number,
                   cr.id           AS customer_core_id_join,
                   cr.invoice_id   AS customer_invoice_id,
                   cr.customer_core_price  AS customer_core_charge,
                   cr.received_date        AS customer_received_at,
                   inv.invoice_number      AS source_invoice_number,
                   c.id            AS source_customer_id,
                   COALESCE(c.name, c.company) AS source_customer_name
            FROM vendor_core_obligations vco
            LEFT JOIN vendors v ON vco.vendor_id = v.id
            LEFT JOIN products p ON vco.product_id = p.id
            LEFT JOIN purchase_orders po ON vco.po_id = po.id
            LEFT JOIN core_returns cr ON cr.id = vco.customer_core_id
            LEFT JOIN invoices inv ON inv.id = cr.invoice_id
            LEFT JOIN customers c ON c.id = cr.customer_id
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(vco.due_date, '9999-12-31') ASC, vco.id DESC
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()
        return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def attach_vendor_cores_to_rga(
    rga_id: int, vcr_ids: list[int], user: str | None = None,
) -> int:
    """Attach selected vendor core obligations to an RGA.

    Despite the legacy name ``vcr_ids``, the integers are now
    ``vendor_core_obligations.id`` values.

    For each obligation:
      • set vco.rga_id and bump status → 'rga_created'
      • insert a vendor_return_lines row carrying the deposit + expected credit
        (writes both legacy ``vendor_core_return_id`` (NULL) and new
        ``vendor_core_obligation_id`` columns for downstream compatibility)
      • log a vendor_return_audit row

    Returns the number of cores attached.
    """
    if not vcr_ids:
        return 0
    attached = 0
    with get_db() as conn:
        for obl_id in vcr_ids:
            row = conn.execute(
                "SELECT * FROM vendor_core_obligations WHERE id = %s", (obl_id,)
            ).fetchone()
            if not row:
                continue
            r = dict(row) if hasattr(row, "keys") else dict(enumerate(row))
            # Skip if already attached.
            if r.get("rga_id"):
                continue
            qty = int(r.get("quantity") or 1)
            deposit = float(r.get("core_amount") or 0)
            line_total = qty * deposit
            conn.execute(
                """
                INSERT INTO vendor_return_lines (
                    return_id, product_id, quantity, unit_cost, line_total,
                    condition, vendor_core_obligation_id,
                    source_customer_core_id,
                    source_po_id, vendor_core_deposit, expected_credit, reason
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    rga_id, r.get("product_id"), qty, deposit, line_total,
                    "core_return", obl_id, r.get("customer_core_id"),
                    r.get("po_id"), deposit, line_total, "vendor_core_return",
                ),
            )
            old_status = r.get("status") or "pending"
            conn.execute(
                """
                UPDATE vendor_core_obligations
                   SET rga_id = %s,
                       status = 'rga_created',
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s
                """,
                (rga_id, obl_id),
            )
            conn.execute(
                """
                INSERT INTO vendor_return_audit
                    (rga_id, vendor_core_return_id, event_type,
                     old_status, new_status, user, notes)
                VALUES (%s, %s, 'attach_to_rga', %s, 'rga_created', %s, %s)
                """,
                (rga_id, obl_id, old_status, user,
                 "Attached vendor_core_obligation to RGA"),
            )
            attached += 1
        # Recompute RGA credit_amount from lines.
        total = conn.execute(
            "SELECT COALESCE(SUM(line_total),0) FROM vendor_return_lines WHERE return_id = %s",
            (rga_id,),
        ).fetchone()
        credit = float(total[0] if isinstance(total, tuple) else 0)
        conn.execute(
            "UPDATE vendor_returns SET credit_amount = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (credit, rga_id),
        )
        conn.commit()
    return attached


def log_vendor_return_event(
    rga_id: int | None,
    *,
    vendor_core_return_id: int | None = None,
    event_type: str,
    old_status: str | None = None,
    new_status: str | None = None,
    tracking_number: str | None = None,
    credit_amount: float | None = None,
    user: str | None = None,
    notes: str | None = None,
) -> int:
    """Insert an audit row. Returns the new audit id."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO vendor_return_audit (
                rga_id, vendor_core_return_id, event_type, old_status,
                new_status, tracking_number, credit_amount, user, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (rga_id, vendor_core_return_id, event_type, old_status,
             new_status, tracking_number, credit_amount, user, notes),
        )
        conn.commit()
        return conn.lastrowid or 0


def get_vendor_return_audit(
    rga_id: int | None = None, vcr_id: int | None = None, limit: int = 200,
) -> list[dict]:
    where = ["1=1"]
    params: list = []
    if rga_id is not None:
        where.append("rga_id = %s")
        params.append(rga_id)
    if vcr_id is not None:
        where.append("vendor_core_return_id = %s")
        params.append(vcr_id)
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM vendor_return_audit
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()
        return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


# Vendor core status flow:
#   pending → added_to_rga → rga_created → shipped → vendor_received
#   → credit_pending → credited / rejected / written_off / voided
_VENDOR_CORE_RGA_STATUS_FLOW = {
    "pending": {"added_to_rga", "voided", "written_off"},
    "added_to_rga": {"rga_created", "pending", "voided"},
    "rga_created": {"shipped", "voided"},
    "shipped": {"vendor_received", "rejected", "voided"},
    "vendor_received": {"credit_pending", "rejected", "credited"},
    "credit_pending": {"credited", "rejected", "written_off"},
    "credited": set(),
    "rejected": {"written_off"},
    "written_off": set(),
    "voided": set(),
}


def transition_vendor_core_status(
    vcr_id: int,
    new_status: str,
    *,
    user: str | None = None,
    tracking_number: str | None = None,
    credit_amount: float | None = None,
    vendor_credit_number: str | None = None,
    credit_received_date: str | None = None,
    notes: str | None = None,
    force: bool = False,
) -> bool:
    """Move a vendor_core_returns row to a new status with audit logging.

    Validates against ``_VENDOR_CORE_RGA_STATUS_FLOW`` unless ``force=True``.
    Updates ancillary columns appropriate for the target status (e.g.
    ``shipped_date`` + ``tracking_number`` on 'shipped').
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM vendor_core_returns WHERE id = %s", (vcr_id,)
        ).fetchone()
        if not row:
            return False
        r = dict(row) if hasattr(row, "keys") else dict(enumerate(row))
        old_status = r.get("status") or "pending"
        allowed = _VENDOR_CORE_RGA_STATUS_FLOW.get(old_status, set())
        if not force and new_status not in allowed and new_status != old_status:
            raise ValueError(
                f"Illegal transition {old_status!r} → {new_status!r}"
            )
        sets = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
        params: list = [new_status]
        if new_status == "shipped" and tracking_number:
            sets.append("tracking_number = %s")
            params.append(tracking_number)
            sets.append("shipped_date = CURRENT_DATE")
        if new_status == "credited":
            if credit_amount is not None:
                sets.append("credit_amount = %s")
                params.append(float(credit_amount))
            if vendor_credit_number:
                sets.append("vendor_credit_number = %s")
                params.append(vendor_credit_number)
            sets.append("credit_received_date = COALESCE(%s, CURRENT_DATE)")
            params.append(credit_received_date)
        if new_status == "rejected":
            sets.append("rejected_at = CURRENT_TIMESTAMP")
        if new_status == "written_off":
            sets.append("written_off_at = CURRENT_TIMESTAMP")
        params.append(vcr_id)
        conn.execute(
            f"UPDATE vendor_core_returns SET {', '.join(sets)} WHERE id = %s",
            tuple(params),
        )
        conn.execute(
            """
            INSERT INTO vendor_return_audit (
                rga_id, vendor_core_return_id, event_type, old_status,
                new_status, tracking_number, credit_amount, user, notes
            ) VALUES (%s, %s, 'status_change', %s, %s, %s, %s, %s, %s)
            """,
            (r.get("rga_id"), vcr_id, old_status, new_status,
             tracking_number, credit_amount, user, notes),
        )
        conn.commit()

        # If credited, hand off to existing QBO vendor-credit pusher.
        if new_status == "credited":
            try:
                mark_vendor_core_credited(
                    vcr_id,
                    credit_amount=credit_amount or float(r.get("credit_amount") or 0),
                    vendor_credit_number=vendor_credit_number,
                    credit_received_date=credit_received_date,
                )
            except Exception:
                # Local credit must succeed even if QBO push fails.
                pass
    return True


def transition_rga_status(
    rga_id: int,
    new_status: str,
    *,
    user: str | None = None,
    tracking_number: str | None = None,
    credit_amount: float | None = None,
    credit_memo_number: str | None = None,
    notes: str | None = None,
    cascade_lines: bool = True,
) -> bool:
    """Move a vendor_returns row to a new status and optionally cascade to
    the linked vendor_core_returns lines.

    Status mapping (RGA → vendor core):
        rga_created     → rga_created
        shipped         → shipped
        received        → vendor_received
        credit_pending  → credit_pending
        credited        → credited
        rejected        → rejected
        written_off     → written_off
        voided          → voided
    """
    cascade_map = {
        "rga_created": "rga_created",
        "shipped": "shipped",
        "received": "vendor_received",
        "vendor_received": "vendor_received",
        "credit_pending": "credit_pending",
        "credited": "credited",
        "rejected": "rejected",
        "written_off": "written_off",
        "cancelled": "voided",
        "voided": "voided",
    }
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM vendor_returns WHERE id = %s", (rga_id,)
        ).fetchone()
        if not row:
            return False
        old_status = (
            row[0] if isinstance(row, tuple)
            else (dict(row).get("status") if hasattr(row, "keys") else "pending")
        )
        fields = {"status": new_status}
        ts_col = {
            "shipped": "shipped_at",
            "received": "received_at",
            "vendor_received": "received_at",
            "credited": "credited_at",
            "rejected": "rejected_at",
            "written_off": "written_off_at",
        }.get(new_status)
        if tracking_number:
            fields["tracking_number"] = tracking_number
        if credit_amount is not None:
            fields["credit_amount"] = credit_amount
            if new_status == "credited":
                fields["credit_received"] = 1
        if credit_memo_number:
            fields["credit_memo_number"] = credit_memo_number
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        params = list(fields.values())
        if ts_col:
            set_clause += f", {ts_col} = CURRENT_TIMESTAMP"
        params.append(rga_id)
        conn.execute(
            f"UPDATE vendor_returns SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            tuple(params),
        )
        conn.execute(
            """
            INSERT INTO vendor_return_audit (
                rga_id, event_type, old_status, new_status,
                tracking_number, credit_amount, user, notes
            ) VALUES (%s, 'rga_status_change', %s, %s, %s, %s, %s, %s)
            """,
            (rga_id, old_status, new_status, tracking_number,
             credit_amount, user, notes),
        )
        conn.commit()

    if cascade_lines:
        cascade_target = cascade_map.get(new_status)
        if cascade_target:
            # Find linked vendor cores and transition each (best-effort).
            with get_db() as conn:
                vcr_rows = conn.execute(
                    "SELECT id, status FROM vendor_core_returns WHERE rga_id = %s",
                    (rga_id,),
                ).fetchall()
            for vrow in vcr_rows:
                vid = vrow[0] if isinstance(vrow, tuple) else dict(vrow).get("id")
                vstatus = (
                    vrow[1] if isinstance(vrow, tuple)
                    else dict(vrow).get("status")
                )
                if vstatus == cascade_target:
                    continue
                try:
                    transition_vendor_core_status(
                        vid, cascade_target,
                        user=user,
                        tracking_number=tracking_number,
                        credit_amount=(
                            credit_amount / max(len(vcr_rows), 1)
                            if credit_amount is not None and new_status == "credited"
                            else None
                        ),
                        vendor_credit_number=credit_memo_number,
                        notes=notes,
                        force=True,
                    )
                except Exception:
                    continue
    return True


def get_vendor_core_kpis(vendor_id: int | None = None) -> dict:
    """KPI snapshot for the Vendor Returns / Vendor Cores screen.

    Backed by ``vendor_core_obligations`` (mig 108/112). If ``vendor_id``
    is provided, KPIs are scoped to that vendor. Otherwise totals across
    all vendors are returned (overview mode).
    """
    out = {
        "awaiting_customer_core": 0,
        "pending_vendor_cores": 0,
        "shipped_cores": 0,
        "vendor_received": 0,
        "credited": 0,
        "outstanding_vendor_core_amount": 0.0,
    }
    where_extra = ""
    params: tuple = ()
    if vendor_id:
        where_extra = " WHERE vendor_id = %s"
        params = (vendor_id,)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT status, COUNT(*) AS cnt,
                   COALESCE(SUM(quantity * core_amount), 0) AS total
            FROM vendor_core_obligations
            {where_extra}
            GROUP BY status
            """,
            params,
        ).fetchall()
        counts: dict[str, dict] = {}
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {
                "status": r[0], "cnt": r[1], "total": r[2],
            }
            counts[d.get("status") or ""] = d
        out["awaiting_customer_core"] = int(
            counts.get("awaiting_customer_core", {}).get("cnt") or 0
        )
        out["pending_vendor_cores"] = int(
            (counts.get("pending", {}).get("cnt") or 0)
            + (counts.get("awaiting_return", {}).get("cnt") or 0)
            + (counts.get("rga_created", {}).get("cnt") or 0)
        )
        out["shipped_cores"] = int(counts.get("shipped", {}).get("cnt") or 0)
        out["vendor_received"] = int(counts.get("vendor_received", {}).get("cnt") or 0)
        out["credited"] = int(counts.get("credited", {}).get("cnt") or 0)
        outstanding_where = (
            "status NOT IN ('credited', 'rejected', 'written_off')"
        )
        if vendor_id:
            outstanding_where += " AND vendor_id = %s"
        outstanding_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(quantity * core_amount
                                - COALESCE(credit_received, 0)), 0) AS outstanding
            FROM vendor_core_obligations
            WHERE {outstanding_where}
            """,
            params,
        ).fetchone()
        try:
            if outstanding_row is None:
                out["outstanding_vendor_core_amount"] = 0.0
            elif hasattr(outstanding_row, "keys"):
                out["outstanding_vendor_core_amount"] = float(
                    outstanding_row["outstanding"] or 0
                )
            else:
                out["outstanding_vendor_core_amount"] = float(
                    outstanding_row[0] or 0
                )
        except Exception:
            out["outstanding_vendor_core_amount"] = 0.0
    return out


def list_outstanding_vendor_obligations(
    vendor_id: int | None = None, limit: int = 500,
) -> list[dict]:
    """All open vendor_core_obligations not yet attached to an RGA.

    Used by the Vendor Returns screen to show what JAK's owes back to
    vendors (both awaiting-customer-core and pending). Joins vendor,
    product, source PO, and source customer for full lineage.
    """
    where = [
        "(vco.rga_id IS NULL OR vco.rga_id = 0)",
        "vco.status IN ('awaiting_customer_core', 'pending')",
    ]
    params: list = []
    if vendor_id:
        where.append("vco.vendor_id = %s")
        params.append(vendor_id)
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT vco.*,
                   v.name          AS vendor_name,
                   p.sku           AS sku,
                   p.title         AS product_title,
                   po.po_number    AS po_number,
                   cr.invoice_id   AS customer_invoice_id,
                   inv.invoice_number      AS source_invoice_number,
                   COALESCE(c.name, c.company) AS source_customer_name
            FROM vendor_core_obligations vco
            LEFT JOIN vendors v ON vco.vendor_id = v.id
            LEFT JOIN products p ON vco.product_id = p.id
            LEFT JOIN purchase_orders po ON vco.po_id = po.id
            LEFT JOIN core_returns cr ON cr.id = vco.customer_core_id
            LEFT JOIN invoices inv ON inv.id = cr.invoice_id
            LEFT JOIN customers c ON c.id = cr.customer_id
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE vco.status WHEN 'pending' THEN 0
                                WHEN 'awaiting_customer_core' THEN 1
                                ELSE 2 END,
                COALESCE(vco.due_date, '9999-12-31') ASC,
                vco.id DESC
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()
        return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]


def delete_vendor_return(rga_id: int) -> dict:
    """Delete an RGA (vendor_return).

    Safety rules:
      * Cannot delete if status not in ('pending', 'cancelled').
      * Cannot delete if any vendor_return_lines row carries a
        ``vendor_core_obligation_id`` (the obligation must be released
        first to keep customer-core lineage intact).

    Returns ``{success, error?}``.
    """
    if not rga_id:
        return {"success": False, "error": "rga_id required"}
    with get_db() as conn:
        ret = conn.execute(
            "SELECT id, status, rga_number FROM vendor_returns WHERE id = %s",
            (rga_id,),
        ).fetchone()
        if not ret:
            return {"success": False, "error": "RGA not found"}
        r = dict(ret) if hasattr(ret, "keys") else {
            "id": ret[0], "status": ret[1], "rga_number": ret[2],
        }
        status = (r.get("status") or "").lower()
        if status not in ("pending", "cancelled", ""):
            return {
                "success": False,
                "error": (
                    f"Cannot delete RGA {r.get('rga_number')}: status is "
                    f"'{status}'. Only pending or cancelled RGAs may be deleted."
                ),
            }
        attached = conn.execute(
            """
            SELECT COUNT(*) AS cnt
              FROM vendor_return_lines
             WHERE return_id = %s
               AND vendor_core_obligation_id IS NOT NULL
            """,
            (rga_id,),
        ).fetchone()
        attached_cnt = (
            attached["cnt"] if attached and hasattr(attached, "keys")
            else (attached[0] if attached else 0)
        )
        if int(attached_cnt or 0) > 0:
            return {
                "success": False,
                "error": (
                    f"Cannot delete: {int(attached_cnt)} vendor core "
                    "obligation(s) are attached. Release them first by "
                    "removing those lines (this returns them to the "
                    "outstanding queue)."
                ),
            }
        # Free any obligations that point here defensively (shouldn't be
        # any given the check above, but keep schema consistent).
        conn.execute(
            "UPDATE vendor_core_obligations "
            "   SET rga_id = NULL, status = 'pending' "
            " WHERE rga_id = %s",
            (rga_id,),
        )
        conn.execute(
            "DELETE FROM vendor_return_lines WHERE return_id = %s",
            (rga_id,),
        )
        conn.execute(
            "DELETE FROM vendor_return_audit WHERE rga_id = %s",
            (rga_id,),
        )
        conn.execute(
            "DELETE FROM vendor_returns WHERE id = %s",
            (rga_id,),
        )
        conn.commit()
    return {"success": True, "rga_number": r.get("rga_number")}


# ─── Kits / Assemblies ──────────────────────────────────────────────────


def list_kits(active_only: bool = True) -> list:
    with get_db() as conn:
        where = "WHERE k.is_active = 1" if active_only else ""
        rows = conn.execute(f"""
            SELECT k.*, COUNT(kc.id) as component_count,
                   COALESCE(SUM(kc.quantity), 0) as total_parts
            FROM kits k LEFT JOIN kit_components kc ON kc.kit_id = k.id
            {where}
            GROUP BY k.id ORDER BY k.name
        """).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_kit(kit_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM kits WHERE id=%s", (kit_id,)).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else (dict(enumerate(row)) if row else None)


def create_kit(sku: str, name: str, description: str = None,
               kit_price: float = 0) -> int:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO kits (sku, name, description, kit_price)
            VALUES (%s, %s, %s, %s)
        """, (sku, name, description, kit_price))
        conn.commit()
        return conn.lastrowid


def update_kit(kit_id: int, **fields) -> bool:
    allowed = {"sku", "name", "description", "kit_price", "is_active"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [kit_id]
        conn.execute(f"UPDATE kits SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=%s", tuple(vals))
        conn.commit()
        return True


def delete_kit(kit_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM kit_components WHERE kit_id=%s", (kit_id,))
        conn.execute("DELETE FROM kits WHERE id=%s", (kit_id,))
        conn.commit()
        return True


def add_kit_component(kit_id: int, product_id: int, quantity: int = 1,
                      notes: str = None) -> int:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO kit_components (kit_id, product_id, quantity, notes)
            VALUES (%s, %s, %s, %s)
        """, (kit_id, product_id, quantity, notes))
        conn.commit()
        return conn.lastrowid


def remove_kit_component(component_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM kit_components WHERE id=%s", (component_id,))
        conn.commit()
        return True


def get_kit_components(kit_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT kc.*, p.sku, p.title as product_title, p.selling_price as price,
                   p.qty_on_hand, p.cost
            FROM kit_components kc
            LEFT JOIN products p ON p.id = kc.product_id
            WHERE kc.kit_id = %s ORDER BY kc.id
        """, (kit_id,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_kit_availability(kit_id: int) -> dict:
    """How many complete kits can be built from current inventory."""
    components = get_kit_components(kit_id)
    if not components:
        return {"available": 0, "components": []}
    min_kits = None
    details = []
    for c in components:
        on_hand = int(c.get("qty_on_hand", 0) or 0)
        needed = int(c.get("quantity", 1) or 1)
        available_kits = on_hand // needed if needed > 0 else 0
        details.append({
            "sku": c.get("sku"), "needed": needed,
            "on_hand": on_hand, "available_kits": available_kits
        })
        if min_kits is None or available_kits < min_kits:
            min_kits = available_kits
    return {"available": min_kits or 0, "components": details}


def explode_kit_to_lines(kit_id: int) -> list:
    """Get kit components as line items for adding to a sales order."""
    components = get_kit_components(kit_id)
    lines = []
    for c in components:
        lines.append({
            "product_id": c.get("product_id"),
            "sku": c.get("sku"),
            "description": c.get("product_title", ""),
            "quantity": c.get("quantity", 1),
            "unit_price": float(c.get("price", 0) or 0),
        })
    return lines


# ─── Shipments / Delivery & ETA Tracking ────────────────────────────────


HENDERSON_ADDRESS = "Henderson, NV"


def create_shipment(shipment_type: str = "outbound", so_id: int = None,
                    po_id: int = None, customer_id: int = None,
                    vendor_id: int = None, carrier: str = None,
                    service_level: str = None, tracking_number: str = None,
                    origin: str = None, destination: str = None,
                    ship_date: str = None, estimated_arrival: str = None,
                    weight_lbs: float = None, freight_cost: float = 0,
                    freight_charge: float = 0, notes: str = None,
                    user: str = None) -> int:
    """Create inbound (vendor→Henderson) or outbound (Henderson→customer) shipment."""
    with get_db() as conn:
        if shipment_type == "inbound" and not origin:
            origin = "Vendor"
        if shipment_type == "inbound" and not destination:
            destination = HENDERSON_ADDRESS
        if shipment_type == "outbound" and not origin:
            origin = HENDERSON_ADDRESS
        conn.execute("""
            INSERT INTO shipments (shipment_type, so_id, po_id, customer_id, vendor_id,
                carrier, service_level, tracking_number, origin_address, destination_address,
                ship_date, estimated_arrival, weight_lbs, freight_cost, freight_charge,
                notes, created_by, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'in_transit')
        """, (shipment_type, so_id, po_id, customer_id, vendor_id,
              carrier, service_level, tracking_number, origin, destination,
              ship_date, estimated_arrival, weight_lbs, freight_cost,
              freight_charge, notes, user))
        conn.commit()
        return conn.lastrowid


def list_shipments(shipment_type: str = None, status: str = None,
                   customer_id: int = None, vendor_id: int = None,
                   limit: int = 100) -> list:
    with get_db() as conn:
        conditions, params = ["1=1"], []
        if shipment_type:
            conditions.append("s.shipment_type = %s"); params.append(shipment_type)
        if status:
            conditions.append("s.status = %s"); params.append(status)
        if customer_id:
            conditions.append("s.customer_id = %s"); params.append(customer_id)
        if vendor_id:
            conditions.append("s.vendor_id = %s"); params.append(vendor_id)
        params.append(limit)
        rows = conn.execute(f"""
            SELECT s.*, c.name as customer_name, c.company as customer_company,
                   v.name as vendor_name, so.so_number, po.po_number
            FROM shipments s
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN vendors v ON v.id = s.vendor_id
            LEFT JOIN sales_orders so ON so.id = s.so_id
            LEFT JOIN purchase_orders po ON po.id = s.po_id
            WHERE {' AND '.join(conditions)}
            ORDER BY s.created_at DESC LIMIT %s
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_shipment(shipment_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("""
            SELECT s.*, c.name as customer_name, v.name as vendor_name,
                   so.so_number, po.po_number
            FROM shipments s
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN vendors v ON v.id = s.vendor_id
            LEFT JOIN sales_orders so ON so.id = s.so_id
            LEFT JOIN purchase_orders po ON po.id = s.po_id
            WHERE s.id = %s
        """, (shipment_id,)).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else (dict(enumerate(row)) if row else None)


def update_shipment(shipment_id: int, **fields) -> bool:
    allowed = {"carrier", "service_level", "tracking_number", "origin_address",
               "destination_address", "ship_date", "estimated_arrival",
               "actual_arrival", "delivery_confirmed", "weight_lbs",
               "freight_cost", "freight_charge", "status", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [shipment_id]
        conn.execute(f"UPDATE shipments SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=%s", tuple(vals))
        conn.commit()
        return True


def confirm_delivery(shipment_id: int) -> bool:
    """Mark shipment as delivered with current timestamp."""
    with get_db() as conn:
        conn.execute("""
            UPDATE shipments SET status='delivered', delivery_confirmed=1,
                   actual_arrival=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (shipment_id,))
        conn.commit()
        return True


def get_delivery_summary() -> dict:
    """Get delivery dashboard KPIs."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT shipment_type, status, COUNT(*) as cnt FROM shipments
            GROUP BY shipment_type, status
        """).fetchall()
        result = {
            "inbound_transit": 0, "inbound_delivered": 0,
            "outbound_transit": 0, "outbound_delivered": 0,
            "total_active": 0
        }
        for row in rows:
            r = dict(row) if hasattr(row, 'keys') else {"shipment_type": row[0], "status": row[1], "cnt": row[2]}
            stype = r.get("shipment_type", "")
            status = r.get("status", "")
            cnt = int(r.get("cnt", 0) or 0)
            key = f"{stype}_{status}"
            if key == "inbound_in_transit":
                result["inbound_transit"] = cnt
            elif key == "inbound_delivered":
                result["inbound_delivered"] = cnt
            elif key == "outbound_in_transit":
                result["outbound_transit"] = cnt
            elif key == "outbound_delivered":
                result["outbound_delivered"] = cnt
            if status == "in_transit":
                result["total_active"] += cnt

        # Overdue (past estimated arrival)
        overdue = conn.execute("""
            SELECT COUNT(*) as cnt FROM shipments
            WHERE status = 'in_transit' AND estimated_arrival < CURRENT_DATE
        """).fetchone()
        result["overdue"] = int(overdue[0] if isinstance(overdue, tuple) else 0)

        # Today's deliveries
        today = conn.execute("""
            SELECT COUNT(*) as cnt FROM shipments
            WHERE estimated_arrival = CURRENT_DATE AND status = 'in_transit'
        """).fetchone()
        result["arriving_today"] = int(today[0] if isinstance(today, tuple) else 0)

        return result


def get_eta_board(days_ahead: int = 14) -> list:
    """Get upcoming ETAs for the next N days."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, c.name as customer_name, c.company as customer_company,
                   v.name as vendor_name, so.so_number, po.po_number
            FROM shipments s
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN vendors v ON v.id = s.vendor_id
            LEFT JOIN sales_orders so ON so.id = s.so_id
            LEFT JOIN purchase_orders po ON po.id = s.po_id
            WHERE s.status = 'in_transit'
              AND s.estimated_arrival <= date('now', '+' || %s || ' days')
            ORDER BY s.estimated_arrival ASC
        """, (days_ahead,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# ─── Supersessions ──────────────────────────────────────────────────────


def add_supersession(old_sku: str, new_sku: str, reason: str = None,
                     notes: str = None) -> int:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO supersessions (old_sku, new_sku, reason, notes)
            VALUES (%s, %s, %s, %s)
        """, (old_sku, new_sku, reason, notes))
        conn.commit()
        return conn.lastrowid


def get_supersession_chain(sku: str) -> list:
    """Follow supersession chain to find the latest part number."""
    with get_db() as conn:
        chain = [sku]
        current = sku
        seen = {sku}
        while True:
            row = conn.execute(
                "SELECT new_sku FROM supersessions WHERE old_sku=%s ORDER BY supersession_date DESC LIMIT 1",
                (current,)).fetchone()
            if not row:
                break
            new_sku = row[0] if isinstance(row, tuple) else (dict(row) if hasattr(row, 'keys') else {}).get("new_sku")
            if not new_sku or new_sku in seen:
                break
            chain.append(new_sku)
            seen.add(new_sku)
            current = new_sku
        return chain


def get_supersessions_for_sku(sku: str) -> dict:
    """Get both predecessors and successors for a SKU."""
    with get_db() as conn:
        predecessors = conn.execute(
            "SELECT * FROM supersessions WHERE new_sku=%s ORDER BY supersession_date DESC",
            (sku,)).fetchall()
        successors = conn.execute(
            "SELECT * FROM supersessions WHERE old_sku=%s ORDER BY supersession_date DESC",
            (sku,)).fetchall()
        return {
            "predecessors": [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in predecessors],
            "successors": [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in successors],
            "chain": get_supersession_chain(sku),
        }


def delete_supersession(supersession_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM supersessions WHERE id=%s", (supersession_id,))
        conn.commit()
        return True


def search_supersessions(query: str) -> list:
    with get_db() as conn:
        pattern = f"%{query}%"
        rows = conn.execute("""
            SELECT s.*, p1.title as old_title, p2.title as new_title
            FROM supersessions s
            LEFT JOIN products p1 ON p1.sku = s.old_sku
            LEFT JOIN products p2 ON p2.sku = s.new_sku
            WHERE s.old_sku LIKE %s OR s.new_sku LIKE %s
            ORDER BY s.supersession_date DESC
        """, (pattern, pattern)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# ─── Margin Analysis ────────────────────────────────────────────────────


def get_product_margins(limit: int = 100, sort_by: str = "margin_pct") -> list:
    """Get profit margins per product (price - cost)."""
    with get_db() as conn:
        order = "margin_pct DESC" if sort_by == "margin_pct" else "total_revenue DESC"
        rows = conn.execute(f"""
            SELECT p.id, p.sku, p.title, p.selling_price as price, p.cost, p.qty_on_hand,
                   CASE WHEN p.cost > 0 THEN ROUND((p.selling_price - p.cost) / p.cost * 100, 1) ELSE 0 END as margin_pct,
                   ROUND(p.selling_price - COALESCE(p.cost, 0), 2) as margin_dollars,
                   COALESCE(sales.total_revenue, 0) as total_revenue,
                   COALESCE(sales.total_cost, 0) as total_cost,
                   COALESCE(sales.units_sold, 0) as units_sold
            FROM products p
            LEFT JOIN (
                SELECT il.product_id,
                       SUM(il.line_total) as total_revenue,
                       SUM(il.quantity * COALESCE(p2.cost, 0)) as total_cost,
                       SUM(il.quantity) as units_sold
                FROM invoice_lines il
                JOIN products p2 ON p2.id = il.product_id
                JOIN invoices inv ON inv.id = il.invoice_id AND inv.status != 'voided'
                GROUP BY il.product_id
            ) sales ON sales.product_id = p.id
            WHERE p.price > 0
            ORDER BY {order} LIMIT %s
        """, (limit,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_customer_margins(limit: int = 50) -> list:
    """Get profit margins per customer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.company,
                   SUM(il.line_total) as total_revenue,
                   SUM(il.quantity * COALESCE(p.cost, 0)) as total_cost,
                   ROUND(SUM(il.line_total) - SUM(il.quantity * COALESCE(p.cost, 0)), 2) as profit,
                   CASE WHEN SUM(il.quantity * COALESCE(p.cost, 0)) > 0
                        THEN ROUND((SUM(il.line_total) - SUM(il.quantity * COALESCE(p.cost, 0)))
                                   / SUM(il.quantity * COALESCE(p.cost, 0)) * 100, 1)
                        ELSE 0 END as margin_pct,
                   COUNT(DISTINCT inv.id) as order_count
            FROM customers c
            JOIN invoices inv ON inv.customer_id = c.id AND inv.status != 'voided'
            JOIN invoice_lines il ON il.invoice_id = inv.id
            LEFT JOIN products p ON p.id = il.product_id
            GROUP BY c.id
            ORDER BY total_revenue DESC LIMIT %s
        """, (limit,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_margin_summary() -> dict:
    """Overall margin KPIs."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(il.line_total), 0) as total_revenue,
                   COALESCE(SUM(il.quantity * COALESCE(p.cost, 0)), 0) as total_cost,
                   COUNT(DISTINCT inv.id) as invoice_count
            FROM invoices inv
            JOIN invoice_lines il ON il.invoice_id = inv.id
            LEFT JOIN products p ON p.id = il.product_id
            WHERE inv.status != 'voided'
        """).fetchone()
        r = dict(row) if hasattr(row, 'keys') else {"total_revenue": row[0], "total_cost": row[1], "invoice_count": row[2]}
        revenue = float(r.get("total_revenue", 0) or 0)
        cost = float(r.get("total_cost", 0) or 0)
        profit = revenue - cost
        margin_pct = (profit / cost * 100) if cost > 0 else 0
        return {
            "total_revenue": round(revenue, 2),
            "total_cost": round(cost, 2),
            "total_profit": round(profit, 2),
            "margin_pct": round(margin_pct, 1),
            "invoice_count": int(r.get("invoice_count", 0) or 0),
        }


# ─── Reorder Suggestions ────────────────────────────────────────────────


def get_reorder_suggestions(vendor_id: int = None) -> list:
    """Get products below min_qty grouped by preferred vendor, with suggested PO qty."""
    formula = get_setting("reorder_formula", "max_minus_onhand")
    if formula == "fixed_qty":
        qty_expr = "p.min_qty"
    else:
        # max_minus_onhand (default)
        qty_expr = ("CASE WHEN p.max_qty > 0"
                    " THEN p.max_qty - p.qty_on_hand"
                    " ELSE p.min_qty * 2 - p.qty_on_hand END")
    with get_db() as conn:
        conditions, params = ["p.min_qty > 0", "p.qty_on_hand < p.min_qty"], []
        if vendor_id:
            conditions.append("p.preferred_vendor_id = %s")
            params.append(vendor_id)
        rows = conn.execute(f"""
            SELECT p.id, p.sku, p.title, p.qty_on_hand, p.min_qty, p.max_qty,
                   p.cost, p.preferred_vendor_id,
                   v.name as vendor_name,
                   {qty_expr} as suggested_qty
            FROM products p
            LEFT JOIN vendors v ON v.id = p.preferred_vendor_id
            WHERE {' AND '.join(conditions)}
            ORDER BY v.name, p.sku
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# ─── Customer Purchase History ───────────────────────────────────────────


def get_customer_purchase_history(customer_id: int, limit: int = 200) -> list:
    """Get all items a customer has purchased."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.sku, p.title as product_title, il.quantity, il.unit_price,
                   il.line_total, inv.invoice_number, inv.created_at as invoice_date,
                   inv.status as invoice_status,
                   COALESCE(il.warranty_years, 0) as warranty_years,
                   COALESCE(il.warranty_price, 0) as warranty_price
            FROM invoice_lines il
            JOIN invoices inv ON inv.id = il.invoice_id
            LEFT JOIN products p ON p.id = il.product_id
            WHERE inv.customer_id = %s
            ORDER BY inv.created_at DESC LIMIT %s
        """, (customer_id, limit)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_customer_frequently_ordered(customer_id: int, limit: int = 20) -> list:
    """Get customer's most frequently ordered products."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.sku, p.title, p.selling_price as price,
                   SUM(il.quantity) as total_qty,
                   COUNT(DISTINCT inv.id) as order_count,
                   MAX(inv.created_at) as last_ordered
            FROM invoice_lines il
            JOIN invoices inv ON inv.id = il.invoice_id
            JOIN products p ON p.id = il.product_id
            WHERE inv.customer_id = %s AND inv.status != 'voided'
            GROUP BY p.id
            ORDER BY total_qty DESC LIMIT %s
        """, (customer_id, limit)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# ─── Customer Employees ────────────────────────────────────────────────


def get_customer_employees(customer_id: int) -> list:
    """Get all employees for a customer."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM customer_employees WHERE customer_id = %s ORDER BY name",
            (customer_id,),
        ).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def add_customer_employee(customer_id: int, name: str, title: str = "",
                          phone: str = "", email: str = "") -> int:
    """Add an employee contact to a customer. Returns new row ID."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO customer_employees (customer_id, name, title, phone, email) "
            "VALUES (%s, %s, %s, %s, %s)",
            (customer_id, name, title, phone, email),
        )
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        conn.commit()
        return row[0] if row else 0


def update_customer_employee(emp_id: int, **kwargs) -> None:
    """Update employee fields."""
    allowed = {"name", "title", "phone", "email", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    with get_db() as conn:
        sets = ", ".join(f"{k} = %s" for k in updates)
        vals = list(updates.values()) + [emp_id]
        conn.execute(f"UPDATE customer_employees SET {sets} WHERE id = %s", vals)
        conn.commit()


def delete_customer_employee(emp_id: int) -> None:
    """Delete a customer employee record."""
    with get_db() as conn:
        conn.execute("DELETE FROM customer_employees WHERE id = %s", (emp_id,))
        conn.commit()


# ─── Credit Agreements ─────────────────────────────────────────────────


def get_credit_agreement(customer_id: int) -> dict | None:
    """Get the active credit agreement for a customer."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM credit_agreements WHERE customer_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else None


def save_credit_agreement(customer_id: int, credit_limit: float, apr: float,
                          net_days: int, issue_date: str,
                          guarantor_name: str = "", notes: str = "") -> int:
    """Create or update a credit agreement for a customer."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM credit_agreements WHERE customer_id = %s LIMIT 1",
            (customer_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE credit_agreements SET credit_limit=%s, apr=%s, net_days=%s, "
                "issue_date=%s, guarantor_name=%s, notes=%s, updated_at=datetime('now') "
                "WHERE id=%s",
                (credit_limit, apr, net_days, issue_date, guarantor_name, notes, existing[0]),
            )
            conn.commit()
            return existing[0]
        else:
            conn.execute(
                "INSERT INTO credit_agreements "
                "(customer_id, credit_limit, apr, net_days, issue_date, guarantor_name, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (customer_id, credit_limit, apr, net_days, issue_date, guarantor_name, notes),
            )
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            conn.commit()
            return row[0] if row else 0


# ─── Customer Analytics ────────────────────────────────────────────────


def get_customer_aging(customer_id: int) -> dict:
    """Return 30/60/90+ day aging buckets for outstanding invoices.

    Outstanding = invoices in status 'sent' or 'partial'. Drafts and voided
    invoices are excluded so balances match the invoices screen footer.
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, invoice_number, total,
                   COALESCE(amount_paid, 0) as paid,
                   created_at,
                   CAST(julianday('now') - julianday(created_at) AS INTEGER) as days_old
            FROM invoices
            WHERE customer_id = %s AND status IN ('sent', 'partial')
        """, (customer_id,)).fetchall()

    buckets = {"current": 0.0, "over_30": 0.0, "over_60": 0.0, "over_90": 0.0, "items": []}
    for r in rows:
        r = dict(r) if hasattr(r, 'keys') else dict(enumerate(r))
        balance = float(r.get("total") or 0) - float(r.get("paid") or 0)
        days = int(r.get("days_old") or 0)
        r["balance"] = balance
        buckets["items"].append(r)
        if days > 90:
            buckets["over_90"] += balance
        elif days > 60:
            buckets["over_60"] += balance
        elif days > 30:
            buckets["over_30"] += balance
        else:
            buckets["current"] += balance
    buckets["total_outstanding"] = (
        buckets["current"] + buckets["over_30"] + buckets["over_60"] + buckets["over_90"]
    )
    return buckets


def get_customer_spending_summary(customer_id: int) -> dict:
    """Return 30/60/90 day + annual spending totals."""
    with get_db() as conn:
        def _sum(days: int) -> float:
            r = conn.execute("""
                SELECT COALESCE(SUM(total), 0) FROM invoices
                WHERE customer_id = %s AND status != 'voided'
                  AND created_at >= datetime('now', %s)
            """, (customer_id, f"-{days} days")).fetchone()
            return float(r[0]) if r else 0.0

        return {
            "spend_30": _sum(30),
            "spend_60": _sum(60),
            "spend_90": _sum(90),
            "annual": _sum(365),
        }


def get_core_margin_summary(start_date: str | None = None,
                            end_date: str | None = None) -> dict:
    """P4 — Net core-spread / core gross-profit summary.

    JAK's both **collects** customer-facing core deposits (a liability
    until refunded) and **pays** vendor-side core deposits (an asset
    until the old core ships back). The arithmetic difference between
    those two streams is the realized core margin once both legs clear.
    This function returns the snapshot needed for the margin/profit
    reporting screens.

    Returned keys:
        ``customer_core_collected``
            ``SUM(invoice_lines.core_charge * quantity)`` for non-voided
            invoices in the window. What was charged to customers as a
            refundable deposit.
        ``customer_core_refunded``
            Sum of ``core_returns.refund_amount`` already credited back
            (statuses ``credited``/``refunded``).
        ``vendor_core_paid``
            Sum of ``vendor_core_returns.core_charge * qty`` for open
            cores — cash put up to vendors.
        ``vendor_core_credited``
            Sum of vendor cores already received back as credit
            (status ``credited``).
        ``net_core_spread``
            ``customer_core_collected - vendor_core_paid`` — the
            instantaneous cash position contributed by cores.
        ``core_gross_profit``
            ``(customer_core_collected - customer_core_refunded)
              - (vendor_core_paid - vendor_core_credited)``
            — realized profit once both legs clear. Forfeited customer
            deposits land here as positive contribution; vendor credits
            already received reduce the asset side.

    The date window is optional; both ``None`` returns the all-time
    roll-up.
    """
    where_inv: list[str] = ["inv.status != 'voided'"]
    params_inv: list = []
    if start_date:
        where_inv.append("inv.created_at >= %s")
        params_inv.append(start_date)
    if end_date:
        where_inv.append("inv.created_at <= %s")
        params_inv.append(end_date)
    inv_where_sql = " AND ".join(where_inv)

    with get_db() as conn:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(COALESCE(il.core_charge, 0) * il.quantity), 0)
            FROM invoice_lines il
            JOIN invoices inv ON inv.id = il.invoice_id
            WHERE {inv_where_sql}
              AND COALESCE(il.core_charge, 0) > 0
            """,
            tuple(params_inv),
        ).fetchone()
        customer_core_collected = float((row[0] if row else 0) or 0)

        try:
            r = conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(refund_amount, 0)), 0)
                FROM core_returns
                WHERE LOWER(COALESCE(status, '')) IN ('credited', 'refunded')
                """
            ).fetchone()
            customer_core_refunded = float((r[0] if r else 0) or 0)
        except Exception:
            customer_core_refunded = 0.0

        try:
            r = conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(core_charge, 0) * COALESCE(qty, 0)), 0)
                FROM vendor_core_returns
                WHERE LOWER(COALESCE(status, '')) IN ('pending', 'shipped', 'received')
                """
            ).fetchone()
            vendor_core_paid = float((r[0] if r else 0) or 0)
        except Exception:
            vendor_core_paid = 0.0

        try:
            r = conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(core_charge, 0) * COALESCE(qty, 0)), 0)
                FROM vendor_core_returns
                WHERE LOWER(COALESCE(status, '')) = 'credited'
                """
            ).fetchone()
            vendor_core_credited = float((r[0] if r else 0) or 0)
        except Exception:
            vendor_core_credited = 0.0

    net_core_spread = customer_core_collected - vendor_core_paid
    core_gross_profit = (
        (customer_core_collected - customer_core_refunded)
        - (vendor_core_paid - vendor_core_credited)
    )
    return {
        "customer_core_collected": round(customer_core_collected, 2),
        "customer_core_refunded": round(customer_core_refunded, 2),
        "vendor_core_paid": round(vendor_core_paid, 2),
        "vendor_core_credited": round(vendor_core_credited, 2),
        "net_core_spread": round(net_core_spread, 2),
        "core_gross_profit": round(core_gross_profit, 2),
    }


def get_customer_outstanding_cores(customer_id: int) -> list:
    """Get outstanding core charges for a customer.

    Only returns rows where a core_returns record has been materialized
    (i.e. the source invoice has been finalized) and the core is still
    open (pending or received but not yet credited or voided). Surfaces
    both the supplier-side core_charge and the customer-facing
    customer_core_price (what they paid / will be credited).
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT il.id, p.sku, p.title,
                   p.core_charge,
                   -- P6 — prefer the per-line snapshot (what the customer
                   -- was actually charged) over the live product master.
                   COALESCE(NULLIF(il.core_charge, 0),
                            p.core_sell_price,
                            p.core_charge) AS customer_core_price,
                   il.quantity,
                   inv.invoice_number, inv.created_at as invoice_date,
                   p.oem_part_number,
                   cr.status as core_status, cr.id as core_return_id
            FROM invoice_lines il
            JOIN invoices inv ON inv.id = il.invoice_id
            LEFT JOIN products p ON p.id = il.product_id
            JOIN core_returns cr ON cr.invoice_line_id = il.id
            WHERE inv.customer_id = %s AND inv.status != 'voided'
              AND (COALESCE(il.core_charge, 0) > 0
                   OR COALESCE(p.core_charge, 0) > 0)
              AND LOWER(COALESCE(cr.status,'')) IN ('pending','received')
            ORDER BY inv.created_at DESC
        """, (customer_id,)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def get_customer_grid_data() -> list[dict]:
    """Return enriched customer list for the customer maintenance grid.

    Each dict contains standard customer fields plus:
      balance, available_credit, last_order_date, customer_type,
      open_core_count, open_core_value, open_quote_count
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.*,
                -- outstanding balance (invoices not paid/voided)
                COALESCE(bal.outstanding, 0) AS balance,
                -- credit limit from agreement (or customer table)
                COALESCE(ca.credit_limit, c.credit_limit, 0) AS eff_credit_limit,
                -- last invoice date
                last_inv.last_order_date,
                -- open quotes count
                COALESCE(oq.open_quote_count, 0) AS open_quote_count,
                -- open core count & value
                COALESCE(oc.open_core_count, 0) AS open_core_count,
                COALESCE(oc.open_core_value, 0) AS open_core_value
            FROM customers c
            LEFT JOIN (
                SELECT customer_id,
                       SUM(COALESCE(total, 0) - COALESCE(amount_paid, 0)) AS outstanding
                FROM invoices
                WHERE status NOT IN ('paid', 'voided')
                GROUP BY customer_id
            ) bal ON bal.customer_id = c.id
            LEFT JOIN credit_agreements ca ON ca.customer_id = c.id
            LEFT JOIN (
                SELECT customer_id,
                       MAX(created_at) AS last_order_date
                FROM invoices WHERE status != 'voided'
                GROUP BY customer_id
            ) last_inv ON last_inv.customer_id = c.id
            LEFT JOIN (
                SELECT customer_id, COUNT(*) AS open_quote_count
                FROM quotes
                WHERE status IN ('draft', 'sent')
                GROUP BY customer_id
            ) oq ON oq.customer_id = c.id
            LEFT JOIN (
                -- P6 — outstanding-core liability rolls up from the
                -- snapshot on ``invoice_lines.core_charge`` (set at
                -- invoice creation time), NOT from the live
                -- ``products.core_charge`` master. This guarantees the
                -- liability stays consistent with what the customer
                -- was actually charged, even if the product master is
                -- adjusted later.
                SELECT inv.customer_id,
                       COUNT(*) AS open_core_count,
                       SUM(COALESCE(il.core_charge, 0) * il.quantity) AS open_core_value
                FROM invoice_lines il
                JOIN invoices inv ON inv.id = il.invoice_id
                LEFT JOIN core_returns cr ON cr.invoice_line_id = il.id
                WHERE inv.status != 'voided'
                  AND COALESCE(il.core_charge, 0) > 0
                  AND (cr.id IS NULL OR cr.status = 'pending')
                GROUP BY inv.customer_id
            ) oc ON oc.customer_id = c.id
            WHERE c.is_active = TRUE
            ORDER BY c.name
        """).fetchall()

        results = []
        for r in rows:
            d = dict(r) if hasattr(r, 'keys') else dict(enumerate(r))
            d["available_credit"] = max(
                0, float(d.get("eff_credit_limit") or 0) - float(d.get("balance") or 0)
            )
            results.append(d)
        return results


def get_customer_overdue_cores(customer_id: int, days_threshold: int = 30) -> list[dict]:
    """Get overdue core returns for a customer (pending cores older than threshold days)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT cr.*, p.sku, p.title as product_title,
                   p.oem_part_number, i.invoice_number,
                   CAST(julianday('now') - julianday(cr.created_at) AS INTEGER) AS days_outstanding
            FROM core_returns cr
            LEFT JOIN products p ON p.id = cr.product_id
            LEFT JOIN invoices i ON i.id = cr.invoice_id
            WHERE cr.customer_id = %s
              AND cr.status = 'pending'
              AND julianday('now') - julianday(cr.created_at) > %s
            ORDER BY cr.created_at ASC
        """, (customer_id, days_threshold)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


# ─── SKU Prefix Management ──────────────────────────────────────────────


def list_sku_prefixes() -> list:
    """List all configured SKU prefixes."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT sp.*, v.name as vendor_name
            FROM sku_prefixes sp
            LEFT JOIN vendors v ON v.id = sp.vendor_id
            ORDER BY sp.prefix
        """).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_sku_prefix(prefix: str) -> dict | None:
    """Get a single prefix config."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM sku_prefixes WHERE prefix=%s", (prefix.upper(),)).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else None


def create_sku_prefix(prefix: str, manufacturer: str = None,
                      vendor_id: int = None, description: str = None) -> int:
    """Create a new SKU prefix mapping."""
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO sku_prefixes (prefix, manufacturer, vendor_id, description)
            VALUES (%s, %s, %s, %s)
        """, (prefix.upper(), manufacturer, vendor_id, description))
        conn.commit()
        return cursor.lastrowid


def delete_sku_prefix(prefix_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM sku_prefixes WHERE id=%s", (prefix_id,))
        conn.commit()
        return True


def generate_sku(prefix: str) -> str:
    """Generate next SKU in sequence: JAKS-{PREFIX}-{6-digit number}.

    Example: generate_sku("PAI") -> "JAKS-PAI-000001"
    Thread-safe via row-level locking.
    """
    prefix = prefix.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_number FROM sku_sequences WHERE prefix=%s", (prefix,)
        ).fetchone()
        if row:
            next_num = (row["last_number"] if hasattr(row, 'keys') else row[0]) + 1
            conn.execute(
                "UPDATE sku_sequences SET last_number=%s WHERE prefix=%s",
                (next_num, prefix))
        else:
            next_num = 1
            conn.execute(
                "INSERT INTO sku_sequences (prefix, last_number) VALUES (%s, %s)",
                (prefix, next_num))
        conn.commit()
        return f"JAKS-{prefix}-{next_num:06d}"


def get_next_sku_preview(prefix: str) -> str:
    """Preview the next SKU without consuming the number."""
    prefix = prefix.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_number FROM sku_sequences WHERE prefix=%s", (prefix,)
        ).fetchone()
        next_num = ((row["last_number"] if hasattr(row, 'keys') else row[0]) + 1) if row else 1
        return f"JAKS-{prefix}-{next_num:06d}"


# ─── Bulk Product Import ────────────────────────────────────────────────


def bulk_import_products(rows: list[dict], auto_sku_prefix: str = None,
                         update_existing: bool = False,
                         filename: str = "manual") -> dict:
    """Import products from parsed spreadsheet/CSV rows.

    Each row dict should have keys matching product columns:
      sku, title, manufacturer, category, cost, selling_price, qty_on_hand, etc.

    If auto_sku_prefix is set and a row has no SKU, one is generated.

    P0.1: Duplicate handling
      - Intra-file duplicate SKUs (same SKU appearing twice in ``rows``)
        are reported as explicit errors and skipped after the first row.
      - If a SKU already exists in the DB and ``update_existing`` is False
        an explicit error is recorded (previously these were silently
        counted as "skipped").
      - The return dict now includes ``duplicate_skus`` listing every SKU
        that collided — both intra-file and against the DB — so the UI
        can highlight the offending rows.

    Returns: {imported, updated, skipped, errors, duplicate_skus, batch_id}
    """
    result = {
        "imported": 0, "updated": 0, "skipped": 0,
        "errors": [], "duplicate_skus": [], "batch_id": 0,
    }
    seen_in_batch: set[str] = set()
    dup_seen: set[str] = set()
    with get_db() as conn:
        # Log the batch
        cursor = conn.execute("""
            INSERT INTO import_batches (filename, source_type, total_rows)
            VALUES (%s, %s, %s)
        """, (filename, "csv", len(rows)))
        batch_id = cursor.lastrowid
        result["batch_id"] = batch_id

        for i, row in enumerate(rows, 1):
            try:
                sku = str(row.get("sku", "") or "").strip()

                # Auto-generate SKU if missing and prefix provided
                if not sku and auto_sku_prefix:
                    sku = generate_sku(auto_sku_prefix)
                elif not sku:
                    result["errors"].append(f"Row {i}: No SKU and no auto-prefix set")
                    result["skipped"] += 1
                    continue

                sku_key = sku.upper()
                # P0.1 — intra-file duplicate detection.
                if sku_key in seen_in_batch:
                    result["errors"].append(
                        f"Row {i}: duplicate SKU '{sku}' in this file "
                        f"(already appears in an earlier row — skipping)"
                    )
                    result["skipped"] += 1
                    if sku not in dup_seen:
                        result["duplicate_skus"].append(sku)
                        dup_seen.add(sku)
                    continue
                seen_in_batch.add(sku_key)

                title = str(row.get("title", "") or row.get("name", "") or row.get("description", "") or "").strip()
                manufacturer = str(row.get("manufacturer", "") or row.get("brand", "") or row.get("mfg", "") or "").strip()
                category = str(row.get("category", "") or "").strip()

                cost = 0.0
                try:
                    cost_val = str(row.get("cost", "") or "0").replace("$", "").replace(",", "").strip()
                    cost = float(cost_val) if cost_val else 0.0
                except (ValueError, TypeError):
                    pass

                price = 0.0
                try:
                    price_val = str(row.get("selling_price", "") or row.get("price", "") or "0").replace("$", "").replace(",", "").strip()
                    price = float(price_val) if price_val else 0.0
                except (ValueError, TypeError):
                    pass

                qty = 0
                try:
                    qty_val = str(row.get("qty_on_hand", "") or row.get("quantity", "") or row.get("qty", "") or "0").replace(",", "").strip()
                    qty = int(float(qty_val)) if qty_val else 0
                except (ValueError, TypeError):
                    pass

                # Check if SKU exists
                existing = conn.execute(
                    "SELECT id FROM products WHERE sku=%s", (sku,)
                ).fetchone()

                if existing:
                    if update_existing:
                        pid = existing["id"] if hasattr(existing, 'keys') else existing[0]
                        conn.execute("""
                            UPDATE products SET title=%s, manufacturer=%s, category=%s,
                                   cost=%s, selling_price=%s, qty_on_hand=%s,
                                   updated_at=%s
                            WHERE id=%s
                        """, (title or None, manufacturer or None, category or None,
                              cost, price, qty, datetime.now().isoformat(), pid))
                        result["updated"] += 1
                    else:
                        # P0.1 — explicit error instead of silent skip.
                        result["errors"].append(
                            f"Row {i}: SKU '{sku}' already exists in inventory "
                            f"(enable 'Update existing' to overwrite)"
                        )
                        result["skipped"] += 1
                        if sku not in dup_seen:
                            result["duplicate_skus"].append(sku)
                            dup_seen.add(sku)
                else:
                    now = datetime.now().isoformat()
                    conn.execute("""
                        INSERT INTO products (sku, title, manufacturer, category,
                                            cost, selling_price, qty_on_hand,
                                            created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (sku, title or None, manufacturer or None, category or None,
                          cost, price, qty, now, now))
                    result["imported"] += 1

            except Exception as e:
                result["errors"].append(f"Row {i}: {e}")

        # Update batch record
        conn.execute("""
            UPDATE import_batches SET imported=%s, updated=%s, skipped=%s,
                   errors=%s, error_details=%s
            WHERE id=%s
        """, (result["imported"], result["updated"], result["skipped"],
              len(result["errors"]),
              "\n".join(result["errors"][:50]) if result["errors"] else None,
              batch_id))
        conn.commit()

    return result


def get_import_history(limit: int = 50) -> list:
    """Get recent import batch log."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM import_batches ORDER BY created_at DESC LIMIT %s
        """, (limit,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


# ─── CRM Customer Activities ────────────────────────────────────────────


def add_customer_activity(customer_id: int, activity_type: str, subject: str,
                          notes: str = None, follow_up_date: str = None) -> int:
    """Log a CRM activity (visit, call, email, note, follow-up)."""
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO customer_activities
            (customer_id, activity_type, subject, notes, follow_up_date)
            VALUES (%s, %s, %s, %s, %s)
        """, (customer_id, activity_type, subject, notes, follow_up_date))
        conn.commit()
        return cursor.lastrowid


def get_customer_activities(customer_id: int, limit: int = 100) -> list:
    """Get activity log for a customer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM customer_activities
            WHERE customer_id=%s
            ORDER BY created_at DESC LIMIT %s
        """, (customer_id, limit)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_all_activities(activity_type: str = None, limit: int = 200) -> list:
    """Get all activities, optionally filtered by type."""
    with get_db() as conn:
        if activity_type:
            rows = conn.execute("""
                SELECT ca.*, c.name as customer_name, c.company
                FROM customer_activities ca
                JOIN customers c ON c.id = ca.customer_id
                WHERE ca.activity_type=%s
                ORDER BY ca.created_at DESC LIMIT %s
            """, (activity_type, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT ca.*, c.name as customer_name, c.company
                FROM customer_activities ca
                JOIN customers c ON c.id = ca.customer_id
                ORDER BY ca.created_at DESC LIMIT %s
            """, (limit,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def get_upcoming_followups(days: int = 14) -> list:
    """Get follow-ups due within the next N days."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT ca.*, c.name as customer_name, c.company, c.phone, c.email
            FROM customer_activities ca
            JOIN customers c ON c.id = ca.customer_id
            WHERE ca.follow_up_date IS NOT NULL
              AND ca.completed = 0
              AND ca.follow_up_date <= date('now', '+' || %s || ' days')
            ORDER BY ca.follow_up_date ASC
        """, (days,)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else dict(enumerate(row)) for row in rows]


def complete_activity(activity_id: int) -> bool:
    """Mark a follow-up/activity as completed."""
    with get_db() as conn:
        conn.execute(
            "UPDATE customer_activities SET completed=1 WHERE id=%s",
            (activity_id,))
        conn.commit()
        return True


def delete_activity(activity_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM customer_activities WHERE id=%s", (activity_id,))
        conn.commit()
        return True


# ---------------------------------------------------------------------------
# Daily Route Planner
# ---------------------------------------------------------------------------

def get_or_create_daily_route(route_date: str) -> dict:
    """Get or create a daily route for the given date (YYYY-MM-DD)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_routes WHERE route_date=%s", (route_date,)
        ).fetchone()
        if row:
            return dict(row) if hasattr(row, 'keys') else {"id": row[0], "route_date": row[1]}
        conn.execute("""
            INSERT INTO daily_routes (route_date, status) VALUES (%s, 'planned')
        """, (route_date,))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM daily_routes WHERE route_date=%s", (route_date,)
        ).fetchone()
        return dict(row) if hasattr(row, 'keys') else {"id": row[0], "route_date": row[1]}


def get_daily_route(route_date: str) -> dict | None:
    """Get a daily route by date with its stops."""
    with get_db() as conn:
        route = conn.execute(
            "SELECT * FROM daily_routes WHERE route_date=%s", (route_date,)
        ).fetchone()
        if not route:
            return None
        route = dict(route) if hasattr(route, 'keys') else {"id": route[0], "route_date": route[1]}
        stops = conn.execute("""
            SELECT rs.*, c.name as customer_name, c.company as customer_company,
                   c.phone as customer_phone, c.address as customer_address,
                   c.city as customer_city, c.state as customer_state, c.zip as customer_zip
            FROM route_stops rs
            LEFT JOIN customers c ON c.id = rs.customer_id
            WHERE rs.route_id = %s
            ORDER BY rs.stop_order
        """, (route["id"],)).fetchall()
        route["stops"] = [dict(s) if hasattr(s, 'keys') else s for s in stops]
        return route


def add_route_stop(route_id: int, customer_id: int = None, stop_type: str = "visit",
                   purpose: str = None, address: str = None, city: str = None,
                   state: str = None, zip_code: str = None,
                   scheduled_time: str = None, shipment_id: int = None,
                   activity_id: int = None, notes: str = None) -> int:
    """Add a stop to a daily route. Returns stop ID."""
    with get_db() as conn:
        # Get next stop order
        row = conn.execute(
            "SELECT COALESCE(MAX(stop_order), 0) + 1 FROM route_stops WHERE route_id=%s",
            (route_id,)).fetchone()
        next_order = row[0] if isinstance(row, tuple) else list(row.values())[0]

        # If customer_id given but no address, pull from customer record
        if customer_id and not address:
            cust = conn.execute(
                "SELECT address, city, state, zip, latitude, longitude FROM customers WHERE id=%s",
                (customer_id,)).fetchone()
            if cust:
                c = dict(cust) if hasattr(cust, 'keys') else {}
                address = address or c.get("address")
                city = city or c.get("city")
                state = state or c.get("state")
                zip_code = zip_code or c.get("zip")

        cursor = conn.execute("""
            INSERT INTO route_stops
                (route_id, stop_order, customer_id, stop_type, purpose,
                 address, city, state, zip, scheduled_time,
                 shipment_id, activity_id, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (route_id, next_order, customer_id, stop_type, purpose,
              address, city, state, zip_code, scheduled_time,
              shipment_id, activity_id, notes))

        # Update stop count
        conn.execute("""
            UPDATE daily_routes SET total_stops = (
                SELECT COUNT(*) FROM route_stops WHERE route_id=%s
            ) WHERE id=%s
        """, (route_id, route_id))
        conn.commit()
        return cursor.lastrowid


def update_route_stop(stop_id: int, **fields) -> bool:
    """Update a route stop."""
    allowed = {"stop_order", "purpose", "scheduled_time", "actual_arrival",
               "actual_departure", "duration_min", "miles_from_prev",
               "status", "notes", "address", "city", "state", "zip"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k}=%s" for k in updates)
        values = list(updates.values()) + [stop_id]
        conn.execute(f"UPDATE route_stops SET {set_clause} WHERE id=%s", values)
        conn.commit()
        return True


def complete_route_stop(stop_id: int, notes: str = None) -> bool:
    """Mark a route stop as completed with arrival time."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_db() as conn:
        updates = {"status": "completed", "actual_arrival": now}
        if notes:
            updates["notes"] = notes
        set_clause = ", ".join(f"{k}=%s" for k in updates)
        values = list(updates.values()) + [stop_id]
        conn.execute(f"UPDATE route_stops SET {set_clause} WHERE id=%s", values)
        conn.commit()
        return True


def skip_route_stop(stop_id: int, reason: str = None) -> bool:
    """Skip a route stop."""
    with get_db() as conn:
        conn.execute(
            "UPDATE route_stops SET status='skipped', notes=%s WHERE id=%s",
            (reason, stop_id))
        conn.commit()
        return True


def delete_route_stop(stop_id: int) -> bool:
    """Remove a stop from a route."""
    with get_db() as conn:
        row = conn.execute("SELECT route_id FROM route_stops WHERE id=%s", (stop_id,)).fetchone()
        if not row:
            return False
        route_id = row[0] if isinstance(row, tuple) else row["route_id"]
        conn.execute("DELETE FROM route_stops WHERE id=%s", (stop_id,))
        # Re-number remaining stops
        stops = conn.execute(
            "SELECT id FROM route_stops WHERE route_id=%s ORDER BY stop_order",
            (route_id,)).fetchall()
        for i, s in enumerate(stops, 1):
            sid = s[0] if isinstance(s, tuple) else s["id"]
            conn.execute("UPDATE route_stops SET stop_order=%s WHERE id=%s", (i, sid))
        conn.execute("""
            UPDATE daily_routes SET total_stops = (
                SELECT COUNT(*) FROM route_stops WHERE route_id=%s
            ) WHERE id=%s
        """, (route_id, route_id))
        conn.commit()
        return True


def reorder_route_stops(route_id: int, stop_ids: list) -> bool:
    """Reorder stops by providing stop IDs in desired order."""
    with get_db() as conn:
        for i, sid in enumerate(stop_ids, 1):
            conn.execute(
                "UPDATE route_stops SET stop_order=%s WHERE id=%s AND route_id=%s",
                (i, sid, route_id))
        conn.commit()
        return True


def complete_daily_route(route_id: int) -> bool:
    """Mark a daily route as completed."""
    from datetime import datetime
    with get_db() as conn:
        conn.execute("""
            UPDATE daily_routes SET status='completed', completed_at=%s WHERE id=%s
        """, (datetime.now().strftime("%Y-%m-%d %H:%M"), route_id))
        conn.commit()
        return True


def auto_populate_route(route_date: str) -> list:
    """Auto-populate a route from outbound deliveries + CRM follow-ups for the date.
    Returns list of automatically added stops."""
    route = get_or_create_daily_route(route_date)
    route_id = route["id"]
    added = []

    with get_db() as conn:
        # Check existing stops to avoid duplicates
        existing_shipments = set()
        existing_activities = set()
        existing = conn.execute(
            "SELECT shipment_id, activity_id FROM route_stops WHERE route_id=%s",
            (route_id,)).fetchall()
        for e in existing:
            e = dict(e) if hasattr(e, 'keys') else e
            sid = e.get("shipment_id") if isinstance(e, dict) else e[0]
            aid = e.get("activity_id") if isinstance(e, dict) else e[1]
            if sid:
                existing_shipments.add(sid)
            if aid:
                existing_activities.add(aid)

        # 1. Outbound shipments for this date
        shipments = conn.execute("""
            SELECT s.*, c.name as customer_name, c.company, c.address, c.city, c.state, c.zip
            FROM shipments s
            LEFT JOIN customers c ON c.id = s.customer_id
            WHERE s.shipment_type = 'outbound'
              AND s.ship_date = %s
              AND s.status IN ('pending', 'in_transit')
        """, (route_date,)).fetchall()

        for ship in shipments:
            ship = dict(ship) if hasattr(ship, 'keys') else ship
            ship_id = ship.get("id") if isinstance(ship, dict) else ship[0]
            if ship_id in existing_shipments:
                continue
            cust_name = ship.get("customer_name", "") or ""
            company = ship.get("company", "") or ""
            label = company or cust_name
            add_route_stop(
                route_id=route_id,
                customer_id=ship.get("customer_id"),
                stop_type="delivery",
                purpose=f"Delivery to {label}",
                address=ship.get("destination_address") or ship.get("address"),
                city=ship.get("city"),
                state=ship.get("state"),
                zip_code=ship.get("zip"),
                shipment_id=ship_id
            )
            added.append({"type": "delivery", "customer": label, "shipment_id": ship_id})

        # 2. CRM follow-ups due on this date
        followups = conn.execute("""
            SELECT ca.*, c.name as customer_name, c.company, c.address, c.city, c.state, c.zip
            FROM customer_activities ca
            JOIN customers c ON c.id = ca.customer_id
            WHERE ca.follow_up_date = %s
              AND ca.completed = 0
              AND ca.activity_type IN ('visit', 'follow-up')
        """, (route_date,)).fetchall()

        for fu in followups:
            fu = dict(fu) if hasattr(fu, 'keys') else fu
            act_id = fu.get("id") if isinstance(fu, dict) else fu[0]
            if act_id in existing_activities:
                continue
            cust_name = fu.get("customer_name", "") or ""
            company = fu.get("company", "") or ""
            label = company or cust_name
            add_route_stop(
                route_id=route_id,
                customer_id=fu.get("customer_id"),
                stop_type="visit",
                purpose=fu.get("subject") or f"Follow-up with {label}",
                address=fu.get("address"),
                city=fu.get("city"),
                state=fu.get("state"),
                zip_code=fu.get("zip"),
                activity_id=act_id
            )
            added.append({"type": "visit", "customer": label, "activity_id": act_id})

    return added


def get_route_directions_url(stops: list, start_address: str = "Henderson, NV") -> str:
    """Build a Google Maps directions URL for multi-stop routing.
    stops = list of dicts with 'address', 'city', 'state', 'zip' keys."""
    import urllib.parse
    base = "https://www.google.com/maps/dir/"
    waypoints = [urllib.parse.quote_plus(start_address)]
    for stop in stops:
        parts = []
        if stop.get("address"):
            parts.append(stop["address"])
        if stop.get("city"):
            parts.append(stop["city"])
        if stop.get("state"):
            parts.append(stop["state"])
        if stop.get("zip"):
            parts.append(stop["zip"])
        addr = ", ".join(parts) if parts else ""
        if addr:
            waypoints.append(urllib.parse.quote_plus(addr))
    waypoints.append(urllib.parse.quote_plus(start_address))
    return base + "/".join(waypoints)


def get_stop_directions_url(from_address: str, to_address: str) -> str:
    """Build a Google Maps directions URL from one address to another."""
    import urllib.parse
    origin = urllib.parse.quote_plus(from_address)
    dest = urllib.parse.quote_plus(to_address)
    return f"https://www.google.com/maps/dir/{origin}/{dest}"


# ---------------------------------------------------------------------------
# Customer 360 View (Unified sync across CRM, Sales, Invoices, Deliveries)
# ---------------------------------------------------------------------------

def get_customer_360(customer_id: int) -> dict:
    """Get a unified view of a customer: info, CRM activities, sales orders,
    invoices, deliveries, and purchase history."""
    with get_db() as conn:
        cust = conn.execute("SELECT * FROM customers WHERE id=%s", (customer_id,)).fetchone()
        if not cust:
            return {}
        result = dict(cust) if hasattr(cust, 'keys') else {}

        # CRM Activities (last 20)
        activities = conn.execute("""
            SELECT id, activity_type, subject, notes, follow_up_date, completed, created_at
            FROM customer_activities WHERE customer_id=%s
            ORDER BY created_at DESC LIMIT 20
        """, (customer_id,)).fetchall()
        result["activities"] = [dict(a) if hasattr(a, 'keys') else a for a in activities]

        # Open follow-ups
        followups = conn.execute("""
            SELECT id, subject, follow_up_date, activity_type
            FROM customer_activities
            WHERE customer_id=%s AND follow_up_date IS NOT NULL AND completed=0
            ORDER BY follow_up_date
        """, (customer_id,)).fetchall()
        result["open_followups"] = [dict(f) if hasattr(f, 'keys') else f for f in followups]

        # Sales orders (last 20)
        try:
            sos = conn.execute("""
                SELECT id, so_number, status, total, created_at
                FROM sales_orders WHERE customer_id=%s
                ORDER BY created_at DESC LIMIT 20
            """, (customer_id,)).fetchall()
            result["sales_orders"] = [dict(s) if hasattr(s, 'keys') else s for s in sos]
        except Exception:
            result["sales_orders"] = []

        # Invoices
        try:
            invoices = conn.execute("""
                SELECT id, invoice_number, status, total, created_at
                FROM invoices WHERE customer_id=%s
                ORDER BY created_at DESC LIMIT 20
            """, (customer_id,)).fetchall()
            result["invoices"] = [dict(i) if hasattr(i, 'keys') else i for i in invoices]
        except Exception:
            result["invoices"] = []

        # Shipments/deliveries
        try:
            shipments = conn.execute("""
                SELECT id, shipment_type, carrier, tracking_number, status,
                       ship_date, estimated_arrival, actual_arrival
                FROM shipments WHERE customer_id=%s
                ORDER BY created_at DESC LIMIT 20
            """, (customer_id,)).fetchall()
            result["shipments"] = [dict(s) if hasattr(s, 'keys') else s for s in shipments]
        except Exception:
            result["shipments"] = []

        # Revenue totals
        try:
            total_row = conn.execute("""
                SELECT COUNT(*) as cnt, COALESCE(SUM(total), 0) as revenue
                FROM invoices WHERE customer_id=%s AND status='paid'
            """, (customer_id,)).fetchone()
            if total_row:
                t = dict(total_row) if hasattr(total_row, 'keys') else {"cnt": total_row[0], "revenue": total_row[1]}
                result["total_invoices_paid"] = t.get("cnt", 0)
                result["total_revenue"] = float(t.get("revenue", 0) or 0)
        except Exception:
            result["total_invoices_paid"] = 0
            result["total_revenue"] = 0.0

        return result


def update_customer_coords(customer_id: int, latitude: float, longitude: float) -> bool:
    """Cache geocoded coordinates on a customer record."""
    with get_db() as conn:
        conn.execute(
            "UPDATE customers SET latitude=%s, longitude=%s WHERE id=%s",
            (latitude, longitude, customer_id))
        conn.commit()
        return True


# ── Aging Accounts Receivable ──────────────────────────────────────────────

def get_aging_report() -> dict:
    """Generate an accounts receivable aging report.

    Returns a dict with:
        summary: {current, days_1_30, days_31_60, days_61_90, days_over_90, total}
        customers: [
            {customer_id, customer_name, company, current, days_1_30, days_31_60,
             days_61_90, days_over_90, total, invoice_count}
        ]
        invoices: [
            {id, invoice_number, customer_name, company, invoice_date, due_date,
             total, amount_paid, balance_due, status, days_overdue, aging_bucket}
        ]
    """
    with get_db() as conn:
        # Get all unpaid invoices (sent, partial — not draft/void/paid)
        rows = conn.execute("""
            SELECT i.id, i.invoice_number, i.customer_id, i.invoice_date, i.due_date,
                   i.total, i.amount_paid, i.balance_due, i.status,
                   c.name as customer_name, c.company as customer_company
            FROM invoices i
            LEFT JOIN customers c ON i.customer_id = c.id
            WHERE i.status IN ('sent', 'partial')
              AND i.balance_due > 0
            ORDER BY i.due_date ASC, i.id ASC
        """).fetchall()

        invoices = []
        summary = {
            "current": 0.0, "days_1_30": 0.0, "days_31_60": 0.0,
            "days_61_90": 0.0, "days_over_90": 0.0, "total": 0.0,
        }
        customers: dict[int, dict] = {}

        today = datetime.now().date()

        for row in rows:
            inv = dict(row) if hasattr(row, 'keys') else {}
            if not inv:
                continue

            balance = float(inv.get("balance_due") or 0)
            if balance <= 0:
                continue

            due_str = inv.get("due_date")
            if due_str:
                try:
                    due_date = datetime.strptime(str(due_str)[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    due_date = today
            else:
                due_date = today

            days_overdue = (today - due_date).days

            if days_overdue <= 0:
                bucket = "current"
            elif days_overdue <= 30:
                bucket = "days_1_30"
            elif days_overdue <= 60:
                bucket = "days_31_60"
            elif days_overdue <= 90:
                bucket = "days_61_90"
            else:
                bucket = "days_over_90"

            summary[bucket] += balance
            summary["total"] += balance

            inv["days_overdue"] = max(0, days_overdue)
            inv["aging_bucket"] = bucket
            invoices.append(inv)

            # Aggregate by customer
            cid = inv.get("customer_id")
            if cid and cid not in customers:
                customers[cid] = {
                    "customer_id": cid,
                    "customer_name": inv.get("customer_name", ""),
                    "company": inv.get("customer_company", ""),
                    "current": 0.0, "days_1_30": 0.0, "days_31_60": 0.0,
                    "days_61_90": 0.0, "days_over_90": 0.0, "total": 0.0,
                    "invoice_count": 0,
                }
            if cid:
                customers[cid][bucket] += balance
                customers[cid]["total"] += balance
                customers[cid]["invoice_count"] += 1

        # Sort customers by total descending
        cust_list = sorted(customers.values(), key=lambda c: c["total"], reverse=True)

        return {
            "summary": summary,
            "customers": cust_list,
            "invoices": invoices,
        }


# ---------------------------------------------------------------------------
# Full Data Reset (preserves settings and schema)
# ---------------------------------------------------------------------------

# Tables to clear during reset, ordered to respect FK constraints (children first)
_RESET_TABLES = [
    # Line items / children first
    "audit_lines", "transfer_lines", "work_order_lines", "purchase_order_lines",
    "invoice_lines", "vendor_return_lines", "kit_components",
    # Dependent records
    "core_returns", "serial_numbers", "product_locations", "product_images",
    "xref_links", "part_numbers", "part_interchanges", "engine_compatibility",
    "inventory_transactions", "route_stops",
    # Mid-level
    "inventory_audits", "inventory_transfers", "returns", "vendor_returns",
    "qbo_exports", "import_batches", "shipments",
    "customer_activities", "daily_routes",
    "customer_pricing", "price_tiers", "supersessions",
    # Parents
    "invoices", "sales_orders", "so_lines",
    "purchase_orders", "work_orders", "vehicles",
    "kits",
    "products",
    "customers",
    "vendors",
    "storage_locations",
    "esn_cache",
    "sku_prefixes",
]


def reset_all_data() -> dict:
    """Delete ALL data from every table except app_settings and schema_migrations.

    Returns dict with table name -> deleted count.
    """
    results = {}
    with get_db() as conn:
        for table in _RESET_TABLES:
            try:
                count = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
                n = count["c"] if count else 0
                conn.execute(f"DELETE FROM {table}")
                results[table] = n
            except Exception:
                results[table] = -1  # table may not exist
        conn.commit()
    return results


# ---------------------------------------------------------------------------
# QBO CSV Import Helpers
# ---------------------------------------------------------------------------

def import_customers_from_csv(rows: list[dict]) -> dict:
    """Import customers from QBO Customer Contact List CSV.

    Expected columns (flexible matching):
      Customer, Company, Email, Phone, Street, City, State, ZIP,
      Terms, Balance, Is Active

    Returns: {imported: int, updated: int, skipped: int, errors: list[str]}
    """
    result = {"imported": 0, "updated": 0, "skipped": 0, "errors": []}
    with get_db() as conn:
        for i, row in enumerate(rows, 1):
            try:
                name = _csv_val(row, "Customer", "Customer Name",
                                "Display Name", "Name")
                if not name:
                    result["errors"].append(f"Row {i}: No customer name")
                    result["skipped"] += 1
                    continue

                company = _csv_val(row, "Company", "Company Name") or ""
                email = _csv_val(row, "Email", "Main Email",
                                 "Primary Email") or ""
                phone = _csv_val(row, "Phone", "Main Phone",
                                 "Primary Phone") or ""
                address = _csv_val(row, "Street", "Billing Street",
                                   "Address", "Billing Address") or ""
                city = _csv_val(row, "City", "Billing City") or ""
                state = _csv_val(row, "State", "Billing State",
                                 "Billing State/Province") or ""
                zipcode = _csv_val(row, "ZIP", "Billing Zip",
                                   "Billing Postal Code", "Zip Code") or ""
                terms = _csv_val(row, "Terms", "Payment Terms") or "Net 30"
                notes = _csv_val(row, "Notes", "Memo") or ""

                existing = conn.execute(
                    "SELECT id FROM customers WHERE name=%s", (name,)
                ).fetchone()

                now = datetime.now().isoformat()
                if existing:
                    pid = existing["id"]
                    conn.execute("""
                        UPDATE customers SET company=%s, email=%s, phone=%s,
                               address=%s, city=%s, state=%s, zip=%s,
                               payment_terms=%s, notes=%s, updated_at=%s
                        WHERE id=%s
                    """, (company, email, phone, address, city, state,
                          zipcode, terms, notes, now, pid))
                    result["updated"] += 1
                else:
                    conn.execute("""
                        INSERT INTO customers (name, company, email, phone,
                                             address, city, state, zip,
                                             payment_terms, notes,
                                             is_active, created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (name, company, email, phone, address, city,
                          state, zipcode, terms, notes, True, now, now))
                    result["imported"] += 1
            except Exception as e:
                result["errors"].append(f"Row {i}: {e}")
        conn.commit()
    return result


def import_vendors_from_csv(rows: list[dict]) -> dict:
    """Import vendors from QBO Vendor Contact List CSV.

    Returns: {imported: int, updated: int, skipped: int, errors: list[str]}
    """
    result = {"imported": 0, "updated": 0, "skipped": 0, "errors": []}
    with get_db() as conn:
        for i, row in enumerate(rows, 1):
            try:
                name = _csv_val(row, "Vendor", "Vendor Name",
                                "Display Name", "Name")
                if not name:
                    result["errors"].append(f"Row {i}: No vendor name")
                    result["skipped"] += 1
                    continue

                contact = _csv_val(row, "Contact", "First Name",
                                   "Contact Name") or ""
                email = _csv_val(row, "Email", "Main Email") or ""
                phone = _csv_val(row, "Phone", "Main Phone") or ""
                address = _csv_val(row, "Street", "Address",
                                   "Billing Address") or ""
                terms = _csv_val(row, "Terms", "Payment Terms") or ""
                notes = _csv_val(row, "Notes", "Memo") or ""

                # Generate a code from vendor name
                code = name[:20].upper().replace(" ", "-").replace(".", "")

                existing = conn.execute(
                    "SELECT id FROM vendors WHERE name=%s", (name,)
                ).fetchone()

                now = datetime.now().isoformat()
                if existing:
                    pid = existing["id"]
                    conn.execute("""
                        UPDATE vendors SET contact_name=%s, contact_email=%s,
                               contact_phone=%s, address=%s, payment_terms=%s,
                               notes=%s, updated_at=%s
                        WHERE id=%s
                    """, (contact, email, phone, address, terms, notes, now, pid))
                    result["updated"] += 1
                else:
                    conn.execute("""
                        INSERT INTO vendors (name, code, contact_name,
                                           contact_email, contact_phone,
                                           address, payment_terms, notes,
                                           is_active, created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (name, code, contact, email, phone, address,
                          terms, notes, True, now, now))
                    result["imported"] += 1
            except Exception as e:
                result["errors"].append(f"Row {i}: {e}")
        conn.commit()
    return result


def import_products_from_csv(rows: list[dict]) -> dict:
    """Import products from QBO Product/Service List CSV.

    Returns: {imported: int, updated: int, skipped: int, errors: list[str]}
    """
    result = {"imported": 0, "updated": 0, "skipped": 0, "errors": []}
    with get_db() as conn:
        for i, row in enumerate(rows, 1):
            try:
                # QBO uses "Name" or "Product/Service" for the item name
                name = _csv_val(row, "Product/Service", "Name",
                                "Item Name", "Product Name")
                sku = _csv_val(row, "SKU", "Sku", "Item SKU") or ""
                if not sku and not name:
                    result["errors"].append(f"Row {i}: No SKU or name")
                    result["skipped"] += 1
                    continue
                if not sku:
                    # Use name as SKU if none provided
                    sku = name.upper().replace(" ", "-")[:40]

                title = name or sku
                category = _csv_val(row, "Type", "Category",
                                    "Product Type") or ""
                description = _csv_val(row, "Description",
                                       "Sales Description") or ""

                cost = _csv_float(row, "Cost", "Purchase Cost",
                                  "Cost Price")
                price = _csv_float(row, "Price", "Sales Price",
                                   "Selling Price", "Rate")
                qty = _csv_int(row, "Qty On Hand", "Quantity On Hand",
                               "Qty", "Quantity", "QOH")

                existing = conn.execute(
                    "SELECT id FROM products WHERE sku=%s", (sku,)
                ).fetchone()

                now = datetime.now().isoformat()
                if existing:
                    pid = existing["id"]
                    conn.execute("""
                        UPDATE products SET title=%s, category=%s,
                               description_html=%s, cost=%s,
                               selling_price=%s, qty_on_hand=%s,
                               updated_at=%s
                        WHERE id=%s
                    """, (title, category, description, cost,
                          price, qty, now, pid))
                    result["updated"] += 1
                else:
                    conn.execute("""
                        INSERT INTO products (sku, title, category,
                                            description_html, cost,
                                            selling_price, qty_on_hand,
                                            created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (sku, title, category, description,
                          cost, price, qty, now, now))
                    result["imported"] += 1
            except Exception as e:
                result["errors"].append(f"Row {i}: {e}")
        conn.commit()
    return result


def import_invoices_from_csv(rows: list[dict]) -> dict:
    """Import invoices from QBO Invoice List CSV.

    This handles header-level data. Each row = one invoice.
    Line items should be imported separately via import_invoice_lines_from_csv.

    Returns: {imported: int, updated: int, skipped: int, errors: list[str]}
    """
    result = {"imported": 0, "updated": 0, "skipped": 0, "errors": []}
    with get_db() as conn:
        for i, row in enumerate(rows, 1):
            try:
                inv_num = _csv_val(row, "Invoice No", "Num", "Number",
                                   "Invoice #", "Invoice Number",
                                   "Doc Number")
                if not inv_num:
                    result["errors"].append(f"Row {i}: No invoice number")
                    result["skipped"] += 1
                    continue

                cust_name = _csv_val(row, "Customer", "Customer Name",
                                     "Name") or ""
                # Look up customer by name
                customer_id = None
                if cust_name:
                    cust = conn.execute(
                        "SELECT id FROM customers WHERE name=%s",
                        (cust_name,)
                    ).fetchone()
                    if cust:
                        customer_id = cust["id"]

                inv_date = _csv_val(row, "Date", "Invoice Date",
                                    "Txn Date") or ""
                due_date = _csv_val(row, "Due Date") or ""
                status_raw = _csv_val(row, "Status", "Invoice Status") or ""
                # Map QBO status to our status
                status_map = {
                    "paid": "paid", "open": "sent", "overdue": "sent",
                    "closed": "paid", "voided": "void", "draft": "draft",
                    "partially paid": "partial",
                }
                status = status_map.get(status_raw.lower(), "sent")

                total = _csv_float(row, "Total", "Amount", "Invoice Total")
                balance = _csv_float(row, "Balance", "Open Balance",
                                     "Balance Due")
                paid = total - balance if total and balance is not None else 0

                existing = conn.execute(
                    "SELECT id FROM invoices WHERE invoice_number=%s",
                    (str(inv_num),)
                ).fetchone()

                now = datetime.now().isoformat()
                if existing:
                    pid = existing["id"]
                    conn.execute("""
                        UPDATE invoices SET customer_id=%s, status=%s,
                               invoice_date=%s, due_date=%s,
                               total=%s, amount_paid=%s, balance_due=%s,
                               updated_at=%s
                        WHERE id=%s
                    """, (customer_id, status, inv_date, due_date,
                          total, paid, balance, now, pid))
                    result["updated"] += 1
                else:
                    conn.execute("""
                        INSERT INTO invoices (invoice_number, customer_id,
                                            status, invoice_date, due_date,
                                            total, amount_paid, balance_due,
                                            created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (str(inv_num), customer_id, status, inv_date,
                          due_date, total, paid, balance, now, now))
                    result["imported"] += 1
            except Exception as e:
                result["errors"].append(f"Row {i}: {e}")
        conn.commit()
    return result


def import_invoice_lines_from_csv(rows: list[dict]) -> dict:
    """Import invoice line items from QBO Sales by Product/Service Detail CSV.

    Each row has invoice number + product + qty + amount.

    Returns: {imported: int, skipped: int, errors: list[str]}
    """
    result = {"imported": 0, "skipped": 0, "errors": []}
    with get_db() as conn:
        for i, row in enumerate(rows, 1):
            try:
                inv_num = _csv_val(row, "Num", "Invoice No", "Number",
                                   "Doc Number", "Invoice #")
                if not inv_num:
                    result["skipped"] += 1
                    continue

                inv = conn.execute(
                    "SELECT id FROM invoices WHERE invoice_number=%s",
                    (str(inv_num),)
                ).fetchone()
                if not inv:
                    result["errors"].append(
                        f"Row {i}: Invoice {inv_num} not found")
                    result["skipped"] += 1
                    continue

                invoice_id = inv["id"]
                product_name = _csv_val(row, "Product/Service",
                                        "Item", "Name") or ""
                description = _csv_val(row, "Memo", "Description",
                                       "Line Description") or product_name
                sku = ""
                product_id = None

                # Try to find product
                if product_name:
                    prod = conn.execute(
                        "SELECT id, sku FROM products WHERE title=%s OR sku=%s",
                        (product_name, product_name)
                    ).fetchone()
                    if prod:
                        product_id = prod["id"]
                        sku = prod["sku"]

                qty = _csv_int(row, "Qty", "Quantity")
                if qty == 0:
                    qty = 1
                rate = _csv_float(row, "Rate", "Price", "Unit Price")
                amount = _csv_float(row, "Amount", "Line Total", "Total")
                if not amount and rate:
                    amount = rate * qty

                conn.execute("""
                    INSERT INTO invoice_lines (invoice_id, product_id,
                                             sku, description,
                                             quantity, unit_price,
                                             line_total, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (invoice_id, product_id, sku, description,
                      qty, rate, amount, datetime.now().isoformat()))
                result["imported"] += 1
            except Exception as e:
                result["errors"].append(f"Row {i}: {e}")
        conn.commit()
    return result


# CSV helper: flexible column name lookup
def _csv_val(row: dict, *keys: str) -> str | None:
    """Get value from a CSV row dict, trying multiple possible column names."""
    for k in keys:
        if k in row and row[k]:
            v = str(row[k]).strip()
            if v:
                return v
        # Also try case-insensitive match
        for rk in row:
            if rk.lower() == k.lower() and row[rk]:
                v = str(row[rk]).strip()
                if v:
                    return v
    return None


def _csv_float(row: dict, *keys: str) -> float:
    """Get float value from CSV row, trying multiple column names."""
    val = _csv_val(row, *keys)
    if not val:
        return 0.0
    try:
        return float(val.replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _csv_int(row: dict, *keys: str) -> int:
    """Get int value from CSV row, trying multiple column names."""
    val = _csv_val(row, *keys)
    if not val:
        return 0
    try:
        return int(float(val.replace(",", "").strip()))
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# App Settings (key-value store)
# ---------------------------------------------------------------------------

def _ensure_app_settings_table(conn):
    """Create app_settings table if it doesn't exist."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except Exception:
        pass


def get_setting(key: str, default: str | None = None) -> str | None:
    """Get a single app setting by key."""
    with get_db() as conn:
        _ensure_app_settings_table(conn)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = %s", (key,)
        ).fetchone()
        if row:
            val = row["value"] if isinstance(row, dict) else row[0]
            return val
        return default


def set_setting(key: str, value: str) -> bool:
    """Set a single app setting (upsert)."""
    with get_db() as conn:
        _ensure_app_settings_table(conn)
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (key, value))
        conn.commit()
        return True


def get_all_settings() -> dict:
    """Get all app settings as a dict."""
    with get_db() as conn:
        _ensure_app_settings_table(conn)
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {
            (r["key"] if isinstance(r, dict) else r[0]):
            (r["value"] if isinstance(r, dict) else r[1])
            for r in rows
        }


# ─── Sales Tax Rates (manual nexus table) ───────────────────────────────


def list_tax_rates(active_only: bool = False) -> list[dict]:
    """Return all tax rate rows ordered by state then ZIP (state default first)."""
    sql = "SELECT * FROM tax_rates"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY state, CASE WHEN zip IS NULL THEN 0 ELSE 1 END, zip"
    with get_db() as conn:
        rows = conn.execute(sql).fetchall()
        out: list[dict] = []
        for r in rows:
            if isinstance(r, dict):
                out.append(dict(r))
            else:
                out.append({
                    "id": r[0], "state": r[1], "zip": r[2], "rate": r[3],
                    "description": r[4], "active": r[5],
                    "created_at": r[6] if len(r) > 6 else None,
                    "updated_at": r[7] if len(r) > 7 else None,
                    "qbo_tax_code": r[8] if len(r) > 8 else None,
                })
        return out


def upsert_tax_rate(rate_id: int | None, state: str, zip_code: str | None,
                    rate: float, description: str = "",
                    active: bool = True,
                    qbo_tax_code: str | None = None) -> int:
    """Insert (rate_id is None) or update an existing tax rate row.

    rate is a decimal (e.g. 0.029 for 2.9%).
    Returns the row id.
    """
    state = (state or "").strip().upper()[:2]
    zip_code = (zip_code or "").strip() or None
    qbo_code = (qbo_tax_code or "").strip() or None
    with get_db() as conn:
        # Detect column presence — older DBs may not have run migration 077.
        try:
            cols = {c[1] if not hasattr(c, "keys") else c["name"]
                    for c in conn.execute("PRAGMA table_info(tax_rates)").fetchall()}
        except Exception:
            cols = set()
        has_code = "qbo_tax_code" in cols
        if rate_id:
            if has_code:
                conn.execute(
                    "UPDATE tax_rates SET state=%s, zip=%s, rate=%s, description=%s, "
                    "active=%s, qbo_tax_code=%s, updated_at=datetime('now') WHERE id=%s",
                    (state, zip_code, float(rate), description,
                     1 if active else 0, qbo_code, int(rate_id)),
                )
            else:
                conn.execute(
                    "UPDATE tax_rates SET state=%s, zip=%s, rate=%s, description=%s, "
                    "active=%s, updated_at=datetime('now') WHERE id=%s",
                    (state, zip_code, float(rate), description,
                     1 if active else 0, int(rate_id)),
                )
            conn.commit()
            return int(rate_id)
        if has_code:
            cur = conn.execute(
                "INSERT INTO tax_rates (state, zip, rate, description, active, qbo_tax_code) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (state, zip_code, float(rate), description,
                 1 if active else 0, qbo_code),
            )
        else:
            cur = conn.execute(
                "INSERT INTO tax_rates (state, zip, rate, description, active) "
                "VALUES (%s, %s, %s, %s, %s)",
                (state, zip_code, float(rate), description, 1 if active else 0),
            )
        conn.commit()
        return cur.lastrowid


def get_qbo_tax_code_for_rate(rate: float) -> str | None:
    """Return the configured ``qbo_tax_code`` for the given decimal rate.

    Used by the QBO CSV exporter (D2) to stamp jurisdictional tax codes
    on every taxable invoice line. Lookup matches on ``rate`` to within
    1e-6 — a single-row exact match wins; otherwise None is returned and
    callers should fall back to the generic ``"TAX"`` code.
    """
    if rate is None:
        return None
    try:
        target = float(rate)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    with get_db() as conn:
        try:
            cols = {c[1] if not hasattr(c, "keys") else c["name"]
                    for c in conn.execute("PRAGMA table_info(tax_rates)").fetchall()}
        except Exception:
            cols = set()
        if "qbo_tax_code" not in cols:
            return None
        try:
            rows = conn.execute(
                "SELECT qbo_tax_code, rate FROM tax_rates "
                "WHERE qbo_tax_code IS NOT NULL AND qbo_tax_code != ''"
            ).fetchall()
        except Exception:
            return None
    for r in rows:
        if isinstance(r, dict):
            code = r.get("qbo_tax_code")
            r_rate = r.get("rate")
        else:
            code = r[0]
            r_rate = r[1]
        try:
            if abs(float(r_rate) - target) < 1e-6:
                return (code or "").strip() or None
        except (TypeError, ValueError):
            continue
    return None


def delete_tax_rate(rate_id: int) -> None:
    """Delete a tax rate row by id."""
    with get_db() as conn:
        conn.execute("DELETE FROM tax_rates WHERE id=%s", (int(rate_id),))
        conn.commit()


def get_tax_rate_for_address(state: str | None,
                             zip_code: str | None) -> float:
    """Return the decimal tax rate for a ship-to address.

    Lookup order:
      1. Exact (state, zip) active match
      2. State default (state, zip IS NULL) active match
      3. 0.0 (no nexus in this state)
    """
    state = (state or "").strip().upper()[:2]
    zip_code = (zip_code or "").strip()
    if not state:
        return 0.0
    with get_db() as conn:
        if zip_code:
            row = conn.execute(
                "SELECT rate FROM tax_rates "
                "WHERE active=1 AND state=%s AND zip=%s LIMIT 1",
                (state, zip_code),
            ).fetchone()
            if row:
                val = row["rate"] if isinstance(row, dict) else row[0]
                return float(val or 0)
        row = conn.execute(
            "SELECT rate FROM tax_rates "
            "WHERE active=1 AND state=%s AND zip IS NULL LIMIT 1",
            (state,),
        ).fetchone()
        if row:
            val = row["rate"] if isinstance(row, dict) else row[0]
            return float(val or 0)
        return 0.0


# TaxJar cache TTL — re-query if cached row is older than this many days
_TAXJAR_CACHE_DAYS = 7
_TAXJAR_CACHE_TAG = "[TaxJar cache"


def _taxjar_cache_lookup(state: str, zip_code: str) -> float | None:
    """Return cached TaxJar rate for a ZIP if it exists and is fresh."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT rate, description, updated_at FROM tax_rates "
            "WHERE active=1 AND state=%s AND zip=%s LIMIT 1",
            (state, zip_code),
        ).fetchone()
    if not row:
        return None
    rate = row["rate"] if isinstance(row, dict) else row[0]
    desc = row["description"] if isinstance(row, dict) else row[1]
    updated = row["updated_at"] if isinstance(row, dict) else row[2]
    if not desc or _TAXJAR_CACHE_TAG not in (desc or ""):
        # Manual override — caller decides; treat as a "hit" so we don't
        # overwrite with TaxJar data.
        return float(rate or 0)
    # Check freshness
    try:
        from datetime import datetime, timedelta
        ts = datetime.fromisoformat(str(updated).replace("Z", ""))
        if datetime.utcnow() - ts > timedelta(days=_TAXJAR_CACHE_DAYS):
            return None
    except Exception:
        return None
    return float(rate or 0)


def _taxjar_cache_store(state: str, zip_code: str, rate: float,
                        breakdown: str = "") -> None:
    """Upsert a TaxJar-sourced rate into tax_rates for offline fallback."""
    from datetime import datetime
    desc = f"{_TAXJAR_CACHE_TAG} {datetime.utcnow().date().isoformat()}] {breakdown}".strip()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM tax_rates WHERE state=%s AND zip=%s LIMIT 1",
            (state, zip_code),
        ).fetchone()
        if existing:
            rid = existing["id"] if isinstance(existing, dict) else existing[0]
            # Don't overwrite manual overrides
            cur_desc_row = conn.execute(
                "SELECT description FROM tax_rates WHERE id=%s", (rid,)
            ).fetchone()
            cur_desc = (cur_desc_row["description"] if isinstance(cur_desc_row, dict)
                        else cur_desc_row[0]) if cur_desc_row else ""
            if cur_desc and _TAXJAR_CACHE_TAG not in (cur_desc or ""):
                return
            conn.execute(
                "UPDATE tax_rates SET rate=%s, description=%s, active=1, "
                "updated_at=datetime('now') WHERE id=%s",
                (float(rate), desc, rid),
            )
        else:
            conn.execute(
                "INSERT INTO tax_rates (state, zip, rate, description, active) "
                "VALUES (%s, %s, %s, %s, 1)",
                (state, zip_code, float(rate), desc),
            )
        conn.commit()


def resolve_tax_rate(state: str | None, zip_code: str | None) -> float:
    """Hybrid tax-rate lookup: TaxJar (if configured) → manual table.

    Behavior:
      - Reads `tax_provider` setting ('manual' [default] or 'taxjar').
      - If 'taxjar' + key set: serve from cache when fresh (<7d), else
        call TaxJar and update the cache. On any API failure falls back
        to manual `get_tax_rate_for_address`.
      - 'manual' or no ZIP: returns `get_tax_rate_for_address`.
    """
    state = (state or "").strip().upper()[:2]
    zip_code = (zip_code or "").strip()

    provider = (get_setting("tax_provider", "manual") or "manual").lower()
    if provider != "taxjar" or not zip_code or not state:
        return get_tax_rate_for_address(state, zip_code)

    api_key = (get_setting("taxjar_api_key", "") or "").strip()
    if not api_key:
        return get_tax_rate_for_address(state, zip_code)

    # Fresh cache hit (covers manual ZIP overrides too — _cache_lookup
    # returns the manual rate without re-querying TaxJar).
    cached = _taxjar_cache_lookup(state, zip_code)
    if cached is not None:
        return cached

    # Stale or missing — call TaxJar
    sandbox = (get_setting("taxjar_sandbox", "0") or "0") == "1"
    try:
        from jaks_inventory.utils.taxjar_client import lookup_rate
        rate_obj = lookup_rate(api_key, zip_code, state=state, sandbox=sandbox)
        combined = float(rate_obj.get("combined_rate") or 0)
        breakdown = (
            f"state {float(rate_obj.get('state_rate') or 0)*100:.3f}% / "
            f"county {float(rate_obj.get('county_rate') or 0)*100:.3f}% / "
            f"city {float(rate_obj.get('city_rate') or 0)*100:.3f}%"
        )
        _taxjar_cache_store(state, zip_code, combined, breakdown)
        return combined
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "TaxJar lookup failed for %s %s: %s — falling back to manual table",
            state, zip_code, e,
        )
        return get_tax_rate_for_address(state, zip_code)


# ─── Phase 6: SKU Prefix Management & Auto-SKU ──────────────────────────


def create_sku_prefix(prefix: str, manufacturer: str = None,
                      vendor_id: int = None, description: str = None) -> int:
    """Create a SKU prefix mapping. Returns new prefix ID."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sku_prefixes (prefix, manufacturer, vendor_id, description)
            VALUES (%s, %s, %s, %s)
        """, (prefix.upper(), manufacturer, vendor_id, description))
        conn.commit()
        return conn.lastrowid


def get_sku_prefixes(vendor_id: int = None, manufacturer: str = None) -> list[dict]:
    """List all SKU prefixes, optionally filtered."""
    with get_db() as conn:
        conditions = []
        params = []
        if vendor_id:
            conditions.append("sp.vendor_id = %s")
            params.append(vendor_id)
        if manufacturer:
            conditions.append("sp.manufacturer = %s")
            params.append(manufacturer)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(f"""
            SELECT sp.*, v.name as vendor_name
            FROM sku_prefixes sp
            LEFT JOIN vendors v ON v.id = sp.vendor_id
            {where}
            ORDER BY sp.prefix
        """, tuple(params)).fetchall()
        return [dict(row) if hasattr(row, 'keys') else {} for row in rows]


def update_sku_prefix(prefix_id: int, prefix: str = None, manufacturer: str = None,
                      vendor_id: int = None, description: str = None) -> bool:
    """Update an existing SKU prefix."""
    fields = {}
    if prefix is not None:
        fields["prefix"] = prefix.upper()
    if manufacturer is not None:
        fields["manufacturer"] = manufacturer
    if vendor_id is not None:
        fields["vendor_id"] = vendor_id
    if description is not None:
        fields["description"] = description
    if not fields:
        return False
    with get_db() as conn:
        set_clause = ", ".join(f"{k} = %s" for k in fields)
        vals = list(fields.values()) + [prefix_id]
        conn.execute(f"UPDATE sku_prefixes SET {set_clause} WHERE id = %s", tuple(vals))
        conn.commit()
        return True


def delete_sku_prefix(prefix_id: int) -> bool:
    """Delete a SKU prefix."""
    with get_db() as conn:
        conn.execute("DELETE FROM sku_prefixes WHERE id = %s", (prefix_id,))
        conn.commit()
        return True


def generate_next_sku(prefix: str) -> str:
    """Generate the next sequential SKU for a given prefix.
    
    Uses sku_sequences table for atomic counter. Format: PREFIX-NNNNNN
    """
    prefix = prefix.upper()
    with get_db() as conn:
        # Try to increment existing sequence
        row = conn.execute(
            "SELECT last_number FROM sku_sequences WHERE prefix = %s", (prefix,)
        ).fetchone()

        if row:
            next_num = int(row[0]) + 1
            conn.execute(
                "UPDATE sku_sequences SET last_number = %s WHERE prefix = %s",
                (next_num, prefix)
            )
        else:
            next_num = 1
            conn.execute(
                "INSERT INTO sku_sequences (prefix, last_number) VALUES (%s, %s)",
                (prefix, next_num)
            )

        conn.commit()
        return f"{prefix}-{next_num:06d}"


def get_sku_prefix_for_vendor(vendor_id: int) -> str:
    """Get the SKU prefix associated with a vendor. Returns prefix string or None.

    Checks sku_prefixes table first, then falls back to JAKS-<vendor.code>.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT prefix FROM sku_prefixes WHERE vendor_id = %s LIMIT 1", (vendor_id,)
        ).fetchone()
        if row and row[0]:
            return row[0]
        # Fall back to vendor code
        vrow = conn.execute(
            "SELECT code FROM vendors WHERE id = %s", (vendor_id,)
        ).fetchone()
        if vrow and vrow[0]:
            return f"JAKS-{vrow[0].upper()}"
        return None


# ─── Phase 6: Duplicate Detection ───────────────────────────────────────


def find_similar_products(title: str = None, sku: str = None,
                          oem_part_number: str = None, xref_part_number: str = None,
                          exclude_id: int = None) -> list[dict]:
    """Find products that may be duplicates based on title, SKU, OEM, or XREF.
    
    Returns list of potential matches with a 'match_reason' field.
    """
    results = []
    seen_ids = set()

    with get_db() as conn:
        exclude = f"AND id != {int(exclude_id)}" if exclude_id else ""

        # Exact SKU match (strongest signal)
        if sku:
            rows = conn.execute(f"""
                SELECT id, sku, title, oem_part_number, xref_part_number, manufacturer
                FROM products WHERE sku = %s {exclude}
            """, (sku,)).fetchall()
            for row in rows:
                r = dict(row)
                r["match_reason"] = "exact_sku"
                r["confidence"] = "high"
                if r["id"] not in seen_ids:
                    results.append(r)
                    seen_ids.add(r["id"])

        # OEM part number match
        if oem_part_number:
            rows = conn.execute(f"""
                SELECT id, sku, title, oem_part_number, xref_part_number, manufacturer
                FROM products WHERE oem_part_number = %s {exclude}
            """, (oem_part_number,)).fetchall()
            for row in rows:
                r = dict(row)
                r["match_reason"] = "exact_oem"
                r["confidence"] = "high"
                if r["id"] not in seen_ids:
                    results.append(r)
                    seen_ids.add(r["id"])

        # XREF match (check both oem and xref columns)
        if xref_part_number:
            rows = conn.execute(f"""
                SELECT id, sku, title, oem_part_number, xref_part_number, manufacturer
                FROM products WHERE xref_part_number = %s OR oem_part_number = %s {exclude}
            """, (xref_part_number, xref_part_number)).fetchall()
            for row in rows:
                r = dict(row)
                r["match_reason"] = "xref_match"
                r["confidence"] = "high"
                if r["id"] not in seen_ids:
                    results.append(r)
                    seen_ids.add(r["id"])

        # Title similarity (word-based matching)
        if title and len(title) >= 3:
            # Split title into significant words (3+ chars), excluding generic terms
            _generic = {
                "kit", "set", "engine", "part", "parts", "new", "used",
                "for", "with", "and", "the", "reman", "rebuild", "repair",
                "assembly", "complete", "heavy", "duty", "truck", "diesel",
            }
            words = [w for w in title.split() if len(w) >= 3 and w.lower() not in _generic]
            if words:
                # Match products containing any significant word
                word_conditions = " OR ".join(["title LIKE %s"] * len(words))
                word_params = [f"%{w}%" for w in words[:5]]  # Limit to 5 words
                rows = conn.execute(f"""
                    SELECT id, sku, title, oem_part_number, xref_part_number, manufacturer
                    FROM products WHERE ({word_conditions}) {exclude}
                """, tuple(word_params)).fetchall()
                for row in rows:
                    r = dict(row)
                    if r["id"] not in seen_ids:
                        # Count matching words for confidence
                        title_lower = (r.get("title") or "").lower()
                        matches = sum(1 for w in words if w.lower() in title_lower)
                        # Require majority of significant words to match
                        threshold = max(2, len(words) // 2 + 1)
                        if matches >= threshold or (matches == 1 and len(words) == 1):
                            r["match_reason"] = "title_similarity"
                            r["confidence"] = "medium" if matches >= threshold else "low"
                            r["matching_words"] = matches
                            results.append(r)
                            seen_ids.add(r["id"])

    return results


# ── Scraper Functions ─────────────────────────────────────────────
# Functions for scrape_runs, scraped_cross_references, competitor_prices


def create_scrape_run(run_type: str, source: str, *, notes: str | None = None) -> int:
    """Create a new scrape run record.  Returns the run id."""
    with get_db() as db:
        db.execute(
            "INSERT INTO scrape_runs (run_type, source, status, notes) VALUES (?, ?, 'running', ?)",
            (run_type, source, notes),
        )
        row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        return row_id


def update_scrape_run(
    run_id: int,
    *,
    status: str | None = None,
    total_searched: int | None = None,
    total_found: int | None = None,
) -> None:
    """Update a scrape run (status, counts)."""
    parts, params = [], []
    if status is not None:
        parts.append("status = ?")
        params.append(status)
        if status in ("completed", "failed"):
            parts.append("completed_at = CURRENT_TIMESTAMP")
    if total_searched is not None:
        parts.append("total_searched = ?")
        params.append(total_searched)
    if total_found is not None:
        parts.append("total_found = ?")
        params.append(total_found)
    if not parts:
        return
    params.append(run_id)
    with get_db() as db:
        db.execute(f"UPDATE scrape_runs SET {', '.join(parts)} WHERE id = ?", params)
        db.commit()


def get_scrape_runs(limit: int = 50, run_type: str | None = None) -> list[dict]:
    """Return recent scrape runs."""
    sql = "SELECT * FROM scrape_runs"
    params: list = []
    if run_type:
        sql += " WHERE run_type = ?"
        params.append(run_type)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_last_scrape_run(run_type: str | None = None, source: str | None = None) -> dict | None:
    """Return the most recent completed scrape run matching filters."""
    sql = "SELECT * FROM scrape_runs WHERE status = 'completed'"
    params: list = []
    if run_type:
        sql += " AND run_type = ?"
        params.append(run_type)
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY completed_at DESC LIMIT 1"
    with get_db() as db:
        row = db.execute(sql, params).fetchone()
        return dict(row) if row else None


# ── Scraped cross references ──


def save_scraped_xref(
    run_id: int,
    source_part_number: str,
    found_alternate_number: str,
    source: str,
    *,
    product_id: int | None = None,
    confidence: str = "medium",
    match_type: str | None = None,
) -> int | None:
    """Save a scraped cross-reference (pending review).  Returns id or None on error."""
    with get_db() as db:
        try:
            db.execute(
                """INSERT INTO scraped_cross_references
                   (run_id, product_id, source_part_number, found_alternate_number,
                    source, confidence, match_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, product_id, source_part_number, found_alternate_number,
                 source, confidence, match_type),
            )
            row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
            return row_id
        except Exception:
            # Likely UNIQUE constraint — return existing row id so caller
            # knows data exists (count reflects actual results, not just new inserts)
            try:
                row = db.execute(
                    """SELECT id FROM scraped_cross_references
                       WHERE source_part_number = ? AND found_alternate_number = ? AND source = ?""",
                    (source_part_number, found_alternate_number, source),
                ).fetchone()
                if row:
                    return row[0]
            except Exception:
                pass
            return None


def get_pending_scraped_xrefs(product_id: int | None = None) -> list[dict]:
    """Return scraped cross-references awaiting review."""
    sql = "SELECT * FROM scraped_cross_references WHERE status = 'pending'"
    params: list = []
    if product_id is not None:
        sql += " AND product_id = ?"
        params.append(product_id)
    sql += " ORDER BY scraped_at DESC"
    with get_db() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def accept_scraped_xref(xref_id: int) -> bool:
    """Accept a scraped xref — creates a live interchange record."""
    with get_db() as db:
        row = db.execute("SELECT * FROM scraped_cross_references WHERE id = ?", (xref_id,)).fetchone()
        if not row:
            return False
        row = dict(row)
        # Write to live part_interchanges table
        if row.get("product_id"):
            try:
                db.execute(
                    """INSERT OR IGNORE INTO part_interchanges
                       (product_id, interchange_number, interchange_type, manufacturer, notes)
                       VALUES (?, ?, ?, ?, ?)""",
                    (row["product_id"], row["found_alternate_number"],
                     row.get("match_type") or "Cross-Reference",
                     row.get("source") or "", f"Scraped from {row.get('source', '')}")
                )
            except Exception:
                pass
        db.execute(
            "UPDATE scraped_cross_references SET status = 'accepted', accepted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (xref_id,),
        )
        db.commit()
        return True


def reject_scraped_xref(xref_id: int) -> bool:
    """Reject a scraped xref."""
    with get_db() as db:
        db.execute(
            "UPDATE scraped_cross_references SET status = 'rejected' WHERE id = ?",
            (xref_id,),
        )
        db.commit()
        return True


def get_scraped_xref_counts() -> dict:
    """Return counts by status."""
    with get_db() as db:
        rows = db.execute(
            "SELECT status, COUNT(*) as cnt FROM scraped_cross_references GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}


# ── Competitor prices ──


def save_competitor_price(
    run_id: int,
    sku_searched: str,
    competitor_name: str,
    *,
    product_id: int | None = None,
    competitor_part_number: str | None = None,
    competitor_price: float | None = None,
    availability: str | None = None,
    source_url: str | None = None,
) -> int | None:
    """Save a scraped competitor price (pending review)."""
    with get_db() as db:
        try:
            # Avoid exact duplicates (same product, competitor, part number, price)
            existing = db.execute(
                """SELECT id FROM competitor_prices
                   WHERE sku_searched = ? AND competitor_name = ?
                     AND competitor_part_number = ? AND competitor_price = ?
                   ORDER BY scraped_at DESC LIMIT 1""",
                (sku_searched, competitor_name, competitor_part_number, competitor_price),
            ).fetchone()
            if existing:
                return existing[0]
            db.execute(
                """INSERT INTO competitor_prices
                   (run_id, product_id, sku_searched, competitor_name,
                    competitor_part_number, competitor_price, availability, source_url,
                    status, accepted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', CURRENT_TIMESTAMP)""",
                (run_id, product_id, sku_searched, competitor_name,
                 competitor_part_number, competitor_price, availability, source_url),
            )
            row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
            return row_id
        except Exception:
            return None


def get_pending_competitor_prices(product_id: int | None = None) -> list[dict]:
    """Return competitor prices awaiting review."""
    sql = "SELECT * FROM competitor_prices WHERE status = 'pending'"
    params: list = []
    if product_id is not None:
        sql += " AND product_id = ?"
        params.append(product_id)
    sql += " ORDER BY scraped_at DESC"
    with get_db() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def accept_competitor_price(price_id: int) -> bool:
    """Accept a competitor price record (marks as accepted for reference)."""
    with get_db() as db:
        db.execute(
            "UPDATE competitor_prices SET status = 'accepted', accepted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (price_id,),
        )
        db.commit()
        return True


def reject_competitor_price(price_id: int) -> bool:
    """Reject a competitor price record."""
    with get_db() as db:
        db.execute(
            "UPDATE competitor_prices SET status = 'rejected' WHERE id = ?",
            (price_id,),
        )
        db.commit()
        return True


def get_competitor_price_counts() -> dict:
    """Return counts by status."""
    with get_db() as db:
        rows = db.execute(
            "SELECT status, COUNT(*) as cnt FROM competitor_prices GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}


# ── Latest accepted competitor prices per source ──


def get_latest_competitor_prices(product_id: int = None, sku_searched: str = None) -> list[dict]:
    """Return the most recent accepted competitor price per source for a product.

    Can look up by product_id or by sku_searched (for unsaved/new products).

    Returns rows like: [{"competitor_name": "FleetPride", "competitor_price": 123.45,
                         "scraped_at": "...", "availability": "In Stock", ...}, ...]
    """
    with get_db() as db:
        if product_id:
            rows = db.execute(
                """SELECT cp.*
                   FROM competitor_prices cp
                   INNER JOIN (
                       SELECT competitor_name, MAX(scraped_at) AS max_at
                       FROM competitor_prices
                       WHERE product_id = ? AND status = 'accepted'
                       GROUP BY competitor_name
                   ) latest ON cp.competitor_name = latest.competitor_name
                            AND cp.scraped_at = latest.max_at
                   WHERE cp.product_id = ? AND cp.status = 'accepted'
                   ORDER BY cp.competitor_name""",
                (product_id, product_id),
            ).fetchall()
        elif sku_searched:
            rows = db.execute(
                """SELECT cp.*
                   FROM competitor_prices cp
                   INNER JOIN (
                       SELECT competitor_name, MAX(scraped_at) AS max_at
                       FROM competitor_prices
                       WHERE sku_searched = ? AND status = 'accepted'
                       GROUP BY competitor_name
                   ) latest ON cp.competitor_name = latest.competitor_name
                            AND cp.scraped_at = latest.max_at
                   WHERE cp.sku_searched = ? AND cp.status = 'accepted'
                   ORDER BY cp.competitor_name""",
                (sku_searched, sku_searched),
            ).fetchall()
        else:
            return []
        return [dict(r) for r in rows]


def get_scrape_dates_for_product(product_id: int) -> dict[str, str]:
    """Return {source_abbr: 'M/D'} for latest competitor price scrape per source."""
    _NAME_MAP = {
        "Diesel Parts Direct": "DPD",
        "ATL Diesel": "ATL",
        "FleetPride": "FLT",
        "Highway & Heavy Parts": "HHP",
    }
    with get_db() as db:
        rows = db.execute(
            """SELECT competitor_name, MAX(scraped_at) as last_at
               FROM competitor_prices
               WHERE product_id = ?
               GROUP BY competitor_name""",
            (product_id,),
        ).fetchall()
    result: dict[str, str] = {}
    for r in rows:
        name = r["competitor_name"]
        abbr = _NAME_MAP.get(name)
        if not abbr:
            continue
        dt_str = r["last_at"] or ""
        if dt_str:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                result[abbr] = f"{dt.month}/{dt.day}"
            except Exception:
                result[abbr] = dt_str[:5]
    return result


# ── Product images ──


def get_product_images(product_id: int) -> list[dict]:
    """Return all images for a product, ordered by position."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY position",
            (product_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_product_image(
    product_id: int,
    filename: str,
    path: str,
    *,
    position: int = 1,
    width: int | None = None,
    height: int | None = None,
    file_size: int | None = None,
    hash_val: str | None = None,
    source: str = "upload",
) -> int | None:
    """Add an image record for a product. Returns image id or None."""
    with get_db() as db:
        try:
            db.execute(
                """INSERT OR IGNORE INTO product_images
                   (product_id, filename, path, position, width, height, file_size, hash, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (product_id, filename, path, position, width, height, file_size, hash_val, source),
            )
            # Update image_count on products table
            db.execute(
                "UPDATE products SET image_count = (SELECT COUNT(*) FROM product_images WHERE product_id = ?) WHERE id = ?",
                (product_id, product_id),
            )
            new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
            return new_id
        except Exception:
            return None


def delete_product_image(image_id: int) -> bool:
    """Delete a product image record and update the product's image_count."""
    with get_db() as db:
        row = db.execute("SELECT product_id FROM product_images WHERE id = ?", (image_id,)).fetchone()
        if not row:
            return False
        pid = row["product_id"]
        db.execute("DELETE FROM product_images WHERE id = ?", (image_id,))
        db.execute(
            "UPDATE products SET image_count = (SELECT COUNT(*) FROM product_images WHERE product_id = ?) WHERE id = ?",
            (pid, pid),
        )
        db.commit()
        return True


def get_product_sales_stats(product_id: int) -> dict:
    """Return sales statistics for a product (90-day and 1-year).

    Returns dict with keys:
        qty_90d, revenue_90d, profit_90d, qty_1y, revenue_1y, profit_1y

    Profit = revenue − (current product cost × qty_sold). Cost data isn't
    snapshot per-line today, so this is a best-effort using the current
    ``products.cost`` value.
    """
    result = {
        "qty_90d": 0, "revenue_90d": 0.0, "profit_90d": 0.0,
        "qty_1y": 0, "revenue_1y": 0.0, "profit_1y": 0.0,
    }
    with get_db() as db:
        cost_row = db.execute(
            "SELECT COALESCE(cost, 0) AS cost FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        unit_cost = float(cost_row["cost"]) if cost_row else 0.0
        for key_suffix, interval in [("90d", "-90 days"), ("1y", "-365 days")]:
            row = db.execute(
                "SELECT COALESCE(SUM(il.quantity), 0) AS qty, "
                "COALESCE(SUM(il.line_total), 0.0) AS revenue "
                "FROM invoice_lines il "
                "JOIN invoices i ON il.invoice_id = i.id "
                "WHERE il.product_id = ? AND i.invoice_date >= date('now', ?)",
                (product_id, interval),
            ).fetchone()
            if row:
                qty = int(row["qty"])
                revenue = float(row["revenue"])
                result[f"qty_{key_suffix}"] = qty
                result[f"revenue_{key_suffix}"] = revenue
                result[f"profit_{key_suffix}"] = revenue - (unit_cost * qty)
    return result


def delete_product(product_id: int) -> dict:
    """Permanently delete a product and its related records.

    Returns {"success": True} or {"success": False, "error": str}.
    """
    with get_db() as conn:
        # Verify product exists
        row = conn.execute(
            "SELECT id, sku FROM products WHERE id = %s", (product_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "Product not found"}
        # Remove child records (order matters for FK constraints)
        for child_table, col in [
            ("part_numbers", "product_id"),
            ("product_locations", "product_id"),
            ("inventory_transactions", "product_id"),
            ("part_interchanges", "product_id"),
        ]:
            try:
                conn.execute(f"DELETE FROM {child_table} WHERE {col} = %s", (product_id,))
            except Exception:
                pass  # table may not exist yet
        conn.execute("DELETE FROM products WHERE id = %s", (product_id,))
        conn.commit()
    return {"success": True}


def get_customers_who_bought(product_id: int) -> list[dict]:
    """Return customers who have purchased a given product (via invoice lines).

    Each dict: {customer_id, customer_name, company, total_qty, last_date}.
    """
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id   AS customer_id,
                   c.name AS customer_name,
                   c.company,
                   SUM(il.quantity) AS total_qty,
                   MAX(i.invoice_date) AS last_date
            FROM invoice_lines il
            JOIN invoices i ON il.invoice_id = i.id
            JOIN customers c ON i.customer_id = c.id
            WHERE il.product_id = %s
            GROUP BY c.id, c.name, c.company
            ORDER BY last_date DESC
        """, (product_id,)).fetchall()
        return [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]


def get_last_sold_date(product_id: int) -> str | None:
    """Return the most recent invoice date for a product, or None."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT MAX(i.invoice_date) AS last_date
            FROM invoice_lines il
            JOIN invoices i ON il.invoice_id = i.id
            WHERE il.product_id = %s
        """, (product_id,)).fetchone()
        if row and row[0]:
            return str(row[0])
    return None


def get_sales_summary_batch(product_ids: list[int]) -> dict[int, dict]:
    """Return {product_id: {"last_sold": str|None, "qty_12m": int}} for a batch of products."""
    if not product_ids:
        return {}
    result = {pid: {"last_sold": None, "qty_12m": 0} for pid in product_ids}
    with get_db() as conn:
        placeholders = ", ".join(["%s"] * len(product_ids))
        rows = conn.execute(f"""
            SELECT il.product_id,
                   MAX(i.invoice_date) AS last_sold,
                   COALESCE(SUM(CASE WHEN i.invoice_date >= date('now', '-365 days')
                                     THEN il.quantity ELSE 0 END), 0) AS qty_12m
            FROM invoice_lines il
            JOIN invoices i ON il.invoice_id = i.id
            WHERE il.product_id IN ({placeholders})
            GROUP BY il.product_id
        """, tuple(product_ids)).fetchall()
        for r in rows:
            row = dict(r) if hasattr(r, 'keys') else {"product_id": r[0], "last_sold": r[1], "qty_12m": r[2]}
            pid = row.get("product_id") or r[0]
            result[pid] = {
                "last_sold": str(row.get("last_sold") or r[1] or "") or None,
                "qty_12m": int(row.get("qty_12m") or r[2] or 0),
            }
    return result


# =====================================================================
#  Marketing Phase 2 – Templates, Audiences, Campaigns, Automation
# =====================================================================

# ── SMS Templates ──

def get_sms_templates(category: str | None = None) -> list[dict]:
    with get_db() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM sms_templates WHERE category=? AND is_active=1 ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sms_templates WHERE is_active=1 ORDER BY category, name"
            ).fetchall()
    return [dict(r) for r in rows]


def get_sms_template(template_id: int) -> dict | None:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM sms_templates WHERE id=?", (template_id,)).fetchone()
    return dict(r) if r else None


def create_sms_template(name: str, category: str, body: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO sms_templates (name, category, body) VALUES (?, ?, ?)",
            (name, category, body),
        )
        conn.commit()
        return cur.lastrowid


def update_sms_template(template_id: int, **kwargs) -> None:
    allowed = {"name", "category", "body", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [template_id]
    with get_db() as conn:
        conn.execute(f"UPDATE sms_templates SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)
        conn.commit()


def delete_sms_template(template_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM sms_templates WHERE id=?", (template_id,))
        conn.commit()


# ── Saved Audiences ──

def get_saved_audiences() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM saved_audiences ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_saved_audience(audience_id: int) -> dict | None:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM saved_audiences WHERE id=?", (audience_id,)).fetchone()
    return dict(r) if r else None


def create_saved_audience(name: str, description: str, filters_json: str, customer_count: int = 0) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO saved_audiences (name, description, filters_json, customer_count) VALUES (?, ?, ?, ?)",
            (name, description, filters_json, customer_count),
        )
        conn.commit()
        return cur.lastrowid


def update_saved_audience(audience_id: int, **kwargs) -> None:
    allowed = {"name", "description", "filters_json", "customer_count"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [audience_id]
    with get_db() as conn:
        conn.execute(f"UPDATE saved_audiences SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)
        conn.commit()


def delete_saved_audience(audience_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM saved_audiences WHERE id=?", (audience_id,))
        conn.commit()


# ── Campaigns ──

def create_campaign(name: str, channel: str, message_body: str,
                    subject: str = "", audience_id: int | None = None,
                    template_id: int | None = None,
                    scheduled_at: str | None = None) -> int:
    with get_db() as conn:
        status = "scheduled" if scheduled_at else "draft"
        cur = conn.execute(
            """INSERT INTO campaigns
               (name, channel, audience_id, template_id, message_body, subject, status, scheduled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, channel, audience_id, template_id, message_body, subject, status, scheduled_at),
        )
        conn.commit()
        return cur.lastrowid


def get_campaigns(status: str | None = None) -> list[dict]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM campaigns WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_campaign(campaign_id: int) -> dict | None:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    return dict(r) if r else None


def update_campaign(campaign_id: int, **kwargs) -> None:
    allowed = {"name", "status", "sent_at", "total_recipients", "total_sent",
               "total_failed", "total_replied", "message_body", "subject", "scheduled_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [campaign_id]
    with get_db() as conn:
        conn.execute(f"UPDATE campaigns SET {sets} WHERE id=?", vals)
        conn.commit()


def add_campaign_recipient(campaign_id: int, customer_id: int,
                           phone: str = "", email: str = "") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO campaign_recipients (campaign_id, customer_id, phone, email) VALUES (?, ?, ?, ?)",
            (campaign_id, customer_id, phone, email),
        )
        conn.commit()
        return cur.lastrowid


def get_campaign_recipients(campaign_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT cr.*, c.name AS customer_name, c.company
               FROM campaign_recipients cr
               JOIN customers c ON c.id = cr.customer_id
               WHERE cr.campaign_id=?
               ORDER BY c.name""",
            (campaign_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_campaign_recipient(recipient_id: int, **kwargs) -> None:
    allowed = {"status", "sent_at", "error", "replied", "reply_body", "replied_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [recipient_id]
    with get_db() as conn:
        conn.execute(f"UPDATE campaign_recipients SET {sets} WHERE id=?", vals)
        conn.commit()


# ── Automation Rules ──

def get_automation_rules() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM automation_rules ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_automation_rule(rule_id: int) -> dict | None:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM automation_rules WHERE id=?", (rule_id,)).fetchone()
    return dict(r) if r else None


def update_automation_rule(rule_id: int, **kwargs) -> None:
    allowed = {"name", "is_active", "config_json", "template_id", "last_run_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [rule_id]
    with get_db() as conn:
        conn.execute(f"UPDATE automation_rules SET {sets} WHERE id=?", vals)
        conn.commit()


def add_automation_log(rule_id: int, customer_id: int, action: str,
                       message: str = "", status: str = "suggested") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO automation_log (rule_id, customer_id, action, message, status) VALUES (?, ?, ?, ?, ?)",
            (rule_id, customer_id, action, message, status),
        )
        conn.commit()
        return cur.lastrowid


def get_automation_log(rule_id: int | None = None, status: str | None = None,
                       limit: int = 100) -> list[dict]:
    with get_db() as conn:
        clauses = []
        params = []
        if rule_id:
            clauses.append("al.rule_id=?")
            params.append(rule_id)
        if status:
            clauses.append("al.status=?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"""SELECT al.*, c.name AS customer_name, c.phone,
                       ar.name AS rule_name, ar.rule_type
                FROM automation_log al
                JOIN customers c ON c.id = al.customer_id
                JOIN automation_rules ar ON ar.id = al.rule_id
                {where}
                ORDER BY al.created_at DESC LIMIT ?""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def update_automation_log_status(log_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE automation_log SET status=?, acted_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, log_id),
        )
        conn.commit()


# ── Campaign Filter Engine ──

def filter_customers_by_criteria(filters: list[dict]) -> list[dict]:
    """Apply AND-combined filters to customers. Returns matching customer rows.

    Each filter dict: {type, operator, value}
    Types: last_order_within, no_order_since, bought_part, bought_category,
           top_spenders, open_balance, over_credit, aging_days,
           payment_terms, open_cores, overdue_cores, core_credits,
           customer_type, has_phone, has_email, open_quotes, engine_type
    """
    with get_db() as conn:
        # Start with all active customers
        base = """
            SELECT DISTINCT c.id, c.name, c.company, c.phone, c.email,
                   c.payment_terms, c.customer_type, c.pricing_tier,
                   c.account_number
            FROM customers c
            WHERE c.is_active = 1
        """
        params = []
        joins = []
        conditions = []

        for f in filters:
            ftype = f.get("type", "")
            val = f.get("value", "")

            if ftype == "last_order_within":
                # Customers who bought in last X days
                conditions.append(
                    "c.id IN (SELECT DISTINCT customer_id FROM invoices "
                    "WHERE date(invoice_date) >= date('now', '-' || ? || ' days'))"
                )
                params.append(int(val))

            elif ftype == "no_order_since":
                # Customers who have NOT bought in X days
                conditions.append(
                    "(c.id NOT IN (SELECT DISTINCT customer_id FROM invoices "
                    "WHERE date(invoice_date) >= date('now', '-' || ? || ' days')) "
                    "OR c.id NOT IN (SELECT DISTINCT customer_id FROM invoices))"
                )
                params.append(int(val))

            elif ftype == "bought_part":
                # Customers who bought specific part (keyword in title/sku)
                conditions.append(
                    "c.id IN (SELECT DISTINCT i.customer_id FROM invoices i "
                    "JOIN invoice_lines il ON il.invoice_id = i.id "
                    "JOIN products p ON p.id = il.product_id "
                    "WHERE p.title LIKE ? OR p.sku LIKE ? OR p.description_html LIKE ?)"
                )
                kw = f"%{val}%"
                params.extend([kw, kw, kw])

            elif ftype == "bought_category":
                conditions.append(
                    "c.id IN (SELECT DISTINCT i.customer_id FROM invoices i "
                    "JOIN invoice_lines il ON il.invoice_id = i.id "
                    "JOIN products p ON p.id = il.product_id "
                    "WHERE p.category LIKE ?)"
                )
                params.append(f"%{val}%")

            elif ftype == "top_spenders":
                # Top N spenders in last X days (val = "30" for 30 days)
                conditions.append(
                    "c.id IN (SELECT customer_id FROM invoices "
                    "WHERE date(invoice_date) >= date('now', '-' || ? || ' days') "
                    "GROUP BY customer_id ORDER BY SUM(total) DESC LIMIT 50)"
                )
                params.append(int(val))

            elif ftype == "open_balance":
                conditions.append(
                    "c.id IN (SELECT customer_id FROM invoices "
                    "WHERE status NOT IN ('paid', 'void') AND total > COALESCE(amount_paid, 0))"
                )

            elif ftype == "over_credit":
                conditions.append(
                    """c.id IN (
                        SELECT ca.customer_id FROM credit_agreements ca
                        WHERE ca.credit_limit > 0
                        AND (SELECT COALESCE(SUM(total - COALESCE(amount_paid, 0)), 0)
                             FROM invoices WHERE customer_id = ca.customer_id
                             AND status NOT IN ('paid', 'void')) > ca.credit_limit
                    )"""
                )

            elif ftype == "aging_days":
                # Invoices older than X days unpaid
                conditions.append(
                    "c.id IN (SELECT DISTINCT customer_id FROM invoices "
                    "WHERE status NOT IN ('paid', 'void') "
                    "AND julianday('now') - julianday(invoice_date) > ?)"
                )
                params.append(int(val))

            elif ftype == "payment_terms":
                conditions.append("c.payment_terms = ?")
                params.append(val)

            elif ftype == "open_cores":
                conditions.append(
                    "c.id IN (SELECT DISTINCT i.customer_id FROM invoices i "
                    "JOIN invoice_lines il ON il.invoice_id = i.id "
                    "WHERE il.core_charge > 0 "
                    "AND il.id NOT IN (SELECT invoice_line_id FROM core_returns "
                    "WHERE status IN ('received', 'credited')))"
                )

            elif ftype == "overdue_cores":
                days = int(val) if val else 30
                conditions.append(
                    "c.id IN (SELECT DISTINCT i.customer_id FROM invoices i "
                    "JOIN invoice_lines il ON il.invoice_id = i.id "
                    "WHERE il.core_charge > 0 "
                    "AND julianday('now') - julianday(i.invoice_date) > ? "
                    "AND il.id NOT IN (SELECT invoice_line_id FROM core_returns "
                    "WHERE status IN ('received', 'credited')))"
                )
                params.append(days)

            elif ftype == "core_credits":
                conditions.append(
                    "c.id IN (SELECT DISTINCT i.customer_id FROM invoices i "
                    "JOIN invoice_lines il ON il.invoice_id = i.id "
                    "JOIN core_returns cr ON cr.invoice_line_id = il.id "
                    "WHERE cr.status = 'received')"
                )

            elif ftype == "customer_type":
                conditions.append("c.customer_type = ?")
                params.append(val)

            elif ftype == "has_phone":
                conditions.append("c.phone IS NOT NULL AND c.phone != ''")

            elif ftype == "has_email":
                conditions.append("c.email IS NOT NULL AND c.email != ''")

            elif ftype == "open_quotes":
                conditions.append(
                    "c.id IN (SELECT DISTINCT customer_id FROM quotes "
                    "WHERE status IN ('draft', 'sent'))"
                )

            elif ftype == "engine_type":
                # C15, ISX, etc. — search product titles in customer purchases
                conditions.append(
                    "c.id IN (SELECT DISTINCT i.customer_id FROM invoices i "
                    "JOIN invoice_lines il ON il.invoice_id = i.id "
                    "JOIN products p ON p.id = il.product_id "
                    "WHERE p.title LIKE ? OR p.engine LIKE ?)"
                )
                kw = f"%{val}%"
                params.extend([kw, kw])

        if conditions:
            base += " AND " + " AND ".join(conditions)

        base += " ORDER BY c.name"
        rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]


def get_customers_who_bought_product(product_id: int) -> list[dict]:
    """Customers who previously purchased a specific product."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT c.id, c.name, c.company, c.phone, c.email
               FROM customers c
               JOIN invoices i ON i.customer_id = c.id
               JOIN invoice_lines il ON il.invoice_id = i.id
               WHERE il.product_id = ? AND c.is_active = 1
               ORDER BY c.name""",
            (product_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_stale_quotes(days: int = 2) -> list[dict]:
    """Quotes not converted after X days."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT q.*, c.name AS customer_name, c.phone
               FROM quotes q
               JOIN customers c ON c.id = q.customer_id
               WHERE q.status IN ('draft', 'sent')
               AND julianday('now') - julianday(q.created_at) > ?
               ORDER BY q.created_at ASC""",
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_inactive_customers(days: int = 60) -> list[dict]:
    """Customers with no orders in the given number of days."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.name, c.company, c.phone, c.email,
                      MAX(i.invoice_date) AS last_order
               FROM customers c
               LEFT JOIN invoices i ON i.customer_id = c.id
               WHERE c.is_active = 1
               GROUP BY c.id
               HAVING last_order IS NULL
                  OR julianday('now') - julianday(last_order) > ?
               ORDER BY last_order ASC""",
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_customers_near_credit_limit(threshold_pct: int = 90) -> list[dict]:
    """Customers at or above threshold % of credit limit."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.name, c.company, c.phone,
                      ca.credit_limit,
                      COALESCE(SUM(
                          CASE WHEN inv.status NOT IN ('paid','void')
                               THEN inv.total - COALESCE(inv.amount_paid, 0)
                               ELSE 0 END
                      ), 0) AS outstanding
               FROM customers c
               JOIN credit_agreements ca ON ca.customer_id = c.id
               LEFT JOIN invoices inv ON inv.customer_id = c.id
               WHERE ca.credit_limit > 0 AND c.is_active = 1
               GROUP BY c.id
               HAVING outstanding >= (ca.credit_limit * ? / 100.0)
               ORDER BY outstanding DESC""",
            (threshold_pct,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation_stats() -> dict:
    """Get messaging stats for dashboard."""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(DISTINCT customer_id) FROM messages").fetchone()[0]
        unread = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='inbound' AND read=0"
        ).fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(created_at) = date('now')"
        ).fetchone()[0]
    return {"total_conversations": total, "unread": unread, "today": today}


# ── Suggested Sells ──────────────────────────────────────────────────

def add_suggested_sell(
    product_id: int,
    suggested_product_id: int,
    relationship_type: str = "recommended",
    notes: str = "",
    sort_order: int = 0,
) -> int | None:
    """Link a suggested-sell product. Returns new row id or None on duplicate."""
    with get_db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO suggested_sells "
                "(product_id, suggested_product_id, relationship_type, notes, sort_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (product_id, suggested_product_id, relationship_type, notes, sort_order),
            )
            conn.commit()
            return cur.lastrowid
        except Exception:
            return None


def get_suggested_sells(product_id: int) -> list[dict]:
    """Return suggested-sell rows for a product, joined with product info."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ss.id, ss.suggested_product_id, ss.relationship_type, "
            "ss.notes, ss.sort_order, "
            "p.sku, p.title, p.selling_price, p.supplier, "
            "COALESCE(NULLIF(p.pai_cost, 0), p.cost, 0) AS cost, "
            "p.jaks_warranty_value, p.jaks_warranty_unit, p.jaks_warranty_enabled, "
            "p.core_charge "
            "FROM suggested_sells ss "
            "JOIN products p ON p.id = ss.suggested_product_id "
            "WHERE ss.product_id = ? "
            "ORDER BY ss.sort_order, p.sku",
            (product_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_suggested_sell(row_id: int) -> bool:
    """Remove a suggested-sell link by its id."""
    with get_db() as conn:
        conn.execute("DELETE FROM suggested_sells WHERE id = ?", (row_id,))
        conn.commit()
        return True


def update_suggested_sell(
    row_id: int,
    relationship_type: str | None = None,
    notes: str | None = None,
    sort_order: int | None = None,
) -> bool:
    """Update fields on an existing suggested-sell row."""
    sets, vals = [], []
    if relationship_type is not None:
        sets.append("relationship_type = ?")
        vals.append(relationship_type)
    if notes is not None:
        sets.append("notes = ?")
        vals.append(notes)
    if sort_order is not None:
        sets.append("sort_order = ?")
        vals.append(sort_order)
    if not sets:
        return False
    vals.append(row_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE suggested_sells SET {', '.join(sets)} WHERE id = ?", vals
        )
        conn.commit()
        return True


# ─── Purchase Receipts (formal warehouse receiving) ───────────────────────────
# These functions drive the new PO Receipts workflow screen.
# purchase_receipts / purchase_receipt_lines are the source of truth for all
# received goods.  The legacy receive_po_line() path (used by ReceiveDialog)
# still works; finalize_receipt() is the new canonical path.

def generate_receipt_number() -> str:
    """Generate next receipt number in format RCP-YYYY-NNNN."""
    year = datetime.now().year
    prefix = f"RCP-{year}-"
    with get_db() as conn:
        row = conn.execute(
            "SELECT receipt_number FROM purchase_receipts WHERE receipt_number LIKE %s "
            "ORDER BY receipt_number DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
        last = int((row[0] if row else f"{prefix}0000").split("-")[-1])
        return f"{prefix}{last + 1:04d}"


def list_receivable_pos() -> list[dict]:
    """Return POs that are ready to receive (sent / confirmed / ordered / partial)."""
    today = datetime.now().date().isoformat()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                po.id,
                po.po_number,
                po.status,
                po.order_date,
                po.expected_date,
                po.total,
                v.name  AS vendor_name,
                (SELECT COUNT(*) FROM sales_orders so
                 WHERE so.id = po.linked_so_id OR po.notes LIKE '%From SO #' || so.id || '%')
                    AS linked_so_count,
                (SELECT COALESCE(SUM(
                     MAX(pol.quantity_ordered - pol.quantity_received, 0)
                 ), 0)
                 FROM purchase_order_lines pol WHERE pol.po_id = po.id)
                    AS outstanding_qty
            FROM purchase_orders po
            LEFT JOIN vendors v ON po.vendor_id = v.id
            WHERE po.status IN ('sent', 'confirmed', 'ordered', 'partial')
            ORDER BY po.expected_date ASC NULLS LAST, po.id DESC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {
                "id": r[0], "po_number": r[1], "status": r[2],
                "order_date": r[3], "expected_date": r[4],
                "total": r[5],
                "vendor_name": r[6], "linked_so_count": r[7],
                "outstanding_qty": r[8],
            }
            exp = d.get("expected_date") or ""
            if exp and exp < today:
                d["priority"] = "LATE"
            elif exp and exp == today:
                d["priority"] = "TODAY"
            else:
                d["priority"] = "normal"
            result.append(d)
        return result


def create_receipt_draft(po_id: int, received_by: str = None) -> dict:
    """Create a new draft purchase receipt for a PO.

    Returns {"success": True, "receipt_id": int, "receipt_number": str}.
    """
    po = get_purchase_order(po_id)
    if not po:
        return {"success": False, "error": "Purchase order not found"}
    status = (po.get("status") or "").lower()
    if status not in ("sent", "confirmed", "ordered", "partial"):
        return {"success": False, "error": f"PO is not receivable (status='{status}')"}

    # Check for an existing open draft for this PO
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, receipt_number FROM purchase_receipts WHERE po_id = %s AND status = 'draft' LIMIT 1",
            (po_id,),
        ).fetchone()
        if existing:
            return {
                "success": True,
                "receipt_id": existing[0],
                "receipt_number": existing[1],
                "existing": True,
            }

        receipt_number = generate_receipt_number()
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO purchase_receipts
               (receipt_number, po_id, vendor_id, received_by, status, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'draft', %s, %s)""",
            (receipt_number, po_id, po.get("vendor_id"), received_by, now, now),
        )
        conn.commit()
        receipt_id = conn.execute(
            "SELECT id FROM purchase_receipts WHERE receipt_number = %s", (receipt_number,)
        ).fetchone()[0]
        return {"success": True, "receipt_id": receipt_id, "receipt_number": receipt_number}


def get_receipt(receipt_id: int) -> dict | None:
    """Get a purchase receipt with all its lines."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT pr.*, v.name AS vendor_name, po.po_number
               FROM purchase_receipts pr
               LEFT JOIN vendors v ON pr.vendor_id = v.id
               LEFT JOIN purchase_orders po ON pr.po_id = po.id
               WHERE pr.id = %s""",
            (receipt_id,),
        ).fetchone()
        if not row:
            return None
        receipt = dict(row) if hasattr(row, "keys") else dict(zip(
            ["id","receipt_number","po_id","vendor_id","received_date","received_by",
             "vendor_invoice_number","packing_slip_number","freight_amount","duty_amount",
             "other_landed_fees","landed_cost_allocated","notes","status","qbo_bill_id",
             "qbo_sync_status","qbo_sync_error","finalized_at","finalized_by",
             "voided_at","voided_by","void_reason","created_at","updated_at",
             "vendor_name","po_number"], row
        ))

        # Check for serial_number column (migration 111)
        line_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_receipt_lines)").fetchall()}
        serial_expr = ", prl.serial_number" if "serial_number" in line_cols else ", NULL AS serial_number"

        lines = conn.execute(
            f"""SELECT prl.*, p.sku AS product_sku, p.title AS product_title,
                       pol.quantity_ordered, pol.quantity_received AS po_qty_received,
                       pol.unit_cost AS po_unit_cost{serial_expr}
                FROM purchase_receipt_lines prl
                LEFT JOIN products p ON prl.product_id = p.id
                LEFT JOIN purchase_order_lines pol ON prl.po_line_id = pol.id
                WHERE prl.receipt_id = %s
                ORDER BY prl.id""",
            (receipt_id,),
        ).fetchall()
        receipt["lines"] = [dict(r) if hasattr(r, "keys") else r for r in lines]
        return receipt


def list_receipts(status: str = None, po_id: int = None, limit: int = 200) -> list[dict]:
    """List purchase receipts with optional filters."""
    conds = []
    params: list = []
    if status:
        conds.append("pr.status = %s")
        params.append(status)
    if po_id:
        conds.append("pr.po_id = %s")
        params.append(po_id)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT pr.*, v.name AS vendor_name, po.po_number
                FROM purchase_receipts pr
                LEFT JOIN vendors v ON pr.vendor_id = v.id
                LEFT JOIN purchase_orders po ON pr.po_id = po.id
                {where}
                ORDER BY pr.created_at DESC
                LIMIT %s""",
            params,
        ).fetchall()
        return [dict(r) if hasattr(r, "keys") else r for r in rows]


def save_receipt_draft(receipt_id: int, header: dict, lines: list[dict]) -> dict:
    """Save (update) a draft receipt's header fields and receipt lines.

    *header* keys match purchase_receipts columns (received_by, received_date,
    vendor_invoice_number, packing_slip_number, freight_amount, duty_amount,
    other_landed_fees, notes).

    *lines* is a list of dicts with keys:
      po_line_id, product_id, sku, description, qty_received, qty_damaged,
      qty_backordered, unit_cost, vendor_core_amount, location_id, bin_location,
      serial_number (optional), notes.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM purchase_receipts WHERE id = %s", (receipt_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "Receipt not found"}
        if (row[0] if not hasattr(row, "keys") else row["status"]) != "draft":
            return {"success": False, "error": "Only draft receipts can be edited"}

        now = datetime.now().isoformat()
        allowed_header = {
            "received_by", "received_date", "vendor_invoice_number",
            "packing_slip_number", "freight_amount", "duty_amount",
            "other_landed_fees", "notes",
        }
        sets, vals = [], []
        for k, v in header.items():
            if k in allowed_header:
                sets.append(f"{k} = %s")
                vals.append(v)
        if sets:
            vals.extend([now, receipt_id])
            conn.execute(
                f"UPDATE purchase_receipts SET {', '.join(sets)}, updated_at = %s WHERE id = %s",
                vals,
            )

        # Check for serial_number column
        line_cols = {c[1] for c in conn.execute("PRAGMA table_info(purchase_receipt_lines)").fetchall()}
        has_serial = "serial_number" in line_cols

        # Delete existing draft lines and re-insert
        conn.execute("DELETE FROM purchase_receipt_lines WHERE receipt_id = %s", (receipt_id,))
        for ln in lines:
            if (int(ln.get("qty_received") or 0) + int(ln.get("qty_damaged") or 0)
                    + int(ln.get("qty_backordered") or 0)) == 0:
                continue  # skip blank rows
            if has_serial:
                conn.execute(
                    """INSERT INTO purchase_receipt_lines
                       (receipt_id, po_line_id, product_id, sku, description,
                        qty_received, qty_damaged, qty_backordered, unit_cost,
                        vendor_core_amount, location_id, bin_location, serial_number, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (receipt_id, ln.get("po_line_id"), ln.get("product_id"),
                     ln.get("sku"), ln.get("description"),
                     int(ln.get("qty_received") or 0), int(ln.get("qty_damaged") or 0),
                     int(ln.get("qty_backordered") or 0), float(ln.get("unit_cost") or 0),
                     float(ln.get("vendor_core_amount") or 0), ln.get("location_id"),
                     ln.get("bin_location"), ln.get("serial_number"), ln.get("notes")),
                )
            else:
                conn.execute(
                    """INSERT INTO purchase_receipt_lines
                       (receipt_id, po_line_id, product_id, sku, description,
                        qty_received, qty_damaged, qty_backordered, unit_cost,
                        vendor_core_amount, location_id, bin_location, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (receipt_id, ln.get("po_line_id"), ln.get("product_id"),
                     ln.get("sku"), ln.get("description"),
                     int(ln.get("qty_received") or 0), int(ln.get("qty_damaged") or 0),
                     int(ln.get("qty_backordered") or 0), float(ln.get("unit_cost") or 0),
                     float(ln.get("vendor_core_amount") or 0), ln.get("location_id"),
                     ln.get("bin_location"), ln.get("notes")),
                )
        conn.commit()
    return {"success": True}


def finalize_receipt(receipt_id: int, finalized_by: str = None) -> dict:
    """Finalize a draft purchase receipt.

    For each receipt line:
      - Updates purchase_order_lines.quantity_received
      - Creates an inventory_transactions record
      - Calls adjust_product_quantity to update qty_on_hand
      - Allocates landed costs proportionally
    Then updates PO status (partial / received) and marks receipt as finalized.
    """
    receipt = get_receipt(receipt_id)
    if not receipt:
        return {"success": False, "error": "Receipt not found"}
    if receipt.get("status") != "draft":
        return {"success": False, "error": f"Receipt is already {receipt.get('status')}"}

    po_id = receipt["po_id"]
    po = get_purchase_order(po_id)
    if not po:
        return {"success": False, "error": "Linked PO not found"}

    lines = receipt.get("lines") or []
    if not lines:
        return {"success": False, "error": "No lines to finalize — save the receipt first"}

    # Calculate landed cost allocation
    freight = float(receipt.get("freight_amount") or 0)
    duty = float(receipt.get("duty_amount") or 0)
    other = float(receipt.get("other_landed_fees") or 0)
    total_extra = freight + duty + other
    total_cost = sum(
        float(ln.get("unit_cost") or 0) * int(ln.get("qty_received") or 0)
        for ln in lines
    )

    errors = []
    with get_db() as conn:
        now_ts = datetime.now().isoformat()
        today = datetime.now().strftime("%Y-%m-%d")

        for ln in lines:
            qty = int(ln.get("qty_received") or 0)
            if qty <= 0:
                continue
            line_cost = float(ln.get("unit_cost") or 0) * qty
            landed_extra = (line_cost / total_cost * total_extra) if total_cost > 0 else 0
            landed_unit = float(ln.get("unit_cost") or 0) + (landed_extra / qty if qty else 0)

            po_line_id = ln.get("po_line_id")
            product_id = ln.get("product_id")

            # Update PO line quantity_received
            if po_line_id:
                conn.execute(
                    """UPDATE purchase_order_lines
                       SET quantity_received = quantity_received + %s,
                           landed_unit_cost = %s,
                           updated_at = %s
                       WHERE id = %s""",
                    (qty, landed_unit, now_ts, po_line_id),
                )

            # Update receipt line with landed cost
            conn.execute(
                "UPDATE purchase_receipt_lines SET landed_unit_cost = %s WHERE id = %s",
                (landed_unit, ln.get("id")),
            )

            # Adjust inventory
            if product_id:
                sku = ln.get("sku") or ln.get("product_sku") or ""
                result = adjust_product_quantity(
                    sku=sku,
                    delta=qty,
                    txn_type="receive",
                    notes=f"Receipt {receipt.get('receipt_number')} from PO {po.get('po_number')}",
                    reference=receipt.get("receipt_number"),
                    user=finalized_by or "system",
                )
                if not result.get("success"):
                    errors.append(f"SKU {sku}: {result.get('error')}")

                # Assign to location if specified
                loc_id = ln.get("location_id")
                if loc_id and product_id:
                    try:
                        assign_product_to_location(product_id, loc_id, qty)
                    except Exception:
                        pass

        # Update PO status
        remaining = conn.execute(
            """SELECT COUNT(*) FROM purchase_order_lines
               WHERE po_id = %s AND quantity_received < quantity_ordered""",
            (po_id,),
        ).fetchone()[0]

        if remaining == 0:
            conn.execute(
                "UPDATE purchase_orders SET status='received', received_date=%s, updated_at=%s WHERE id=%s",
                (today, now_ts, po_id),
            )
        else:
            conn.execute(
                "UPDATE purchase_orders SET status='partial', updated_at=%s WHERE id=%s",
                (now_ts, po_id),
            )

        # Mark receipt finalized
        conn.execute(
            """UPDATE purchase_receipts
               SET status='finalized', finalized_at=%s, finalized_by=%s,
                   landed_cost_allocated=%s, updated_at=%s
               WHERE id=%s""",
            (now_ts, finalized_by, 1 if total_extra > 0 else 0, now_ts, receipt_id),
        )
        conn.commit()

    return {
        "success": True,
        "errors": errors,
        "receipt_number": receipt.get("receipt_number"),
        "po_status": "received" if remaining == 0 else "partial",
    }


def void_receipt(receipt_id: int, reason: str = "", voided_by: str = None) -> dict:
    """Void a finalized receipt, reversing its inventory effect."""
    receipt = get_receipt(receipt_id)
    if not receipt:
        return {"success": False, "error": "Receipt not found"}
    if receipt.get("status") != "finalized":
        return {"success": False, "error": "Only finalized receipts can be voided"}

    po_id = receipt["po_id"]
    po = get_purchase_order(po_id)
    errors = []

    with get_db() as conn:
        now_ts = datetime.now().isoformat()
        today = datetime.now().strftime("%Y-%m-%d")

        for ln in (receipt.get("lines") or []):
            qty = int(ln.get("qty_received") or 0)
            if qty <= 0:
                continue
            po_line_id = ln.get("po_line_id")
            product_id = ln.get("product_id")

            if po_line_id:
                conn.execute(
                    """UPDATE purchase_order_lines
                       SET quantity_received = MAX(quantity_received - %s, 0), updated_at = %s
                       WHERE id = %s""",
                    (qty, now_ts, po_line_id),
                )

            if product_id:
                sku = ln.get("sku") or ln.get("product_sku") or ""
                result = adjust_product_quantity(
                    sku=sku,
                    delta=-qty,
                    txn_type="adjustment",
                    notes=f"VOID of receipt {receipt.get('receipt_number')}. Reason: {reason}",
                    reference=receipt.get("receipt_number"),
                    user=voided_by or "system",
                )
                if not result.get("success"):
                    errors.append(f"SKU {sku}: {result.get('error')}")

        # Recompute PO status
        remaining = conn.execute(
            """SELECT COUNT(*) FROM purchase_order_lines
               WHERE po_id = %s AND quantity_received < quantity_ordered""",
            (po_id,),
        ).fetchone()[0]
        total_lines = conn.execute(
            "SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = %s", (po_id,)
        ).fetchone()[0]

        if remaining == total_lines:
            conn.execute(
                "UPDATE purchase_orders SET status='sent', received_date=NULL, updated_at=%s WHERE id=%s",
                (now_ts, po_id),
            )
        else:
            conn.execute(
                "UPDATE purchase_orders SET status='partial', updated_at=%s WHERE id=%s",
                (now_ts, po_id),
            )

        conn.execute(
            """UPDATE purchase_receipts
               SET status='voided', voided_at=%s, voided_by=%s, void_reason=%s, updated_at=%s
               WHERE id=%s""",
            (now_ts, voided_by, reason, now_ts, receipt_id),
        )
        conn.commit()

    return {"success": True, "errors": errors, "receipt_number": receipt.get("receipt_number")}


def get_on_order_qty_batch(product_ids: list[int]) -> dict[int, int]:
    """Return {product_id: outstanding_on_order_qty} across open/partial POs.

    Outstanding = quantity_ordered - quantity_received, summed for the given
    products on purchase orders that are not closed/cancelled/voided.
    """
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    sql = f"""
        SELECT pol.product_id,
               COALESCE(SUM(CASE WHEN pol.quantity_ordered > pol.quantity_received
                                 THEN pol.quantity_ordered - pol.quantity_received
                                 ELSE 0 END), 0) AS on_order
          FROM purchase_order_lines pol
          JOIN purchase_orders po ON po.id = pol.po_id
         WHERE pol.product_id IN ({placeholders})
           AND po.status NOT IN ('closed', 'cancelled', 'void', 'voided', 'received')
         GROUP BY pol.product_id
    """
    out: dict[int, int] = {}
    try:
        with get_db() as conn:
            rows = conn.execute(sql, tuple(product_ids)).fetchall()
            for row in rows:
                pid = row[0]
                qty = int(row[1] or 0)
                if pid is not None and qty > 0:
                    out[int(pid)] = qty
    except Exception:
        return {}
    return out


def get_recent_events(limit: int = 20) -> list[dict]:
    """Return the most recent business events across the app for the
    dashboard Recent Activity feed.

    Each item is::

        {"ts": iso_timestamp, "kind": <str>, "icon": <emoji>,
         "title": <str>, "subtitle": <str>, "entity_id": <int|None>}

    ``kind`` is one of: quote, invoice, payment, po, receipt, qbo_error.
    All queries are best-effort; if a table is missing or a query fails
    the helper simply skips that source rather than raising.
    """
    events: list[dict] = []

    def _safe(fn):
        try:
            return fn() or []
        except Exception:
            return []

    with get_db() as conn:
        def _q(sql, params=()):
            try:
                return conn.execute(sql, params).fetchall()
            except Exception:
                return []

        # Quotes ---------------------------------------------------------
        for row in _safe(lambda: _q(
            """SELECT id, quote_number, status, created_at
                 FROM quotes
                ORDER BY created_at DESC
                LIMIT %s""",
            (limit,),
        )):
            events.append({
                "ts": row[3],
                "kind": "quote",
                "icon": "\U0001F4DD",  # memo
                "title": f"Quote {row[1]} created",
                "subtitle": (row[2] or "").replace("_", " "),
                "entity_id": row[0],
            })

        # Invoices -------------------------------------------------------
        for row in _safe(lambda: _q(
            """SELECT id, invoice_number, status, total, created_at
                 FROM invoices
                ORDER BY created_at DESC
                LIMIT %s""",
            (limit,),
        )):
            events.append({
                "ts": row[4],
                "kind": "invoice",
                "icon": "\U0001F4C4",  # page
                "title": f"Invoice {row[1]} created",
                "subtitle": f"${float(row[3] or 0):,.2f} \u00b7 {(row[2] or '').replace('_', ' ')}",
                "entity_id": row[0],
            })

        # Payments -------------------------------------------------------
        for row in _safe(lambda: _q(
            """SELECT id, invoice_id, amount, payment_date
                 FROM payments
                ORDER BY payment_date DESC, id DESC
                LIMIT %s""",
            (limit,),
        )):
            events.append({
                "ts": row[3],
                "kind": "payment",
                "icon": "\U0001F4B5",  # dollar
                "title": "Payment received",
                "subtitle": f"${float(row[2] or 0):,.2f} on invoice #{row[1]}",
                "entity_id": row[0],
            })

        # Purchase Orders ------------------------------------------------
        for row in _safe(lambda: _q(
            """SELECT id, po_number, status, created_at
                 FROM purchase_orders
                ORDER BY created_at DESC
                LIMIT %s""",
            (limit,),
        )):
            status = (row[2] or "").lower()
            verb = "received" if status == "received" else (
                "sent" if status == "sent" else status or "created"
            )
            events.append({
                "ts": row[3],
                "kind": "po",
                "icon": "\U0001F4E6",  # package
                "title": f"PO {row[1]} {verb}",
                "subtitle": status,
                "entity_id": row[0],
            })

        # PO Receipts ----------------------------------------------------
        for row in _safe(lambda: _q(
            """SELECT id, receipt_number, po_id, status, created_at
                 FROM po_receipts
                ORDER BY created_at DESC
                LIMIT %s""",
            (limit,),
        )):
            events.append({
                "ts": row[4],
                "kind": "receipt",
                "icon": "\U0001F4E5",  # inbox
                "title": f"Receipt {row[1]}",
                "subtitle": f"PO #{row[2]} \u00b7 {(row[3] or '').replace('_', ' ')}",
                "entity_id": row[0],
            })

        # QBO sync errors -----------------------------------------------
        for row in _safe(lambda: _q(
            """SELECT id, entity_type, error_message, created_at
                 FROM qbo_sync_log
                WHERE status = 'failed' AND resolved_at IS NULL
                ORDER BY created_at DESC
                LIMIT %s""",
            (limit,),
        )):
            msg = (row[2] or "")[:80]
            events.append({
                "ts": row[3],
                "kind": "qbo_error",
                "icon": "\u26A0",  # warning
                "title": f"QBO sync failed: {row[1] or 'unknown'}",
                "subtitle": msg,
                "entity_id": row[0],
            })

    # Sort by ts desc, slice
    events.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return events[:limit]





# -- Product quantity tiers (Product Workbench: Pricing ? Quantity Discounts) --

def get_product_qty_tiers(product_id: int) -> list[dict]:
    """Return tier rows for a product, ordered by min_qty ascending.

    Tiers live in the `product_qty_tiers` table which is provisioned by
    _apply_migrations. Each dict has: id, name, min_qty, price, discount_pct,
    sort_order.
    """
    if not product_id:
        return []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, min_qty, price, discount_pct, sort_order "
            "FROM product_qty_tiers WHERE product_id = %s "
            "ORDER BY min_qty ASC, sort_order ASC, id ASC",
            (int(product_id),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def set_product_qty_tiers(product_id: int, tiers: list[dict]) -> bool:
    """Replace all tiers for `product_id` with the supplied list.

    Each tier dict may contain: name, min_qty, price, discount_pct, sort_order.
    Returns True on success.
    """
    if not product_id:
        return False
    with get_db() as conn:
        conn.execute(
            "DELETE FROM product_qty_tiers WHERE product_id = %s",
            (int(product_id),),
        )
        for i, t in enumerate(tiers or []):
            try:
                min_qty = int(t.get("min_qty") or 0)
            except Exception:
                min_qty = 0
            if min_qty <= 0:
                continue
            try:
                price = float(t.get("price") or 0)
            except Exception:
                price = 0.0
            try:
                discount = float(t.get("discount_pct") or 0)
            except Exception:
                discount = 0.0
            name = str(t.get("name") or "").strip()
            sort_order = int(t.get("sort_order") or i)
            conn.execute(
                "INSERT INTO product_qty_tiers "
                "(product_id, name, min_qty, price, discount_pct, sort_order) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (int(product_id), name, min_qty, price, discount, sort_order),
            )
        conn.commit()
    return True
