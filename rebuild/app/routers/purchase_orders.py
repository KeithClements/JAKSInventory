from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import POStatus
from app.deps import get_db, get_current_user_id
from app.models.customer import Customer, CustomerAddress
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine, VendorBill
from app.models.vendor import Vendor
from app.services.document_render import (
    customer_address_lines,
    get_company_dict,
    render_pdf_or_fallback,
    vendor_address_lines,
)
from app.services.po_service import POService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/purchase-orders", tags=["purchase_orders"])
templates = Jinja2Templates(directory="app/templates")

# ── L2 list tab definitions (JAKS_UI_Change_Plan.md §2) ──────────────────────
# Maps user-facing tab slug → underlying PO statuses it covers.
# "all" → no filter (empty list signals "no filter").
TAB_GROUPS: dict[str, list[str]] = {
    "all":       [],
    "open":      [POStatus.DRAFT, POStatus.VERBAL_ORDER, POStatus.SENT],
    "receiving": [POStatus.PARTIAL],
    "received":  [POStatus.RECEIVED],
    "billed":    [POStatus.BILLED],
    "cancelled": [POStatus.CANCELLED],
}

# Old individual status → grouped tab slug (for backward-compat with ?status= links)
_STATUS_TO_TAB: dict[str, str] = {
    POStatus.DRAFT:        "open",
    POStatus.VERBAL_ORDER: "open",
    POStatus.SENT:         "open",
    POStatus.PARTIAL:      "receiving",
    POStatus.RECEIVED:     "received",
    POStatus.BILLED:       "billed",
    POStatus.CANCELLED:    "cancelled",
}

PO_LIST_TABS: list[tuple[str, str]] = [
    ("all",       "All"),
    ("open",      "Open"),
    ("receiving", "Receiving"),
    ("received",  "Received"),
    ("billed",    "Billed"),
    ("cancelled", "Cancelled"),
]


# ── 3-way match (PO · Receipt · Bill) ────────────────────────────────────────
#
# Computation lives in POService.compute_match_line / compute_match_summary so
# the resolution service methods can call it without a circular import.
# These thin wrappers keep the call sites in this router unchanged.

def _match_line(line: POLine) -> dict:
    return POService.compute_match_line(line)


def _match_summary(po: PurchaseOrder) -> dict:
    return POService.compute_match_summary(po)


def _workspace_ctx(po: PurchaseOrder) -> dict:
    editable   = po.status in (POStatus.DRAFT, POStatus.VERBAL_ORDER)
    can_receive = po.status in (POStatus.SENT, POStatus.PARTIAL)
    can_bill    = po.status in (POStatus.RECEIVED, POStatus.PARTIAL)
    match = _match_summary(po)
    return {
        "po": po,
        "editable": editable,
        "can_receive": can_receive,
        "can_bill": can_bill,
        "POStatus": POStatus,
        "unreceived_lines": [ln for ln in po.lines if ln.qty_outstanding > 0] if can_receive else [],
        "received_lines":   [ln for ln in po.lines if ln.qty_received > 0 and (ln.qty_received - (ln.qty_billed or 0)) > 0] if can_bill else [],
        "match": match,
    }


# ── List ──────────────────────────────────────────────────────────────────────
#
# L2 — Operational List Screen Standard (JAKS_UI_Change_Plan.md §2).
# Mirrors the Products List pattern: grouped tab filter with counts from the
# *unfiltered* dataset, search across PO #, vendor name, and vendor confirmation
# number, and a per-row preview dock (loaded via /purchase-orders/preview/{id}).


