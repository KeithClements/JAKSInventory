"""
app/services/customer_metrics_service.py
=========================================
Phase 2 §4.4 / P2-Q1 — the ONE definition of customer relationship metrics, so
the list, preview, detail, and invoice-intelligence panels can never disagree.

P2-Q1 (locked): Lifetime / YTD Sales = **net invoiced incl. open, minus
returns/credits**; YTD = **calendar year**. In this system returns and warranty
credits flow through CreditMemo, so "minus returns/credits" = minus
non-reversed credit memos (no double counting).

Money fidelity: invoice totals route through the same app.invoice_totals engine
that every other surface uses (cores never taxed, invoice discount on parts
only), so these aggregates match the workspace/print to the penny. That means
the batch path eager-loads invoice lines + allocations and sums in Python rather
than approximating with a raw SQL SUM. For a counter-shop dataset that is fine;
the plan flags cache/materialisation as the later perf path (§4.4) and
metrics_for_batch is the seam for it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.constants import (
    InvoiceStatus, CreditMemoStatus, PaymentStatus, PaymentDirection,
    CoreDirection, CoreStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment
from app.models.quote import Quote
from app.models.credit_memo import CreditMemo
from app.models.core import CoreCharge
from app.services.base import BaseService


# Metric keys — exported so callers/tests can rely on the shape.
METRIC_KEYS = (
    "lifetime_sales", "ytd_sales", "gross_invoiced", "invoice_count", "aov",
    "last_quote_date", "last_invoice_date", "last_payment_date",
    "open_ar", "credit_limit", "available_credit", "credit_balance",
    "outstanding_core_credits",
)


class CustomerMetricsService(BaseService):
    """Customer relationship metrics (P2-Q1). Read-only; no audit/commit."""

    # ── Focused single-number helper (used by credit checks) ──────────────────

    def open_ar(self, customer_id: int) -> float:
        """Open AR for one customer = Σ balance_due over non-void invoices.

        Uses the same totals engine as metrics_for so the credit warn (§4.5) and
        the intelligence panel agree."""
        agg = self._invoice_aggregates([customer_id]).get(customer_id)
        return agg["open_ar"] if agg else 0.0

    # ── Single customer (live, used on detail) ────────────────────────────────

    def metrics_for(self, customer) -> dict:
        """Full metric dict for one customer (id or Customer instance)."""
        customer_id = customer.id if isinstance(customer, Customer) else int(customer)
        return self.metrics_for_batch([customer_id])[customer_id]

    # ── Batch (list screen) ───────────────────────────────────────────────────

    def metrics_for_batch(self, customer_ids: list[int] | None = None) -> dict[int, dict]:
        """{customer_id: metric-dict} for the given ids (or every active customer
        when ``customer_ids`` is None). Every returned dict has all METRIC_KEYS,
        so the list template can render uniformly without per-row guards."""
        if customer_ids is None:
            customer_ids = [
                cid for (cid,) in self.db.query(Customer.id)
                .filter(Customer.is_active == True).all()  # noqa: E712
            ]
        ids = list({int(c) for c in customer_ids})
        if not ids:
            return {}

        customers = {
            c.id: c for c in self.db.query(Customer).filter(Customer.id.in_(ids)).all()
        }
        inv = self._invoice_aggregates(ids)
        credits_total, credits_ytd = self._credit_memo_totals(ids)
        last_payment = self._last_payment_dates(ids)
        last_quote = self._last_quote_dates(ids)
        core_out = self._outstanding_core_credits(ids)

        out: dict[int, dict] = {}
        for cid in ids:
            cust = customers.get(cid)
            limit = float(cust.credit_limit or 0.0) if cust else 0.0
            credit_balance = float(cust.credit_balance or 0.0) if cust else 0.0

            agg = inv.get(cid, {})
            gross = agg.get("gross_invoiced", 0.0)
            gross_ytd = agg.get("gross_invoiced_ytd", 0.0)
            count = agg.get("invoice_count", 0)
            open_ar = agg.get("open_ar", 0.0)

            lifetime = round(gross - credits_total.get(cid, 0.0), 2)
            ytd = round(gross_ytd - credits_ytd.get(cid, 0.0), 2)
            aov = round(gross / count, 2) if count else 0.0
            available = round(limit - open_ar, 2) if limit > 0 else None

            out[cid] = {
                "lifetime_sales": lifetime,
                "ytd_sales": ytd,
                "gross_invoiced": round(gross, 2),
                "invoice_count": count,
                "aov": aov,
                "last_quote_date": last_quote.get(cid),
                "last_invoice_date": agg.get("last_invoice_date"),
                "last_payment_date": last_payment.get(cid),
                "open_ar": round(open_ar, 2),
                "credit_limit": round(limit, 2),
                "available_credit": available,
                "credit_balance": round(credit_balance, 2),
                "outstanding_core_credits": round(core_out.get(cid, 0.0), 2),
            }
        return out

    # ── Internals ─────────────────────────────────────────────────────────────

    def _invoice_aggregates(self, ids: list[int]) -> dict[int, dict]:
        """Per-customer invoice aggregates computed through the totals engine.

        Returns {cid: {gross_invoiced, gross_invoiced_ytd, open_ar,
        invoice_count, last_invoice_date}} over POSTED invoices only — DRAFT is
        excluded (not yet invoiced/owed) and VOID is excluded (reversed). open_ar
        sums balance_due, which is ~0 on PAID invoices, so it naturally equals the
        open/partial outstanding balance (matching the customer-list AR column)."""
        if not ids:
            return {}
        year_start = datetime(datetime.utcnow().year, 1, 1)
        invoices = (
            self.db.query(Invoice)
            .filter(
                Invoice.customer_id.in_(ids),
                Invoice.status.notin_([InvoiceStatus.DRAFT, InvoiceStatus.VOID]),
            )
            .options(
                selectinload(Invoice.lines),
                selectinload(Invoice.allocations),
            )
            .all()
        )
        out: dict[int, dict] = {}
        for invc in invoices:
            row = out.setdefault(invc.customer_id, {
                "gross_invoiced": 0.0, "gross_invoiced_ytd": 0.0,
                "open_ar": 0.0, "invoice_count": 0, "last_invoice_date": None,
            })
            total = invc.total
            row["gross_invoiced"] += total
            row["open_ar"] += invc.balance_due
            row["invoice_count"] += 1
            if invc.created_at and invc.created_at >= year_start:
                row["gross_invoiced_ytd"] += total
            if invc.created_at and (
                row["last_invoice_date"] is None
                or invc.created_at > row["last_invoice_date"]
            ):
                row["last_invoice_date"] = invc.created_at
        return out

    def _credit_memo_totals(self, ids: list[int]) -> tuple[dict[int, float], dict[int, float]]:
        """(lifetime, ytd) maps of non-reversed credit-memo totals per customer.
        total_amount is a stored column, so a plain SQL SUM is exact here."""
        year_start = datetime(datetime.utcnow().year, 1, 1)
        base = (
            self.db.query(CreditMemo.customer_id, func.sum(CreditMemo.total_amount))
            .filter(
                CreditMemo.customer_id.in_(ids),
                CreditMemo.status != CreditMemoStatus.REVERSED,
            )
        )
        lifetime = {cid: float(amt or 0.0) for cid, amt in base.group_by(CreditMemo.customer_id).all()}
        ytd = {
            cid: float(amt or 0.0)
            for cid, amt in base.filter(CreditMemo.created_at >= year_start)
            .group_by(CreditMemo.customer_id).all()
        }
        return lifetime, ytd

    def _last_payment_dates(self, ids: list[int]) -> dict[int, datetime]:
        """Most recent APPLIED incoming-from-customer payment per customer."""
        rows = (
            self.db.query(Payment.customer_id, func.max(Payment.payment_date))
            .filter(
                Payment.customer_id.in_(ids),
                Payment.status == PaymentStatus.APPLIED,
                Payment.direction == PaymentDirection.INCOMING_FROM_CUSTOMER,
            )
            .group_by(Payment.customer_id)
            .all()
        )
        return {cid: dt for cid, dt in rows if dt is not None}

    def _last_quote_dates(self, ids: list[int]) -> dict[int, datetime]:
        rows = (
            self.db.query(Quote.customer_id, func.max(Quote.created_at))
            .filter(Quote.customer_id.in_(ids))
            .group_by(Quote.customer_id)
            .all()
        )
        return {cid: dt for cid, dt in rows if dt is not None}

    def _outstanding_core_credits(self, ids: list[int]) -> dict[int, float]:
        """Customer-side core deposit still recoverable: Σ customer_unit_charge ×
        qty_outstanding over the customer's CUSTOMER_OWES_RETURN cores that aren't
        closed. This is the credit the customer gets back as cores come in."""
        rows = (
            self.db.query(
                CoreCharge.customer_id,
                CoreCharge.customer_unit_charge,
                CoreCharge.qty_charged,
                CoreCharge.qty_returned,
            )
            .filter(
                CoreCharge.customer_id.in_(ids),
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status != CoreStatus.CLOSED,
            )
            .all()
        )
        out: dict[int, float] = {}
        for cid, unit, qty_charged, qty_returned in rows:
            outstanding_qty = max(0, (qty_charged or 0) - (qty_returned or 0))
            out[cid] = out.get(cid, 0.0) + float(unit or 0.0) * outstanding_qty
        return out
