"""
app/routers/invoices.py
========================
Invoice list, workspace (create→edit→finalize), payment, void, print/PDF.

Phase A — Transaction Workspace pattern:
  - GET  /invoices/{id}      → workspace.html (renders editable when DRAFT, locked when OPEN+)
  - GET  /invoices/new       → minimal customer-picker slide-over content (HTMX target)
  - POST /invoices/new       → create_draft(customer_id), 303 → /invoices/{id}
  - HTMX endpoints for header / line CRUD return the partial that needs swapping.

All mutations go through InvoiceService (sole owner of invoice.status).
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import (
    ENGINE_MODELS_BY_MAKE,
    InvoiceStatus, LineType, PaymentMethod, QBOSyncStatus,
)
from app.deps import get_current_user_id, get_db
from app.services.category_service import engine_make_names
from app.services.document_messaging import (
    build_send_context,
    itemize_lines,
    perform_document_send,
    render_pdf_or_none,
)
from app.services.public_links import public_doc_url
from app.services.document_render import (
    get_company_dict, get_prepared_by, static_url_fetcher,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product, CrossReference, ProductSerialNumber
from app.settings_utils import get_setting_value_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_invoice_or_redirect(db: Session, invoice_id: int) -> Invoice | RedirectResponse:
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)
    return inv


def _require_draft(db: Session, invoice_id: int) -> Invoice | HTMLResponse:
    """
    Pre-flight guard for HTMX mutation routes.
    Returns the Invoice if it is DRAFT, or an HTMLResponse(400) if it is locked.
    Caller should check `isinstance(result, HTMLResponse)` and return it immediately.
    """
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return HTMLResponse(
            f'<div class="text-xs text-red-600 p-2">Invoice not found.</div>',
            status_code=404,
        )
    if inv.status != InvoiceStatus.DRAFT:
        return HTMLResponse(
            f'<div class="text-xs text-red-600 p-2">'
            f'Invoice {inv.invoice_number} is locked (status: {inv.status.upper()}). '
            f'Void and reissue to make corrections.'
            f'</div>',
            status_code=400,
        )
    return inv


def _build_invoice_panel_metrics(m: dict) -> list[dict]:
    """Descriptor list for the generic intelligence_panel() macro (pre-formatted
    `value` strings, optional tone/hint/margin). Order per §5.8 v2: Lifetime
    Sales, Open AR, Last Purchase, Outstanding Cores, Open Warranty Claims, then
    Profit/Margin (margin=True → gated behind the showMargin toggle)."""
    def _money(v) -> str:
        return "$" + format(float(v or 0), ",.2f")
    lp = m.get("last_purchase")
    return [
        {"label": "Lifetime Sales", "value": _money(m.get("customer_lifetime_sales")),
         "hint": "Net invoiced, less returns/credits"},
        {"label": "Open AR", "value": _money(m.get("open_ar")),
         "tone": ("warn" if (m.get("open_ar") or 0) > 0 else "muted")},
        {"label": "Last Purchase",
         "value": (lp.strftime("%b %d, %Y") if lp else "—"), "tone": "muted"},
        {"label": "Outstanding Cores", "value": str(m.get("outstanding_cores") or 0),
         "tone": ("warn" if (m.get("outstanding_cores") or 0) else "muted")},
        {"label": "Open Warranty Claims", "value": str(m.get("open_warranty_claims") or 0),
         "tone": ("warn" if (m.get("open_warranty_claims") or 0) else "muted")},
        {"label": "Profit", "value": _money(m.get("profit")),
         "tone": ("good" if (m.get("profit") or 0) >= 0 else "bad"), "margin": True},
        {"label": "Margin", "value": format(float(m.get("margin_pct") or 0), ".1f") + "%",
         "tone": ("good" if (m.get("margin_pct") or 0) >= 0 else "bad"), "margin": True},
    ]


def _workspace_context(
    db: Session,
    request: Request,
    invoice: Invoice,
    current_user_id: int = 1,
) -> dict:
    """Build the full context dict the workspace template expects."""
    from app.services.invoice_service import InvoiceService
    from app.services.statement_service import StatementService
    totals = InvoiceService(db, current_user_id).calculate_totals(invoice.id)

    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    cc_raw = get_setting_value_db(db, "cc_surcharge_pct", "3.0") or "3.0"
    try:
        cc_surcharge_pct = float(cc_raw)
    except (TypeError, ValueError):
        cc_surcharge_pct = 3.0

    bal = StatementService(db).get_customer_balance_summary(invoice.customer_id)

    # ── Open customer-owes cores on THIS invoice (After-Sale Service card) ──
    # Joined via the core child line: finalise() stamps each CoreCharge with
    # invoice_line_id = <core line> (invoice_service.py ~635), so this is exact.
    from app.constants import CoreDirection, CoreStatus
    from app.models.core import CoreCharge
    invoice_cores = (
        db.query(CoreCharge)
        .join(InvoiceLine, CoreCharge.invoice_line_id == InvoiceLine.id)
        .filter(
            InvoiceLine.invoice_id == invoice.id,
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
        )
        .order_by(CoreCharge.id)
        .all()
    )

    # ── §5.8 Invoice Intelligence panel (P2-D3 — margin gated client-side) ──
    from app.services.invoice_metrics_service import InvoiceMetricsService
    invoice_intelligence = InvoiceMetricsService(db).intelligence_for(invoice)
    # Descriptor list for the generic intelligence_panel() macro: customer-
    # relationship context while invoicing. Profit/Margin carry margin=True so the
    # macro keeps them behind the per-user showMargin toggle (P2-D3). UI lane wires
    # intelligence_panel(invoice_panel_metrics); the invoice_intelligence dict
    # stays for the current invoice_intelligence_panel() call (no regression).
    invoice_panel_metrics = _build_invoice_panel_metrics(invoice_intelligence)

    # ── §4.5 credit warn (WARN-ONLY) — same contract as customer detail ──────
    # A DRAFT invoice isn't in open AR yet, so its total is the prospective charge;
    # a posted invoice is already counted in open AR → prospective 0 (no double-count).
    from app.services.customer_service import CustomerService
    _prospective = invoice.total if invoice.status == InvoiceStatus.DRAFT else 0.0
    credit_status = (
        CustomerService(db).credit_status(invoice.customer, _prospective)
        if invoice.customer else None
    )

    # ── §22 Send dialog context (shared messaging) ───────────────────────────
    send_ctx = build_send_context(
        db,
        doc_label="Invoice",
        doc_number=invoice.invoice_number,
        customer=invoice.customer,
        total=(invoice.total or 0.0),
        action_url=f"/invoices/{invoice.id}/send-message",
        lines=itemize_lines(invoice.lines),
        view_url=public_doc_url(db, "invoice", invoice.id),
    )

    return {
        "invoice": invoice,
        "totals": totals,
        "customers": customers,
        "send": send_ctx,
        "invoice_cores": invoice_cores,
        "invoice_intelligence": invoice_intelligence,
        "invoice_panel_metrics": invoice_panel_metrics,
        "credit_status": credit_status,
        "editable": invoice.status == InvoiceStatus.DRAFT,
        "cc_surcharge_pct": cc_surcharge_pct,
        "InvoiceStatus": InvoiceStatus,
        "LineType": LineType,
        "PaymentMethod": PaymentMethod,
        # Standardized engine make/model cascading picker (header vehicle block —
        # same wiring as the quote workspace).
        "engine_makes": engine_make_names(db),
        "engine_models_by_make": ENGINE_MODELS_BY_MAKE,
        # Customer balance chips
        "cust_open_balance": bal["open_balance"],
        "cust_overdue_balance": bal["overdue_balance"],
        "cust_credit_balance": bal["credit_balance"],
        "cust_credit_limit": bal["credit_limit"],
        "cust_payment_terms": bal["payment_terms"],
        "cust_cores_owed_qty": bal["cores_owed_qty"],
        "cust_last_payment_date": bal["last_payment_date"],
        "cust_open_invoice_count": bal["open_invoice_count"],
    }


# ── L2 list tab definitions (JAKS_UI_Change_Plan.md §6 — Invoice List brief) ──
# Maps user-facing tab slug → underlying invoice statuses it covers.
# "all" → every non-VOID status (VOID kept out so it doesn't pollute "all").
# "draft" → not yet finalized.  "open" → finalized, nothing paid yet.
# "partial" → some payment received.  "overdue" is virtual — open/partial whose
# due_date has passed (see list query).
INV_TAB_GROUPS: dict[str, list[str]] = {
    "all":     [InvoiceStatus.DRAFT, InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID],
    "draft":   [InvoiceStatus.DRAFT],
    "open":    [InvoiceStatus.OPEN],
    "partial": [InvoiceStatus.PARTIAL],
    "overdue": [InvoiceStatus.OPEN, InvoiceStatus.PARTIAL],  # + due_date < now
    "paid":    [InvoiceStatus.PAID],
    "void":    [InvoiceStatus.VOID],
}

# Old individual status → grouped tab slug (backward-compat with ?status= links)
_INV_STATUS_TO_TAB: dict[str, str] = {
    InvoiceStatus.DRAFT:   "draft",
    InvoiceStatus.OPEN:    "open",
    InvoiceStatus.PARTIAL: "partial",
    InvoiceStatus.PAID:    "paid",
    InvoiceStatus.VOID:    "void",
}

INV_LIST_TABS: list[tuple[str, str]] = [
    ("all",     "All"),
    ("draft",   "Draft"),
    ("open",    "Open"),
    ("partial", "Partial"),
    ("overdue", "Overdue"),
    ("paid",    "Paid"),
    ("void",    "Void"),
    # QBO-dimension tabs (orthogonal to the status tabs above)
    ("not_synced",          "Not Synced"),
    ("sync_failed",         "Sync Failed"),
    ("modified_since_sync", "Modified"),
]

# QBO filter tabs are NOT status groups — they filter on the QBO sync columns.
_FINALIZED_STATUSES = [InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID]
_QBO_TAB_SLUGS = ("not_synced", "sync_failed", "modified_since_sync")


def _apply_invoice_list_filters(db: Session, query, tab: str, q: str, now: datetime):
    """Tab + search filtering shared by the list view and the CSV export so
    "export what I see" matches the list exactly."""
    from sqlalchemy import or_, func

    if tab in _QBO_TAB_SLUGS:
        if tab == "not_synced":
            query = query.filter(Invoice.status.in_(_FINALIZED_STATUSES),
                                 Invoice.qbo_invoice_id.is_(None))
        elif tab == "sync_failed":
            query = query.filter(Invoice.qbo_sync_status == QBOSyncStatus.ERROR)
        else:  # modified_since_sync
            query = query.filter(Invoice.qbo_last_synced_at.isnot(None),
                                 Invoice.updated_at > Invoice.qbo_last_synced_at)
    else:
        statuses = INV_TAB_GROUPS.get(tab, INV_TAB_GROUPS["all"])
        query = query.filter(Invoice.status.in_(statuses))
        if tab == "overdue":
            query = query.filter(Invoice.due_date.isnot(None), Invoice.due_date < now)
    if q:
        like = f"%{q.strip()}%"
        # Line-based matches via id-subqueries (no row duplication): product SKU /
        # cross-ref number on any line, and sold-serial on any line.
        line_match = (
            db.query(InvoiceLine.invoice_id)
            .join(Product, InvoiceLine.product_id == Product.id)
            .outerjoin(CrossReference, CrossReference.product_id == Product.id)
            .filter(or_(Product.sku.ilike(like), CrossReference.ref_number.ilike(like)))
        )
        serial_match = (
            db.query(InvoiceLine.invoice_id)
            .join(ProductSerialNumber, ProductSerialNumber.invoice_line_id == InvoiceLine.id)
            .filter(ProductSerialNumber.serial_number.ilike(like))
        )
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(like),
                # de-dash so "inv20260021" still finds "INV-2026-0021"
                func.replace(func.replace(Invoice.invoice_number, "-", ""), " ", "").ilike(
                    "%" + q.replace("-", "").replace(" ", "") + "%"),
                Customer.company_name.ilike(like),
                Customer.account_number.ilike(like),   # #5
                Customer.phone.ilike(like),
                Invoice.customer_po_number.ilike(like),
                Invoice.esn.ilike(like),            # ESN already matched — kept
                Invoice.id.in_(line_match),
                Invoice.id.in_(serial_match),
            )
        )
    return query


# ── List ──────────────────────────────────────────────────────────────────────
#
# L2 — Operational List Screen Standard (JAKS_UI_Change_Plan.md §2).
# Mirrors the Products / PO List pattern: grouped tab filter with counts from the
# *unfiltered* dataset, search across invoice #, customer, PO#, and ESN, and a
# per-row preview dock (loaded via /invoices/preview/{id}).

@router.get("/", response_class=HTMLResponse)
def invoice_list(
    request: Request,
    tab: str = "all",
    q: str = "",
    sort: str = "created",
    direction: str = "desc",
    page: int = 1,
    # `status` kept for backward-compat with old links (?status=open).
    status: str = "",
    db: Session = Depends(get_db),
):
    from sqlalchemy import func
    from app.utils import apply_sort

    # Backward-compat: ?status=open → ?tab=open, etc.
    if status and tab == "all":
        tab = _INV_STATUS_TO_TAB.get(status, "all")

    now = datetime.utcnow()

    # Counts — always from the *full* unfiltered dataset so tab counts are stable
    # regardless of which tab is active (mirrors Products / PO List behavior).
    raw_counts = dict(
        db.query(Invoice.status, func.count(Invoice.id))
          .group_by(Invoice.status)
          .all()
    )
    overdue_count = (
        db.query(func.count(Invoice.id))
          .filter(
              Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
              Invoice.due_date.isnot(None),
              Invoice.due_date < now,
          )
          .scalar()
    ) or 0

    def _group_count(slug: str) -> int:
        return sum(raw_counts.get(s, 0) for s in INV_TAB_GROUPS.get(slug, []))

    # QBO-dimension counts (unfiltered, like the status counts above).
    not_synced_count = (
        db.query(func.count(Invoice.id))
          .filter(Invoice.status.in_(_FINALIZED_STATUSES), Invoice.qbo_invoice_id.is_(None))
          .scalar()
    ) or 0
    sync_failed_count = (
        db.query(func.count(Invoice.id))
          .filter(Invoice.qbo_sync_status == QBOSyncStatus.ERROR)
          .scalar()
    ) or 0
    modified_since_sync_count = (
        db.query(func.count(Invoice.id))
          .filter(Invoice.qbo_last_synced_at.isnot(None),
                  Invoice.updated_at > Invoice.qbo_last_synced_at)
          .scalar()
    ) or 0

    counts = {
        "all":     _group_count("all"),
        "draft":   _group_count("draft"),
        "open":    _group_count("open"),
        "partial": _group_count("partial"),
        "overdue": overdue_count,
        "paid":    _group_count("paid"),
        "void":    _group_count("void"),
        # QBO dimension
        "not_synced":          not_synced_count,
        "sync_failed":         sync_failed_count,
        "modified_since_sync": modified_since_sync_count,
    }

    # Filtered query.
    # Eager-load everything each row renders: Total / Balance Due walk
    # inv.lines (totals engine) + inv.allocations (amount_paid), and the
    # source-document sub-line walks inv.sales_order — without these options a
    # 200-row page fires ~3 lazy SELECTs per row (N+1).
    from sqlalchemy.orm import joinedload, selectinload
    query = (
        db.query(Invoice)
        .join(Customer)
        .options(
            joinedload(Invoice.customer),
            selectinload(Invoice.lines),
            selectinload(Invoice.allocations),
            joinedload(Invoice.sales_order),
        )
    )
    query = _apply_invoice_list_filters(db, query, tab, q, now)
    # Sort (#4 — whitelisted keys, asc/desc). total/balance are computed
    # properties (the totals engine), not columns — they are ordered in Python
    # after .all() per the plan's non-column-sort rule (mirrors the quotes
    # list's "margin" sort).
    _INV_SORT = {
        "created":  Invoice.created_at,
        "number":   Invoice.invoice_number,
        "due":      Invoice.due_date,   # legacy alias (pre-R2 links)
        "due_date": Invoice.due_date,
        "customer": Customer.company_name,
    }
    _computed_sort = sort if sort in ("total", "balance") else None
    query, sort, direction = apply_sort(
        query, _INV_SORT, (None if _computed_sort else sort), direction, default="created"
    )
    # §21 — real pagination (was a silent limit(200) that hid older invoices).
    from app.utils import compute_pager
    total_rows = query.order_by(None).count()
    pager = compute_pager(page, total_rows, per_page=50)
    if _computed_sort:
        # total/balance are computed properties → must sort the full set in Python,
        # then slice the page (cap the load so a huge filtered set can't blow up).
        sort = _computed_sort  # echo the active key back to the sort headers
        rows = query.limit(2000).all()
        rows.sort(
            key=(lambda i: i.total) if _computed_sort == "total" else (lambda i: i.balance_due),
            reverse=(direction == "desc"),
        )
        invoices = rows[pager["offset"]:pager["offset"] + pager["per_page"]]
    else:
        invoices = query.limit(pager["per_page"]).offset(pager["offset"]).all()
    return templates.TemplateResponse(
        request,
        "invoices/list.html",
        {
            "invoices": invoices,
            "tabs": INV_LIST_TABS,
            "tab": tab,
            "q": q,
            "sort": sort,
            "direction": direction,
            "counts": counts,
            "InvoiceStatus": InvoiceStatus,
            "now": now,
            "pager": pager,
        },
    )


# ── Export CSV — MUST be before /{invoice_id} ────────────────────────────────

@router.get("/export.csv")
def invoice_export_csv(
    tab: str = "all",
    q: str = "",
    # `status` kept for backward-compat with old links (?status=open).
    status: str = "",
    db: Session = Depends(get_db),
):
    """
    Stream the current filtered invoice list as a CSV download.
    Respects the same tab/q filters as the list view so "export what I see" works.
    """
    from sqlalchemy.orm import joinedload

    if status and tab == "all":
        tab = _INV_STATUS_TO_TAB.get(status, "all")
    now = datetime.utcnow()

    query = (
        db.query(Invoice)
        .join(Customer)
        .options(joinedload(Invoice.customer))
    )
    query = _apply_invoice_list_filters(db, query, tab, q, now)
    invoices = query.order_by(Invoice.created_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "invoice_number", "status", "customer", "customer_po_number", "esn",
        "created", "due_date", "subtotal", "tax", "total",
        "amount_paid", "balance_due", "overdue",
    ])
    for inv in invoices:
        writer.writerow([
            inv.invoice_number,
            inv.status,
            inv.customer.company_name if inv.customer else "",
            inv.customer_po_number or "",
            inv.esn or "",
            inv.created_at.strftime("%Y-%m-%d") if inv.created_at else "",
            inv.due_date.strftime("%Y-%m-%d") if inv.due_date else "",
            f"{inv.subtotal:.2f}",
            f"{inv.tax_amount:.2f}",
            f"{inv.total:.2f}",
            f"{inv.amount_paid:.2f}",
            f"{inv.balance_due:.2f}",
            "yes" if inv.is_overdue else "no",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoices.csv"},
    )


# ── List row preview panel (HTMX partial) ────────────────────────────────────

@router.get("/preview/{invoice_id}", response_class=HTMLResponse)
def invoice_preview_panel(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Bottom preview dock body, loaded by htmx.ajax() on row click in the list."""
    from sqlalchemy.orm import joinedload
    inv = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.customer),
            joinedload(Invoice.lines),
            joinedload(Invoice.allocations),
        )
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not inv:
        return HTMLResponse(
            '<div class="px-6 py-5 text-sm text-gray-400">Invoice not found.</div>',
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "invoices/_preview_panel.html",
        {
            "inv": inv,
            "InvoiceStatus": InvoiceStatus,
            "LineType": LineType,
            "now": datetime.utcnow(),
        },
    )


