"""
app/routers/vendor_returns.py
==============================
Vendor Return (VR) UI — merchandise returns to vendor lifecycle.

Flow:
  create (DRAFT) → ship (SHIPPED) → vendor decision (ACCEPTED/PARTIAL/REJECTED) → close (CLOSED)

All mutations route through VendorReturnService — no direct model writes here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import VendorReturnStatus, VendorReturnLineOutcome
from app.deps import get_current_user_id, get_db
from app.models.vendor import Vendor
from app.models.vendor_return import VendorReturn, VendorReturnLine
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.services.vendor_return_service import VendorReturnService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/vendor-returns", tags=["vendor_returns"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── Shared helpers ───────────────────────────────────────────────────────────

def _parse_lines(form) -> list[dict]:
    """Read the dynamic array-style line fields the picker submits — one entry
    per row: line_product_id[], line_description[], line_qty[], line_unit_credit[].
    Rows with neither a product nor a description are dropped (blank spare rows)."""
    product_ids = form.getlist("line_product_id[]")
    descriptions = form.getlist("line_description[]")
    qtys = form.getlist("line_qty[]")
    credits = form.getlist("line_unit_credit[]")

    lines: list[dict] = []
    for i in range(len(descriptions)):
        desc = str(descriptions[i]).strip() if i < len(descriptions) else ""
        pid_raw = str(product_ids[i]).strip() if i < len(product_ids) else ""
        if not desc and not pid_raw:
            continue

        try:
            qty = max(1, int(qtys[i])) if i < len(qtys) and str(qtys[i]).strip() else 1
        except (ValueError, TypeError):
            qty = 1

        try:
            credit_raw = credits[i] if i < len(credits) else ""
            unit_credit = float(credit_raw) if str(credit_raw).strip() else 0.0
        except (ValueError, TypeError):
            unit_credit = 0.0

        product_id: int | None = None
        if pid_raw:
            try:
                product_id = int(pid_raw)
            except (ValueError, TypeError):
                product_id = None

        lines.append({
            "product_id": product_id,
            "description": desc,
            "qty": qty,
            "expected_unit_credit": unit_credit,
        })
    return lines


def _opt_id(form, key: str) -> int | None:
    raw = str(form.get(key, "")).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _opt_money(form, key: str) -> float:
    raw = str(form.get(key, "")).strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except (ValueError, TypeError):
        return 0.0


def _product_map(db: Session, vr: VendorReturn) -> dict[int, Product]:
    """SKU/title lookup for the workspace so linked lines show the real part,
    not the internal row id."""
    pids = {ln.product_id for ln in vr.lines if ln.product_id}
    if not pids:
        return {}
    return {p.id: p for p in db.query(Product).filter(Product.id.in_(pids)).all()}


def _seed_lines_from_po(db: Session, po: PurchaseOrder) -> list[dict]:
    """Pre-fill VR rows from a PO's received (or ordered) lines: real product,
    description, qty, and cost → expected unit credit — the natural 'send some
    of this PO back' entry point (mirrors RA-from-invoice)."""
    seed: list[dict] = []
    pids = {ln.product_id for ln in po.lines if ln.product_id}
    prod_map = (
        {p.id: p for p in db.query(Product).filter(Product.id.in_(pids)).all()}
        if pids else {}
    )
    for ln in po.lines:
        qty = ln.qty_received or ln.qty_ordered or 1
        if qty <= 0:
            continue
        prod = prod_map.get(ln.product_id) if ln.product_id else None
        sku = prod.sku if prod else ""
        title = prod.title if prod else (ln.description or "")
        label = f"{sku} — {title}".strip(" —") if (sku or title) else ""
        seed.append({
            "productId": ln.product_id or "",
            "sku": sku,
            "q": label,
            "description": ln.description or title or "",
            "qty": qty,
            "credit": round(ln.unit_cost or 0.0, 2),
        })
    return seed


# ── List ───────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def vr_list(
    request: Request,
    db: Session = Depends(get_db),
):
    vendor_returns = (
        db.query(VendorReturn)
        .order_by(VendorReturn.created_at.desc())
        .limit(100)
        .all()
    )
    # Build a vendor lookup dict so templates can reference vendor names without
    # a relationship defined on VendorReturn.
    vendor_ids = {vr.vendor_id for vr in vendor_returns}
    vendors_map: dict[int, Vendor] = {}
    if vendor_ids:
        for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids)).all():
            vendors_map[v.id] = v

    return templates.TemplateResponse(
        request,
        "vendor_returns/list.html",
        {
            "vendor_returns": vendor_returns,
            "vendors_map": vendors_map,
            "VendorReturnStatus": VendorReturnStatus,
        },
    )


