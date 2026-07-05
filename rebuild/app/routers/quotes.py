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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.constants import (
    QuoteOutcome, QuoteStatus, SOPaymentMode, LineRole,
    LostReason, LOST_REASON_LABELS,
    ENGINE_MODELS_BY_MAKE,
)
from app.deps import get_current_user_id, get_db
from app.services.base import ConcurrentEditError
from app.services.category_service import engine_make_names
from app.services.document_messaging import (
    build_send_context,
    itemize_lines,
    perform_document_send,
    render_pdf_or_none,
)
from app.services.public_links import public_doc_url
from app.services.document_render import (
    customer_address_lines, get_company_dict, get_prepared_by, static_url_fetcher,
)
from app.models.customer import Customer
from app.models.quote import Quote
from app.services.quote_service import QuoteService

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
    # Staff-only deal economics (gated client-side by the Show-margin toggle; never
    # on the customer print). Cost = sum of included line costs; profit = the
    # discounted line revenue (subtotal) minus that cost.
    total_cost = round(sum((ln.unit_cost or 0.0) * ln.qty for ln in included), 2)
    total_profit = round(subtotal - total_cost, 2)
    return {
        "gross": gross,
        "discount_amount": discount_amount,
        "subtotal": subtotal,
        "line_count": len(included),
        "total_cost": total_cost,
        "total_profit": total_profit,
        # §21 — quote tax (decision 6.16)
        "quote": quote,
        "is_taxable": quote.is_taxable,
        "tax_rate": quote.tax_rate_snapshot,
        "tax_amount": quote.tax_amount,
        "total": quote.total,
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def list_quotes(
    request: Request,
    tab: str = "",
    status: str = "",
    q: str = "",
    follow_up: str = "",
    customer_id: int = 0,
    sort: str = "created",
    direction: str = "desc",
    page: int = 1,
    db: Session = Depends(get_db),
):
    from datetime import datetime, date as date_type
    from app.utils import apply_sort

    now = datetime.utcnow()
    today = date_type.today()

    # ── Unfiltered tab counts (always over full dataset) ──────────────────
    _status_counts: dict = dict(
        db.query(Quote.status, func.count(Quote.id))
        .group_by(Quote.status)
        .all()
    )
    _follow_up_due_count: int = (
        db.query(func.count(Quote.id))
        .filter(
            Quote.follow_up_date <= now,
            Quote.outcome == QuoteOutcome.PENDING,
            Quote.status.notin_([QuoteStatus.CONVERTED, QuoteStatus.DECLINED]),
        )
        .scalar()
        or 0
    )
    # "open" = the live pipeline (terminal statuses excluded). This is the exact
    # count the dashboard's Open Quotes KPI shows, so its card links here (?tab=open).
    _OPEN_QUOTE_EXCLUDED = [
        QuoteStatus.CONVERTED, QuoteStatus.DECLINED, QuoteStatus.EXPIRED,
    ]
    _open_count: int = (
        db.query(func.count(Quote.id))
        .filter(Quote.status.notin_(_OPEN_QUOTE_EXCLUDED))
        .scalar()
        or 0
    )
    counts: dict = {
        "": sum(_status_counts.values()),
        "open":                _open_count,
        QuoteStatus.DRAFT:     _status_counts.get(QuoteStatus.DRAFT, 0),
        QuoteStatus.SENT:      _status_counts.get(QuoteStatus.SENT, 0),
        QuoteStatus.CONVERTED: _status_counts.get(QuoteStatus.CONVERTED, 0),
        QuoteStatus.DECLINED:  _status_counts.get(QuoteStatus.DECLINED, 0),
        QuoteStatus.EXPIRED:   _status_counts.get(QuoteStatus.EXPIRED, 0),
        "follow_up_due":       _follow_up_due_count,
    }

    # `tab` is the canonical param emitted by the filter_tabs macro (?tab=<slug>).
    # Legacy `status` and `follow_up` params are still accepted for back-compat.
    active_tab = tab or (follow_up and "follow_up_due") or status

    query = db.query(Quote).join(Customer)
    if customer_id:
        query = query.filter(Quote.customer_id == customer_id)
    if active_tab == "follow_up_due" or follow_up == "due":
        query = query.filter(
            Quote.follow_up_date <= now,
            Quote.outcome == QuoteOutcome.PENDING,
            Quote.status.notin_([QuoteStatus.CONVERTED, QuoteStatus.DECLINED]),
        )
    elif active_tab == "open":
        # Live pipeline — matches the dashboard Open Quotes KPI exactly.
        query = query.filter(Quote.status.notin_(_OPEN_QUOTE_EXCLUDED))
    elif active_tab:
        query = query.filter(Quote.status == active_tab)
    if q:
        _qd = q.replace("-", "").replace(" ", "")
        query = query.filter(
            or_(
                Quote.quote_number.ilike(f"%{q}%"),
                # de-dash so "q2026" / "20260001" still finds "Q-2026-0001"
                func.replace(func.replace(Quote.quote_number, "-", ""), " ", "").ilike(f"%{_qd}%"),
                Customer.company_name.ilike(f"%{q}%"),
            )
        )
    # Sort — whitelisted SQL keys + one computed key. "margin" is derived from the
    # loaded lines (not a column), so it is ordered in Python after .all() per the
    # plan's non-column-sort rule; created/number/customer/valid_until sort in SQL.
    _Q_SORT = {
        "created":  Quote.created_at,
        "number":   Quote.quote_number,
        "customer": Customer.company_name,
        "valid_until": Quote.valid_until,
    }
    _margin_sort = (sort == "margin")
    query, sort, direction = apply_sort(
        query, _Q_SORT, (None if _margin_sort else sort), direction, default="created"
    )
    # §21 — pagination (was a silent limit(150)).
    from app.utils import compute_pager
    pager = compute_pager(page, query.order_by(None).count(), per_page=50)
    if _margin_sort:
        sort = "margin"  # echo the active key back to the template's sort header

        def _q_margin(qte):
            sub = qte.subtotal or 0
            if not sub:
                return -1.0  # empty / zero-subtotal quotes sort to the bottom
            cost = sum(
                (ln.product.cost or 0) * ln.qty
                for ln in qte.lines
                if ln.product and ln.product.cost
            )
            return (sub - cost) / sub

        # margin is a computed property → sort the full (capped) set, then slice.
        _rows = query.limit(2000).all()
        _rows.sort(key=_q_margin, reverse=(direction == "desc"))
        quotes = _rows[pager["offset"]:pager["offset"] + pager["per_page"]]
    else:
        quotes = query.limit(pager["per_page"]).offset(pager["offset"]).all()

    # ── Bulk AR aggregate for visible customer set (no N+1) ───────────────
    from collections import defaultdict
    from sqlalchemy.orm import selectinload as _sload
    from app.models.invoice import Invoice
    from app.constants import InvoiceStatus

    _cust_ids = list({q_.customer_id for q_ in quotes})
    if _cust_ids:
        _open_invoices = (
            db.query(Invoice)
            .options(_sload(Invoice.lines), _sload(Invoice.allocations))
            .filter(
                Invoice.customer_id.in_(_cust_ids),
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
            )
            .all()
        )
        _ar: dict = defaultdict(lambda: {"open_balance": 0.0, "is_overdue": False})
        for _inv in _open_invoices:
            _cid = _inv.customer_id
            _ar[_cid]["open_balance"] = round(
                _ar[_cid]["open_balance"] + _inv.balance_due, 2
            )
            if _inv.is_overdue:
                _ar[_cid]["is_overdue"] = True
        ar_map: dict = dict(_ar)
    else:
        ar_map = {}

    # Load active customers for the inline New Quote modal on the list page.
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "quotes/list.html",
        {
            "quotes": quotes,
            "active_tab": active_tab,
            "status_filter": status,
            "follow_up_filter": follow_up,
            "customer_id": customer_id,
            "q": q,
            "sort": sort,
            "direction": direction,
            "QuoteStatus": QuoteStatus,
            "customers": customers,
            "now": now,
            "today": today,
            "counts": counts,
            "ar_map": ar_map,
            "pager": pager,
        },
    )


