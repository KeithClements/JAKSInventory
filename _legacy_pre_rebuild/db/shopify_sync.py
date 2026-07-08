"""Shopify <-> Database Sync Module.

Provides two-way sync between Shopify and the local inventory database:
- sync_from_shopify(): Pull all Shopify products into local DB
- push_to_shopify(): Push local changes back to Shopify

The database acts as a local cache/mirror of Shopify data.
Shopify is the source of truth for what products exist.
"""

from __future__ import annotations

import os
import re
import json
import logging
from datetime import datetime
from typing import Any

import requests

from .database import get_connection, is_postgresql

logger = logging.getLogger(__name__)

# Shopify API configuration
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "jaks-heavy-duty")
SHOPIFY_API_VERSION = "2024-01"
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", os.getenv("ACCESS_TOKEN", ""))


def _shopify_request(method: str, endpoint: str, json_data: dict = None) -> requests.Response | None:
    """Make a request to Shopify Admin API."""
    base_url = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/{SHOPIFY_API_VERSION}"
    url = f"{base_url}{endpoint}"
    
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    
    try:
        resp = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        logger.error(f"Shopify API error: {e}")
        return None


def fetch_all_shopify_products() -> list[dict]:
    """Fetch all products from Shopify (handles pagination)."""
    all_products = []
    endpoint = "/products.json?limit=250"
    
    while endpoint:
        resp = _shopify_request("GET", endpoint)
        if not resp:
            logger.error("Failed to fetch products from Shopify")
            break
        
        data = resp.json()
        products = data.get("products", [])
        all_products.extend(products)
        logger.info(f"Fetched {len(products)} products (total: {len(all_products)})")
        
        # Handle pagination via Link header
        link_header = resp.headers.get("Link", "")
        endpoint = None
        if 'rel="next"' in link_header:
            # Parse next URL from Link header
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    match = re.search(r'<([^>]+)>', link)
                    if match:
                        next_url = match.group(1)
                        # Extract just the endpoint part
                        endpoint = next_url.split(SHOPIFY_API_VERSION)[1] if SHOPIFY_API_VERSION in next_url else None
                    break
    
    return all_products


def _extract_sku_from_product(product: dict) -> str:
    """Extract SKU from Shopify product (from variant or tags)."""
    # Try variant SKU first
    variants = product.get("variants", [])
    if variants and variants[0].get("sku"):
        return variants[0]["sku"]
    
    # Fall back to product handle with JAKS- prefix
    handle = product.get("handle", "")
    if handle:
        return f"JAKS-{handle}".upper()
    
    return ""


def _extract_category_from_product(product: dict) -> str:
    """Extract category from product type or tags."""
    product_type = product.get("product_type", "")
    if product_type:
        return product_type
    
    # Try to extract from tags
    tags = product.get("tags", "")
    if "Engine Parts" in tags:
        return "engine_parts"
    elif "Rebuild Kit" in tags or "Kit" in tags:
        return "engine_rebuild_kits"
    
    return "uncategorized"


def _extract_manufacturer_from_product(product: dict) -> str:
    """Extract manufacturer from vendor or tags."""
    vendor = product.get("vendor", "")
    if vendor and vendor.lower() not in ["jaks", "jaks heavy duty"]:
        return vendor
    
    # Try to extract from tags
    tags = product.get("tags", "")
    manufacturers = ["Caterpillar", "Cummins", "Detroit", "International", "Navistar", "PAI"]
    for mfr in manufacturers:
        if mfr.lower() in tags.lower():
            return mfr
    
    return ""


