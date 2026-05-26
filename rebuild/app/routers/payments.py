"""
app/routers/payments.py
========================
Payment list and detail views.
Payments are created via POST /invoices/{id}/payment (InvoiceService routes).
This router only provides read views for payment history.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import PaymentMethod, PaymentStatus
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.invoice import Payment

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def payment_list(
    request: Request,
    status: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    from sqlalchemy import or_
    query = db.query(Payment).join(Customer)

    if status:
        query = query.filter(Payment.status == status)
    if q:
        query = query.filter(
            or_(
                Customer.company_name.ilike(f"%{q}%"),
                Payment.check_number.ilike(f"%{q}%"),
            )
        )

    payments = query.order_by(Payment.payment_date.desc()).limit(300).all()

    return templates.TemplateResponse(
        "payments/list.html",
        {
            "request": request,
            "payments": payments,
            "status_filter": status,
            "q": q,
            "PaymentStatus": PaymentStatus,
            "PaymentMethod": PaymentMethod,
        },
    )


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{payment_id}", response_class=HTMLResponse)
def payment_detail(
    payment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    pmt = db.query(Payment).filter(Payment.id == payment_id).first()
    if not pmt:
        return RedirectResponse("/payments/", status_code=303)

    active_allocations = [a for a in pmt.allocations if not a.is_reversed]

    return templates.TemplateResponse(
        "payments/detail.html",
        {
            "request": request,
            "payment": pmt,
            "active_allocations": active_allocations,
            "PaymentStatus": PaymentStatus,
            "PaymentMethod": PaymentMethod,
        },
    )


# ── Reverse ───────────────────────────────────────────────────────────────────

@router.post("/{payment_id}/reverse", response_class=RedirectResponse)
async def payment_reverse(
    payment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.payment_service import PaymentService

    form = await request.form()
    reason = str(form.get("reason", "")).strip() or "reversed"

    try:
        PaymentService(db, current_user_id=user_id).reverse_payment(payment_id, reason)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error reversing payment %s", payment_id)
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote('Unexpected error — payment was not reversed.')}",
            status_code=303,
        )

    return RedirectResponse(f"/payments/{payment_id}?saved=1", status_code=303)
