"""
app/services/report_service.py
==============================
Read-only reporting queries. Series 1 of the Reports & Analytics build.

Rules:
  - Reports never mutate the DB.
  - All bucket/sum/margin math lives here, not in routers or templates.
  - Methods return plain dicts/lists ready for Jinja iteration — no ORM
    objects bleed into return shapes when a snapshot is what's needed.

Method index:
  get_ar_aging(as_of_date=None)
  get_sales_by_customer(start_date, end_date)
  get_sales_by_product(start_date, end_date)
  get_inventory_valuation()
  get_open_pos()
  get_core_charges_outstanding()
  get_low_stock()
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import (
    CoreDirection, CoreStatus, FulfillmentSource,
    InvoiceStatus, POStatus, SOLineStatus, SOStatus,
)
from app.models.core import CoreCharge, CoreSlip
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product, ProductCategory, ProductVendorSource
from app.models.purchase_order import PurchaseOrder, POLine
from app.models.quote import LostSaleLog, Quote, SalesOrder, SOLine
from app.services.base import BaseService
from app.services.ar_aging_utils import (
    AGING_BUCKETS, as_date, zero_buckets, bucket_for,
)


# Statuses that count as "finalized" — posted invoices that represent real revenue.
# Drafts are excluded (not posted yet). Voids are excluded (reversed).
_FINALIZED_INVOICE_STATUSES = (
    InvoiceStatus.OPEN,
    InvoiceStatus.PARTIAL,
    InvoiceStatus.PAID,
)

# Open core charge states for the outstanding-cores report
_OPEN_CORE_STATUSES = (
    CoreStatus.OPEN,
    CoreStatus.PARTIAL,
)

# Aging bucket keys — canonical order from shared utils (kept here for imports
# that reference report_service._AGING_BUCKETS if any).  New code should import
# directly from ar_aging_utils.
_AGING_BUCKETS = AGING_BUCKETS


class ReportService(BaseService):
    """Read-only report queries. Inherits db + audit machinery but never writes."""

    # ── Cost-estimation fallback (R1-14 margin truth) ────────────────────────

    @staticmethod
    def _fallback_unit_cost(product) -> float:
        """
        Estimated unit cost for invoice lines whose frozen unit_cost snapshot
        is 0 (sold before the product was ever receipted — true for the whole
        un-receipted imported catalog, which otherwise reports ~100% margin).

        Order: preferred vendor source vendor_cost (the per-source vendor quote
        is the source of truth for what we'd pay today, §8N), then last_cost
        (most recent actual receipt cost). Returns 0.0 when no usable cost.
        """
        if product is None:
            return 0.0
        src = product.preferred_vendor_source
        if src is not None and (src.vendor_cost or 0.0) > 0:
            return src.vendor_cost
        if (product.last_cost or 0.0) > 0:
            return product.last_cost
        return 0.0

    def _resolve_line_cost(self, ln) -> tuple[float, bool, bool]:
        """
        Per-line COGS for sales reports: (unit_cost, estimated, zero_cost).

        estimated  — snapshot was 0 but a fallback cost was substituted.
        zero_cost  — no cost basis at all on a revenue-carrying line (margin
                     overstated for that line).
        """
        unit_cost = ln.unit_cost or 0.0
        if unit_cost > 0:
            return unit_cost, False, False
        est = self._fallback_unit_cost(ln.product)
        if est > 0:
            return est, True, False
        return 0.0, False, ln.line_total != 0

    # ── 1. AR Aging ───────────────────────────────────────────────────────────

    def get_ar_aging(self, as_of_date: date | None = None) -> dict[str, Any]:
        """
        Bucket unpaid invoice balances by age relative to as_of_date.

        Per spec:
          - Exclude voided invoices.
          - Bucket: Current / 1–30 / 31–60 / 61–90 / 90+
          - Use invoice.due_date if available, else invoice.created_at.
          - Return customer-level rows + grand totals.

        Returns:
          {
            "as_of": date,
            "rows": [
              {
                "customer": Customer, "customer_id": int,
                "invoice_count": int,
                "current": float, "1_30": float, "31_60": float,
                "61_90": float, "over_90": float, "total": float,
                "invoices": [Invoice, ...],
              }, ...
            ],
            "totals": {bucket: float, ..., "total": float},
          }
        """
        as_of = as_of_date or date.today()

        # Load all non-void invoices with the relationships needed for balance_due
        # and customer name. DRAFT invoices have no balance due (not posted) but
        # leaving them in costs nothing — they'll be filtered out by balance<=0.
        invoices = (
            self.db.query(Invoice)
            .options(
                joinedload(Invoice.lines),
                joinedload(Invoice.allocations),
                joinedload(Invoice.customer),
            )
            .filter(Invoice.status != InvoiceStatus.VOID)
            .all()
        )

        aging: dict[int, dict[str, Any]] = defaultdict(lambda: {
            "customer": None,
            "customer_id": None,
            "invoice_count": 0,
            **zero_buckets(),
            "total": 0.0,
            "invoices": [],
        })

        for inv in invoices:
            balance = inv.balance_due
            if balance <= 0:
                continue

            row = aging[inv.customer_id]
            row["customer"] = inv.customer
            row["customer_id"] = inv.customer_id
            row["invoice_count"] += 1
            row["invoices"].append(inv)
            row["total"] = round(row["total"] + balance, 2)

            # Use due_date if present, else fall back to invoice created date
            reference_date = as_date(inv.due_date) or as_date(inv.created_at)
            if reference_date is None:
                # Truly no date — treat as current rather than crashing
                row["current"] = round(row["current"] + balance, 2)
                continue

            days_late = (as_of - reference_date).days
            bucket = bucket_for(days_late)
            row[bucket] = round(row[bucket] + balance, 2)

        # Sort by total descending — biggest debtors at the top
        rows = sorted(aging.values(), key=lambda r: r["total"], reverse=True)

        totals = {b: round(sum(r[b] for r in rows), 2) for b in AGING_BUCKETS}
        totals["total"] = round(sum(r["total"] for r in rows), 2)

        # Smoke check: bucket columns must sum to grand total
        bucket_sum = round(sum(totals[b] for b in AGING_BUCKETS), 2)
        if abs(bucket_sum - totals["total"]) > 0.02:
            log.warning(
                "AR aging bucket sum %.2f != totals.total %.2f (as_of=%s)",
                bucket_sum, totals["total"], as_of,
            )

        return {"as_of": as_of, "rows": rows, "totals": totals}

    # ── 1b. AP Aging (payables — what JAKS owes vendors) ──────────────────────

    def get_ap_aging(self, as_of_date: date | None = None) -> dict[str, Any]:
        """§21 — payables mirror of get_ar_aging. Buckets unpaid vendor-bill
        balances (total_amount NET of applied, non-reversed vendor credits) by age
        relative to as_of. Excludes PAID bills; reference date = due_date, else
        bill_date, else created_at. Vendor-level rows + grand totals."""
        from sqlalchemy import func as _func
        from app.constants import VendorBillStatus
        from app.models.purchase_order import VendorBill
        from app.models.vendor_credit import VendorCreditMemoAllocation

        as_of = as_of_date or date.today()
        bills = (
            self.db.query(VendorBill)
            .options(joinedload(VendorBill.vendor))
            .filter(VendorBill.status != VendorBillStatus.PAID)
            .all()
        )
        # Applied (non-reversed) vendor credits per bill — net them off the balance.
        credit_by_bill = {
            bid: float(amt or 0.0)
            for bid, amt in (
                self.db.query(
                    VendorCreditMemoAllocation.vendor_bill_id,
                    _func.sum(VendorCreditMemoAllocation.amount_applied),
                )
                .filter(VendorCreditMemoAllocation.is_reversed == False)  # noqa: E712
                .group_by(VendorCreditMemoAllocation.vendor_bill_id)
                .all()
            )
        }

        aging: dict[int, dict[str, Any]] = defaultdict(lambda: {
            "vendor": None, "vendor_id": None, "bill_count": 0,
            **zero_buckets(), "total": 0.0, "bills": [],
        })
        for bill in bills:
            balance = round(bill.total_amount - credit_by_bill.get(bill.id, 0.0), 2)
            if balance <= 0:
                continue
            row = aging[bill.vendor_id]
            row["vendor"] = bill.vendor
            row["vendor_id"] = bill.vendor_id
            row["bill_count"] += 1
            row["bills"].append(bill)
            row["total"] = round(row["total"] + balance, 2)
            ref = as_date(bill.due_date) or as_date(bill.bill_date) or as_date(bill.created_at)
            if ref is None:
                row["current"] = round(row["current"] + balance, 2)
                continue
            bucket = bucket_for((as_of - ref).days)
            row[bucket] = round(row[bucket] + balance, 2)

        rows = sorted(aging.values(), key=lambda r: r["total"], reverse=True)
        totals = {b: round(sum(r[b] for r in rows), 2) for b in AGING_BUCKETS}
        totals["total"] = round(sum(r["total"] for r in rows), 2)
        return {"as_of": as_of, "rows": rows, "totals": totals}

    # ── Quote conversion rate (§21.10) ────────────────────────────────────────

    def get_quote_conversion(self, start_date: date, end_date: date) -> dict[str, Any]:
        """§21 — quote win-rate over a period. Conversion = won / (won + lost)
        decided quotes; also reports pending + dollar value won vs total."""
        from app.constants import QuoteOutcome
        quotes = (
            self.db.query(Quote)
            .options(joinedload(Quote.lines))
            .filter(
                func.date(Quote.created_at) >= start_date,
                func.date(Quote.created_at) <= end_date,
            )
            .all()
        )
        counts = {QuoteOutcome.WON: 0, QuoteOutcome.LOST: 0,
                  QuoteOutcome.PENDING: 0, QuoteOutcome.NO_DECISION: 0}
        won_value = total_value = 0.0
        for q in quotes:
            counts[q.outcome] = counts.get(q.outcome, 0) + 1
            sub = q.subtotal
            total_value = round(total_value + sub, 2)
            if q.outcome == QuoteOutcome.WON:
                won_value = round(won_value + sub, 2)
        won, lost = counts[QuoteOutcome.WON], counts[QuoteOutcome.LOST]
        decided = won + lost
        return {
            "start_date": start_date, "end_date": end_date,
            "total": len(quotes), "won": won, "lost": lost,
            "pending": counts[QuoteOutcome.PENDING],
            "no_decision": counts[QuoteOutcome.NO_DECISION],
            "conversion_rate": round(won / decided * 100, 1) if decided else None,
            "won_value": won_value, "total_value": total_value,
        }

    # ── Vendor performance (§21.10) ───────────────────────────────────────────

    def get_vendor_performance(self, start_date: date, end_date: date) -> dict[str, Any]:
        """§21 — per-vendor PO/bill scorecard over a period: PO count + value,
        fill rate (qty_received / qty_ordered across the vendor's PO lines), and
        bill-discrepancy count (3-way-match failures: over-bill / cost variance).
        (On-time delivery isn't reported — a receipt can span multiple POs, so
        there's no clean per-PO received date to compare against expected_at.)"""
        from app.models.purchase_order import VendorBill
        from app.models.vendor import Vendor

        pos = (
            self.db.query(PurchaseOrder)
            .options(joinedload(PurchaseOrder.vendor), joinedload(PurchaseOrder.lines))
            .filter(
                func.date(PurchaseOrder.created_at) >= start_date,
                func.date(PurchaseOrder.created_at) <= end_date,
            )
            .all()
        )
        bills = (
            self.db.query(VendorBill)
            .options(joinedload(VendorBill.lines))
            .filter(
                func.date(VendorBill.created_at) >= start_date,
                func.date(VendorBill.created_at) <= end_date,
            )
            .all()
        )

        rows: dict[int, dict[str, Any]] = defaultdict(lambda: {
            "vendor": None, "vendor_id": None, "po_count": 0, "po_value": 0.0,
            "qty_ordered": 0, "qty_received": 0, "bills": 0, "discrepancy_bills": 0,
        })
        for po in pos:
            r = rows[po.vendor_id]
            r["vendor"] = po.vendor
            r["vendor_id"] = po.vendor_id
            r["po_count"] += 1
            r["po_value"] = round(r["po_value"] + po.total, 2)
            for ln in po.lines:
                r["qty_ordered"] += ln.qty_ordered
                r["qty_received"] += ln.qty_received
        for b in bills:
            r = rows[b.vendor_id]
            if r["vendor_id"] is None:
                r["vendor_id"] = b.vendor_id
            r["bills"] += 1
            if b.has_discrepancy:
                r["discrepancy_bills"] += 1

        # Fill any vendor names still missing (bill-only rows).
        missing = [vid for vid, r in rows.items() if r["vendor"] is None]
        if missing:
            for v in self.db.query(Vendor).filter(Vendor.id.in_(missing)).all():
                rows[v.id]["vendor"] = v
        for r in rows.values():
            r["fill_rate"] = (
                round(r["qty_received"] / r["qty_ordered"] * 100, 1)
                if r["qty_ordered"] else None
            )

        out = sorted(rows.values(), key=lambda r: r["po_value"], reverse=True)
        return {"start_date": start_date, "end_date": end_date, "rows": out}

    # ── 2. Sales by Customer ─────────────────────────────────────────────────

    def get_sales_by_customer(
        self, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """
        Aggregate finalized-invoice activity per customer in [start_date, end_date].

        Per spec:
          - Use locked/finalized invoices only (OPEN/PARTIAL/PAID; DRAFT excluded).
          - Exclude voided invoices.
          - Return: customer, invoice count, gross sales, payments received,
            balance due, margin where available.

        Date filter is inclusive on both ends, applied to invoice.created_at.

        Returns:
          {
            "start_date": date, "end_date": date,
            "rows": [
              {
                "customer": Customer | None,
                "invoice_count": int,
                "gross_sales": float,
                "payments_received": float,
                "balance_due": float,
                "cost": float,
                "margin": float,
                "margin_pct": float | None,
              }, ...
            ],
            "totals": {same keys, summed},
          }
        """
        # end_date is inclusive — bump by one day for the strict-less-than compare
        end_exclusive = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        start_dt = datetime.combine(start_date, datetime.min.time())

        invoices = (
            self.db.query(Invoice)
            .options(
                joinedload(Invoice.lines).joinedload(InvoiceLine.product),
                joinedload(Invoice.allocations),
                joinedload(Invoice.customer),
            )
            .filter(
                Invoice.status.in_(_FINALIZED_INVOICE_STATUSES),
                Invoice.created_at >= start_dt,
                Invoice.created_at < end_exclusive,
            )
            .all()
        )

        by_customer: dict[int | None, dict[str, Any]] = defaultdict(lambda: {
            "customer": None,
            "invoice_count": 0,
            "gross_sales": 0.0,
            "payments_received": 0.0,
            "balance_due": 0.0,
            "cost": 0.0,
            "margin": 0.0,
            "margin_pct": None,
        })

        # R1-14 margin truth — lines sold before any receipt carry a frozen
        # unit_cost of 0; fall back to vendor/receipt cost and count the
        # substitutions so the UI can flag the margin as estimated.
        cost_estimated_lines = 0
        zero_cost_lines = 0

        for inv in invoices:
            row = by_customer[inv.customer_id]
            row["customer"] = inv.customer
            row["invoice_count"] += 1
            # Core-charge lines are a deposit on part returns, not earned revenue.
            # Subtract their line_total from the invoice total so reports reflect
            # true product/service revenue only.
            core_deposits = sum(ln.line_total for ln in inv.lines if ln.is_core_line)
            row["gross_sales"] = round(row["gross_sales"] + inv.total - core_deposits, 2)
            row["payments_received"] = round(
                row["payments_received"] + inv.amount_paid, 2
            )
            row["balance_due"] = round(row["balance_due"] + inv.balance_due, 2)
            # Cost snapshot is per-line; exclude core-charge lines (deposit, not COGS).
            line_cost = 0.0
            for ln in inv.lines:
                if ln.is_core_line:
                    continue
                unit_cost, estimated, zero_cost = self._resolve_line_cost(ln)
                line_cost += unit_cost * ln.qty
                cost_estimated_lines += estimated
                zero_cost_lines += zero_cost
            row["cost"] = round(row["cost"] + line_cost, 2)

        # Compute margin and margin_pct per row
        for row in by_customer.values():
            row["margin"] = round(row["gross_sales"] - row["cost"], 2)
            if row["gross_sales"] > 0:
                row["margin_pct"] = round(
                    (row["margin"] / row["gross_sales"]) * 100, 2
                )

        rows = sorted(by_customer.values(), key=lambda r: r["gross_sales"], reverse=True)

        totals = {
            "invoice_count":      sum(r["invoice_count"] for r in rows),
            "gross_sales":        round(sum(r["gross_sales"]        for r in rows), 2),
            "payments_received":  round(sum(r["payments_received"]  for r in rows), 2),
            "balance_due":        round(sum(r["balance_due"]        for r in rows), 2),
            "cost":               round(sum(r["cost"]               for r in rows), 2),
            "margin":             round(sum(r["margin"]             for r in rows), 2),
        }
        totals["margin_pct"] = (
            round((totals["margin"] / totals["gross_sales"]) * 100, 2)
            if totals["gross_sales"] > 0 else None
        )

        return {
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "cost_estimated_lines": cost_estimated_lines,
            "zero_cost_lines": zero_cost_lines,
        }

    # ── 3. Sales by Product ──────────────────────────────────────────────────

    def get_sales_by_product(
        self, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """
        Aggregate invoice_lines per product/SKU within [start_date, end_date].

        Per spec:
          - Pull from invoice_lines (finalized invoices only, voids excluded).
          - Handle missing product_id gracefully — group those under a single
            "(no product)" pseudo-row so misc/freight lines aren't dropped.
          - Return: product/SKU, qty sold, revenue, estimated cost, gross margin, margin %.

        Returns:
          {
            "start_date": date, "end_date": date,
            "rows": [
              {
                "product": Product | None,
                "sku": str, "description": str,
                "qty_sold": int,
                "revenue": float,
                "cost": float,
                "margin": float,
                "margin_pct": float | None,
              }, ...
            ],
            "totals": {qty_sold, revenue, cost, margin, margin_pct},
          }
        """
        end_exclusive = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        start_dt = datetime.combine(start_date, datetime.min.time())

        lines = (
            self.db.query(InvoiceLine)
            .options(
                joinedload(InvoiceLine.invoice),
                joinedload(InvoiceLine.product),
            )
            .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
            .filter(
                Invoice.status.in_(_FINALIZED_INVOICE_STATUSES),
                Invoice.created_at >= start_dt,
                Invoice.created_at < end_exclusive,
            )
            .all()
        )

        # Bucket by product_id; None lines all collapse under key=None
        by_product: dict[int | None, dict[str, Any]] = defaultdict(lambda: {
            "product": None,
            "sku": "",
            "description": "",
            "qty_sold": 0,
            "revenue": 0.0,
            "cost": 0.0,
            "margin": 0.0,
            "margin_pct": None,
        })

        # R1-14 margin truth — same estimated-cost fallback as Sales by Customer.
        cost_estimated_lines = 0
        zero_cost_lines = 0

        for ln in lines:
            # Core-charge lines are a deposit on part returns — exclude them from
            # revenue so the product revenue report shows earned income only.
            if ln.is_core_line:
                continue

            key = ln.product_id  # None means non-product line (freight, misc fee, etc.)
            row = by_product[key]

            if key is not None and row["product"] is None:
                row["product"] = ln.product
                row["sku"] = ln.product.sku if ln.product else ""
                row["description"] = (
                    ln.product.title if ln.product else (ln.description or "")
                )
            elif key is None:
                row["sku"] = "—"
                row["description"] = "(non-product lines)"

            unit_cost, estimated, zero_cost = self._resolve_line_cost(ln)
            cost_estimated_lines += estimated
            zero_cost_lines += zero_cost

            row["qty_sold"] += ln.qty
            row["revenue"] = round(row["revenue"] + ln.line_total, 2)
            row["cost"] = round(row["cost"] + unit_cost * ln.qty, 2)

        for row in by_product.values():
            row["margin"] = round(row["revenue"] - row["cost"], 2)
            if row["revenue"] > 0:
                row["margin_pct"] = round((row["margin"] / row["revenue"]) * 100, 2)

        rows = sorted(by_product.values(), key=lambda r: r["revenue"], reverse=True)

        totals = {
            "qty_sold": sum(r["qty_sold"] for r in rows),
            "revenue": round(sum(r["revenue"] for r in rows), 2),
            "cost":    round(sum(r["cost"]    for r in rows), 2),
            "margin":  round(sum(r["margin"]  for r in rows), 2),
        }
        totals["margin_pct"] = (
            round((totals["margin"] / totals["revenue"]) * 100, 2)
            if totals["revenue"] > 0 else None
        )

        return {
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "cost_estimated_lines": cost_estimated_lines,
            "zero_cost_lines": zero_cost_lines,
        }

    # ── 4. Inventory Valuation ───────────────────────────────────────────────
    #
    # Valuation rule (UNCHANGED — both the summary and the detail paths mirror it
    # exactly so the headline number ties out across every view):
    #   * Active products only (Product.is_active == True).
    #   * value per part = round(qty_on_hand * cost, 2)   (cost = moving-avg COGS).
    #   * total_value    = sum of those per-part values (negative-qty rows included,
    #                      exactly as before — they net the valuation down).
    #   * total_units / in_stock_skus count only qty_on_hand > 0.
    #   * zero_cost_count counts qty_on_hand > 0 AND cost <= 0.
    #
    # Perf: the headline totals + the by-category breakdown are computed in SQL with
    # GROUP BY (get_inventory_valuation_summary) so the page never materialises all
    # ~31k products. Detail rows are paginated / category-scoped on demand.

    def _inventory_warning_case(self):
        """SQLAlchemy CASE classifying a product into 'zero_cost' | 'negative_qty' | ''."""
        from sqlalchemy import case
        return case(
            (Product.qty_on_hand < 0, "negative_qty"),
            ((Product.qty_on_hand > 0) & (func.coalesce(Product.cost, 0.0) <= 0), "zero_cost"),
            else_="",
        )

    def get_inventory_valuation_summary(self) -> dict[str, Any]:
        """
        Fast, fully-aggregated inventory valuation — computed in SQL (GROUP BY),
        never materialising the ~31k product rows in Python.

        Returns:
          {
            "totals": {sku_count, in_stock_skus, total_units, total_value, zero_cost_count,
                       zero_cost_recoverable_count, cost_source_breakdown},
            "by_category": [
              {
                "category_id": int | None, "category": str,
                "sku_count": int, "in_stock_skus": int,
                "total_units": int, "total_value": float, "zero_cost_count": int,
              }, ...  # sorted by total_value desc
            ],
          }

        §23.3 Phase 2 — "cost-source callout": the valuation math itself is
        UNCHANGED (still raw Product.cost, never effective_cost — see the rule
        comment above this method); this only adds VISIBILITY into WHY a SKU
        has the cost it shows. cost_source_breakdown counts active products by
        Product.cost_source ("receipt" = real moving-avg from a PO receipt,
        "manual" = user-set OR simply never touched — it's the column default,
        "vendor" = legacy, no longer written). zero_cost_recoverable_count is
        the subset of zero_cost_count that DOES have an active vendor source
        with a real cost on file — i.e. Product.effective_cost would price
        these non-zero even though this report's own $0 total does not.
        """
        from sqlalchemy import case, exists

        # round(qty*cost, 2) per row, summed — matches the per-row rounding the old
        # Python path used (SQLite ROUND then SUM keeps cents identical at this scale).
        value_expr = func.sum(
            func.round(Product.qty_on_hand * func.coalesce(Product.cost, 0.0), 2)
        )
        in_stock_expr = func.sum(case((Product.qty_on_hand > 0, 1), else_=0))
        units_expr = func.sum(case((Product.qty_on_hand > 0, Product.qty_on_hand), else_=0))
        zero_cost_cond = (Product.qty_on_hand > 0) & (func.coalesce(Product.cost, 0.0) <= 0)
        zero_cost_expr = func.sum(case((zero_cost_cond, 1), else_=0))
        # Correlated EXISTS (not a join) so a product with multiple vendor
        # sources never fans out the surrounding GROUP BY aggregates.
        has_vendor_cost = exists().where(
            ProductVendorSource.product_id == Product.id,
            ProductVendorSource.is_active == True,  # noqa: E712
            ProductVendorSource.vendor_cost > 0,
        )
        zero_cost_recoverable_expr = func.sum(
            case((zero_cost_cond & has_vendor_cost, 1), else_=0)
        )

        rows = (
            self.db.query(
                Product.category_id.label("category_id"),
                ProductCategory.name.label("category_name"),
                func.count(Product.id).label("sku_count"),
                in_stock_expr.label("in_stock_skus"),
                units_expr.label("total_units"),
                value_expr.label("total_value"),
                zero_cost_expr.label("zero_cost_count"),
            )
            .outerjoin(ProductCategory, Product.category_id == ProductCategory.id)
            .filter(Product.is_active == True)  # noqa: E712
            .group_by(Product.category_id, ProductCategory.name)
            .all()
        )

        by_category: list[dict[str, Any]] = []
        tot_sku = tot_instock = tot_units = tot_zero = 0
        tot_value = 0.0
        for r in rows:
            value = round(float(r.total_value or 0.0), 2)
            by_category.append({
                "category_id":   r.category_id,
                "category":      r.category_name or "Uncategorized",
                "sku_count":     int(r.sku_count or 0),
                "in_stock_skus": int(r.in_stock_skus or 0),
                "total_units":   int(r.total_units or 0),
                "total_value":   value,
                "zero_cost_count": int(r.zero_cost_count or 0),
            })
            tot_sku     += int(r.sku_count or 0)
            tot_instock += int(r.in_stock_skus or 0)
            tot_units   += int(r.total_units or 0)
            tot_zero    += int(r.zero_cost_count or 0)
            tot_value   += value

        by_category.sort(key=lambda c: c["total_value"], reverse=True)

        # Separate query (whole-population, single row) — cost_source_breakdown
        # and zero_cost_recoverable_count are catalog-wide, not per-category.
        cs_rows = (
            self.db.query(
                Product.cost_source.label("cost_source"),
                func.count(Product.id).label("n"),
            )
            .filter(Product.is_active == True)  # noqa: E712
            .group_by(Product.cost_source)
            .all()
        )
        cost_source_breakdown = {(r.cost_source or "manual"): int(r.n or 0) for r in cs_rows}

        zero_cost_recoverable = (
            self.db.query(zero_cost_recoverable_expr)
            .filter(Product.is_active == True)  # noqa: E712
            .scalar()
        ) or 0

        totals = {
            "sku_count":       tot_sku,
            "in_stock_skus":   tot_instock,
            "total_units":     tot_units,
            "total_value":     round(tot_value, 2),
            "zero_cost_count": tot_zero,
            "zero_cost_recoverable_count": int(zero_cost_recoverable),
            "cost_source_breakdown": cost_source_breakdown,
        }
        return {"totals": totals, "by_category": by_category}

    def get_inventory_valuation(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        category_id: int | None = None,
        uncategorized: bool = False,
    ) -> dict[str, Any]:
        """
        Per-active-product valuation snapshot.

        Per spec:
          - Active products only.
          - Return qty_on_hand, avg cost (Product.cost), last_cost, total value.
          - Bottom totals (full-population, computed in SQL — independent of paging).
          - Flag zero/negative cost with warning field.

        Pagination / drill-down (all optional — when page/page_size are omitted the
        method returns EVERY active row exactly as before, so the CSV export and the
        original callers keep their contract):
          - page / page_size  -> server-side window of detail rows.
          - category_id        -> restrict detail rows to one category (drill-down).
          - uncategorized      -> restrict detail rows to products with no category.

        Returns:
          {
            "rows": [ {product, sku, title, qty_on_hand, qty_committed,
                       qty_available, avg_cost, last_cost, total_value,
                       warning}, ... ],     # highest-value first
            "totals": {sku_count, in_stock_skus, total_units, total_value,
                       zero_cost_count},    # whole active population (not the page)
            "page": int, "page_size": int | None, "total_rows": int,
            "total_pages": int, "category_id": int | None,
          }
        """
        # Totals are always the full-population SQL aggregate — never the page.
        totals = self.get_inventory_valuation_summary()["totals"]

        q = (
            self.db.query(Product)
            .filter(Product.is_active == True)  # noqa: E712
        )
        if uncategorized:
            q = q.filter(Product.category_id.is_(None))
        elif category_id is not None:
            q = q.filter(Product.category_id == category_id)

        # Highest-value SKUs first (most useful), computed in SQL so paging windows
        # over the right order. NULLs (no cost) sort last via coalesce.
        value_order = (Product.qty_on_hand * func.coalesce(Product.cost, 0.0)).desc()
        q = q.order_by(value_order, Product.sku)

        total_rows = q.count()

        if page_size is not None:
            page = max(1, page or 1)
            total_pages = max(1, (total_rows + page_size - 1) // page_size)
            page = min(page, total_pages)
            products = q.offset((page - 1) * page_size).limit(page_size).all()
        else:
            page = 1
            total_pages = 1
            products = q.all()

        rows: list[dict[str, Any]] = []
        for p in products:
            qty_on_hand = p.qty_on_hand
            avg_cost = p.cost or 0.0
            last_cost = p.last_cost or 0.0
            value = round(qty_on_hand * avg_cost, 2)

            # Warning flags — surface data-integrity issues
            warning: str | None = None
            if qty_on_hand > 0 and avg_cost <= 0:
                warning = "zero_cost"
            elif qty_on_hand < 0:
                warning = "negative_qty"

            # §23.3 Phase 2 — cost-source callout. Doesn't change avg_cost/value
            # (still raw Product.cost, per the locked valuation rule above); only
            # surfaces WHY: cost_source explains what set the shown cost, and for
            # a zero_cost row, recoverable_cost is the vendor cost Product.
            # effective_cost would use instead — None when no vendor cost exists
            # either (a genuinely blank cost basis, not just an unreceived part).
            recoverable_cost: float | None = None
            if warning == "zero_cost":
                eff = p.effective_cost
                if eff and eff > 0:
                    recoverable_cost = eff

            rows.append({
                "product": p,
                "sku": p.sku,
                "title": p.title,
                "qty_on_hand": qty_on_hand,
                "qty_committed": p.qty_committed,
                "qty_available": p.qty_available,
                "avg_cost": avg_cost,
                "last_cost": last_cost,
                "total_value": value,
                "warning": warning,
                "cost_source": p.cost_source or "manual",
                "recoverable_cost": recoverable_cost,
            })

        return {
            "rows": rows,
            "totals": totals,
            "page": page,
            "page_size": page_size,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "category_id": category_id,
        }

    # ── 5. Open POs ──────────────────────────────────────────────────────────

    def get_open_pos(self) -> dict[str, Any]:
        """
        POs that are not fully received/cancelled.

        Per spec:
          - Return open/partial POs.
          - Vendor, ordered qty, received qty, remaining qty, expected date.
          - Remaining = ordered - received - cancelled  (verified in Phase 1D).

        Returns:
          {
            "as_of": date,
            "rows": [
              {
                "po": PurchaseOrder, "po_number": str, "vendor": Vendor,
                "status": str, "ordered_at": datetime | None,
                "expected_at": datetime | None, "overdue": bool,
                "qty_ordered": int, "qty_received": int,
                "qty_cancelled": int, "qty_remaining": int,
                "outstanding_value": float,
                "lines": [
                  {
                    "po_line": POLine, "product": Product | None,
                    "sku": str, "description": str,
                    "qty_ordered": int, "qty_received": int,
                    "qty_cancelled": int, "qty_remaining": int,
                    "unit_cost": float, "extended": float,
                  }, ...
                ],
              }, ...
            ],
            "totals": {po_count, qty_remaining, outstanding_value},
          }
        """
        as_of = date.today()

        open_statuses = (
            POStatus.VERBAL_ORDER,
            POStatus.DRAFT,
            POStatus.SENT,
            POStatus.PARTIAL,
        )

        pos = (
            self.db.query(PurchaseOrder)
            .options(
                joinedload(PurchaseOrder.lines).joinedload(POLine.product),
                joinedload(PurchaseOrder.vendor),
            )
            .filter(PurchaseOrder.status.in_(open_statuses))
            .order_by(
                PurchaseOrder.expected_at.asc().nulls_last(),
                PurchaseOrder.created_at.asc(),
            )
            .all()
        )

        rows: list[dict[str, Any]] = []
        for po in pos:
            line_rows: list[dict[str, Any]] = []
            po_ordered = 0
            po_received = 0
            po_cancelled = 0
            po_outstanding_value = 0.0

            for ln in po.lines:
                qty_remaining = max(
                    ln.qty_ordered - ln.qty_received - ln.qty_cancelled, 0
                )
                if qty_remaining == 0:
                    continue  # nothing left to receive
                extended = round((ln.unit_cost or 0.0) * qty_remaining, 2)
                line_rows.append({
                    "po_line": ln,
                    "product": ln.product,
                    "sku": ln.product.sku if ln.product else "—",
                    "description": (
                        ln.product.title if ln.product else (ln.description or "")
                    ),
                    "qty_ordered": ln.qty_ordered,
                    "qty_received": ln.qty_received,
                    "qty_cancelled": ln.qty_cancelled,
                    "qty_remaining": qty_remaining,
                    "unit_cost": ln.unit_cost or 0.0,
                    "extended": extended,
                })
                po_ordered += ln.qty_ordered
                po_received += ln.qty_received
                po_cancelled += ln.qty_cancelled
                po_outstanding_value += extended

            if not line_rows:
                continue  # fully fulfilled (status not yet rolled to RECEIVED)

            expected_date = as_date(po.expected_at)
            overdue = expected_date is not None and expected_date < as_of

            rows.append({
                "po": po,
                "po_number": po.po_number,
                "vendor": po.vendor,
                "status": po.status,
                "ordered_at": po.ordered_at,
                "expected_at": po.expected_at,
                "overdue": overdue,
                "qty_ordered": po_ordered,
                "qty_received": po_received,
                "qty_cancelled": po_cancelled,
                "qty_remaining": po_ordered - po_received - po_cancelled,
                "outstanding_value": round(po_outstanding_value, 2),
                "lines": line_rows,
            })

        totals = {
            "po_count":          len(rows),
            "qty_remaining":     sum(r["qty_remaining"] for r in rows),
            "outstanding_value": round(sum(r["outstanding_value"] for r in rows), 2),
        }

        # Smoke check: per-PO qty_remaining = ordered - received - cancelled
        for r in rows:
            expected = r["qty_ordered"] - r["qty_received"] - r["qty_cancelled"]
            if r["qty_remaining"] != expected:
                log.warning(
                    "Open PO %s qty_remaining=%d but ordered-received-cancelled=%d",
                    r["po_number"], r["qty_remaining"], expected,
                )

        return {"as_of": as_of, "rows": rows, "totals": totals}

    # ── 6. Outstanding Core Charges ──────────────────────────────────────────

    def get_core_charges_outstanding(self) -> dict[str, Any]:
        """
        Open customer-owed core charges (the "cores out the door, not yet
        returned" picture). Excludes vendor-direction cores and closed/credited.

        Per spec:
          - Return open customer cores.
          - Customer, invoice/core slip, part, amount, age, overdue flag.

        Returns:
          {
            "as_of": date,
            "rows": [
              {
                "core": CoreCharge,
                "customer": Customer | None,
                "product": Product | None, "sku": str, "description": str,
                "invoice_number": str | None,
                "core_slip_number": str | None,
                "qty_outstanding": int,
                "amount": float,         # qty_outstanding × customer_unit_charge
                "created_at": datetime,
                "age_days": int,
                "return_deadline": datetime | None,
                "overdue": bool,
              }, ...
            ],
            "totals": {
              "core_count": int, "qty_outstanding": int,
              "amount": float, "overdue_count": int,
            },
          }
        """
        as_of = date.today()

        cores = (
            self.db.query(CoreCharge)
            .options(
                joinedload(CoreCharge.customer),
                joinedload(CoreCharge.product),
                joinedload(CoreCharge.core_slip),
            )
            .filter(
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status.in_(_OPEN_CORE_STATUSES),
            )
            .order_by(CoreCharge.created_at.asc())
            .all()
        )

        # Bulk-load invoice numbers for cores with an invoice_line_id
        invoice_line_ids = [
            c.invoice_line_id for c in cores if c.invoice_line_id is not None
        ]
        invoice_number_by_line: dict[int, str] = {}
        if invoice_line_ids:
            ln_rows = (
                self.db.query(InvoiceLine.id, Invoice.invoice_number)
                .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
                .filter(InvoiceLine.id.in_(invoice_line_ids))
                .all()
            )
            invoice_number_by_line = {ln_id: num for ln_id, num in ln_rows}

        rows: list[dict[str, Any]] = []
        for core in cores:
            qty_out = core.qty_outstanding
            if qty_out <= 0:
                continue  # safety — status should already exclude these

            amount = round(core.customer_unit_charge * qty_out, 2)
            created_d = as_date(core.created_at) or as_of
            age_days = max((as_of - created_d).days, 0)

            invoice_number = (
                invoice_number_by_line.get(core.invoice_line_id)
                if core.invoice_line_id else None
            )

            rows.append({
                "core": core,
                "customer": core.customer,
                "product": core.product,
                "sku": core.product.sku if core.product else "—",
                "description": core.product.title if core.product else "",
                "invoice_number": invoice_number,
                "core_slip_number": (
                    core.core_slip.slip_number if core.core_slip else None
                ),
                "qty_outstanding": qty_out,
                "amount": amount,
                "created_at": core.created_at,
                "age_days": age_days,
                "return_deadline": core.return_deadline,
                "overdue": core.is_overdue,
            })

        # Oldest first — what's been outstanding longest is the most urgent
        rows.sort(key=lambda r: r["age_days"], reverse=True)

        totals = {
            "core_count":      len(rows),
            "qty_outstanding": sum(r["qty_outstanding"] for r in rows),
            "amount":          round(sum(r["amount"] for r in rows), 2),
            "overdue_count":   sum(1 for r in rows if r["overdue"]),
        }

        # Smoke check: all rows must be OPEN/PARTIAL + CUSTOMER_OWES_RETURN
        for r in rows:
            c = r["core"]
            if c.status not in _OPEN_CORE_STATUSES:
                log.warning("Outstanding core %d has status %s — expected OPEN/PARTIAL", c.id, c.status)
            if c.direction != CoreDirection.CUSTOMER_OWES_RETURN:
                log.warning("Outstanding core %d direction=%s — expected CUSTOMER_OWES_RETURN", c.id, c.direction)

        return {"as_of": as_of, "rows": rows, "totals": totals}

    # ── 6b. Overdue Cores (§23.3 Phase 3) ─────────────────────────────────────

    def get_overdue_cores(self) -> dict[str, Any]:
        """
        Standalone overdue-cores chase list: open customer cores PAST their
        return deadline. This is the "who do we call today" subset of the
        Outstanding Cores report, so it derives from the same query (single
        source of truth) and adds days_overdue + the customer's phone number.

        Returns:
          {
            "as_of": date,
            "rows": [ ...outstanding-cores row + {
                "days_overdue": int,
                "customer_phone": str | None,
            } ],   # most-overdue first
            "totals": {
              "core_count": int, "qty_outstanding": int,
              "amount": float, "oldest_days_overdue": int,
            },
          }
        """
        base = self.get_core_charges_outstanding()
        as_of = base["as_of"]

        rows: list[dict[str, Any]] = []
        for r in base["rows"]:
            if not r["overdue"] or r["return_deadline"] is None:
                continue
            deadline_d = as_date(r["return_deadline"]) or as_of
            r = dict(r)
            r["days_overdue"] = max((as_of - deadline_d).days, 0)
            r["customer_phone"] = r["customer"].phone if r["customer"] else None
            rows.append(r)

        rows.sort(key=lambda r: r["days_overdue"], reverse=True)

        totals = {
            "core_count":          len(rows),
            "qty_outstanding":     sum(r["qty_outstanding"] for r in rows),
            "amount":              round(sum(r["amount"] for r in rows), 2),
            "oldest_days_overdue": max((r["days_overdue"] for r in rows), default=0),
        }
        return {"as_of": as_of, "rows": rows, "totals": totals}

    # ── 7. Overdue Invoices + Accrued Interest ────────────────────────────────

    def get_overdue_invoices(self, as_of_date: date | None = None) -> dict[str, Any]:
        """
        Invoices past due with outstanding balance. Includes estimated accrued interest.

        Per spec:
          - OPEN + PARTIAL statuses only (not PAID, VOID, DRAFT).
          - due_date < as_of AND balance_due > 0.
          - Invoices with no due_date are excluded (COD — no terms to be late on).
          - Interest calculation (simple monthly, prorated by day):
              days_overdue = (as_of - due_date).days
              grace = customer.interest_grace_days (default 10)
              if days_overdue > grace AND customer.interest_rate > 0:
                  daily_rate = customer.interest_rate / 100 / 30
                  interest = round(balance_due × daily_rate × (days_overdue - grace), 2)
              else:
                  interest = 0.0

        Returns:
          {
            "as_of": date,
            "rows": [
              {
                "invoice": Invoice,
                "customer": Customer,
                "invoice_number": str,
                "due_date": date,
                "days_overdue": int,
                "balance_due": float,
                "interest_accrued": float,
                "total_owed": float,
              }, ...
            ],
            "totals": {
              "invoice_count": int,
              "balance_due": float,
              "interest_accrued": float,
              "total_owed": float,
            }
          }
          Sorted by days_overdue descending (worst first).
        """
        as_of = as_of_date or date.today()

        invoices = (
            self.db.query(Invoice)
            .options(
                joinedload(Invoice.lines),
                joinedload(Invoice.allocations),
                joinedload(Invoice.customer),
            )
            .filter(
                Invoice.status.in_((InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)),
                Invoice.due_date.isnot(None),
                Invoice.due_date < as_of,
            )
            .all()
        )

        rows: list[dict[str, Any]] = []
        for inv in invoices:
            balance = inv.balance_due
            if balance <= 0:
                continue

            due = as_date(inv.due_date)
            if due is None:
                continue  # safety — filter above should already exclude these

            days_overdue = (as_of - due).days
            customer = inv.customer

            # Accrued interest calculation
            grace = getattr(customer, "interest_grace_days", None) or 10
            rate = getattr(customer, "interest_rate", None) or 0.0
            if days_overdue > grace and rate > 0:
                daily_rate = rate / 100 / 30
                interest = round(balance * daily_rate * (days_overdue - grace), 2)
            else:
                interest = 0.0

            rows.append({
                "invoice": inv,
                "customer": customer,
                "invoice_number": inv.invoice_number,
                "due_date": due,
                "days_overdue": days_overdue,
                "balance_due": balance,
                "interest_accrued": interest,
                "total_owed": round(balance + interest, 2),
            })

        # Worst first — most overdue at the top
        rows.sort(key=lambda r: r["days_overdue"], reverse=True)

        totals = {
            "invoice_count":    len(rows),
            "balance_due":      round(sum(r["balance_due"]       for r in rows), 2),
            "interest_accrued": round(sum(r["interest_accrued"]  for r in rows), 2),
            "total_owed":       round(sum(r["total_owed"]         for r in rows), 2),
        }

        return {"as_of": as_of, "rows": rows, "totals": totals}

    # ── 8. Sales Tax Collected ────────────────────────────────────────────────

    def get_sales_tax_collected(
        self, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """
        Sales tax collected from finalized invoices in [start_date, end_date].

        Per spec:
          - OPEN + PARTIAL + PAID statuses (finalized; no DRAFT, no VOID).
          - Sum of invoice_lines.tax_amount per invoice (frozen per-line snapshot).
          - Also compute effective tax rate (total_tax / taxable_revenue × 100).
          - Only rows where tax_collected > 0 are included.

        Returns:
          {
            "start_date": date,
            "end_date": date,
            "rows": [
              {
                "invoice": Invoice,
                "customer": Customer,
                "invoice_number": str,
                "invoice_date": date,
                "taxable_revenue": float,
                "tax_collected": float,
                "invoice_total": float,
              }, ...
            ],
            "totals": {
              "invoice_count": int,
              "taxable_revenue": float,
              "tax_collected": float,
              "effective_rate_pct": float | None,
            }
          }
          Sorted by invoice_date ascending.
        """
        end_exclusive = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        start_dt = datetime.combine(start_date, datetime.min.time())

        invoices = (
            self.db.query(Invoice)
            .options(
                joinedload(Invoice.lines),
                joinedload(Invoice.allocations),
                joinedload(Invoice.customer),
            )
            .filter(
                Invoice.status.in_(_FINALIZED_INVOICE_STATUSES),
                Invoice.created_at >= start_dt,
                Invoice.created_at < end_exclusive,
            )
            .all()
        )

        rows: list[dict[str, Any]] = []
        for inv in invoices:
            # Tax collected = sum of the frozen per-line tax_amount snapshots
            tax_collected = round(
                sum((ln.tax_amount or 0.0) for ln in inv.lines), 2
            )
            if tax_collected <= 0:
                continue  # skip non-taxable invoices

            # Taxable revenue = sum of line totals for taxable lines.
            # line_total is net of the per-line discount — qty × unit_price
            # would overstate the tax base on discounted lines (R1-14).
            taxable_revenue = round(
                sum(
                    ln.line_total
                    for ln in inv.lines
                    if getattr(ln, "is_taxable", False)
                ),
                2,
            )

            invoice_date = as_date(inv.created_at) or date.today()

            rows.append({
                "invoice": inv,
                "customer": inv.customer,
                "invoice_number": inv.invoice_number,
                "invoice_date": invoice_date,
                "taxable_revenue": taxable_revenue,
                "tax_collected": tax_collected,
                "invoice_total": inv.total,
            })

        # Oldest invoices first
        rows.sort(key=lambda r: r["invoice_date"])

        total_tax = round(sum(r["tax_collected"]   for r in rows), 2)
        total_rev = round(sum(r["taxable_revenue"] for r in rows), 2)
        effective_rate_pct = (
            round((total_tax / total_rev) * 100, 4) if total_rev > 0 else None
        )

        totals = {
            "invoice_count":      len(rows),
            "taxable_revenue":    total_rev,
            "tax_collected":      total_tax,
            "effective_rate_pct": effective_rate_pct,
        }

        return {
            "start_date": start_date,
            "end_date":   end_date,
            "rows":       rows,
            "totals":     totals,
        }

    # ── 9. Lost Sales Log ────────────────────────────────────────────────────

    def get_lost_sales(
        self, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """
        Lost-sale log entries in [start_date, end_date].

        Per spec:
          - Query LostSaleLog with Customer, Product, Quote left-outer joined.
          - Date filter: logged_at >= start_dt AND < end_exclusive (inclusive both ends).
          - Return ALL rows in range (no minimum filter).
          - Sort by logged_at DESC.

        Returns:
          {
            "start_date": date,
            "end_date": date,
            "rows": [
              {
                "log": LostSaleLog,
                "logged_at": datetime,
                "customer_name": str,
                "product_sku": str,
                "product_title": str,
                "reason": str,
                "competitor_name": str,
                "competitor_price": float | None,
                "quote_number": str | None,
                "quote_id": int | None,
              }, ...
            ],
            "totals": {
              "count": int,
              "with_competitor": int,
              "top_reasons": dict,
            }
          }
        """
        end_exclusive = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        start_dt = datetime.combine(start_date, datetime.min.time())

        from app.models.quote import Quote  # local import avoids circular reference

        log_entries = (
            self.db.query(LostSaleLog)
            .filter(
                LostSaleLog.logged_at >= start_dt,
                LostSaleLog.logged_at < end_exclusive,
            )
            .order_by(LostSaleLog.logged_at.desc())
            .all()
        )

        # Bulk-load related objects to avoid N+1 queries
        customer_ids = {e.customer_id for e in log_entries if e.customer_id is not None}
        product_ids  = {e.product_id  for e in log_entries if e.product_id  is not None}
        quote_ids    = {e.quote_id    for e in log_entries if e.quote_id    is not None}

        customers_by_id: dict[int, Any] = {}
        if customer_ids:
            customers_by_id = {
                c.id: c
                for c in self.db.query(Customer).filter(Customer.id.in_(customer_ids)).all()
            }

        products_by_id: dict[int, Any] = {}
        if product_ids:
            products_by_id = {
                p.id: p
                for p in self.db.query(Product).filter(Product.id.in_(product_ids)).all()
            }

        quotes_by_id: dict[int, Any] = {}
        if quote_ids:
            quotes_by_id = {
                q.id: q
                for q in self.db.query(Quote).filter(Quote.id.in_(quote_ids)).all()
            }

        rows: list[dict[str, Any]] = []
        for entry in log_entries:
            customer = customers_by_id.get(entry.customer_id) if entry.customer_id else None
            product  = products_by_id.get(entry.product_id)   if entry.product_id  else None
            quote    = quotes_by_id.get(entry.quote_id)       if entry.quote_id    else None

            rows.append({
                "log": entry,
                "logged_at": entry.logged_at,
                "customer_name": customer.company_name if customer else "—",
                "product_sku": product.sku if product else "—",
                "product_title": product.title if product else "—",
                "reason": entry.reason or "—",
                "competitor_name": entry.competitor_name or "—",
                "competitor_price": entry.competitor_price,
                "quote_number": quote.quote_number if quote else None,
                "quote_id": quote.id if quote else None,
            })

        # Totals
        count = len(rows)
        with_competitor = sum(
            1 for r in rows if r["competitor_name"] and r["competitor_name"] != "—"
        )

        reason_counts: dict[str, int] = defaultdict(int)
        for r in rows:
            reason = r["reason"]
            if reason and reason != "—":
                reason_counts[reason] += 1
        top_reasons = dict(
            sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        )

        totals = {
            "count": count,
            "with_competitor": with_competitor,
            "top_reasons": top_reasons,
        }

        return {
            "start_date": start_date,
            "end_date":   end_date,
            "rows":       rows,
            "totals":     totals,
        }

    # ── 10. Low Stock / Reorder ──────────────────────────────────────────────

    def get_low_stock(self) -> dict[str, Any]:
        """
        R2 — printable morning reorder worklist: every active product whose
        stock sits at or below its reorder point, with vendor ordering info.

        Filter (same threshold as the dashboard low-stock counter and the
        products-list "Low stock" tab):
          - is_active
          - reorder_point > 0      (no reorder point set = unmanaged → excluded)
          - qty_on_hand <= reorder_point
        Unlike the products-list tab (which adds qty_on_hand > 0 because full
        stockouts live under its separate "Out of stock" tab), stockouts ARE
        included here — a managed part at zero on-hand is the most urgent
        reorder of all.

        Suggested order qty (documented rule):
          - target = max_stock_level when set (> 0), else reorder_point
            (no max ⇒ restock at least back up to the reorder threshold)
          - suggested = max(target - qty_on_hand - qty_on_order, 0)
            (inbound PO qty counts toward the target; never suggest negative)

        Estimated order cost per row = suggested_qty × estimated unit cost,
        using the same fallback order as the sales reports
        (_fallback_unit_cost: preferred-vendor vendor_cost, then last_cost,
        else 0 — rows costed at 0 understate the Est. Order Cost total).

        Returns:
          {
            "as_of": date,
            "rows": [
              {
                "product": Product, "sku": str, "title": str,
                "category": str,                       # "" when uncategorized
                "qty_on_hand": int, "qty_committed": int,
                "qty_available": int, "qty_on_order": int,
                "reorder_point": int, "max_stock_level": int | None,
                "suggested_qty": int,
                "vendor_name": str | None,             # preferred ACTIVE source
                "vendor_part_number": str | None,
                "vendor_cost": float | None,
                "est_unit_cost": float,
                "est_order_cost": float,
              }, ...
            ],
            "totals": {
              "item_count": int, "stockout_count": int,
              "total_suggested_qty": int, "total_order_cost": float,
              "no_vendor_count": int,
            },
          }
          Rows sorted most-negative availability first (deepest hole on top).
        """
        from app.models.product import ProductVendorSource

        as_of = date.today()

        products = (
            self.db.query(Product)
            .options(
                # Bulk-load sources + their vendors so preferred_vendor_source
                # (a Python property over vendor_sources) never lazy-loads N+1.
                joinedload(Product.vendor_sources)
                .joinedload(ProductVendorSource.vendor),
                joinedload(Product.category),
            )
            .filter(
                Product.is_active == True,  # noqa: E712
                Product.reorder_point > 0,
                Product.qty_on_hand <= Product.reorder_point,
            )
            .order_by(Product.sku)
            .all()
        )

        rows: list[dict[str, Any]] = []
        for p in products:
            qty_on_hand = p.qty_on_hand
            qty_on_order = p.qty_on_order or 0

            # Suggested order qty — see docstring for the rule.
            target = (
                p.max_stock_level
                if (p.max_stock_level or 0) > 0
                else p.reorder_point
            )
            suggested = max(target - qty_on_hand - qty_on_order, 0)

            src = p.preferred_vendor_source  # active+preferred only (§8N)
            est_unit_cost = self._fallback_unit_cost(p)

            rows.append({
                "product": p,
                "sku": p.sku,
                "title": p.title,
                "category": p.category.name if p.category else "",
                "qty_on_hand": qty_on_hand,
                "qty_committed": p.qty_committed,
                "qty_available": p.qty_available,
                "qty_on_order": qty_on_order,
                "reorder_point": p.reorder_point,
                "max_stock_level": p.max_stock_level,
                "suggested_qty": suggested,
                "vendor_name": src.vendor.name if src and src.vendor else None,
                "vendor_part_number": src.vendor_part_number if src else None,
                "vendor_cost": src.vendor_cost if src else None,
                "est_unit_cost": est_unit_cost,
                "est_order_cost": round(suggested * est_unit_cost, 2),
            })

        # Most-negative availability first — committed-beyond-stock parts are
        # the most urgent; ties broken by SKU for a stable printable order.
        rows.sort(key=lambda r: (r["qty_available"], r["sku"]))

        totals = {
            "item_count":          len(rows),
            "stockout_count":      sum(1 for r in rows if r["qty_on_hand"] <= 0),
            "total_suggested_qty": sum(r["suggested_qty"] for r in rows),
            "total_order_cost":    round(sum(r["est_order_cost"] for r in rows), 2),
            "no_vendor_count":     sum(1 for r in rows if r["vendor_name"] is None),
        }

        return {"as_of": as_of, "rows": rows, "totals": totals}

    # ── Dead Stock (§23.3 Phase 2) ────────────────────────────────────────────

    def get_dead_stock(self, days: int = 90) -> dict[str, Any]:
        """
        Active products with stock on hand that have NOT sold in `days` days —
        cash tied up in inventory nobody's buying. Companion to get_low_stock
        (which flags too little stock); this flags too much of the wrong stock.

        "Last sold" = the most recent FINALIZED invoice's created_at across
        every non-core PRODUCT line referencing the SKU (same finalized-status
        set + core-line exclusion as get_sales_by_product, so this ties out
        with the sales reports). Computed as a single GROUP BY subquery joined
        once — never loads invoice_lines/invoices into Python — so it scales to
        the same ~31k-SKU catalog get_inventory_valuation_summary does.

        A product that has NEVER sold (no subquery row at all) is treated as
        maximally dead — sorted first, days_since_sale is None (not a number,
        so the template can print "Never sold" instead of a huge day count).

        Filter:
          - is_active
          - qty_on_hand > 0        (nothing to report on stock that's already gone)
          - last sale is NULL or older than `days` days ago

        Returns:
          {
            "as_of": date, "cutoff_date": date, "days": int,
            "rows": [
              {
                "product": Product, "sku": str, "title": str, "category": str,
                "qty_on_hand": int, "cost": float, "tied_up_value": float,
                "last_sold_at": datetime | None, "days_since_sale": int | None,
              }, ...
            ],  # never-sold first, then oldest last-sale first
            "totals": {item_count, total_units, total_tied_up_value, never_sold_count},
          }
        """
        as_of = date.today()
        cutoff_dt = datetime.combine(as_of, datetime.min.time()) - timedelta(days=days)

        last_sold_subq = (
            self.db.query(
                InvoiceLine.product_id.label("product_id"),
                func.max(Invoice.created_at).label("last_sold_at"),
            )
            .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
            .filter(
                Invoice.status.in_(_FINALIZED_INVOICE_STATUSES),
                InvoiceLine.product_id.isnot(None),
                InvoiceLine.is_core_line == False,  # noqa: E712 — a core line isn't a part sale
            )
            .group_by(InvoiceLine.product_id)
            .subquery()
        )

        results = (
            self.db.query(Product, last_sold_subq.c.last_sold_at)
            .outerjoin(last_sold_subq, last_sold_subq.c.product_id == Product.id)
            .options(joinedload(Product.category))
            .filter(
                Product.is_active == True,  # noqa: E712
                Product.qty_on_hand > 0,
            )
            .filter(
                (last_sold_subq.c.last_sold_at.is_(None))
                | (last_sold_subq.c.last_sold_at < cutoff_dt)
            )
            .order_by(last_sold_subq.c.last_sold_at.asc().nullsfirst(), Product.sku)
            .all()
        )

        rows: list[dict[str, Any]] = []
        for p, last_sold_at in results:
            cost = p.cost or 0.0
            days_since_sale = (
                (datetime.combine(as_of, datetime.min.time()) - last_sold_at).days
                if last_sold_at else None
            )
            rows.append({
                "product": p,
                "sku": p.sku,
                "title": p.title,
                "category": p.category.name if p.category else "",
                "qty_on_hand": p.qty_on_hand,
                "cost": cost,
                "tied_up_value": round(p.qty_on_hand * cost, 2),
                "last_sold_at": last_sold_at,
                "days_since_sale": days_since_sale,
            })

        totals = {
            "item_count":          len(rows),
            "total_units":         sum(r["qty_on_hand"] for r in rows),
            "total_tied_up_value": round(sum(r["tied_up_value"] for r in rows), 2),
            "never_sold_count":    sum(1 for r in rows if r["last_sold_at"] is None),
        }

        return {"as_of": as_of, "cutoff_date": cutoff_dt.date(), "days": days,
                "rows": rows, "totals": totals}

    # ── Inventory Movement History (audit follow-up) ──────────────────────────

    def get_inventory_movement(
        self,
        *,
        sku_query: str | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Append-only ledger view: every InventoryTransaction (receipt, sale,
        return, void correction, transfer, manual adjust) in the window, newest
        first, so staff can trace WHY a SKU's on-hand changed — the gap flagged
        in the audit. Optional SKU substring filter + date range. Capped at
        ``limit`` rows; ``truncated`` is set when more matched than were returned.
        """
        from app.models.inventory import InventoryTransaction
        q = (
            self.db.query(InventoryTransaction)
            .join(Product, InventoryTransaction.product_id == Product.id)
            .options(joinedload(InventoryTransaction.product))
        )
        if sku_query and sku_query.strip():
            q = q.filter(Product.sku.ilike(f"%{sku_query.strip()}%"))
        if start:
            q = q.filter(InventoryTransaction.performed_at >= datetime(start.year, start.month, start.day))
        if end:
            _end_dt = datetime(end.year, end.month, end.day) + timedelta(days=1)  # inclusive
            q = q.filter(InventoryTransaction.performed_at < _end_dt)

        total_matched = q.count()
        txns = (
            q.order_by(InventoryTransaction.performed_at.desc(), InventoryTransaction.id.desc())
            .limit(limit).all()
        )
        rows = [{
            "created_at": t.performed_at,
            "product_id": t.product_id,
            "sku": t.product.sku if t.product else "",
            "title": t.product.title if t.product else "",
            "transaction_type": t.transaction_type,
            "qty_change": t.qty_change,
            "qty_after": t.qty_after,
            "reference_type": t.reference_type,
            "reference_id": t.reference_id,
            "notes": t.notes or "",
        } for t in txns]
        totals = {
            "row_count": len(rows),
            "total_matched": total_matched,
            "truncated": total_matched > len(rows),
        }
        return {
            "rows": rows, "totals": totals,
            "start": start, "end": end, "sku_query": (sku_query or "").strip(),
        }

    # ── QBO Unsynced Transactions (audit follow-up) ───────────────────────────

    def get_qbo_unsynced(self) -> dict[str, Any]:
        """Finalized invoices whose QBO sync is still PENDING or in ERROR — the
        drill-down behind the dashboard QBO chip (audit: the count existed but
        there was no list). ERROR rows first (they need action), then oldest
        PENDING. Read-only. (Payments/vendor-bills/credit-memos also carry the
        QBO mixin; surfacing those here is a straightforward follow-up.)"""
        from app.constants import InvoiceStatus, QBOSyncStatus
        unsynced = (QBOSyncStatus.PENDING, QBOSyncStatus.ERROR)
        invs = (
            self.db.query(Invoice)
            .filter(
                Invoice.qbo_sync_status.in_(unsynced),
                Invoice.status != InvoiceStatus.DRAFT,
            )
            .options(joinedload(Invoice.customer))
            .all()
        )
        rows = [{
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "customer": inv.customer.company_name if inv.customer else "",
            "total": inv.total,
            "status": inv.qbo_sync_status,
            "error": inv.qbo_sync_error or "",
            "retry_count": getattr(inv, "qbo_sync_retry_count", 0) or 0,
            "last_synced_at": inv.qbo_last_synced_at,
            "created_at": inv.created_at,
        } for inv in invs]
        # ERROR first, then PENDING; within each, oldest first (longest stuck).
        rows.sort(key=lambda r: (r["status"] != QBOSyncStatus.ERROR, r["created_at"] or datetime.min))
        totals = {
            "count": len(rows),
            "error_count": sum(1 for r in rows if r["status"] == QBOSyncStatus.ERROR),
            "pending_count": sum(1 for r in rows if r["status"] == QBOSyncStatus.PENDING),
            "amount": round(sum(r["total"] for r in rows), 2),
        }
        return {"rows": rows, "totals": totals}
