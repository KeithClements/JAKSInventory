"""QBO Item sync - Create/update inventory items in QuickBooks Online.

This module handles syncing products from the local database to QBO Items.

⚠️ IMPORTANT: QBO writes are disabled by default. Set QBO_WRITE_ENABLED=1 to enable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from qbo.client import QBOClient, get_qbo_client
from db import get_product_by_sku, update_product

# QBO.9 — central toggle; evaluated at CALL time, not import time.
from qbo.config import is_write_enabled as _is_write_enabled


def _check_write_enabled() -> bool:
    """Check if QBO writes are enabled, warn if not."""
    if not _is_write_enabled():
        print("⚠️ QBO writes disabled. Enable in Settings → QBO or set QBO_WRITE_ENABLED=1.")
        return False
    return True


def _get_client() -> QBOClient:
    """Get connected QBO client."""
    client = get_qbo_client()
    if not client.is_connected:
        client.connect()
    return client


def create_qbo_item(
    sku: str,
    name: str,
    description: str = "",
    sales_price: float = 0.0,
    purchase_cost: float = 0.0,
    qty_on_hand: int = 0,
    item_type: str = "Inventory",
    income_account_id: str | None = None,
    expense_account_id: str | None = None,
    asset_account_id: str | None = None,
) -> dict[str, Any]:
    """Create a new Item in QuickBooks Online.
    
    Args:
        sku: Product SKU (becomes QBO SKU field)
        name: Product name/title
        description: Product description
        sales_price: Selling price
        purchase_cost: Cost (for COGS)
        qty_on_hand: Initial quantity
        item_type: "Inventory" or "NonInventory"
        income_account_id: QBO Income account ID (None → resolved from
            ``qbo.config.get_account_mapping()['sales_income']``)
        expense_account_id: QBO Expense/COGS account ID (None → resolved
            from ``qbo.config.get_account_mapping()['cogs']``)
        asset_account_id: QBO Inventory Asset account ID (None →
            resolved from ``qbo.config.get_account_mapping()['inventory_asset']``)
    
    Returns:
        {"success": True, "qbo_id": "123"} or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    # Validate inputs
    if not sku or not name:
        return {"success": False, "error": "SKU and name are required"}

    # D2 — resolve account ids from app_settings when not explicitly passed.
    if income_account_id is None or expense_account_id is None or asset_account_id is None:
        try:
            from qbo.config import get_account_mapping
            mapping = get_account_mapping()
        except Exception:
            mapping = {"sales_income": "1", "cogs": "2", "inventory_asset": "3"}
        if income_account_id is None:
            income_account_id = mapping.get("sales_income") or "1"
        if expense_account_id is None:
            expense_account_id = mapping.get("cogs") or "2"
        if asset_account_id is None:
            asset_account_id = mapping.get("inventory_asset") or "3"

    client = _get_client()
    
    if client.is_mock_mode:
        # Mock mode: simulate successful creation
        mock_id = f"MOCK-{sku}"
        return {"success": True, "qbo_id": mock_id, "mock": True}
    
    try:
        from quickbooks.objects.item import Item
        
        item = Item()
        item.Name = name[:100]  # QBO name limit
        item.Sku = sku
        item.Description = description[:4000] if description else ""
        item.Type = item_type
        item.UnitPrice = sales_price
        item.PurchaseCost = purchase_cost
        item.QtyOnHand = qty_on_hand
        item.TrackQtyOnHand = item_type == "Inventory"
        
        # Account refs (required for inventory items)
        if item_type == "Inventory":
            item.IncomeAccountRef = {"value": income_account_id}
            item.ExpenseAccountRef = {"value": expense_account_id}
            item.AssetAccountRef = {"value": asset_account_id}
        else:
            item.IncomeAccountRef = {"value": income_account_id}
            item.ExpenseAccountRef = {"value": expense_account_id}
        
        result = item.save(qb=client._client)
        return {"success": True, "qbo_id": str(result.Id)}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def update_qbo_item(
    qbo_id: str,
    name: str = None,
    description: str = None,
    sales_price: float = None,
    purchase_cost: float = None,
    qty_on_hand: int = None,
) -> dict[str, Any]:
    """Update an existing Item in QuickBooks Online.
    
    Args:
        qbo_id: QBO Item ID
        name: New name (optional)
        description: New description (optional)
        sales_price: New price (optional)
        purchase_cost: New cost (optional)
        qty_on_hand: New quantity (optional) - USE WITH CAUTION
    
    Returns:
        {"success": True} or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    if not qbo_id:
        return {"success": False, "error": "QBO Item ID is required"}
    
    client = _get_client()
    
    if client.is_mock_mode:
        return {"success": True, "mock": True}
    
    try:
        from quickbooks.objects.item import Item
        
        item = Item.get(qbo_id, qb=client._client)
        
        if name is not None:
            item.Name = name[:100]
        if description is not None:
            item.Description = description[:4000]
        if sales_price is not None:
            item.UnitPrice = sales_price
        if purchase_cost is not None:
            item.PurchaseCost = purchase_cost
        if qty_on_hand is not None:
            item.QtyOnHand = qty_on_hand
        
        item.save(qb=client._client)
        return {"success": True}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def sync_product_to_qbo(sku: str) -> dict[str, Any]:
    """Sync a single product from local DB to QBO.
    
    - If product doesn't exist in QBO: creates it
    - If product exists: updates cost/price if changed
    
    Args:
        sku: Product SKU
    
    Returns:
        {"success": True, "action": "created"|"updated"|"unchanged", "qbo_id": str}
        or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    # 1. Get product from local DB
    product = get_product_by_sku(sku)
    if not product:
        return {"success": False, "error": f"Product {sku} not found in local DB"}
    
    client = _get_client()
    
    # 2. Search QBO for matching SKU
    qbo_item = client.search_by_sku(sku)
    
    if qbo_item is None:
        # 3. Not found: create new item (idempotent + retry/backoff — QBO.11).
        from qbo._retry import idempotent_write

        def _do_create():
            r = create_qbo_item(
                sku=sku,
                name=product.get("title", sku)[:100],
                description=product.get("description", "")[:4000],
                sales_price=float(product.get("selling_price") or 0),
                purchase_cost=float(product.get("cost") or 0),
                qty_on_hand=int(product.get("qty_on_hand") or 0),
                item_type="Inventory",
            )
            if not r.get("success"):
                raise RuntimeError(r.get("error") or "QBO item create failed")
            return r.get("qbo_id")

        outcome = idempotent_write(
            entity_type="item",
            local_ref=f"sku:{sku}",
            fn=_do_create,
        )
        if outcome.get("success"):
            qbo_id = outcome.get("qbo_id")
            if qbo_id:
                try:
                    update_product(product["id"], qbo_item_id=qbo_id)
                except Exception:
                    pass
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": qbo_id,
            }
        return {"success": False, "error": outcome.get("error")}
    else:
        # 4. Found: compare and update if changed
        qbo_id = qbo_item["id"]
        local_price = float(product.get("selling_price") or 0)
        local_cost = float(product.get("cost") or 0)
        qbo_price = float(qbo_item.get("price") or 0)
        qbo_cost = float(qbo_item.get("cost") or 0)
        
        needs_update = (
            abs(local_price - qbo_price) > 0.01 or
            abs(local_cost - qbo_cost) > 0.01
        )
        
        if needs_update:
            result = update_qbo_item(
                qbo_id=qbo_id,
                sales_price=local_price,
                purchase_cost=local_cost,
            )
            if result.get("success"):
                return {"success": True, "action": "updated", "qbo_id": qbo_id}
            else:
                return result
        else:
            return {"success": True, "action": "unchanged", "qbo_id": qbo_id}


