"""
app/routers/quotes.py
======================
Quote workspace — keyboard-first, HTMX-powered.
All mutations delegate to QuoteService. No business logic here.

Line mutations return just the affected <tr> via HTMX.
Totals refresh uses the htmx:afterRequest event listener in workspace.html
to trigger a separate GET /quotes/{id}/totals request, avoiding DOM
detachment issues with OOB swaps inside <tbody> context.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import (
    CoreDirection, CoreStatus, InvoiceStatus, QuoteStatus, SOPaymentMode, LineRole,
)
from app.deps import get_current_user_id, get_db
from app.models.core import CoreCharge
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.quote import Quote
from app.services.quote_service import QuoteService
from app.services.search_service import SearchService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/quotes", tags=["quotes"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_quote_or_404(db: Session, quote_id: int) -> Quote:
    q = db.query(Quote).filter(Quote.id == quote_id).first()
    if q is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return q


def _tree_sort_lines(lines):
    from app.utils import tree_sort_lines
    return tree_sort_lines(lines)


def _totals_ctx(quote: Quote) -> dict:
    included = [ln for ln in quote.lines if ln.is_included]
    gross = round(sum(ln.unit_price * ln.qty for ln in included), 2)
    subtotal = quote.subtotal           # line_total already applies per-line disc
    discount_amount = round(gross - subtotal, 2)
    return {
        "gross": gross,
        "discount_amount": discount_amount,
        "subtotal": subtotal,
        "line_count": len(included),
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def list_quotes(
    request: Request,
    status: str = "",
    q: str = "",
    follow_up: str = "",
    db: Session = Depends(get_db),
):
    from datetime import datetime
    from sqlalchemy import or_

    from app.constants import QuoteOutcome

    query = db.query(Quote).join(Customer)
    if follow_up == "due":
        now = datetime.utcnow()
        query = query.filter(
            Quote.follow_up_date <= now,
            Quote.outcome == QuoteOutcome.PENDING,
            Quote.status.notin_([QuoteStatus.CONVERTED, QuoteStatus.DECLINED]),
        )
    elif status:
        query = query.filter(Quote.status == status)
    if q:
        query = query.filter(
            or_(
                Quote.quote_number.ilike(f"%{q}%"),
                Customer.company_name.ilike(f"%{q}%"),
            )
        )
    quotes = query.order_by(Quote.created_at.desc()).limit(150).all()
    return templates.TemplateResponse(
        "quotes/list.html",
        {
            "request": request,
            "quotes": quotes,
            "status_filter": status,
            "follow_up_filter": follow_up,
            "q": q,
            "QuoteStatus": QuoteStatus,
        },
    )


# ── New Quote ─────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_quote_form(request: Request, db: Session = Depends(get_db)):
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    return templates.TemplateResponse(
        "quotes/new.html",
        {"request": request, "customers": customers},
    )


@router.post("/new")
async def create_quote(
    customer_id: int = Form(...),
    discount_pct: float = Form(0.0),
    validity_days: int = Form(30),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    svc = QuoteService(db, user_id)
    quote = svc.create_quote(
        customer_id=customer_id,
        data={
            "discount_pct": discount_pct,
            "validity_days": validity_days,
            "notes": notes,
        },
    )
    return RedirectResponse(f"/quotes/{quote.id}", status_code=303)


# ── Product Search (JSON for Alpine line-adder) ────────────────────────────────

@router.get("/product-search")
async def product_search_json(
    q: str = "",
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    JSON endpoint powering the Alpine.js product search in the line-adder row.
    Returns ranked results including cost, suggested sell, and stock level.
    """
    q = q.strip()
    if len(q) < 2:
        return JSONResponse([])
    svc = SearchService(db, user_id)
    results = svc.search_products(q, limit=8)
    return JSONResponse([
        {
            "product_id": r.product_id,
            "part_number": r.part_number,
            "description": r.description,
            "current_cost": r.current_cost,
            "suggested_sell": r.suggested_sell,
            "qty_on_hand": r.qty_on_hand,
            "vendor_name": r.vendor_name,
            "match_type": r.match_type,
            "last_sold_price": r.last_sold_price,
            "last_sold_date": r.last_sold_date,
        }
        for r in results
    ])


