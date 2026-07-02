"""
app/routers/serials.py
======================
Serial Number Lookup (§23.3 Phase 3) — cylinder-head chain of custody.

READ-ONLY: one screen answering "where did this serialized unit come from and
where did it go?" for warranty/liability work. Each row shows the unit's full
chain: received (PO + vendor + date, via POReceiptLine), current status, sold
(invoice + customer + date, via InvoiceLine), and any warranty claims filed
against that serial (WarrantyClaimLine.serial_number).

Write paths live in SerialService (receipt → IN_STOCK, invoice finalize →
SOLD, invoice void → released); this router never mutates.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.deps import get_db
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product, ProductSerialNumber
from app.models.purchase_order import POReceipt, POReceiptLine, PurchaseOrder
from app.models.vendor import Vendor
from app.models.warranty import WarrantyClaim, WarrantyClaimLine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/serials", tags=["serials"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

_MAX_ROWS = 200


@router.get("/", response_class=HTMLResponse)
def serial_lookup(request: Request, q: str = "", db: Session = Depends(get_db)):
    qn = q.strip()

    base = db.query(ProductSerialNumber).options(
        joinedload(ProductSerialNumber.product)
    )
    if qn:
        like = f"%{qn}%"
        base = base.join(Product, ProductSerialNumber.product_id == Product.id).filter(
            or_(
                ProductSerialNumber.serial_number.ilike(like),
                Product.sku.ilike(like),
            )
        )
    # Newest first — with no search this doubles as "recently received serials"
    serials = base.order_by(ProductSerialNumber.id.desc()).limit(_MAX_ROWS).all()
    total_serials = db.query(func.count(ProductSerialNumber.id)).scalar() or 0

    # ── Chain of custody — three bulk loads, no per-row queries ──────────────

    # Received: receipt line → receipt (date, vendor) + PO number
    receipt_line_ids = [s.po_receipt_line_id for s in serials if s.po_receipt_line_id]
    received_by_line: dict[int, dict] = {}
    if receipt_line_ids:
        rl_rows = (
            db.query(
                POReceiptLine.id,
                POReceipt.received_at,
                POReceipt.vendor_id,
                PurchaseOrder.id,
                PurchaseOrder.po_number,
            )
            .join(POReceipt, POReceiptLine.receipt_id == POReceipt.id)
            .join(PurchaseOrder, POReceiptLine.po_id == PurchaseOrder.id)
            .filter(POReceiptLine.id.in_(receipt_line_ids))
            .all()
        )
        vendor_ids = {r[2] for r in rl_rows if r[2]}
        vendor_names = (
            dict(
                db.query(Vendor.id, Vendor.name)
                .filter(Vendor.id.in_(vendor_ids))
                .all()
            )
            if vendor_ids
            else {}
        )
        received_by_line = {
            rl_id: {
                "received_at": received_at,
                "vendor_name": vendor_names.get(vendor_id),
                "po_id": po_id,
                "po_number": po_number,
            }
            for rl_id, received_at, vendor_id, po_id, po_number in rl_rows
        }

    # Sold: invoice line → invoice (number, date) + customer
    invoice_line_ids = [s.invoice_line_id for s in serials if s.invoice_line_id]
    sold_by_line: dict[int, dict] = {}
    if invoice_line_ids:
        inv_rows = (
            db.query(
                InvoiceLine.id,
                Invoice.id,
                Invoice.invoice_number,
                Invoice.created_at,
                Invoice.customer_id,
            )
            .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
            .filter(InvoiceLine.id.in_(invoice_line_ids))
            .all()
        )
        customer_ids = {r[4] for r in inv_rows if r[4]}
        from app.models.customer import Customer
        customer_names = (
            dict(
                db.query(Customer.id, Customer.company_name)
                .filter(Customer.id.in_(customer_ids))
                .all()
            )
            if customer_ids
            else {}
        )
        sold_by_line = {
            ln_id: {
                "invoice_id": inv_id,
                "invoice_number": inv_number,
                "invoice_date": inv_created,
                "customer_id": cust_id,
                "customer_name": customer_names.get(cust_id),
            }
            for ln_id, inv_id, inv_number, inv_created, cust_id in inv_rows
        }

    # Warranty claims filed against these serials (free-text serial on the
    # claim line — matched case-insensitively; ProductSerialNumber stores upper)
    serial_values = {s.serial_number.upper() for s in serials if s.serial_number}
    claims_by_serial: dict[str, list[dict]] = {}
    if serial_values:
        wc_rows = (
            db.query(
                func.upper(WarrantyClaimLine.serial_number),
                WarrantyClaim.id,
                WarrantyClaim.claim_number,
                WarrantyClaim.status,
            )
            .join(WarrantyClaim, WarrantyClaimLine.warranty_claim_id == WarrantyClaim.id)
            .filter(func.upper(WarrantyClaimLine.serial_number).in_(serial_values))
            .all()
        )
        for sn_upper, claim_id, claim_number, claim_status in wc_rows:
            claims_by_serial.setdefault(sn_upper, []).append(
                {"id": claim_id, "claim_number": claim_number, "status": claim_status}
            )

    rows = [
        {
            "serial": s,
            "product": s.product,
            "received": received_by_line.get(s.po_receipt_line_id),
            "sold": sold_by_line.get(s.invoice_line_id),
            "claims": claims_by_serial.get((s.serial_number or "").upper(), []),
        }
        for s in serials
    ]

    return templates.TemplateResponse(
        request,
        "serials/lookup.html",
        {
            "rows": rows,
            "q": q,
            "total_serials": total_serials,
            "shown": len(rows),
            "max_rows": _MAX_ROWS,
        },
    )