def sync_qty_to_qbo(sku: str, qty: int) -> dict[str, Any]:
    """Update QBO item quantity to match local.
    
    Args:
        sku: Product SKU
        qty: New quantity
    
    Returns:
        {"success": True} or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    client = _get_client()
    qbo_item = client.search_by_sku(sku)
    
    if not qbo_item:
        return {"success": False, "error": f"Item {sku} not found in QBO"}
    
    return update_qbo_item(qbo_id=qbo_item["id"], qty_on_hand=qty)


def bulk_sync_to_qbo(skus: list[str], progress_callback=None) -> dict[str, Any]:
    """Sync multiple products to QBO.
    
    Args:
        skus: List of SKUs to sync
        progress_callback: Optional callback(current, total, sku)
    
    Returns:
        {"success": True, "created": int, "updated": int, "unchanged": int, "errors": list}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    results = {"created": 0, "updated": 0, "unchanged": 0, "errors": []}
    total = len(skus)
    
    for i, sku in enumerate(skus):
        if progress_callback:
            progress_callback(i + 1, total, sku)
        
        result = sync_product_to_qbo(sku)
        
        if result.get("success"):
            action = result.get("action", "unchanged")
            if action in results:
                results[action] += 1
        else:
            results["errors"].append({"sku": sku, "error": result.get("error")})
    
    results["success"] = True
    return results


def bulk_sync_products_to_qbo(skus: list[str], progress_callback=None) -> dict[str, Any]:
    """Sync multiple products to QBO.
    
    Args:
        skus: List of SKUs to sync
        progress_callback: Optional callback(current, total)
    
    Returns:
        {"created": int, "updated": int, "unchanged": int, "errors": int}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    # TODO: Implement batch sync
    raise NotImplementedError("QBO bulk sync not yet implemented. Coming in Phase E.")
