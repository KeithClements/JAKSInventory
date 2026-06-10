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

from datetime import date as date_type

from app.constants import AuditAction, ActivityType, CallType, EntityType, InvoiceStatus
from app.models.customer import Activity, Customer, CustomerCallLog
from app.models.invoice import Invoice, Payment
from app.models.communication import Communication
from app.services.base import BaseService
from app.services.ar_aging_utils import as_date, zero_buckets, bucket_for


class CRMService(BaseService):

    # ── Call Log ──────────────────────────────────────────────────────────────

    # ── Unified Activity Log (ACTIVITY_LOG_CONTRACT.md) ──────────────────────

    def log_activity(
        self,
        customer_id: int,
        activity_type: str = ActivityType.CALL,
        outcome: str | None = None,
        notes: str = "",
        follow_up_date: date_type | None = None,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
        direction: str | None = None,
    ) -> Activity:
        """
        The ONE write path for all customer interactions.

        activity_type: ActivityType value — 'call' | 'text' | 'counter_visit' |
                       'email' | 'note'.
        outcome:       CallOutcome value — optional (a NOTE has no outcome).
        follow_up_date: ISO date — drives the dashboard "follow-ups due" widget.
        related_entity_type: 'quote' | 'invoice' | 'purchase_order'
        related_entity_id:   pk of the linked document.
        direction:     CallType value (inbound/outbound) — only meaningful for calls.
        """
        self._get_customer_or_404(customer_id)
        call_type = direction or CallType.INBOUND
        # Back-compat: set quote_id when logging against a quote.
        quote_id: int | None = (
            related_entity_id
            if related_entity_type == "quote" and related_entity_id
            else None
        )
        entry = Activity(
            customer_id=customer_id,
            logged_by_id=self.current_user_id,
            call_type=call_type,
            outcome=outcome,
            quote_id=quote_id,
            notes=notes,
            activity_type=activity_type,
            follow_up_date=follow_up_date,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def log_call(
        self,
        customer_id: int,
        call_type: str,
        outcome: str,
        notes: str = "",
        quote_id: int | None = None,
    ) -> CustomerCallLog:
        """Thin back-compat alias for log_activity with activity_type='call'."""
        return self.log_activity(
            customer_id=customer_id,
            activity_type=ActivityType.CALL,
            outcome=outcome,
            notes=notes,
            direction=call_type,
            related_entity_type="quote" if quote_id else None,
            related_entity_id=quote_id,
        )

    def follow_ups_due(
        self,
        through: date_type | None = None,
    ) -> list[Activity]:
        """Activities with a follow_up_date on or before `through` (default today)
        that have not yet been marked done (follow_up_done_at IS NULL)."""
        cutoff = through or date_type.today()
        return (
            self.db.query(Activity)
            .filter(
                Activity.follow_up_date <= cutoff,
                Activity.follow_up_done_at.is_(None),
            )
            .order_by(Activity.follow_up_date.asc())
            .all()
        )

    def mark_follow_up_done(self, activity_id: int) -> None:
        """Stamp follow_up_done_at = now, removing the activity from the due list."""
        entry = self.db.query(Activity).filter(Activity.id == activity_id).first()
        if entry is None:
            raise ValueError(f"Activity {activity_id} not found")
        entry.follow_up_done_at = datetime.utcnow()
        self.db.commit()

    def get_timeline(self, customer_id: int, limit: int = 100) -> list[dict]:
        """Merge manual activities + system-sent communications for this customer,
        newest first. Each item has a 'kind' field ('activity' | 'comm') so the
        template can render both in a single chronological feed."""
        self._get_customer_or_404(customer_id)

        # Manual activities
        activities = (
            self.db.query(Activity)
            .filter(Activity.customer_id == customer_id)
            .order_by(Activity.logged_at.desc())
            .limit(limit)
            .all()
        )

        # System-sent communications (read-only — never mutated)
        comms = (
            self.db.query(Communication)
            .filter(Communication.customer_id == customer_id)
            .order_by(Communication.sent_at.desc())
            .limit(limit)
            .all()
        )

        items: list[dict] = []
        for a in activities:
            items.append({
                "kind":            "activity",
                "id":              a.id,
                "when":            a.logged_at,
                "activity_type":   a.activity_type,
                "outcome":         a.outcome,
                "note":            a.notes,   # contract key (QA uses "note" not "notes")
                "follow_up_date":  a.follow_up_date,
                "follow_up_done":  a.follow_up_done_at is not None,
                "related": (
                    {
                        "type":  a.related_entity_type,
                        "id":    a.related_entity_id,
                    }
                    if a.related_entity_type else None
                ),
                "logged_by_id":   a.logged_by_id,
            })
        for c in comms:
            items.append({
                "kind":          "comm",
                "id":            c.id,
                "when":          c.sent_at,
                "activity_type": c.channel,
                "outcome":       None,
                "note":          c.subject or "",
                "follow_up_date": None,
                "follow_up_done": True,
                "related":       None,
                "logged_by_id":  None,
            })

        items.sort(key=lambda x: x["when"], reverse=True)
        return items[:limit]

    def get_unified_timeline(self, customer_id: int, limit: int = 100) -> list[dict]:
        """Full chronological feed for a customer, newest first: the manual
        activities + system communications (via get_timeline) PLUS the customer's
        quotes, sales orders, invoices, and payments as entries
        (kind='quote'|'so'|'invoice'|'payment'). Each document entry carries:
        kind, id, when, label (number), status, amount, note. Read-only."""
        self._get_customer_or_404(customer_id)
        from app.models.quote import Quote, SalesOrder

        items = self.get_timeline(customer_id, limit=limit)  # activities + comms

        for qte in (self.db.query(Quote).filter(Quote.customer_id == customer_id)
                    .order_by(Quote.created_at.desc()).limit(limit).all()):
            items.append({"kind": "quote", "id": qte.id, "when": qte.created_at,
                          "label": qte.quote_number, "status": qte.status,
                          "amount": qte.subtotal, "note": qte.quote_number})
        for so in (self.db.query(SalesOrder).filter(SalesOrder.customer_id == customer_id)
                   .order_by(SalesOrder.created_at.desc()).limit(limit).all()):
            items.append({"kind": "so", "id": so.id, "when": so.created_at,
                          "label": so.so_number, "status": so.status,
                          "amount": so.subtotal, "note": so.so_number})
        for inv in (self.db.query(Invoice).filter(Invoice.customer_id == customer_id)
                    .order_by(Invoice.created_at.desc()).limit(limit).all()):
            items.append({"kind": "invoice", "id": inv.id, "when": inv.created_at,
                          "label": inv.invoice_number, "status": inv.status,
                          "amount": inv.total, "note": inv.invoice_number})
        for p in (self.db.query(Payment).filter(Payment.customer_id == customer_id)
                  .order_by(Payment.payment_date.desc()).limit(limit).all()):
            label = (f"Check #{p.check_number}" if p.check_number
                     else (p.payment_method or "payment").replace("_", " ").title())
            items.append({"kind": "payment", "id": p.id, "when": p.payment_date,
                          "label": label, "status": p.status,
                          "amount": p.amount_received, "note": p.notes or label})

        items.sort(key=lambda x: x.get("when") or datetime.min, reverse=True)
        return items[:limit]

    def activities_for_entity(
        self,
        entity_type: str,
        entity_id: int,
    ) -> list[Activity]:
        """All activities linked to a specific document (doc-side panel)."""
        return (
            self.db.query(Activity)
            .filter(
                Activity.related_entity_type == entity_type,
                Activity.related_entity_id == entity_id,
            )
            .order_by(Activity.logged_at.desc())
            .all()
        )

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

        # ── AR aging buckets ──────────────────────────────────────────────────
        # Use the shared ar_aging_utils helpers so these boundaries stay in
        # sync with the AR Aging report and customer statement pages.
        # Reference date: invoice.due_date when set, else invoice.created_at
        # (same fallback used by ReportService.get_ar_aging).
        from datetime import date as _date
        _today = _date.today()
        buckets = zero_buckets()
        for inv in open_invoices:
            bal = inv.balance_due
            if bal <= 0:
                continue
            ref = as_date(inv.due_date) or as_date(inv.created_at)
            if ref is None:
                buckets["current"] = round(buckets["current"] + bal, 2)
                continue
            days_late = (_today - ref).days
            b = bucket_for(days_late)
            buckets[b] = round(buckets[b] + bal, 2)

        return {
            "total_open": total_open,
            "overdue_amount": overdue_amount,
            "oldest_overdue_days": oldest_overdue_days,
            "credit_balance": customer.credit_balance,
            "unapplied_payments": unapplied_payments,
            # AR aging buckets: current / 1_30 / 31_60 / 61_90 / over_90
            # Matches the boundaries in ReportService.get_ar_aging() and
            # StatementService so all three surfaces agree.
            "buckets": buckets,
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

    def deduct_credit(
        self,
        customer_id: int,
        amount: float,
        reason: str,
        *,
        allow_negative: bool = False,
    ) -> None:
        """
        Deduct from customer.credit_balance.
        Called by PaymentService (account_credit payment method) and
        CoreService (vendor-denial chargebacks).
        Validates sufficient balance before deducting unless allow_negative —
        a chargeback may pull back credit the customer already spent; the
        negative balance records that they owe it back.
        """
        if amount <= 0:
            raise ValueError(f"Deduct amount must be positive, got {amount}")
        customer = self._get_customer_or_404(customer_id)
        if not allow_negative and customer.credit_balance < amount - 0.001:
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
