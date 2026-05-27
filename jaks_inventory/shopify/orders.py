"""Shopify integration module for JAKS Inventory.

Provides functions for:
- Fetching orders from Shopify
- Auto-deducting inventory on fulfilled orders
- Syncing inventory levels to Shopify
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

import requests

# Shopify configuration
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "jaks-diesel-3")
API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-01")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")


def _shopify_request(
    method: str,
    endpoint: str,
    data: dict | None = None,
) -> requests.Response | None:
    """Make authenticated request to Shopify Admin API."""
    if not ACCESS_TOKEN:
        return None
    
    url = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/{API_VERSION}{endpoint}"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    
    try:
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        elif method.upper() == "POST":
            resp = requests.post(url, headers=headers, json=data, timeout=30)
        elif method.upper() == "PUT":
            resp = requests.put(url, headers=headers, json=data, timeout=30)
        else:
            return None
        
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"Shopify API error: {e}")
        return None


def fetch_shopify_orders(
    status: str = "any",
    fulfillment_status: str = "any",
    since_date: datetime | None = None,
    limit: int = 50,
    progress_callback: Callable[[int, str], None] | None = None,
) -> list[dict]:
    """Fetch orders from Shopify.
    
    Args:
        status: Order status filter (any, open, closed, cancelled)
        fulfillment_status: Fulfillment filter (any, shipped, partial, unshipped, unfulfilled)
        since_date: Only fetch orders created after this date
        limit: Max orders per page
        progress_callback: Optional callback for progress updates
        
    Returns:
        List of order dicts with line items
    """
    if not ACCESS_TOKEN:
        return []
    
    all_orders: list[dict] = []
    params = [f"limit={min(limit, 250)}", f"status={status}"]
    
    if fulfillment_status != "any":
        params.append(f"fulfillment_status={fulfillment_status}")
    
    if since_date:
        iso_date = since_date.strftime("%Y-%m-%dT%H:%M:%S-00:00")
        params.append(f"created_at_min={iso_date}")
    
    endpoint: str | None = f"/orders.json?{'&'.join(params)}"
    page = 0
    
    while endpoint:
        page += 1
        if progress_callback:
            progress_callback(page, f"Fetching orders page {page}...")
        
        resp = _shopify_request("GET", endpoint)
        if not resp:
            break
        
        try:
            json_data = resp.json() or {}
        except Exception:
            json_data = {}
        
        orders = json_data.get("orders", []) or []
        all_orders.extend(orders)
        
        # Handle pagination
        link_header = resp.headers.get("Link", "")
        if link_header:
            links = requests.utils.parse_header_links(link_header)
            endpoint = None
            for link in links:
                if link.get("rel") == "next" and link.get("url"):
                    try:
                        endpoint = str(link["url"]).split("/admin/api", 1)[1]
                    except Exception:
                        endpoint = None
                    break
        else:
            endpoint = None
    
    return all_orders


def get_order_details(order_id: int | str) -> dict | None:
    """Get full details for a single order."""
    if not ACCESS_TOKEN:
        return None
    
    resp = _shopify_request("GET", f"/orders/{order_id}.json")
    if resp:
        try:
            return resp.json().get("order")
        except Exception:
            pass
    return None


def parse_order_for_inventory(order: dict) -> list[dict]:
    """Parse Shopify order into inventory deduction records.
    
    Args:
        order: Shopify order dict
        
    Returns:
        List of dicts with: sku, quantity, line_item_id, title, price
    """
    deductions = []
    
    for line_item in order.get("line_items", []):
        sku = (line_item.get("sku") or "").strip()
        if not sku:
            continue
        
        quantity = line_item.get("quantity", 0)
        if quantity <= 0:
            continue
        
        deductions.append({
            "sku": sku,
            "quantity": quantity,
            "line_item_id": line_item.get("id"),
            "title": line_item.get("title", ""),
            "price": Decimal(str(line_item.get("price", 0))),
            "variant_id": line_item.get("variant_id"),
        })
    
    return deductions


def process_shopify_order(
    order: dict,
    auto_deduct: bool = True,
) -> dict[str, Any]:
    """Process a Shopify order - optionally deduct inventory.
    
    Args:
        order: Shopify order dict
        auto_deduct: Whether to auto-deduct inventory
        
    Returns:
        Result dict with: success, order_id, order_number, deductions, errors
    """
    from db import get_product_by_sku, adjust_product_quantity
    
    order_id = order.get("id")
    order_number = order.get("order_number") or order.get("name", "")
    
    result = {
        "success": True,
        "order_id": order_id,
        "order_number": order_number,
        "deductions": [],
        "errors": [],
        "skipped": [],
    }
    
    deductions = parse_order_for_inventory(order)
    
    for item in deductions:
        sku = item["sku"]
        qty = item["quantity"]
        
        try:
            product = get_product_by_sku(sku)
            if not product:
                result["skipped"].append({
                    "sku": sku,
                    "reason": "SKU not found in inventory",
                })
                continue
            
            if auto_deduct:
                adjust_product_quantity(
                    product_id=product["id"],
                    delta=-qty,
                    transaction_type="sale",
                    reference=f"Shopify #{order_number}",
                    notes=f"Auto-deduct from Shopify order {order_id}",
                )
            
            result["deductions"].append({
                "sku": sku,
                "quantity": qty,
                "product_id": product["id"],
                "title": item["title"],
            })
            
        except Exception as e:
            result["errors"].append({
                "sku": sku,
                "error": str(e),
            })
            result["success"] = False
    
    return result


def sync_inventory_to_shopify(
    products: list[dict],
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Push inventory levels to Shopify.
    
    Args:
        products: List of product dicts with sku, qty_on_hand
        progress_callback: Optional callback for progress updates
        
    Returns:
        Result dict with: success, updated, errors
    """
    if not ACCESS_TOKEN:
        return {"success": False, "error": "No Shopify access token"}
    
    result = {
        "success": True,
        "updated": [],
        "not_found": [],
        "errors": [],
    }
    
    # First, get location ID (required for inventory API)
    location_id = _get_primary_location_id()
    if not location_id:
        return {"success": False, "error": "Could not get Shopify location ID"}
    
    total = len(products)
    for idx, product in enumerate(products):
        sku = product.get("sku", "")
        qty = product.get("qty_on_hand", 0) or 0
        
        if progress_callback:
            progress_callback(idx + 1, f"Syncing {sku} ({idx + 1}/{total})")
        
        try:
            # Find inventory item by SKU
            inventory_item_id = _find_inventory_item_by_sku(sku)
            if not inventory_item_id:
                result["not_found"].append(sku)
                continue
            
            # Set inventory level
            success = _set_inventory_level(location_id, inventory_item_id, qty)
            if success:
                result["updated"].append({"sku": sku, "qty": qty})
            else:
                result["errors"].append({"sku": sku, "error": "Failed to update"})
                
        except Exception as e:
            result["errors"].append({"sku": sku, "error": str(e)})
            result["success"] = False
    
    return result