# ── New Quote ─────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_quote_form(
    request: Request,
    customer_id: int = 0,
    db: Session = Depends(get_db),
):
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    selected_customer = (
        db.query(Customer).filter(Customer.id == customer_id).first()
        if customer_id else None
    )
    return templates.TemplateResponse(
        request,
        "quotes/new.html",
        {"customers": customers, "selected_customer_id": customer_id, "selected_customer": selected_customer},
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
    try:
        quote = svc.create_quote(
            customer_id=customer_id,
            data={
                "discount_pct": discount_pct,
                "validity_days": validity_days,
                "notes": notes,
            },
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/customers/{customer_id}?error={url_quote(str(exc))}", status_code=303,
        )
    return RedirectResponse(f"/quotes/{quote.id}", status_code=303)


# ── Product Search ─────────────────────────────────────────────────────────────
# The deprecated /quotes/product-search JSON alias was removed after the §8H
# migration — the quote line-adder now calls the canonical
# GET /line-items/product-search (see LINE_ITEM_BUILDER_CONTRACT.md).


# ── Preview panel (HTMX dock) ─────────────────────────────────────────────────
# MUST be registered before /{quote_id} — otherwise "preview" is captured as
# an int param and raises a 422.

@router.get("/preview/{quote_id}", response_class=HTMLResponse)
async def quote_preview_panel(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Bottom-dock preview panel for the Quote List (§7 Primitive 5).
    Loaded via htmx.ajax() on row click; renders quotes/_preview_panel.html.

    §2B context published to UI lane:
      quote            — Quote ORM object (with .customer, .lines)
      margin_pct       — float | None  (None when subtotal = 0)
      cost_total       — float
      ar_overdue_count — int  (open/partial invoices past due_date)
      now              — datetime (for overdue detection in template)
      QuoteStatus      — enum class
      QuoteOutcome     — enum class
    """
    from datetime import datetime
    from sqlalchemy import func as _func
    from app.models.invoice import Invoice
    from app.constants import QuoteOutcome as _QO

    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if quote is None:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-red-500">Quote not found.</p>',
            status_code=404,
        )

    # Margin — sum included lines' (unit_cost × qty), compare to subtotal
    included = [ln for ln in quote.lines if ln.is_included]
    cost_total = round(sum(ln.unit_cost * ln.qty for ln in included), 2)
    subtotal = quote.subtotal
    margin_pct: float | None = (
        round((subtotal - cost_total) / subtotal * 100, 1) if subtotal > 0 else None
    )

    # AR overdue — quick scalar: how many invoices past due for this customer?
    now = datetime.utcnow()
    ar_overdue_count = (
        db.query(_func.count(Invoice.id))
        .filter(
            Invoice.customer_id == quote.customer_id,
            Invoice.status.in_(["open", "partial"]),
            Invoice.due_date < now,
        )
        .scalar()
        or 0
    )

    return templates.TemplateResponse(
        request,
        "quotes/_preview_panel.html",
        {
            "quote": quote,
            "margin_pct": margin_pct,
            "cost_total": cost_total,
            "ar_overdue_count": ar_overdue_count,
            "now": now,
            "QuoteStatus": QuoteStatus,
            "QuoteOutcome": _QO,
        },
    )


# ── Workspace ─────────────────────────────────────────────────────────────────

def _quote_customer_ctx(db, quote) -> dict:
    """Seam 3 — customer relationship context for the quote workspace.
    Pulls outstanding_cores, last_purchase, and lifetime_sales from the
    existing metrics services so the workspace sidebar can show them."""
    from app.services.customer_metrics_service import CustomerMetricsService
    from app.services.invoice_metrics_service import InvoiceMetricsService
    cid = quote.customer_id
    if not cid:
        return {"cust_outstanding_cores": 0,
                "cust_last_purchase": None,
                "cust_lifetime_sales": 0.0}
    m = CustomerMetricsService(db).metrics_for(cid)
    inv_metrics = InvoiceMetricsService(db)
    return {
        # Item 12 — this dict previously read keys CustomerMetricsService never
        # emits (outstanding_cores / last_purchase), so the panel showed 0 cores
        # / no last purchase ALWAYS. The panel wants a core COUNT and a purchase
        # DATE — the authoritative helpers live on InvoiceMetricsService
        # (outstanding_core_credits on CustomerMetricsService is a $ amount).
        "cust_outstanding_cores": inv_metrics._outstanding_cores(cid),
        "cust_last_purchase": inv_metrics._last_purchase(cid),
        "cust_lifetime_sales": m.get("lifetime_sales", 0.0),
    }


@router.get("/{quote_id}", response_class=HTMLResponse)
async def workspace(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    quote = _get_quote_or_404(db, quote_id)
    today = date.today()

    from app.services.statement_service import StatementService
    from app.services.document_links import related_documents
    bal = StatementService(db).get_customer_balance_summary(quote.customer_id)

    # §4.5 credit warn (WARN-ONLY) — same contract as SO / invoice workspaces.
    # A quote's value is never in open AR (AR starts at invoicing), so the full
    # quote subtotal is the prospective charge for the over-limit check.
    from app.services.customer_service import CustomerService
    credit_status = (
        CustomerService(db).credit_status(quote.customer, quote.subtotal)
        if quote.customer else None
    )

    return templates.TemplateResponse(
        request,
        "quotes/workspace.html",
        {
            "quote": quote,
            "credit_status": credit_status,
            "linked_documents": related_documents(db, quote),
            "sorted_lines": _tree_sort_lines(quote.lines),
            "QuoteStatus": QuoteStatus,
            "SOPaymentMode": SOPaymentMode,
            "today": today,
            "tomorrow": today + timedelta(days=1),
            # Customer balance chips
            "cust_open_balance": bal["open_balance"],
            "cust_overdue_balance": bal["overdue_balance"],
            "cust_credit_balance": bal["credit_balance"],
            "cust_credit_limit": bal["credit_limit"],
            "cust_payment_terms": bal["payment_terms"],
            "cust_cores_owed_qty": bal["cores_owed_qty"],
            "cust_last_payment_date": bal["last_payment_date"],
            "cust_open_invoice_count": bal["open_invoice_count"],
            # P2-D7 — structured Mark-Lost reason picker (UI wires the dropdown).
            "lost_reasons": list(LostReason),
            "lost_reason_labels": LOST_REASON_LABELS,
            "LostReason": LostReason,
            # Standardized engine make/model cascading picker (header).
            "engine_makes": engine_make_names(db),
            "engine_models_by_make": ENGINE_MODELS_BY_MAKE,
            # Seam 3 — customer relationship context while quoting
            **_quote_customer_ctx(db, quote),
            **_totals_ctx(quote),
            # §22 — shared Send dialog (consent-aware, log-only by default)
            "send": build_send_context(
                db,
                doc_label="Quote",
                doc_number=quote.quote_number,
                customer=quote.customer,
                total=(quote.subtotal or 0.0),
                action_url=f"/quotes/{quote.id}/send-message",
                lines=itemize_lines(_tree_sort_lines(quote.lines),
                                    include=lambda ln: ln.is_included),
                view_url=public_doc_url(db, "quote", quote.id),
            ),
        },
    )


# ── Print / PDF ───────────────────────────────────────────────────────────────

def _quote_print_context(db: Session, quote: Quote, request: Request,
                         user_id: int) -> dict:
    """Build the context that quotes/print.html consumes.

    Shared by GET /{quote_id}/print, GET /{quote_id}/pdf, and the §22 Send route
    so the emailed PDF is byte-identical to the on-screen/download print doc.
    """
    all_lines = _tree_sort_lines(quote.lines)

    included_lines = [ln for ln in all_lines if ln.is_included]
    alt_lines = [
        ln for ln in all_lines
        if ln.line_role == LineRole.UPGRADE_OPTION and not ln.is_included
    ]

    gross_total = round(sum(ln.unit_price * ln.qty for ln in included_lines), 2)
    subtotal = quote.subtotal
    discount_amount = round(gross_total - subtotal, 2)

    # Pre-format customer address lines to avoid Jinja2 filter edge cases.
    # customer_address_lines() (document_render.py) is the shared builder used by
    # every other document — it already appends phone as the trailing line. This
    # module used to carry its own duplicate copy AND the print template rendered
    # customer.phone a second time right after it, so every printed quote showed
    # the phone number twice (FULL_ERP_REVIEW_2026-07-04). Consolidated onto the
    # shared helper; the template's separate phone line was removed to match.
    addr_lines = customer_address_lines(quote.customer)

    return {
        "request":             request,
        "quote":               quote,
        "included_lines":      included_lines,
        "alt_lines":           alt_lines,
        "customer_addr_lines": addr_lines,
        "gross_total":         gross_total,
        "subtotal":            subtotal,
        "discount_amount":     discount_amount,
        "company":             get_company_dict(db),
        "prepared_by":         get_prepared_by(db, user_id),
    }


@router.get("/{quote_id}/print", response_class=HTMLResponse)
async def print_quote(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Standalone print-ready HTML page for a quote.
    Opens in a new tab — user hits Ctrl+P / Cmd+P or 'Print / Save PDF' button.
    No weasyprint dependency: the browser's built-in PDF engine handles rendering.
    """
    quote = _get_quote_or_404(db, quote_id)
    return templates.TemplateResponse(
        request,
        "quotes/print.html",
        _quote_print_context(db, quote, request, user_id),
    )


@router.get("/{quote_id}/pdf")
async def quote_pdf(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Server-side PDF generation via WeasyPrint.
    Returns a downloadable PDF file — no browser print dialog required.
    Used for emailing quotes directly from the app.
    """
    from fastapi.responses import Response as FastAPIResponse

    quote = _get_quote_or_404(db, quote_id)

    html_str = templates.env.get_template("quotes/print.html").render(
        **_quote_print_context(db, quote, request, user_id)
    )

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(
            string=html_str, base_url=str(request.base_url),
            url_fetcher=static_url_fetcher,
        ).write_pdf()
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
    customer_po_number: str = Form(""),
    customer_job_number: str = Form(""),
    esn: str = Form(""),
    engine_manufacturer: str = Form(""),
    engine_model: str = Form(""),
    updated_at: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Update quote-level notes, settings, and engine/job reference fields."""
    svc = QuoteService(db, user_id)
    try:
        svc.update_header(
            quote_id,
            {
                "notes": notes, "internal_notes": internal_notes,
                "discount_pct": discount_pct, "validity_days": validity_days,
                # Empty optional refs persist as NULL (mirrors invoice header handler).
                "customer_po_number": customer_po_number.strip() or None,
                "customer_job_number": customer_job_number.strip() or None,
                "esn": esn.strip() or None,
                "engine_manufacturer": engine_manufacturer.strip(),
                "engine_model": engine_model.strip(),
            },
            updated_at,
        )
    # ConcurrentEditError (optimistic lock, R9) is a RuntimeError — it needs
    # its own catch alongside ValueError; both surface via the ?error banner.
    except (ConcurrentEditError, ValueError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/quotes/{quote_id}?error={url_quote(str(exc))}", status_code=303,
        )
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
        request,
        "quotes/_totals.html",
        {"quote": quote, **_totals_ctx(quote)},
    )


@router.post("/{quote_id}/toggle-tax", response_class=HTMLResponse)
async def toggle_tax(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """§21 (6.16) — clerk toggles sales tax on a quote. Flips is_taxable; when
    turning tax ON with no snapshot yet, seeds the rate from the customer's
    tax_rate so the toggle actually applies tax. Returns the refreshed totals."""
    quote = _get_quote_or_404(db, quote_id)
    quote.is_taxable = not quote.is_taxable
    if quote.is_taxable and not quote.tax_rate_snapshot:
        quote.tax_rate_snapshot = quote.customer.tax_rate if quote.customer else 0.0
    db.commit()
    return templates.TemplateResponse(
        request,
        "quotes/_totals.html",
        {"quote": quote, **_totals_ctx(quote)},
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
    lines = svc.add_line(
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
    for ln in lines:
        db.refresh(ln)

    if len(lines) > 1 or parent_line_id is not None:
        # Return the full tbody when a core child was auto-added (len > 1) OR this is
        # a child/optional sub-line (parent_line_id set), so every row lands in its
        # right tree-sorted position. HX-Retarget/HX-Reswap override the JS call's
        # beforeend/beforebegin swap to an innerHTML swap on the tbody. This also
        # fixes the orphaned-<tr> bug: a bare <tr> swapped relative to a <tr> target
        # (#chips-N) gets reparented to <html> by the HTMX fragment parser.
        quote = _get_quote_or_404(db, quote_id)
        resp = templates.TemplateResponse(
            request,
            "quotes/_lines_tbody.html",
            {"lines": _tree_sort_lines(quote.lines)},
        )
        resp.headers["HX-Retarget"] = "#quote-lines-tbody"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp

    return templates.TemplateResponse(
        request,
        "quotes/_line_row.html",
        {"line": lines[0]},
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
    try:
        line, cascaded = svc.update_line(line_id, data)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(
            f'<div class="text-xs text-red-600 p-2">{exc}</div>', status_code=400,
        )
    db.refresh(line)
    if cascaded:
        quote = _get_quote_or_404(db, quote_id)
        resp = templates.TemplateResponse(
            request,
            "quotes/_lines_tbody.html",
            {"lines": _tree_sort_lines(quote.lines)},
        )
        resp.headers["HX-Retarget"] = "#quote-lines-tbody"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
    return templates.TemplateResponse(
        request,
        "quotes/_line_row.html",
        {"line": line},
    )


@router.delete("/{quote_id}/lines/{line_id}", response_class=HTMLResponse)
async def remove_line(
    quote_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Delete a line and its children (core, warranty, upgrade options).

    If the deleted line had children, return a full tbody refresh so all orphaned
    child rows are removed from the DOM in one shot.  Otherwise return empty so
    hx-swap="delete" removes just the single row.
    """
    had_children = QuoteService(db, user_id).remove_line(line_id)
    if had_children:
        quote = _get_quote_or_404(db, quote_id)
        resp = templates.TemplateResponse(
            request,
            "quotes/_lines_tbody.html",
            {"lines": _tree_sort_lines(quote.lines)},
        )
        resp.headers["HX-Retarget"] = "#quote-lines-tbody"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
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
        request,
        "quotes/_lines_tbody.html",
        {"lines": _tree_sort_lines(quote.lines)},
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
        request,
        "quotes/_line_row.html",
        {"line": line},
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
        request,
        "quotes/_line_row.html",
        {"line": line},
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
        request,
        "quotes/_lines_tbody.html",
        {"lines": _tree_sort_lines(quote.lines)},
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
        request,
        "quotes/_lines_tbody.html",
        {"lines": _tree_sort_lines(quote.lines)},
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
        request,
        "quotes/_line_row.html",
        {"line": line},
    )


# ── Autosave ─────────────────────────────────────────────────────────────────

@router.post("/{quote_id}/autosave", response_class=HTMLResponse)
async def autosave_quote(
    quote_id: int,
    notes: str = Form(""),
    internal_notes: str = Form(""),
    discount_pct: float = Form(0.0),
    validity_days: int = Form(30),
    customer_po_number: str = Form(""),
    customer_job_number: str = Form(""),
    esn: str = Form(""),
    engine_manufacturer: str = Form(""),
    engine_model: str = Form(""),
    updated_at: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """HTMX autosave — called every 2.5s on header field changes."""
    try:
        quote = QuoteService(db, user_id).update_header(
            quote_id,
            {
                "notes": notes, "internal_notes": internal_notes,
                "discount_pct": discount_pct, "validity_days": validity_days,
                "customer_po_number": customer_po_number.strip() or None,
                "customer_job_number": customer_job_number.strip() or None,
                "esn": esn.strip() or None,
                "engine_manufacturer": engine_manufacturer.strip(),
                "engine_model": engine_model.strip(),
            },
            updated_at,
        )
        # X-Updated-At lets the autosave JS refresh its hidden updated_at so
        # the NEXT save carries the fresh version and never self-conflicts.
        fresh_ts = quote.updated_at.isoformat() if quote.updated_at else ""
        return HTMLResponse(
            '<span class="text-xs text-green-600 font-medium">&#10003; Saved</span>',
            headers={"X-Updated-At": fresh_ts},
        )
    except ConcurrentEditError as exc:
        # Optimistic lock (R9): someone else saved this quote since the page
        # loaded. 409 — the sticky save bar shows the reload prompt.
        db.rollback()
        return HTMLResponse(
            f'<span class="text-xs text-red-500 font-medium">{exc}</span>',
            status_code=409,
        )
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
        request,
        "quotes/_follow_up_bar.html",
        {
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


@router.post("/{quote_id}/send-message")
async def send_quote_message(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """§22 — email/SMS the quote to the customer via the shared messaging core.

    Consent + rate limiting + the global log-only kill-switch all live in
    MessagingService; this route only renders the PDF, fans the selected channels
    out through perform_document_send, and flips a DRAFT quote → SENT when at
    least one channel actually went out. Never raises into the user: blocked or
    failed channels still return a 303 back to the workspace.
    """
    quote = _get_quote_or_404(db, quote_id)

    form = await request.form()
    channels = form.getlist("channels")
    to_email = (form.get("to_email") or "").strip()
    to_phone = (form.get("to_phone") or "").strip()
    email_subject = form.get("email_subject") or ""
    email_body = form.get("email_body") or ""
    sms_body = form.get("sms_body") or ""

    # Best-effort PDF attachment (None on hosts without WeasyPrint/GTK — email
    # then sends without the attachment, matching the /pdf fallback).
    pdf_bytes = render_pdf_or_none(
        templates.env,
        "quotes/print.html",
        str(request.base_url),
        **_quote_print_context(db, quote, request, user_id),
    )

    result = perform_document_send(
        db,
        user_id,
        customer_id=quote.customer_id,
        channels=channels,
        to_email=to_email,
        to_phone=to_phone,
        email_subject=email_subject,
        email_body=email_body,
        sms_body=sms_body,
        pdf_bytes=pdf_bytes,
        pdf_filename=f"{quote.quote_number}.pdf",
        related_entity_type="quote",
        related_entity_id=quote.id,
    )

    n_sent = len(result["sent"])
    # First successful send promotes a DRAFT quote to SENT (mirrors the legacy
    # /send button). Already-SENT quotes can be re-sent without a status change.
    if n_sent and quote.status == QuoteStatus.DRAFT:
        QuoteService(db, user_id).send_quote(quote.id)

    qs = f"?sent={n_sent}"
    if result["failed"] or result["blocked"]:
        qs += "&send_error=1"
    return RedirectResponse(f"/quotes/{quote_id}{qs}", status_code=303)


@router.post("/{quote_id}/mark-lost")
async def mark_lost(
    quote_id: int,
    lost_reason: str = Form(""),
    note: str = Form(""),
    competitor_name: str = Form(""),
    competitor_price: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # P2-D7 — structured lost-reason picker (reason + optional note; competitor
    # name/price when reason = competitor). Free text still tolerated (→ OTHER).
    # competitor_price arrives as a raw form string: a blank <input type=number>
    # submits "" which a `float | None` param would 422 on — coerce here instead.
    try:
        comp_price: float | None = (
            float(competitor_price) if competitor_price.strip() else None
        )
    except ValueError:
        comp_price = None
    QuoteService(db, user_id).mark_lost(
        quote_id,
        lost_reason or "No reason given",
        note=note,
        competitor_name=competitor_name or None,
        competitor_price=comp_price,
    )
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/reactivate")
async def reactivate_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    QuoteService(db, user_id).reactivate(quote_id)
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/duplicate")
async def duplicate_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # R2 — re-quote the same job: copy all lines into a fresh DRAFT quote.
    # Allowed from any status (duplicating a converted/declined/expired quote
    # into a fresh one is the whole point); the service owns numbering + cloning.
    dup = QuoteService(db, user_id).duplicate_quote(quote_id)
    # ?created=1 → base.html renders the green "Record created" flash banner.
    return RedirectResponse(f"/quotes/{dup.id}?created=1", status_code=303)


# ── Conversions ───────────────────────────────────────────────────────────────

@router.post("/{quote_id}/convert-to-so")
async def convert_to_so(
    quote_id: int,
    payment_mode: str = Form(SOPaymentMode.NONE),
    confirm_credit_hold: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # §21 — credit-hold = WARN (owner #6). If the quote's customer is on hold and
    # the clerk hasn't acknowledged it, bounce back to the quote with a prominent
    # warning + a "Convert anyway" path. The SO-create path was the audit's named
    # gap alongside invoice finalize.
    if confirm_credit_hold.lower() not in {"1", "true", "on", "yes"}:
        from app.services.customer_service import CustomerService
        quote = _get_quote_or_404(db, quote_id)
        if quote.customer and CustomerService(db).is_on_credit_hold(quote.customer):
            return RedirectResponse(f"/quotes/{quote_id}?credit_hold=1", status_code=303)

    try:
        so = QuoteService(db, user_id).convert_to_sales_order(quote_id, payment_mode)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/quotes/{quote_id}?error={url_quote(str(exc))}", status_code=303,
        )
    return RedirectResponse(f"/sales-orders/{so.id}", status_code=303)


@router.post("/{quote_id}/convert-to-invoice")
async def convert_to_invoice(
    quote_id: int,
    confirm_credit_hold: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # Item 13 — credit-hold WARN gate, mirroring convert-to-so. The list-page
    # "→ Invoice" button POSTed straight here with no gate, so a held customer
    # could be converted to a live invoice in one click. Bounce back with the
    # warning unless the clerk has acknowledged it (confirm_credit_hold=1).
    if confirm_credit_hold.lower() not in {"1", "true", "on", "yes"}:
        from app.services.customer_service import CustomerService
        quote = _get_quote_or_404(db, quote_id)
        if quote.customer and CustomerService(db).is_on_credit_hold(quote.customer):
            return RedirectResponse(f"/quotes/{quote_id}?credit_hold=1", status_code=303)

    try:
        invoice = QuoteService(db, user_id).convert_to_invoice(quote_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/quotes/{quote_id}?error={url_quote(str(exc))}", status_code=303,
        )
    return RedirectResponse(f"/invoices/{invoice.id}", status_code=303)
