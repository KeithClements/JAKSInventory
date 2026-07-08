"""Shopify-specific worker threads for background operations.

These workers handle:
- Full sync from Shopify
- Quick sync (incremental, last 24h)
- Price checking (Phase 1 + 2.5 style)
- Description regeneration via Grok API
- Bulk status changes
- Push/Pull operations
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workers.signals import Signals

logger = logging.getLogger(__name__)

__all__ = [
    "run_shopify_full_sync_thread",
    "run_shopify_quick_sync_thread",
    "run_price_check_thread",
    "run_regenerate_descriptions_shopify_thread",
    "run_bulk_status_change_thread",
    "run_push_products_thread",
    "run_pull_products_thread",
    "run_inventory_adjustment_thread",
    "run_export_products_thread",
    "run_import_products_thread",
]


def run_shopify_full_sync_thread(signals: "Signals") -> None:
    """
    Full sync from Shopify - fetches all products and updates local DB.
    
    Emits sync_status_ready signal with results.
    """
    from db.shopify_sync import sync_from_shopify
    
    try:
        signals.log.emit("Starting full Shopify sync...")
        
        def progress_cb(current, total, message):
            pct = int((current / max(total, 1)) * 100)
            signals.progress.emit(pct, message)
            signals.log.emit(message)
        
        result = sync_from_shopify(progress_callback=progress_cb)
        
        signals.log.emit(f"Full sync complete: {result.get('synced', 0)} products synced")
        signals.log.emit(f"  Created: {result.get('created', 0)}")
        signals.log.emit(f"  Updated: {result.get('updated', 0)}")
        signals.log.emit(f"  Errors: {result.get('errors', 0)}")
        
        signals.sync_status_ready.emit(result)
        signals.done.emit("Full Shopify sync complete")
        
    except Exception as e:
        logger.exception(f"Full sync error: {e}")
        signals.log.emit(f"❌ Sync error: {e}")
        signals.failed.emit(str(e))


def run_shopify_quick_sync_thread(signals: "Signals", since_hours: int = 24) -> None:
    """
    Quick/incremental sync - only products modified in last N hours.
    
    Uses Shopify's updated_at_min parameter for efficiency.
    """
    from db.shopify_sync import fetch_all_shopify_products, _shopify_request, SHOPIFY_API_VERSION
    from db.database import get_connection
    
    try:
        signals.log.emit(f"Starting quick sync (last {since_hours} hours)...")
        
        # Calculate cutoff time
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Fetch only recently modified products
        endpoint = f"/products.json?limit=250&updated_at_min={cutoff_str}"
        resp = _shopify_request("GET", endpoint)
        
        if not resp:
            signals.log.emit("❌ Failed to fetch from Shopify")
            signals.failed.emit("Failed to fetch from Shopify")
            return
        
        products = resp.json().get("products", [])
        signals.log.emit(f"Found {len(products)} products modified in last {since_hours}h")
        
        # Process each product
        conn = get_connection()
        now = datetime.now().isoformat()
        updated = 0
        
        for i, product in enumerate(products):
            signals.progress.emit(int((i / max(len(products), 1)) * 100), f"Syncing {product.get('title', '')[:30]}...")
            
            try:
                shopify_id = product.get("id")
                variant = product.get("variants", [{}])[0]
                
                # Update local DB
                conn.execute("""
                    UPDATE products SET
                        title = %s,
                        description_html = %s,
                        selling_price = %s,
                        shopify_synced_at = %s,
                        needs_shopify_update = FALSE,
                        updated_at = %s
                    WHERE shopify_id = %s
                """, (
                    product.get("title", ""),
                    product.get("body_html", ""),
                    float(variant.get("price", 0) or 0),
                    now,
                    now,
                    shopify_id,
                ))
                updated += 1
            except Exception as e:
                logger.error(f"Error updating product {product.get('id')}: {e}")
        
        conn.commit()
        
        signals.log.emit(f"✓ Quick sync complete: {updated} products updated")
        signals.progress.emit(100, "Quick sync complete")
        signals.sync_status_ready.emit({"synced": updated, "quick": True})
        signals.done.emit(f"Quick sync complete: {updated} products")
        
    except Exception as e:
        logger.exception(f"Quick sync error: {e}")
        signals.log.emit(f"❌ Quick sync error: {e}")
        signals.failed.emit(str(e))


def run_price_check_thread(signals: "Signals", skus: list[str]) -> None:
    """
    Run quick price check (Phase 1 + 2.5 ATL style) for products.
    
    Fetches current HHP listing prices and compares to local prices.
    Flags products where price differs.
    """
    import aiohttp
    from bs4 import BeautifulSoup
    from db.database import get_connection
    
    async def fetch_hhp_price(session: aiohttp.ClientSession, url: str) -> float | None:
        """Fetch price from HHP listing page."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # Try to find price element (adjust selector as needed)
                price_elem = soup.select_one(".price, .product-price, [data-price]")
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    # Extract numeric value
                    import re
                    match = re.search(r'[\d,]+\.?\d*', price_text.replace(",", ""))
                    if match:
                        return float(match.group())
        except Exception as e:
            logger.debug(f"Price fetch error for {url}: {e}")
        return None
    
    async def check_prices_async():
        conn = get_connection()
        price_changes = []
        checked = 0
        
        async with aiohttp.ClientSession() as session:
            total = len(skus)
            
            for i, sku in enumerate(skus):
                signals.progress.emit(int((i / max(total, 1)) * 100), f"Checking {sku}...")
                
                # Get product from DB
                row = conn.execute(
                    "SELECT url, selling_price FROM products WHERE sku = %s",
                    (sku,)
                ).fetchone()
                
                if not row:
                    continue
                
                url = row[0] if isinstance(row, (list, tuple)) else row.get("url")
                current_price = row[1] if isinstance(row, (list, tuple)) else row.get("selling_price", 0)
                
                if not url:
                    continue
                
                # Fetch HHP price
                hhp_price = await fetch_hhp_price(session, url)
                checked += 1
                
                if hhp_price and current_price and abs(hhp_price - current_price) > 0.01:
                    diff = hhp_price - current_price
                    price_changes.append({
                        "sku": sku,
                        "old_price": current_price,
                        "new_price": hhp_price,
                        "diff": diff,
                        "url": url,
                    })
                    signals.log.emit(f"💰 Price change: {sku} ${current_price:.2f} → ${hhp_price:.2f} ({'+' if diff > 0 else ''}{diff:.2f})")
        
        return {"checked": checked, "price_changes": price_changes}
    
    try:
        signals.log.emit(f"Starting price check for {len(skus)} products...")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(check_prices_async())
        loop.close()
        
        changes = result.get("price_changes", [])
        signals.log.emit(f"✓ Price check complete: {result.get('checked', 0)} checked, {len(changes)} changes found")
        
        if changes:
            signals.log.emit("Products with price changes:")
            for c in changes[:10]:  # Show first 10
                signals.log.emit(f"  {c['sku']}: ${c['old_price']:.2f} → ${c['new_price']:.2f}")
            if len(changes) > 10:
                signals.log.emit(f"  ... and {len(changes) - 10} more")
        
        signals.progress.emit(100, "Price check complete")
        signals.sync_status_ready.emit({"price_check": True, **result})
        signals.done.emit(f"Price check complete: {len(changes)} changes found")
        
    except Exception as e:
        logger.exception(f"Price check error: {e}")
        signals.log.emit(f"❌ Price check error: {e}")
        signals.failed.emit(str(e))


