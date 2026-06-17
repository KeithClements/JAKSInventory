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

from app.constants import POStatus, POShipToType
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
from app.services.serial_service import SerialService, parse_serials

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
    # §21 — VERBAL_ORDER POs are receivable directly. A phone/verbal order with
    # same-day delivery is a daily diesel-counter event; forcing staff through a
    # "Place Order" (→ SENT) step first is needless friction and inflates
    # qty_on_order. The receiving queue already lists VERBAL_ORDER as awaiting.
    can_receive = po.status in (POStatus.VERBAL_ORDER, POStatus.SENT, POStatus.PARTIAL)
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
        _qd = q.replace("-", "").replace(" ", "")
        _po_dedash = func.replace(func.replace(PurchaseOrder.po_number, "-", ""), " ", "")
        query = query.outerjoin(Vendor, PurchaseOrder.vendor_id == Vendor.id).filter(
            PurchaseOrder.po_number.ilike(like)
            # de-dash so "po20260001" still finds "PO-2026-0001"
            | _po_dedash.ilike(f"%{_qd}%")
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

def _po_core_return_context(db: Session, po: PurchaseOrder) -> dict:
    """
    Cores ready to ship back to THIS PO's vendor + this vendor's recent core
    returns (VCRs) — surfaced on the PO so a vendor core return slip can be
    printed and shipped from the order the cores belong to.

    A customer-returned core is offered for this vendor when ANY of: it is
    explicitly tagged to the vendor, the part was purchased on THIS PO, or the
    product's preferred vendor source is this vendor. Customer cores carry no
    vendor_id until batched, and vendor sources can be sparse, so we union the
    signals rather than rely on one.
    """
    from app.constants import (
        CoreDirection, CoreStatus, CoreVendorStatus, CoreInspectionOutcome,
    )
    from app.models.core import CoreCharge, VendorCoreReturn

    vendor_id = po.vendor_id
    po_product_ids = {ln.product_id for ln in po.lines if ln.product_id}

    ready_all = (
        db.query(CoreCharge)
        .filter(
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status == CoreStatus.RETURNED,
            CoreCharge.vendor_status == CoreVendorStatus.PENDING,
            CoreCharge.inspection_outcome != CoreInspectionOutcome.HOLD,
            CoreCharge.vcr_id.is_(None),
        )
        .order_by(CoreCharge.updated_at)
        .all()
    )

    def _for_this_vendor(c: CoreCharge) -> bool:
        if c.vendor_id == vendor_id:
            return True
        if c.product_id in po_product_ids:
            return True
        pvs = c.product.preferred_vendor_source if c.product else None
        return bool(pvs and pvs.vendor_id == vendor_id)

    ready = [c for c in ready_all if _for_this_vendor(c)]

    vcrs = (
        db.query(VendorCoreReturn)
        .filter(VendorCoreReturn.vendor_id == vendor_id)
        .order_by(VendorCoreReturn.created_at.desc())
        .limit(10)
        .all()
    )

    return {"core_return_ready": ready, "core_return_vcrs": vcrs}


# ── Standalone vendor-bill list (§21) ────────────────────────────────────────
# Registered BEFORE /{po_id} so "bills" isn't matched as a PO id. All vendor
# bills across POs, with their net-of-vendor-credit balance, filterable by status.

@router.get("/bills", response_class=HTMLResponse)
def vendor_bill_list(request: Request, tab: str = "open", q: str = "", db: Session = Depends(get_db)):
    from app.constants import VendorBillStatus
    from app.models.vendor_credit import VendorCreditMemoAllocation

    _OPEN = [VendorBillStatus.PENDING, VendorBillStatus.APPROVED, VendorBillStatus.DISCREPANCY]
    TAB_STATUS = {
        "open": _OPEN,
        "pending": [VendorBillStatus.PENDING],
        "discrepancy": [VendorBillStatus.DISCREPANCY],
        "paid": [VendorBillStatus.PAID],
        "all": [],
    }

    raw_counts = dict(
        db.query(VendorBill.status, func.count(VendorBill.id)).group_by(VendorBill.status).all()
    )
    counts = {
        "open": sum(raw_counts.get(s, 0) for s in _OPEN),
        "pending": raw_counts.get(VendorBillStatus.PENDING, 0),
        "discrepancy": raw_counts.get(VendorBillStatus.DISCREPANCY, 0),
        "paid": raw_counts.get(VendorBillStatus.PAID, 0),
        "all": sum(raw_counts.values()),
    }

    query = (
        db.query(VendorBill)
        .options(joinedload(VendorBill.vendor), joinedload(VendorBill.lines))
        .order_by(VendorBill.created_at.desc())
    )
    statuses = TAB_STATUS.get(tab, _OPEN)
    if statuses:
        query = query.filter(VendorBill.status.in_(statuses))
    if q:
        like = f"%{q.strip()}%"
        query = query.outerjoin(Vendor, VendorBill.vendor_id == Vendor.id).filter(
            VendorBill.bill_number.ilike(like) | Vendor.name.ilike(like)
        )
    bills = query.all()

    # Net-of-vendor-credit balance per shown bill.
    bill_ids = [b.id for b in bills]
    credit_by_bill: dict[int, float] = {}
    if bill_ids:
        for bid, amt in (
            db.query(VendorCreditMemoAllocation.vendor_bill_id,
                     func.sum(VendorCreditMemoAllocation.amount_applied))
            .filter(VendorCreditMemoAllocation.vendor_bill_id.in_(bill_ids),
                    VendorCreditMemoAllocation.is_reversed == False)  # noqa: E712
            .group_by(VendorCreditMemoAllocation.vendor_bill_id).all()
        ):
            credit_by_bill[bid] = float(amt or 0.0)
    balance_map = {
        b.id: (0.0 if b.status == VendorBillStatus.PAID
               else round(b.total_amount - credit_by_bill.get(b.id, 0.0), 2))
        for b in bills
    }

    return templates.TemplateResponse(
        request, "purchase_orders/bills_list.html",
        {
            "bills": bills, "tab": tab, "q": q, "counts": counts,
            "balance_map": balance_map, "VendorBillStatus": VendorBillStatus,
            "now": datetime.utcnow(),
        },
    )


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
    ctx.update(_po_core_return_context(db, po))
    # Bill-to / ship-to controls + resolved blocks for the header.
    primary = _primary_company_location(db)
    ctx["company_locations"] = _active_company_locations(db)
    ctx["default_location_id"] = primary.id if primary else None
    ctx["effective_ship_to_type"] = _effective_ship_to_type(po)
    ctx.update(_resolve_po_addresses(po, db))
    return templates.TemplateResponse(request, "purchase_orders/workspace.html", ctx)


# ── Core Returns to Vendor (reuses the cores/VCR ledger; print ≠ ship) ─────────

@router.post("/{po_id}/core-return", response_class=RedirectResponse)
async def po_core_return_create(
    po_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Batch the selected ready cores into a vendor core return (VCR) for this
    PO's vendor, then open the printable slip (vendor + office copies). Tracking
    is captured later via mark-shipped — printing never requires it."""
    from app.services.core_service import CoreService
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)
    form = await request.form()
    try:
        core_ids = [int(i) for i in form.getlist("core_ids")]
        vcr = CoreService(db, user_id).create_vcr(
            vendor_id=po.vendor_id,
            core_charge_ids=core_ids,
            notes=f"Core return for PO {po.po_number}",
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating core return for PO %s", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error="
            f"{url_quote('Unexpected error — the core return was not created.')}",
            status_code=303)
    return RedirectResponse(f"/cores/vcr/{vcr.id}/print?copies=both", status_code=303)


@router.post("/{po_id}/core-return/{vcr_id}/ship", response_class=RedirectResponse)
async def po_core_return_ship(
    po_id: int, vcr_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Mark a vendor core return shipped — tracking + RMA optional (printed,
    signed, boxed now; shipped/recorded later). Stays on the PO."""
    from app.services.core_service import CoreService
    form = await request.form()
    try:
        CoreService(db, user_id).ship_vcr(
            vcr_id=vcr_id,
            tracking_number=str(form.get("tracking_number", "")).strip(),
            rma_number=str(form.get("rma_number", "")).strip(),
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Unexpected error shipping core return %s for PO %s", vcr_id, po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error="
            f"{url_quote('Unexpected error — the core return was not marked shipped.')}",
            status_code=303)
    return RedirectResponse(
        f"/purchase-orders/{po_id}?ok={url_quote('Core return marked shipped.')}",
        status_code=303)


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

    def _opt_int(key: str):
        raw = str(form.get(key, "")).strip()
        return int(raw) if raw.isdigit() else None

    data = {
        "notes": str(form.get("notes", "")).strip(),
        "internal_notes": str(form.get("internal_notes", "")).strip(),
        "vendor_confirmation_number": str(form.get("vendor_confirmation_number", "")).strip() or None,
        # Checkbox: present in the form body only when ticked.
        "vendor_confirmed": form.get("vendor_confirmed") is not None,
        "freight_in_cost": float(form.get("freight_in_cost") or 0.0),
        "expected_at": expected_dt,
        # Bill-to / ship-to (save_header sorts out which ship-to fields apply).
        "bill_to_location_id": _opt_int("bill_to_location_id"),
        "ship_to_type": str(form.get("ship_to_type", "")).strip() or None,
        "ship_to_location_id": _opt_int("ship_to_location_id"),
        "ship_to_snapshot": str(form.get("ship_to_snapshot", "")).strip() or None,
        "drop_ship_customer_id": _opt_int("drop_ship_customer_id"),
        "drop_ship_address_id": _opt_int("drop_ship_address_id"),
    }
    # Only let the header autosave touch ship-to when the form actually carries it
    # (the field is present on the PO workspace form). Guards other callers.
    if "ship_to_type" not in form:
        for k in ("bill_to_location_id", "ship_to_type", "ship_to_location_id",
                  "ship_to_snapshot", "drop_ship_customer_id", "drop_ship_address_id"):
            data.pop(k, None)
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

    # Status guard — VERBAL_ORDER, SENT, or PARTIAL POs can be received (§21:
    # verbal/phone orders receive directly without a Place Order step).
    if po.status not in (POStatus.VERBAL_ORDER, POStatus.SENT, POStatus.PARTIAL):
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Cannot receive: PO must be in VERBAL ORDER, SENT, or PARTIAL status.')}",
            status_code=303,
        )

    form = await request.form()
    _t_form = time.perf_counter()

    po_line_quantities: dict[int, int] = {}
    condition_notes_map: dict[int, str] = {}
    serials_map: dict[int, list[str]] = {}
    for line in po.lines:
        raw = form.get(f"recv_{line.id}", "")
        qty = int(raw) if raw and str(raw).strip().isdigit() else 0
        if qty > 0:
            po_line_quantities[line.id] = qty
        # Per-line condition notes (e.g. "damaged", "wrong part")
        cond = str(form.get(f"condition_{line.id}", "")).strip()
        if cond:
            condition_notes_map[line.id] = cond
        # R3 — optional per-line serial numbers (serialized products only;
        # textarea is comma/newline separated). Parsed here, recorded AFTER
        # the receipt service call succeeds.
        parsed_serials = parse_serials(str(form.get(f"serials_{line.id}", "") or ""))
        if parsed_serials:
            serials_map[line.id] = parsed_serials

    # R1-12 — qty inputs default to 0 so a careless submit can't fully receive
    # a partial delivery; an all-zero submit must not flash "received".
    if not po_line_quantities:
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('No receive quantities entered — lines left at 0 are skipped.')}",
            status_code=303,
        )

    receipt = None
    if po_line_quantities:
        try:
            svc = POService(db, current_user_id=user_id)
            receipt_data = {
                "tracking_number": str(form.get("tracking_number", "")).strip() or None,
                "carrier": str(form.get("carrier", "")).strip() or None,
                "notes": str(form.get("notes", "")).strip(),
                "condition_notes_map": condition_notes_map,
            }
            receipt = svc.create_receipt(
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

    # R3 — serial-number capture (fail-safe). The receipt above has already
    # committed; serials are recorded in a follow-up transaction so a serial
    # problem can never undo the goods receipt. Receiving with no serials is
    # always allowed; count mismatches are allowed but flashed as an info note.
    info_notes: list[str] = []
    if serials_map and receipt is not None:
        try:
            serial_svc = SerialService(db, current_user_id=user_id)
            receipt_line_by_po_line = {rl.po_line_id: rl for rl in receipt.lines}
            line_by_id = {ln.id: ln for ln in po.lines}
            for po_line_id, serials in serials_map.items():
                line = line_by_id.get(po_line_id)
                if line is None or not line.product_id:
                    continue
                product = line.product
                if not product or not product.has_serial_number:
                    continue  # serials only tracked for serialized products
                label = product.sku or f"line {po_line_id}"
                qty_received = po_line_quantities.get(po_line_id, 0)
                if qty_received <= 0:
                    info_notes.append(
                        f"{label}: serial numbers entered but the line was not received — not recorded."
                    )
                    continue
                receipt_line = receipt_line_by_po_line.get(po_line_id)
                result = serial_svc.record_received_serials(
                    product_id=line.product_id,
                    serials=serials,
                    po_receipt_line_id=receipt_line.id if receipt_line else None,
                )
                if result["skipped"]:
                    info_notes.append(
                        f"{label}: {len(result['skipped'])} duplicate serial(s) skipped "
                        f"({', '.join(result['skipped'][:5])})."
                    )
                if len(serials) != qty_received:
                    info_notes.append(
                        f"{label}: {len(serials)} serial(s) entered for {qty_received} unit(s) received."
                    )
            db.commit()
        except Exception:
            db.rollback()
            log.exception(
                "Serial capture failed for PO %s — receipt itself was already recorded", po_id
            )
            info_notes = ["Serial numbers could not be recorded — the goods receipt itself was saved."]

    _t_svc = time.perf_counter()
    _form_ms  = (_t_form - _t0) * 1000
    _svc_ms   = (_t_svc - _t_form) * 1000
    _total_ms = (_t_svc - _t0) * 1000
    log.info(
        "TIMING po_receive po=%s  total=%.1fms  form=%.1fms  svc=%.1fms",
        po_id, _total_ms, _form_ms, _svc_ms,
    )
    _redirect_url = f"/purchase-orders/{po_id}?ok=received"
    if info_notes:
        _redirect_url += f"&info={url_quote(' '.join(info_notes))}"
    resp = RedirectResponse(_redirect_url, status_code=303)
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
async def po_approve_bill(
    po_id: int, bill_id: int,
    request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # `override_reason` (optional) documents an "Approve Anyway" override when the
    # bill is still in DISCREPANCY. Empty for a normal reconciled approval.
    form = await request.form()
    override_reason = str(form.get("override_reason", "")).strip()

    svc = POService(db, current_user_id=user_id)
    try:
        svc.approve_bill(bill_id, override_reason=override_reason)
    except (ValueError, PermissionError) as exc:
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


@router.post("/{po_id}/bills/{bill_id}/pay", response_class=RedirectResponse)
def po_pay_bill(
    po_id: int, bill_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # R1-12 — AP reconciliation: records the bill as paid; no money moves here.
    svc = POService(db, current_user_id=user_id)
    try:
        svc.mark_bill_paid(bill_id)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error marking bill %s paid for PO %s", bill_id, po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — bill was not marked paid.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}?ok=bill_paid", status_code=303)


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


@router.post("/{po_id}/bills/{bill_id}/lines/{line_id}/correct", response_class=RedirectResponse)
async def po_correct_match_line(
    po_id: int, bill_id: int, line_id: int,
    request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Correct the actual PO/bill numbers on a flagged match line so they reconcile,
    then clear the flag (match_resolution = CORRECTED). Requires APPROVE_VENDOR_BILL.

    Unlike /resolve (which only records a decision and leaves the numbers diverged),
    this edits POLine.unit_cost and/or the bill line's qty_billed / unit_cost,
    recomputes the bill total, and enforces a must-match gate. Opens the bill
    DISCREPANCY -> PENDING when the last flag clears; does NOT approve.

    `line_id` is the PO line id (mirrors the /resolve route).
    Form fields (all the numeric ones optional; blank = leave that side unchanged):
      po_unit_cost · billed_qty · billed_unit_cost · reason (required)
    """
    form = await request.form()
    reason = str(form.get("reason", "")).strip()

    def _opt_float(key: str) -> float | None:
        raw = str(form.get(key, "")).strip()
        return float(raw) if raw else None

    def _opt_int(key: str) -> int | None:
        raw = str(form.get(key, "")).strip()
        return int(raw) if raw else None

    try:
        po_unit_cost = _opt_float("po_unit_cost")
        billed_unit_cost = _opt_float("billed_unit_cost")
        billed_qty = _opt_int("billed_qty")
    except (ValueError, TypeError):
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Enter valid numbers for the corrected cost / qty.')}",
            status_code=303,
        )

    svc = POService(db, current_user_id=user_id)
    try:
        svc.correct_match_line(
            po_line_id=line_id,
            bill_id=bill_id,
            new_po_unit_cost=po_unit_cost,
            new_billed_qty=billed_qty,
            new_billed_unit_cost=billed_unit_cost,
            reason=reason,
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error correcting match line %s on bill %s", line_id, bill_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — line was not corrected.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}?ok=match_corrected", status_code=303)


# ── Bill-to / Ship-to ───────────────────────────────────────────────────────

def _active_company_locations(db: Session) -> list:
    from app.models.company_location import CompanyLocation
    return (
        db.query(CompanyLocation)
        .filter(CompanyLocation.is_active == True)  # noqa: E712
        .order_by(CompanyLocation.is_primary.desc(), CompanyLocation.name)
        .all()
    )


def _primary_company_location(db: Session):
    from app.models.company_location import CompanyLocation
    return (
        db.query(CompanyLocation)
        .filter(CompanyLocation.is_active == True,  # noqa: E712
                CompanyLocation.is_primary == True)  # noqa: E712
        .first()
    )


def _effective_ship_to_type(po: PurchaseOrder) -> str:
    """ship_to_type, but legacy drop-ship POs (created before the field existed,
    so type is still the 'location' default) are treated as drop_ship."""
    stype = po.ship_to_type or POShipToType.LOCATION
    if po.is_drop_ship and stype == POShipToType.LOCATION:
        return POShipToType.DROP_SHIP
    return stype


def _resolve_po_addresses(po: PurchaseOrder, db: Session) -> dict:
    """Render-ready {name, lines} blocks for the PO's bill-to and ship-to, used by
    both the workspace and the print/PDF so the branching lives in one place."""
    primary = _primary_company_location(db)

    bill_loc = po.bill_to_location or primary
    bill_to = {
        "name": bill_loc.name if bill_loc else (get_company_dict(db).get("name") or ""),
        "lines": bill_loc.address_lines if bill_loc else [],
    }

    stype = _effective_ship_to_type(po)
    if stype == POShipToType.DROP_SHIP:
        cust = (
            db.query(Customer).filter(Customer.id == po.drop_ship_customer_id).first()
            if po.drop_ship_customer_id else None
        )
        snap = [ln.strip() for ln in (po.ship_to_snapshot or "").splitlines() if ln.strip()]
        lines = snap or (customer_address_lines(cust) if cust else [])
        cust_name = ""
        if cust is not None:
            cust_name = (getattr(cust, "display_name", "") or getattr(cust, "company_name", "")
                         or getattr(cust, "name", "") or "")
        ship_to = {"kind": "drop_ship", "name": cust_name or "Drop-ship",
                   "lines": lines, "is_drop_ship": True}
    elif stype == POShipToType.AD_HOC:
        lines = [ln.strip() for ln in (po.ship_to_snapshot or "").splitlines() if ln.strip()]
        ship_to = {"kind": "ad_hoc", "name": "One-time address",
                   "lines": lines, "is_drop_ship": False}
    else:
        loc = po.ship_to_location or primary
        ship_to = {"kind": "location", "name": loc.name if loc else "",
                   "lines": loc.address_lines if loc else [], "is_drop_ship": False}
    return {"bill_to": bill_to, "ship_to": ship_to}


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

    addrs = _resolve_po_addresses(po, db)
    return {
        "po": po,
        "company": company,
        "company_addr_lines": company_addr_lines,
        "vendor_addr_lines": vendor_addr_lines_,
        "dropship_customer": dropship_customer,
        "dropship_addr_lines": dropship_addr_lines,
        "bill_to": addrs["bill_to"],
        "ship_to": addrs["ship_to"],
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


@router.get("/{po_id}/receiving-slip", response_class=HTMLResponse)
def po_receiving_slip(po_id: int, request: Request, db: Session = Depends(get_db)):
    """R2 — warehouse receiving slip (dock check-off sheet).

    Print-styled internal document for checking a delivery against the PO:
    one row per line with SKU, title, vendor part #, qty ordered, qty received
    so far, qty outstanding, and a blank write-in check-off column. No money
    columns — the dock doesn't need costs. Opened in a new tab from the
    Receiving Queue (same idiom as the PO print button).
    """
    po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.lines).joinedload(POLine.product),
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if po is None:
        return RedirectResponse("/purchase-orders/", status_code=303)

    company = get_company_dict(db)
    company_addr_lines = [
        ln.strip() for ln in (company.get("address") or "").splitlines() if ln.strip()
    ]
    if company.get("phone"):
        company_addr_lines.append(company["phone"])

    # Vendor part numbers: prefer the active source for THIS PO's vendor,
    # fall back to the product's preferred source. Keyed by product_id.
    from app.models.product import ProductVendorSource

    product_ids = [ln.product_id for ln in po.lines if ln.product_id]
    vendor_part_map: dict[int, str] = {}
    if product_ids:
        sources = (
            db.query(ProductVendorSource)
            .filter(
                ProductVendorSource.product_id.in_(product_ids),
                ProductVendorSource.vendor_id == po.vendor_id,
                ProductVendorSource.is_active == True,  # noqa: E712
            )
            .all()
        )
        vendor_part_map = {
            s.product_id: s.vendor_part_number
            for s in sources
            if s.vendor_part_number
        }
        for ln in po.lines:
            if ln.product_id and ln.product_id not in vendor_part_map and ln.product:
                src = ln.product.preferred_vendor_source
                if src and src.vendor_part_number:
                    vendor_part_map[ln.product_id] = src.vendor_part_number

    return templates.TemplateResponse(
        request,
        "purchase_orders/receiving_slip_print.html",
        {
            "po": po,
            "company": company,
            "company_addr_lines": company_addr_lines,
            "vendor_addr_lines": vendor_address_lines(po.vendor),
            "vendor_part_map": vendor_part_map,
        },
    )
