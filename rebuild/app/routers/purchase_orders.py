from __future__ import annotations

import logging
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import POStatus
from app.deps import get_db
from app.models.purchase_order import PurchaseOrder
from app.models.vendor import Vendor
from app.models.product import Product
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


@router.get("/new", response_class=HTMLResponse)
def po_new(request: Request, db: Session = Depends(get_db)):
    vendors = db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()
    products = db.query(Product).filter(Product.is_active == True).order_by(Product.sku).all()
    return templates.TemplateResponse(
        "purchase_orders/new.html",
        {"request": request, "vendors": vendors, "products": products},
    )


@router.post("/new", response_class=RedirectResponse)
async def po_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    po_data = {
        "notes": str(form.get("notes", "")).strip(),
        "internal_notes": str(form.get("internal_notes", "")).strip(),
        "freight_in_cost": float(form.get("freight_in_cost") or 0.0),
        "vendor_confirmation_number": str(form.get("vendor_confirmation_number", "")).strip() or None,
        "expected_at": None,
    }

    expected_raw = str(form.get("expected_at", "")).strip()
    if expected_raw:
        from datetime import datetime
        try:
            po_data["expected_at"] = datetime.strptime(expected_raw, "%Y-%m-%d")
        except ValueError:
            pass

    po = svc.create_po(vendor_id=int(form["vendor_id"]), data=po_data)

    # Add lines (submitted as parallel arrays)
    product_ids = form.getlist("product_id[]")
    qtys = form.getlist("qty[]")
    costs = form.getlist("unit_cost[]")
    cores = form.getlist("core_charge[]")
    descs = form.getlist("description[]")

    for i, pid in enumerate(product_ids):
        desc = descs[i] if i < len(descs) else ""
        qty_raw = qtys[i] if i < len(qtys) else ""
        cost_raw = costs[i] if i < len(costs) else ""
        core_raw = cores[i] if i < len(cores) else ""

        # Skip totally blank rows
        if not pid and not desc.strip():
            continue

        line_data = {
            "description": desc.strip(),
            "qty_ordered": int(qty_raw) if qty_raw else 1,
            "unit_cost": float(cost_raw) if cost_raw else 0.0,
            "core_charge_per_unit": float(core_raw) if core_raw else 0.0,
            "notes": "",
        }
        svc.add_line(
            po_id=po.id,
            product_id=int(pid) if pid else None,
            data=line_data,
        )

    return RedirectResponse(f"/purchase-orders/{po.id}", status_code=303)


@router.get("/{po_id}", response_class=HTMLResponse)
def po_detail(po_id: int, request: Request, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)
    products = db.query(Product).filter(Product.is_active == True).order_by(Product.sku).all()
    unreceived = [ln for ln in po.lines if ln.qty_outstanding > 0]
    received = [ln for ln in po.lines if ln.qty_received > 0]
    return templates.TemplateResponse(
        "purchase_orders/detail.html",
        {
            "request": request,
            "po": po,
            "products": products,
            "unreceived_lines": unreceived,
            "received_lines": received,
        },
    )


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
    return RedirectResponse(f"/purchase-orders/{po_id}", status_code=303)


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

    return RedirectResponse(f"/purchase-orders/{po_id}?received=1", status_code=303)


@router.post("/{po_id}/cancel-status", response_class=RedirectResponse)
def po_cancel(po_id: int, db: Session = Depends(get_db)):
    """Cancel PO via service — restores qty_on_order if PO was already sent."""
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
    """Create a vendor bill from received goods (3-way match)."""
    from datetime import datetime as dt

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return RedirectResponse("/purchase-orders/", status_code=303)

    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    bill_number = str(form.get("bill_number", "")).strip()

    bill_date: dt | None = None
    due_date: dt | None = None
    for field, raw in [("bill_date", str(form.get("bill_date", "")).strip()),
                       ("due_date", str(form.get("due_date", "")).strip())]:
        if raw:
            try:
                parsed = dt.strptime(raw, "%Y-%m-%d")
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

    return RedirectResponse(f"/purchase-orders/{po_id}?billed=1", status_code=303)


@router.post("/{po_id}/add-line", response_class=HTMLResponse)
async def po_add_line(po_id: int, request: Request, db: Session = Depends(get_db)):
    """HTMX endpoint — returns a single <tr> for the new line."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        return HTMLResponse("", status_code=404)

    form = await request.form()
    svc = POService(db, current_user_id=CURRENT_USER_ID)

    pid_raw = str(form.get("product_id", "")).strip()
    line_data = {
        "description": str(form.get("description", "")).strip(),
        "qty_ordered": int(form.get("qty_ordered") or 1),
        "unit_cost": float(form.get("unit_cost") or 0.0),
        "core_charge_per_unit": float(form.get("core_charge_per_unit") or 0.0),
        "notes": "",
    }

    try:
        line = svc.add_line(
            po_id=po_id,
            product_id=int(pid_raw) if pid_raw else None,
            data=line_data,
        )
    except ValueError as exc:
        return HTMLResponse(f'<tr><td colspan="7" class="px-5 py-2 text-red-500 text-xs">{exc}</td></tr>')

    return templates.TemplateResponse(
        "purchase_orders/_line_row.html",
        {"request": request, "line": line},
    )