def run_regenerate_descriptions_shopify_thread(signals: "Signals", skus: list[str]) -> None:
    """
    Regenerate AI descriptions for selected products using Grok API.
    
    Updates both local DB and marks for Shopify push.
    """
    from phases.description import generate_engaging_description
    from db.database import get_connection
    from db.shopify_sync import mark_product_for_update
    
    try:
        signals.log.emit(f"Regenerating descriptions for {len(skus)} products...")
        
        conn = get_connection()
        regenerated = 0
        errors = 0
        
        for i, sku in enumerate(skus):
            signals.progress.emit(int((i / max(len(skus), 1)) * 100), f"Regenerating {sku}...")
            
            try:
                # Load product data from DB
                row = conn.execute(
                    "SELECT title, manufacturer, category, kit_contents FROM products WHERE sku = %s",
                    (sku,)
                ).fetchone()
                
                if not row:
                    signals.log.emit(f"⚠️ Product {sku} not found")
                    continue
                
                title = row[0] if isinstance(row, (list, tuple)) else row.get("title", "")
                manufacturer = row[1] if isinstance(row, (list, tuple)) else row.get("manufacturer", "")
                category = row[2] if isinstance(row, (list, tuple)) else row.get("category", "")
                kit_contents = row[3] if isinstance(row, (list, tuple)) else row.get("kit_contents", "")
                
                # Parse kit_contents if it's JSON string
                if isinstance(kit_contents, str) and kit_contents:
                    try:
                        import json
                        kit_contents = json.loads(kit_contents)
                    except:
                        kit_contents = []
                
                # Generate new description using Grok
                signals.log.emit(f"Calling Grok API for {sku}...")
                
                new_desc = generate_engaging_description(
                    kit_contents=kit_contents if isinstance(kit_contents, list) else [],
                    brand=manufacturer,
                    engine="",  # Could be enhanced to store engine info
                    dimensions="",
                    title=title,
                    xrefs=[],
                )
                
                if new_desc:
                    # Update database
                    now = datetime.now().isoformat()
                    conn.execute(
                        "UPDATE products SET description_html = %s, updated_at = %s WHERE sku = %s",
                        (new_desc, now, sku)
                    )
                    
                    # Mark for Shopify update
                    mark_product_for_update(sku)
                    
                    regenerated += 1
                    signals.log.emit(f"✓ Regenerated description for {sku}")
                else:
                    signals.log.emit(f"⚠️ Empty description returned for {sku}")
                    errors += 1
                    
            except Exception as e:
                logger.error(f"Error regenerating {sku}: {e}")
                signals.log.emit(f"❌ Error regenerating {sku}: {e}")
                errors += 1
        
        conn.commit()
        
        signals.log.emit(f"✓ Description regeneration complete: {regenerated} success, {errors} errors")
        signals.progress.emit(100, "Regeneration complete")
        signals.sync_status_ready.emit({"regenerated": regenerated, "errors": errors})
        signals.done.emit(f"Regenerated {regenerated} descriptions")
        
    except Exception as e:
        logger.exception(f"Regeneration error: {e}")
        signals.log.emit(f"❌ Regeneration error: {e}")
        signals.failed.emit(str(e))


