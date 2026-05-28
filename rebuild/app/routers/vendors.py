from __future__ import annotations

import html
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import PaymentTerms
from app.deps import get_db
from app.models.vendor import Vendor
from app.models.purchase_order import PurchaseOrder
from app.models.product import ProductVendorSource

router = APIRouter(prefix="/vendors", tags=["vendors"])
templates = Jinja2Templates(directory="app/templates")

PAYMENT_TERMS = list(PaymentTerms)


@router.get("/", response_class=HTMLResponse)
def vendor_list(request: Request, db: Session = Depends(get_db)):
    vendors = db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()
    return templates.TemplateResponse(
        "vendors/list.html",
        {"request": request, "vendors": vendors},
    )


@router.get("/new", response_class=HTMLResponse)
def vendor_new(request: Request):
    return templates.TemplateResponse(
        "vendors/new.html",
        {"request": request, "payment_terms": PAYMENT_TERMS},
    )


@router.post("/new", response_class=RedirectResponse)
async def vendor_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    vendor_code = str(form.get("vendor_code", "")).strip().upper()[:4]
    v = Vendor(
        name=str(form.get("name", "")).strip(),
        vendor_code=vendor_code,
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
    return templates.TemplateResponse("vendors/_quick_create.html", {"request": request})


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
        "vendors/detail.html",
        {
            "request": request,
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
