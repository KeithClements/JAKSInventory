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

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import TypedDict

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import CoreDirection, CoreStatus, CreditMemoStatus, InvoiceStatus
from app.models.core import CoreCharge
from app.models.credit_memo import CreditMemo
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment
from app.models.statement import CustomerStatement
from app.services.ar_aging_utils import AGING_BUCKETS, as_date, zero_buckets, bucket_for
from app.settings_utils import bump_counter


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

        # §21 — credit memos reduce net A/R but are absent from invoice balances
        # (balance_due = total − payment allocations only; CMs are independent
        # documents). A customer with a warranty/core/return credit therefore
        # showed an inflated statement balance. Net pre-period CMs into the
        # opening balance so the running balance starts from the true figure.
        _ACTIVE_CM = [
            CreditMemoStatus.OPEN, CreditMemoStatus.APPLIED, CreditMemoStatus.PARTIAL,
        ]
        pre_period_cm_total = round(
            sum(
                cm.total_amount for cm in (
                    self.db.query(CreditMemo)
                    .filter(
                        CreditMemo.customer_id == customer_id,
                        CreditMemo.status.in_(_ACTIVE_CM),
                        func.date(CreditMemo.created_at) < period_start,
                    )
                    .all()
                )
            ),
            2,
        )
        opening_balance = round(opening_balance - pre_period_cm_total, 2)

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

        # §21 — credit memos issued within the period, shown as credit lines.
        period_credit_memos = (
            self.db.query(CreditMemo)
            .filter(
                CreditMemo.customer_id == customer_id,
                CreditMemo.status.in_(_ACTIVE_CM),
                func.date(CreditMemo.created_at) >= period_start,
                func.date(CreditMemo.created_at) <= period_end,
            )
            .order_by(CreditMemo.created_at)
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

        for cm in period_credit_memos:
            cm_date = cm.created_at.date() if isinstance(cm.created_at, datetime) else cm.created_at
            raw_txns.append((cm_date, "credit_memo", cm.cm_number, 0.0, cm.total_amount))

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

    # ── R3 — Statement persistence (immutable record of what was sent) ────────

    def persist_statement(
        self,
        stmt: StatementData,
        generated_by_user_id: int | None = None,
    ) -> CustomerStatement:
        """
        Persist a CustomerStatement row recording exactly what `stmt` showed.

        Called at the print / PDF moment — the point a statement is considered
        "sent to the customer" — so a disputed statement can be re-rendered
        from its stored snapshot even after the underlying invoices and
        payments change.

        IDEMPOTENCY RULE: regenerating the SAME customer + SAME period
        (period_start AND period_end both equal) on the SAME calendar day
        reuses and refreshes the existing row — repeated print/PDF clicks do
        not mint duplicate statement numbers. A different period (either
        bound) or a new calendar day mints a fresh number from the
        next_statement_number counter (ST-YYYY-NNNN).

        Commits — callers are GET print/PDF routes that otherwise never commit.
        """
        customer = stmt["customer"]
        today = date.today()
        aging = stmt["aging"]

        row = (
            self.db.query(CustomerStatement)
            .filter(
                CustomerStatement.customer_id == customer.id,
                CustomerStatement.date_range_start == stmt["period_start"],
                CustomerStatement.date_range_end == stmt["period_end"],
                func.date(CustomerStatement.generated_at) == today.isoformat(),
            )
            .order_by(CustomerStatement.id.desc())
            .first()
        )
        if row is None:
            number = bump_counter(self.db, "next_statement_number", "ST", today.year)
            row = CustomerStatement(
                statement_number=number,
                customer_id=customer.id,
                date_range_start=stmt["period_start"],
                date_range_end=stmt["period_end"],
            )
            self.db.add(row)

        row.generated_at = datetime.now()
        if generated_by_user_id is not None:
            row.generated_by_user_id = generated_by_user_id
        row.opening_balance = stmt["opening_balance"]
        row.closing_balance = stmt["closing_balance"]
        row.total_invoiced = stmt["total_charges"]
        row.total_paid = stmt["total_credits"]
        # Bucket keys follow ar_aging_utils.AGING_BUCKETS; the model's legacy
        # due_120 column is dead (see app/models/statement.py) — over_90 is
        # the terminal bucket.
        row.current_due = float(aging.get("current", 0.0))
        row.due_30 = float(aging.get("1_30", 0.0))
        row.due_60 = float(aging.get("31_60", 0.0))
        row.due_90 = float(aging.get("61_90", 0.0))
        row.over_90 = float(aging.get("over_90", 0.0))
        row.snapshot_json = json.dumps(self._snapshot_payload(stmt), sort_keys=True)
        self.db.commit()
        self.db.refresh(row)
        return row

    @staticmethod
    def _snapshot_payload(stmt: StatementData) -> dict:
        """JSON-safe copy of the full statement (dates → isoformat strings)."""
        c = stmt["customer"]
        return {
            "customer": {
                "id": c.id,
                "company_name": c.company_name,
                "address_line1": getattr(c, "address_line1", None),
                "address_line2": getattr(c, "address_line2", None),
                "city": getattr(c, "city", None),
                "state": getattr(c, "state", None),
                "zip_code": getattr(c, "zip_code", None),
                "phone": getattr(c, "phone", None),
            },
            "as_of": stmt["as_of"].isoformat(),
            "period_start": stmt["period_start"].isoformat(),
            "period_end": stmt["period_end"].isoformat(),
            "opening_balance": stmt["opening_balance"],
            "closing_balance": stmt["closing_balance"],
            "total_charges": stmt["total_charges"],
            "total_credits": stmt["total_credits"],
            "aging": {k: float(stmt["aging"].get(k, 0.0)) for k in AGING_BUCKETS},
            "lines": [
                {
                    "txn_date": ln["txn_date"].isoformat(),
                    "txn_type": ln["txn_type"],
                    "reference": ln["reference"],
                    "charges": ln["charges"],
                    "credits": ln["credits"],
                    "running_balance": ln["running_balance"],
                }
                for ln in stmt["lines"]
            ],
        }

    def get_statement(self, statement_id: int) -> dict | None:
        """Parsed snapshot of a persisted statement (R3 re-print path).

        Returns the statement_print.html-shaped ctx rebuilt from the stored
        snapshot_json — i.e. exactly what was sent, regardless of how the
        underlying invoices/payments have changed since. None if the row
        doesn't exist or predates snapshots (legacy rows)."""
        row = self.db.get(CustomerStatement, statement_id)
        if row is None:
            return None
        return self.snapshot_to_render_ctx(row)

    def get_statement_history(
        self, customer_id: int, limit: int = 24
    ) -> list[CustomerStatement]:
        """Saved statements for a customer, newest first (history panel)."""
        return (
            self.db.query(CustomerStatement)
            .filter(CustomerStatement.customer_id == customer_id)
            .order_by(CustomerStatement.generated_at.desc(), CustomerStatement.id.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def snapshot_to_render_ctx(row: CustomerStatement) -> dict | None:
        """
        Rebuild a statement_print.html-shaped dict from a saved snapshot.

        READ-ONLY: never touches live invoice/payment data — this is the
        dispute-resolution view of exactly what was sent. Returns None for
        legacy rows persisted without a snapshot.
        """
        if not row.snapshot_json:
            return None
        snap = json.loads(row.snapshot_json)
        return {
            "customer": SimpleNamespace(**snap.get("customer", {})),
            "as_of": date.fromisoformat(snap["as_of"]),
            "period_start": date.fromisoformat(snap["period_start"]),
            "period_end": date.fromisoformat(snap["period_end"]),
            "opening_balance": snap["opening_balance"],
            "closing_balance": snap["closing_balance"],
            "total_charges": snap["total_charges"],
            "total_credits": snap["total_credits"],
            "aging": snap["aging"],
            "lines": [
                {**ln, "txn_date": date.fromisoformat(ln["txn_date"])}
                for ln in snap["lines"]
            ],
        }

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