def run_bulk_status_change_thread(signals: "Signals", skus: list[str], new_status: str) -> None:
    """
    Change Shopify status of multiple products.
    
    Valid statuses: active, draft, archived
    """
    from db.shopify_sync import _shopify_request
    from db.database import get_connection
    
    try:
        signals.log.emit(f"Changing status to '{new_status}' for {len(skus)} products...")
        
        conn = get_connection()
        changed = 0
        errors = 0
        
        for i, sku in enumerate(skus):
            signals.progress.emit(int((i / max(len(skus), 1)) * 100), f"Updating {sku}...")
            
            try:
                # Get Shopify ID
                row = conn.execute(
                    "SELECT shopify_id FROM products WHERE sku = %s",
                    (sku,)
                ).fetchone()
                
                if not row:
                    continue
                
                shopify_id = row[0] if isinstance(row, (list, tuple)) else row.get("shopify_id")
                
                if not shopify_id:
                    signals.log.emit(f"⚠️ {sku} has no Shopify ID")
                    continue
                
                # Update Shopify
                update_data = {
                    "product": {
                        "id": shopify_id,
                        "status": new_status,
                    }
                }
                
                resp = _shopify_request("PUT", f"/products/{shopify_id}.json", update_data)
                
                if resp and resp.status_code == 200:
                    # Update local DB
                    now = datetime.now().isoformat()
                    conn.execute(
                        "UPDATE products SET shopify_status = %s, shopify_synced_at = %s, updated_at = %s WHERE sku = %s",
                        (new_status, now, now, sku)
                    )
                    changed += 1
                    signals.log.emit(f"✓ {sku} → {new_status}")
                else:
                    errors += 1
                    signals.log.emit(f"❌ Failed to update {sku}")
                    
            except Exception as e:
                logger.error(f"Error changing status for {sku}: {e}")
                errors += 1
        
        conn.commit()
        
        signals.log.emit(f"✓ Status change complete: {changed} updated, {errors} errors")
        signals.progress.emit(100, "Status change complete")
        signals.sync_status_ready.emit({"status_changed": changed, "errors": errors})
        signals.done.emit(f"Changed status to '{new_status}' for {changed} products")
        
    except Exception as e:
        logger.exception(f"Status change error: {e}")
        signals.log.emit(f"❌ Status change error: {e}")
        signals.failed.emit(str(e))


