"""QBO Bill creation - Create bills from received purchase orders.

A Bill in QBO represents an invoice from a vendor (money you owe).
When you receive goods against a PO, you create a Bill to record the payable.

⚠️ IMPORTANT: QBO writes are disabled by default. Set QBO_WRITE_ENABLED=1 to enable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from datetime import date

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from qbo.client import QBOClient, get_qbo_client
from db import get_purchase_order, get_product_by_id, update_po_status

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


def create_qbo_bill(
    vendor_name: str,
    vendor_qbo_id: str = None,
    line_items: list[dict] = None,
    bill_date: str = None,
    due_date: str = None,
    ref_number: str = None,
    memo: str = None,
    *,
    source_id: int | None = None,
) -> dict[str, Any]:
    """Create a Bill in QuickBooks Online.
    
    Args:
        vendor_name: Vendor name (for display)
        vendor_qbo_id: QBO Vendor ID (if known)
        line_items: List of line items, each with:
            - qbo_item_id: QBO Item ID (optional)
            - sku: Product SKU (used if no qbo_item_id)
            - description: Line description
            - quantity: Qty
            - unit_cost: Cost per unit
            - amount: Line total (qty * unit_cost)
        bill_date: Bill date (YYYY-MM-DD)
        due_date: Due date (YYYY-MM-DD)
        ref_number: Reference/PO number
        memo: Notes
    
    Returns:
        {"success": True, "qbo_bill_id": "123"} or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    if not vendor_qbo_id and not vendor_name:
        return {"success": False, "error": "Vendor is required"}
    
    if not line_items:
        return {"success": False, "error": "At least one line item is required"}
    
    from qbo import sync_log
    request_preview = {
        "VendorRef": {"value": vendor_qbo_id, "name": vendor_name},
        "DocNumber": ref_number,
        "TxnDate": bill_date,
        "DueDate": due_date,
        "PrivateNote": memo,
        "Line": line_items,
    }
    log_id = sync_log.start_log(
        source_type="bill", source_id=source_id,
        qbo_object_type="Bill",
        request_json=sync_log.to_json_text(request_preview),
    )

    client = _get_client()
    
    if client.is_mock_mode:
        # Mock mode: simulate successful bill creation
        mock_id = f"MOCK-BILL-{ref_number or 'NEW'}"
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": mock_id, "_mock": True}),
            qbo_object_id=mock_id,
        )
        return {"success": True, "qbo_bill_id": mock_id, "mock": True}
    
    try:
        from quickbooks.objects.bill import Bill, BillLine
        from quickbooks.objects.detailline import ItemBasedExpenseLineDetail
        
        bill = Bill()
        
        # Set vendor reference
        if vendor_qbo_id:
            bill.VendorRef = {"value": vendor_qbo_id, "name": vendor_name}
        else:
            # Try to find vendor by name
            from quickbooks.objects.vendor import Vendor
            vendors = Vendor.filter(DisplayName=vendor_name, qb=client._client)
            if vendors:
                bill.VendorRef = {"value": str(vendors[0].Id), "name": vendor_name}
            else:
                sync_log.complete_log(log_id, status="failed",
                    error_message=f"Vendor '{vendor_name}' not found in QBO")
                return {"success": False, "error": f"Vendor '{vendor_name}' not found in QBO"}
        
        # Set dates
        bill.TxnDate = bill_date or date.today().isoformat()
        if due_date:
            bill.DueDate = due_date
        
        # Set reference and memo
        if ref_number:
            bill.DocNumber = ref_number[:21]  # QBO limit
        if memo:
            bill.PrivateNote = memo[:4000]
        
        # Build line items
        bill.Line = []
        for item_data in line_items:
            line = BillLine()
            line.DetailType = "ItemBasedExpenseLineDetail"
            
            detail = ItemBasedExpenseLineDetail()
            
            # Get QBO item ID
            qbo_item_id = item_data.get("qbo_item_id")
            if not qbo_item_id and item_data.get("sku"):
                # Look up by SKU
                qbo_item = client.search_by_sku(item_data["sku"])
                if qbo_item:
                    qbo_item_id = qbo_item["id"]
            
            if qbo_item_id:
                detail.ItemRef = {"value": qbo_item_id}
            
            detail.Qty = item_data.get("quantity", 1)
            detail.UnitPrice = item_data.get("unit_cost", 0)
            
            line.ItemBasedExpenseLineDetail = detail
            line.Amount = item_data.get("amount", detail.Qty * detail.UnitPrice)
            line.Description = item_data.get("description", "")
            
            bill.Line.append(line)
        
        result = bill.save(qb=client._client)
        sync_log.update_request(log_id, request_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(bill)))
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(result)),
            qbo_object_id=str(result.Id),
        )
        return {"success": True, "qbo_bill_id": str(result.Id)}
        
    except Exception as e:
        sync_log.complete_log(log_id, status="failed", error_message=f"{type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


def create_bill_from_po(po_id: int) -> dict[str, Any]:
    """Create a QBO Bill from a received Purchase Order.
    
    Args:
        po_id: Local purchase_orders.id
    
    Returns:
        {"success": True, "qbo_bill_id": "123"} or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    # 1. Get PO from local DB
    po = get_purchase_order(po_id)
    if not po:
        return {"success": False, "error": f"PO {po_id} not found"}
    
    # Check PO status - should be received or partial
    if po.get("status") not in ("received", "partial"):
        return {"success": False, "error": f"PO status must be 'received' or 'partial', got '{po.get('status')}'"}
    
    # Check if bill already created
    if po.get("qbo_bill_id"):
        return {"success": False, "error": f"Bill already exists: {po['qbo_bill_id']}"}
    
    # 2. Get vendor info
    vendor_name = po.get("vendor_name", "Unknown Vendor")
    vendor_qbo_id = po.get("qbo_vendor_id")
    
    # 3. Build line items
    lines = po.get("lines", [])
    if not lines:
        return {"success": False, "error": "PO has no line items"}

    # Core handling overhaul (migration 108): build lookup maps so the
    # bill respects the new schema —
    #   * ``core_children`` maps parent PO line id → child ``line_kind='core'``
    #     row (per-unit deposit stored on the child's unit_cost).
    #   * ``ob_status_by_pol`` lets us honor the receiving-time decision
    #     ("vendor did not charge", etc.) recorded on
    #     ``vendor_core_obligations`` so we never bill a deposit the user
    #     already wrote off.
    core_children: dict[int, dict] = {}
    for ln in lines:
        if str(ln.get("line_kind") or "").lower() == "core":
            parent_id = ln.get("parent_line_id")
            if parent_id is not None:
                core_children[int(parent_id)] = ln

    ob_status_by_pol: dict[int, str] = {}
    try:
        from db import list_vendor_core_obligations
        for ob in list_vendor_core_obligations(po_id=po_id):
            pol_id = ob.get("po_line_id")
            if pol_id is not None:
                ob_status_by_pol[int(pol_id)] = str(ob.get("status") or "pending").lower()
    except Exception:
        ob_status_by_pol = {}

    line_items = []
    for line in lines:
        # Skip child ``core`` rows — they are folded into the parent's
        # ``core_charge`` below and re-emitted by
        # ``expand_bill_lines_with_cores``.
        if str(line.get("line_kind") or "").lower() == "core":
            continue

        # Only include lines with received quantity
        qty_received = int(line.get("quantity_received") or 0)
        if qty_received <= 0:
            continue
        
        # Get product info
        product = get_product_by_id(line.get("product_id"))

        # Determine the effective per-unit vendor core deposit for this
        # bill line.
        line_id = line.get("id")
        legacy_core = float(line.get("core_charge") or 0)
        child = core_children.get(int(line_id) if line_id is not None else -1)
        effective_core: float = legacy_core
        if child is not None:
            child_pol_id = int(child.get("id") or 0)
            status = ob_status_by_pol.get(child_pol_id, "pending")
            if status in ("written_off", "rejected"):
                effective_core = 0.0
            else:
                # Per-unit deposit is stored on the child line's unit_cost.
                effective_core = float(child.get("unit_cost") or 0)
        else:
            # No migration-108 child row — fall back to the legacy inline
            # field but still respect a write-off recorded on a directly
            # attached obligation (defensive).
            status = ob_status_by_pol.get(int(line_id) if line_id is not None else -1, "")
            if status in ("written_off", "rejected"):
                effective_core = 0.0

        line_items.append({
            "sku": product.get("sku") if product else None,
            "qbo_item_id": product.get("qbo_item_id") if product else None,
            "description": product.get("title", "") if product else line.get("description", ""),
            "quantity": qty_received,
            "unit_cost": float(line.get("unit_cost") or 0),
            "amount": qty_received * float(line.get("unit_cost") or 0),
            # P3 — carry the vendor-side per-unit core deposit so
            # ``expand_bill_lines_with_cores`` can emit a separate bill
            # line that posts to Vendor Core Receivable (asset). The
            # part line itself remains a clean Inventory Asset hit.
            "core_charge": effective_core,
        })

    if not line_items:
        return {"success": False, "error": "No received items to bill"}

    # P3 — split each PO line into (part line + vendor-core-deposit line)
    # so the bill's accounting symmetry mirrors the invoice side.
    try:
        from qbo.core_charges import expand_bill_lines_with_cores
        line_items = expand_bill_lines_with_cores(line_items)
    except Exception:
        # Vendor-core expansion is best-effort; fall back to the raw
        # part-line list so a misconfigured QBO item never blocks the
        # bill itself from being created.
        pass
    
    # 4. Create the bill (idempotent + retry/backoff — QBO.11).
    from qbo._retry import idempotent_write

    def _do_create():
        r = create_qbo_bill(
            vendor_name=vendor_name,
            vendor_qbo_id=vendor_qbo_id,
            line_items=line_items,
            bill_date=po.get("order_date"),
            ref_number=po.get("po_number"),
            memo=f"From PO #{po.get('po_number')}",
        )
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "QBO bill create failed")
        return r.get("qbo_bill_id")

    outcome = idempotent_write(
        entity_type="bill",
        local_ref=f"po:{po_id}",
        fn=_do_create,
    )

    # 5. Surface result
    if outcome.get("success"):
        return {
            "success": True,
            "qbo_bill_id": outcome.get("qbo_id"),
            "po_number": po.get("po_number"),
            "total": sum(item["amount"] for item in line_items),
            "action": outcome.get("action"),
        }
    return {"success": False, "error": outcome.get("error")}
