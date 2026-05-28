from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.constants import POStatus
from app.deps import get_db
from app.models.customer import Customer, CustomerAddress
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine
from app.models.vendor import Vendor
from app.services.document_render import (
    customer_address_lines,
    get_company_dict,
    render_pdf_or_fallback,
    vendor_address_lines,
)
from app.services.po_service import POService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/purchase-orders", tags=["purchase_orders"])
templates = Jinja2Templates(directory="app/templates")

CURRENT_USER_ID = 1

STATUS_TABS = [
    ("", "All"),
    (POStatus.DRAFT, "Draft"),
    (POStatus.VERBAL_ORDER, "Verbal"),
    (POStatus.SENT, "Sent"),
    (POStatus.PARTIAL, "Partial"),
    (POStatus.RECEIVED, "Received"),
    (POStatus.BILLED, "Billed"),
    (POStatus.CANCELLED, "Cancelled"),
]


def _workspace_ctx(po: PurchaseOrder) -> dict:
    editable   = po.status in (POStatus.DRAFT, POStatus.VERBAL_ORDER)
    can_receive = po.status in (POStatus.SENT, POStatus.PARTIAL)
    can_bill    = po.status in (POStatus.RECEIVED, POStatus.PARTIAL)
    return {
        "po": po,
        "editable": editable,
        "can_receive": can_receive,
        "can_bill": can_bill,
        "POStatus": POStatus,
        "unreceived_lines": [ln for ln in po.lines if ln.qty_outstanding > 0] if can_receive else [],
        "received_lines":   [ln for ln in po.lines if ln.qty_received > 0 and (ln.qty_received - (ln.qty_billed or 0)) > 0] if can_bill else [],
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def po_list(request: Request, status: str = "", db: Session = Depends(get_db)):
    query = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
    if status:
        query = query.filter(PurchaseOrder.status == status)
    pos = query.all()
    return templates.TemplateResponse(
        "purchase_orders/list.html",
        {
            "request": request,
            "pos": pos,
            "status_tabs": STATUS_TABS,
            "active_status": status,
        },
    )


# ── New PO picker (slide-over) ─────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def po_new(request: Request, db: Session = Depends(get_db)):
    if not request.headers.get("HX-Request"):
        return RedirectResponse("/purchase-orders/", status_code=303)
    vendors = db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()
    return templates.TemplateResponse(
        "purchase_orders/_new_picker.html",
        {"request": request, "vendors": vendors, "POStatus": POStatus},
    )


@router.post("/new", response_class=RedirectResponse)
async def po_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    po_data: dict = {
        "notes": str(form.get("notes", "")).strip(),
        "internal_notes": "",
        "freight_in_cost": float(form.get("freight_in_cost") or 0.0),
        "vendor_confirmation_number": str(form.get("vendor_confirmation_number", "")).strip() or None,
        "expected_at": None,
    }

    expected_raw = str(form.get("expected_at", "")).strip()
    if expected_raw:
        try:
            po_data["expected_at"] = datetime.strptime(expected_raw, "%Y-%m-%d")
        except ValueError:
            pass

    status_override = str(form.get("status", "")).strip()
    if status_override == POStatus.VERBAL_ORDER:
        po_data["status"] = POStatus.VERBAL_ORDER

    po = svc.create_po(vendor_id=int(form["vendor_id"]), data=po_data)

    if status_override == POStatus.VERBAL_ORDER:
        po.status = POStatus.VERBAL_ORDER
        db.commit()

    return RedirectResponse(f"/purchase-orders/{po.id}", status_code=303)


# ── Workspace ─────────────────────────────────────────────────────────────────

@router.get("/{po_id}", response_class=HTMLResponse)
def po_workspace(po_id: int, request: Request, db: Session = Depends(get_db)):
    po = (
        db.query(PurchaseOrder)
        .options(joinedload(PurchaseOrder.lines).joinedload(POLine.product))
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)
    ctx = _workspace_ctx(po)
    ctx["request"] = request
    return templates.TemplateResponse("purchase_orders/workspace.html", ctx)


# ── Header autosave ───────────────────────────────────────────────────────────

@router.post("/{po_id}/header", response_class=HTMLResponse)
async def po_header_save(po_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    expected_raw = str(form.get("expected_at", "")).strip()
    expected_dt = None
    if expected_raw:
        try:
            expected_dt = datetime.strptime(expected_raw, "%Y-%m-%d")
        except ValueError:
            pass

    data = {
        "notes": str(form.get("notes", "")).strip(),
        "internal_notes": str(form.get("internal_notes", "")).strip(),
        "vendor_confirmation_number": str(form.get("vendor_confirmation_number", "")).strip() or None,
        "freight_in_cost": float(form.get("freight_in_cost") or 0.0),
        "expected_at": expected_dt,
    }
    try:
        svc.save_header(po_id, data)
    except ValueError:
        pass
    return HTMLResponse("", status_code=204)


# ── Line CRUD (HTMX) ──────────────────────────────────────────────────────────

def _lines_response(po_id: int, request: Request, db: Session) -> HTMLResponse:
    po = (
        db.query(PurchaseOrder)
        .options(joinedload(PurchaseOrder.lines).joinedload(POLine.product))
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    ctx = _workspace_ctx(po)
    ctx["request"] = request
    return templates.TemplateResponse("purchase_orders/_lines_section.html", ctx)


@router.post("/{po_id}/lines", response_class=HTMLResponse)
async def po_add_line(po_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    pid_raw = str(form.get("product_id", "")).strip()
    desc = str(form.get("description", "")).strip()
    qty_raw = str(form.get("qty_ordered", "1")).strip()
    cost_raw = str(form.get("unit_cost", "")).strip()
    core_raw = str(form.get("core_charge_per_unit", "")).strip()

    line_data = {
        "description": desc,
        "qty_ordered": max(1, int(qty_raw) if qty_raw.isdigit() else 1),
        "unit_cost": float(cost_raw) if cost_raw else 0.0,
        "core_charge_per_unit": float(core_raw) if core_raw else 0.0,
        "notes": "",
    }

    # Auto-fill description from product if blank
    pid = int(pid_raw) if pid_raw else None
    if pid and not desc:
        product = db.query(Product).filter(Product.id == pid).first()
        if product:
            line_data["description"] = product.title or ""

    try:
        svc.add_line(po_id=po_id, product_id=pid, data=line_data)
    except ValueError as exc:
        log.warning("add_line error on PO %s: %s", po_id, exc)

    return _lines_response(po_id, request, db)


@router.post("/{po_id}/lines/{line_id}", response_class=HTMLResponse)
async def po_update_line(po_id: int, line_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    data: dict = {}
    if "description" in form:
        data["description"] = str(form["description"]).strip()
    if "qty_ordered" in form:
        raw = str(form["qty_ordered"]).strip()
        data["qty_ordered"] = max(1, int(raw)) if raw.isdigit() else 1
    if "unit_cost" in form:
        try:
            data["unit_cost"] = float(form["unit_cost"])
        except (ValueError, TypeError):
            pass
    if "core_charge_per_unit" in form:
        try:
            data["core_charge_per_unit"] = float(form["core_charge_per_unit"])
        except (ValueError, TypeError):
            pass

    try:
        svc.update_line(line_id, data)
    except ValueError as exc:
        log.warning("update_line error on line %s: %s", line_id, exc)

    return _lines_response(po_id, request, db)


@router.post("/{po_id}/lines/{line_id}/delete", response_class=HTMLResponse)
async def po_delete_line(po_id: int, line_id: int, request: Request, db: Session = Depends(get_db)):
    svc = POService(db, current_user_id=CURRENT_USER_ID)
    try:
        svc.delete_line(line_id)
    except ValueError as exc:
        log.warning("delete_line error on line %s: %s", line_id, exc)
    return _lines_response(po_id, request, db)


# ── Product search (HTMX typeahead) ──────────────────────────────────────────

@router.get("/_/product-search", response_class=HTMLResponse)
def po_product_search(q: str = "", db: Session = Depends(get_db), request: Request = None):
    results = []
    if q and len(q) >= 2:
        pattern = f"%{q}%"
        results = (
            db.query(Product)
            .filter(
                Product.is_active == True,
                (Product.sku.ilike(pattern) | Product.title.ilike(pattern)),
            )
            .order_by(Product.sku)
            .limit(12)
            .all()
        )
    return templates.TemplateResponse(
        "purchase_orders/_product_search_results.html",
        {"request": request, "results": results},
    )


# ── Status transitions ─────────────────────────────────────────────────────────

@router.post("/{po_id}/send", response_class=RedirectResponse)
def po_send(po_id: int, db: Session = Depends(get_db)):
    svc = POService(db, current_user_id=CURRENT_USER_ID)
    try:
        svc.send_to_vendor(po_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error sending PO %s to vendor", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — PO was not sent.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}?ok=sent", status_code=303)


@router.post("/{po_id}/receive", response_class=RedirectResponse)
async def po_receive(po_id: int, request: Request, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)

    form = await request.form()
    po_line_quantities: dict[int, int] = {}
    for line in po.lines:
        raw = form.get(f"recv_{line.id}", "")
        qty = int(raw) if raw and str(raw).strip().isdigit() else 0
        if qty > 0:
            po_line_quantities[line.id] = qty

    if po_line_quantities:
        try:
            svc = POService(db, current_user_id=CURRENT_USER_ID)
            receipt_data = {
                "tracking_number": str(form.get("tracking_number", "")).strip() or None,
                "carrier": str(form.get("carrier", "")).strip() or None,
                "notes": str(form.get("notes", "")).strip(),
            }
            svc.create_receipt(
                vendor_id=po.vendor_id,
                po_line_quantities=po_line_quantities,
                data=receipt_data,
            )
        except ValueError as exc:
            db.rollback()
            return RedirectResponse(
                f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
                status_code=303,
            )
        except Exception:
            db.rollback()
            log.exception("Unexpected error receiving PO %s", po_id)
            return RedirectResponse(
                f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — receipt was not recorded.')}",
                status_code=303,
            )

    return RedirectResponse(f"/purchase-orders/{po_id}?ok=received", status_code=303)


@router.post("/{po_id}/cancel-status", response_class=RedirectResponse)
def po_cancel(po_id: int, db: Session = Depends(get_db)):
    svc = POService(db, current_user_id=CURRENT_USER_ID)
    try:
        svc.cancel(po_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error cancelling PO %s", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — PO was not cancelled.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}", status_code=303)


@router.post("/{po_id}/create-bill", response_class=RedirectResponse)
async def po_create_bill(po_id: int, request: Request, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)

    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    bill_number = str(form.get("bill_number", "")).strip()
    bill_date: datetime | None = None
    due_date: datetime | None = None
    for field, raw in [("bill_date", str(form.get("bill_date", "")).strip()),
                       ("due_date", str(form.get("due_date", "")).strip())]:
        if raw:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d")
                if field == "bill_date":
                    bill_date = parsed
                else:
                    due_date = parsed
            except ValueError:
                pass

    lines = []
    for line in po.lines:
        qty_raw = str(form.get(f"qty_billed_{line.id}", "0")).strip()
        cost_raw = str(form.get(f"unit_cost_{line.id}", "")).strip()
        try:
            qty = int(qty_raw)
        except (ValueError, TypeError):
            qty = 0
        if qty > 0:
            try:
                unit_cost = float(cost_raw)
            except (ValueError, TypeError):
                unit_cost = float(line.unit_cost)
            lines.append({"po_line_id": line.id, "qty_billed": qty, "unit_cost": unit_cost})

    if not lines:
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('No billable quantities entered.')}",
            status_code=303,
        )

    try:
        svc.create_vendor_bill(
            po_id=po_id,
            vendor_id=po.vendor_id,
            bill_number=bill_number,
            bill_date=bill_date,
            due_date=due_date,
            lines=lines,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error creating bill for PO %s", po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — bill was not created.')}",
            status_code=303,
        )

    return RedirectResponse(f"/purchase-orders/{po_id}?ok=billed", status_code=303)


@router.post("/{po_id}/bills/{bill_id}/approve", response_class=RedirectResponse)
async def po_approve_bill(po_id: int, bill_id: int, db: Session = Depends(get_db)):
    svc = POService(db, current_user_id=CURRENT_USER_ID)
    try:
        svc.approve_bill(bill_id)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        log.exception("Unexpected error approving bill %s for PO %s", bill_id, po_id)
        return RedirectResponse(
            f"/purchase-orders/{po_id}?error={url_quote('Unexpected error — bill was not approved.')}",
            status_code=303,
        )
    return RedirectResponse(f"/purchase-orders/{po_id}?ok=bill_approved", status_code=303)


# ── Print / PDF ───────────────────────────────────────────────────────────────

def _po_print_context(po: PurchaseOrder, db: Session) -> dict:
    company = get_company_dict(db)

    # The company address comes from settings as a multi-line blob — split it
    # into lines for the Ship-To block.
    company_addr_lines = [
        ln.strip() for ln in (company.get("address") or "").splitlines() if ln.strip()
    ]
    if company.get("phone"):
        company_addr_lines.append(company["phone"])

    vendor_addr_lines_ = vendor_address_lines(po.vendor)

    # Drop-ship destination (customer address)
    dropship_customer = None
    dropship_addr_lines: list[str] = []
    if po.is_drop_ship and po.drop_ship_customer_id:
        dropship_customer = (
            db.query(Customer).filter(Customer.id == po.drop_ship_customer_id).first()
        )
        if po.drop_ship_address_id:
            addr = (
                db.query(CustomerAddress)
                .filter(CustomerAddress.id == po.drop_ship_address_id)
                .first()
            )
            if addr is not None:
                # CustomerAddress uses `street` / `street_line2`; build a shim
                # so customer_address_lines() can handle it.
                class _AddrShim:
                    address_line1 = addr.street
                    address_line2 = addr.street_line2
                    city = addr.city
                    state = addr.state
                    zip_code = addr.zip_code
                    phone = addr.phone
                dropship_addr_lines = customer_address_lines(_AddrShim())
        if not dropship_addr_lines and dropship_customer is not None:
            dropship_addr_lines = customer_address_lines(dropship_customer)

    return {
        "po": po,
        "company": company,
        "company_addr_lines": company_addr_lines,
        "vendor_addr_lines": vendor_addr_lines_,
        "dropship_customer": dropship_customer,
        "dropship_addr_lines": dropship_addr_lines,
    }


@router.get("/{po_id}/print", response_class=HTMLResponse)
def po_print(po_id: int, request: Request, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if po is None:
        return RedirectResponse("/purchase-orders/", status_code=303)
    ctx = _po_print_context(po, db)
    ctx["request"] = request
    return templates.TemplateResponse("purchase_orders/print.html", ctx)


@router.get("/{po_id}/pdf")
def po_pdf(po_id: int, request: Request, db: Session = Depends(get_db)):
    """WeasyPrint PDF; redirects to /print on missing GTK libs."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if po is None:
        return RedirectResponse("/purchase-orders/", status_code=303)
    ctx = _po_print_context(po, db)
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="purchase_orders/print.html",
        context=ctx,
        fallback_print_url=f"/purchase-orders/{po_id}/print",
        download_filename=po.po_number,
    )
