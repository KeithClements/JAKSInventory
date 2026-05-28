from __future__ import annotations

import html
import io
import csv
import json
import logging

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import (
    AddressType, CallOutcome, CallType,
    CommunicationChannel, CommunicationDirection,
    PaymentTerms, PricingTier, QuoteStatus,
)
from app.deps import get_db
from app.models.communication import Communication
from app.models.customer import Customer, CustomerAddress, CustomerCallLog
from app.models.invoice import Invoice
from app.models.quote import Quote
from app.services.crm_service import CRMService
from app.services.messaging_service import MessagingService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["customers"])
templates = Jinja2Templates(directory="app/templates")

CURRENT_USER_ID = 1


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def customer_list(request: Request, q: str = "", db: Session = Depends(get_db)):
    query = db.query(Customer).filter(Customer.is_active == True)
    if q:
        like = f"%{q}%"
        query = query.filter(
            Customer.company_name.ilike(like)
            | Customer.contact_name.ilike(like)
            | Customer.phone.ilike(like)
        )
    customers = query.order_by(Customer.company_name).all()

    # Bulk activity counts — two queries, no N+1
    customer_ids = [c.id for c in customers]
    if customer_ids:
        open_invoice_counts = dict(
            db.query(Invoice.customer_id, func.count(Invoice.id))
            .filter(
                Invoice.customer_id.in_(customer_ids),
                Invoice.status.in_(["draft", "open", "partial"]),
            )
            .group_by(Invoice.customer_id)
            .all()
        )
        open_quote_counts = dict(
            db.query(Quote.customer_id, func.count(Quote.id))
            .filter(
                Quote.customer_id.in_(customer_ids),
                Quote.status.notin_([
                    QuoteStatus.CONVERTED,
                    QuoteStatus.DECLINED,
                    QuoteStatus.EXPIRED,
                ]),
            )
            .group_by(Quote.customer_id)
            .all()
        )
    else:
        open_invoice_counts = {}
        open_quote_counts = {}

    # Bulk last-sale date — one query, no N+1. Returns formatted "Mon DD" strings.
    if customer_ids:
        _raw_dates = dict(
            db.query(Invoice.customer_id, func.max(Invoice.created_at))
            .filter(
                Invoice.customer_id.in_(customer_ids),
                Invoice.status != "void",
            )
            .group_by(Invoice.customer_id)
            .all()
        )
        last_sale_dates: dict[int, str] = {}
        for _cid, _val in _raw_dates.items():
            if _val is None:
                continue
            if hasattr(_val, "strftime"):
                last_sale_dates[_cid] = _val.strftime("%b %d")
            else:
                last_sale_dates[_cid] = str(_val)[:10]
    else:
        last_sale_dates = {}

    return templates.TemplateResponse(
        "customers/list.html",
        {
            "request": request,
            "customers": customers,
            "q": q,
            "open_invoice_counts": open_invoice_counts,
            "open_quote_counts": open_quote_counts,
            "last_sale_dates": last_sale_dates,
        },
    )


# ── New ───────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def customer_new(request: Request):
    return templates.TemplateResponse(
        "customers/new.html",
        {"request": request, "payment_terms": list(PaymentTerms)},
    )


@router.post("/new", response_class=RedirectResponse)
async def customer_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    c = Customer(
        company_name=str(form.get("company_name", "")).strip(),
        contact_name=str(form.get("contact_name", "")).strip(),
        phone=str(form.get("phone", "")).strip(),
        email=str(form.get("email", "")).strip(),
        address_line1=str(form.get("address_line1", "")).strip(),
        address_line2=str(form.get("address_line2", "")).strip(),
        city=str(form.get("city", "")).strip(),
        state=str(form.get("state", "")).strip(),
        zip_code=str(form.get("zip_code", "")).strip(),
        payment_terms=str(form.get("payment_terms", PaymentTerms.COD)),
        pricing_tier=str(form.get("pricing_tier", "standard")),
        credit_limit=float(form.get("credit_limit") or 0),
        discount_pct=float(form.get("discount_pct") or 0),
        interest_rate=float(form.get("interest_rate") or 0),
        is_tax_exempt=bool(form.get("is_tax_exempt")),
        tax_exempt_cert_number=str(form.get("tax_exempt_cert_number", "")).strip() or None,
        notes=str(form.get("notes", "")).strip(),
        internal_notes=str(form.get("internal_notes", "")).strip(),
    )
    db.add(c)
    db.commit()
    return RedirectResponse(f"/customers/{c.id}", status_code=303)


