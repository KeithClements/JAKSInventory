"""
app/routers/invoices.py
========================
Invoice list, detail, create, finalise, payment recording, and void.

Key design rules:
  - InvoiceService owns invoice.status — no direct model writes here.
  - PaymentService owns Payment + PaymentAllocation — called for all payment paths.
  - Lazy service imports inside route bodies to avoid circular imports.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import InvoiceStatus, LineType, PaymentMethod
from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.settings_utils import get_setting_value_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def invoice_list(
    request: Request,
    status: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    from sqlalchemy import or_
    query = (
        db.query(Invoice)
        .join(Customer)
        .filter(Invoice.status != InvoiceStatus.VOID)
    )
    if status:
        query = query.filter(Invoice.status == status)
    if q:
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(f"%{q}%"),
                Customer.company_name.ilike(f"%{q}%"),
                Invoice.customer_po_number.ilike(f"%{q}%"),
                Invoice.esn.ilike(f"%{q}%"),
            )
        )
    invoices = query.order_by(Invoice.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "invoices/list.html",
        {
            "request": request,
            "invoices": invoices,
            "status_filter": status,
            "q": q,
            "InvoiceStatus": InvoiceStatus,
        },
    )


# ── New / Create ───────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def invoice_new(
    request: Request,
    customer_id: int = 0,
    so_id: int = 0,
    db: Session = Depends(get_db),
):
    customers = (
        db.query(Customer)
        .filter(Customer.is_active == True)  # noqa: E712
        .order_by(Customer.company_name)
        .all()
    )
    products = (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .order_by(Product.sku)
        .all()
    )
    selected_customer = None
    if customer_id:
        selected_customer = db.query(Customer).filter(Customer.id == customer_id).first()

    surcharge_pct = float(get_setting_value_db(db, "cc_surcharge_pct", "3.0"))

    return templates.TemplateResponse(
        "invoices/new.html",
        {
            "request": request,
            "customers": customers,
            "products": products,
            "selected_customer": selected_customer,
            "so_id": so_id,
            "surcharge_pct": surcharge_pct,
        },
    )


@router.post("/new", response_class=RedirectResponse)
async def invoice_create(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.invoice_service import InvoiceService

    form = await request.form()
    surcharge_pct = float(get_setting_value_db(db, "cc_surcharge_pct", "3.0"))

    # Parse due date
    due_date = None
    due_raw = str(form.get("due_date", "")).strip()
    if due_raw:
        try:
            due_date = datetime.strptime(due_raw, "%Y-%m-%d")
        except ValueError:
            pass

    # Parse SO link
    so_id_raw = str(form.get("so_id", "")).strip()
    so_id = int(so_id_raw) if so_id_raw else None

    discount_pct = float(form.get("discount_pct") or 0.0)

    data = {
        "customer_po_number": str(form.get("customer_po_number", "")).strip() or None,
        "esn": str(form.get("esn", "")).strip() or None,
        "engine_manufacturer": str(form.get("engine_manufacturer", "")).strip(),
        "engine_model": str(form.get("engine_model", "")).strip(),
        "discount_pct": discount_pct,
        "is_taxable": bool(form.get("is_taxable")),
        "tax_rate": float(form.get("tax_rate") or 0.0),
        "apply_cc_surcharge": bool(form.get("apply_cc_surcharge")),
        "cc_surcharge_pct": surcharge_pct,
        "notes": str(form.get("notes", "")).strip(),
        "due_date": due_date,
    }

    # Parse parallel-array line fields
    product_ids = form.getlist("product_id[]")
    descriptions = form.getlist("description[]")
    qtys = form.getlist("qty[]")
    unit_prices = form.getlist("unit_price[]")

    lines = []
    for i, pid in enumerate(product_ids):
        desc = descriptions[i] if i < len(descriptions) else ""
        qty_raw = qtys[i] if i < len(qtys) else "1"
        price_raw = unit_prices[i] if i < len(unit_prices) else "0"

        if not pid and not desc.strip():
            continue

        lines.append({
            "product_id": int(pid) if pid else None,
            "description": desc.strip(),
            "qty": max(1, int(qty_raw)) if qty_raw else 1,
            "unit_price": float(price_raw) if price_raw else 0.0,
            "line_type": LineType.PRODUCT,
            "discount_pct": discount_pct,
        })

    customer_id_raw = str(form.get("customer_id", "")).strip()
    if not customer_id_raw:
        return RedirectResponse("/invoices/new", status_code=303)
    customer_id = int(customer_id_raw)
    invoice = InvoiceService(db, user_id).create_invoice(
        customer_id=customer_id, data=data, so_id=so_id, lines=lines
    )
    return RedirectResponse(f"/invoices/{invoice.id}", status_code=303)


# ── Print / PDF ───────────────────────────────────────────────────────────────

@router.get("/{invoice_id}/print", response_class=HTMLResponse)
def invoice_print(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)

    # Build customer address lines
    c = inv.customer
    addr_lines: list[str] = [ln for ln in [c.address_line1, c.address_line2] if ln and ln.strip()]
    city_parts = [p for p in [c.city, c.state] if p and p.strip()]
    city_line = ", ".join(city_parts)
    if city_line and c.zip_code and c.zip_code.strip():
        city_line += " " + c.zip_code.strip()
    elif not city_line and c.zip_code and c.zip_code.strip():
        city_line = c.zip_code.strip()
    if city_line:
        addr_lines.append(city_line)
    if c.phone and c.phone.strip():
        addr_lines.append(c.phone.strip())

    # Discount amount (gross - subtotal)
    gross = round(sum(ln.line_total for ln in inv.lines), 2)
    discount_amount = round(gross - inv.subtotal, 2) if inv.discount_pct else 0.0

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    return templates.TemplateResponse("invoices/print.html", {
        "request": request,
        "invoice": inv,
        "customer_addr_lines": addr_lines,
        "discount_amount": discount_amount,
        "company": company,
    })


@router.get("/{invoice_id}/pdf")
def invoice_pdf(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Server-side PDF generation via WeasyPrint.
    Returns a downloadable PDF — no browser print dialog required.
    """
    from fastapi.responses import Response as FastAPIResponse

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)

    c = inv.customer
    addr_lines: list[str] = [ln for ln in [c.address_line1, c.address_line2] if ln and ln.strip()]
    city_parts = [p for p in [c.city, c.state] if p and p.strip()]
    city_line = ", ".join(city_parts)
    if city_line and c.zip_code and c.zip_code.strip():
        city_line += " " + c.zip_code.strip()
    elif not city_line and c.zip_code and c.zip_code.strip():
        city_line = c.zip_code.strip()
    if city_line:
        addr_lines.append(city_line)
    if c.phone and c.phone.strip():
        addr_lines.append(c.phone.strip())

    gross = round(sum(ln.line_total for ln in inv.lines), 2)
    discount_amount = round(gross - inv.subtotal, 2) if inv.discount_pct else 0.0

    company = {
        "name":    get_setting_value_db(db, "company_name",    "JAKS Parts"),
        "address": get_setting_value_db(db, "company_address", ""),
        "phone":   get_setting_value_db(db, "company_phone",   ""),
        "email":   get_setting_value_db(db, "company_email",   ""),
    }

    html_str = templates.env.get_template("invoices/print.html").render(
        request=request,
        invoice=inv,
        customer_addr_lines=addr_lines,
        discount_amount=discount_amount,
        company=company,
    )

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_str, base_url=str(request.base_url)).write_pdf()
    except (OSError, ImportError, Exception):
        # WeasyPrint system libraries (GTK/Pango) not available on this host.
        # Fall back to browser print-to-PDF.
        return RedirectResponse(
            f"/invoices/{invoice_id}/print", status_code=302
        )

    safe_number = inv.invoice_number.replace("/", "-").replace("\\", "-")
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_number}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)
    active_allocations = [a for a in inv.allocations if not a.is_reversed]
    return templates.TemplateResponse(
        "invoices/detail.html",
        {
            "request": request,
            "invoice": inv,
            "active_allocations": active_allocations,
            "InvoiceStatus": InvoiceStatus,
            "PaymentMethod": PaymentMethod,
        },
    )


