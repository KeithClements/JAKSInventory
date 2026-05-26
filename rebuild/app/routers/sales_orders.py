"""
app/routers/sales_orders.py
============================
Sales Order lifecycle — list, detail, fulfill, hold, cancel, deposit.
All mutations delegate to SalesOrderService. No business logic here.

Primary creation path: Quote → SO via QuoteService.convert_to_sales_order().
Direct SO creation is also supported for non-quote orders.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import PaymentMethod, SOPaymentMode, SOStatus
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.quote import SalesOrder
from app.services.sales_order_service import SalesOrderService

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


# ── New SO ────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_so_form(request: Request, db: Session = Depends(get_db)):
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    return templates.TemplateResponse(
        "sales_orders/new.html",
        {
            "request": request,
            "customers": customers,
            "SOPaymentMode": SOPaymentMode,
        },
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
            "lines": [],   # lines added on detail page
        },
    )
    return RedirectResponse(f"/sales-orders/{so.id}", status_code=303)


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{so_id}", response_class=HTMLResponse)
async def so_detail(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        "sales_orders/detail.html",
        {
            "request": request,
            "so": so,
            "SOStatus": SOStatus,
            "SOPaymentMode": SOPaymentMode,
            "PaymentMethod": PaymentMethod,
        },
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
            f"/sales-orders/{so_id}?error=no_qty",
            status_code=303,
        )

    svc = SalesOrderService(db, user_id)
    invoice = svc.fulfill_and_invoice(so_id, line_quantities)
    return RedirectResponse(f"/invoices/{invoice.id}", status_code=303)


# ── Hold / Release ────────────────────────────────────────────────────────────

@router.post("/{so_id}/hold")
async def hold_order(
    so_id: int,
    hold_reason: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    SalesOrderService(db, user_id).hold_order(so_id, hold_reason or "On hold")
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/release-hold")
async def release_hold(
    so_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    SalesOrderService(db, user_id).release_hold(so_id)
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.post("/{so_id}/cancel")
async def cancel_order(
    so_id: int,
    cancel_reason: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    SalesOrderService(db, user_id).cancel_order(so_id, cancel_reason or "Cancelled")
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
    SalesOrderService(db, user_id).collect_deposit(so_id, amount, payment_method)
    return RedirectResponse(f"/sales-orders/{so_id}?saved=1", status_code=303)
