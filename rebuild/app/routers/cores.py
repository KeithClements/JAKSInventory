"""
app/routers/cores.py
=====================
Core charge lifecycle UI — customer returns and vendor core credit tracking.

Three-stage flow (all on the list page):
  1. Customer owes return (OPEN/PARTIAL) → record_customer_return()
  2. Core returned, ready for vendor → submit_to_vendor()
  3. Core shipped to vendor → record_vendor_acceptance() / record_vendor_denial()

All mutations go through CoreService — no direct model writes in routes.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import CoreDirection, CoreDenialResolution, CoreStatus, CoreVendorStatus
from app.deps import get_current_user_id, get_db
from app.models.core import CoreCharge, CoreSlip
from app.settings_utils import get_setting_value_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/cores", tags=["cores"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def cores_list(request: Request, db: Session = Depends(get_db)):
    # Customer cores awaiting return (OPEN or PARTIAL)
    awaiting_return = (
        db.query(CoreCharge)
        .filter(
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
        )
        .order_by(CoreCharge.return_deadline)
        .all()
    )

    # Fully returned from customer, not yet shipped to vendor
    pending_vendor_ship = (
        db.query(CoreCharge)
        .filter(
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status == CoreStatus.RETURNED,
            CoreCharge.vendor_status == CoreVendorStatus.PENDING,
        )
        .order_by(CoreCharge.updated_at)
        .all()
    )

    # Shipped to vendor — awaiting their decision
    awaiting_vendor = (
        db.query(CoreCharge)
        .filter(CoreCharge.status == CoreStatus.SHIPPED_TO_VENDOR)
        .order_by(CoreCharge.updated_at)
        .all()
    )

    return templates.TemplateResponse(
        "cores/list.html",
        {
            "request": request,
            "awaiting_return": awaiting_return,
            "pending_vendor_ship": pending_vendor_ship,
            "awaiting_vendor": awaiting_vendor,
            "CoreDenialResolution": CoreDenialResolution,
        },
    )


# ── Customer Return ───────────────────────────────────────────────────────────

@router.post("/{core_id}/return", response_class=RedirectResponse)
async def record_return(
    core_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Customer brings back their core — issues account credit."""
    from app.services.core_service import CoreService

    form = await request.form()
    try:
        qty = int(form.get("qty_returned") or 1)
        condition = str(form.get("condition", "")).strip() or None
        CoreService(db, user_id).record_customer_return(
            core_charge_id=core_id,
            qty_returned=qty,
            condition=condition,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/cores/?error={url_quote(str(exc))}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Unexpected error recording core return for core_charge %s", core_id)
        return RedirectResponse(
            f"/cores/?error={url_quote('Unexpected error — core return was not recorded.')}",
            status_code=303,
        )
    # Create a core slip and redirect to its print page
    try:
        from app.services.core_service import CoreService as _CS
        slip = _CS(db, user_id).create_core_slip(core_id)
        return RedirectResponse(f"/cores/{core_id}/slip-print?slip_id={slip.id}", status_code=303)
    except Exception:
        log.exception("Could not create core slip for core_charge %s", core_id)
        return RedirectResponse(
            f"/cores/?ok={url_quote('Core return recorded — account credit applied.')}",
            status_code=303,
        )


# ── Submit to Vendor ──────────────────────────────────────────────────────────

@router.post("/{core_id}/submit-to-vendor", response_class=RedirectResponse)
async def submit_to_vendor(
    core_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Mark that JAKS has physically shipped the core back to the vendor."""
    from app.services.core_service import CoreService

    form = await request.form()
    tracking = str(form.get("tracking_number", "")).strip() or None
    try:
        CoreService(db, user_id).submit_to_vendor(
            core_charge_id=core_id,
            tracking_number=tracking,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/cores/?error={url_quote(str(exc))}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Unexpected error submitting core_charge %s to vendor", core_id)
        return RedirectResponse(
            f"/cores/?error={url_quote('Unexpected error — core was not submitted to vendor.')}",
            status_code=303,
        )
    return RedirectResponse(f"/cores/{core_id}/vendor-slip-print", status_code=303)


# ── Vendor Accepted ───────────────────────────────────────────────────────────

@router.post("/{core_id}/vendor-accepted", response_class=RedirectResponse)
async def vendor_accepted(
    core_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Vendor sent credit for the returned core."""
    from app.services.core_service import CoreService

    form = await request.form()
    try:
        credit_amount = float(form.get("credit_amount") or 0)
        CoreService(db, user_id).record_vendor_acceptance(
            core_charge_id=core_id,
            credit_amount=credit_amount,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/cores/?error={url_quote(str(exc))}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Unexpected error recording vendor acceptance for core_charge %s", core_id)
        return RedirectResponse(
            f"/cores/?error={url_quote('Unexpected error — vendor acceptance was not recorded.')}",
            status_code=303,
        )
    return RedirectResponse(f"/cores/?ok={url_quote('Vendor credit recorded.')}", status_code=303)


# ── Vendor Denied ─────────────────────────────────────────────────────────────

@router.post("/{core_id}/vendor-denied", response_class=RedirectResponse)
async def vendor_denied(
    core_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Vendor rejected the core return."""
    from app.services.core_service import CoreService

    form = await request.form()
    denial_reason = str(form.get("denial_reason", "")).strip() or "Rejected by vendor"
    resolution = str(form.get("resolution", CoreDenialResolution.ABSORBED_BY_JAKS))
    notes = str(form.get("notes", "")).strip()
    try:
        CoreService(db, user_id).record_vendor_denial(
            core_charge_id=core_id,
            denial_reason=denial_reason,
            resolution=resolution,
            notes=notes,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/cores/?error={url_quote(str(exc))}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Unexpected error recording vendor denial for core_charge %s", core_id)
        return RedirectResponse(
            f"/cores/?error={url_quote('Unexpected error — vendor denial was not recorded.')}",
            status_code=303,
        )
    return RedirectResponse(f"/cores/?ok={url_quote('Vendor denial recorded.')}", status_code=303)


# ── Core Slip Print (customer receipt when core returned) ─────────────────────

@router.get("/{core_id}/slip-print", response_class=HTMLResponse)
def core_slip_print(
    core_id: int,
    request: Request,
    slip_id: int | None = None,
    db: Session = Depends(get_db),
):
    """
    Standalone print page for a customer core return slip.
    Opens after recording a customer return — user hits Ctrl+P.
    """
    core = db.query(CoreCharge).filter(CoreCharge.id == core_id).first()
    if not core:
        return RedirectResponse("/cores/", status_code=303)

    # Find the slip — prefer slip_id param, fall back to the linked slip
    slip = None
    if slip_id:
        slip = db.query(CoreSlip).filter(CoreSlip.id == slip_id).first()
    if not slip and core.core_slip_id:
        slip = db.query(CoreSlip).filter(CoreSlip.id == core.core_slip_id).first()

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    return templates.TemplateResponse("cores/slip_print.html", {
        "request": request,
        "core": core,
        "slip": slip,
        "company": company,
    })


# ── Vendor Shipment Slip Print (what goes in the box to vendor) ───────────────

@router.get("/{core_id}/vendor-slip-print", response_class=HTMLResponse)
def core_vendor_slip_print(
    core_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Standalone print page for the vendor core return shipment document.
    Shows tracking number and RMA info — printed and included in the box.
    Opens automatically after 'Mark Shipped to Vendor'.
    """
    core = db.query(CoreCharge).filter(CoreCharge.id == core_id).first()
    if not core:
        return RedirectResponse("/cores/", status_code=303)

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    return templates.TemplateResponse("cores/vendor_slip_print.html", {
        "request": request,
        "core": core,
        "company": company,
    })
