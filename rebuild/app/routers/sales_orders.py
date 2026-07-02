"""
app/routers/sales_orders.py
============================
Sales Order workspace — HTMX-powered, modeled after the invoice workspace.
All mutations delegate to SalesOrderService. No business logic here.

Primary creation path: customer-picker slide-over → POST /new → 303 → workspace.
Also: Quote → SO via QuoteService.convert_to_sales_order().

Workspace pattern:
  - GET  /sales-orders/{id}          → workspace.html
  - GET  /sales-orders/new           → slide-over content fragment (_new_picker.html)
  - POST /sales-orders/new           → create SO, 303 → /sales-orders/{id}
  - POST /sales-orders/{id}/header   → update header fields, returns updated header strip
  - POST /sales-orders/{id}/lines    → add line, returns _lines_section.html
  - POST /sales-orders/{id}/lines/{line_id}        → update line
  - POST /sales-orders/{id}/lines/{line_id}/delete → cancel/remove line
  - POST /sales-orders/{id}/fulfill  → fulfill & invoice (full-page redirect)
  - POST /sales-orders/{id}/hold, release-hold, cancel, collect-deposit
"""
from __future__ import annotations

import csv
import io
import logging
import time
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.constants import (
    ENGINE_MODELS_BY_MAKE,
    LineType, PaymentMethod, SOPaymentMode, SOStatus,
)
from app.deps import get_current_user_id, get_db
from app.services.base import ConcurrentEditError
from app.services.category_service import engine_make_names
from app.models.customer import Customer, CustomerAddress
from app.models.quote import SalesOrder, SOLine
from app.services.document_messaging import (
    build_send_context,
    itemize_lines,
    perform_document_send,
    render_pdf_or_none,
)
from app.services.public_links import public_doc_url
from app.services.document_render import get_prepared_by  # noqa: F401 (used below)
from app.services.document_render import (
    customer_address_lines,
    get_company_dict,
    render_pdf_or_fallback,
)
from app.services.sales_order_service import SalesOrderService
from app.services.sales_order_metrics_service import SalesOrderMetricsService

log = logging.getLogger(__name__)

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


def _apply_so_list_filters(query, active_tab: str, q: str):
    """Shared tab/q filter chain for the SO list AND its CSV export — one
    implementation so "export what I see" can never drift from the list view.
    Caller must have already joined Customer."""
    if active_tab:
        query = query.filter(SalesOrder.status == active_tab)
    if q:
        _qd = q.replace("-", "").replace(" ", "")
        query = query.filter(
            or_(
                SalesOrder.so_number.ilike(f"%{q}%"),
                # de-dash so "so20260001" still finds "SO-2026-0001"
                func.replace(func.replace(SalesOrder.so_number, "-", ""), " ", "").ilike(f"%{_qd}%"),
                Customer.company_name.ilike(f"%{q}%"),
                SalesOrder.customer_po_number.ilike(f"%{q}%"),
                SalesOrder.esn.ilike(f"%{q}%"),
            )
        )
    return query


