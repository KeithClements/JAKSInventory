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

from app.constants import InvoiceStatus
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment


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
            ref = pmt.reference_number or pmt.payment_method or "Payment"
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

        aging: dict[str, float] = {
            "current": 0.0,
            "1_30": 0.0,
            "31_60": 0.0,
            "61_90": 0.0,
            "over_90": 0.0,
        }
        for inv in open_invoices:
            bal = inv.balance_due
            if bal <= 0:
                continue
            if inv.due_date is None:
                aging["current"] = round(aging["current"] + bal, 2)
                continue
            due = inv.due_date.date() if isinstance(inv.due_date, datetime) else inv.due_date
            days_late = (as_of - due).days
            if days_late <= 0:
                aging["current"] = round(aging["current"] + bal, 2)
            elif days_late <= 30:
                aging["1_30"] = round(aging["1_30"] + bal, 2)
            elif days_late <= 60:
                aging["31_60"] = round(aging["31_60"] + bal, 2)
            elif days_late <= 90:
                aging["61_90"] = round(aging["61_90"] + bal, 2)
            else:
                aging["over_90"] = round(aging["over_90"] + bal, 2)

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