# ── Workspace ─────────────────────────────────────────────────────────────────

@router.get("/{quote_id}", response_class=HTMLResponse)
async def workspace(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    quote = _get_quote_or_404(db, quote_id)
    today = date.today()

    # ── Customer balance panel data ────────────────────────────────────────────
    cust_id = quote.customer_id
    open_invoices = (
        db.query(Invoice)
        .filter(
            Invoice.customer_id == cust_id,
            Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
        )
        .all()
    )
    open_balance = round(sum(inv.balance_due for inv in open_invoices), 2)
    overdue_balance = round(
        sum(inv.balance_due for inv in open_invoices if inv.is_overdue), 2
    )
    # Cores the customer still owes back to JAKS
    core_charges = (
        db.query(CoreCharge)
        .filter(
            CoreCharge.customer_id == cust_id,
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
        )
        .all()
    )
    cores_owed_qty = sum(
        max(0, c.qty_charged - c.qty_returned) for c in core_charges
    )

    return templates.TemplateResponse(
        "quotes/workspace.html",
        {
            "request": request,
            "quote": quote,
            "sorted_lines": _tree_sort_lines(quote.lines),
            "QuoteStatus": QuoteStatus,
            "SOPaymentMode": SOPaymentMode,
            "today": today,
            "tomorrow": today + timedelta(days=1),
            # Customer balance panel
            "cust_open_balance": open_balance,
            "cust_overdue_balance": overdue_balance,
            "cust_credit_balance": round(quote.customer.credit_balance, 2),
            "cores_owed_qty": cores_owed_qty,
            **_totals_ctx(quote),
        },
    )


# ── Print / PDF ───────────────────────────────────────────────────────────────

@router.get("/{quote_id}/print", response_class=HTMLResponse)
async def print_quote(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Standalone print-ready HTML page for a quote.
    Opens in a new tab — user hits Ctrl+P / Cmd+P or 'Print / Save PDF' button.
    No weasyprint dependency: the browser's built-in PDF engine handles rendering.
    """
    from app.settings_utils import get_setting_value_db

    quote = _get_quote_or_404(db, quote_id)
    all_lines = _tree_sort_lines(quote.lines)

    included_lines = [ln for ln in all_lines if ln.is_included]
    alt_lines = [
        ln for ln in all_lines
        if ln.line_role == LineRole.UPGRADE_OPTION and not ln.is_included
    ]

    gross_total = round(sum(ln.unit_price * ln.qty for ln in included_lines), 2)
    subtotal = quote.subtotal
    discount_amount = round(gross_total - subtotal, 2)

    # Pre-format customer address lines to avoid Jinja2 filter edge cases
    c = quote.customer
    addr_lines: list[str] = [ln for ln in [c.address_line1, c.address_line2] if ln.strip()]
    city_parts = [p for p in [c.city, c.state] if p.strip()]
    city_line = ", ".join(city_parts)
    if city_line and c.zip_code.strip():
        city_line += " " + c.zip_code.strip()
    elif not city_line and c.zip_code.strip():
        city_line = c.zip_code.strip()
    if city_line:
        addr_lines.append(city_line)
    if c.phone.strip():
        addr_lines.append(c.phone.strip())

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    return templates.TemplateResponse(
        "quotes/print.html",
        {
            "request":             request,
            "quote":               quote,
            "included_lines":      included_lines,
            "alt_lines":           alt_lines,
            "customer_addr_lines": addr_lines,
            "gross_total":         gross_total,
            "subtotal":            subtotal,
            "discount_amount":     discount_amount,
            "company":             company,
        },
    )


@router.get("/{quote_id}/pdf")
async def quote_pdf(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Server-side PDF generation via WeasyPrint.
    Returns a downloadable PDF file — no browser print dialog required.
    Used for emailing quotes directly from the app.
    """
    from fastapi.responses import Response as FastAPIResponse
    from app.settings_utils import get_setting_value_db

    quote = _get_quote_or_404(db, quote_id)
    all_lines = _tree_sort_lines(quote.lines)

    included_lines = [ln for ln in all_lines if ln.is_included]
    alt_lines = [
        ln for ln in all_lines
        if ln.line_role == LineRole.UPGRADE_OPTION and not ln.is_included
    ]

    gross_total = round(sum(ln.unit_price * ln.qty for ln in included_lines), 2)
    subtotal = quote.subtotal
    discount_amount = round(gross_total - subtotal, 2)

    c = quote.customer
    addr_lines: list[str] = [ln for ln in [c.address_line1, c.address_line2] if ln.strip()]
    city_parts = [p for p in [c.city, c.state] if p.strip()]
    city_line = ", ".join(city_parts)
    if city_line and c.zip_code.strip():
        city_line += " " + c.zip_code.strip()
    elif not city_line and c.zip_code.strip():
        city_line = c.zip_code.strip()
    if city_line:
        addr_lines.append(city_line)
    if c.phone.strip():
        addr_lines.append(c.phone.strip())

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    html_str = templates.env.get_template("quotes/print.html").render(
        request=request,
        quote=quote,
        included_lines=included_lines,
        alt_lines=alt_lines,
        customer_addr_lines=addr_lines,
        gross_total=gross_total,
        subtotal=subtotal,
        discount_amount=discount_amount,
        company=company,
    )

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_str, base_url=str(request.base_url)).write_pdf()
    except (OSError, ImportError, Exception):
        # WeasyPrint system libraries (GTK/Pango) not available on this host.
        # Fall back to browser print-to-PDF.
        return RedirectResponse(
            f"/quotes/{quote_id}/print", status_code=302
        )

    safe_number = quote.quote_number.replace("/", "-").replace("\\", "-")
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_number}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.post("/{quote_id}")
async def update_quote_header(
    quote_id: int,
    notes: str = Form(""),
    internal_notes: str = Form(""),
    discount_pct: float = Form(0.0),
    validity_days: int = Form(30),
    db: Session = Depends(get_db),
):
    """Update quote-level notes and settings (safe field updates, no service delegation needed)."""
    quote = _get_quote_or_404(db, quote_id)
    quote.notes = notes
    quote.internal_notes = internal_notes
    quote.discount_pct = discount_pct
    quote.validity_days = validity_days
    db.commit()
    return RedirectResponse(f"/quotes/{quote_id}?saved=1", status_code=303)