# ── Quick Create (slide-over — called from quote/invoice customer field [+]) ──

@router.get("/quick-create-form", response_class=HTMLResponse)
def customer_quick_create_form(request: Request):
    return templates.TemplateResponse("customers/_quick_create.html", {"request": request})


@router.post("/quick-create", response_class=HTMLResponse)
async def customer_quick_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    company_name = str(form.get("company_name", "")).strip()
    if not company_name:
        return HTMLResponse(
            '<p class="text-sm text-red-600 font-medium px-5 py-3">Company name is required.</p>',
            status_code=422,
        )

    _action = str(form.get("_action", "save"))

    # Checkboxes: absent = unchecked, "on" = checked
    is_tax_exempt = form.get("is_tax_exempt") is not None
    ship_same     = form.get("ship_same") is not None

    c = Customer(
        company_name=company_name,
        contact_name=str(form.get("contact_name", "")).strip(),
        phone=str(form.get("phone", "")).strip(),
        email=str(form.get("email", "")).strip(),
        payment_terms=str(form.get("payment_terms", PaymentTerms.NET_30)),
        pricing_tier=str(form.get("pricing_tier", "standard")),
        credit_limit=float(form.get("credit_limit") or 0),
        discount_pct=float(form.get("discount_pct") or 0),
        is_tax_exempt=is_tax_exempt,
        tax_exempt_cert_number=str(form.get("tax_exempt_cert_number", "")).strip() or None,
        address_line1=str(form.get("address_line1", "")).strip(),
        address_line2=str(form.get("address_line2", "")).strip(),
        city=str(form.get("city", "")).strip(),
        state=str(form.get("state", "")).strip(),
        zip_code=str(form.get("zip_code", "")).strip(),
        notes=str(form.get("notes", "")).strip(),
    )
    db.add(c)
    db.flush()  # populate c.id before creating child records

    # Create a separate shipping address when the user unchecked "Same as billing"
    if not ship_same:
        ship_street = str(form.get("ship_address_line1", "")).strip()
        if ship_street:  # only if user actually filled something in
            ship_addr = CustomerAddress(
                customer_id=c.id,
                address_type=AddressType.SHIPPING,
                is_default_shipping=True,
                street=ship_street,
                street_line2=str(form.get("ship_address_line2", "")).strip(),
                city=str(form.get("ship_city", "")).strip(),
                state=str(form.get("ship_state", "")).strip(),
                zip_code=str(form.get("ship_zip_code", "")).strip(),
            )
            db.add(ship_addr)

    db.commit()

    # ── Save & New: return fresh form with in-panel success flash ──────────
    if _action == "save_new":
        return templates.TemplateResponse(
            "customers/_quick_create.html",
            {"request": request, "success_flash": f"✓ {company_name} saved."},
        )

    # ── Save & Quote: navigate to customer detail (New Quote button is there) ──
    if _action == "save_quote":
        return HTMLResponse(
            "<span></span>",
            headers={"HX-Redirect": f"/customers/{c.id}"},
        )

    # ── Default (Save Customer): fire record-created + show toast ──────────
    _detail = html.escape(json.dumps({"type": "customer", "id": c.id, "label": c.company_name}))
    _name   = html.escape(c.company_name)
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
    Customer created: {_name}
  </div>