@router.get("/", response_class=HTMLResponse)
def po_list(
    request: Request,
    tab: str = "all",
    q: str = "",
    # `status` kept for backward-compat with old links (?status=draft).
    status: str = "",
    db: Session = Depends(get_db),
):
    # Backward-compat: ?status=draft → ?tab=open, etc.
    if status and tab == "all":
        tab = _STATUS_TO_TAB.get(status, "all")

    # Counts — always from the *full* unfiltered dataset so tab counts are stable
    # regardless of which tab is active (mirrors Products List behavior).
    raw_counts = dict(
        db.query(PurchaseOrder.status, func.count(PurchaseOrder.id))
          .group_by(PurchaseOrder.status)
          .all()
    )
    total = sum(raw_counts.values())

    def _group_count(slug: str) -> int:
        return sum(raw_counts.get(s, 0) for s in TAB_GROUPS.get(slug, []))

    counts = {
        "all":       total,
        "open":      _group_count("open"),
        "receiving": _group_count("receiving"),
        "received":  _group_count("received"),
        "billed":    _group_count("billed"),
        "cancelled": _group_count("cancelled"),
    }

    # Filtered query
    query = (
        db.query(PurchaseOrder)
        .options(joinedload(PurchaseOrder.vendor))
        .order_by(PurchaseOrder.created_at.desc())
    )
    statuses = TAB_GROUPS.get(tab, [])
    if statuses:  # empty list = "all" → no filter
        query = query.filter(PurchaseOrder.status.in_(statuses))
    if q:
        like = f"%{q.strip()}%"
        query = query.outerjoin(Vendor, PurchaseOrder.vendor_id == Vendor.id).filter(
            PurchaseOrder.po_number.ilike(like)
            | PurchaseOrder.vendor_confirmation_number.ilike(like)
            | Vendor.name.ilike(like)
        )

    pos = query.all()
    return templates.TemplateResponse(
        request,
        "purchase_orders/list.html",
        {
            "pos": pos,
            "tabs": PO_LIST_TABS,
            "tab": tab,
            "q": q,
            "counts": counts,
            "POStatus": POStatus,
            "now": datetime.utcnow(),
        },
    )


# ── List row preview panel (HTMX partial) ────────────────────────────────────

@router.get("/preview/{po_id}", response_class=HTMLResponse)
def po_preview_panel(po_id: int, request: Request, db: Session = Depends(get_db)):
    """Bottom preview dock body, loaded by htmx.ajax() on row click in the list."""
    po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.lines).joinedload(POLine.product),
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not po:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-gray-400">Purchase order not found.</p>'
        )
    return templates.TemplateResponse(
        request,
        "purchase_orders/_preview_panel.html",
        {"po": po},
    )


# ── Receiving Queue ───────────────────────────────────────────────────────────
#
# Standalone operational board covering the full receiving lifecycle (ordered →
# partial → received → billed), grouped by vendor with overdue/discrepancy
# vendors floated to the top. Row actions link into the PO workspace — this
# screen surfaces the workflow, it does not duplicate PO editing. Declared
# before /{po_id} so the literal path wins routing.

# Receiving lifecycle states, with sort rank (lower = more urgent) and stripe.
_RECV_RANK = {
    "discrepancy": 0,
    "overdue":     0,
    "partial":     1,
    "open":        2,
    "received":    3,
    "billed":      4,
}


