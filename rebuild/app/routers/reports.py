"""
app/routers/reports.py
======================
Financial and operational reports.

Reports available:
  /reports/              — Reports landing / AR Aging
  /reports/sales/        — Sales Summary (by period)
  /reports/inventory/    — Inventory Snapshot

All queries are read-only — no DB mutations here.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import FulfillmentSource, InvoiceStatus, POStatus, SOLineStatus, SOStatus
from app.deps import get_db
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine
from app.models.quote import SalesOrder, SOLine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── AR Aging ──────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def reports_ar_aging(request: Request, db: Session = Depends(get_db)):
    """
    AR Aging report — groups outstanding invoice balances by age bucket
    per customer: Current / 1-30 / 31-60 / 61-90 / 90+ days past due.
    """
    today = date.today()

    # Load open/partial invoices with lines and payment allocations (avoids N+1)
    invoices = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.lines),
            joinedload(Invoice.allocations),
            joinedload(Invoice.customer),
        )
        .filter(Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]))
        .all()
    )

    # Build per-customer aging buckets
    # buckets: current, 1_30, 31_60, 61_90, over_90, total
    aging: dict[int, dict] = defaultdict(lambda: {
        "customer": None,
        "current": 0.0,
        "1_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "over_90": 0.0,
        "total": 0.0,
        "invoices": [],
    })

    for inv in invoices:
        balance = inv.balance_due
        if balance <= 0:
            continue

        row = aging[inv.customer_id]
        row["customer"] = inv.customer
        row["total"] = round(row["total"] + balance, 2)
        row["invoices"].append(inv)

        if inv.due_date is None:
            # No due date → treat as current
            row["current"] = round(row["current"] + balance, 2)
            continue

        # Compute days past due (negative = not yet due)
        due = inv.due_date.date() if isinstance(inv.due_date, datetime) else inv.due_date
        days_late = (today - due).days

        if days_late <= 0:
            row["current"] = round(row["current"] + balance, 2)
        elif days_late <= 30:
            row["1_30"] = round(row["1_30"] + balance, 2)
        elif days_late <= 60:
            row["31_60"] = round(row["31_60"] + balance, 2)
        elif days_late <= 90:
            row["61_90"] = round(row["61_90"] + balance, 2)
        else:
            row["over_90"] = round(row["over_90"] + balance, 2)

    # Sort by total descending
    aging_rows = sorted(aging.values(), key=lambda r: r["total"], reverse=True)

    # Column totals
    totals = {
        "current":  round(sum(r["current"]  for r in aging_rows), 2),
        "1_30":     round(sum(r["1_30"]     for r in aging_rows), 2),
        "31_60":    round(sum(r["31_60"]    for r in aging_rows), 2),
        "61_90":    round(sum(r["61_90"]    for r in aging_rows), 2),
        "over_90":  round(sum(r["over_90"]  for r in aging_rows), 2),
        "total":    round(sum(r["total"]    for r in aging_rows), 2),
    }

    return templates.TemplateResponse(
        "reports/ar_aging.html",
        {
            "request": request,
            "today": today,
            "aging_rows": aging_rows,
            "totals": totals,
        },
    )


# ── Sales Summary ─────────────────────────────────────────────────────────────

@router.get("/sales/", response_class=HTMLResponse)
def reports_sales(
    request: Request,
    year: int = 0,
    month: int = 0,
    db: Session = Depends(get_db),
):
    """
    Sales Summary — revenue by customer for a selected month,
    plus period-over-period summary cards.
    """
    today = date.today()
    if not year:
        year = today.year
    if not month:
        month = today.month

    # Period bounds
    period_start = date(year, month, 1)
    if month == 12:
        period_end = date(year + 1, 1, 1)
    else:
        period_end = date(year, month + 1, 1)

    # Previous period
    prev_start = (period_start - timedelta(days=1)).replace(day=1)
    prev_end = period_start

    def _period_revenue(start: date, end: date) -> float:
        """Sum of invoice totals (paid + partial + open) for a date range."""
        invs = (
            db.query(Invoice)
            .options(joinedload(Invoice.lines))
            .filter(
                Invoice.status.in_([
                    InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID
                ]),
                func.date(Invoice.created_at) >= start,
                func.date(Invoice.created_at) < end,
            )
            .all()
        )
        return round(sum(inv.total for inv in invs), 2)

    def _period_collected(start: date, end: date) -> float:
        """Sum of payments actually received in a date range."""
        result = (
            db.query(func.sum(Payment.amount_received))
            .filter(
                func.date(Payment.payment_date) >= start,
                func.date(Payment.payment_date) < end,
            )
            .scalar() or 0.0
        )
        return round(float(result), 2)

    # Current period stats
    period_revenue = _period_revenue(period_start, period_end)
    period_collected = _period_collected(period_start, period_end)
    prev_revenue = _period_revenue(prev_start, prev_end)

    # YTD
    ytd_start = date(year, 1, 1)
    ytd_revenue = _period_revenue(ytd_start, today + timedelta(days=1))
    ytd_collected = _period_collected(ytd_start, today + timedelta(days=1))

    # Sales by customer for the selected period
    period_invoices = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.lines),
            joinedload(Invoice.customer),
            joinedload(Invoice.allocations),
        )
        .filter(
            Invoice.status.in_([
                InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID
            ]),
            func.date(Invoice.created_at) >= period_start,
            func.date(Invoice.created_at) < period_end,
        )
        .all()
    )

    # Group by customer
    by_customer: dict[int, dict] = defaultdict(lambda: {
        "customer": None,
        "invoice_count": 0,
        "revenue": 0.0,
        "collected": 0.0,
    })
    for inv in period_invoices:
        row = by_customer[inv.customer_id]
        row["customer"] = inv.customer
        row["invoice_count"] += 1
        row["revenue"] = round(row["revenue"] + inv.total, 2)
        row["collected"] = round(row["collected"] + inv.amount_paid, 2)

    customer_rows = sorted(by_customer.values(), key=lambda r: r["revenue"], reverse=True)

    # Build month selector options (last 24 months)
    month_options = []
    cur = today.replace(day=1)
    for _ in range(24):
        month_options.append((cur.year, cur.month, cur.strftime("%B %Y")))
        if cur.month == 1:
            cur = cur.replace(year=cur.year - 1, month=12)
        else:
            cur = cur.replace(month=cur.month - 1)

    return templates.TemplateResponse(
        "reports/sales.html",
        {
            "request": request,
            "year": year,
            "month": month,
            "period_label": period_start.strftime("%B %Y"),
            "period_revenue": period_revenue,
            "period_collected": period_collected,
            "prev_revenue": prev_revenue,
            "ytd_revenue": ytd_revenue,
            "ytd_collected": ytd_collected,
            "customer_rows": customer_rows,
            "month_options": month_options,
            "today": today,
        },
    )


# ── Inventory Snapshot ────────────────────────────────────────────────────────

@router.get("/inventory/", response_class=HTMLResponse)
def reports_inventory(request: Request, db: Session = Depends(get_db)):
    """
    Inventory Snapshot — stock on hand by product, sorted by value.
    Shows qty on hand, cost, and estimated inventory value.
    """
    products = (
        db.query(Product)
        .filter(Product.is_active == True, Product.qty_on_hand > 0)  # noqa: E712
        .order_by((Product.qty_on_hand * Product.cost).desc())
        .all()
    )

    total_units = sum(p.qty_on_hand for p in products)
    total_value = round(sum((p.qty_on_hand * (p.cost or 0.0)) for p in products), 2)
    total_retail = round(
        sum((p.qty_on_hand * p.selling_price) for p in products), 2
    )

    return templates.TemplateResponse(
        "reports/inventory.html",
        {
            "request": request,
            "products": products,
            "total_units": total_units,
            "total_value": total_value,
            "total_retail": total_retail,
            "today": date.today(),
        },
    )


# ── Open POs + Backorders ─────────────────────────────────────────────────────

@router.get("/open-pos/", response_class=HTMLResponse)
def reports_open_pos(request: Request, db: Session = Depends(get_db)):
    """
    Open POs & Backorders report — two sections:
      1. Purchase orders that are not yet fully received (VERBAL_ORDER / DRAFT / SENT / PARTIAL)
      2. SO lines with fulfillment_source = backorder awaiting stock
    """
    today = date.today()

    # Section 1 — Open Purchase Orders
    open_pos = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.lines).joinedload(POLine.product),
            joinedload(PurchaseOrder.vendor),
        )
        .filter(
            PurchaseOrder.status.in_([
                POStatus.VERBAL_ORDER,
                POStatus.DRAFT,
                POStatus.SENT,
                POStatus.PARTIAL,
            ])
        )
        .order_by(PurchaseOrder.expected_at.asc().nulls_last(), PurchaseOrder.created_at.asc())
        .all()
    )

    # Compute outstanding $ per PO
    po_rows = []
    for po in open_pos:
        outstanding_lines = [ln for ln in po.lines if ln.qty_outstanding > 0]
        outstanding_value = round(
            sum(ln.unit_cost * ln.qty_outstanding for ln in outstanding_lines), 2
        )
        days_since_order = None
        if po.ordered_at:
            ordered_date = po.ordered_at.date() if isinstance(po.ordered_at, datetime) else po.ordered_at
            days_since_order = (today - ordered_date).days
        overdue = (
            po.expected_at is not None
            and (po.expected_at.date() if isinstance(po.expected_at, datetime) else po.expected_at) < today
        )
        po_rows.append({
            "po": po,
            "outstanding_lines": outstanding_lines,
            "outstanding_value": outstanding_value,
            "days_since_order": days_since_order,
            "overdue": overdue,
        })

    po_total_value = round(sum(r["outstanding_value"] for r in po_rows), 2)

    # Section 2 — Backordered SO lines
    backorder_lines = (
        db.query(SOLine)
        .options(
            joinedload(SOLine.so).joinedload(SalesOrder.customer),
            joinedload(SOLine.product),
        )
        .join(SalesOrder)
        .filter(
            SalesOrder.status.in_([SOStatus.OPEN, SOStatus.PARTIAL, SOStatus.HOLD]),
            SOLine.fulfillment_source == FulfillmentSource.BACKORDER,
            SOLine.line_status != SOLineStatus.INVOICED,
            SOLine.line_status != SOLineStatus.CANCELLED,
        )
        .order_by(SalesOrder.created_at.asc())
        .all()
    )

    backorder_value = round(
        sum(ln.unit_price * ln.qty_remaining for ln in backorder_lines), 2
    )

    return templates.TemplateResponse(
        "reports/open_pos.html",
        {
            "request": request,
            "today": today,
            "po_rows": po_rows,
            "po_total_value": po_total_value,
            "backorder_lines": backorder_lines,
            "backorder_value": backorder_value,
            "POStatus": POStatus,
        },
    )