</div>"""
    )


# ── Global typeahead search (used by Log Call slide-over in base.html) ────────

@router.get("/search-json", response_class=HTMLResponse)
def customer_search_partial(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Returns an HTML partial — list of customers matching q for typeahead dropdowns."""
    customers: list[Customer] = []
    if q and len(q) >= 2:
        like = f"%{q}%"
        customers = (
            db.query(Customer)
            .filter(
                Customer.is_active == True,
                (Customer.company_name.ilike(like) | Customer.phone.ilike(like)),
            )
            .order_by(Customer.company_name)
            .limit(8)
            .all()
        )
    return templates.TemplateResponse(
        "customers/_search_results.html",
        {"request": request, "customers": customers},
    )


# ── Global Log Call (from header button — customer selected in form body) ─────

@router.post("/log-call", response_class=HTMLResponse)
async def log_call_global(request: Request, db: Session = Depends(get_db)):
    """
    Handles the Quick Log Call slide-over in base.html.
    customer_id comes from the form body (chosen via typeahead, not URL).
    Returns OOB toast on success; error HTML on validation failure.
    """
    form = await request.form()
    customer_id_raw = form.get("customer_id", "")
    if not customer_id_raw:
        return HTMLResponse(
            '<p class="text-sm text-red-600 font-medium">Please select a customer before saving.</p>',
            status_code=422,
        )

    try:
        customer_id_int = int(customer_id_raw)
    except ValueError:
        return HTMLResponse(
            '<p class="text-sm text-red-600 font-medium">Invalid customer selection.</p>',
            status_code=422,
        )

    customer = db.query(Customer).filter(Customer.id == customer_id_int).first()
    if not customer:
        return HTMLResponse(
            '<p class="text-sm text-red-600 font-medium">Customer not found.</p>',
            status_code=422,
        )

    call_type = str(form.get("call_type", CallType.INBOUND))
    outcome   = str(form.get("outcome", CallOutcome.OTHER))
    notes     = str(form.get("notes", "")).strip()

    crm = CRMService(db, current_user_id=CURRENT_USER_ID)
    crm.log_call(
        customer_id=customer.id,
        call_type=call_type,
        outcome=outcome,
        notes=notes,
    )

    # Primary swap target is #log-call-result (empty on success).
    # OOB swap appends a toast to #toast-container.
    return HTMLResponse(
        f"""<span></span>
<div id="toast-container" hx-swap-oob="beforeend">
  <div x-data x-init="setTimeout(() => $el.remove(), 4000)"
       class="toast toast-success">
    <svg class="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
      <path fill-rule="evenodd"
            d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
            clip-rule="evenodd"/>
    </svg>
    Call logged for {html.escape(customer.company_name)}.
  </div>
</div>"""
    )


# ── Excel / CSV Import ────────────────────────────────────────────────────────
# IMPORTANT: These routes MUST remain before /{customer_id} (the wildcard).
# FastAPI matches routes in declaration order; if /{customer_id} came first,
# "import" would be captured as a customer_id, fail int conversion, and 422.

# Maps common column name variants → canonical field name
_IMPORT_ALIASES: dict[str, str] = {
    "company": "company_name",
    "company name": "company_name",
    "business": "company_name",
    "business name": "company_name",
    "contact": "contact_name",
    "contact name": "contact_name",
    "name": "contact_name",
    "phone": "phone",
    "phone number": "phone",
    "tel": "phone",
    "telephone": "phone",
    "email": "email",
    "email address": "email",
    "address": "address_line1",
    "address line 1": "address_line1",
    "address_line1": "address_line1",
    "address2": "address_line2",
    "address line 2": "address_line2",
    "address_line2": "address_line2",
    "city": "city",
    "state": "state",
    "st": "state",
    "zip": "zip_code",
    "zip code": "zip_code",
    "postal": "zip_code",
    "postal code": "zip_code",
    "zip_code": "zip_code",
    "terms": "payment_terms",
    "payment terms": "payment_terms",
    "payment_terms": "payment_terms",
    "discount": "discount_pct",
    "discount %": "discount_pct",
    "discount_pct": "discount_pct",
    "interest": "interest_rate",
    "interest rate": "interest_rate",
    "interest rate %": "interest_rate",
    "interest %": "interest_rate",
    "interest_rate": "interest_rate",
    "notes": "notes",
    "note": "notes",
    "internal notes": "internal_notes",
    "internal_notes": "internal_notes",
    "is_taxable": "is_taxable",
    "taxable": "is_taxable",
    "tax exempt": "is_tax_exempt",
    "is_tax_exempt": "is_tax_exempt",
}

