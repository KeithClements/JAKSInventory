"""
app/routers/warranty.py
========================
Warranty claim UI — list, create, detail, and full lifecycle actions.

Workflow:
  DRAFT → submit_to_vendor() → record_vendor_decision() →
    (approved)  credit_customer() or issue_refund_check()
    (denied)    notify_customer_of_denial()
  → close_claim()

All mutations route through WarrantyService — no direct model writes here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import LineType, WarrantyDecision, WarrantyResolution, WarrantyStatus
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.vendor import Vendor
from app.models.warranty import WarrantyClaim
from app.services.document_render import (
    customer_address_lines,
    get_company_dict,
    render_pdf_or_fallback,
    vendor_address_lines,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/warranty", tags=["warranty"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def warranty_list(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
):
    """
    Warranty Queue — QB2 Queue Board (§2A): active claims grouped by vendor, with a
    metrics strip and a per-state stripe/chip + next-action link into the claim.
    Closed claims are excluded — this is a work queue, not an archive.  Queue-route
    assembly is UI-builder scope per the §6 queue-board precedent (cf. po_receiving_queue).
    """
    from datetime import datetime

    # status → queue-state slug (drives stripe / chip / next-action in the template)
    _state_of = {
        WarrantyStatus.DRAFT:               "draft",
        WarrantyStatus.SUBMITTED_TO_VENDOR: "submitted",
        WarrantyStatus.VENDOR_APPROVED:     "approved",
        WarrantyStatus.VENDOR_DENIED:       "denied",
        WarrantyStatus.CUSTOMER_CREDITED:   "credited",
        WarrantyStatus.CUSTOMER_NOTIFIED:   "notified",
    }
    NO_VENDOR = "— No Vendor Assigned —"
    STALE_DAYS = 14  # awaiting a vendor decision longer than this flags red

    # All open (non-closed) claims drive the metrics; the visible rows may be search-filtered.
    active = (
        db.query(WarrantyClaim)
        .join(Customer)
        .filter(WarrantyClaim.status != WarrantyStatus.CLOSED)
        .order_by(WarrantyClaim.claim_date.desc())
        .all()
    )

    claims = active
    if q:
        ql = q.lower()
        claims = [
            c for c in active
            if ql in (c.claim_number or "").lower()
            or ql in (c.customer.company_name or "").lower()
        ]

    now = datetime.now()
    rows = []
    for c in claims:
        wait_days = None
        stale = False
        if c.status == WarrantyStatus.SUBMITTED_TO_VENDOR and c.submitted_to_vendor_at:
            wait_days = (now - c.submitted_to_vendor_at).days
            stale = wait_days >= STALE_DAYS
        rows.append({
            "claim": c,
            "vendor_name": c.vendor.name if c.vendor else NO_VENDOR,
            "state": _state_of.get(c.status, "draft"),
            "wait_days": wait_days,
            "stale": stale,
        })

    # Group by vendor — real vendors first (alpha), "no vendor" last; newest first within.
    rows.sort(key=lambda r: (
        r["vendor_name"] == NO_VENDOR,
        r["vendor_name"],
        -(r["claim"].claim_date.timestamp() if r["claim"].claim_date else 0.0),
    ))

    metrics = {
        "drafts":          sum(1 for c in active if c.status == WarrantyStatus.DRAFT),
        "awaiting_vendor": sum(1 for c in active if c.status == WarrantyStatus.SUBMITTED_TO_VENDOR),
        "to_credit":       sum(1 for c in active if c.status == WarrantyStatus.VENDOR_APPROVED),
        "to_notify":       sum(1 for c in active if c.status == WarrantyStatus.VENDOR_DENIED),
    }

    return templates.TemplateResponse(
        request,
        "warranty/list.html",
        {
            "rows": rows,
            "metrics": metrics,
            "total": len(rows),
            "q": q,
            "now": now,
        },
    )


# ── New / Create ───────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def warranty_new(
    request: Request,
    customer_id: int = 0,
    invoice_id: int = 0,
    db: Session = Depends(get_db),
):
    if not request.headers.get("HX-Request"):
        return RedirectResponse("/warranty/", status_code=303)
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    vendors = (
        db.query(Vendor)
        .filter(Vendor.is_active == True)  # noqa: E712
        .order_by(Vendor.name)
        .all()
    )
    products = (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .order_by(Product.sku)
        .all()
    )
    selected_customer = (
        db.query(Customer).filter(Customer.id == customer_id).first()
        if customer_id else None
    )

    # ── Seed from an originating invoice (After-Sale Service entry point) ──
    # Pre-fills the customer, carries the invoice number, and pre-loads the
    # invoice's physical parts (PRODUCT lines) as editable claim lines.
    selected_invoice = None
    seed_lines: list[dict] = []
    if invoice_id:
        selected_invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if selected_invoice is not None:
            if selected_customer is None:
                selected_customer = selected_invoice.customer
            for ln in selected_invoice.lines:
                if ln.line_type == LineType.PRODUCT and ln.product_id:
                    seed_lines.append(
                        {"productId": ln.product_id, "qty": ln.qty, "credit": ""}
                    )

    # Keep seeded products selectable even if since deactivated (the <select>
    # only lists active products otherwise, dropping the pre-filled option).
    if seed_lines:
        have = {p.id for p in products}
        missing = [s["productId"] for s in seed_lines if s["productId"] not in have]
        if missing:
            extra = db.query(Product).filter(Product.id.in_(missing)).all()
            products = sorted([*products, *extra], key=lambda p: (p.sku or ""))

    return templates.TemplateResponse(
        request,
        "warranty/_new_picker.html",
        {
            "customers": customers,
            "vendors": vendors,
            "products": products,
            "selected_customer": selected_customer,
            "selected_invoice": selected_invoice,
            "seed_lines": seed_lines,
        },
    )


@router.post("/new", response_class=RedirectResponse)
async def warranty_create(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.warranty_service import WarrantyService

    form = await request.form()

    customer_id_raw = str(form.get("customer_id", "")).strip()
    if not customer_id_raw:
        return RedirectResponse("/warranty/new", status_code=303)
    customer_id = int(customer_id_raw)

    failure_description = str(form.get("failure_description", "")).strip()
    notes = str(form.get("notes", "")).strip()

    # Optional invoice and vendor links
    inv_raw = str(form.get("invoice_number", "")).strip()
    invoice_id = None
    if inv_raw:
        inv = db.query(Invoice).filter(Invoice.invoice_number == inv_raw).first()
        if inv:
            invoice_id = inv.id

    vendor_id_raw = str(form.get("vendor_id", "")).strip()
    vendor_id = int(vendor_id_raw) if vendor_id_raw else None

    # Parse parallel line arrays
    product_ids = form.getlist("product_id[]")
    qty_claimeds = form.getlist("qty_claimed[]")
    credit_amounts = form.getlist("credit_amount[]")

    lines = []
    for i, pid in enumerate(product_ids):
        qty_raw = qty_claimeds[i] if i < len(qty_claimeds) else "1"
        amt_raw = credit_amounts[i] if i < len(credit_amounts) else "0"
        if not pid:
            continue
        lines.append({
            "product_id": int(pid),
            "qty_claimed": max(1, int(qty_raw)) if qty_raw else 1,
            "credit_amount": float(amt_raw) if amt_raw else 0.0,
        })

    if not lines:
        return RedirectResponse("/warranty/new", status_code=303)

    try:
        claim = WarrantyService(db, user_id).create_claim(
            customer_id=customer_id,
            invoice_id=invoice_id,
            vendor_id=vendor_id,
            failure_description=failure_description,
            lines=lines,
            notes=notes,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/warranty/new?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating warranty claim")
        return RedirectResponse(
            f"/warranty/new?error={url_quote('Unexpected error — claim was not created.')}",
            status_code=303,
        )
    return RedirectResponse(f"/warranty/{claim.id}", status_code=303)


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{claim_id}", response_class=HTMLResponse)
def warranty_detail(claim_id: int, request: Request, db: Session = Depends(get_db)):
    claim = db.query(WarrantyClaim).filter(WarrantyClaim.id == claim_id).first()
    if not claim:
        return RedirectResponse("/warranty/", status_code=303)
    return templates.TemplateResponse(
        request,
        "warranty/workspace.html",
        {
            "claim": claim,
            "WarrantyStatus": WarrantyStatus,
            "WarrantyDecision": WarrantyDecision,
            "WarrantyResolution": WarrantyResolution,
        },
    )


# ── Submit to Vendor ──────────────────────────────────────────────────────────

@router.post("/{claim_id}/submit", response_class=RedirectResponse)
def warranty_submit(
    claim_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.warranty_service import WarrantyService
    try:
        WarrantyService(db, user_id).submit_to_vendor(claim_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error submitting claim %s to vendor", claim_id)
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote('Unexpected error — claim was not submitted.')}",
            status_code=303,
        )
    return RedirectResponse(f"/warranty/{claim_id}", status_code=303)


# ── Vendor Decision ───────────────────────────────────────────────────────────

@router.post("/{claim_id}/vendor-decision", response_class=RedirectResponse)
async def warranty_vendor_decision(
    claim_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.warranty_service import WarrantyService

    form = await request.form()
    decision = str(form.get("decision", WarrantyDecision.APPROVED))
    decision_notes = str(form.get("decision_notes", "")).strip() or None

    claim = db.query(WarrantyClaim).filter(WarrantyClaim.id == claim_id).first()
    if not claim:
        return RedirectResponse("/warranty/", status_code=303)

    # Parse per-line resolutions
    line_resolutions = []
    for line in claim.claim_lines:
        approved_qty = int(form.get(f"line_{line.id}_approved_qty", 0) or 0)
        credit_amount = float(form.get(f"line_{line.id}_credit_amount", 0) or 0.0)
        resolution = str(form.get(f"line_{line.id}_resolution", "")).strip() or None
        line_resolutions.append({
            "claim_line_id": line.id,
            "approved_qty": approved_qty,
            "credit_amount": credit_amount,
            "resolution": resolution,
        })

    try:
        WarrantyService(db, user_id).record_vendor_decision(
            claim_id=claim_id,
            decision=decision,
            line_resolutions=line_resolutions,
            decision_notes=decision_notes,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error recording vendor decision for claim %s", claim_id)
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote('Unexpected error — decision was not recorded.')}",
            status_code=303,
        )
    return RedirectResponse(f"/warranty/{claim_id}", status_code=303)


# ── Credit Customer ───────────────────────────────────────────────────────────

@router.post("/{claim_id}/credit-customer", response_class=RedirectResponse)
def warranty_credit_customer(
    claim_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.warranty_service import WarrantyService
    try:
        WarrantyService(db, user_id).credit_customer(claim_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error crediting customer for claim %s", claim_id)
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote('Unexpected error — credit was not applied.')}",
            status_code=303,
        )
    return RedirectResponse(f"/warranty/{claim_id}", status_code=303)


# ── Notify Customer of Denial ─────────────────────────────────────────────────

@router.post("/{claim_id}/notify-denial", response_class=RedirectResponse)
async def warranty_notify_denial(
    claim_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.warranty_service import WarrantyService
    form = await request.form()
    notes = str(form.get("notes", "")).strip() or "Customer notified of vendor denial"
    try:
        WarrantyService(db, user_id).notify_customer_of_denial(claim_id, notes)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error notifying denial for claim %s", claim_id)
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote('Unexpected error — notification was not recorded.')}",
            status_code=303,
        )
    return RedirectResponse(f"/warranty/{claim_id}", status_code=303)


# ── Close Claim ───────────────────────────────────────────────────────────────

@router.post("/{claim_id}/close", response_class=RedirectResponse)
def warranty_close(
    claim_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.warranty_service import WarrantyService
    try:
        WarrantyService(db, user_id).close_claim(claim_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote(str(exc))}", status_code=303
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error closing claim %s", claim_id)
        return RedirectResponse(
            f"/warranty/{claim_id}?error={url_quote('Unexpected error — claim was not closed.')}",
            status_code=303,
        )
    return RedirectResponse(f"/warranty/{claim_id}", status_code=303)


# ── Print / PDF ───────────────────────────────────────────────────────────────

def _claim_print_context(claim: WarrantyClaim, db: Session) -> dict:
    company = get_company_dict(db)
    customer_addr_lines_ = customer_address_lines(claim.customer)
    vendor_addr_lines_ = vendor_address_lines(claim.vendor)

    invoice = None
    if claim.invoice_id:
        invoice = db.query(Invoice).filter(Invoice.id == claim.invoice_id).first()

    return {
        "claim": claim,
        "invoice": invoice,
        "company": company,
        "customer_addr_lines": customer_addr_lines_,
        "vendor_addr_lines": vendor_addr_lines_,
    }


@router.get("/{claim_id}/print", response_class=HTMLResponse)
def warranty_print(claim_id: int, request: Request, db: Session = Depends(get_db)):
    claim = db.query(WarrantyClaim).filter(WarrantyClaim.id == claim_id).first()
    if claim is None:
        return RedirectResponse("/warranty/", status_code=303)
    ctx = _claim_print_context(claim, db)
    return templates.TemplateResponse(request, "warranty/print.html", ctx)


@router.get("/{claim_id}/pdf")
def warranty_pdf(claim_id: int, request: Request, db: Session = Depends(get_db)):
    claim = db.query(WarrantyClaim).filter(WarrantyClaim.id == claim_id).first()
    if claim is None:
        return RedirectResponse("/warranty/", status_code=303)
    ctx = _claim_print_context(claim, db)
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="warranty/print.html",
        context=ctx,
        fallback_print_url=f"/warranty/{claim_id}/print",
        download_filename=claim.claim_number,
    )