@router.get("/receiving", response_class=HTMLResponse)
def po_receiving_queue(request: Request, q: str = "", db: Session = Depends(get_db)):
    pos = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.lines).joinedload(POLine.bill_lines),
        )
        .filter(PurchaseOrder.status.in_([
            POStatus.SENT, POStatus.VERBAL_ORDER, POStatus.PARTIAL,
            POStatus.RECEIVED, POStatus.BILLED,
        ]))
        .all()
    )
    if q:
        ql = q.strip().lower()
        pos = [
            p for p in pos
            if ql in (p.po_number or "").lower()
            or (p.vendor and ql in (p.vendor.name or "").lower())
            or ql in (p.vendor_confirmation_number or "").lower()
        ]

    now = datetime.utcnow()
    today = now.date()
    far = datetime.max
    awaiting = (POStatus.SENT, POStatus.VERBAL_ORDER, POStatus.PARTIAL)

    def _is_overdue(p: PurchaseOrder) -> bool:
        return bool(p.expected_at and p.expected_at.date() < today and p.status in awaiting)

    rows = []
    for p in pos:
        flagged = _match_summary(p)["flag_count"] > 0
        if flagged:
            state = "discrepancy"
        elif _is_overdue(p):
            state = "overdue"
        elif p.status == POStatus.PARTIAL:
            state = "partial"
        elif p.status in (POStatus.SENT, POStatus.VERBAL_ORDER):
            state = "open"
        elif p.status == POStatus.RECEIVED:
            state = "received"
        else:  # BILLED
            state = "billed"
        rows.append({
            "po": p,
            "state": state,
            "flagged": flagged,
            "overdue": _is_overdue(p),
            "vendor_name": p.vendor.name if p.vendor else "No vendor",
        })

    metrics = {
        "open":        sum(1 for p in pos if p.status in (POStatus.SENT, POStatus.VERBAL_ORDER)),
        "due_overdue": sum(1 for p in pos if p.expected_at and p.expected_at.date() <= today and p.status in awaiting),
        "partial":     sum(1 for p in pos if p.status == POStatus.PARTIAL),
        "flagged":     sum(1 for r in rows if r["flagged"]),
    }

    # Group by vendor; float the most urgent vendor (lowest row rank) to the top.
    group_rank: dict[str, int] = {}
    for r in rows:
        rank = _RECV_RANK[r["state"]]
        group_rank[r["vendor_name"]] = min(group_rank.get(r["vendor_name"], 99), rank)

    rows.sort(key=lambda r: (
        group_rank[r["vendor_name"]],
        r["vendor_name"].lower(),
        _RECV_RANK[r["state"]],
        r["po"].expected_at or far,
    ))

    return templates.TemplateResponse(
        request,
        "purchase_orders/receiving_queue.html",
        {
            "rows": rows,
            "metrics": metrics,
            "q": q,
            "total": len(rows),
            "now": now,
        },
    )


# ── 3-Way Match Queue ───────────────────────────────────────────────────────
#
# Cross-PO queue of POs whose lines carry a match variance needing AP review
# (billed > received, or billed unit cost ≠ ordered unit cost). Normal
# "awaiting receipt / awaiting bill" states are *not* flagged — only true
# variances surface here.

@router.get("/match", response_class=HTMLResponse)
def po_match_queue(request: Request, db: Session = Depends(get_db)):
    pos = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.lines).joinedload(POLine.product),
            joinedload(PurchaseOrder.lines).joinedload(POLine.bill_lines),
            joinedload(PurchaseOrder.bills).joinedload(VendorBill.lines),
        )
        .filter(PurchaseOrder.status.in_(
            [POStatus.PARTIAL, POStatus.RECEIVED, POStatus.BILLED]
        ))
        .order_by(PurchaseOrder.created_at.desc())
        .all()
    )

    flagged = []
    for p in pos:
        summary = _match_summary(p)
        if summary["flag_count"] > 0:
            flagged.append({"po": p, "match": summary})

    return templates.TemplateResponse(
        request,
        "purchase_orders/match_queue.html",
        {
            "flagged": flagged,
            "total": len(flagged),
            "now": datetime.utcnow(),
        },
    )


# ── New PO picker (slide-over) ─────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def po_new(
    request: Request,
    db: Session = Depends(get_db),
    product_id: int | None = None,
    vendor_id: int | None = None,
):
    if not request.headers.get("HX-Request"):
        return RedirectResponse("/purchase-orders/", status_code=303)
    vendors = db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()  # noqa: E712
    # When launched from a product ("New PO" in the product preview dock) seed the
    # part being ordered and pre-select its preferred vendor.
    product = (
        db.query(Product).filter(Product.id == product_id).first() if product_id else None
    )
    return templates.TemplateResponse(
        request,
        "purchase_orders/_new_picker.html",
        {
            "vendors": vendors,
            "POStatus": POStatus,
            "product": product,
            "preselect_vendor_id": vendor_id,
        },
    )