def run_push_products_thread(signals: "Signals", skus: list[str]) -> None:
    """
    Push selected products to Shopify.
    """
    from db.shopify_sync import push_product_to_shopify
    
    try:
        signals.log.emit(f"Pushing {len(skus)} products to Shopify...")
        
        pushed = 0
        errors = 0
        
        for i, sku in enumerate(skus):
            signals.progress.emit(int((i / max(len(skus), 1)) * 100), f"Pushing {sku}...")
            
            result = push_product_to_shopify(sku)
            
            if result.get("success"):
                pushed += 1
                signals.log.emit(f"✓ Pushed {sku}")
            else:
                errors += 1
                signals.log.emit(f"❌ Failed to push {sku}: {result.get('error')}")
        
        signals.log.emit(f"✓ Push complete: {pushed} success, {errors} errors")
        signals.progress.emit(100, "Push complete")
        signals.sync_status_ready.emit({"pushed": pushed, "errors": errors})
        signals.done.emit(f"Pushed {pushed} products to Shopify")
        
    except Exception as e:
        logger.exception(f"Push error: {e}")
        signals.log.emit(f"❌ Push error: {e}")
        signals.failed.emit(str(e))


def run_pull_products_thread(signals: "Signals", skus: list[str]) -> None:
    """
    Pull selected products from Shopify (overwrite local with Shopify data).
    """
    from db.shopify_sync import _shopify_request
    from db.database import get_connection
    
    try:
        signals.log.emit(f"Pulling {len(skus)} products from Shopify...")
        
        conn = get_connection()
        pulled = 0
        errors = 0
        
        for i, sku in enumerate(skus):
            signals.progress.emit(int((i / max(len(skus), 1)) * 100), f"Pulling {sku}...")
            
            try:
                # Get Shopify ID
                row = conn.execute(
                    "SELECT shopify_id FROM products WHERE sku = %s",
                    (sku,)
                ).fetchone()
                
                if not row:
                    continue
                
                shopify_id = row[0] if isinstance(row, (list, tuple)) else row.get("shopify_id")
                
                if not shopify_id:
                    continue
                
                # Fetch from Shopify
                resp = _shopify_request("GET", f"/products/{shopify_id}.json")
                
                if resp and resp.status_code == 200:
                    product = resp.json().get("product", {})
                    variant = product.get("variants", [{}])[0]
                    
                    # Update local DB
                    now = datetime.now().isoformat()
                    conn.execute("""
                        UPDATE products SET
                            title = %s,
                            description_html = %s,
                            selling_price = %s,
                            compare_at_price = %s,
                            shopify_synced_at = %s,
                            needs_shopify_update = FALSE,
                            updated_at = %s
                        WHERE sku = %s
                    """, (
                        product.get("title", ""),
                        product.get("body_html", ""),
                        float(variant.get("price", 0) or 0),
                        float(variant.get("compare_at_price", 0) or 0),
                        now,
                        now,
                        sku,
                    ))
                    
                    pulled += 1
                    signals.log.emit(f"✓ Pulled {sku}")
                else:
                    errors += 1
                    signals.log.emit(f"❌ Failed to pull {sku}")
                    
            except Exception as e:
                logger.error(f"Error pulling {sku}: {e}")
                errors += 1
        
        conn.commit()
        
        signals.log.emit(f"✓ Pull complete: {pulled} success, {errors} errors")
        signals.progress.emit(100, "Pull complete")
        signals.sync_status_ready.emit({"pulled": pulled, "errors": errors})
        signals.done.emit(f"Pulled {pulled} products from Shopify")
        
    except Exception as e:
        logger.exception(f"Pull error: {e}")
        signals.log.emit(f"❌ Pull error: {e}")
        signals.failed.emit(str(e))


