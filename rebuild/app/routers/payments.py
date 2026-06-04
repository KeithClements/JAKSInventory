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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.constants import InvoiceStatus, PaymentDirection, PaymentMethod, PaymentStatus
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment, PaymentAllocation

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
        request,
        "payments/new.html",
        {
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
    tab: str = "",
    status: str = "",
    q: str = "",
    sort: str = "date",
    direction: str = "desc",
    db: Session = Depends(get_db),
):
    from app.utils import apply_sort
    # ── Unfiltered tab counts ─────────────────────────────────────────────
    _raw: dict = dict(
        db.query(Payment.status, func.count(Payment.id))
        .group_by(Payment.status)
        .all()
    )
    counts: dict = {
        "":                      sum(_raw.values()),
        PaymentStatus.APPLIED:   _raw.get(PaymentStatus.APPLIED,  0),
        PaymentStatus.REVERSED:  _raw.get(PaymentStatus.REVERSED, 0),
        PaymentStatus.NSF:       _raw.get(PaymentStatus.NSF,      0),
    }

    _VALID = {"", PaymentStatus.APPLIED, PaymentStatus.REVERSED, PaymentStatus.NSF}
    active_tab = tab if tab in _VALID else (status if status in _VALID else "")

    query = (
        db.query(Payment)
        .join(Customer)
        .options(selectinload(Payment.allocations))  # fix N+1 on amount_unallocated
    )
    if active_tab:
        query = query.filter(Payment.status == active_tab)
    if q:
        query = query.filter(
            or_(
                Customer.company_name.ilike(f"%{q}%"),
                Payment.check_number.ilike(f"%{q}%"),
            )
        )
    # Sort (#4 — whitelisted keys, asc/desc).
    _P_SORT = {
        "date":     Payment.payment_date,
        "amount":   Payment.amount_received,
        "customer": Customer.company_name,
        "method":   Payment.payment_method,
    }
    query, sort, direction = apply_sort(query, _P_SORT, sort, direction, default="date")
    payments = query.limit(300).all()

    # ── Bulk §2B: invoice numbers per payment (fixes N+1 on alloc.invoice) ─
    from collections import defaultdict
    _pmt_ids = [p.id for p in payments]
    _alloc_rows = (
        db.query(PaymentAllocation.payment_id, Invoice.invoice_number, Invoice.id)
        .join(Invoice, PaymentAllocation.invoice_id == Invoice.id)
        .filter(
            PaymentAllocation.payment_id.in_(_pmt_ids),
            PaymentAllocation.is_reversed == False,  # noqa: E712
        )
        .all()
    ) if _pmt_ids else []

    # invoice_nums_map: {payment_id: [(invoice_number, invoice_id), ...]}
    invoice_nums_map: dict = defaultdict(list)
    for pmt_id, inv_num, inv_id in _alloc_rows:
        invoice_nums_map[pmt_id].append((inv_num, inv_id))

    return templates.TemplateResponse(
        request,
        "payments/list.html",
        {
            "request":           request,
            "payments":          payments,
            "active_tab":        active_tab,
            "status_filter":     active_tab,  # back-compat alias
            "counts":            counts,
            "q":                 q,
            "sort":              sort,
            "direction":         direction,
            "PaymentStatus":     PaymentStatus,
            "PaymentMethod":     PaymentMethod,
            "invoice_nums_map":  dict(invoice_nums_map),
        },
    )


# ── Preview panel — MUST stay registered before /{payment_id} ────────────────

@router.get("/preview/{payment_id}", response_class=HTMLResponse)
def payment_preview_panel(
    payment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Bottom-dock preview partial for the Payment List (§7 Primitive 5).
    Loaded via htmx.ajax() on row click; returns payments/_preview_panel.html.

    Context published to UI lane:
      payment         — Payment ORM (with .customer, .allocations pre-loaded)
      invoice_entries — list[(invoice_number, invoice_id, amount_applied)]
                        active (non-reversed) allocations only
      PaymentStatus   — enum class
      PaymentMethod   — enum class
      PaymentDirection— enum class
    """
    pmt = (
        db.query(Payment)
        .options(selectinload(Payment.allocations))
        .filter(Payment.id == payment_id)
        .first()
    )
    if pmt is None:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-red-500">Payment not found.</p>',
            status_code=404,
        )

    # Active allocations with invoice numbers — one join query
    active_alloc_ids = [a.invoice_id for a in pmt.allocations if not a.is_reversed]
    _inv_map: dict = {}
    if active_alloc_ids:
        _inv_map = dict(
            db.query(Invoice.id, Invoice.invoice_number)
            .filter(Invoice.id.in_(active_alloc_ids))
            .all()
        )

    invoice_entries: list[tuple] = [
        (_inv_map.get(a.invoice_id, f"INV-{a.invoice_id}"), a.invoice_id, a.amount_applied)
        for a in pmt.allocations
        if not a.is_reversed
    ]

    return templates.TemplateResponse(
        request,
        "payments/_preview_panel.html",
        {
            "request":          request,
            "payment":          pmt,
            "invoice_entries":  invoice_entries,
            "PaymentStatus":    PaymentStatus,
            "PaymentMethod":    PaymentMethod,
            "PaymentDirection": PaymentDirection,
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
        request,
        "payments/detail.html",
        {
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