# ── Totals refresh endpoint ───────────────────────────────────────────────────

@router.get("/{quote_id}/totals", response_class=HTMLResponse)
async def get_totals(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """HTMX endpoint triggered by JS after any line mutation to refresh the totals bar."""
    quote = _get_quote_or_404(db, quote_id)
    return templates.TemplateResponse(
        "quotes/_totals.html",
        {"request": request, "quote": quote, **_totals_ctx(quote)},
    )


# ── Lines ─────────────────────────────────────────────────────────────────────

@router.post("/{quote_id}/lines", response_class=HTMLResponse)
async def add_line(
    quote_id: int,
    request: Request,
    product_id: int | None = Form(default=None),
    parent_line_id: int | None = Form(default=None),
    description: str = Form(""),
    qty: int = Form(1),
    unit_price: float = Form(0.0),
    unit_cost: float = Form(0.0),
    discount_pct: float = Form(0.0),
    line_type: str = Form("product"),
    line_role: str = Form("primary"),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    svc = QuoteService(db, user_id)
    line = svc.add_line(
        quote_id=quote_id,
        product_id=product_id,
        data={
            "description": description,
            "qty": qty,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "discount_pct": discount_pct,
            "line_type": line_type,
            "line_role": line_role,
            "parent_line_id": parent_line_id,
        },
    )
    db.refresh(line)
    return templates.TemplateResponse(
        "quotes/_line_row.html",
        {"request": request, "line": line},
    )


@router.post("/{quote_id}/lines/{line_id}", response_class=HTMLResponse)
async def update_line(
    quote_id: int,
    line_id: int,
    request: Request,
    description: str | None = Form(default=None),
    qty: int | None = Form(default=None),
    unit_price: float | None = Form(default=None),
    unit_cost: float | None = Form(default=None),
    discount_pct: float | None = Form(default=None),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    data = {
        k: v for k, v in {
            "description": description,
            "qty": qty,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "discount_pct": discount_pct,
        }.items()
        if v is not None
    }
    svc = QuoteService(db, user_id)
    line = svc.update_line(line_id, data)
    db.refresh(line)
    return templates.TemplateResponse(
        "quotes/_line_row.html",
        {"request": request, "line": line},
    )


@router.delete("/{quote_id}/lines/{line_id}", response_class=HTMLResponse)
async def remove_line(
    quote_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Delete a line. Returns empty — the row is removed by hx-swap="delete" on
    the button. The htmx:afterRequest listener in workspace.html refreshes totals.
    """
    QuoteService(db, user_id).remove_line(line_id)
    return HTMLResponse("")


# ── Full tbody refresh (used after multi-row state changes) ──────────────

@router.get("/{quote_id}/lines", response_class=HTMLResponse)
async def get_lines_tbody(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the full lines tbody; used by select-upgrade and toggle-included."""
    quote = _get_quote_or_404(db, quote_id)
    return templates.TemplateResponse(
        "quotes/_lines_tbody.html",
        {"request": request, "lines": _tree_sort_lines(quote.lines)},
    )


# ── Upgrade option routes ─────────────────────────────────────────────────

@router.post("/{quote_id}/lines/{line_id}/upgrade-option", response_class=HTMLResponse)
async def add_upgrade_option(
    quote_id: int,
    line_id: int,
    request: Request,
    product_id: int = Form(...),
    description: str = Form(""),
    qty: int = Form(1),
    unit_price: float = Form(0.0),
    unit_cost: float = Form(0.0),
    discount_pct: float = Form(0.0),
    option_label: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    svc = QuoteService(db, user_id)
    line = svc.add_upgrade_option(
        parent_line_id=line_id,
        product_id=product_id,
        data={
            "description": description,
            "qty": qty,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "discount_pct": discount_pct,
            "option_label": option_label or None,
        },
    )
    db.refresh(line)
    return templates.TemplateResponse(
        "quotes/_line_row.html",
        {"request": request, "line": line},
    )


@router.post("/{quote_id}/lines/{line_id}/optional", response_class=HTMLResponse)
async def add_optional_line(
    quote_id: int,
    line_id: int,
    request: Request,
    product_id: int = Form(...),
    description: str = Form(""),
    qty: int = Form(1),
    unit_price: float = Form(0.0),
    unit_cost: float = Form(0.0),
    discount_pct: float = Form(0.0),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    svc = QuoteService(db, user_id)
    line = svc.add_optional_line(
        parent_line_id=line_id,
        product_id=product_id,
        data={
            "description": description,
            "qty": qty,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "discount_pct": discount_pct,
        },
    )
    db.refresh(line)
    return templates.TemplateResponse(
        "quotes/_line_row.html",
        {"request": request, "line": line},
    )


@router.post("/{quote_id}/lines/{line_id}/select-upgrade", response_class=HTMLResponse)
async def select_upgrade(
    quote_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Select this upgrade option — deselects parent + siblings, refreshes full tbody."""
    QuoteService(db, user_id).select_upgrade_option(line_id)
    quote = _get_quote_or_404(db, quote_id)
    return templates.TemplateResponse(
        "quotes/_lines_tbody.html",
        {"request": request, "lines": _tree_sort_lines(quote.lines)},
    )


@router.post("/{quote_id}/lines/{line_id}/toggle-included", response_class=HTMLResponse)
async def toggle_included(
    quote_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Toggle is_included — may affect parent/siblings, so refreshes full tbody."""
    QuoteService(db, user_id).toggle_line_included(line_id)
    quote = _get_quote_or_404(db, quote_id)
    return templates.TemplateResponse(
        "quotes/_lines_tbody.html",
        {"request": request, "lines": _tree_sort_lines(quote.lines)},
    )


# ── Research Status ──────────────────────────────────────────────────────────

@router.post("/{quote_id}/lines/{line_id}/research", response_class=HTMLResponse)
async def set_research_status(
    quote_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Set (or clear) research_status on a quote line.
    On first flag (NULL → non-NULL), creates a ResearchItem via ResearchService.
    Returns the updated <tr> row HTML via HTMX outerHTML swap.
    """
    from app.models.quote import QuoteLine
    from app.services.research_service import ResearchService

    form = await request.form()
    new_status = str(form.get("research_status", "")).strip() or None

    line = db.query(QuoteLine).filter(QuoteLine.id == line_id, QuoteLine.quote_id == quote_id).first()
    if not line:
        return HTMLResponse("", status_code=404)

    was_unset = line.research_status is None

    # Create ResearchItem on first flag
    if new_status and was_unset:
        quote = _get_quote_or_404(db, quote_id)
        ri = ResearchService(db, current_user_id=user_id).create_research_item(
            search_term=line.description or f"Quote {quote.quote_number} line {line_id}",
            customer_id=quote.customer_id,
            quote_id=quote_id,
            quote_line_id=line_id,
        )
        db.flush()
        line.research_item_id = ri.id

    line.research_status = new_status
    db.commit()
    db.refresh(line)

    # Return just the updated row
    return templates.TemplateResponse(
        "quotes/_line_row.html",
        {"request": request, "line": line},
    )


# ── Autosave ─────────────────────────────────────────────────────────────────

@router.post("/{quote_id}/autosave", response_class=HTMLResponse)
async def autosave_quote(
    quote_id: int,
    notes: str = Form(""),
    internal_notes: str = Form(""),
    discount_pct: float = Form(0.0),
    validity_days: int = Form(30),
    db: Session = Depends(get_db),
):
    """
    HTMX autosave endpoint — called every 2.5s after any header field changes.
    Returns a small indicator HTML fragment (not a redirect).
    """
    try:
        quote = _get_quote_or_404(db, quote_id)
        quote.notes = notes
        quote.internal_notes = internal_notes
        quote.discount_pct = discount_pct
        quote.validity_days = validity_days
        db.commit()
        return HTMLResponse('<span class="text-xs text-green-600 font-medium">&#10003; Saved</span>')
    except Exception:
        db.rollback()
        log.exception("Autosave failed for quote %s", quote_id)
        return HTMLResponse(
            '<span class="text-xs text-red-500 font-medium">Save failed</span>',
            status_code=500,
        )


# ── Follow-Up Bar ─────────────────────────────────────────────────────────────

@router.post("/{quote_id}/follow-up", response_class=HTMLResponse)
async def set_follow_up(
    quote_id: int,
    request: Request,
    status: str = Form(...),
    days: int | None = Form(default=None),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    HTMX — set follow-up status and schedule date, return refreshed bar.
    days=None clears the date (No Follow Up).
    days=0 sets today (Truck Down — urgent).
    """
    try:
        QuoteService(db, user_id).set_follow_up(quote_id, status, days)
    except Exception:
        db.rollback()
        log.exception("set_follow_up failed for quote %s", quote_id)
    quote = _get_quote_or_404(db, quote_id)
    today = date.today()
    return templates.TemplateResponse(
        "quotes/_follow_up_bar.html",
        {
            "request": request,
            "quote": quote,
            "today": today,
            "tomorrow": today + timedelta(days=1),
        },
    )


# ── Status Transitions ────────────────────────────────────────────────────────

@router.post("/{quote_id}/send")
async def send_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    QuoteService(db, user_id).send_quote(quote_id)
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/mark-lost")
async def mark_lost(
    quote_id: int,
    lost_reason: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    QuoteService(db, user_id).mark_lost(quote_id, lost_reason or "No reason given")
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/reactivate")
async def reactivate_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    QuoteService(db, user_id).reactivate(quote_id)
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


# ── Conversions ───────────────────────────────────────────────────────────────

@router.post("/{quote_id}/convert-to-so")
async def convert_to_so(
    quote_id: int,
    payment_mode: str = Form(SOPaymentMode.NONE),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    so = QuoteService(db, user_id).convert_to_sales_order(quote_id, payment_mode)
    return RedirectResponse(f"/sales-orders/{so.id}", status_code=303)


@router.post("/{quote_id}/convert-to-invoice")
async def convert_to_invoice(
    quote_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    invoice = QuoteService(db, user_id).convert_to_invoice(quote_id)
    return RedirectResponse(f"/invoices/{invoice.id}", status_code=303)