_VALID_TERMS = {t.value for t in PaymentTerms}


def _normalise_terms(raw: str) -> str:
    """Map free-text payment terms to a PaymentTerms value, default COD."""
    raw = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if raw in _VALID_TERMS:
        return raw
    if "30" in raw:
        return PaymentTerms.NET_30
    if "60" in raw:
        return PaymentTerms.NET_60
    return PaymentTerms.COD


def _safe_float(val: object, default: float = 0.0) -> float:
    """Parse a float from an import cell; tolerates '5%', '$10', '1,200' etc."""
    try:
        return float(str(val).strip().replace("%", "").replace("$", "").replace(",", "") or default)
    except (ValueError, TypeError):
        return default


def _parse_rows(raw_rows: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    """
    Convert raw header→value dicts into canonical Customer field dicts.
    Returns (valid_rows, skipped_rows).
    Each skipped row has an extra 'error' key.
    """
    valid: list[dict] = []
    skipped: list[dict] = []

    for i, row in enumerate(raw_rows, start=2):  # row 2 = first data row after header
        # Build normalised field dict
        canonical: dict[str, str] = {}
        for col_raw, val in row.items():
            key = _IMPORT_ALIASES.get(col_raw.strip().lower())
            if key:
                canonical[key] = val.strip()

        company = canonical.get("company_name", "").strip()
        if not company:
            skipped.append({**row, "error": f"Row {i}: missing company name"})
            continue

        # Resolve is_tax_exempt — accept both is_tax_exempt and is_taxable columns.
        # is_taxable is the inverse of is_tax_exempt.
        is_tax_exempt_raw = canonical.get("is_tax_exempt", "")
        is_taxable_raw = canonical.get("is_taxable", "")
        if is_tax_exempt_raw:
            is_tax_exempt = is_tax_exempt_raw.lower() in ("true", "yes", "1", "y")
        elif is_taxable_raw:
            is_tax_exempt = is_taxable_raw.lower() not in ("true", "yes", "1", "y")
        else:
            is_tax_exempt = False

        valid.append({
            "company_name":   company,
            "contact_name":   canonical.get("contact_name", ""),
            "phone":          canonical.get("phone", ""),
            "email":          canonical.get("email", ""),
            "address_line1":  canonical.get("address_line1", ""),
            "address_line2":  canonical.get("address_line2", ""),
            "city":           canonical.get("city", ""),
            "state":          canonical.get("state", ""),
            "zip_code":       canonical.get("zip_code", ""),
            "payment_terms":  _normalise_terms(canonical.get("payment_terms", "")),
            "discount_pct":   _safe_float(canonical.get("discount_pct", "0")),
            "interest_rate":  _safe_float(canonical.get("interest_rate", "0")),
            "is_tax_exempt":  is_tax_exempt,
            "notes":          canonical.get("notes", ""),
            "internal_notes": canonical.get("internal_notes", ""),
        })

    return valid, skipped


def _read_xlsx(content: bytes) -> list[dict[str, str]]:
    from openpyxl import load_workbook
    wb = load_workbook(filename=io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h or "").strip() for h in rows[0]]
    result = []
    for row in rows[1:]:
        if all((v is None or str(v).strip() == "") for v in row):
            continue  # skip blank rows
        result.append({headers[i]: str(v or "").strip() for i, v in enumerate(row) if i < len(headers)})
    wb.close()
    return result


def _read_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")  # handles BOM from Excel CSV exports
    reader = csv.DictReader(io.StringIO(text))
    return [
        {k.strip(): v.strip() for k, v in row.items()}
        for row in reader
        if any(v.strip() for v in row.values())
    ]


