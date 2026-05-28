"""
app/routers/payments.py
========================
Payment list, detail, new-payment form, and reverse.
New payments can be created from /payments/new (multi-invoice) or
from the invoice detail page (single invoice).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import InvoiceStatus, PaymentMethod, PaymentStatus
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── New Payment (multi-invoice) ───────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def new_payment_form(
    request: Request,
    customer_id: int | None = None,
    db: Session = Depends(get_db),
):
    """
    Step 1 — no customer_id: show customer search box + list.
    Step 1b — customer_id provided: show their open invoices for selection.
    """
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    selected_customer = None
    open_invoices: list[Invoice] = []
    if customer_id:
        selected_customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if selected_customer:
            open_invoices = (
                db.query(Invoice)
                .filter(
                    Invoice.customer_id == customer_id,
                    Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
                )
                .order_by(Invoice.due_date.asc().nullslast(), Invoice.created_at.asc())
                .all()
            )
    return templates.TemplateResponse(
        "payments/new.html",
        {
            "request": request,
            "customers": customers,
            "selected_customer": selected_customer,
            "open_invoices": open_invoices,
            "today": date.today(),
            "PaymentMethod": PaymentMethod,
        },
    )


@router.post("/new", response_class=RedirectResponse)
async def create_payment(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Create a payment and allocate to one or more invoices.
    If no invoices are checked, the payment sits as unapplied customer credit.
    """
    from app.services.payment_service import PaymentService

    form = await request.form()

    # ── Step 1 redirect: user just selected a customer ──────────────────────
    if "select_customer" in form:
        customer_id = str(form.get("customer_id", "")).strip()
        return RedirectResponse(
            f"/payments/new?customer_id={customer_id}" if customer_id else "/payments/new",
            status_code=303,
        )

    # ── Step 2: record the payment ──────────────────────────────────────────
    try:
        customer_id = int(str(form.get("customer_id", "0")))
        amount = float(str(form.get("amount", "0")))
        if amount <= 0:
            raise ValueError("Payment amount must be greater than zero.")

        payment_method = str(form.get("method", PaymentMethod.CASH))
        raw_date = str(form.get("payment_date", "")).strip()
        try:
            pmt_date = datetime.strptime(raw_date, "%Y-%m-%d") if raw_date else datetime.utcnow()
        except ValueError:
            pmt_date = datetime.utcnow()

        data = {
            "check_number": str(form.get("check_number", "")).strip() or None,
            "notes": str(form.get("notes", "")).strip(),
            "payment_date": pmt_date,
        }

        # Collect checked invoice IDs (multi-value form field)
        invoice_ids: list[int] = []
        raw_ids = form.getlist("invoice_ids")
        for raw in raw_ids:
            try:
                invoice_ids.append(int(raw))
            except (ValueError, TypeError):
                pass

        pmt = PaymentService(db, user_id).record_payment(
            customer_id=customer_id,
            amount_received=amount,
            payment_method=payment_method,
            data=data,
            invoice_ids=invoice_ids or None,
        )
    except ValueError as exc:
        db.rollback()
        cid = str(form.get("customer_id", ""))
        return RedirectResponse(
            f"/payments/new?customer_id={cid}&error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating payment")
        return RedirectResponse(
            f"/payments/new?error={url_quote('Unexpected error — payment was not recorded.')}",
            status_code=303,
        )

    return RedirectResponse(f"/payments/{pmt.id}?saved=1", status_code=303)


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


# ── NSF ───────────────────────────────────────────────────────────────────────

@router.post("/{payment_id}/nsf", response_class=RedirectResponse)
async def payment_nsf(
    payment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Mark payment as NSF (non-sufficient funds / returned check):
    reverses the payment and creates an NSF-fee invoice.
    """
    from app.services.payment_service import PaymentService

    form = await request.form()
    try:
        nsf_fee = float(str(form.get("nsf_fee", "35")).strip() or "35")
    except (ValueError, TypeError):
        nsf_fee = 35.0

    try:
        PaymentService(db, current_user_id=user_id).process_nsf(payment_id, nsf_fee)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error processing NSF for payment %s", payment_id)
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote('Unexpected error — NSF was not processed.')}",
            status_code=303,
        )

    return RedirectResponse(f"/payments/{payment_id}?saved=1", status_code=303)


# ── Allocate ──────────────────────────────────────────────────────────────────

@router.post("/{payment_id}/allocate", response_class=RedirectResponse)
async def payment_allocate(
    payment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Manually allocate (part of) a payment to a specific invoice."""
    from app.services.payment_service import PaymentService

    form = await request.form()
    try:
        invoice_id = int(str(form.get("invoice_id", "0")).strip())
        amount = float(str(form.get("amount", "0")).strip())
        if invoice_id <= 0 or amount <= 0:
            raise ValueError("invoice_id and amount are required")
        PaymentService(db, current_user_id=user_id).allocate(payment_id, invoice_id, amount)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error allocating payment %s", payment_id)
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote('Unexpected error — allocation was not recorded.')}",
            status_code=303,
        )

    return RedirectResponse(f"/payments/{payment_id}?saved=1", status_code=303)


# ── De-allocate ───────────────────────────────────────────────────────────────

@router.post("/{payment_id}/allocations/{alloc_id}/remove", response_class=RedirectResponse)
def payment_deallocate(
    payment_id: int,
    alloc_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Remove a specific allocation — returns the amount to the payment's unallocated pool."""
    from app.services.payment_service import PaymentService

    try:
        PaymentService(db, current_user_id=user_id).deallocate(alloc_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error removing allocation %s from payment %s", alloc_id, payment_id)
        return RedirectResponse(
            f"/payments/{payment_id}?error={url_quote('Unexpected error — allocation was not removed.')}",
            status_code=303,
        )

    return RedirectResponse(f"/payments/{payment_id}?saved=1", status_code=303)
