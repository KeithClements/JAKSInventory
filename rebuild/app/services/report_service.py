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
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

from sqlalchemy.orm import Session, joinedload

from app.constants import (
    CoreDirection, CoreStatus, FulfillmentSource,
    InvoiceStatus, POStatus, SOLineStatus, SOStatus,
)
from app.models.core import CoreCharge, CoreSlip
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine
from app.models.quote import LostSaleLog, SalesOrder, SOLine
from app.services.base import BaseService


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

# Aging bucket keys — kept in display order
_AGING_BUCKETS = ("current", "1_30", "31_60", "61_90", "over_90")


def _as_date(value: Any) -> date | None:
    """Normalize a datetime/date/None to a date for day-diff math."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def _zero_buckets() -> dict[str, float]:
    return {k: 0.0 for k in _AGING_BUCKETS}


class ReportService(BaseService):
    """Read-only report queries. Inherits db + audit machinery but never writes."""

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
            **_zero_buckets(),
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
            reference_date = _as_date(inv.due_date) or _as_date(inv.created_at)
            if reference_date is None:
                # Truly no date — treat as current rather than crashing
                row["current"] = round(row["current"] + balance, 2)
                continue

            days_late = (as_of - reference_date).days
            bucket = self._bucket_for(days_late)
            row[bucket] = round(row[bucket] + balance, 2)

        # Sort by total descending — biggest debtors at the top
        rows = sorted(aging.values(), key=lambda r: r["total"], reverse=True)

        totals = {b: round(sum(r[b] for r in rows), 2) for b in _AGING_BUCKETS}
        totals["total"] = round(sum(r["total"] for r in rows), 2)

        # Smoke check: bucket columns must sum to grand total
        bucket_sum = round(sum(totals[b] for b in _AGING_BUCKETS), 2)
        if abs(bucket_sum - totals["total"]) > 0.02:
            log.warning(
                "AR aging bucket sum %.2f != totals.total %.2f (as_of=%s)",
                bucket_sum, totals["total"], as_of,
            )

        return {"as_of": as_of, "rows": rows, "totals": totals}

    @staticmethod
    def _bucket_for(days_late: int) -> str:
        if days_late <= 0:
            return "current"
        if days_late <= 30:
            return "1_30"
        if days_late <= 60:
            return "31_60"
        if days_late <= 90:
            return "61_90"
        return "over_90"

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

        for inv in invoices:
            row = by_customer[inv.customer_id]
            row["customer"] = inv.customer
            row["invoice_count"] += 1
            row["gross_sales"] = round(row["gross_sales"] + inv.total, 2)
            row["payments_received"] = round(
                row["payments_received"] + inv.amount_paid, 2
            )
            row["balance_due"] = round(row["balance_due"] + inv.balance_due, 2)
            # Cost snapshot is per-line; sum across all lines
            line_cost = sum(
                (ln.unit_cost or 0.0) * ln.qty for ln in inv.lines
            )
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

        for ln in lines:
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

            row["qty_sold"] += ln.qty
            row["revenue"] = round(row["revenue"] + ln.line_total, 2)
            row["cost"] = round(
                row["cost"] + (ln.unit_cost or 0.0) * ln.qty, 2
            )

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
        }

    # ── 4. Inventory Valuation ───────────────────────────────────────────────

    def get_inventory_valuation(self) -> dict[str, Any]:
        """
        Per-active-product valuation snapshot.

        Per spec:
          - Active products only.
          - Return qty_on_hand, avg cost (Product.cost), last_cost, total value.
          - Bottom totals.
          - Flag zero/negative cost with warning field.

        Returns:
          {
            "rows": [
              {
                "product": Product, "sku": str, "title": str,
                "qty_on_hand": int, "qty_committed": int, "qty_available": int,
                "avg_cost": float, "last_cost": float,
                "total_value": float,
                "warning": str | None,   # "zero_cost" | "negative_qty" | None
              }, ...
            ],
            "totals": {
              "sku_count": int,
              "in_stock_skus": int,
              "total_units": int,
              "total_value": float,
              "zero_cost_count": int,
            },
          }
        """
        products = (
            self.db.query(Product)
            .filter(Product.is_active == True)  # noqa: E712
            .order_by(Product.sku)
            .all()
        )

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
            })

        # Sort so the highest-value SKUs appear first (most useful for the table)
        rows.sort(key=lambda r: r["total_value"], reverse=True)

        totals = {
            "sku_count":       len(rows),
            "in_stock_skus":   sum(1 for r in rows if r["qty_on_hand"] > 0),
            "total_units":     sum(r["qty_on_hand"] for r in rows if r["qty_on_hand"] > 0),
            "total_value":     round(sum(r["total_value"] for r in rows), 2),
            "zero_cost_count": sum(1 for r in rows if r["warning"] == "zero_cost"),
        }

        # Smoke check: total_value must equal sum of qty × avg_cost per row
        computed = round(sum(r["qty_on_hand"] * r["avg_cost"] for r in rows), 2)
        if abs(computed - totals["total_value"]) > 0.02:
            log.warning(
                "Inventory valuation drift: computed=%.2f totals.total_value=%.2f",
                computed, totals["total_value"],
            )

        return {"rows": rows, "totals": totals}

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

            expected_date = _as_date(po.expected_at)
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
            created_d = _as_date(core.created_at) or as_of
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

            due = _as_date(inv.due_date)
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

            # Taxable revenue = sum of line totals for taxable lines
            taxable_revenue = round(
                sum(
                    ln.qty * ln.unit_price
                    for ln in inv.lines
                    if getattr(ln, "is_taxable", False)
                ),
                2,
            )

            invoice_date = _as_date(inv.created_at) or date.today()

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
