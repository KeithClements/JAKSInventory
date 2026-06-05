from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract, func
from sqlalchemy.orm import Session, joinedload

from app.constants import CoreDirection, CoreStatus, InvoiceStatus, POStatus, QuoteOutcome, QuoteStatus, SOStatus, QuoteFollowupStatus
from app.deps import get_db
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.models.quote import Quote, QuoteLine, SalesOrder
from app.models.purchase_order import PurchaseOrder
from app.models.core import CoreCharge
from app.models.product import Product
from app.models.customer import Customer, CustomerCallLog
from app.services.report_service import ReportService

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    today = date.today()

    # Today's collected payments
    today_payments = (
        db.query(func.sum(Payment.amount_received))
        .filter(func.date(Payment.payment_date) == today)
        .scalar() or 0.0
    )

    # AR balance — ReportService is the source of truth (matches /reports/ar-aging)
    try:
        ar_balance = ReportService(db).get_ar_aging()["totals"]["total"]
    except Exception:
        ar_balance = 0.0

    # Overdue invoices — simple count: open/partial invoices past their due date
    overdue_count = (
        db.query(func.count(Invoice.id))
        .filter(
            Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
            Invoice.due_date.isnot(None),
            Invoice.due_date < datetime.utcnow(),
        )
        .scalar() or 0
    )

    # Open quotes (draft)
    open_quotes = (
        db.query(func.count(Quote.id))
        .filter(Quote.status == "draft")
        .scalar() or 0
    )

    # Open SOs (open / partial / hold — not fulfilled, invoiced, or cancelled)
    open_sos = (
        db.query(func.count(SalesOrder.id))
        .filter(SalesOrder.status.in_([SOStatus.OPEN, SOStatus.PARTIAL, SOStatus.HOLD]))
        .scalar() or 0
    )

    # Open POs — same statuses as ReportService so dashboard and reports page agree
    open_pos = (
        db.query(func.count(PurchaseOrder.id))
        .filter(PurchaseOrder.status.in_([
            POStatus.VERBAL_ORDER, POStatus.DRAFT, POStatus.SENT, POStatus.PARTIAL,
        ]))
        .scalar() or 0
    )

    # Core charges awaiting customer return — same filter as ReportService
    # (OPEN/PARTIAL status + customer direction only, not vendor cores)
    open_cores = (
        db.query(func.count(CoreCharge.id))
        .filter(
            CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
            CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
        )
        .scalar() or 0
    )

    # Low stock products (on_hand <= reorder_point, reorder_point > 0)
    low_stock = (
        db.query(Product)
        .filter(
            Product.reorder_point > 0,
            Product.qty_on_hand <= Product.reorder_point,
            Product.is_active == True,  # noqa: E712
        )
        .order_by(Product.qty_on_hand)
        .limit(10)
        .all()
    )

    # Recent invoices (last 10, excluding void)
    recent_invoices = (
        db.query(Invoice)
        .filter(Invoice.status != InvoiceStatus.VOID)
        .order_by(Invoice.created_at.desc())
        .limit(10)
        .all()
    )

    # Recent call logs
    recent_calls = (
        db.query(CustomerCallLog)
        .order_by(CustomerCallLog.logged_at.desc())
        .limit(5)
        .all()
    )

    # Research queue — quote lines flagged as "researching" or "waiting_dealer"
    # on active (non-converted, non-declined) quotes.
    research_queue = (
        db.query(QuoteLine)
        .options(joinedload(QuoteLine.quote).joinedload(Quote.customer))
        .join(QuoteLine.quote)
        .join(Quote.customer)
        .filter(
            QuoteLine.research_status.in_(["researching", "waiting_dealer"]),
            Quote.status.notin_([QuoteStatus.CONVERTED, QuoteStatus.DECLINED]),
        )
        .order_by(QuoteLine.id.asc())
        .limit(10)
        .all()
    )

    # Quotes with overdue follow-ups (follow_up_date in the past, outcome still pending)
    now = datetime.utcnow()
    overdue_followups = (
        db.query(Quote)
        .join(Quote.customer)
        .filter(
            Quote.follow_up_date <= now,
            Quote.outcome == QuoteOutcome.PENDING,
            Quote.status.notin_([QuoteStatus.CONVERTED, QuoteStatus.DECLINED]),
        )
        .order_by(Quote.follow_up_date.asc())
        .limit(10)
        .all()
    )

    # Monthly revenue (last 6 months) — sum invoice_lines since Invoice.total is a @property
    # Formula: unit_price * qty * (1 - discount_pct / 100), grouped by invoice month.
    monthly_labels: list[str] = []
    monthly_totals: list[float] = []
    for i in range(5, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        total = (
            db.query(
                func.sum(
                    InvoiceLine.unit_price * InvoiceLine.qty
                    * (1 - InvoiceLine.discount_pct / 100)
                )
            )
            .join(InvoiceLine.invoice)
            .filter(
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID]),
                extract("year", Invoice.created_at) == y,
                extract("month", Invoice.created_at) == m,
            )
            .scalar() or 0.0
        )
        monthly_labels.append(calendar.month_abbr[m])
        monthly_totals.append(round(float(total), 2))

    # Top 5 customers by lifetime sales (open invoiced + paid, net of credits).
    # We approximate here as sum of non-void invoice totals — the full P2-Q1
    # net-of-credits figure is on CustomerMetricsService for per-customer views.
    _top_raw = (
        db.query(
            Invoice.customer_id,
            func.sum(
                InvoiceLine.unit_price * InvoiceLine.qty
                * (1 - InvoiceLine.discount_pct / 100)
            ).label("total"),
        )
        .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
        .filter(Invoice.status.notin_([InvoiceStatus.VOID]))
        .group_by(Invoice.customer_id)
        .order_by(func.sum(
            InvoiceLine.unit_price * InvoiceLine.qty
            * (1 - InvoiceLine.discount_pct / 100)
        ).desc())
        .limit(5)
        .all()
    )
    _cust_map = {
        c.id: c for c in (
            db.query(Customer)
            .filter(Customer.id.in_([r.customer_id for r in _top_raw]))
            .all()
        )
    }
    top_customers = [
        {
            "customer": _cust_map.get(r.customer_id),
            "lifetime_sales": round(float(r.total or 0), 2),
        }
        for r in _top_raw
        if _cust_map.get(r.customer_id)
    ]

    # Open quote follow-ups grouped by follow_up_status (the existing taxonomy).
    _followup_raw = (
        db.query(Quote.follow_up_status, func.count(Quote.id))
        .filter(
            Quote.outcome == QuoteOutcome.PENDING,
            Quote.status.notin_([QuoteStatus.CONVERTED, QuoteStatus.DECLINED]),
            Quote.follow_up_status.isnot(None),
        )
        .group_by(Quote.follow_up_status)
        .all()
    )
    open_followups = {
        str(status or ""): int(count)
        for status, count in _followup_raw
    }
    open_followups_total = sum(open_followups.values())

    return templates.TemplateResponse(request, "dashboard.html", {
        "today_payments": today_payments,
        "ar_balance": ar_balance,
        "overdue_count": overdue_count,
        "open_quotes": open_quotes,
        "open_sos": open_sos,
        "open_pos": open_pos,
        "open_cores": open_cores,
        "low_stock": low_stock,
        "recent_invoices": recent_invoices,
        "recent_calls": recent_calls,
        "overdue_followups": overdue_followups,
        "research_queue": research_queue,
        "top_customers": top_customers,          # Seam 4
        "open_followups": open_followups,         # Seam 4 — dict {status: count}
        "open_followups_total": open_followups_total,
        "today": today,
        "monthly_labels_json": json.dumps(monthly_labels),
        "monthly_totals_json": json.dumps(monthly_totals),
        "monthly_current": monthly_totals[-1] if monthly_totals else 0.0,
    })
