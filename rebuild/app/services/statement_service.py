"""
app/services/statement_service.py
===================================
Generates customer account statements for a given date range.

A statement shows:
  - Opening balance as of period_start (unpaid invoice balance before that date)
  - All invoices created within the period (date, invoice#, amount)
  - All payments received within the period (date, method/ref, amount)
  - Running balance column
  - Closing balance
  - AR aging buckets as of as_of_date

Typical use: monthly statements for net-terms customers.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TypedDict

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import CoreDirection, CoreStatus, InvoiceStatus
from app.models.core import CoreCharge
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment
from app.services.ar_aging_utils import as_date, zero_buckets, bucket_for


class StatementLine(TypedDict):
    txn_date: date
    txn_type: str          # "invoice" | "payment" | "credit_memo"
    reference: str
    charges: float         # positive = amount charged
    credits: float         # positive = amount credited / paid
    running_balance: float


class StatementData(TypedDict):
    customer: Customer
    as_of: date
    period_start: date
    period_end: date
    opening_balance: float
    lines: list[StatementLine]
    closing_balance: float
    aging: dict            # current / 1_30 / 31_60 / 61_90 / over_90
    total_charges: float
    total_credits: float


class StatementService:

    def __init__(self, db: Session):
        self.db = db

    def generate_statement(
        self,
        customer_id: int,
        period_start: date,
        period_end: date,
        as_of: date | None = None,
    ) -> StatementData:
        """
        Build a complete statement dict for the customer and date range.

        period_start / period_end define the activity shown on the statement.
        as_of defaults to period_end and is used for aging calculation.
        """
        if as_of is None:
            as_of = period_end

        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            raise ValueError(f"Customer {customer_id} not found")

        # ── Opening balance — invoices with balance_due issued BEFORE period_start ─
        pre_period_invoices = (
            self.db.query(Invoice)
            .options(joinedload(Invoice.allocations))
            .filter(
                Invoice.customer_id == customer_id,
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
                func.date(Invoice.created_at) < period_start,
            )
            .all()
        )
        opening_balance = round(sum(inv.balance_due for inv in pre_period_invoices), 2)

        # ── Activity within period ─────────────────────────────────────────────
        period_invoices = (
            self.db.query(Invoice)
            .options(joinedload(Invoice.allocations))
            .filter(
                Invoice.customer_id == customer_id,
                Invoice.status.in_([
                    InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID
                ]),
                func.date(Invoice.created_at) >= period_start,
                func.date(Invoice.created_at) <= period_end,
            )
            .order_by(Invoice.created_at)
            .all()
        )

        period_payments = (
            self.db.query(Payment)
            .filter(
                Payment.customer_id == customer_id,
                Payment.direction == "incoming_from_customer",
                func.date(Payment.payment_date) >= period_start,
                func.date(Payment.payment_date) <= period_end,
            )
            .order_by(Payment.payment_date)
            .all()
        )

        # Merge and sort all transactions by date
        raw_txns: list[tuple[date, str, str, float, float]] = []
        # (date, type, reference, charges, credits)

        for inv in period_invoices:
            inv_date = inv.created_at.date() if isinstance(inv.created_at, datetime) else inv.created_at
            raw_txns.append((inv_date, "invoice", inv.invoice_number, inv.total, 0.0))

        for pmt in period_payments:
            pmt_date = pmt.payment_date.date() if isinstance(pmt.payment_date, datetime) else pmt.payment_date
            ref = pmt.check_number or pmt.payment_method or "Payment"
            raw_txns.append((pmt_date, "payment", ref, 0.0, pmt.amount_received))

        raw_txns.sort(key=lambda t: t[0])

        # Build running balance lines
        running = opening_balance
        lines: list[StatementLine] = []
        total_charges = 0.0
        total_credits = 0.0

        for txn_date, txn_type, reference, charges, credits in raw_txns:
            running = round(running + charges - credits, 2)
            total_charges = round(total_charges + charges, 2)
            total_credits = round(total_credits + credits, 2)
            lines.append(StatementLine(
                txn_date=txn_date,
                txn_type=txn_type,
                reference=reference,
                charges=charges,
                credits=credits,
                running_balance=running,
            ))

        closing_balance = round(opening_balance + total_charges - total_credits, 2)

        # ── Aging as of as_of date ────────────────────────────────────────────
        # All open invoices as of as_of (issued on or before as_of, not yet paid)
        open_invoices = (
            self.db.query(Invoice)
            .options(joinedload(Invoice.allocations))
            .filter(
                Invoice.customer_id == customer_id,
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
                func.date(Invoice.created_at) <= as_of,
            )
            .all()
        )

        # Bucket open balances using the shared ar_aging_utils helpers so that
        # the statement aging agrees exactly with the AR aging report and the
        # customer balance widget (same boundaries, same code).
        aging: dict[str, float] = zero_buckets()
        for inv in open_invoices:
            bal = inv.balance_due
            if bal <= 0:
                continue
            due = as_date(inv.due_date)
            if due is None:
                aging["current"] = round(aging["current"] + bal, 2)
                continue
            days_late = (as_of - due).days
            b = bucket_for(days_late)
            aging[b] = round(aging[b] + bal, 2)

        return StatementData(
            customer=customer,
            as_of=as_of,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance,
            lines=lines,
            closing_balance=closing_balance,
            aging=aging,
            total_charges=total_charges,
            total_credits=total_credits,
        )

    # ── Customer balance summary (used by Quote + Invoice workspace headers) ──

    def get_customer_balance_summary(self, customer_id: int) -> dict:
        """
        Returns a lightweight dict for the workspace balance chips.
        No N+1 — four targeted queries, returns plain values (no ORM objects).
        """
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            return {
                "open_balance": 0.0,
                "overdue_balance": 0.0,
                "credit_balance": 0.0,
                "credit_limit": 0.0,
                "payment_terms": "",
                "cores_owed_qty": 0,
                "last_payment_date": None,
                "open_invoice_count": 0,
            }

        open_invoices = (
            self.db.query(Invoice)
            .options(joinedload(Invoice.allocations))
            .filter(
                Invoice.customer_id == customer_id,
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
            )
            .all()
        )
        open_balance = round(sum(inv.balance_due for inv in open_invoices), 2)
        overdue_balance = round(
            sum(inv.balance_due for inv in open_invoices if inv.is_overdue), 2
        )

        core_charges = (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.customer_id == customer_id,
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
            )
            .all()
        )
        cores_owed_qty = sum(
            max(0, c.qty_charged - c.qty_returned) for c in core_charges
        )

        last_pmt = (
            self.db.query(Payment.payment_date)
            .filter(
                Payment.customer_id == customer_id,
                Payment.direction == "incoming_from_customer",
            )
            .order_by(Payment.payment_date.desc())
            .first()
        )
        last_payment_date: date | None = None
        if last_pmt:
            raw = last_pmt[0]
            last_payment_date = raw.date() if isinstance(raw, datetime) else raw

        return {
            "open_balance": open_balance,
            "overdue_balance": overdue_balance,
            "credit_balance": round(customer.credit_balance, 2),
            "credit_limit": round(customer.credit_limit, 2),
            "payment_terms": customer.payment_terms,
            "cores_owed_qty": cores_owed_qty,
            "last_payment_date": last_payment_date,
            "open_invoice_count": len(open_invoices),
        }