def run_inventory_adjustment_thread(
    signals: "Signals",
    sku: str,
    delta: int,
    txn_type: str,
    notes: str = "",
) -> None:
    """
    Adjust inventory for a product with audit trail.
    
    Args:
        signals: Worker signals
        sku: Product SKU
        delta: Quantity change (positive/negative)
        txn_type: Transaction type ('receive', 'sale', 'adjust', etc.)
        notes: Optional notes
    """
    from db.inventory import adjust_product_quantity
    
    try:
        signals.log.emit(f"Adjusting inventory for {sku}: {'+' if delta > 0 else ''}{delta} ({txn_type})")
        signals.progress.emit(50, f"Adjusting {sku}...")
        
        result = adjust_product_quantity(
            sku=sku,
            delta=delta,
            txn_type=txn_type,
            notes=notes,
            user="shopify_mgmt",
        )
        
        if result.get("success"):
            msg = f"✓ {sku}: {result['qty_before']} → {result['qty_after']}"
            signals.log.emit(msg)
            signals.progress.emit(100, "Adjustment complete")
            signals.sync_status_ready.emit({
                "adjusted": True,
                "sku": sku,
                "qty_before": result["qty_before"],
                "qty_after": result["qty_after"],
            })
            signals.done.emit(msg)
        else:
            err = result.get("error", "Unknown error")
            signals.log.emit(f"❌ Adjustment failed: {err}")
            signals.failed.emit(err)
            
    except Exception as e:
        logger.exception(f"Inventory adjustment error: {e}")
        signals.log.emit(f"❌ Inventory adjustment error: {e}")
        signals.failed.emit(str(e))


