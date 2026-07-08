from __future__ import annotations

import csv
import html
import io
import json
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import (
    PaymentTerms, POStatus, VendorBillStatus, VendorContactRole,
    VendorCreditStatus, VendorProgramType,
)
from app.deps import get_db, get_current_user_id
from app.models.vendor import Vendor, VendorContact, VendorCredit, VendorProgram
from app.models.purchase_order import PurchaseOrder, VendorBill
from app.models.product import ProductVendorSource
from app.services.sku_service import next_free_vendor_digit, vendor_digit_locked

router = APIRouter(prefix="/vendors", tags=["vendors"])
templates = Jinja2Templates(directory="app/templates")

PAYMENT_TERMS = list(PaymentTerms)


def _vendor_duplicate_error(db: Session, name: str, vendor_code: str,
                            *, vendor_id: int | None = None) -> str:
    """Risk #2 — block a duplicate vendor before insert/update. Returns a clear
    error message, or '' when the (name, vendor_code) pair is usable.

      * name           — rejected if it case-insensitively matches another ACTIVE
                         vendor (mirrors the partial unique index WHERE is_active=1).
      * vendor_code    — rejected if a non-empty code exactly matches any other
                         vendor (mirrors the partial unique index WHERE code != '').

    `vendor_id`, when given, excludes the row being edited from the probe."""
    name = (name or "").strip()
    vendor_code = (vendor_code or "").strip()
    if name:
        clash_q = db.query(Vendor).filter(
            func.lower(Vendor.name) == name.lower(),
            Vendor.is_active == True,  # noqa: E712
        )
        if vendor_id is not None:
            clash_q = clash_q.filter(Vendor.id != vendor_id)
        clash = clash_q.first()
        if clash is not None:
            return (f"A vendor named \"{clash.name}\" already exists. "
                    f"Use the existing vendor or deactivate it first.")
    if vendor_code:
        clash_q = db.query(Vendor).filter(Vendor.vendor_code == vendor_code)
        if vendor_id is not None:
            clash_q = clash_q.filter(Vendor.id != vendor_id)
        clash = clash_q.first()
        if clash is not None:
            return (f"Vendor code \"{vendor_code}\" is already used by "
                    f"{clash.name} — vendor codes must be unique.")
    return ""


def _resolve_vendor_digit(db: Session, submitted: str, *, current: str = "",
                          vendor_id: int | None = None) -> tuple[str, str]:
    """R4 owner workflow — the vendor form takes the FEED CODE (PAI / IMB) and
    the SKU digit converts automatically: blank keeps the current digit or
    auto-assigns the next free one; an explicit digit must be a single 0-9 and
    unique across vendors (one digit per vendor, owner-locked SKU scheme).
    Returns (digit, error) — error is '' when the digit is usable."""
    submitted = (submitted or "").strip()
    if submitted and (len(submitted) != 1 or not submitted.isdigit()):
        return current, "SKU # must be a single digit 0-9."
    digit = submitted or (current or "").strip() or next_free_vendor_digit(db)
    if digit and digit != (current or "").strip():
        clash_q = db.query(Vendor).filter(Vendor.vendor_number == digit)
        if vendor_id:
            clash_q = clash_q.filter(Vendor.id != vendor_id)
        clash = clash_q.first()
        if clash is not None:
            return current, (f"SKU # {digit} is already assigned to "
                             f"{clash.name} — one digit per vendor.")
    return digit, ""


_OPEN_PO_STATUSES = [
    POStatus.VERBAL_ORDER, POStatus.DRAFT, POStatus.SENT, POStatus.PARTIAL,
]


def _filtered_vendors(db: Session, active_tab: str, q: str) -> list[Vendor]:
    """The list view's tab/q filter — shared with /export.csv so "export what
    I see" matches the table exactly."""
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
    return vendor_query.all()


def _vendor_aggregate_maps(db: Session, _vids: list[int]) -> dict:
    """§2B bulk aggregates (no N+1) — one grouped query per metric, keyed by
    vendor_id. The list template must read these maps, never per-row
    vendor.<relationship> traversals (each of those lazy-loads the vendor's
    full PO/credit history)."""
    open_po_map: dict = dict(
        db.query(PurchaseOrder.vendor_id, func.count(PurchaseOrder.id))
        .filter(PurchaseOrder.vendor_id.in_(_vids), PurchaseOrder.status.in_(_OPEN_PO_STATUSES))
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

    return {
        "open_po_map":   open_po_map,
        "open_bill_map": open_bill_map,
        "credit_map":    credit_map,
        "last_po_map":   last_po_map,
        "lead_time_map": lead_time_map,
    }


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

    vendors = _filtered_vendors(db, active_tab, q)
    maps = _vendor_aggregate_maps(db, [v.id for v in vendors])

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
            **maps,
        },
    )