# ── New / Create ───────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def vr_new(
    request: Request,
    po_id: int = 0,
    db: Session = Depends(get_db),
):
    vendors = (
        db.query(Vendor)
        .filter(Vendor.is_active == True)  # noqa: E712
        .order_by(Vendor.name)
        .all()
    )

    # ── Seed from a PO (the "Return to Vendor" entry point) ──────────────────
    selected_vendor_id: int | None = None
    seed_lines: list[dict] = []
    original_po_id: int | None = None
    if po_id:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        if po is not None:
            selected_vendor_id = po.vendor_id
            original_po_id = po.id
            seed_lines = _seed_lines_from_po(db, po)

    return templates.TemplateResponse(
        request,
        "vendor_returns/new.html",
        {
            "vendors": vendors,
            "selected_vendor_id": selected_vendor_id,
            "seed_lines": seed_lines,
            "original_po_id": original_po_id,
            "vr": None,
            "form_action": "/vendor-returns/new",
            "submit_label": "Create Vendor Return",
        },
    )


@router.post("/new", response_class=RedirectResponse)
async def vr_create(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()

    try:
        vendor_id = int(str(form.get("vendor_id", "")).strip())
    except (ValueError, TypeError):
        return RedirectResponse(
            f"/vendor-returns/new?error={url_quote('Vendor is required')}",
            status_code=303,
        )

    reason = str(form.get("reason", "")).strip()
    notes = str(form.get("notes", "")).strip()
    original_po_id = _opt_id(form, "original_po_id")
    original_vendor_bill_id = _opt_id(form, "original_vendor_bill_id")
    restocking_fee = _opt_money(form, "restocking_fee")
    lines = _parse_lines(form)

    try:
        vr = VendorReturnService(db, user_id).create_vendor_return(
            vendor_id=vendor_id,
            lines=lines,
            reason=reason,
            original_po_id=original_po_id,
            original_vendor_bill_id=original_vendor_bill_id,
            notes=notes,
            restocking_fee=restocking_fee,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/vendor-returns/new?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating vendor return")
        return RedirectResponse(
            f"/vendor-returns/new?error={url_quote('Unexpected error — vendor return was not created.')}",
            status_code=303,
        )

    return RedirectResponse(f"/vendor-returns/{vr.id}?ok=Created", status_code=303)


# ── Workspace / Detail ─────────────────────────────────────────────────────────

@router.get("/{vr_id}", response_class=HTMLResponse)
def vr_detail(
    vr_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    vr = db.query(VendorReturn).filter(VendorReturn.id == vr_id).first()
    if not vr:
        return RedirectResponse("/vendor-returns/", status_code=303)
    vendor = db.query(Vendor).filter(Vendor.id == vr.vendor_id).first()
    return templates.TemplateResponse(
        request,
        "vendor_returns/workspace.html",
        {
            "vr": vr,
            "vendor": vendor,
            "product_map": _product_map(db, vr),
            "net_expected_credit": round(vr.expected_credit - vr.restocking_fee, 2),
            "VendorReturnStatus": VendorReturnStatus,
            "VendorReturnLineOutcome": VendorReturnLineOutcome,
        },
    )


# ── Edit (DRAFT only) ────────────────────────────────────────────────────────

@router.get("/{vr_id}/edit", response_class=HTMLResponse)
def vr_edit(
    vr_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    vr = db.query(VendorReturn).filter(VendorReturn.id == vr_id).first()
    if not vr:
        return RedirectResponse("/vendor-returns/", status_code=303)
    if vr.status != VendorReturnStatus.DRAFT:
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote('Only draft returns can be edited.')}",
            status_code=303,
        )

    vendors = (
        db.query(Vendor)
        .filter(Vendor.is_active == True)  # noqa: E712
        .order_by(Vendor.name)
        .all()
    )
    prod_map = _product_map(db, vr)
    seed_lines = []
    for ln in vr.lines:
        prod = prod_map.get(ln.product_id) if ln.product_id else None
        sku = prod.sku if prod else ""
        title = prod.title if prod else (ln.description or "")
        label = f"{sku} — {title}".strip(" —") if (sku or title) else (ln.description or "")
        seed_lines.append({
            "productId": ln.product_id or "",
            "sku": sku,
            "q": label,
            "description": ln.description or "",
            "qty": ln.qty,
            "credit": round(ln.expected_unit_credit or 0.0, 2),
        })

    return templates.TemplateResponse(
        request,
        "vendor_returns/new.html",
        {
            "vendors": vendors,
            "selected_vendor_id": vr.vendor_id,
            "seed_lines": seed_lines,
            "original_po_id": vr.original_po_id,
            "vr": vr,
            "form_action": f"/vendor-returns/{vr_id}/edit",
            "submit_label": "Save Changes",
        },
    )


@router.post("/{vr_id}/edit", response_class=RedirectResponse)
async def vr_update(
    vr_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    vendor_id = _opt_id(form, "vendor_id")
    reason = str(form.get("reason", "")).strip()
    notes = str(form.get("notes", "")).strip()
    restocking_fee = _opt_money(form, "restocking_fee")
    original_po_id = _opt_id(form, "original_po_id") or 0
    original_vendor_bill_id = _opt_id(form, "original_vendor_bill_id") or 0
    lines = _parse_lines(form)

    try:
        VendorReturnService(db, user_id).update_vendor_return(
            vr_id,
            vendor_id=vendor_id,
            reason=reason,
            notes=notes,
            restocking_fee=restocking_fee,
            original_po_id=original_po_id,
            original_vendor_bill_id=original_vendor_bill_id,
            lines=lines,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/vendor-returns/{vr_id}/edit?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error editing vendor return %s", vr_id)
        return RedirectResponse(
            f"/vendor-returns/{vr_id}/edit?error={url_quote('Unexpected error — changes were not saved.')}",
            status_code=303,
        )

    return RedirectResponse(f"/vendor-returns/{vr_id}?ok=Saved", status_code=303)


# ── Void / cancel ────────────────────────────────────────────────────────────

@router.post("/{vr_id}/void", response_class=RedirectResponse)
async def vr_void(
    vr_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    reason = str(form.get("reason", "")).strip()
    try:
        VendorReturnService(db, user_id).void_vendor_return(vr_id, reason=reason)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error voiding vendor return %s", vr_id)
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote('Unexpected error — return was not voided.')}",
            status_code=303,
        )
    return RedirectResponse(f"/vendor-returns/{vr_id}?ok=Voided", status_code=303)


# ── Print (RMA pack slip) ────────────────────────────────────────────────────

@router.get("/{vr_id}/print", response_class=HTMLResponse)
def vr_print(
    vr_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    from app.services.document_render import get_company_dict
    vr = db.query(VendorReturn).filter(VendorReturn.id == vr_id).first()
    if not vr:
        return RedirectResponse("/vendor-returns/", status_code=303)
    vendor = db.query(Vendor).filter(Vendor.id == vr.vendor_id).first()
    return templates.TemplateResponse(
        request,
        "vendor_returns/print.html",
        {
            "vr": vr,
            "vendor": vendor,
            "company": get_company_dict(db),
            "product_map": _product_map(db, vr),
            "net_expected_credit": round(vr.expected_credit - vr.restocking_fee, 2),
        },
    )


# ── Ship ───────────────────────────────────────────────────────────────────────

@router.post("/{vr_id}/ship", response_class=RedirectResponse)
async def vr_ship(
    vr_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    tracking_number = str(form.get("tracking_number", "")).strip()
    rma_number = str(form.get("rma_number", "")).strip()
    decrement_inventory = str(form.get("decrement_inventory", "")).strip() == "1"

    try:
        VendorReturnService(db, user_id).ship_return(
            vr_id=vr_id,
            tracking_number=tracking_number,
            rma_number=rma_number,
            decrement_inventory=decrement_inventory,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error shipping vendor return %s", vr_id)
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote('Unexpected error — shipment was not recorded.')}",
            status_code=303,
        )

    return RedirectResponse(f"/vendor-returns/{vr_id}?ok=Shipped", status_code=303)


# ── Vendor Decision ────────────────────────────────────────────────────────────

@router.post("/{vr_id}/decision", response_class=RedirectResponse)
async def vr_decision(
    vr_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    notes = str(form.get("notes", "")).strip()

    # Build line_outcomes from per-line form fields: outcome_{line_id}, actual_credit_{line_id}
    line_outcomes = []
    form_data = dict(form)
    for key, value in form_data.items():
        if key.startswith("outcome_"):
            line_id_str = key[len("outcome_"):]
            try:
                line_id = int(line_id_str)
            except (ValueError, TypeError):
                continue
            outcome = str(value).strip()
            actual_credit_raw = str(form_data.get(f"actual_credit_{line_id}", "0")).strip()
            try:
                actual_unit_credit = float(actual_credit_raw) if actual_credit_raw else 0.0
            except (ValueError, TypeError):
                actual_unit_credit = 0.0
            line_outcomes.append({
                "line_id": line_id,
                "outcome": outcome,
                "actual_unit_credit": actual_unit_credit,
            })

    try:
        VendorReturnService(db, user_id).record_vendor_decision(
            vr_id=vr_id,
            line_outcomes=line_outcomes,
            notes=notes,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error recording vendor decision for VR %s", vr_id)
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote('Unexpected error — decision was not recorded.')}",
            status_code=303,
        )

    return RedirectResponse(
        f"/vendor-returns/{vr_id}?ok=Decision+recorded", status_code=303
    )


# ── Close ──────────────────────────────────────────────────────────────────────

@router.post("/{vr_id}/close", response_class=RedirectResponse)
def vr_close(
    vr_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        VendorReturnService(db, user_id).close_vendor_return(vr_id=vr_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error closing vendor return %s", vr_id)
        return RedirectResponse(
            f"/vendor-returns/{vr_id}?error={url_quote('Unexpected error — return was not closed.')}",
            status_code=303,
        )

    return RedirectResponse(f"/vendor-returns/{vr_id}?ok=Closed", status_code=303)