def _get_primary_location_id() -> int | None:
    """Get the primary Shopify location ID."""
    resp = _shopify_request("GET", "/locations.json")
    if resp:
        try:
            locations = resp.json().get("locations", [])
            for loc in locations:
                if loc.get("active"):
                    return loc.get("id")
        except Exception:
            pass
    return None


def _find_inventory_item_by_sku(sku: str) -> int | None:
    """Find Shopify inventory_item_id by SKU."""
    # Search products by SKU
    resp = _shopify_request("GET", f"/products.json?fields=id,variants&limit=250")
    if resp:
        try:
            products = resp.json().get("products", [])
            for p in products:
                for v in p.get("variants", []):
                    if (v.get("sku") or "").strip().upper() == sku.strip().upper():
                        return v.get("inventory_item_id")
        except Exception:
            pass
    return None


def _set_inventory_level(location_id: int, inventory_item_id: int, qty: int) -> bool:
    """Set inventory level for an item at a location."""
    data = {
        "location_id": location_id,
        "inventory_item_id": inventory_item_id,
        "available": qty,
    }
    resp = _shopify_request("POST", "/inventory_levels/set.json", data)
    return resp is not None and resp.status_code == 200


def get_unfulfilled_orders(days_back: int = 30) -> list[dict]:
    """Get unfulfilled orders from the last N days."""
    since = datetime.now() - timedelta(days=days_back)
    return fetch_shopify_orders(
        status="open",
        fulfillment_status="unfulfilled",
        since_date=since,
    )


def get_recent_orders(days_back: int = 7) -> list[dict]:
    """Get all orders from the last N days."""
    since = datetime.now() - timedelta(days=days_back)
    return fetch_shopify_orders(
        status="any",
        fulfillment_status="any",
        since_date=since,
    )
