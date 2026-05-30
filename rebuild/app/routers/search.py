"""
app/routers/search.py
======================
Global search endpoints.
  GET /search          — HTMX header dropdown (products + customers only).
  GET /search/overlay  — JSON endpoint for Ctrl+K overlay (all 7 entity types).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.deps import get_current_user_id, get_db
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


@router.get("", response_class=HTMLResponse)
async def global_search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    HTMX endpoint — legacy header dropdown (kept for compatibility).
    Returns an HTML partial rendered into the results dropdown div.
    """
    q = q.strip()
    if len(q) < 2:
        return HTMLResponse("")

    svc = SearchService(db, user_id)
    products = svc.search_products(q, limit=6)
    customers = svc.search_customers(q, limit=4)

    return templates.TemplateResponse(
        request,
        "search/_results_dropdown.html",
        {
            "q": q,
            "products": products,
            "customers": customers,
        },
    )


@router.get("/overlay", response_class=JSONResponse)
async def overlay_search(
    q: str = "",
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    JSON endpoint for the Ctrl+K full-screen overlay.
    Returns grouped results across all 7 entity types.
    """
    q = q.strip()
    if len(q) < 2:
        return {"groups": []}

    svc = SearchService(db, user_id)

    products = svc.search_products(q, limit=5)
    customers = svc.search_customers(q, limit=4)
    invoices = svc.search_invoices(q, limit=4)
    quotes = svc.search_quotes(q, limit=4)
    sales_orders = svc.search_sales_orders(q, limit=4)
    vendors = svc.search_vendors(q, limit=4)
    purchase_orders = svc.search_purchase_orders(q, limit=4)

    groups = []

    if products:
        groups.append({
            "key": "products",
            "label": "Products",
            "items": [
                {
                    "url": f"/products/{p.product_id}",
                    "label": p.part_number,
                    "sub": p.description,
                    "meta": "Out" if p.qty_on_hand <= 0 else f"{p.qty_on_hand} ea",
                    "meta_class": "text-red-500" if p.qty_on_hand <= 0 else "text-gray-400",
                    "badge": f"↳ {p.cross_ref_number}" if p.match_type == "cross_ref" and p.cross_ref_number else None,
                }
                for p in products
            ],
        })

    if customers:
        groups.append({
            "key": "customers",
            "label": "Customers",
            "items": [
                {
                    "url": f"/customers/{c['id']}",
                    "label": c["company_name"],
                    "sub": c["contact_name"] or "",
                    "meta": c["phone"] or "",
                    "meta_class": "text-gray-400",
                    "badge": None,
                }
                for c in customers
            ],
        })

    if invoices:
        groups.append({
            "key": "invoices",
            "label": "Invoices",
            "items": [
                {
                    "url": f"/invoices/{i['id']}",
                    "label": i["invoice_number"],
                    "sub": f"${i['total']:.2f}" if i.get("total") else "",
                    "meta": i["status"].upper(),
                    "meta_class": _inv_status_class(i["status"]),
                    "badge": None,
                }
                for i in invoices
            ],
        })

    if quotes:
        groups.append({
            "key": "quotes",
            "label": "Quotes",
            "items": [
                {
                    "url": f"/quotes/{qt['id']}",
                    "label": qt["quote_number"],
                    "sub": qt["customer_name"],
                    "meta": qt["status"].upper(),
                    "meta_class": "text-gray-500",
                    "badge": None,
                }
                for qt in quotes
            ],
        })

    if sales_orders:
        groups.append({
            "key": "sales_orders",
            "label": "Sales Orders",
            "items": [
                {
                    "url": f"/sales-orders/{so['id']}",
                    "label": so["so_number"],
                    "sub": so["customer_name"],
                    "meta": so["status"].upper(),
                    "meta_class": "text-gray-500",
                    "badge": None,
                }
                for so in sales_orders
            ],
        })

    if vendors:
        groups.append({
            "key": "vendors",
            "label": "Vendors",
            "items": [
                {
                    "url": f"/vendors/{v['id']}",
                    "label": v["name"],
                    "sub": v["vendor_code"] or "",
                    "meta": v["phone"] or "",
                    "meta_class": "text-gray-400",
                    "badge": None,
                }
                for v in vendors
            ],
        })

    if purchase_orders:
        groups.append({
            "key": "purchase_orders",
            "label": "Purchase Orders",
            "items": [
                {
                    "url": f"/purchase-orders/{po['id']}",
                    "label": po["po_number"],
                    "sub": po["vendor_name"],
                    "meta": po["status"].upper(),
                    "meta_class": "text-gray-500",
                    "badge": None,
                }
                for po in purchase_orders
            ],
        })

    return {"groups": groups}


def _inv_status_class(status: str) -> str:
    return {
        "paid": "text-green-600",
        "open": "text-blue-600",
        "partial": "text-amber-600",
        "void": "text-red-500",
        "draft": "text-gray-400",
    }.get(status, "text-gray-500")