def _workspace_ctx(request: Request, so: SalesOrder, db: Session) -> dict:
    editable = so.status in (SOStatus.OPEN, SOStatus.PARTIAL, SOStatus.HOLD)
    can_fulfill = so.status in (SOStatus.OPEN, SOStatus.PARTIAL)
    return {
        "so": so,
        "editable": editable,
        "can_fulfill": can_fulfill,
        "SOStatus": SOStatus,
        "SOPaymentMode": SOPaymentMode,
        "PaymentMethod": PaymentMethod,
        "LineType": LineType,
        # Standardized engine make/model cascading picker (header vehicle block —
        # same wiring as the quote workspace).
        "engine_makes": engine_make_names(db),
        "engine_models_by_make": ENGINE_MODELS_BY_MAKE,
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def list_sales_orders(
    request: Request,
    tab: str = "",
    status: str = "",
    q: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    # ── Unfiltered tab counts ─────────────────────────────────────────────
    _so_counts: dict = dict(
        db.query(SalesOrder.status, func.count(SalesOrder.id))
        .group_by(SalesOrder.status)
        .all()
    )
    _so_total = sum(_so_counts.values())
    counts: dict = {
        "":                 _so_total,
        "all":              _so_total,   # alias: SO list template uses _counts.get('all', 0)
        SOStatus.OPEN:      _so_counts.get(SOStatus.OPEN, 0),
        SOStatus.PARTIAL:   _so_counts.get(SOStatus.PARTIAL, 0),
        SOStatus.HOLD:      _so_counts.get(SOStatus.HOLD, 0),
        SOStatus.FULFILLED: _so_counts.get(SOStatus.FULFILLED, 0),
        SOStatus.INVOICED:  _so_counts.get(SOStatus.INVOICED, 0),
        SOStatus.CANCELLED: _so_counts.get(SOStatus.CANCELLED, 0),
    }

    # `tab` is the canonical param from the filter_tabs macro (?tab=<slug>).
    # Legacy ?status= accepted for back-compat.
    active_tab = tab or status

    query = db.query(SalesOrder).join(Customer)
    query = _apply_so_list_filters(query, active_tab, q)
    # §21 — pagination (was a silent limit(150)).
    from app.utils import compute_pager
    query = query.order_by(SalesOrder.created_at.desc())
    pager = compute_pager(page, query.order_by(None).count(), per_page=50)
    orders = query.limit(pager["per_page"]).offset(pager["offset"]).all()
    return templates.TemplateResponse(
        request,
        "sales_orders/list.html",
        {
            "orders": orders,
            "active_tab": active_tab,
            "status_filter": status,
            "counts": counts,
            "q": q,
            "SOStatus": SOStatus,
            "pager": pager,
            # §5.1 — SO dashboard strip metrics (UI renders the tiles)
            "so_metrics": SalesOrderMetricsService(db).dashboard_metrics(),
        },
    )


# ── Export CSV — MUST stay registered before /{so_id} ────────────────────────

@router.get("/export.csv")
def so_export_csv(
    tab: str = "",
    q: str = "",
    # `status` kept for parity with the list view's legacy param.
    status: str = "",
    db: Session = Depends(get_db),
):
    """
    Stream the current filtered SO list as a CSV download.
    Respects the same tab/q filters as the list view so "export what I see"
    works, and streams ALL matching rows — not just the visible page.
    (Same pattern as /invoices/export.csv and /products/export.csv.)
    """
    from sqlalchemy.orm import joinedload, selectinload

    active_tab = tab or status
    query = (
        db.query(SalesOrder)
        .join(Customer)
        .options(
            joinedload(SalesOrder.customer),
            selectinload(SalesOrder.lines),  # subtotal/qty rollups walk .lines
        )
    )
    query = _apply_so_list_filters(query, active_tab, q)
    orders = query.order_by(SalesOrder.created_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "so_number", "status", "customer", "customer_po_number", "esn",
        "payment_mode", "deposit_amount", "lines",
        "qty_ordered", "qty_fulfilled", "subtotal", "ordered",
    ])
    for so in orders:
        writer.writerow([
            so.so_number,
            so.status,
            so.customer.company_name if so.customer else "",
            so.customer_po_number or "",
            so.esn or "",
            so.payment_mode,
            f"{so.deposit_amount:.2f}",
            len(so.lines),
            sum(ln.qty_ordered for ln in so.lines),
            sum(ln.qty_fulfilled for ln in so.lines),
            f"{so.subtotal:.2f}",
            so.created_at.strftime("%Y-%m-%d") if so.created_at else "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales_orders.csv"},
    )


# ── New SO — customer picker slide-over ──────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_so_picker(request: Request, db: Session = Depends(get_db)):
    """Slide-over content fragment for 'New SO'. Returns just the picker form."""
    hx = request.headers.get("HX-Request")
    if hx:
        return templates.TemplateResponse(
            request,
            "sales_orders/_new_picker.html",
            {"SOPaymentMode": SOPaymentMode},
        )
    # Direct navigation: render a full page wrapping the picker
    return templates.TemplateResponse(
        request,
        "sales_orders/new.html",
        {"SOPaymentMode": SOPaymentMode},
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
            "lines": [],
        },
    )
    return RedirectResponse(f"/sales-orders/{so.id}", status_code=303)