@router.get("/import", response_class=HTMLResponse)
def customer_import_form(request: Request):
    return templates.TemplateResponse(
        "customers/import.html",
        {
            "request": request,
            "preview_rows": None,
            "import_json": None,
            "skipped": [],
            "total_valid": 0,
            "total_skipped": 0,
        },
    )


@router.post("/import", response_class=HTMLResponse)
async def customer_import_preview(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Parse the uploaded file and return a preview — no DB writes yet."""
    content = await file.read()
    filename = (file.filename or "").lower()

    try:
        if filename.endswith(".xlsx"):
            raw_rows = _read_xlsx(content)
        elif filename.endswith(".csv"):
            raw_rows = _read_csv(content)
        else:
            return templates.TemplateResponse(
                "customers/import.html",
                {
                    "request": request,
                    "preview_rows": None,
                    "import_json": None,
                    "skipped": [],
                    "total_valid": 0,
                    "total_skipped": 0,
                    "error": "Unsupported file type. Please upload a .xlsx or .csv file.",
                },
            )
    except Exception as exc:
        log.exception("Failed to parse import file %s", filename)
        return templates.TemplateResponse(
            "customers/import.html",
            {
                "request": request,
                "preview_rows": None,
                "import_json": None,
                "skipped": [],
                "total_valid": 0,
                "total_skipped": 0,
                "error": f"Could not read file: {exc}",
            },
        )

    valid_rows, skipped_rows = _parse_rows(raw_rows)

    # Flag existing duplicates (by company name, case-insensitive, active customers only).
    existing_names = {
        r[0].lower()
        for r in db.query(Customer.company_name).filter(Customer.is_active == True).all()  # noqa: E712
    }
    for row in valid_rows:
        row["_duplicate"] = row["company_name"].lower() in existing_names

    return templates.TemplateResponse(
        "customers/import.html",
        {
            "request": request,
            "preview_rows": valid_rows[:200],
            "import_json": json.dumps(valid_rows),
            "skipped": skipped_rows,
            "total_valid": len(valid_rows),
            "total_skipped": len(skipped_rows),
            "error": None,
        },
    )


@router.post("/import/confirm", response_class=RedirectResponse)
async def customer_import_confirm(
    request: Request,
    db: Session = Depends(get_db),
):
    """Write validated rows to DB.

    Duplicate check uses ALL customers (including inactive) so that soft-deleted
    records are not silently re-created.  The preview stage only flags active
    duplicates — if the user sees no warning but the confirm skips a row, the
    redirect message will show the skip count as a clue.
    """
    form = await request.form()
    import_json = str(form.get("import_json", "")).strip()
    skip_dupes = str(form.get("skip_duplicates", "1")) == "1"

    try:
        rows = json.loads(import_json)
        if not isinstance(rows, list):
            raise ValueError("payload must be a list")
    except (json.JSONDecodeError, ValueError):
        return RedirectResponse("/customers/import?error=Invalid+import+data", status_code=303)

    existing_names: set[str] = set()
    if skip_dupes:
        # ALL customers including inactive — prevents name collision with soft-deleted records.
        existing_names = {
            r[0].lower()
            for r in db.query(Customer.company_name).all()
        }

    created = 0
    skipped = 0
    try:
        for row in rows:
            if not isinstance(row, dict):
                continue
            company = row.get("company_name", "")
            if not company:
                continue
            if skip_dupes and company.lower() in existing_names:
                skipped += 1
                continue
            db.add(Customer(
                company_name=company,
                contact_name=row.get("contact_name", ""),
                phone=row.get("phone", ""),
                email=row.get("email", ""),
                address_line1=row.get("address_line1", ""),
                address_line2=row.get("address_line2", ""),
                city=row.get("city", ""),
                state=row.get("state", ""),
                zip_code=row.get("zip_code", ""),
                payment_terms=row.get("payment_terms", PaymentTerms.COD),
                discount_pct=_safe_float(row.get("discount_pct", 0)),
                interest_rate=_safe_float(row.get("interest_rate", 0)),
                is_tax_exempt=bool(row.get("is_tax_exempt", False)),
                notes=row.get("notes", ""),
                internal_notes=row.get("internal_notes", ""),
            ))
            created += 1
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Import failed during DB write")
        return RedirectResponse(
            "/customers/import?error=Database+error+during+import",
            status_code=303,
        )

    msg = f"Imported+{created}+customer{'s' if created != 1 else ''}"
    if skipped:
        msg += f"+(skipped+{skipped}+duplicate{'s' if skipped != 1 else ''})"
    return RedirectResponse(f"/customers/?ok={msg}", status_code=303)


# ── Balance mini-panel (HTMX partial for Quote / Invoice workspace headers) ───

@router.get("/{customer_id}/balance-mini", response_class=HTMLResponse)
def customer_balance_mini(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Lightweight partial: balance chips used in Quote and Invoice workspace headers.
    Called via hx-get with hx-trigger="load" so it loads after the page renders.
    """
    from app.services.statement_service import StatementService
    summary = StatementService(db).get_customer_balance_summary(customer_id)
    return templates.TemplateResponse(
        "customers/_balance_mini.html",
        {"request": request, **summary},
    )


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{customer_id}", response_class=HTMLResponse)
def customer_detail(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        return RedirectResponse("/customers/", status_code=303)

    recent_invoices = (
        db.query(Invoice)
        .filter(
            Invoice.customer_id == customer_id,
            Invoice.status != "void",
        )
        .order_by(Invoice.created_at.desc())
        .limit(10)
        .all()
    )

    open_quotes = (
        db.query(Quote)
        .filter(
            Quote.customer_id == customer_id,
            Quote.status.notin_([QuoteStatus.CONVERTED, QuoteStatus.DECLINED]),
        )
        .order_by(Quote.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "customers/detail.html",
        {
            "request": request,
            "customer": c,
            "recent_invoices": recent_invoices,
            "open_quotes": open_quotes,
            "payment_terms": list(PaymentTerms),
            "pricing_tiers": list(PricingTier),
            "call_types": list(CallType),
            "call_outcomes": list(CallOutcome),
        },
    )


# ── Update ────────────────────────────────────────────────────────────────────

@router.post("/{customer_id}", response_class=RedirectResponse)
async def customer_update(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        return RedirectResponse("/customers/", status_code=303)

    form = await request.form()
    c.company_name = str(form.get("company_name", "")).strip()
    c.contact_name = str(form.get("contact_name", "")).strip()
    c.phone = str(form.get("phone", "")).strip()
    c.email = str(form.get("email", "")).strip()
    c.address_line1 = str(form.get("address_line1", "")).strip()
    c.address_line2 = str(form.get("address_line2", "")).strip()
    c.city = str(form.get("city", "")).strip()
    c.state = str(form.get("state", "")).strip()
    c.zip_code = str(form.get("zip_code", "")).strip()
    c.payment_terms = str(form.get("payment_terms", PaymentTerms.COD))
    c.pricing_tier  = str(form.get("pricing_tier", "standard"))
    c.credit_limit  = float(form.get("credit_limit") or 0)
    c.discount_pct  = float(form.get("discount_pct") or 0)
    c.interest_rate = float(form.get("interest_rate") or 0)
    c.is_tax_exempt = bool(form.get("is_tax_exempt"))
    c.tax_exempt_cert_number = str(form.get("tax_exempt_cert_number", "")).strip() or None
    c.notes = str(form.get("notes", "")).strip()
    c.internal_notes = str(form.get("internal_notes", "")).strip()
    db.commit()
    return RedirectResponse(f"/customers/{customer_id}?saved=1", status_code=303)


# ── Log Call (HTMX) ───────────────────────────────────────────────────────────

@router.post("/{customer_id}/log-call", response_class=HTMLResponse)
async def log_call(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    call_type = str(form.get("call_type", CallType.INBOUND))
    outcome = str(form.get("outcome", CallOutcome.OTHER))
    notes = str(form.get("notes", "")).strip()

    crm = CRMService(db, current_user_id=CURRENT_USER_ID)
    entry = crm.log_call(
        customer_id=customer_id,
        call_type=call_type,
        outcome=outcome,
        notes=notes,
    )

    return templates.TemplateResponse(
        "customers/_call_log_row.html",
        {"request": request, "log": entry},
    )


# ── Communications Timeline ───────────────────────────────────────────────────

_RELATED_ENTITY_HREF = {
    "quote":      "/quotes/{id}",
    "invoice":    "/invoices/{id}",
    "so":         "/sales-orders/{id}",
    "po":         "/purchase-orders/{id}",
    "ra":         "/returns/{id}",
    "warranty":   "/warranty/{id}",
    "research":   "/research/{id}",
    "core_slip":  "/cores/{id}",
    "statement":  "/customers/{id}/statement",
}


@router.get("/{customer_id}/communications", response_class=HTMLResponse)
def customer_communications(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Communication timeline for a customer.

    Lists every email, SMS, phone-call note, and manual comm logged to this
    customer — outbound and inbound — newest first. Read-only (immutable log).
    """
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if c is None:
        return RedirectResponse("/customers/", status_code=303)

    comms = (
        db.query(Communication)
        .filter(Communication.customer_id == customer_id)
        .order_by(Communication.sent_at.desc())
        .limit(500)
        .all()
    )

    # Build a related-doc href lookup per row so the template can stay dumb
    related_links: dict[int, dict[str, str] | None] = {}
    for comm in comms:
        if comm.related_entity_type and comm.related_entity_id:
            tmpl = _RELATED_ENTITY_HREF.get(comm.related_entity_type)
            if tmpl:
                related_links[comm.id] = {
                    "label": f"{comm.related_entity_type.replace('_', ' ').title()} #{comm.related_entity_id}",
                    "href":  tmpl.format(id=comm.related_entity_id),
                }
            else:
                related_links[comm.id] = {
                    "label": f"{comm.related_entity_type} #{comm.related_entity_id}",
                    "href":  "",
                }
        else:
            related_links[comm.id] = None

    return templates.TemplateResponse(
        "customers/communications.html",
        {
            "request":        request,
            "customer":       c,
            "comms":          comms,
            "related_links":  related_links,
        },
    )


# Maps the form's comm_type select value → (channel, direction)
_COMM_TYPE_MAP: dict[str, tuple[str, str]] = {
    "phone_outbound": (CommunicationChannel.PHONE_CALL, CommunicationDirection.OUTBOUND),
    "phone_inbound":  (CommunicationChannel.PHONE_CALL, CommunicationDirection.INBOUND),
    "email_outbound": (CommunicationChannel.EMAIL,       CommunicationDirection.OUTBOUND),
    "email_inbound":  (CommunicationChannel.EMAIL,       CommunicationDirection.INBOUND),
    "sms_outbound":   (CommunicationChannel.SMS,         CommunicationDirection.OUTBOUND),
    "sms_inbound":    (CommunicationChannel.SMS,         CommunicationDirection.INBOUND),
    "note":           (CommunicationChannel.MANUAL_NOTE, CommunicationDirection.OUTBOUND),
}


@router.post("/{customer_id}/communications/log", response_class=RedirectResponse)
async def customer_communications_log(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Log a manual communication entry (no real send — all go through NullProvider)."""
    form = await request.form()
    comm_type      = str(form.get("comm_type", "phone_outbound"))
    body           = str(form.get("body", "")).strip()
    subject        = str(form.get("subject", "")).strip() or None
    contact_addr   = str(form.get("contact_address", "")).strip()
    rel_type       = str(form.get("related_entity_type", "")).strip() or None
    rel_id_raw     = str(form.get("related_entity_id", "")).strip()
    rel_id         = int(rel_id_raw) if rel_id_raw.isdigit() else None

    channel, direction = _COMM_TYPE_MAP.get(
        comm_type,
        (CommunicationChannel.MANUAL_NOTE, CommunicationDirection.OUTBOUND),
    )

    svc = MessagingService(db, current_user_id=CURRENT_USER_ID)
    if direction == CommunicationDirection.INBOUND:
        svc.record_inbound(
            customer_id=customer_id,
            channel=channel,
            body=body,
            subject=subject,
            from_address=contact_addr,
            related_entity_type=rel_type,
            related_entity_id=rel_id,
        )
    else:
        svc.log_manual_communication(
            customer_id=customer_id,
            channel=channel,
            body=body,
            subject=subject,
            to_address=contact_addr,
            related_entity_type=rel_type,
            related_entity_id=rel_id,
        )

    return RedirectResponse(
        f"/customers/{customer_id}/communications",
        status_code=303,
    )


# ── Balance Widget (HTMX) ─────────────────────────────────────────────────────

@router.get("/{customer_id}/balance", response_class=HTMLResponse)
def customer_balance(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    crm = CRMService(db, current_user_id=CURRENT_USER_ID)
    balance = crm.get_account_balance(customer_id)
    return templates.TemplateResponse(
        "customers/_balance_widget.html",
        {"request": request, "balance": balance},
    )


# ── Deactivate ────────────────────────────────────────────────────────────────

@router.post("/{customer_id}/deactivate", response_class=RedirectResponse)
def customer_deactivate(
    customer_id: int,
    db: Session = Depends(get_db),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if c:
        c.is_active = False
        db.commit()
    return RedirectResponse("/customers/", status_code=303)


# ── Statement ─────────────────────────────────────────────────────────────────

@router.get("/{customer_id}/statement", response_class=HTMLResponse)
def customer_statement_form(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Statement date-range selector page."""
    from datetime import date
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if c is None:
        return RedirectResponse("/customers/", status_code=303)
    today = date.today()
    # Default: first of current month → today
    default_start = today.replace(day=1)
    return templates.TemplateResponse(
        "customers/statement_form.html",
        {
            "request": request,
            "customer": c,
            "default_start": default_start.isoformat(),
            "default_end": today.isoformat(),
        },
    )


@router.get("/{customer_id}/statement/print", response_class=HTMLResponse)
def customer_statement_print(
    customer_id: int,
    request: Request,
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
):
    """Print-ready statement HTML. Also used by /pdf to generate the PDF bytes."""
    from datetime import date
    from app.services.statement_service import StatementService
    from app.settings_utils import get_setting_value_db

    today = date.today()
    try:
        period_start = date.fromisoformat(start) if start else today.replace(day=1)
        period_end = date.fromisoformat(end) if end else today
    except ValueError:
        period_start = today.replace(day=1)
        period_end = today

    stmt = StatementService(db).generate_statement(
        customer_id=customer_id,
        period_start=period_start,
        period_end=period_end,
    )
    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }
    return templates.TemplateResponse(
        "customers/statement_print.html",
        {"request": request, "stmt": stmt, "company": company},
    )


@router.get("/{customer_id}/statement/pdf")
def customer_statement_pdf(
    customer_id: int,
    request: Request,
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
):
    """Server-side PDF via WeasyPrint. Falls back to print view if GTK missing."""
    from datetime import date
    from urllib.parse import quote as url_quote
    from app.services.statement_service import StatementService
    from app.settings_utils import get_setting_value_db
    from fastapi.responses import Response as FastAPIResponse

    today = date.today()
    try:
        period_start = date.fromisoformat(start) if start else today.replace(day=1)
        period_end = date.fromisoformat(end) if end else today
    except ValueError:
        period_start = today.replace(day=1)
        period_end = today

    stmt = StatementService(db).generate_statement(
        customer_id=customer_id,
        period_start=period_start,
        period_end=period_end,
    )
    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }
    html_str = templates.env.get_template("customers/statement_print.html").render(
        request=request, stmt=stmt, company=company,
    )
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_str, base_url=str(request.base_url)).write_pdf()
    except Exception:
        return RedirectResponse(
            f"/customers/{customer_id}/statement/print?start={start}&end={end}",
            status_code=302,
        )
    safe_name = url_quote(stmt["customer"].company_name.replace("/", "-"))
    filename = f"Statement_{safe_name}_{period_end.isoformat()}.pdf"
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