@router.post("/new", response_class=RedirectResponse)
async def po_create(request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    svc = POService(db, current_user_id=user_id)

    po_data: dict = {
        "notes": str(form.get("notes", "")).strip(),
        "internal_notes": "",
        "freight_in_cost": float(form.get("freight_in_cost") or 0.0),
        "vendor_confirmation_number": str(form.get("vendor_confirmation_number", "")).strip() or None,
        "expected_at": None,
    }

    expected_raw = str(form.get("expected_at", "")).strip()
    if expected_raw:
        try:
            po_data["expected_at"] = datetime.strptime(expected_raw, "%Y-%m-%d")
        except ValueError:
            pass

    status_override = str(form.get("status", "")).strip()
    if status_override == POStatus.VERBAL_ORDER:
        po_data["status"] = POStatus.VERBAL_ORDER

    po = svc.create_po(vendor_id=int(form["vendor_id"]), data=po_data)

    # Seed the selected part as the first line when launched from a product
    # ("New PO" in the product preview dock). add_line backfills the unit cost
    # (from this vendor's source), description, and core charge from the product.
    seed_pid = str(form.get("product_id", "")).strip()
    if seed_pid.isdigit():
        try:
            svc.add_line(po_id=po.id, product_id=int(seed_pid), data={"qty_ordered": 1})
        except ValueError as exc:
            log.warning("New-PO product seed failed (po=%s, product=%s): %s", po.id, seed_pid, exc)

    if status_override == POStatus.VERBAL_ORDER:
        po.status = POStatus.VERBAL_ORDER
        db.commit()

    return RedirectResponse(f"/purchase-orders/{po.id}", status_code=303)


# ── Workspace ─────────────────────────────────────────────────────────────────

@router.get("/{po_id}", response_class=HTMLResponse)
def po_workspace(po_id: int, request: Request, db: Session = Depends(get_db)):
    po = (
        db.query(PurchaseOrder)
        .options(joinedload(PurchaseOrder.lines).joinedload(POLine.product))
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)
    from app.services.document_links import related_documents
    ctx = _workspace_ctx(po)
    ctx["linked_documents"] = related_documents(db, po)
    return templates.TemplateResponse(request, "purchase_orders/workspace.html", ctx)


# ── Header autosave ───────────────────────────────────────────────────────────

@router.post("/{po_id}/header", response_class=HTMLResponse)
async def po_header_save(po_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    svc = POService(db, current_user_id=user_id)

    expected_raw = str(form.get("expected_at", "")).strip()
    expected_dt = None
    if expected_raw:
        try:
            expected_dt = datetime.strptime(expected_raw, "%Y-%m-%d")
        except ValueError:
            pass

    data = {
        "notes": str(form.get("notes", "")).strip(),
        "internal_notes": str(form.get("internal_notes", "")).strip(),
        "vendor_confirmation_number": str(form.get("vendor_confirmation_number", "")).strip() or None,
        "freight_in_cost": float(form.get("freight_in_cost") or 0.0),
        "expected_at": expected_dt,
    }
    try:
        svc.save_header(po_id, data)
    except ValueError:
        pass
    return HTMLResponse("", status_code=204)


# ── Line CRUD (HTMX) ──────────────────────────────────────────────────────────

def _lines_response(po_id: int, request: Request, db: Session) -> HTMLResponse:
    po = (
        db.query(PurchaseOrder)
        .options(joinedload(PurchaseOrder.lines).joinedload(POLine.product))
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    ctx = _workspace_ctx(po)
    return templates.TemplateResponse(request, "purchase_orders/_lines_section.html", ctx)


@router.post("/{po_id}/lines", response_class=HTMLResponse)
async def po_add_line(po_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    svc = POService(db, current_user_id=user_id)

    pid_raw = str(form.get("product_id", "")).strip()
    desc = str(form.get("description", "")).strip()
    # Canonical line-item field is `qty`; accept legacy `qty_ordered` too.
    qty_raw = str(form.get("qty", form.get("qty_ordered", "1"))).strip()
    cost_raw = str(form.get("unit_cost", "")).strip()
    core_raw = str(form.get("core_charge_per_unit", "")).strip()

    line_data = {
        "description": desc,
        "qty_ordered": max(1, int(qty_raw) if qty_raw.isdigit() else 1),
        "unit_cost": float(cost_raw) if cost_raw else 0.0,
        "core_charge_per_unit": float(core_raw) if core_raw else 0.0,
        "notes": "",
    }

    # Auto-fill description from product if blank
    pid = int(pid_raw) if pid_raw else None
    if pid and not desc:
        product = db.query(Product).filter(Product.id == pid).first()
        if product:
            line_data["description"] = product.title or ""

    try:
        svc.add_line(po_id=po_id, product_id=pid, data=line_data)
    except ValueError as exc:
        log.warning("add_line error on PO %s: %s", po_id, exc)

    return _lines_response(po_id, request, db)


@router.post("/{po_id}/lines/{line_id}", response_class=HTMLResponse)
async def po_update_line(po_id: int, line_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    svc = POService(db, current_user_id=user_id)

    data: dict = {}
    if "description" in form:
        data["description"] = str(form["description"]).strip()
    if "qty_ordered" in form:
        raw = str(form["qty_ordered"]).strip()
        data["qty_ordered"] = max(1, int(raw)) if raw.isdigit() else 1
    if "unit_cost" in form:
        try:
            data["unit_cost"] = float(form["unit_cost"])
        except (ValueError, TypeError):
            pass
    if "core_charge_per_unit" in form:
        try:
            data["core_charge_per_unit"] = float(form["core_charge_per_unit"])
        except (ValueError, TypeError):
            pass

    try:
        svc.update_line(line_id, data)
    except ValueError as exc:
        log.warning("update_line error on line %s: %s", line_id, exc)

    return _lines_response(po_id, request, db)


@router.post("/{po_id}/lines/{line_id}/delete", response_class=HTMLResponse)
async def po_delete_line(po_id: int, line_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    svc = POService(db, current_user_id=user_id)
    try:
        svc.delete_line(line_id)
    except ValueError as exc:
        log.warning("delete_line error on line %s: %s", line_id, exc)
    return _lines_response(po_id, request, db)


# ── Product search ───────────────────────────────────────────────────────────
# The per-doc /purchase-orders/_/product-search HTML endpoint was removed after
# the §8H migration (its partial purchase_orders/_product_search_results.html is
# gone, along with the now-redundant de-dash patch — separator-insensitive SKU
# matching lives once in SearchService/normalize_part). The PO workspace and the
# product-detail special-order box now call GET /line-items/product-search (JSON).


# ── Status transitions ─────────────────────────────────────────────────────────

@router.post("/{po_id}/send", response_class=RedirectResponse)
def po_send(po_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    svc = POService(db, current_user_id=user_id)
    try:
        svc.send_to_vendor(po_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error sending PO %s to vendor", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — PO was not sent.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}?ok=sent", status_code=303)


@router.post("/{po_id}/receive", response_class=RedirectResponse)
async def po_receive(po_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    _t0 = time.perf_counter()

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)

    # Status guard — only SENT or PARTIAL POs can be received
    if po.status not in (POStatus.SENT, POStatus.PARTIAL):
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Cannot receive: PO must be in SENT or PARTIAL status.')}",
            status_code=303,
        )

    form = await request.form()
    _t_form = time.perf_counter()

    po_line_quantities: dict[int, int] = {}
    condition_notes_map: dict[int, str] = {}
    for line in po.lines:
        raw = form.get(f"recv_{line.id}", "")
        qty = int(raw) if raw and str(raw).strip().isdigit() else 0
        if qty > 0:
            po_line_quantities[line.id] = qty
        # Per-line condition notes (e.g. "damaged", "wrong part")
        cond = str(form.get(f"condition_{line.id}", "")).strip()
        if cond:
            condition_notes_map[line.id] = cond

    if po_line_quantities:
        try:
            svc = POService(db, current_user_id=user_id)
            receipt_data = {
                "tracking_number": str(form.get("tracking_number", "")).strip() or None,
                "carrier": str(form.get("carrier", "")).strip() or None,
                "notes": str(form.get("notes", "")).strip(),
                "condition_notes_map": condition_notes_map,
            }
            svc.create_receipt(
                vendor_id=po.vendor_id,
                po_line_quantities=po_line_quantities,
                data=receipt_data,
            )
        except ValueError as exc:
            db.rollback()
            return RedirectResponse(
                f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
                status_code=303,
            )
        except Exception:
            db.rollback()
            log.exception("Unexpected error receiving PO %s", po_id)
            return RedirectResponse(
                f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — receipt was not recorded.')}",
                status_code=303,
            )

    _t_svc = time.perf_counter()
    _form_ms  = (_t_form - _t0) * 1000
    _svc_ms   = (_t_svc - _t_form) * 1000
    _total_ms = (_t_svc - _t0) * 1000
    log.info(
        "TIMING po_receive po=%s  total=%.1fms  form=%.1fms  svc=%.1fms",
        po_id, _total_ms, _form_ms, _svc_ms,
    )
    resp = RedirectResponse(f"/purchase-orders/{po_id}?ok=received", status_code=303)
    resp.headers["Server-Timing"] = (
        f"form;dur={_form_ms:.1f},svc;dur={_svc_ms:.1f},total;dur={_total_ms:.1f}"
    )
    return resp


@router.post("/{po_id}/cancel-status", response_class=RedirectResponse)
def po_cancel(po_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    svc = POService(db, current_user_id=user_id)
    try:
        svc.cancel(po_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error cancelling PO %s", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — PO was not cancelled.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}", status_code=303)


@router.post("/{po_id}/cancel-line", response_class=RedirectResponse)
async def po_cancel_line(po_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        line_id = int(str(form.get("line_id", "0")))
        reason = str(form.get("reason", "")).strip() or "cancelled"
        POService(db, current_user_id=user_id).cancel_line(line_id, reason)
    except (ValueError, TypeError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error cancelling PO line %s", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — line was not cancelled.')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/purchase-orders/{po_id}?ok={url_quote('Line cancelled — outstanding qty cleared.')}",
        status_code=303,
    )


@router.post("/{po_id}/create-bill", response_class=RedirectResponse)
async def po_create_bill(po_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)

    form = await request.form()
    svc = POService(db, current_user_id=user_id)

    bill_number = str(form.get("bill_number", "")).strip()
    bill_date: datetime | None = None
    due_date: datetime | None = None
    for field, raw in [("bill_date", str(form.get("bill_date", "")).strip()),
                       ("due_date", str(form.get("due_date", "")).strip())]:
        if raw:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d")
                if field == "bill_date":
                    bill_date = parsed
                else:
                    due_date = parsed
            except ValueError:
                pass

    lines = []
    for line in po.lines:
        qty_raw = str(form.get(f"qty_billed_{line.id}", "0")).strip()
        cost_raw = str(form.get(f"unit_cost_{line.id}", "")).strip()
        try:
            qty = int(qty_raw)
        except (ValueError, TypeError):
            qty = 0
        if qty > 0:
            try:
                unit_cost = float(cost_raw)
            except (ValueError, TypeError):
                unit_cost = float(line.unit_cost)
            lines.append({"po_line_id": line.id, "qty_billed": qty, "unit_cost": unit_cost})

    if not lines:
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('No billable quantities entered.')}",
            status_code=303,
        )

    try:
        svc.create_vendor_bill(
            po_id=po_id,
            vendor_id=po.vendor_id,
            bill_number=bill_number,
            bill_date=bill_date,
            due_date=due_date,
            lines=lines,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating bill for PO %s", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — bill was not created.')}",
            status_code=303,
        )

    return RedirectResponse(f"/purchase-orders/{po_id}?ok=billed", status_code=303)


@router.post("/{po_id}/bills/{bill_id}/approve", response_class=RedirectResponse)
async def po_approve_bill(po_id: int, bill_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    svc = POService(db, current_user_id=user_id)
    try:
        svc.approve_bill(bill_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error approving bill %s for PO %s", bill_id, po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — bill was not approved.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}?ok=bill_approved", status_code=303)


@router.post("/{po_id}/bills/{bill_id}/lines/{line_id}/resolve", response_class=RedirectResponse)
async def po_resolve_match_line(
    po_id: int, bill_id: int, line_id: int,
    request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Record an AP resolution decision on a flagged PO match line.

    Requires APPROVE_VENDOR_BILL. Transitions the bill from DISCREPANCY → PENDING
    when all flagged lines are resolved, opening the gate for explicit approval.
    Does NOT approve the bill.
    """
    form = await request.form()
    decision = str(form.get("decision", "")).strip()
    reason = str(form.get("reason", "")).strip()

    svc = POService(db, current_user_id=user_id)
    try:
        svc.resolve_match_line(line_id, decision, reason)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error resolving match line %s", line_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — line was not resolved.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}?ok=match_resolved", status_code=303)


@router.post("/{po_id}/bills/{bill_id}/create-credit", response_class=RedirectResponse)
async def po_create_match_credit(
    po_id: int, bill_id: int,
    request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Create a vendor credit memo for a match discrepancy on a specific PO line.

    Requires APPROVE_VENDOR_BILL + ISSUE_CREDIT_MEMO (both).
    Sets the line's match_resolution to CREDITED and links the VCM.
    apply_now=1 immediately allocates the VCM against the discrepancy bill.
    """
    form = await request.form()
    po_line_id_raw = str(form.get("po_line_id", "")).strip()
    amount_raw = str(form.get("amount", "")).strip()
    reason = str(form.get("reason", "")).strip()
    apply_now = str(form.get("apply_now", "0")).strip() == "1"

    try:
        po_line_id = int(po_line_id_raw)
    except (ValueError, TypeError):
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('po_line_id is required')}",
            status_code=303,
        )

    amount: float | None = None
    if amount_raw:
        try:
            amount = float(amount_raw)
        except ValueError:
            return RedirectResponse(
                f"/purchase-orders/{po_id}?error={url_quote('Invalid credit amount')}",
                status_code=303,
            )

    svc = POService(db, current_user_id=user_id)
    try:
        vcm = svc.create_match_vendor_credit(
            po_line_id=po_line_id,
            amount=amount,
            reason=reason,
            apply_now=apply_now,
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating match credit for PO %s bill %s", po_id, bill_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — credit was not created.')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/purchase-orders/{po_id}?ok={url_quote(f'Vendor credit {vcm.vcm_number} created')}",
        status_code=303,
    )


# ── Print / PDF ───────────────────────────────────────────────────────────────

def _po_print_context(po: PurchaseOrder, db: Session) -> dict:
    company = get_company_dict(db)

    # The company address comes from settings as a multi-line blob — split it
    # into lines for the Ship-To block.
    company_addr_lines = [
        ln.strip() for ln in (company.get("address") or "").splitlines() if ln.strip()
    ]
    if company.get("phone"):
        company_addr_lines.append(company["phone"])

    vendor_addr_lines_ = vendor_address_lines(po.vendor)

    # Drop-ship destination (customer address)
    dropship_customer = None
    dropship_addr_lines: list[str] = []
    if po.is_drop_ship and po.drop_ship_customer_id:
        dropship_customer = (
            db.query(Customer).filter(Customer.id == po.drop_ship_customer_id).first()
        )
        if po.drop_ship_address_id:
            addr = (
                db.query(CustomerAddress)
                .filter(CustomerAddress.id == po.drop_ship_address_id)
                .first()
            )
            if addr is not None:
                # CustomerAddress uses `street` / `street_line2`; build a shim
                # so customer_address_lines() can handle it.
                class _AddrShim:
                    address_line1 = addr.street
                    address_line2 = addr.street_line2
                    city = addr.city
                    state = addr.state
                    zip_code = addr.zip_code
                    phone = addr.phone
                dropship_addr_lines = customer_address_lines(_AddrShim())
        if not dropship_addr_lines and dropship_customer is not None:
            dropship_addr_lines = customer_address_lines(dropship_customer)

    return {
        "po": po,
        "company": company,
        "company_addr_lines": company_addr_lines,
        "vendor_addr_lines": vendor_addr_lines_,
        "dropship_customer": dropship_customer,
        "dropship_addr_lines": dropship_addr_lines,
    }


@router.get("/{po_id}/print", response_class=HTMLResponse)
def po_print(po_id: int, request: Request, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if po is None:
        return RedirectResponse("/purchase-orders/", status_code=303)
    ctx = _po_print_context(po, db)
    return templates.TemplateResponse(request, "purchase_orders/print.html", ctx)


@router.get("/{po_id}/pdf")
def po_pdf(po_id: int, request: Request, db: Session = Depends(get_db)):
    """WeasyPrint PDF; redirects to /print on missing GTK libs."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if po is None:
        return RedirectResponse("/purchase-orders/", status_code=303)
    ctx = _po_print_context(po, db)
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="purchase_orders/print.html",
        context=ctx,
        fallback_print_url=f"/purchase-orders/{po_id}/print",
        download_filename=po.po_number,
    )