# ── Finalise ──────────────────────────────────────────────────────────────────

@router.post("/{invoice_id}/finalise", response_class=RedirectResponse)
def invoice_finalise(
    invoice_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.invoice_service import InvoiceService
    try:
        InvoiceService(db, user_id).finalise(invoice_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error finalising invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Unexpected error — invoice was not finalised.')}",
            status_code=303,
        )
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


# ── Payment ───────────────────────────────────────────────────────────────────

@router.post("/{invoice_id}/payment", response_class=RedirectResponse)
async def invoice_payment(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.payment_service import PaymentService

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return RedirectResponse("/invoices/", status_code=303)

    form = await request.form()
    try:
        amount = float(form.get("amount", 0))
        if amount <= 0:
            raise ValueError("Payment amount must be greater than zero.")

        payment_method = str(form.get("method", PaymentMethod.CASH))
        data = {
            "check_number": str(form.get("check_number", "")).strip() or None,
            "notes": str(form.get("notes", "")).strip(),
        }
        PaymentService(db, user_id).record_payment(
            customer_id=inv.customer_id,
            amount_received=amount,
            payment_method=payment_method,
            data=data,
            invoice_ids=[invoice_id],
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error recording payment for invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Unexpected error — payment was not recorded.')}",
            status_code=303,
        )

    return RedirectResponse(f"/invoices/{invoice_id}?saved=1", status_code=303)


# ── Void ──────────────────────────────────────────────────────────────────────

@router.post("/{invoice_id}/void", response_class=RedirectResponse)
async def invoice_void(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.invoice_service import InvoiceService

    form = await request.form()
    reason = str(form.get("reason", "")).strip() or "voided"
    try:
        InvoiceService(db, user_id).void_invoice(invoice_id, reason)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error voiding invoice %s", invoice_id)
        return RedirectResponse(
            f"/invoices/{invoice_id}?error={url_quote('Unexpected error — invoice was not voided.')}",
            status_code=303,
        )

    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)