def _parse_price(price_str: str | None) -> float:
    """Parse price string to float."""
    if not price_str:
        return 0.0
    try:
        return float(str(price_str).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def sync_from_shopify(progress_callback=None) -> dict:
    """
    Sync all products FROM Shopify INTO the local database.
    
    This fetches all products from Shopify and upserts them into the DB.
    Products not in Shopify will NOT be deleted (they may be drafts).
    
    Args:
        progress_callback: Optional callback(current, total, message) for UI updates
    
    Returns:
        dict with stats: {synced: int, created: int, updated: int, errors: int}
    """
    if not ACCESS_TOKEN:
        logger.error("SHOPIFY_ACCESS_TOKEN not set")
        return {"synced": 0, "created": 0, "updated": 0, "errors": 1, "error": "Missing API token"}
    
    stats = {"synced": 0, "created": 0, "updated": 0, "errors": 0, "products": []}
    
    # Fetch all products from Shopify
    if progress_callback:
        progress_callback(0, 100, "Fetching products from Shopify...")
    
    products = fetch_all_shopify_products()
    total = len(products)
    
    if not products:
        return {"synced": 0, "created": 0, "updated": 0, "errors": 0, "error": "No products found"}
    
    conn = get_connection()
    now = datetime.now().isoformat()
    
    # Check which columns exist in the products table to be safe
    try:
        from db.database import is_postgresql
        if is_postgresql():
            col_rows = conn.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'products'
            """).fetchall()
            existing_columns = {r[0] if isinstance(r, tuple) else r.get('column_name') for r in col_rows}
        else:
            col_rows = conn.execute("PRAGMA table_info(products)").fetchall()
            existing_columns = {r[1] if isinstance(r, tuple) else r.get('name') for r in col_rows}
    except Exception:
        existing_columns = set()  # If we can't check, try all columns
    
    for i, product in enumerate(products):
        if progress_callback:
            progress_callback(i + 1, total, f"Syncing {product.get('title', 'Unknown')[:40]}...")
        
        try:
            shopify_id = product.get("id")
            variant = product.get("variants", [{}])[0]
            shopify_variant_id = variant.get("id")
            shopify_inventory_item_id = variant.get("inventory_item_id")
            
            sku = _extract_sku_from_product(product)
            if not sku:
                logger.warning(f"Skipping product {shopify_id} - no SKU")
                continue
            
            # Prepare product data - only include columns that exist
            product_data = {
                "sku": sku,
                "handle": product.get("handle", ""),
                "title": product.get("title", ""),
                "category": _extract_category_from_product(product),
                "manufacturer": _extract_manufacturer_from_product(product),
                "description_html": product.get("body_html", ""),
                "selling_price": _parse_price(variant.get("price")),
                "compare_at_price": _parse_price(variant.get("compare_at_price")),
                "image_count": len(product.get("images", [])),
                "shopify_id": shopify_id,
                "shopify_variant_id": shopify_variant_id,
                "shopify_inventory_item_id": shopify_inventory_item_id,
                "shopify_synced_at": now,
                "needs_shopify_update": False,
                "updated_at": now,
            }
            
            # Add optional columns only if they exist in the schema
            if not existing_columns or "shopify_status" in existing_columns:
                product_data["shopify_status"] = product.get("status", "draft")
            if not existing_columns or "shopify_published_at" in existing_columns:
                product_data["shopify_published_at"] = product.get("published_at")
            
            # Check if product exists
            existing = conn.execute(
                "SELECT id, sku FROM products WHERE shopify_id = %s OR sku = %s",
                (shopify_id, sku)
            ).fetchone()
            
            if existing:
                # Update existing product
                product_id = existing[0] if isinstance(existing, (list, tuple)) else existing["id"]
                set_clause = ", ".join(f"{k} = %s" for k in product_data.keys() if k != "sku")
                values = [v for k, v in product_data.items() if k != "sku"]
                values.append(product_id)
                
                conn.execute(f"UPDATE products SET {set_clause} WHERE id = %s", values)
                stats["updated"] += 1
            else:
                # Insert new product
                product_data["created_at"] = now
                cols = ", ".join(product_data.keys())
                placeholders = ", ".join("%s" for _ in product_data)
                
                conn.execute(
                    f"INSERT INTO products ({cols}) VALUES ({placeholders})",
                    list(product_data.values())
                )
                stats["created"] += 1
            
            stats["synced"] += 1
            stats["products"].append(sku)
            
            # Commit after each successful product to avoid losing all progress on error
            conn.commit()
            
        except Exception as e:
            logger.exception(f"Error syncing product {product.get('id')}: {e}")
            stats["errors"] += 1
            # Rollback and continue with next product
            try:
                conn.rollback()
            except Exception:
                pass
    
    # Final commit (may be redundant but ensures all changes are saved)
    try:
        conn.commit()
    except Exception:
        pass
    
    if progress_callback:
        progress_callback(total, total, f"Sync complete: {stats['synced']} products")
    
    logger.info(f"Shopify sync complete: {stats}")
    return stats


def push_product_to_shopify(sku: str) -> dict:
    """
    Push a single product's changes from DB to Shopify.
    
    Only updates fields that can be changed via API:
    - title, body_html, vendor, product_type, tags
    - variant price, compare_at_price, sku
    
    Returns:
        dict with status: {success: bool, error: str | None}
    """
    conn = get_connection()
    
    # Get product from DB
    row = conn.execute(
        "SELECT * FROM products WHERE sku = %s",
        (sku,)
    ).fetchone()
    
    if not row:
        return {"success": False, "error": f"Product {sku} not found in database"}
    
    # Convert to dict
    product = dict(row) if hasattr(row, "keys") else {
        "id": row[0], "sku": row[1], "handle": row[2], "title": row[4],
        "category": row[5], "manufacturer": row[6], "description_html": row[16],
        "selling_price": row[12], "compare_at_price": row[13],
        "shopify_id": row[21], "shopify_variant_id": row[22]
    }
    
    shopify_id = product.get("shopify_id")
    shopify_variant_id = product.get("shopify_variant_id")
    
    if not shopify_id:
        return {"success": False, "error": f"Product {sku} has no Shopify ID - upload first"}
    
    # Build update payload
    update_data = {
        "product": {
            "id": shopify_id,
            "title": product.get("title", ""),
            "body_html": product.get("description_html", ""),
            "vendor": product.get("manufacturer", ""),
            "product_type": product.get("category", ""),
        }
    }
    
    # Update variant pricing
    if shopify_variant_id:
        update_data["product"]["variants"] = [{
            "id": shopify_variant_id,
            "price": str(product.get("selling_price", 0)),
            "compare_at_price": str(product.get("compare_at_price", 0)) if product.get("compare_at_price") else None,
        }]
    
    # Make API call
    resp = _shopify_request("PUT", f"/products/{shopify_id}.json", update_data)
    
    if resp and resp.status_code == 200:
        # Clear the needs_update flag
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE products SET needs_shopify_update = FALSE, shopify_synced_at = %s, updated_at = %s WHERE sku = %s",
            (now, now, sku)
        )
        conn.commit()
        return {"success": True}
    else:
        error = resp.text if resp else "No response"
        return {"success": False, "error": f"Shopify API error: {error}"}


def push_all_pending_to_shopify(progress_callback=None) -> dict:
    """
    Push all products marked with needs_shopify_update=True to Shopify.
    
    Returns:
        dict with stats: {pushed: int, errors: int, skus: list}
    """
    conn = get_connection()
    
    # Get all products needing update
    rows = conn.execute(
        "SELECT sku FROM products WHERE needs_shopify_update = TRUE OR needs_shopify_update = 1"
    ).fetchall()
    
    stats = {"pushed": 0, "errors": 0, "skus": []}
    total = len(rows)
    
    for i, row in enumerate(rows):
        sku = row[0] if isinstance(row, (list, tuple)) else row["sku"]
        
        if progress_callback:
            progress_callback(i + 1, total, f"Pushing {sku}...")
        
        result = push_product_to_shopify(sku)
        
        if result.get("success"):
            stats["pushed"] += 1
            stats["skus"].append(sku)
        else:
            stats["errors"] += 1
            logger.error(f"Failed to push {sku}: {result.get('error')}")
    
    return stats


def get_sync_status() -> dict:
    """
    Get current sync status between DB and Shopify.
    
    Returns:
        dict with: {
            total_products: int,
            synced_products: int,
            needs_update: int,
            never_synced: int,
            last_sync: str | None
        }
    """
    conn = get_connection()
    
    # Total products in DB
    total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    
    # Products with Shopify ID (synced at least once)
    synced = conn.execute(
        "SELECT COUNT(*) FROM products WHERE shopify_id IS NOT NULL"
    ).fetchone()[0]
    
    # Products needing update
    needs_update = conn.execute(
        "SELECT COUNT(*) FROM products WHERE needs_shopify_update = TRUE OR needs_shopify_update = 1"
    ).fetchone()[0]
    
    # Products never synced from Shopify
    never_synced = conn.execute(
        "SELECT COUNT(*) FROM products WHERE shopify_synced_at IS NULL AND shopify_id IS NOT NULL"
    ).fetchone()[0]
    
    # Last sync time
    last_sync_row = conn.execute(
        "SELECT MAX(shopify_synced_at) FROM products"
    ).fetchone()
    last_sync = last_sync_row[0] if last_sync_row else None
    
    return {
        "total_products": total,
        "synced_products": synced,
        "needs_update": needs_update,
        "never_synced": never_synced,
        "last_sync": last_sync,
    }


def mark_product_for_update(sku: str) -> bool:
    """Mark a product as needing Shopify update after local edit."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE products SET needs_shopify_update = TRUE, updated_at = %s WHERE sku = %s",
            (datetime.now().isoformat(), sku)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to mark {sku} for update: {e}")
        return False


def get_shopify_products() -> list[dict]:
    """
    Get all products that have been uploaded to Shopify (have shopify_id).
    
    Used by Shopify Management Screen to show only Shopify products.
    
    Returns:
        List of product dicts with all fields.
    """
    conn = get_connection()
    
    # Use SELECT * to be compatible with different schema versions
    rows = conn.execute("""
        SELECT * FROM products 
        WHERE shopify_id IS NOT NULL
        ORDER BY updated_at DESC
    """).fetchall()
    
    products = []
    for row in rows:
        if hasattr(row, "keys"):
            products.append(dict(row))
        else:
            # For SQLite tuple format, we need column names
            # Get column names from cursor description
            cursor = conn.execute("SELECT * FROM products LIMIT 0")
            columns = [desc[0] for desc in cursor.description]
            products.append(dict(zip(columns, row)))
    
    return products


def update_product_description(sku: str, description_html: str) -> bool:
    """Update a product's description in the database."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE products SET description_html = %s, updated_at = %s WHERE sku = %s",
            (description_html, datetime.now().isoformat(), sku)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to update description for {sku}: {e}")
        return False
