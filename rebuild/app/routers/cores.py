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

from datetime import timedelta

from app.constants import CoreDirection, CoreDenialResolution, CoreInspectionOutcome, CoreStatus, CoreVendorStatus
from app.deps import get_current_user_id, get_db
from app.models.core import CoreCharge, CoreSlip, VendorCoreReturn
from app.models.invoice import Invoice
from app.services.document_render import (
    customer_address_lines,
    get_company_dict,
    render_pdf_or_fallback,
    vendor_address_lines,
)
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

    # Cores physically received but held for inspection (not yet accepted or rejected)
    pending_inspection = (
        db.query(CoreCharge)
        .filter(
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status.in_([CoreStatus.RETURNED, CoreStatus.PARTIAL]),
            CoreCharge.inspection_outcome == CoreInspectionOutcome.HOLD,
        )
        .order_by(CoreCharge.updated_at)
        .all()
    )

    # Fully returned from customer, inspected (accepted), not yet shipped to vendor
    pending_vendor_ship = (
        db.query(CoreCharge)
        .filter(
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
            CoreCharge.status == CoreStatus.RETURNED,
            CoreCharge.vendor_status == CoreVendorStatus.PENDING,
            # Exclude held cores — they're not ready to ship until inspection passes
            CoreCharge.inspection_outcome != CoreInspectionOutcome.HOLD,
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
            "pending_inspection": pending_inspection,
            "pending_vendor_ship": pending_vendor_ship,
            "awaiting_vendor": awaiting_vendor,
            "CoreDenialResolution": CoreDenialResolution,
            "CoreInspectionOutcome": CoreInspectionOutcome,
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
    inspection_outcome = str(form.get("inspection_outcome", CoreInspectionOutcome.ACCEPTED)).strip()
    # Guard against tampered form values
    if inspection_outcome not in (
        CoreInspectionOutcome.ACCEPTED,
        CoreInspectionOutcome.HOLD,
        CoreInspectionOutcome.REJECTED,
    ):
        inspection_outcome = CoreInspectionOutcome.ACCEPTED

    try:
        qty = int(form.get("qty_returned") or 1)
        condition = str(form.get("condition", "")).strip() or None
        CoreService(db, user_id).record_customer_return(
            core_charge_id=core_id,
            qty_returned=qty,
            condition=condition,
            inspection_outcome=inspection_outcome,
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
    # For HOLD or REJECTED outcomes — no credit slip, redirect straight to list
    if inspection_outcome == CoreInspectionOutcome.REJECTED:
        return RedirectResponse(
            f"/cores/?ok={url_quote('Core refused — charge closed, no credit issued.')}",
            status_code=303,
        )
    if inspection_outcome == CoreInspectionOutcome.HOLD:
        return RedirectResponse(
            f"/cores/?ok={url_quote('Core received and held for inspection. Credit will be issued after review.')}",
            status_code=303,
        )

    # ACCEPTED — create a core slip and redirect to its print page.
    # Idempotent: if a slip was already created for this charge (e.g. a prior partial
    # return already ran this path), reuse it rather than minting a duplicate.
    try:
        from app.services.core_service import CoreService as _CS
        core_obj = db.query(CoreCharge).filter(CoreCharge.id == core_id).first()
        if core_obj and core_obj.core_slip_id:
            return RedirectResponse(
                f"/cores/{core_id}/slip-print?slip_id={core_obj.core_slip_id}",
                status_code=303,
            )
        slip = _CS(db, user_id).create_core_slip(core_id)
        return RedirectResponse(f"/cores/{core_id}/slip-print?slip_id={slip.id}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Could not create core slip for core_charge %s", core_id)
        return RedirectResponse(
            f"/cores/?ok={url_quote('Core return recorded — account credit applied.')}",
            status_code=303,
        )


# ── Complete Inspection (resolve a HOLD) ──────────────────────────────────────

@router.post("/{core_id}/complete-inspection", response_class=RedirectResponse)
async def complete_inspection(
    core_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Accept or reject a core that was placed on hold during initial inspection."""
    from app.services.core_service import CoreService

    form = await request.form()
    final_outcome = str(form.get("final_outcome", "")).strip()
    notes = str(form.get("notes", "")).strip() or None

    if final_outcome not in (CoreInspectionOutcome.ACCEPTED, CoreInspectionOutcome.REJECTED):
        return RedirectResponse(
            f"/cores/?error={url_quote('Invalid inspection outcome.')}",
            status_code=303,
        )

    try:
        CoreService(db, user_id).complete_inspection(
            core_charge_id=core_id,
            final_outcome=final_outcome,
            notes=notes,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/cores/?error={url_quote(str(exc))}", status_code=303)
    except Exception:
        db.rollback()
        log.exception("Unexpected error completing inspection for core_charge %s", core_id)
        return RedirectResponse(
            f"/cores/?error={url_quote('Unexpected error — inspection decision was not recorded.')}",
            status_code=303,
        )

    if final_outcome == CoreInspectionOutcome.ACCEPTED:
        # Now create the core slip for the accepted return
        try:
            from app.services.core_service import CoreService as _CS
            core_obj = db.query(CoreCharge).filter(CoreCharge.id == core_id).first()
            if core_obj and core_obj.core_slip_id:
                return RedirectResponse(
                    f"/cores/{core_id}/slip-print?slip_id={core_obj.core_slip_id}",
                    status_code=303,
                )
            slip = _CS(db, user_id).create_core_slip(core_id)
            return RedirectResponse(f"/cores/{core_id}/slip-print?slip_id={slip.id}", status_code=303)
        except Exception:
            db.rollback()
            log.exception("Could not create core slip after inspection for core_charge %s", core_id)
            return RedirectResponse(
                f"/cores/?ok={url_quote('Inspection passed — credit issued.')}",
                status_code=303,
            )
    else:
        return RedirectResponse(
            f"/cores/?ok={url_quote('Core rejected after inspection — charge closed, no credit.')}",
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


# ── Customer Core Return Slip (CORE-XXXX) — group document ─────────────────────

def _slip_print_context(slip: CoreSlip, db: Session) -> dict:
    company = get_company_dict(db)
    customer_addr_lines_ = customer_address_lines(slip.customer)

    # Outstanding cores attached to this slip
    cores = [c for c in (slip.core_charges or []) if c.qty_outstanding > 0] or list(slip.core_charges or [])

    total_qty = sum(c.qty_outstanding for c in cores)
    total_credit = round(
        sum(c.customer_unit_charge * c.qty_outstanding for c in cores), 2
    )

    grace = int(get_setting_value_db(db, "core_return_grace_days", "45") or 45)
    soonest_deadline = None
    for c in cores:
        if c.return_deadline and (soonest_deadline is None or c.return_deadline < soonest_deadline):
            soonest_deadline = c.return_deadline
    if soonest_deadline is None:
        soonest_deadline = slip.created_at + timedelta(days=grace)

    invoice = None
    if slip.invoice_id:
        invoice = db.query(Invoice).filter(Invoice.id == slip.invoice_id).first()

    return {
        "slip": slip,
        "cores": cores,
        "invoice": invoice,
        "company": company,
        "customer_addr_lines": customer_addr_lines_,
        "total_qty": total_qty,
        "total_credit": total_credit,
        "default_grace_days": grace,
        "soonest_deadline": soonest_deadline,
    }


@router.get("/slips/{slip_id}/print", response_class=HTMLResponse)
def core_slip_doc_print(slip_id: int, request: Request, db: Session = Depends(get_db)):
    slip = db.query(CoreSlip).filter(CoreSlip.id == slip_id).first()
    if slip is None:
        return RedirectResponse("/cores/", status_code=303)
    ctx = _slip_print_context(slip, db)
    ctx["request"] = request
    return templates.TemplateResponse("cores/print_slip.html", ctx)


@router.get("/slips/{slip_id}/pdf")
def core_slip_doc_pdf(slip_id: int, request: Request, db: Session = Depends(get_db)):
    slip = db.query(CoreSlip).filter(CoreSlip.id == slip_id).first()
    if slip is None:
        return RedirectResponse("/cores/", status_code=303)
    ctx = _slip_print_context(slip, db)
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="cores/print_slip.html",
        context=ctx,
        fallback_print_url=f"/cores/slips/{slip_id}/print",
        download_filename=slip.slip_number,
    )


# ── Vendor Core Return Sheet (VCR-XXXX) — group document ──────────────────────

def _vcr_print_context(vcr: VendorCoreReturn, db: Session) -> dict:
    company = get_company_dict(db)

    company_addr_lines = [
        ln.strip() for ln in (company.get("address") or "").splitlines() if ln.strip()
    ]
    if company.get("phone"):
        company_addr_lines.append(company["phone"])

    from app.models.vendor import Vendor as _V
    vendor = db.query(_V).filter(_V.id == vcr.vendor_id).first()
    vendor_addr_lines_ = vendor_address_lines(vendor)

    total_qty = sum(ln.qty for ln in (vcr.lines or []))

    return {
        "vcr": vcr,
        "vendor": vendor,
        "company": company,
        "company_addr_lines": company_addr_lines,
        "vendor_addr_lines": vendor_addr_lines_,
        "total_qty": total_qty,
    }


@router.get("/vcr/{vcr_id}/print", response_class=HTMLResponse)
def vcr_doc_print(vcr_id: int, request: Request, db: Session = Depends(get_db)):
    vcr = db.query(VendorCoreReturn).filter(VendorCoreReturn.id == vcr_id).first()
    if vcr is None:
        return RedirectResponse("/cores/", status_code=303)
    ctx = _vcr_print_context(vcr, db)
    ctx["request"] = request
    return templates.TemplateResponse("cores/print_vcr.html", ctx)


@router.get("/vcr/{vcr_id}/pdf")
def vcr_doc_pdf(vcr_id: int, request: Request, db: Session = Depends(get_db)):
    vcr = db.query(VendorCoreReturn).filter(VendorCoreReturn.id == vcr_id).first()
    if vcr is None:
        return RedirectResponse("/cores/", status_code=303)
    ctx = _vcr_print_context(vcr, db)
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="cores/print_vcr.html",
        context=ctx,
        fallback_print_url=f"/cores/vcr/{vcr_id}/print",
        download_filename=vcr.vcr_number,
    )
