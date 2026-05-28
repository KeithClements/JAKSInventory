from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract, func
from sqlalchemy.orm import Session, joinedload

from app.constants import CoreStatus, InvoiceStatus, QuoteOutcome, QuoteStatus, SOStatus
from app.deps import get_db
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.models.quote import Quote, QuoteLine, SalesOrder
from app.models.purchase_order import PurchaseOrder
from app.models.core import CoreCharge
from app.models.product import Product
from app.models.customer import CustomerCallLog

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

    # AR balance + overdue count — load open/partial invoices with their lines
    # and payment allocations in 3 queries (joinedload avoids N+1 per invoice).
    open_invoices = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.lines),
            joinedload(Invoice.allocations),
        )
        .filter(Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]))
        .all()
    )
    ar_balance = round(sum(inv.balance_due for inv in open_invoices), 2)
    overdue_count = sum(1 for inv in open_invoices if inv.is_overdue)

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

    # Open POs (draft / verbal_order / sent / partial)
    open_pos = (
        db.query(func.count(PurchaseOrder.id))
        .filter(PurchaseOrder.status.in_(["draft", "verbal_order", "sent", "partial"]))
        .scalar() or 0
    )

    # Core charges awaiting customer return (OPEN or PARTIAL)
    open_cores = (
        db.query(func.count(CoreCharge.id))
        .filter(CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]))
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

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
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
        "today": today,
        "monthly_labels_json": json.dumps(monthly_labels),
        "monthly_totals_json": json.dumps(monthly_totals),
        "monthly_current": monthly_totals[-1] if monthly_totals else 0.0,
    })
