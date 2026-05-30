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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.constants import RAStatus, ReturnDisposition
from app.deps import get_current_user_id, get_db
from app.models.credit_memo import CreditMemo
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.returns import ReturnAuthorization, ReturnLine
from app.services.document_render import (
    customer_address_lines,
    get_company_dict,
    render_pdf_or_fallback,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/returns", tags=["returns"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def ra_list(
    request: Request,
    tab: str = "",
    status: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    # ── Unfiltered tab counts ─────────────────────────────────────────────
    _raw: dict = dict(
        db.query(ReturnAuthorization.status, func.count(ReturnAuthorization.id))
        .group_by(ReturnAuthorization.status)
        .all()
    )
    counts: dict = {
        "":                 sum(v for k, v in _raw.items() if k != RAStatus.CLOSED),
        RAStatus.DRAFT:     _raw.get(RAStatus.DRAFT, 0),
        RAStatus.OPEN:      _raw.get(RAStatus.OPEN, 0),
        RAStatus.RECEIVED:  _raw.get(RAStatus.RECEIVED, 0),
        RAStatus.CLOSED:    _raw.get(RAStatus.CLOSED, 0),
    }

    # `tab` is the canonical param from filter_tabs (?tab=<slug>).
    # Legacy ?status= accepted for back-compat.
    _VALID = {"", RAStatus.DRAFT, RAStatus.OPEN, RAStatus.RECEIVED, RAStatus.CLOSED}
    active_tab = tab if tab in _VALID else (status if status in _VALID else "")

    query = (
        db.query(ReturnAuthorization)
        .join(Customer)
        .options(selectinload(ReturnAuthorization.lines))  # avoid N+1 on total_credit
    )
    if active_tab:
        query = query.filter(ReturnAuthorization.status == active_tab)
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

    # ── Bulk §2B aggregates (no N+1) ─────────────────────────────────────
    _ra_ids = [ra.id for ra in ras]

    # Credit memo numbers linked to these RAs (one CM per RA max)
    credit_memo_map: dict = dict(
        db.query(CreditMemo.ra_id, CreditMemo.cm_number)
        .filter(CreditMemo.ra_id.in_(_ra_ids))
        .all()
    ) if _ra_ids else {}

    # Invoice numbers for RAs that have an invoice_id
    _inv_ids = [ra.invoice_id for ra in ras if ra.invoice_id]
    invoice_number_map: dict = dict(
        db.query(Invoice.id, Invoice.invoice_number)
        .filter(Invoice.id.in_(_inv_ids))
        .all()
    ) if _inv_ids else {}

    # Unique dispositions per RA (single query, Python grouping)
    from collections import defaultdict
    _disp_rows = (
        db.query(ReturnLine.ra_id, ReturnLine.disposition)
        .filter(ReturnLine.ra_id.in_(_ra_ids))
        .all()
    ) if _ra_ids else []
    disposition_map: dict[int, list[str]] = defaultdict(list)
    for _rid, _disp in _disp_rows:
        if _disp not in disposition_map[_rid]:
            disposition_map[_rid].append(_disp)

    return templates.TemplateResponse(
        request,
        "returns/list.html",
        {
            "request":           request,
            "ras":               ras,
            "active_tab":        active_tab,
            "status_filter":     active_tab,   # back-compat alias
            "counts":            counts,
            "q":                 q,
            "RAStatus":          RAStatus,
            "ReturnDisposition": ReturnDisposition,
            # §2B aggregate maps (keyed by ra.id or invoice.id)
            "credit_memo_map":   dict(credit_memo_map),
            "invoice_number_map":invoice_number_map,
            "disposition_map":   dict(disposition_map),
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
        request,
        "returns/_new_picker.html",
        {
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


# ── Preview panel — MUST stay registered before /{ra_id} ─────────────────────

@router.get("/preview/{ra_id}", response_class=HTMLResponse)
def ra_preview_panel(ra_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Bottom-dock preview partial for the RA List (§7 Primitive 5).
    Loaded via htmx.ajax() on row click; returns returns/_preview_panel.html.

    Context published to UI lane:
      ra               — ReturnAuthorization (with .customer, .lines pre-loaded)
      credit_memo_num  — str | None
      invoice_number   — str | None
      dispositions     — list[str]  unique disposition values across lines
      total_restock    — float  sum of all restocking_fee on lines
      RAStatus         — enum class
      ReturnDisposition — enum class
    """
    ra = (
        db.query(ReturnAuthorization)
        .options(selectinload(ReturnAuthorization.lines))
        .filter(ReturnAuthorization.id == ra_id)
        .first()
    )
    if ra is None:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-red-500">Return authorization not found.</p>',
            status_code=404,
        )

    credit_memo_num: str | None = None
    _cm = db.query(CreditMemo.cm_number).filter(CreditMemo.ra_id == ra_id).first()
    if _cm:
        credit_memo_num = _cm[0]

    invoice_number: str | None = None
    if ra.invoice_id:
        _inv = db.query(Invoice.invoice_number).filter(Invoice.id == ra.invoice_id).first()
        if _inv:
            invoice_number = _inv[0]

    dispositions: list[str] = list({ln.disposition for ln in ra.lines})
    total_restock: float = round(sum(ln.restocking_fee for ln in ra.lines), 2)

    return templates.TemplateResponse(
        request,
        "returns/_preview_panel.html",
        {
            "request":          request,
            "ra":               ra,
            "credit_memo_num":  credit_memo_num,
            "invoice_number":   invoice_number,
            "dispositions":     dispositions,
            "total_restock":    total_restock,
            "RAStatus":         RAStatus,
            "ReturnDisposition":ReturnDisposition,
        },
    )


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{ra_id}", response_class=HTMLResponse)
def ra_detail(ra_id: int, request: Request, db: Session = Depends(get_db)):
    ra = db.query(ReturnAuthorization).filter(ReturnAuthorization.id == ra_id).first()
    if not ra:
        return RedirectResponse("/returns/", status_code=303)
    return templates.TemplateResponse(
        request,
        "returns/workspace.html",
        {
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


# ── Print / PDF ───────────────────────────────────────────────────────────────

def _ra_print_context(ra: ReturnAuthorization, db: Session) -> dict:
    company = get_company_dict(db)
    customer_addr_lines_ = customer_address_lines(ra.customer)

    invoice = None
    if ra.invoice_id:
        invoice = db.query(Invoice).filter(Invoice.id == ra.invoice_id).first()

    line_subtotal = round(sum(ln.unit_price * ln.qty for ln in ra.lines), 2)
    total_restock = round(sum(ln.restocking_fee for ln in ra.lines), 2)

    return {
        "ra": ra,
        "invoice": invoice,
        "company": company,
        "customer_addr_lines": customer_addr_lines_,
        "line_subtotal": line_subtotal,
        "total_restock": total_restock,
    }


@router.get("/{ra_id}/print", response_class=HTMLResponse)
def ra_print(ra_id: int, request: Request, db: Session = Depends(get_db)):
    ra = db.query(ReturnAuthorization).filter(ReturnAuthorization.id == ra_id).first()
    if ra is None:
        return RedirectResponse("/returns/", status_code=303)
    ctx = _ra_print_context(ra, db)
    return templates.TemplateResponse(request, "returns/print.html", ctx)


@router.get("/{ra_id}/pdf")
def ra_pdf(ra_id: int, request: Request, db: Session = Depends(get_db)):
    ra = db.query(ReturnAuthorization).filter(ReturnAuthorization.id == ra_id).first()
    if ra is None:
        return RedirectResponse("/returns/", status_code=303)
    ctx = _ra_print_context(ra, db)
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="returns/print.html",
        context=ctx,
        fallback_print_url=f"/returns/{ra_id}/print",
        download_filename=ra.ra_number,
    )
