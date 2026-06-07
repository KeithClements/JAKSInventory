from __future__ import annotations

import html
import json
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import PaymentTerms, POStatus, VendorBillStatus, VendorContactRole, VendorCreditStatus
from app.deps import get_db
from app.models.vendor import Vendor, VendorContact, VendorCredit
from app.models.purchase_order import PurchaseOrder, VendorBill
from app.models.product import ProductVendorSource

router = APIRouter(prefix="/vendors", tags=["vendors"])
templates = Jinja2Templates(directory="app/templates")

PAYMENT_TERMS = list(PaymentTerms)


@router.get("/", response_class=HTMLResponse)
def vendor_list(
    request: Request,
    tab: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    # ── Unfiltered tab counts ─────────────────────────────────────────────
    _v_active   = db.query(func.count(Vendor.id)).filter(Vendor.is_active == True).scalar()  or 0
    _v_inactive = db.query(func.count(Vendor.id)).filter(Vendor.is_active == False).scalar() or 0
    counts: dict = {
        "":         _v_active,
        "active":   _v_active,
        "inactive": _v_inactive,
        "all":      _v_active + _v_inactive,
    }

    # `tab` drives the active filter; default shows active vendors.
    active_tab = tab if tab in ("active", "inactive", "all") else "active"

    vendor_query = db.query(Vendor).order_by(Vendor.name)
    if active_tab == "inactive":
        vendor_query = vendor_query.filter(Vendor.is_active == False)
    elif active_tab == "all":
        pass
    else:
        vendor_query = vendor_query.filter(Vendor.is_active == True)
    if q:
        vendor_query = vendor_query.filter(
            Vendor.name.ilike(f"%{q}%") | Vendor.vendor_code.ilike(f"%{q}%")
        )
    vendors = vendor_query.all()

    # ── Bulk §2B aggregates (no N+1) ─────────────────────────────────────
    _vids = [v.id for v in vendors]

    OPEN_PO_STATUSES = [
        POStatus.VERBAL_ORDER, POStatus.DRAFT, POStatus.SENT, POStatus.PARTIAL,
    ]
    open_po_map: dict = dict(
        db.query(PurchaseOrder.vendor_id, func.count(PurchaseOrder.id))
        .filter(PurchaseOrder.vendor_id.in_(_vids), PurchaseOrder.status.in_(OPEN_PO_STATUSES))
        .group_by(PurchaseOrder.vendor_id)
        .all()
    ) if _vids else {}

    open_bill_map: dict = dict(
        db.query(PurchaseOrder.vendor_id, func.count(VendorBill.id))
        .join(VendorBill, VendorBill.po_id == PurchaseOrder.id)
        .filter(
            PurchaseOrder.vendor_id.in_(_vids),
            VendorBill.status.in_([VendorBillStatus.PENDING, VendorBillStatus.DISCREPANCY]),
        )
        .group_by(PurchaseOrder.vendor_id)
        .all()
    ) if _vids else {}

    # credit_map: {vendor_id: {"count": int, "amount": float}}
    _credit_rows = (
        db.query(
            VendorCredit.vendor_id,
            func.count(VendorCredit.id),
            func.sum(VendorCredit.amount),
        )
        .filter(
            VendorCredit.vendor_id.in_(_vids),
            VendorCredit.status == VendorCreditStatus.OPEN,
        )
        .group_by(VendorCredit.vendor_id)
        .all()
    ) if _vids else []
    credit_map: dict = {
        vid: {"count": cnt, "amount": round(amt or 0.0, 2)}
        for vid, cnt, amt in _credit_rows
    }

    last_po_map: dict = dict(
        db.query(PurchaseOrder.vendor_id, func.max(PurchaseOrder.created_at))
        .filter(PurchaseOrder.vendor_id.in_(_vids))
        .group_by(PurchaseOrder.vendor_id)
        .all()
    ) if _vids else {}

    lead_time_map: dict = {
        vid: round(avg_days)
        for vid, avg_days in (
            db.query(
                ProductVendorSource.vendor_id,
                func.avg(ProductVendorSource.lead_time_days),
            )
            .filter(
                ProductVendorSource.vendor_id.in_(_vids),
                ProductVendorSource.lead_time_days.isnot(None),
                ProductVendorSource.is_active == True,  # noqa: E712
            )
            .group_by(ProductVendorSource.vendor_id)
            .all()
        )
        if avg_days is not None
    } if _vids else {}

    return templates.TemplateResponse(
        request,
        "vendors/list.html",
        {
            "request":      request,
            "vendors":      vendors,
            "active_tab":   active_tab,
            "counts":       counts,
            "q":            q,
            # §2B aggregate maps (keyed by vendor_id)
            "open_po_map":   open_po_map,
            "open_bill_map": open_bill_map,
            "credit_map":    credit_map,
            "last_po_map":   last_po_map,
            "lead_time_map": lead_time_map,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def vendor_new(request: Request):
    return templates.TemplateResponse(
        request,
        "vendors/new.html",
        {"payment_terms": PAYMENT_TERMS},
    )


@router.post("/new", response_class=RedirectResponse)
async def vendor_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    vendor_code = str(form.get("vendor_code", "")).strip().upper()[:4]
    v = Vendor(
        name=str(form.get("name", "")).strip(),
        vendor_code=vendor_code,
        vendor_number=str(form.get("vendor_number", "")).strip()[:2],  # 1-digit SKU vendor #
        account_number=str(form.get("account_number", "")).strip(),
        contact_name=str(form.get("contact_name", "")).strip(),
        phone=str(form.get("phone", "")).strip(),
        email=str(form.get("email", "")).strip(),
        website=str(form.get("website", "")).strip(),
        payment_terms=str(form.get("payment_terms", PaymentTerms.NET_30)),
        notes=str(form.get("notes", "")).strip(),
    )
    db.add(v)
    db.commit()
    return RedirectResponse(f"/vendors/{v.id}", status_code=303)


# ── Quick Create (slide-over — called from product/PO vendor field [+]) ───────

@router.get("/quick-create-form", response_class=HTMLResponse)
def vendor_quick_create_form(request: Request):
    return templates.TemplateResponse(request, "vendors/_quick_create.html")


@router.post("/quick-create", response_class=HTMLResponse)
async def vendor_quick_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    vendor_code = str(form.get("vendor_code", "")).strip().upper()[:10]
    if not name or not vendor_code:
        return HTMLResponse(
            '<p class="text-sm text-red-600 font-medium px-5 py-3">Vendor name and code are required.</p>',
            status_code=422,
        )
    v = Vendor(
        name=name,
        vendor_code=vendor_code,
        phone=str(form.get("phone", "")).strip(),
        account_number=str(form.get("account_number", "")).strip(),
    )
    db.add(v)
    db.commit()
    _detail = html.escape(json.dumps({"type": "vendor", "id": v.id, "label": v.name}))
    _name   = html.escape(v.name)
    return HTMLResponse(
        f"""<span></span>
<div id="toast-container" hx-swap-oob="beforeend">
  <div x-data x-init="
      setTimeout(() => $el.remove(), 4000);
      window.dispatchEvent(new CustomEvent('record-created', {{ detail: {_detail} }}));
    "
    class="toast toast-success">
    <svg class="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
      <path fill-rule="evenodd"
            d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
            clip-rule="evenodd"/>
    </svg>
    Vendor created: {_name}
  </div>
</div>"""
    )


# ── Preview panel — MUST stay registered before /{vendor_id} ─────────────────

@router.get("/preview/{vendor_id}", response_class=HTMLResponse)
def vendor_preview_panel(vendor_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Bottom-dock preview partial for the Vendor List (§7 Primitive 5).
    Loaded via htmx.ajax() on row click; returns vendors/_preview_panel.html.

    Context published to UI lane:
      vendor         — Vendor ORM object (with .contacts, .purchase_orders)
      open_po_count  — int
      open_bill_count— int
      credit_count   — int
      credit_amount  — float
      last_po_date   — datetime | None
      avg_lead_days  — int | None
      POStatus       — enum class
    """
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if v is None:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-red-500">Vendor not found.</p>',
            status_code=404,
        )

    OPEN_PO_STATUSES = [
        POStatus.VERBAL_ORDER, POStatus.DRAFT, POStatus.SENT, POStatus.PARTIAL,
    ]
    open_po_count: int = (
        db.query(func.count(PurchaseOrder.id))
        .filter(PurchaseOrder.vendor_id == vendor_id, PurchaseOrder.status.in_(OPEN_PO_STATUSES))
        .scalar() or 0
    )
    open_bill_count: int = (
        db.query(func.count(VendorBill.id))
        .join(PurchaseOrder, VendorBill.po_id == PurchaseOrder.id)
        .filter(
            PurchaseOrder.vendor_id == vendor_id,
            VendorBill.status.in_([VendorBillStatus.PENDING, VendorBillStatus.DISCREPANCY]),
        )
        .scalar() or 0
    )
    _credit = (
        db.query(func.count(VendorCredit.id), func.sum(VendorCredit.amount))
        .filter(VendorCredit.vendor_id == vendor_id, VendorCredit.status == VendorCreditStatus.OPEN)
        .first()
    )
    credit_count: int   = _credit[0] or 0
    credit_amount: float = round(_credit[1] or 0.0, 2)

    last_po_date = (
        db.query(func.max(PurchaseOrder.created_at))
        .filter(PurchaseOrder.vendor_id == vendor_id)
        .scalar()
    )
    avg_lead = (
        db.query(func.avg(ProductVendorSource.lead_time_days))
        .filter(
            ProductVendorSource.vendor_id == vendor_id,
            ProductVendorSource.lead_time_days.isnot(None),
            ProductVendorSource.is_active == True,  # noqa: E712
        )
        .scalar()
    )
    avg_lead_days: int | None = round(avg_lead) if avg_lead is not None else None

    return templates.TemplateResponse(
        request,
        "vendors/_preview_panel.html",
        {
            "request":        request,
            "vendor":         v,
            "open_po_count":  open_po_count,
            "open_bill_count":open_bill_count,
            "credit_count":   credit_count,
            "credit_amount":  credit_amount,
            "last_po_date":   last_po_date,
            "avg_lead_days":  avg_lead_days,
            "POStatus":       POStatus,
        },
    )


@router.get("/{vendor_id}", response_class=HTMLResponse)
def vendor_detail(vendor_id: int, request: Request, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        return RedirectResponse("/vendors/", status_code=303)

    recent_pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.vendor_id == vendor_id)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(10)
        .all()
    )

    product_count = (
        db.query(ProductVendorSource)
        .filter(
            ProductVendorSource.vendor_id == vendor_id,
            ProductVendorSource.is_active == True,
        )
        .count()
    )

    return templates.TemplateResponse(
        request,
        "vendors/detail.html",
        {
            "vendor": v,
            "recent_pos": recent_pos,
            "product_count": product_count,
            "payment_terms": PAYMENT_TERMS,
        },
    )


@router.post("/{vendor_id}", response_class=RedirectResponse)
async def vendor_update(vendor_id: int, request: Request, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        return RedirectResponse("/vendors/", status_code=303)
    form = await request.form()
    v.name = str(form.get("name", "")).strip()
    v.vendor_code = str(form.get("vendor_code", "")).strip().upper()[:4]
    v.vendor_number = str(form.get("vendor_number", "")).strip()[:2]  # 1-digit SKU vendor #
    v.account_number = str(form.get("account_number", "")).strip()
    v.contact_name = str(form.get("contact_name", "")).strip()
    v.phone = str(form.get("phone", "")).strip()
    v.email = str(form.get("email", "")).strip()
    v.website = str(form.get("website", "")).strip()
    v.payment_terms = str(form.get("payment_terms", PaymentTerms.NET_30))
    v.notes = str(form.get("notes", "")).strip()
    v.internal_notes = str(form.get("internal_notes", "")).strip()
    db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1", status_code=303)


@router.post("/{vendor_id}/deactivate", response_class=RedirectResponse)
def vendor_deactivate(vendor_id: int, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if v:
        v.is_active = False
        db.commit()
    return RedirectResponse("/vendors/", status_code=303)


@router.post("/{vendor_id}/reactivate", response_class=RedirectResponse)
def vendor_reactivate(vendor_id: int, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if v:
        v.is_active = True
        db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}", status_code=303)


# ── Vendor Contacts (O4 — CRUD for the Contacts card on vendors/detail.html) ──
# Contract: VENDOR_CONTACTS_CONTRACT.md.  The vendor detail route already passes
# `vendor` (with `.contacts` and `.primary_contact`) to the template; these routes
# mutate that collection and 303-redirect back to the detail page, so the UI lane
# only needs <form> posts — Backend owns no contacts partial.

def _normalize_contact_role(role: str) -> str:
    return role if role in {r.value for r in VendorContactRole} else VendorContactRole.GENERAL


def _clear_other_primary_contacts(db: Session, vendor_id: int, keep_id: int | None = None) -> None:
    """Enforce at most one primary contact per vendor."""
    q = db.query(VendorContact).filter(
        VendorContact.vendor_id == vendor_id,
        VendorContact.is_primary == True,  # noqa: E712
    )
    if keep_id is not None:
        q = q.filter(VendorContact.id != keep_id)
    for other in q.all():
        other.is_primary = False


@router.post("/{vendor_id}/contacts", response_class=RedirectResponse)
def vendor_contact_create(
    vendor_id: int,
    name: str = Form(...),
    role: str = Form(VendorContactRole.GENERAL),
    phone: str = Form(""),
    email: str = Form(""),
    is_primary: bool = Form(False),
    is_sales_contact: bool = Form(False),
    is_warranty_contact: bool = Form(False),
    is_returns_contact: bool = Form(False),
    is_accounting_contact: bool = Form(False),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """Add a contact.  The first contact (or is_primary=on) becomes primary."""
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if vendor is None:
        return RedirectResponse("/vendors/", status_code=303)

    name = name.strip()
    if not name:
        return RedirectResponse(
            f"/vendors/{vendor_id}?error={url_quote('Contact name is required.')}#contacts",
            status_code=303,
        )

    active_count = (
        db.query(VendorContact)
        .filter(VendorContact.vendor_id == vendor_id, VendorContact.is_active == True)  # noqa: E712
        .count()
    )
    make_primary = bool(is_primary) or active_count == 0
    if make_primary:
        _clear_other_primary_contacts(db, vendor_id)

    db.add(VendorContact(
        vendor_id=vendor_id,
        name=name,
        role=_normalize_contact_role(role),
        phone=phone.strip() or None,
        email=email.strip() or None,
        is_primary=make_primary,
        is_sales_contact=bool(is_sales_contact),
        is_warranty_contact=bool(is_warranty_contact),
        is_returns_contact=bool(is_returns_contact),
        is_accounting_contact=bool(is_accounting_contact),
        notes=notes.strip(),
    ))
    db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1#contacts", status_code=303)


@router.post("/{vendor_id}/contacts/{contact_id}", response_class=RedirectResponse)
def vendor_contact_update(
    vendor_id: int,
    contact_id: int,
    name: str = Form(...),
    role: str = Form(VendorContactRole.GENERAL),
    phone: str = Form(""),
    email: str = Form(""),
    is_sales_contact: bool = Form(False),
    is_warranty_contact: bool = Form(False),
    is_returns_contact: bool = Form(False),
    is_accounting_contact: bool = Form(False),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """Edit a contact's fields.  Primary status is managed via /make-primary, not here."""
    contact = (
        db.query(VendorContact)
        .filter(VendorContact.id == contact_id, VendorContact.vendor_id == vendor_id)
        .first()
    )
    if contact is None:
        return RedirectResponse(f"/vendors/{vendor_id}", status_code=303)

    name = name.strip()
    if name:
        contact.name = name
    contact.role = _normalize_contact_role(role)
    contact.phone = phone.strip() or None
    contact.email = email.strip() or None
    contact.is_sales_contact = bool(is_sales_contact)
    contact.is_warranty_contact = bool(is_warranty_contact)
    contact.is_returns_contact = bool(is_returns_contact)
    contact.is_accounting_contact = bool(is_accounting_contact)
    contact.notes = notes.strip()
    db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1#contacts", status_code=303)


@router.post("/{vendor_id}/contacts/{contact_id}/make-primary", response_class=RedirectResponse)
def vendor_contact_make_primary(vendor_id: int, contact_id: int, db: Session = Depends(get_db)):
    """Mark one contact primary; clears the flag on every other contact for this vendor."""
    contact = (
        db.query(VendorContact)
        .filter(VendorContact.id == contact_id, VendorContact.vendor_id == vendor_id)
        .first()
    )
    if contact is None:
        return RedirectResponse(f"/vendors/{vendor_id}", status_code=303)
    _clear_other_primary_contacts(db, vendor_id, keep_id=contact.id)
    contact.is_primary = True
    contact.is_active = True
    db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1#contacts", status_code=303)


@router.post("/{vendor_id}/contacts/{contact_id}/delete", response_class=RedirectResponse)
def vendor_contact_delete(vendor_id: int, contact_id: int, db: Session = Depends(get_db)):
    """Soft-delete a contact (is_active=False).  If it was primary, the vendor is left
    without a primary until another is promoted via /make-primary."""
    contact = (
        db.query(VendorContact)
        .filter(VendorContact.id == contact_id, VendorContact.vendor_id == vendor_id)
        .first()
    )
    if contact is None:
        return RedirectResponse(f"/vendors/{vendor_id}", status_code=303)
    contact.is_active = False
    contact.is_primary = False
    db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1#contacts", status_code=303)
