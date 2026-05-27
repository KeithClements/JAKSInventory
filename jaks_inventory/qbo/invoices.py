"""QBO Invoice creation - Create invoices from sales.

An Invoice in QBO represents money owed to you by a customer.
When you sell goods, you create an Invoice to record the receivable.

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
from db import get_invoice, get_customer, get_product_by_id

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


def create_qbo_invoice(
    customer_name: str,
    customer_qbo_id: str = None,
    line_items: list[dict] = None,
    invoice_date: str = None,
    due_date: str = None,
    invoice_number: str = None,
    memo: str = None,
    email: str = None,
    *,
    source_id: int | None = None,
) -> dict[str, Any]:
    """Create an Invoice in QuickBooks Online.
    
    Args:
        customer_name: Customer name (for display)
        customer_qbo_id: QBO Customer ID (if known)
        line_items: List of line items, each with:
            - qbo_item_id: QBO Item ID (optional)
            - sku: Product SKU (used if no qbo_item_id)
            - description: Line description
            - quantity: Qty sold
            - unit_price: Price per unit
            - amount: Line total (qty * unit_price)
        invoice_date: Invoice date (YYYY-MM-DD)
        due_date: Due date (YYYY-MM-DD)
        invoice_number: Invoice number
        memo: Customer-visible memo
        email: Customer email (for sending invoice)
    
    Returns:
        {"success": True, "qbo_invoice_id": "123"} or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    if not customer_qbo_id and not customer_name:
        return {"success": False, "error": "Customer is required"}
    
    if not line_items:
        return {"success": False, "error": "At least one line item is required"}
    
    from qbo import sync_log
    request_preview = {
        "CustomerRef": {"value": customer_qbo_id, "name": customer_name},
        "DocNumber": invoice_number,
        "TxnDate": invoice_date,
        "DueDate": due_date,
        "CustomerMemo": memo,
        "BillEmail": email,
        "Line": line_items,
    }
    log_id = sync_log.start_log(
        source_type="invoice", source_id=source_id,
        qbo_object_type="Invoice",
        request_json=sync_log.to_json_text(request_preview),
    )

    client = _get_client()
    
    if client.is_mock_mode:
        # Mock mode: simulate successful invoice creation
        mock_id = f"MOCK-INV-{invoice_number or 'NEW'}"
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": mock_id, "_mock": True}),
            qbo_object_id=mock_id,
        )
        return {"success": True, "qbo_invoice_id": mock_id, "mock": True}
    
    try:
        from quickbooks.objects.invoice import Invoice
        from quickbooks.objects.detailline import SalesItemLine, SalesItemLineDetail
        
        invoice = Invoice()
        
        # Set customer reference
        if customer_qbo_id:
            invoice.CustomerRef = {"value": customer_qbo_id, "name": customer_name}
        else:
            # Try to find customer by name
            from quickbooks.objects.customer import Customer
            customers = Customer.filter(DisplayName=customer_name, qb=client._client)
            if customers:
                invoice.CustomerRef = {"value": str(customers[0].Id), "name": customer_name}
            else:
                sync_log.complete_log(log_id, status="failed",
                    error_message=f"Customer '{customer_name}' not found in QBO")
                return {"success": False, "error": f"Customer '{customer_name}' not found in QBO"}
        
        # Set dates
        invoice.TxnDate = invoice_date or date.today().isoformat()
        if due_date:
            invoice.DueDate = due_date
        
        # Set reference number and memo
        if invoice_number:
            invoice.DocNumber = invoice_number[:21]  # QBO limit
        if memo:
            invoice.CustomerMemo = {"value": memo[:1000]}
        if email:
            invoice.BillEmail = {"Address": email}
        
        # Build line items
        invoice.Line = []
        for item_data in line_items:
            line = SalesItemLine()
            line.DetailType = "SalesItemLineDetail"
            
            detail = SalesItemLineDetail()
            
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
            detail.UnitPrice = item_data.get("unit_price", 0)
            
            line.SalesItemLineDetail = detail
            line.Amount = item_data.get("amount", detail.Qty * detail.UnitPrice)
            line.Description = item_data.get("description", "")
            
            invoice.Line.append(line)
        
        result = invoice.save(qb=client._client)
        sync_log.update_request(log_id, request_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(invoice)))
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(result)),
            qbo_object_id=str(result.Id),
        )
        return {"success": True, "qbo_invoice_id": str(result.Id)}
        
    except Exception as e:
        sync_log.complete_log(log_id, status="failed", error_message=f"{type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


def create_invoice_from_sale(invoice_id: int) -> dict[str, Any]:
    """Create a QBO Invoice from a local invoice record.
    
    Args:
        invoice_id: Local invoices.id
    
    Returns:
        {"success": True, "qbo_invoice_id": "123"} or {"success": False, "error": str}
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    # 1. Get invoice from local DB
    invoice = get_invoice(invoice_id)
    if not invoice:
        return {"success": False, "error": f"Invoice {invoice_id} not found"}
    
    # Check if already synced
    if invoice.get("qbo_invoice_id"):
        return {"success": False, "error": f"Already synced: {invoice['qbo_invoice_id']}"}
    
    # 2. Get customer info
    customer_id = invoice.get("customer_id")
    customer = get_customer(customer_id) if customer_id else None
    
    if not customer:
        return {"success": False, "error": "Invoice has no customer"}
    
    customer_name = customer.get("name", "Unknown")
    customer_qbo_id = customer.get("qbo_customer_id")
    customer_email = customer.get("email")
    
    # 3. Build line items
    lines = invoice.get("lines", [])
    if not lines:
        return {"success": False, "error": "Invoice has no line items"}
    
    line_items = []
    for line in lines:
        product = get_product_by_id(line.get("product_id")) if line.get("product_id") else None

        line_items.append({
            "sku": line.get("sku") or (product.get("sku") if product else None),
            "qbo_item_id": product.get("qbo_item_id") if product else None,
            "description": line.get("description") or (product.get("title") if product else ""),
            "quantity": int(line.get("quantity") or 1),
            "unit_price": float(line.get("unit_price") or 0),
            "amount": float(line.get("line_total") or 0),
            # QBO.10 — preserve per-unit core charge so the expander below
            # can emit a dedicated Core Charge line per sold unit.
            "core_charge": float(line.get("core_charge") or 0),
        })

    # QBO.10 — split out core charges as separate line items.
    from qbo.core_charges import expand_invoice_lines_with_cores
    line_items = expand_invoice_lines_with_cores(line_items)
    
    # 4. Create the QBO invoice (idempotent + retry/backoff — QBO.11).
    from qbo._retry import idempotent_write

    def _do_create():
        r = create_qbo_invoice(
            customer_name=customer_name,
            customer_qbo_id=customer_qbo_id,
            line_items=line_items,
            invoice_date=str(invoice.get("invoice_date"))[:10] if invoice.get("invoice_date") else None,
            due_date=str(invoice.get("due_date"))[:10] if invoice.get("due_date") else None,
            invoice_number=invoice.get("invoice_number"),
            memo=invoice.get("notes"),
            email=customer_email,
        )
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "QBO invoice create failed")
        return r.get("qbo_invoice_id")

    outcome = idempotent_write(
        entity_type="invoice",
        local_ref=str(invoice_id),
        fn=_do_create,
    )

    # 5. Surface result
    if outcome.get("success"):
        return {
            "success": True,
            "qbo_invoice_id": outcome.get("qbo_id"),
            "invoice_number": invoice.get("invoice_number"),
            "action": outcome.get("action"),
        }
    return {"success": False, "error": outcome.get("error")}


def sync_invoice_from_shopify(shopify_order_id: str) -> dict[str, Any]:
    """Create a QBO Invoice from a Shopify order.
    
    Args:
        shopify_order_id: Shopify order ID
    
    Returns:
        {"success": True, "qbo_invoice_id": "123"} or {"success": False, "error": str}
    
    TODO: Implement Shopify→QBO invoice sync
    """
    if not _check_write_enabled():
        return {"success": False, "error": "QBO writes disabled"}
    
    # TODO: Implement
    # 1. Fetch Shopify order details
    # 2. Find/create QBO customer
    # 3. Map line items to QBO Item IDs
    # 4. Create invoice
    # 5. Mark as paid if already paid in Shopify
    
    raise NotImplementedError("Shopify→QBO Invoice not yet implemented. Coming in Phase F.")
