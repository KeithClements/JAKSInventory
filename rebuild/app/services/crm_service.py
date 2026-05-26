"""
app/services/crm_service.py
=============================
Customer Relationship Management — call logs, account health, credit balance.

OWNERSHIP:
  CRMService is the sole owner of Customer.credit_balance mutations.
  CoreService and WarrantyService delegate add_credit() / deduct_credit() here
  rather than writing credit_balance directly.

Phase 1 scope:
  - CustomerCallLog: log every interaction with outcome + notes
  - Account balance + overdue tracking for the customer dashboard
  - Customer credit balance management (warranty credits, core returns, overpayments)
  - A/R aging: flag overdue accounts + calculate interest

Phase 2 scope (stubs only):
  - QuoteFollowup scheduling + reminders
  - Automated follow-up queue
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func

from app.constants import AuditAction, EntityType, InvoiceStatus
from app.models.customer import Customer, CustomerCallLog
from app.models.invoice import Invoice, Payment
from app.services.base import BaseService


class CRMService(BaseService):

    # ── Call Log ──────────────────────────────────────────────────────────────

    def log_call(
        self,
        customer_id: int,
        call_type: str,
        outcome: str,
        notes: str = "",
        quote_id: int | None = None,
    ) -> CustomerCallLog:
        """
        Record a customer interaction.
        call_type: CallType value — 'inbound' | 'outbound' | 'email' | 'in_person'
        outcome:   CallOutcome value — 'quoted' | 'order_placed' | 'no_answer' | etc.
        """
        self._get_customer_or_404(customer_id)
        entry = CustomerCallLog(
            customer_id=customer_id,
            logged_by_id=self.current_user_id,
            call_type=call_type,
            outcome=outcome,
            quote_id=quote_id,
            notes=notes,
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def get_call_history(self, customer_id: int, limit: int = 50) -> list[CustomerCallLog]:
        return (
            self.db.query(CustomerCallLog)
            .filter(CustomerCallLog.customer_id == customer_id)
            .order_by(CustomerCallLog.logged_at.desc())
            .limit(limit)
            .all()
        )

    # ── Account Health ────────────────────────────────────────────────────────

    def get_account_balance(self, customer_id: int) -> dict:
        """
        Return a full account health snapshot for the customer dashboard.

          total_open:         sum of balance_due on all open/partial invoices
          overdue_amount:     sum of balance_due on invoices past their due_date
          oldest_overdue_days: days since the oldest unpaid due date (0 if none)
          credit_balance:     customer.credit_balance (warranty / core return credits)
          unapplied_payments: sum of payment.amount_unallocated for APPLIED payments
        """
        customer = self._get_customer_or_404(customer_id)
        now = datetime.utcnow()

        open_invoices = (
            self.db.query(Invoice)
            .filter(
                Invoice.customer_id == customer_id,
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
            )
            .all()
        )

        total_open = round(sum(inv.balance_due for inv in open_invoices), 2)

        overdue = [
            inv for inv in open_invoices
            if inv.due_date and inv.due_date < now
        ]
        overdue_amount = round(sum(inv.balance_due for inv in overdue), 2)

        oldest_overdue_days = 0
        if overdue:
            oldest = min(inv.due_date for inv in overdue)
            oldest_overdue_days = (now - oldest).days

        payments = (
            self.db.query(Payment)
            .filter(
                Payment.customer_id == customer_id,
                Payment.status == "applied",
            )
            .all()
        )
        unapplied_payments = round(sum(p.amount_unallocated for p in payments), 2)

        return {
            "total_open": total_open,
            "overdue_amount": overdue_amount,
            "oldest_overdue_days": oldest_overdue_days,
            "credit_balance": customer.credit_balance,
            "unapplied_payments": unapplied_payments,
        }

    def get_overdue_accounts(self, min_days_overdue: int = 1) -> list[dict]:
        """
        Return all customers with overdue balances.
        Used for the A/R aging report and dashboard alert.
        Each dict contains: customer_id, company_name, overdue_amount, oldest_days.
        """
        cutoff = datetime.utcnow() - timedelta(days=min_days_overdue - 1)
        overdue_invoices = (
            self.db.query(Invoice)
            .filter(
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
                Invoice.due_date < cutoff,
            )
            .all()
        )

        # Group by customer
        by_customer: dict[int, dict] = {}
        for inv in overdue_invoices:
            cid = inv.customer_id
            if cid not in by_customer:
                by_customer[cid] = {
                    "customer_id": cid,
                    "company_name": inv.customer.company_name if inv.customer else "",
                    "overdue_amount": 0.0,
                    "oldest_due_date": inv.due_date,
                }
            by_customer[cid]["overdue_amount"] = round(
                by_customer[cid]["overdue_amount"] + inv.balance_due, 2
            )
            if inv.due_date < by_customer[cid]["oldest_due_date"]:
                by_customer[cid]["oldest_due_date"] = inv.due_date

        now = datetime.utcnow()
        result = []
        for row in by_customer.values():
            row["oldest_days"] = (now - row["oldest_due_date"]).days
            del row["oldest_due_date"]
            result.append(row)

        return sorted(result, key=lambda r: r["oldest_days"], reverse=True)

    def calculate_interest_charge(self, customer_id: int) -> float:
        """
        Calculate accrued interest on the overdue balance.
        Uses customer.interest_rate (annual %) and overdue days.
        Read-only — does NOT post. Caller decides whether to invoice.

        Formula: overdue_amount × (interest_rate / 100) × (overdue_days / 365)
        """
        customer = self._get_customer_or_404(customer_id)
        if not customer.interest_rate or customer.interest_rate <= 0:
            return 0.0

        balance_info = self.get_account_balance(customer_id)
        overdue_amount = balance_info["overdue_amount"]
        overdue_days = balance_info["oldest_overdue_days"]

        if overdue_amount <= 0 or overdue_days <= 0:
            return 0.0

        interest = overdue_amount * (customer.interest_rate / 100) * (overdue_days / 365)
        return round(interest, 2)

    # ── Credit Balance ────────────────────────────────────────────────────────
    # CRMService is the sole owner of Customer.credit_balance mutations.
    # CoreService and WarrantyService call add_credit() / deduct_credit() here.

    def add_credit(self, customer_id: int, amount: float, reason: str) -> None:
        """
        Add to customer.credit_balance.
        Called by CoreService (core return) and WarrantyService (warranty approval).
        Audits the change.
        """
        if amount <= 0:
            raise ValueError(f"Credit amount must be positive, got {amount}")
        customer = self._get_customer_or_404(customer_id)
        old_balance = customer.credit_balance
        customer.credit_balance = round(customer.credit_balance + amount, 2)
        self.audit(
            entity_type=EntityType.CUSTOMER,
            entity_id=customer_id,
            action=AuditAction.EDITED,
            old_value={"credit_balance": old_balance},
            new_value={"credit_balance": customer.credit_balance},
            notes=f"credit added: {reason}",
        )
        self.db.commit()

    def deduct_credit(self, customer_id: int, amount: float, reason: str) -> None:
        """
        Deduct from customer.credit_balance.
        Called by PaymentService (account_credit payment method).
        Validates sufficient balance before deducting.
        """
        if amount <= 0:
            raise ValueError(f"Deduct amount must be positive, got {amount}")
        customer = self._get_customer_or_404(customer_id)
        if customer.credit_balance < amount - 0.001:
            raise ValueError(
                f"Insufficient credit balance ({customer.credit_balance}) for deduction of {amount}"
            )
        old_balance = customer.credit_balance
        customer.credit_balance = round(customer.credit_balance - amount, 2)
        self.audit(
            entity_type=EntityType.CUSTOMER,
            entity_id=customer_id,
            action=AuditAction.EDITED,
            old_value={"credit_balance": old_balance},
            new_value={"credit_balance": customer.credit_balance},
            notes=f"credit deducted: {reason}",
        )
        self.db.commit()

    # ── Follow-ups (Phase 2 stubs) ────────────────────────────────────────────

    def schedule_followup(self, quote_id: int, follow_up_date: str, notes: str = "") -> None:
        """
        Set follow_up_date on a Quote. Full QuoteFollowup scheduling is Phase 2.
        For now, writes directly to Quote.follow_up_date.
        """
        from app.models.quote import Quote
        from datetime import date
        quote = self.db.query(Quote).filter(Quote.id == quote_id).first()
        if quote is None:
            raise ValueError(f"Quote {quote_id} not found")
        if isinstance(follow_up_date, str):
            quote.follow_up_date = datetime.fromisoformat(follow_up_date)
        else:
            quote.follow_up_date = follow_up_date
        self.db.commit()

    def get_due_followups(self) -> list:
        """Return quotes with follow_up_date <= today and outcome still pending. Phase 2 stub."""
        from app.models.quote import Quote
        from app.constants import QuoteOutcome
        now = datetime.utcnow()
        return (
            self.db.query(Quote)
            .filter(
                Quote.follow_up_date <= now,
                Quote.outcome == QuoteOutcome.PENDING,
            )
            .order_by(Quote.follow_up_date)
            .all()
        )

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_customer_or_404(self, customer_id: int) -> Customer:
        c = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if c is None:
            raise ValueError(f"Customer {customer_id} not found")
        return c
