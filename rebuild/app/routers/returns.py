"""
app/routers/returns.py
=======================
Return Authorization (RA) UI — customer returns lifecycle.

Flow:
  create (DRAFT) → approve (OPEN) → receive goods (RECEIVED) → close (CLOSED)

All mutations route through RAService — no direct model writes here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import RAStatus, ReturnDisposition
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.returns import ReturnAuthorization

log = logging.getLogger(__name__)

router = APIRouter(prefix="/returns", tags=["returns"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def ra_list(
    request: Request,
    status: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    from sqlalchemy import or_
    query = db.query(ReturnAuthorization).join(Customer)
    if status:
        query = query.filter(ReturnAuthorization.status == status)
    else:
        query = query.filter(ReturnAuthorization.status != RAStatus.CLOSED)
    if q:
        query = query.filter(
            or_(
                ReturnAuthorization.ra_number.ilike(f"%{q}%"),
                Customer.company_name.ilike(f"%{q}%"),
            )
        )
    ras = query.order_by(ReturnAuthorization.requested_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "returns/list.html",
        {
            "request": request,
            "ras": ras,
            "status_filter": status,
            "q": q,
            "RAStatus": RAStatus,
        },
    )


# ── New / Create ───────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def ra_new(
    request: Request,
    customer_id: int = 0,
    db: Session = Depends(get_db),
):
    if not request.headers.get("HX-Request"):
        return RedirectResponse("/returns/", status_code=303)
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    products = (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .order_by(Product.sku)
        .all()
    )
    selected_customer = (
        db.query(Customer).filter(Customer.id == customer_id).first()
        if customer_id else None
    )
    return templates.TemplateResponse(
        "returns/_new_picker.html",
        {
            "request": request,
            "customers": customers,
            "products": products,
            "selected_customer": selected_customer,
            "ReturnDisposition": ReturnDisposition,
        },
    )


@router.post("/new", response_class=RedirectResponse)
async def ra_create(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.ra_service import RAService

    form = await request.form()

    customer_id_raw = str(form.get("customer_id", "")).strip()
    if not customer_id_raw:
        return RedirectResponse("/returns/new?error=Customer+is+required", status_code=303)
    customer_id = int(customer_id_raw)

    reason = str(form.get("reason", "")).strip()
    notes = str(form.get("notes", "")).strip()
    internal_notes = str(form.get("internal_notes", "")).strip()

    # Optional invoice link
    inv_raw = str(form.get("invoice_number", "")).strip()
    invoice_id = None
    if inv_raw:
        inv = db.query(Invoice).filter(Invoice.invoice_number == inv_raw).first()
        if inv:
            invoice_id = inv.id

    # Parallel line arrays
    product_ids = form.getlist("product_id[]")
    descriptions = form.getlist("description[]")
    qtys = form.getlist("qty[]")
    unit_prices = form.getlist("unit_price[]")
    restocking_fees = form.getlist("restocking_fee[]")
    dispositions = form.getlist("disposition[]")

    lines = []
    for i, pid in enumerate(product_ids):
        desc = descriptions[i] if i < len(descriptions) else ""
        qty_raw = qtys[i] if i < len(qtys) else "1"
        price_raw = unit_prices[i] if i < len(unit_prices) else "0"
        fee_raw = restocking_fees[i] if i < len(restocking_fees) else "0"
        disp = dispositions[i] if i < len(dispositions) else ReturnDisposition.QUARANTINE

        if not pid and not desc.strip():
            continue

        try:
            qty = max(1, int(qty_raw)) if qty_raw else 1
        except (ValueError, TypeError):
            qty = 1
        try:
            unit_price = float(price_raw) if price_raw else 0.0
        except (ValueError, TypeError):
            unit_price = 0.0
        try:
            restocking_fee = float(fee_raw) if fee_raw else 0.0
        except (ValueError, TypeError):
            restocking_fee = 0.0

        lines.append({
            "product_id": int(pid) if pid else None,
            "description": desc.strip(),
            "qty": qty,
            "unit_price": unit_price,
            "restocking_fee": restocking_fee,
            "disposition": disp,
        })

    if not lines:
        return RedirectResponse(
            f"/returns/new?error={url_quote('At least one return line is required')}",
            status_code=303,
        )

    try:
        ra = RAService(db, user_id).create_ra(
            customer_id=customer_id,
            invoice_id=invoice_id,
            reason=reason,
            lines=lines,
            notes=notes,
            internal_notes=internal_notes,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/returns/new?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating RA")
        return RedirectResponse(
            f"/returns/new?error={url_quote('Unexpected error — return was not created.')}",
            status_code=303,
        )
    return RedirectResponse(f"/returns/{ra.id}", status_code=303)


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{ra_id}", response_class=HTMLResponse)
def ra_detail(ra_id: int, request: Request, db: Session = Depends(get_db)):
    ra = db.query(ReturnAuthorization).filter(ReturnAuthorization.id == ra_id).first()
    if not ra:
        return RedirectResponse("/returns/", status_code=303)
    return templates.TemplateResponse(
        "returns/workspace.html",
        {
            "request": request,
            "ra": ra,
            "RAStatus": RAStatus,
            "ReturnDisposition": ReturnDisposition,
        },
    )


# ── Approve ───────────────────────────────────────────────────────────────────

@router.post("/{ra_id}/approve", response_class=RedirectResponse)
async def ra_approve(
    ra_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.ra_service import RAService

    form = await request.form()
    override_reason = str(form.get("override_reason", "")).strip() or None
    try:
        RAService(db, user_id).approve_ra(ra_id, override_reason=override_reason)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/returns/{ra_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error approving RA %s", ra_id)
        return RedirectResponse(
            f"/returns/{ra_id}?error={url_quote('Unexpected error — RA was not approved.')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/returns/{ra_id}?ok={url_quote('Return authorized — customer may ship parts back.')}",
        status_code=303,
    )


# ── Receive Goods ─────────────────────────────────────────────────────────────

@router.post("/{ra_id}/receive", response_class=RedirectResponse)
async def ra_receive(
    ra_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.ra_service import RAService

    ra = db.query(ReturnAuthorization).filter(ReturnAuthorization.id == ra_id).first()
    if not ra:
        return RedirectResponse("/returns/", status_code=303)

    form = await request.form()

    line_updates = []
    for line in ra.lines:
        disposition = str(form.get(f"line_{line.id}_disposition", ReturnDisposition.QUARANTINE))
        qty_raw = str(form.get(f"line_{line.id}_qty_to_stock", "0")).strip()
        condition_notes = str(form.get(f"line_{line.id}_condition_notes", "")).strip() or None

        try:
            qty_to_stock = max(0, int(qty_raw)) if qty_raw else 0
        except (ValueError, TypeError):
            qty_to_stock = 0

        line_updates.append({
            "line_id": line.id,
            "disposition": disposition,
            "qty_returned_to_stock": qty_to_stock,
            "condition_notes": condition_notes,
        })

    try:
        RAService(db, user_id).receive_goods(ra_id, line_updates=line_updates)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/returns/{ra_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error receiving goods for RA %s", ra_id)
        return RedirectResponse(
            f"/returns/{ra_id}?error={url_quote('Unexpected error — goods receipt was not recorded.')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/returns/{ra_id}?ok={url_quote('Goods received and inventory updated.')}",
        status_code=303,
    )


# ── Close ─────────────────────────────────────────────────────────────────────

@router.post("/{ra_id}/close", response_class=RedirectResponse)
def ra_close(
    ra_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.ra_service import RAService

    try:
        ra = RAService(db, user_id).close_ra(ra_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/returns/{ra_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error closing RA %s", ra_id)
        return RedirectResponse(
            f"/returns/{ra_id}?error={url_quote('Unexpected error — return was not closed.')}",
            status_code=303,
        )
    credit = ra.total_credit
    if credit > 0:
        msg = f"Return closed — ${credit:.2f} credit applied to account."
    else:
        msg = "Return closed."
    return RedirectResponse(f"/returns/{ra_id}?ok={url_quote(msg)}", status_code=303)