# ── Preview panel — MUST stay registered before /{so_id} ─────────────────────

@router.get("/preview/{so_id}", response_class=HTMLResponse)
async def so_preview_panel(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Bottom-dock preview partial for the SO List (§7 Primitive 5).
    Loaded via htmx.ajax() on row click; returns sales_orders/_preview_panel.html.

    Context published to UI lane:
      so          — SalesOrder ORM object (with .customer, .lines)
      SOStatus    — enum class
      SOPaymentMode — enum class
    """
    so = db.query(SalesOrder).filter(SalesOrder.id == so_id).first()
    if so is None:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-red-500">Sales order not found.</p>',
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "sales_orders/_preview_panel.html",
        {
            "so": so,
            "SOStatus": SOStatus,
            "SOPaymentMode": SOPaymentMode,
        },
    )


# ── Workspace ─────────────────────────────────────────────────────────────────

@router.get("/{so_id}", response_class=HTMLResponse)
async def so_workspace(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    from app.services.document_links import related_documents
    so = _get_so_or_404(db, so_id)
    ctx = _workspace_ctx(request, so, db)
    ctx["linked_documents"] = related_documents(db, so)
    # §5.10 — SO↔PO status rollup per linked line (UI renders the chip + ETA)
    ctx["po_link_map"] = SalesOrderMetricsService(db).po_link_map(so)
    # §4.5 credit warn (WARN-ONLY) — same contract as customer detail / invoice.
    # An SO's value is never in open AR (AR starts at invoicing), so the full SO
    # total is the prospective charge for the over-limit check.
    from app.services.customer_service import CustomerService
    ctx["credit_status"] = (
        CustomerService(db).credit_status(so.customer, so.subtotal) if so.customer else None
    )
    # §22 Function A — shared "Send" dialog context. SalesOrder exposes `subtotal`
    # (no `total` property like Quote/Invoice); fall back through both so the
    # dialog's pre-filled body shows the order value.
    ctx["send"] = build_send_context(
        db,
        doc_label="Sales Order",
        doc_number=so.so_number,
        customer=so.customer,
        total=(getattr(so, "total", None) or so.subtotal or 0.0),
        action_url=f"/sales-orders/{so.id}/send-message",
        lines=itemize_lines(so.lines),
        view_url=public_doc_url(db, "sales_order", so.id),
    )
    return templates.TemplateResponse(
        request,
        "sales_orders/workspace.html",
        ctx,
    )


# ── Line ETA (§5.2 — backorder arrival estimate) ──────────────────────────────

@router.post("/{so_id}/lines/{line_id}/eta", response_class=RedirectResponse)
async def so_set_line_eta(
    so_id: int,
    line_id: int,
    eta_date: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Set/clear a backordered SO line's ETA. Accepts ISO 'YYYY-MM-DD' or blank
    to clear. Redirects back to the workspace (UI may HTMX-swap later)."""
    SalesOrderService(db, user_id).set_line_eta(line_id, eta_date)
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


# ── Header update ─────────────────────────────────────────────────────────────

@router.post("/{so_id}/header", response_class=HTMLResponse)
async def so_update_header(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    data = {k: form.get(k) for k in (
        "customer_po_number", "customer_job_number", "esn",
        "engine_manufacturer", "engine_model",
        "notes", "internal_notes", "payment_mode",
    ) if k in form}
    submitted_updated_at = form.get("_updated_at")
    try:
        SalesOrderService(db, user_id).update_header(so_id, data, submitted_updated_at)
    except ConcurrentEditError as exc:
        db.rollback()
        return HTMLResponse(str(exc), status_code=409)
    except ValueError as exc:
        return HTMLResponse(f'<div class="text-xs text-red-600">{exc}</div>', status_code=400)
    # X-Updated-At lets the workspace refresh its hidden version field after every
    # successful save so the next autosave never self-conflicts (R9, PO pattern).
    so = db.query(SalesOrder).filter(SalesOrder.id == so_id).first()
    fresh_ts = so.updated_at.isoformat() if so and so.updated_at else ""
    return HTMLResponse("", status_code=204, headers={"X-Updated-At": fresh_ts})


# ── Line CRUD (HTMX) ──────────────────────────────────────────────────────────

@router.post("/{so_id}/lines", response_class=HTMLResponse)
async def so_add_line(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    so = _get_so_or_404(db, so_id)
    if so.status not in (SOStatus.OPEN, SOStatus.PARTIAL):
        return HTMLResponse('<div class="text-xs text-red-600">Cannot add lines to a non-open SO</div>', status_code=400)

    form = await request.form()
    pid_raw = str(form.get("product_id", "")).strip()
    product_id = int(pid_raw) if pid_raw else None

    # Canonical line-item field is `qty`; accept legacy `qty_ordered` too.
    qty_raw = str(form.get("qty", form.get("qty_ordered", "1"))).strip()
    price_raw = str(form.get("unit_price", "0")).strip()
    cost_raw = str(form.get("unit_cost", "0")).strip()
    desc = str(form.get("description", "")).strip()

    # Description/cost/price must NOT be pre-filled from the product here:
    # apply_product_line_defaults treats a non-zero unit_price as an explicit
    # caller price and skips the customer-rule/tier waterfall, so pre-filling
    # selling_price billed tier customers full price. Blanks/0 flow through and
    # the service resolves them; a typed non-zero price still wins.
    data = {
        "description": desc,
        "qty_ordered": max(1, int(qty_raw)) if qty_raw else 1,
        "unit_price": float(price_raw) if price_raw else 0.0,
        "unit_cost": float(cost_raw) if cost_raw else 0.0,
        "line_type": str(form.get("line_type", LineType.PRODUCT)).strip() or LineType.PRODUCT,
    }
    allow_negative = str(form.get("allow_negative_inventory", "")).lower() in {"1", "true", "on", "yes"}

    try:
        SalesOrderService(db, user_id).add_line(
            so_id, product_id, data, allow_negative_inventory=allow_negative,
        )
    except PermissionError:
        db.rollback()
        return HTMLResponse(
            '<div class="text-xs text-red-600 p-2">You do not have permission to override negative inventory.</div>',
            status_code=403,
        )
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600 p-2">{exc}</div>', status_code=400)

    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        request,
        "sales_orders/_lines_section.html",
        _workspace_ctx(request, so, db),
    )


@router.post("/{so_id}/lines/{line_id}", response_class=HTMLResponse)
async def so_update_line(
    so_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    data: dict = {}
    if "description" in form:
        data["description"] = str(form.get("description", "")).strip()
    if "qty_ordered" in form:
        raw = str(form.get("qty_ordered", "")).strip()
        if raw:
            data["qty_ordered"] = max(1, int(raw))
    if "unit_price" in form:
        raw = str(form.get("unit_price", "")).strip()
        if raw:
            data["unit_price"] = float(raw)
    if "discount_pct" in form:
        raw = str(form.get("discount_pct", "")).strip()
        data["discount_pct"] = float(raw) if raw else 0.0
    allow_negative = str(form.get("allow_negative_inventory", "")).lower() in {"1", "true", "on", "yes"}

    try:
        SalesOrderService(db, user_id).update_line(
            line_id, data, allow_negative_inventory=allow_negative,
        )
    except PermissionError:
        db.rollback()
        return HTMLResponse(
            '<div class="text-xs text-red-600 p-2">You do not have permission to override negative inventory.</div>',
            status_code=403,
        )
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600 p-2">{exc}</div>', status_code=400)

    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        request,
        "sales_orders/_lines_section.html",
        _workspace_ctx(request, so, db),
    )


@router.post("/{so_id}/lines/{line_id}/delete", response_class=HTMLResponse)
async def so_delete_line(
    so_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).cancel_line(line_id)
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(f'<div class="text-xs text-red-600 p-2">{exc}</div>', status_code=400)

    so = _get_so_or_404(db, so_id)
    return templates.TemplateResponse(
        request,
        "sales_orders/_lines_section.html",
        _workspace_ctx(request, so, db),
    )


@router.post("/{so_id}/lines/{line_id}/create-po", response_class=HTMLResponse)
async def so_create_po_for_line(
    so_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Order a backordered line on a new draft PO, then send the user to that PO.

    On success returns HX-Redirect so the htmx button navigates the whole page to
    the new PO (review → send). On failure re-renders the lines section with the
    error banner so the reason (e.g. no preferred vendor) is visible in place.
    """
    try:
        po = SalesOrderService(db, user_id).create_po_for_line(so_id, line_id)
    except ValueError as exc:
        db.rollback()
        so = _get_so_or_404(db, so_id)
        ctx = _workspace_ctx(request, so, db)
        ctx["order_error"] = str(exc)
        return templates.TemplateResponse(
            request, "sales_orders/_lines_section.html", ctx
        )

    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/purchase-orders/{po.id}"
    return resp


# ── Product search ───────────────────────────────────────────────────────────
# The per-doc /sales-orders/_/product-search HTML endpoint was removed after the
# §8H migration (its partial sales_orders/_product_search_results.html is gone).
# The SO workspace line-adder now calls GET /line-items/product-search (JSON).


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
    Redirects to the new invoice on success.
    """
    _t0 = time.perf_counter()

    form = await request.form()
    _t_form = time.perf_counter()

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
            f"/sales-orders/{so_id}?error=Select+quantities+to+ship",
            status_code=303,
        )

    try:
        svc = SalesOrderService(db, user_id)
        invoice = svc.fulfill_and_invoice(so_id, line_quantities)
    except PermissionError:
        # C3 — fulfillment finalizes an invoice, which needs FINALIZE_INVOICE.
        db.rollback()
        return RedirectResponse(
            f"/sales-orders/{so_id}?error={url_quote('You do not have permission to fulfill/invoice this order (it finalizes an invoice). Ask an admin or bookkeeper.')}",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/sales-orders/{so_id}?error={str(exc)}",
            status_code=303,
        )

    _t_svc = time.perf_counter()
    _form_ms = (_t_form - _t0) * 1000
    _svc_ms  = (_t_svc - _t_form) * 1000
    _total_ms = (_t_svc - _t0) * 1000
    log.info(
        "TIMING fulfill_and_invoice so=%s  total=%.1fms  form=%.1fms  svc=%.1fms",
        so_id, _total_ms, _form_ms, _svc_ms,
    )
    resp = RedirectResponse(f"/invoices/{invoice.id}?ok=Created+from+SO+{so_id}", status_code=303)
    resp.headers["Server-Timing"] = (
        f"form;dur={_form_ms:.1f},svc;dur={_svc_ms:.1f},total;dur={_total_ms:.1f}"
    )
    return resp


# ── Hold / Release ────────────────────────────────────────────────────────────

@router.post("/{so_id}/hold")
async def hold_order(
    so_id: int,
    hold_reason: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).hold_order(so_id, hold_reason or "On hold")
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={exc}", status_code=303)
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/release-hold")
async def release_hold(
    so_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).release_hold(so_id)
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={exc}", status_code=303)
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.post("/{so_id}/cancel")
async def cancel_order(
    so_id: int,
    cancel_reason: str = Form(""),
    deposit_resolution: str = Form("leave_open"),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        SalesOrderService(db, user_id).cancel_order(
            so_id,
            reason=cancel_reason or "Cancelled",
            deposit_resolution=deposit_resolution or None,
        )
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={str(exc)}", status_code=303)
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
    try:
        SalesOrderService(db, user_id).collect_deposit(so_id, amount, payment_method)
    except ValueError as exc:
        return RedirectResponse(f"/sales-orders/{so_id}?error={str(exc)}", status_code=303)
    return RedirectResponse(f"/sales-orders/{so_id}?ok=Deposit+recorded", status_code=303)


# ── Print / PDF ───────────────────────────────────────────────────────────────

def _so_print_context(so: SalesOrder, db: Session) -> dict:
    company = get_company_dict(db)
    customer_addr_lines_ = customer_address_lines(so.customer)

    ship_to_name = None
    ship_to_lines: list[str] = []
    if so.ship_to_address_id:
        addr = (
            db.query(CustomerAddress)
            .filter(CustomerAddress.id == so.ship_to_address_id)
            .first()
        )
        if addr is not None:
            class _AddrShim:
                address_line1 = addr.street
                address_line2 = addr.street_line2
                city = addr.city
                state = addr.state
                zip_code = addr.zip_code
                phone = addr.phone
            ship_to_lines = customer_address_lines(_AddrShim())
            ship_to_name = (addr.label or so.customer.company_name).strip() or so.customer.company_name

    return {
        "so": so,
        "company": company,
        "customer_addr_lines": customer_addr_lines_,
        "ship_to_name": ship_to_name,
        "ship_to_lines": ship_to_lines,
    }


@router.get("/{so_id}/print", response_class=HTMLResponse)
def so_print(so_id: int, request: Request, db: Session = Depends(get_db),
             user_id: int = Depends(get_current_user_id)):
    so = db.query(SalesOrder).filter(SalesOrder.id == so_id).first()
    if so is None:
        return RedirectResponse("/sales-orders/", status_code=303)
    ctx = _so_print_context(so, db)
    ctx["prepared_by"] = get_prepared_by(db, user_id)
    return templates.TemplateResponse(request, "sales_orders/print.html", ctx)


@router.get("/{so_id}/pdf")
def so_pdf(so_id: int, request: Request, db: Session = Depends(get_db),
           user_id: int = Depends(get_current_user_id)):
    so = db.query(SalesOrder).filter(SalesOrder.id == so_id).first()
    if so is None:
        return RedirectResponse("/sales-orders/", status_code=303)
    ctx = _so_print_context(so, db)
    ctx["prepared_by"] = get_prepared_by(db, user_id)
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="sales_orders/print.html",
        context=ctx,
        fallback_print_url=f"/sales-orders/{so_id}/print",
        download_filename=so.so_number,
    )


# ── Send (§22 Function A) ─────────────────────────────────────────────────────

@router.post("/{so_id}/send-message")
async def so_send_message(
    so_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Email / text the SO PDF to the customer via the shared messaging core.

    Renders the same print context the /pdf route uses, builds a best-effort PDF
    (None on Windows/no-GTK — email then sends without the attachment), and hands
    off to perform_document_send (consent-checked, rate-limited, log-only-safe).
    Never raises into the user — channel-level blocks/failures are summarized and
    the workspace shows the count. Redirects 303 back to the workspace.
    """
    so = _get_so_or_404(db, so_id)

    form = await request.form()
    channels = form.getlist("channels")

    # PDF is best-effort (same as /pdf): None → email sends without attachment.
    # Build the print context defensively so a missing user / settings row never
    # blocks the send itself.
    pdf_bytes = None
    if "email" in channels:
        try:
            ctx = _so_print_context(so, db)
            ctx["prepared_by"] = get_prepared_by(db, user_id)
            pdf_bytes = render_pdf_or_none(
                templates.env, "sales_orders/print.html", str(request.base_url), **ctx
            )
        except Exception:  # noqa: BLE001 — never let PDF prep break the send
            log.info("SO send: PDF context build failed for so=%s", so_id, exc_info=True)
            pdf_bytes = None

    result = perform_document_send(
        db,
        user_id,
        customer_id=so.customer_id,
        channels=channels,
        to_email=str(form.get("to_email", "")),
        to_phone=str(form.get("to_phone", "")),
        email_subject=str(form.get("email_subject", "")),
        email_body=str(form.get("email_body", "")),
        sms_body=str(form.get("sms_body", "")),
        pdf_bytes=pdf_bytes,
        pdf_filename=f"{so.so_number}.pdf",
        related_entity_type="sales_order",
        related_entity_id=so.id,
    )
    n = len(result["sent"])
    qs = f"?sent={n}"
    if result["failed"] or result["blocked"]:
        qs += "&send_error=1"   # base.html shows the amber "couldn't send" banner
    return RedirectResponse(f"/sales-orders/{so_id}{qs}", status_code=303)
