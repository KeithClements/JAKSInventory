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

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import InvoiceStatus, LineType, PaymentMethod
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
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


def _workspace_context(db: Session, request: Request, invoice: Invoice) -> dict:
    """Build the full context dict the workspace template expects."""
    from app.services.invoice_service import InvoiceService
    from app.services.statement_service import StatementService
    totals = InvoiceService(db, 1).calculate_totals(invoice.id)

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

    return {
        "invoice": invoice,
        "totals": totals,
        "customers": customers,
        "invoice_cores": invoice_cores,
        "invoice_intelligence": invoice_intelligence,
        "editable": invoice.status == InvoiceStatus.DRAFT,
        "cc_surcharge_pct": cc_surcharge_pct,
        "InvoiceStatus": InvoiceStatus,
        "LineType": LineType,
        "PaymentMethod": PaymentMethod,
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
]


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
    # `status` kept for backward-compat with old links (?status=open).
    status: str = "",
    db: Session = Depends(get_db),
):
    from sqlalchemy import or_, func

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

    counts = {
        "all":     _group_count("all"),
        "draft":   _group_count("draft"),
        "open":    _group_count("open"),
        "partial": _group_count("partial"),
        "overdue": overdue_count,
        "paid":    _group_count("paid"),
        "void":    _group_count("void"),
    }

    # Filtered query
    from sqlalchemy.orm import joinedload
    query = (
        db.query(Invoice)
        .join(Customer)
        .options(joinedload(Invoice.customer))
    )
    statuses = INV_TAB_GROUPS.get(tab, INV_TAB_GROUPS["all"])
    query = query.filter(Invoice.status.in_(statuses))
    if tab == "overdue":
        query = query.filter(Invoice.due_date.isnot(None), Invoice.due_date < now)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(like),
                Customer.company_name.ilike(like),
                Invoice.customer_po_number.ilike(like),
                Invoice.esn.ilike(like),
            )
        )
    invoices = query.order_by(Invoice.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        request,
        "invoices/list.html",
        {
            "invoices": invoices,
            "tabs": INV_LIST_TABS,
            "tab": tab,
            "q": q,
            "counts": counts,
            "InvoiceStatus": InvoiceStatus,
            "now": now,
        },
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
def invoice_workspace(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Full workspace. Template uses `editable` flag to switch between input fields
    (DRAFT) and static text (OPEN/PARTIAL/PAID/VOID).
    """
    inv = _get_invoice_or_redirect(db, invoice_id)
    if isinstance(inv, RedirectResponse):
        return inv
    from app.services.document_links import related_documents
    ctx = _workspace_context(db, request, inv)
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
        _workspace_context(db, request, inv),
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
        _workspace_context(db, request, inv),
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
        _workspace_context(db, request, inv),
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
        _workspace_context(db, request, inv),
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
        _workspace_context(db, request, inv),
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

    try:
        InvoiceService(db, user_id).finalise(invoice_id, allow_negative_inventory=allow_negative)
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


# ── Print / PDF (unchanged) ───────────────────────────────────────────────────

@router.get("/{invoice_id}/print", response_class=HTMLResponse)
def invoice_print(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = _get_invoice_or_redirect(db, invoice_id)
    if isinstance(inv, RedirectResponse):
        return inv

    c = inv.customer
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

    # Invoice-level discount, computed by the model so the printed document agrees
    # with the List total, the Preview panel, and the workspace totals panel.
    discount_amount = inv.discount_amount

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    return templates.TemplateResponse(request, "invoices/print.html", {
        "invoice": inv,
        "customer_addr_lines": addr_lines,
        "discount_amount": discount_amount,
        "company": company,
    })


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Server-side PDF via WeasyPrint. Falls back to print view if libs missing."""
    from fastapi.responses import Response as FastAPIResponse

    inv = _get_invoice_or_redirect(db, invoice_id)
    if isinstance(inv, RedirectResponse):
        return inv

    c = inv.customer
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

    # Invoice-level discount, computed by the model so the printed document agrees
    # with the List total, the Preview panel, and the workspace totals panel.
    discount_amount = inv.discount_amount

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    html_str = templates.env.get_template("invoices/print.html").render(
        request=request,
        invoice=inv,
        customer_addr_lines=addr_lines,
        discount_amount=discount_amount,
        company=company,
    )
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_str, base_url=str(request.base_url)).write_pdf()
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
        PaymentService(db, user_id).record_payment(
            customer_id=inv.customer_id,
            amount_received=amount,
            payment_method=payment_method,
            data=data,
            invoice_ids=[invoice_id],
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
