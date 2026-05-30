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
from app.services.vendor_return_service import VendorReturnService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/vendor-returns", tags=["vendor_returns"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


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
    db: Session = Depends(get_db),
):
    vendors = (
        db.query(Vendor)
        .filter(Vendor.is_active == True)  # noqa: E712
        .order_by(Vendor.name)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "vendor_returns/new.html",
        {
            "vendors": vendors,
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

    original_po_id_raw = str(form.get("original_po_id", "")).strip()
    original_po_id: int | None = None
    if original_po_id_raw:
        try:
            original_po_id = int(original_po_id_raw)
        except (ValueError, TypeError):
            original_po_id = None

    original_vendor_bill_id_raw = str(form.get("original_vendor_bill_id", "")).strip()
    original_vendor_bill_id: int | None = None
    if original_vendor_bill_id_raw:
        try:
            original_vendor_bill_id = int(original_vendor_bill_id_raw)
        except (ValueError, TypeError):
            original_vendor_bill_id = None

    # Collect lines: n = 1..10
    lines = []
    for n in range(1, 11):
        desc_raw = str(form.get(f"line_description_{n}", "")).strip()
        qty_raw = str(form.get(f"line_qty_{n}", "")).strip()
        credit_raw = str(form.get(f"line_unit_credit_{n}", "")).strip()
        product_id_raw = str(form.get(f"line_product_id_{n}", "")).strip()

        # Skip empty rows
        if not desc_raw and not product_id_raw:
            continue

        try:
            qty = max(1, int(qty_raw)) if qty_raw else 1
        except (ValueError, TypeError):
            qty = 1

        try:
            expected_unit_credit = float(credit_raw) if credit_raw else 0.0
        except (ValueError, TypeError):
            expected_unit_credit = 0.0

        product_id: int | None = None
        if product_id_raw:
            try:
                product_id = int(product_id_raw)
            except (ValueError, TypeError):
                product_id = None

        lines.append({
            "product_id": product_id,
            "description": desc_raw,
            "qty": qty,
            "expected_unit_credit": expected_unit_credit,
        })

    try:
        vr = VendorReturnService(db, user_id).create_vendor_return(
            vendor_id=vendor_id,
            lines=lines,
            reason=reason,
            original_po_id=original_po_id,
            original_vendor_bill_id=original_vendor_bill_id,
            notes=notes,
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
            "VendorReturnStatus": VendorReturnStatus,
            "VendorReturnLineOutcome": VendorReturnLineOutcome,
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