def run_export_products_thread(
    signals: "Signals",
    fmt: str,
    skus: list[str],
    output_dir: str = "output",
) -> None:
    """
    Export products to CSV or JSON.
    
    Args:
        signals: Worker signals
        fmt: Format ('csv' or 'json')
        skus: List of SKUs to export (empty = all)
        output_dir: Where to save the export
    """
    import csv
    import json
    from pathlib import Path
    from db.database import get_connection
    
    try:
        signals.log.emit(f"Exporting products to {fmt.upper()}...")
        signals.progress.emit(10, "Fetching products...")
        
        conn = get_connection()
        
        if skus:
            placeholders = ",".join(["%s"] * len(skus))
            rows = conn.execute(
                f"SELECT * FROM products WHERE sku IN ({placeholders})",
                tuple(skus)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM products WHERE shopify_id IS NOT NULL").fetchall()
        
        if not rows:
            signals.log.emit("No products to export")
            signals.failed.emit("No products found")
            return
        
        products = [dict(r) if hasattr(r, 'keys') else dict(enumerate(r)) for r in rows]
        signals.log.emit(f"Found {len(products)} products to export")
        signals.progress.emit(50, f"Writing {len(products)} products...")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        
        if fmt == "csv":
            filepath = out_path / f"shopify_export_{timestamp}.csv"
            
            # Select key fields for CSV export
            export_fields = [
                "sku", "handle", "title", "category", "manufacturer",
                "selling_price", "compare_at_price", "cost", "qty_on_hand",
                "shopify_id", "shopify_status", "shopify_synced_at",
            ]
            
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=export_fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(products)
                
        elif fmt == "json":
            filepath = out_path / f"shopify_export_{timestamp}.json"
            
            # Convert datetime objects to strings
            for p in products:
                for k, v in p.items():
                    if hasattr(v, 'isoformat'):
                        p[k] = v.isoformat()
            
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(products, f, indent=2, default=str)
        else:
            signals.failed.emit(f"Unknown format: {fmt}")
            return
        
        signals.log.emit(f"✓ Exported {len(products)} products to {filepath}")
        signals.progress.emit(100, "Export complete")
        signals.sync_status_ready.emit({"exported": len(products), "filepath": str(filepath)})
        signals.done.emit(f"Exported {len(products)} products to {filepath.name}")
        
    except Exception as e:
        logger.exception(f"Export error: {e}")
        signals.log.emit(f"❌ Export error: {e}")
        signals.failed.emit(str(e))


def run_import_products_thread(signals: "Signals", file_path: str) -> None:
    """
    Import product updates from CSV or JSON file.
    
    Updates existing products only (by SKU match).
    
    Args:
        signals: Worker signals
        file_path: Path to CSV or JSON file
    """
    import csv
    import json
    from pathlib import Path
    from db.database import get_connection
    
    try:
        filepath = Path(file_path)
        signals.log.emit(f"Importing from {filepath.name}...")
        signals.progress.emit(10, "Reading file...")
        
        if not filepath.exists():
            signals.failed.emit(f"File not found: {file_path}")
            return
        
        ext = filepath.suffix.lower()
        
        if ext == ".csv":
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                records = list(reader)
        elif ext == ".json":
            with open(filepath, "r", encoding="utf-8") as f:
                records = json.load(f)
                if not isinstance(records, list):
                    records = [records]
        else:
            signals.failed.emit(f"Unsupported file type: {ext}")
            return
        
        if not records:
            signals.failed.emit("No records found in file")
            return
        
        signals.log.emit(f"Found {len(records)} records to import")
        signals.progress.emit(30, f"Processing {len(records)} records...")
        
        conn = get_connection()
        updated = 0
        skipped = 0
        errors = 0
        
        # Fields that can be updated via import
        updatable_fields = {
            "title", "selling_price", "compare_at_price", "cost",
            "qty_on_hand", "shopify_status", "category", "manufacturer",
        }
        
        for i, record in enumerate(records):
            sku = record.get("sku")
            if not sku:
                skipped += 1
                continue
            
            pct = 30 + int((i / max(len(records), 1)) * 60)
            signals.progress.emit(pct, f"Updating {sku}...")
            
            try:
                # Check if product exists
                existing = conn.execute(
                    "SELECT id FROM products WHERE sku = %s", (sku,)
                ).fetchone()
                
                if not existing:
                    skipped += 1
                    continue
                
                # Build update query from available fields
                updates = []
                values = []
                
                for field in updatable_fields:
                    if field in record and record[field] not in ("", None):
                        updates.append(f"{field} = %s")
                        val = record[field]
                        # Convert numeric fields
                        if field in ("selling_price", "compare_at_price", "cost"):
                            try:
                                val = float(val)
                            except:
                                continue
                        elif field == "qty_on_hand":
                            try:
                                val = int(val)
                            except:
                                continue
                        values.append(val)
                
                if updates:
                    updates.append("updated_at = %s")
                    updates.append("needs_shopify_update = TRUE")
                    values.append(datetime.now().isoformat())
                    values.append(sku)  # for WHERE clause
                    
                    query = f"UPDATE products SET {', '.join(updates)} WHERE sku = %s"
                    conn.execute(query, tuple(values))
                    updated += 1
                else:
                    skipped += 1
                    
            except Exception as e:
                logger.error(f"Error updating {sku}: {e}")
                errors += 1
        
        conn.commit()
        
        msg = f"✓ Import complete: {updated} updated, {skipped} skipped, {errors} errors"
        signals.log.emit(msg)
        signals.progress.emit(100, "Import complete")
        signals.sync_status_ready.emit({
            "imported": updated,
            "skipped": skipped,
            "errors": errors,
        })
        signals.done.emit(f"Imported {updated} products from {filepath.name}")
        
    except Exception as e:
        logger.exception(f"Import error: {e}")
        signals.log.emit(f"❌ Import error: {e}")
        signals.failed.emit(str(e))