# ── New Invoice — minimal customer-picker → create draft → redirect to workspace ─

@router.get("/new", response_class=HTMLResponse)
def invoice_new_picker(
    request: Request,
    customer_id: int = 0,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Two paths:
      • ?customer_id=N supplied → skip the picker, create draft immediately, 303 → workspace
      • no customer_id → render the picker (slide-over content OR standalone)
    The picker is a doorway, never a destination. The workspace is the workflow.
    """
    if customer_id:
        from app.services.invoice_service import InvoiceService
        try:
            invoice = InvoiceService(db, user_id).create_draft(customer_id=customer_id)
            return RedirectResponse(f"/invoices/{invoice.id}", status_code=303)
        except ValueError as exc:
            db.rollback()
            return RedirectResponse(
                f"/invoices/?error={url_quote(str(exc))}", status_code=303
            )

    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "invoices/_new_picker.html",
        {"customers": customers},
    )


@router.post("/new", response_class=RedirectResponse)
async def invoice_create_draft(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Create a minimal DRAFT invoice and redirect to its workspace.
    The workspace is where all editing happens."""
    from app.services.invoice_service import InvoiceService

    form = await request.form()
    customer_id_raw = str(form.get("customer_id", "")).strip()
    if not customer_id_raw:
        return RedirectResponse(
            f"/invoices/?error={url_quote('Customer required to create an invoice.')}",
            status_code=303,
        )
    try:
        customer_id = int(customer_id_raw)
        invoice = InvoiceService(db, user_id).create_draft(customer_id=customer_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating draft invoice")
        return RedirectResponse(
            f"/invoices/?error={url_quote('Unexpected error — invoice was not created.')}",
            status_code=303,
        )
    return RedirectResponse(f"/invoices/{invoice.id}", status_code=303)


# ── Workspace (DRAFT editable; OPEN/PARTIAL/PAID/VOID locked read-only) ──────

@router.get("/{invoice_id}", response_class=HTMLResponse)
def invoice_workspace(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Full workspace. Template uses `editable` flag to switch between input fields
    (DRAFT) and static text (OPEN/PARTIAL/PAID/VOID).
    """
    inv = _get_invoice_or_redirect(db, invoice_id)
    if isinstance(inv, RedirectResponse):
        return inv
    from app.services.document_links import related_documents
    ctx = _workspace_context(db, request, inv, user_id)
    ctx["linked_documents"] = related_documents(db, inv)
    return templates.TemplateResponse(
        request,
        "invoices/workspace.html",
        ctx,
    )


# ── Header edits (HTMX) ───────────────────────────────────────────────────────

@router.post("/{invoice_id}/header", response_class=HTMLResponse)
async def invoice_update_header(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Apply header field changes from the workspace. Returns the totals partial
    since most header fields (tax, discount, CC fee) affect totals."""
    from app.services.invoice_service import InvoiceService

    guard = _require_draft(db, invoice_id)
    if isinstance(guard, HTMLResponse):
        return guard

    form = await request.form()
    data: dict = {}

    for field in ("customer_po_number", "customer_job_number", "esn",
                  "engine_manufacturer", "engine_model", "notes", "internal_notes"):
        if field in form:
            val = str(form.get(field, "")).strip()
            data[field] = (val or None) if field in {"customer_po_number", "customer_job_number", "esn"} else val

    if "discount_pct" in form:
        data["discount_pct"] = float(form.get("discount_pct") or 0)
    if "tax_rate" in form:
        data["tax_rate"] = float(form.get("tax_rate") or 0)
    # Checkbox fields: a hidden input sends "0" unconditionally; the checkbox
    # sends "1" when checked. getlist() collects both values; "1" wins if present.
    # This is done UNCONDITIONALLY (no "if field in form" gate) so that unchecking
    # a box that WAS checked correctly writes False — the old gated approach was the
    # §1.9e bug: when unchecked, the field was absent from the form so the old value
    # was never cleared.
    data["is_taxable"] = "1" in form.getlist("is_taxable")
    data["apply_cc_surcharge"] = "1" in form.getlist("apply_cc_surcharge")
    if "due_date" in form:
        due_raw = str(form.get("due_date", "")).strip()
        try:
            data["due_date"] = datetime.strptime(due_raw, "%Y-%m-%d") if due_raw else None
        except ValueError:
            data["due_date"] = None

    try:
        InvoiceService(db, user_id).update_header(invoice_id, data)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(
            f'<div class="text-xs text-red-600">{exc}</div>', status_code=400
        )

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    return templates.TemplateResponse(
        request,
        "invoices/_totals_panel.html",
        _workspace_context(db, request, inv, user_id),
    )


# ── Change customer (HTMX) ────────────────────────────────────────────────────

@router.post("/{invoice_id}/change-customer", response_class=RedirectResponse)
async def invoice_change_customer(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Reassign draft invoice to a new customer.
    Form fields: customer_id, recalc_pricing (bool)."""
    from app.services.invoice_service import InvoiceService
    form = await request.form()

    new_customer_id_raw = str(form.get("customer_id", "")).strip()
    if not new_customer_id_raw:
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Customer required.')}",
            status_code=303,
        )
    recalc = str(form.get("recalc_pricing", "")).lower() in {"1", "true", "on", "yes"}

    try:
        InvoiceService(db, user_id).change_customer(
            invoice_id, int(new_customer_id_raw), recalc_pricing=recalc
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


# ── Line CRUD (HTMX — returns the lines+totals partial) ──────────────────────

@router.post("/{invoice_id}/lines", response_class=HTMLResponse)
async def invoice_add_line(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Add a line to a draft invoice. Auto-adds linked core child if product has core."""
    from app.services.invoice_service import InvoiceService

    guard = _require_draft(db, invoice_id)
    if isinstance(guard, HTMLResponse):
        return guard

    form = await request.form()
    pid_raw = str(form.get("product_id", "")).strip()
    product_id = int(pid_raw) if pid_raw else None

    qty_raw = str(form.get("qty", "1")).strip()
    price_raw = str(form.get("unit_price", "0")).strip()
    cost_raw = str(form.get("unit_cost", "0")).strip()

    data = {
        "description": str(form.get("description", "")).strip(),
        "qty": max(1, int(qty_raw)) if qty_raw else 1,
        "unit_price": float(price_raw) if price_raw else 0.0,
        "unit_cost": float(cost_raw) if cost_raw else 0.0,
        "line_type": str(form.get("line_type", LineType.PRODUCT)).strip() or LineType.PRODUCT,
    }

    try:
        InvoiceService(db, user_id).add_line(invoice_id, product_id, data)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600">{exc}</div>', status_code=400)

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    return templates.TemplateResponse(
        request,
        "invoices/_lines_and_totals.html",
        _workspace_context(db, request, inv, user_id),
    )


@router.post("/{invoice_id}/lines/{line_id}", response_class=HTMLResponse)
async def invoice_update_line(
    invoice_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Update line (qty, description, price, discount). Qty change cascades to locked children."""
    from app.services.invoice_service import InvoiceService

    guard = _require_draft(db, invoice_id)
    if isinstance(guard, HTMLResponse):
        return guard

    form = await request.form()
    data: dict = {}
    if "description" in form:
        data["description"] = str(form.get("description", "")).strip()
    if "qty" in form:
        raw = str(form.get("qty", "")).strip()
        if raw:
            data["qty"] = max(0, int(raw))
    if "unit_price" in form:
        raw = str(form.get("unit_price", "")).strip()
        if raw:
            data["unit_price"] = float(raw)
    if "discount_pct" in form:
        raw = str(form.get("discount_pct", "")).strip()
        data["discount_pct"] = float(raw) if raw else 0.0

    try:
        InvoiceService(db, user_id).update_line(line_id, data)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600">{exc}</div>', status_code=400)

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    return templates.TemplateResponse(
        request,
        "invoices/_lines_and_totals.html",
        _workspace_context(db, request, inv, user_id),
    )


@router.post("/{invoice_id}/lines/{line_id}/delete", response_class=HTMLResponse)
def invoice_delete_line(
    invoice_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Delete a line. Cascades to auto-generated children (core lines)."""
    from app.services.invoice_service import InvoiceService

    guard = _require_draft(db, invoice_id)
    if isinstance(guard, HTMLResponse):
        return guard

    try:
        InvoiceService(db, user_id).remove_line(line_id)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600">{exc}</div>', status_code=400)

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    return templates.TemplateResponse(
        request,
        "invoices/_lines_and_totals.html",
        _workspace_context(db, request, inv, user_id),
    )


@router.post("/{invoice_id}/lines/{line_id}/unlink", response_class=HTMLResponse)
def invoice_unlink_line(
    invoice_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Unlink an auto-generated child (e.g. core) from its parent.
    Parent edits will no longer cascade after this."""
    from app.services.invoice_service import InvoiceService

    guard = _require_draft(db, invoice_id)
    if isinstance(guard, HTMLResponse):
        return guard

    try:
        InvoiceService(db, user_id).unlink_line_from_parent(line_id)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600">{exc}</div>', status_code=400)

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    return templates.TemplateResponse(
        request,
        "invoices/_lines_and_totals.html",
        _workspace_context(db, request, inv, user_id),
    )


# ── Product search ───────────────────────────────────────────────────────────
# The per-doc /invoices/_/product-search HTML endpoint was removed after the §8H
# migration (its partial invoices/_search_results.html is gone). The invoice
# workspace line-adder now calls GET /line-items/product-search (JSON).


# ── Finalize ──────────────────────────────────────────────────────────────────

@router.post("/{invoice_id}/finalise", response_class=RedirectResponse)
async def invoice_finalise(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Finalize a DRAFT invoice. Returns to workspace on success (now locked).
    Form may include allow_negative_inventory=1 for explicit admin override.

    TODO (Phase 1 follow-up): When the invoice contains core-eligible lines,
    surface the "Print Core Return Slip" popup described in MASTER_PLAN.md §15.
    Document Engine Series intentionally does not change finalize behavior;
    the slip print route (/cores/slips/{id}/print) is already live and the
    popup can call it once CoreSlip creation hooks into invoice finalize.
    """
    from app.services.invoice_service import InvoiceService

    form = await request.form()
    allow_negative = str(form.get("allow_negative_inventory", "")).lower() in {"1", "true", "on", "yes"}

    # §21 — credit-hold = WARN (owner decision 6.16, not a hard block). If the
    # customer is on credit hold and the operator has not yet acknowledged it,
    # bounce back to the workspace with a prominent warning + a "Finalize anyway"
    # path (re-POST carries confirm_credit_hold=1). Once acknowledged we proceed.
    confirm_hold = str(form.get("confirm_credit_hold", "")).lower() in {"1", "true", "on", "yes"}
    if not confirm_hold:
        from app.services.customer_service import CustomerService
        _inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if _inv and _inv.customer and CustomerService(db).is_on_credit_hold(_inv.customer):
            return RedirectResponse(
                f"/invoices/{invoice_id}?credit_hold=1",
                status_code=303,
            )

    try:
        InvoiceService(db, user_id).finalise(invoice_id, allow_negative_inventory=allow_negative)
    except PermissionError:
        # C3 — SALES role lacks FINALIZE_INVOICE. Friendly message, not a 500.
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('You do not have permission to finalize invoices. Ask an admin or bookkeeper.')}",
            status_code=303,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error finalising invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Unexpected error — invoice was not finalised.')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/invoices/{invoice_id}?ok={url_quote('Invoice finalized successfully. Status: OPEN')}",
        status_code=303,
    )


# ── Print / PDF ───────────────────────────────────────────────────────────────

def _customer_addr_lines(c) -> list[str]:
    """Customer address block for printed documents (shared by print + PDF)."""
    addr_lines: list[str] = [ln for ln in [c.address_line1, c.address_line2] if ln and ln.strip()]
    city_parts = [p for p in [c.city, c.state] if p and p.strip()]
    city_line = ", ".join(city_parts)
    if city_line and c.zip_code and c.zip_code.strip():
        city_line += " " + c.zip_code.strip()
    elif not city_line and c.zip_code and c.zip_code.strip():
        city_line = c.zip_code.strip()
    if city_line:
        addr_lines.append(city_line)
    if c.phone and c.phone.strip():
        addr_lines.append(c.phone.strip())
    return addr_lines


def _invoice_core_slip_context(db: Session, inv) -> dict:
    """
    Companion Core Return Slip data for the printed invoice.

    When an invoice carries core charges, Print/PDF append a second page — a
    return slip listing the cores the customer owes back, with a sign-off block.
    Driven by the SAME open-cores query the After-Sale Service card uses, so the
    slip and the workspace agree. Rendered ad-hoc: NO CoreSlip row is created here
    (the customer-return/receive flow still owns CoreSlip creation — avoids dupes).
    Returns invoice_cores=[] when there are none, so the slip page is omitted.
    """
    from app.constants import CoreDirection, CoreStatus
    from app.models.core import CoreCharge
    cores = (
        db.query(CoreCharge)
        .join(InvoiceLine, CoreCharge.invoice_line_id == InvoiceLine.id)
        .filter(
            InvoiceLine.invoice_id == inv.id,
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
        )
        .order_by(CoreCharge.id)
        .all()
    )
    deadlines = [c.return_deadline for c in cores if c.return_deadline]
    return {
        "invoice_cores": cores,
        "core_slip_total_qty": sum(c.qty_outstanding for c in cores),
        "core_slip_total_credit": round(
            sum(c.customer_unit_charge * c.qty_outstanding for c in cores), 2
        ),
        "core_slip_deadline": min(deadlines) if deadlines else None,
        "core_slip_grace_days": cores[0].grace_days_snapshot if cores else None,
    }


def _invoice_print_context(db: Session, inv, user_id: int) -> dict:
    """Shared render context for the invoice print view and the PDF render."""
    ctx = {
        "invoice": inv,
        "customer_addr_lines": _customer_addr_lines(inv.customer),
        # Invoice-level discount, computed by the model so the printed document
        # agrees with the List total, Preview panel, and workspace totals panel.
        "discount_amount": inv.discount_amount,
        "company": get_company_dict(db),
        "prepared_by": get_prepared_by(db, user_id),
    }
    ctx.update(_invoice_core_slip_context(db, inv))
    return ctx


@router.get("/{invoice_id}/print", response_class=HTMLResponse)
def invoice_print(invoice_id: int, request: Request, db: Session = Depends(get_db),
                  user_id: int = Depends(get_current_user_id)):
    inv = _get_invoice_or_redirect(db, invoice_id)
    if isinstance(inv, RedirectResponse):
        return inv
    return templates.TemplateResponse(
        request, "invoices/print.html", _invoice_print_context(db, inv, user_id)
    )


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, request: Request, db: Session = Depends(get_db),
                user_id: int = Depends(get_current_user_id)):
    """Server-side PDF via WeasyPrint. Falls back to print view if libs missing."""
    from fastapi.responses import Response as FastAPIResponse

    inv = _get_invoice_or_redirect(db, invoice_id)
    if isinstance(inv, RedirectResponse):
        return inv

    html_str = templates.env.get_template("invoices/print.html").render(
        request=request, **_invoice_print_context(db, inv, user_id)
    )
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(
            string=html_str, base_url=str(request.base_url),
            url_fetcher=static_url_fetcher,
        ).write_pdf()
    except (OSError, ImportError, Exception):
        return RedirectResponse(f"/invoices/{invoice_id}/print", status_code=302)

    safe_number = inv.invoice_number.replace("/", "-").replace("\\", "-")
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_number}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.post("/{invoice_id}/send-message", response_class=RedirectResponse)
async def invoice_send_message(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """§22 — email / text the invoice to the customer via shared MessagingService.

    Best-effort attaches the print PDF (None on Windows/no-GTK → email sends
    without attachment, exactly like the /pdf route). Consent + rate-limit +
    log-only enforcement all live in the messaging core; this route never mutates
    invoice status. Redirects 303 back to the workspace with a ?sent=<n> count.
    """
    inv = _get_invoice_or_redirect(db, invoice_id)
    if isinstance(inv, RedirectResponse):
        return inv

    form = await request.form()
    channels = [c for c in form.getlist("channels") if c in ("email", "sms")]

    pdf_bytes = render_pdf_or_none(
        templates.env,
        "invoices/print.html",
        str(request.base_url),
        request=request,
        **_invoice_print_context(db, inv, user_id),
    )

    result = perform_document_send(
        db,
        user_id,
        customer_id=inv.customer_id,
        channels=channels,
        to_email=str(form.get("to_email", "")).strip(),
        to_phone=str(form.get("to_phone", "")).strip(),
        email_subject=str(form.get("email_subject", "")).strip(),
        email_body=str(form.get("email_body", "")),
        sms_body=str(form.get("sms_body", "")),
        pdf_bytes=pdf_bytes,
        pdf_filename=f"{inv.invoice_number}.pdf",
        related_entity_type="invoice",
        related_entity_id=inv.id,
    )
    n = len(result["sent"])
    qs = f"?sent={n}"
    if result["failed"] or result["blocked"]:
        qs += "&send_error=1"   # base.html shows the amber "couldn't send" banner
    return RedirectResponse(f"/invoices/{invoice_id}{qs}", status_code=303)


# ── Payment (unchanged) ───────────────────────────────────────────────────────

@router.post("/{invoice_id}/payment", response_class=RedirectResponse)
async def invoice_payment(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.payment_service import PaymentService

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)

    form = await request.form()
    try:
        amount = float(form.get("amount", 0))
        if amount <= 0:
            raise ValueError("Payment amount must be greater than zero.")

        payment_method = str(form.get("method", PaymentMethod.CASH))
        data = {
            "check_number": str(form.get("check_number", "")).strip() or None,
            "notes": str(form.get("notes", "")).strip(),
        }
        # R1-3 — collect the card surcharge at payment time: card method AND the
        # invoice is flagged. Pct comes from the invoice's R1 snapshot (already
        # resolved from customer override / system default at creation).
        apply_surcharge = (
            payment_method == PaymentMethod.CREDIT_CARD and inv.apply_cc_surcharge
        )
        PaymentService(db, user_id).record_payment(
            customer_id=inv.customer_id,
            amount_received=amount,
            payment_method=payment_method,
            data=data,
            invoice_ids=[invoice_id],
            apply_surcharge=apply_surcharge,
            surcharge_pct=inv.cc_surcharge_pct if apply_surcharge else None,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error recording payment for invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Unexpected error — payment was not recorded.')}",
            status_code=303,
        )
    return RedirectResponse(f"/invoices/{invoice_id}?saved=1", status_code=303)


# ── Apply account credit ─────────────────────────────────────────────────────

@router.post("/{invoice_id}/apply-credit", response_class=RedirectResponse)
async def invoice_apply_credit(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.payment_service import PaymentService

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)

    form = await request.form()
    try:
        amount = float(form.get("amount", 0))
        if amount <= 0:
            raise ValueError("Credit amount must be greater than zero.")
        PaymentService(db, user_id).apply_account_credit(
            customer_id=inv.customer_id,
            invoice_id=invoice_id,
            amount=amount,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error applying credit to invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Unexpected error — credit was not applied.')}",
            status_code=303,
        )
    return RedirectResponse(f"/invoices/{invoice_id}?saved=1", status_code=303)


# ── Issue Credit Memo (R8 — correct a finalized/locked invoice) ──────────────

@router.post("/{invoice_id}/issue-credit-memo", response_class=RedirectResponse)
async def invoice_issue_credit_memo(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Issue a customer credit memo against a finalized (OPEN/PARTIAL/PAID) invoice.

    A credit memo is an INDEPENDENT financial document — it does NOT modify the
    locked invoice. Form fields:
      - reason (required) — recorded on the CM and its audit row.
      - amount (optional) — credit a custom amount instead of the full invoice.
        Blank/absent → InvoiceService defaults to the full invoice subtotal+tax.
    On success, redirects to the new credit memo's detail page.
    """
    from app.services.invoice_service import InvoiceService

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)

    form = await request.form()
    reason = str(form.get("reason", "")).strip()

    # Optional single override line. Blank/absent → full-invoice default (lines=None).
    lines = None
    amount_raw = str(form.get("amount", "")).strip()
    if amount_raw:
        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0.0
        if amount > 0:
            lines = [{
                "description": f"Credit for invoice {inv.invoice_number}: {reason}",
                "qty": 1,
                "unit_price": amount,
            }]

    try:
        cm = InvoiceService(db, user_id).issue_credit_memo(
            invoice_id, lines=lines, reason=reason,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except PermissionError:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error="
            f"{url_quote('You do not have permission to issue credit memos.')}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error issuing credit memo for invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error="
            f"{url_quote('Unexpected error — credit memo was not issued.')}",
            status_code=303,
        )
    return RedirectResponse(f"/credit-memos/{cm.id}", status_code=303)


# ── Void (unchanged) ──────────────────────────────────────────────────────────

@router.post("/{invoice_id}/void", response_class=RedirectResponse)
async def invoice_void(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.invoice_service import InvoiceService

    form = await request.form()
    reason = str(form.get("reason", "")).strip() or "voided"
    try:
        InvoiceService(db, user_id).void_invoice(invoice_id, reason)
    except PermissionError:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error="
            f"{url_quote('You do not have permission to void invoices. Issue a credit memo instead.')}",
            status_code=303,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error voiding invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Unexpected error — invoice was not voided.')}",
            status_code=303,
        )
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)