# ── Export CSV — multi-segment-safe: registered before /{vendor_id} ──────────

@router.get("/export.csv")
def vendor_export_csv(
    tab: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    """
    Stream the current filtered vendor list as a CSV download.
    Mirrors the list view's tab/q filters so "export what I see" matches
    exactly (same pattern as /customers/export.csv), including the list's
    §2B aggregate columns.
    """
    from fastapi.responses import StreamingResponse

    active_tab = tab if tab in ("active", "inactive", "all") else "active"
    vendors = _filtered_vendors(db, active_tab, q)
    maps = _vendor_aggregate_maps(db, [v.id for v in vendors])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "name", "vendor_code", "sku_digit", "account_number", "contact_name",
        "phone", "email", "website", "payment_terms",
        "open_pos", "open_bills", "open_credits", "open_credit_amount",
        "last_po_date", "avg_lead_days", "is_active",
    ])
    for v in vendors:
        credit = maps["credit_map"].get(v.id)
        last_po = maps["last_po_map"].get(v.id)
        lead = maps["lead_time_map"].get(v.id)
        writer.writerow([
            v.name,
            v.vendor_code,
            v.vendor_number,
            v.account_number,
            v.contact_name,
            v.phone,
            v.email,
            v.website,
            v.payment_terms,
            maps["open_po_map"].get(v.id, 0),
            maps["open_bill_map"].get(v.id, 0),
            credit["count"] if credit else 0,
            f"{credit['amount']:.2f}" if credit else "",
            last_po.strftime("%Y-%m-%d") if last_po else "",
            lead if lead is not None else "",
            "yes" if v.is_active else "no",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vendors.csv"},
    )


@router.get("/new", response_class=HTMLResponse)
def vendor_new(request: Request):
    return templates.TemplateResponse(
        request,
        "vendors/new.html",
        {"payment_terms": PAYMENT_TERMS},
    )


@router.post("/new", response_class=RedirectResponse)
async def vendor_create(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    vendor_code = str(form.get("vendor_code", "")).strip().upper()[:4]
    # Risk #2 — reject a duplicate active name / non-empty vendor_code before insert.
    derr = _vendor_duplicate_error(db, name, vendor_code)
    if not derr:
        # R4 — feed code → SKU digit conversion: blank digit auto-assigns the
        # next free one; explicit digits are validated + uniqueness-checked.
        digit, derr = _resolve_vendor_digit(db, str(form.get("vendor_number", "")))
    if derr:
        return templates.TemplateResponse(
            request, "vendors/new.html",
            {"payment_terms": PAYMENT_TERMS, "error": derr,
             "form_data": {k: str(v) for k, v in form.items()}},
            status_code=422)
    v = Vendor(
        name=name,
        vendor_code=vendor_code,
        vendor_number=digit,
        private_label=str(form.get("private_label", "")).strip() in ("1", "on", "true"),
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
async def vendor_quick_create(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    vendor_code = str(form.get("vendor_code", "")).strip().upper()[:4]  # SKU contract: 4-char max, matches full create/update
    if not name or not vendor_code:
        return HTMLResponse(
            '<p class="text-sm text-red-600 font-medium px-5 py-3">Vendor name and code are required.</p>',
            status_code=422,
        )
    # Risk #2 — reject a duplicate active name / non-empty vendor_code before insert.
    derr = _vendor_duplicate_error(db, name, vendor_code)
    if derr:
        return HTMLResponse(
            f'<p class="text-sm text-red-600 font-medium px-5 py-3">{html.escape(derr)}</p>',
            status_code=422,
        )
    v = Vendor(
        name=name,
        vendor_code=vendor_code,
        # R4 — quick-created vendors are import-ready immediately: the SKU
        # digit auto-assigns from the next free one (changeable on the vendor
        # page until the first SKU is minted under it).
        vendor_number=next_free_vendor_digit(db),
        private_label=str(form.get("private_label", "")).strip() in ("1", "on", "true"),
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
            # R4 — SKU digit frozen once products have minted under it
            "digit_locked": vendor_digit_locked(db, v.id),
            # Discount Programs card — type options for the add/edit form
            "program_types": list(VendorProgramType),
        },
    )


@router.post("/{vendor_id}", response_class=RedirectResponse)
async def vendor_update(
    vendor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        return RedirectResponse("/vendors/", status_code=303)
    form = await request.form()
    # Risk #2 — reject an edit that would duplicate another ACTIVE vendor's name
    # or another vendor's non-empty code (mirrors the DB unique indexes, which
    # would otherwise 500 on commit). Excludes this row from the probe.
    _new_name = str(form.get("name", "")).strip()
    _new_code = str(form.get("vendor_code", "")).strip().upper()[:4]
    _dup_err = _vendor_duplicate_error(db, _new_name, _new_code, vendor_id=v.id)
    if _dup_err:
        db.rollback()
        return RedirectResponse(
            f"/vendors/{vendor_id}?error={url_quote(_dup_err)}", status_code=303)
    # R4 — the digit is FROZEN once SKUs have been minted under it (changing
    # it would split the vendor's SKU namespace); until then it behaves like
    # create: blank keeps/auto-assigns, explicit digits validate + dedupe.
    if vendor_digit_locked(db, v.id):
        digit = v.vendor_number  # submitted value ignored — field is read-only
    else:
        digit, derr = _resolve_vendor_digit(
            db, str(form.get("vendor_number", "")),
            current=v.vendor_number or "", vendor_id=v.id)
        if derr:
            db.rollback()
            return RedirectResponse(
                f"/vendors/{vendor_id}?error={url_quote(derr)}", status_code=303)
    v.name = str(form.get("name", "")).strip()
    v.vendor_code = str(form.get("vendor_code", "")).strip().upper()[:4]
    v.vendor_number = digit
    v.private_label = str(form.get("private_label", "")).strip() in ("1", "on", "true")
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
def vendor_deactivate(
    vendor_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if v:
        v.is_active = False
        db.commit()
    return RedirectResponse("/vendors/", status_code=303)


@router.post("/{vendor_id}/reactivate", response_class=RedirectResponse)
def vendor_reactivate(
    vendor_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
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
    user_id: int = Depends(get_current_user_id),
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
    user_id: int = Depends(get_current_user_id),
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
def vendor_contact_make_primary(
    vendor_id: int,
    contact_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
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
def vendor_contact_delete(
    vendor_id: int,
    contact_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
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


# ── Vendor Discount Programs (volume/tier discounts + rebates) ────────────────
# Reuses the existing vendor_programs table. The PO workspace reads the active
# program whose threshold the PO's parts subtotal meets and offers an "Apply
# X% discount" button (POService.eligible_volume_program). Plain form-POSTs
# that 303-redirect to ?saved#programs (no HTMX partial); the detail route
# already passes `vendor` (with `.programs`) to the template.

def _normalize_program_type(ptype: str) -> str:
    return ptype if ptype in {t.value for t in VendorProgramType} else VendorProgramType.VOLUME_REBATE


def _opt_float(raw: str) -> float | None:
    raw = (raw or "").strip().replace(",", "").lstrip("$")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


@router.post("/{vendor_id}/programs", response_class=RedirectResponse)
def vendor_program_create(
    vendor_id: int,
    program_name: str = Form(...),
    program_type: str = Form(VendorProgramType.VOLUME_REBATE),
    threshold_amount: str = Form(""),
    discount_percent: str = Form(""),
    notes: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Add a discount program. A new program defaults to active."""
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if vendor is None:
        return RedirectResponse("/vendors/", status_code=303)
    program_name = program_name.strip()
    if not program_name:
        return RedirectResponse(
            f"/vendors/{vendor_id}?error={url_quote('Program name is required.')}#programs",
            status_code=303,
        )
    db.add(VendorProgram(
        vendor_id=vendor_id,
        program_name=program_name,
        program_type=_normalize_program_type(program_type),
        threshold_amount=_opt_float(threshold_amount),
        discount_percent=_opt_float(discount_percent),
        notes=notes.strip(),
        is_active=True,  # new programs start active
    ))
    db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1#programs", status_code=303)


@router.post("/{vendor_id}/programs/{program_id}", response_class=RedirectResponse)
def vendor_program_update(
    vendor_id: int,
    program_id: int,
    program_name: str = Form(...),
    program_type: str = Form(VendorProgramType.VOLUME_REBATE),
    threshold_amount: str = Form(""),
    discount_percent: str = Form(""),
    notes: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Edit a program's fields. The is_active checkbox doubles as enable/disable."""
    program = (
        db.query(VendorProgram)
        .filter(VendorProgram.id == program_id, VendorProgram.vendor_id == vendor_id)
        .first()
    )
    if program is None:
        return RedirectResponse(f"/vendors/{vendor_id}", status_code=303)
    program_name = program_name.strip()
    if program_name:
        program.program_name = program_name
    program.program_type = _normalize_program_type(program_type)
    program.threshold_amount = _opt_float(threshold_amount)
    program.discount_percent = _opt_float(discount_percent)
    program.notes = notes.strip()
    program.is_active = bool(is_active)
    db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1#programs", status_code=303)


@router.post("/{vendor_id}/programs/{program_id}/delete", response_class=RedirectResponse)
def vendor_program_delete(
    vendor_id: int,
    program_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Hard-delete a program (config row — no history to preserve)."""
    program = (
        db.query(VendorProgram)
        .filter(VendorProgram.id == program_id, VendorProgram.vendor_id == vendor_id)
        .first()
    )
    if program is not None:
        db.delete(program)
        db.commit()
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1#programs", status_code=303)
