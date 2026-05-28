"""
app/routers/sales_orders.py
============================
Sales Order workspace — HTMX-powered, modeled after the invoice workspace.
All mutations delegate to SalesOrderService. No business logic here.

Primary creation path: customer-picker slide-over → POST /new → 303 → workspace.
Also: Quote → SO via QuoteService.convert_to_sales_order().

Workspace pattern:
  - GET  /sales-orders/{id}          → workspace.html
  - GET  /sales-orders/new           → slide-over content fragment (_new_picker.html)
  - POST /sales-orders/new           → create SO, 303 → /sales-orders/{id}
  - POST /sales-orders/{id}/header   → update header fields, returns updated header strip
  - POST /sales-orders/{id}/lines    → add line, returns _lines_section.html
  - POST /sales-orders/{id}/lines/{line_id}        → update line
  - POST /sales-orders/{id}/lines/{line_id}/delete → cancel/remove line
  - POST /sales-orders/{id}/fulfill  → fulfill & invoice (full-page redirect)
  - POST /sales-orders/{id}/hold, release-hold, cancel, collect-deposit
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import LineType, PaymentMethod, SOPaymentMode, SOStatus
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.services.sales_order_service import SalesOrderService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales-orders", tags=["sales_orders"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_so_or_404(db: Session, so_id: int) -> SalesOrder:
    so = db.query(SalesOrder).filter(SalesOrder.id == so_id).first()
    if so is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return so


def _workspace_ctx(request: Request, so: SalesOrder) -> dict:
    editable = so.status in (SOStatus.OPEN, SOStatus.PARTIAL, SOStatus.HOLD)
    can_fulfill = so.status in (SOStatus.OPEN, SOStatus.PARTIAL)
    return {
        "request": request,
        "so": so,
        "editable": editable,
        "can_fulfill": can_fulfill,
        "SOStatus": SOStatus,
        "SOPaymentMode": SOPaymentMode,
        "PaymentMethod": PaymentMethod,
        "LineType": LineType,
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def list_sales_orders(
    request: Request,
    status: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    from sqlalchemy import or_
    query = db.query(SalesOrder).join(Customer)
    if status:
        query = query.filter(SalesOrder.status == status)
    if q:
        query = query.filter(
            or_(
                SalesOrder.so_number.ilike(f"%{q}%"),
                Customer.company_name.ilike(f"%{q}%"),
                SalesOrder.customer_po_number.ilike(f"%{q}%"),
                SalesOrder.esn.ilike(f"%{q}%"),
            )
        )
    orders = query.order_by(SalesOrder.created_at.desc()).limit(150).all()
    return templates.TemplateResponse(
        "sales_orders/list.html",
        {
            "request": request,
            "orders": orders,
            "status_filter": status,
            "q": q,
            "SOStatus": SOStatus,
        },
    )


# ── New SO — customer picker slide-over ──────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_so_picker(request: Request, db: Session = Depends(get_db)):
    """Slide-over content fragment for 'New SO'. Returns just the picker form."""
    hx = request.headers.get("HX-Request")
    if hx:
        return templates.TemplateResponse(
            "sales_orders/_new_picker.html",
            {"request": request, "SOPaymentMode": SOPaymentMode},
        )
    # Direct navigation: render a full page wrapping the picker
    return templates.TemplateResponse(
        "sales_orders/new.html",
        {"request": request, "SOPaymentMode": SOPaymentMode},
    )


@router.post("/new")
async def create_so(
    customer_id: int = Form(...),
    payment_mode: str = Form(SOPaymentMode.NONE),
    customer_po_number: str = Form(""),
    esn: str = Form(""),
    engine_manufacturer: str = Form(""),
    engine_model: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    svc = SalesOrderService(db, user_id)
    so = svc.create_sales_order(
        customer_id=customer_id,
        payment_mode=payment_mode,
        data={
            "customer_po_number": customer_po_number or None,
            "esn": esn or None,
            "engine_manufacturer": engine_manufacturer or None,
            "engine_model": engine_model or None,
            "notes": notes,
            "lines": [],
        },
    )
    return RedirectResponse(f"/sales-orders/{so.id}", status_code=303)


# ── Workspace ─────────────────────────────────────────────────────────────────

@router.get("/{so_id}", response_class=HTMLResponse)
async def so_workspace(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        "sales_orders/workspace.html",
        _workspace_ctx(request, so),
    )


# ── Header update ─────────────────────────────────────────────────────────────

@router.post("/{so_id}/header", response_class=HTMLResponse)
async def so_update_header(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    so = _get_so_or_404(db, so_id)
    if so.status == SOStatus.CANCELLED:
        return HTMLResponse('<div class="text-xs text-red-600">Order is cancelled</div>', status_code=400)

    form = await request.form()
    updatable = [
        "customer_po_number", "customer_job_number", "esn",
        "engine_manufacturer", "engine_model",
        "notes", "internal_notes", "payment_mode",
    ]
    for field in updatable:
        if field in form:
            val = str(form.get(field, "")).strip()
            setattr(so, field, val or None if field in ("customer_po_number", "customer_job_number", "esn") else val)

    db.commit()
    return HTMLResponse("", status_code=204)


# ── Line CRUD (HTMX) ──────────────────────────────────────────────────────────

@router.post("/{so_id}/lines", response_class=HTMLResponse)
async def so_add_line(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    so = _get_so_or_404(db, so_id)
    if so.status not in (SOStatus.OPEN, SOStatus.PARTIAL):
        return HTMLResponse('<div class="text-xs text-red-600">Cannot add lines to a non-open SO</div>', status_code=400)

    form = await request.form()
    pid_raw = str(form.get("product_id", "")).strip()
    product_id = int(pid_raw) if pid_raw else None

    qty_raw = str(form.get("qty_ordered", "1")).strip()
    price_raw = str(form.get("unit_price", "0")).strip()
    cost_raw = str(form.get("unit_cost", "0")).strip()
    desc = str(form.get("description", "")).strip()

    # Auto-fill description and price from product if not supplied
    if product_id and not desc:
        p = db.query(Product).filter(Product.id == product_id).first()
        if p:
            desc = p.description or p.part_number or ""
            if not price_raw or price_raw == "0":
                price_raw = str(p.selling_price or 0.0)
            if not cost_raw or cost_raw == "0":
                cost_raw = str(p.cost or 0.0)

    data = {
        "description": desc,
        "qty_ordered": max(1, int(qty_raw)) if qty_raw else 1,
        "unit_price": float(price_raw) if price_raw else 0.0,
        "unit_cost": float(cost_raw) if cost_raw else 0.0,
        "line_type": str(form.get("line_type", LineType.PRODUCT)).strip() or LineType.PRODUCT,
    }

    try:
        SalesOrderService(db, user_id).add_line(so_id, product_id, data)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600 p-2">{exc}</div>', status_code=400)

    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        "sales_orders/_lines_section.html",
        _workspace_ctx(request, so),
    )


@router.post("/{so_id}/lines/{line_id}", response_class=HTMLResponse)
async def so_update_line(
    so_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    data: dict = {}
    if "description" in form:
        data["description"] = str(form.get("description", "")).strip()
    if "qty_ordered" in form:
        raw = str(form.get("qty_ordered", "")).strip()
        if raw:
            data["qty_ordered"] = max(1, int(raw))
    if "unit_price" in form:
        raw = str(form.get("unit_price", "")).strip()
        if raw:
            data["unit_price"] = float(raw)
    if "discount_pct" in form:
        raw = str(form.get("discount_pct", "")).strip()
        data["discount_pct"] = float(raw) if raw else 0.0

    try:
        SalesOrderService(db, user_id).update_line(line_id, data)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600 p-2">{exc}</div>', status_code=400)

    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        "sales_orders/_lines_section.html",
        _workspace_ctx(request, so),
    )


@router.post("/{so_id}/lines/{line_id}/delete", response_class=HTMLResponse)
async def so_delete_line(
    so_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).cancel_line(line_id)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600 p-2">{exc}</div>', status_code=400)

    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        "sales_orders/_lines_section.html",
        _workspace_ctx(request, so),
    )


# ── Product search (HTMX typeahead) ──────────────────────────────────────────

@router.get("/_/product-search", response_class=HTMLResponse)
def so_product_search(request: Request, q: str = "", db: Session = Depends(get_db)):
    from sqlalchemy import or_
    results: list[Product] = []
    if q and len(q) >= 2:
        results = (
            db.query(Product)
            .filter(
                Product.is_active == True,  # noqa: E712
                or_(
                    Product.sku.ilike(f"%{q}%"),
                    Product.part_number.ilike(f"%{q}%"),
                    Product.description.ilike(f"%{q}%"),
                ),
            )
            .limit(12)
            .all()
        )
    return templates.TemplateResponse(
        "sales_orders/_product_search_results.html",
        {"request": request, "results": results, "q": q},
    )


# ── Fulfillment ───────────────────────────────────────────────────────────────

@router.post("/{so_id}/fulfill")
async def fulfill_and_invoice(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Fulfill specified quantities per line and create an invoice.
    Form fields: line_qty_{line_id} = qty_to_ship for each line.
    Redirects to the new invoice on success.
    """
    form = await request.form()
    line_quantities: dict[int, int] = {}
    for key, value in form.items():
        if key.startswith("line_qty_") and value:
            try:
                line_id = int(key.removeprefix("line_qty_"))
                qty = int(value)
                if qty > 0:
                    line_quantities[line_id] = qty
            except (ValueError, TypeError):
                pass

    if not line_quantities:
        return RedirectResponse(
            f"/sales-orders/{so_id}?error=Select+quantities+to+ship",
            status_code=303,
        )

    try:
        svc = SalesOrderService(db, user_id)
        invoice = svc.fulfill_and_invoice(so_id, line_quantities)
    except ValueError as exc:
        return RedirectResponse(
            f"/sales-orders/{so_id}?error={str(exc)}",
            status_code=303,
        )

    return RedirectResponse(f"/invoices/{invoice.id}?ok=Created+from+SO+{so_id}", status_code=303)


# ── Hold / Release ────────────────────────────────────────────────────────────

@router.post("/{so_id}/hold")
async def hold_order(
    so_id: int,
    hold_reason: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).hold_order(so_id, hold_reason or "On hold")
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={exc}", status_code=303)
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/release-hold")
async def release_hold(
    so_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).release_hold(so_id)
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={exc}", status_code=303)
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.post("/{so_id}/cancel")
async def cancel_order(
    so_id: int,
    cancel_reason: str = Form(""),
    deposit_resolution: str = Form("leave_open"),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).cancel_order(
            so_id,
            reason=cancel_reason or "Cancelled",
            deposit_resolution=deposit_resolution or None,
        )
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={str(exc)}", status_code=303)
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


# ── Deposit ───────────────────────────────────────────────────────────────────

@router.post("/{so_id}/collect-deposit")
async def collect_deposit(
    so_id: int,
    amount: float = Form(...),
    payment_method: str = Form(PaymentMethod.CHECK),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).collect_deposit(so_id, amount, payment_method)
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={str(exc)}", status_code=303)
    return RedirectResponse(f"/sales-orders/{so_id}?ok=Deposit+recorded", status_code=303)
