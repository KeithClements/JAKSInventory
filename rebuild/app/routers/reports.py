"""
app/routers/reports.py
======================
Reports & Analytics — Series 1.

Reports are READ-ONLY.  No route here may mutate the database.
All numbers come from ReportService; routers are thin.

Canonical routes (Series 1):
  GET /reports                       — landing index
  GET /reports/ar-aging              — AR aging buckets
  GET /reports/sales-by-customer     — finalized invoice revenue per customer
  GET /reports/sales-by-product      — finalized invoice revenue per SKU
  GET /reports/inventory-valuation   — on-hand × cost
  GET /reports/open-pos              — POs not fully received
  GET /reports/outstanding-cores     — customer-owed cores still out
  GET /reports/lost-sales            — lost-sale log entries with competitor data
  GET /reports/low-stock             — reorder worklist (at/below reorder point)

Back-compat redirects from the previous URL shape are at the bottom of the file
so existing sidebar/bookmark links keep working.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services.report_service import ReportService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


# ── Date helpers ──────────────────────────────────────────────────────────────

def _parse_date(s: str | None) -> date | None:
    """Parse YYYY-MM-DD; return None on missing or malformed input."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_range(start: str | None, end: str | None) -> tuple[date, date]:
    """
    Default range is the current month-to-date when neither bound is given.
    A missing single bound is filled in:
      - start missing  → first of the end month
      - end missing    → today
    """
    today = date.today()
    s = _parse_date(start)
    e = _parse_date(end)
    if s is None and e is None:
        return today.replace(day=1), today
    if s is None:
        return e.replace(day=1), e  # type: ignore[union-attr]
    if e is None:
        return s, today
    return s, e


def _csv_response(header: list[str], data_rows: list[list], filename: str) -> StreamingResponse:
    """Render rows as a text/csv attachment (same pattern as /products/export.csv)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(data_rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Landing ───────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def reports_index(request: Request, db: Session = Depends(get_db)):
    """
    Reports landing — directory of the six Series 1 reports with light context
    (totals snapshot) so the user gets a one-glance read of where things stand
    before drilling in.
    """
    svc = ReportService(db)
    today = date.today()
    month_start = today.replace(day=1)

    error_message = None
    ar_total = ar_over_90 = inv_value = inv_skus = 0.0
    po_count = core_overdue = 0
    po_value = core_amount = mtd_revenue = mtd_margin = 0.0
    mtd_margin_pct = None
    overdue_total = 0.0
    overdue_count = 0
    mtd_tax_collected = 0.0
    lost_count = 0
    low_stock_count = 0
    low_stock_order_cost = 0.0

    try:
        ar = svc.get_ar_aging()
        inv = svc.get_inventory_valuation()
        pos = svc.get_open_pos()
        cores = svc.get_core_charges_outstanding()
        sales_mtd = svc.get_sales_by_customer(month_start, today)
        ar_total     = ar["totals"]["total"]
        ar_over_90   = ar["totals"]["over_90"]
        inv_value    = inv["totals"]["total_value"]
        inv_skus     = inv["totals"]["in_stock_skus"]
        po_count     = pos["totals"]["po_count"]
        po_value     = pos["totals"]["outstanding_value"]
        core_amount  = cores["totals"]["amount"]
        core_overdue = cores["totals"]["overdue_count"]
        mtd_revenue  = sales_mtd["totals"]["gross_sales"]
        mtd_margin   = sales_mtd["totals"]["margin"]
        mtd_margin_pct = sales_mtd["totals"].get("margin_pct")
        overdue_data = svc.get_overdue_invoices()
        overdue_total = overdue_data["totals"]["total_owed"]
        overdue_count = overdue_data["totals"]["invoice_count"]
        tax_data = svc.get_sales_tax_collected(month_start, today)
        mtd_tax_collected = tax_data["totals"]["tax_collected"]
        lost_mtd = svc.get_lost_sales(month_start, today)
        lost_count = lost_mtd["totals"]["count"]
        low_stock = svc.get_low_stock()
        low_stock_count = low_stock["totals"]["item_count"]
        low_stock_order_cost = low_stock["totals"]["total_order_cost"]
    except Exception:
        log.exception("reports_index: ReportService failed")
        error_message = "Could not load report snapshot. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/index.html",
        {
            "today": today,
            "error_message": error_message,
            "ar_total":      ar_total,
            "ar_over_90":    ar_over_90,
            "inv_value":     inv_value,
            "inv_skus":      inv_skus,
            "po_count":      po_count,
            "po_value":      po_value,
            "core_amount":   core_amount,
            "core_overdue":  core_overdue,
            "mtd_revenue":   mtd_revenue,
            "mtd_margin":    mtd_margin,
            "mtd_margin_pct": mtd_margin_pct,
            "month_label":   today.strftime("%B %Y"),
            "overdue_total":      overdue_total,
            "overdue_count":      overdue_count,
            "mtd_tax_collected":  mtd_tax_collected,
            "lost_count":         lost_count,
            "low_stock_count":      low_stock_count,
            "low_stock_order_cost": low_stock_order_cost,
        },
    )


# ── AR Aging ──────────────────────────────────────────────────────────────────

@router.get("/ar-aging", response_class=HTMLResponse)
def reports_ar_aging(
    request: Request,
    as_of: str | None = None,
    db: Session = Depends(get_db),
):
    as_of_date = _parse_date(as_of) or date.today()
    error_message = None
    rows: list = []
    totals = {b: 0.0 for b in ("current", "1_30", "31_60", "61_90", "over_90", "total")}
    try:
        data = ReportService(db).get_ar_aging(as_of_date)
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_ar_aging failed (as_of=%s)", as_of_date)
        error_message = "Could not load AR aging data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/ar_aging.html",
        {
            "today": as_of_date,
            "as_of": as_of_date,
            "aging_rows": rows,   # legacy template variable
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


@router.get("/ar-aging/export.csv")
def reports_ar_aging_export(
    as_of: str | None = None,
    db: Session = Depends(get_db),
):
    as_of_date = _parse_date(as_of) or date.today()
    data = ReportService(db).get_ar_aging(as_of_date)
    return _csv_response(
        ["customer", "invoice_count", "current", "1_30", "31_60", "61_90", "over_90", "total"],
        [
            [
                r["customer"].company_name if r["customer"] else "",
                r["invoice_count"],
                f"{r['current']:.2f}",
                f"{r['1_30']:.2f}",
                f"{r['31_60']:.2f}",
                f"{r['61_90']:.2f}",
                f"{r['over_90']:.2f}",
                f"{r['total']:.2f}",
            ]
            for r in data["rows"]
        ],
        f"ar_aging_{as_of_date.isoformat()}.csv",
    )


# ── AP Aging (payables) — §21 ─────────────────────────────────────────────────

@router.get("/ap-aging", response_class=HTMLResponse)
def reports_ap_aging(request: Request, as_of: str | None = None, db: Session = Depends(get_db)):
    as_of_date = _parse_date(as_of) or date.today()
    error_message = None
    rows: list = []
    totals = {b: 0.0 for b in ("current", "1_30", "31_60", "61_90", "over_90", "total")}
    try:
        data = ReportService(db).get_ap_aging(as_of_date)
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_ap_aging failed (as_of=%s)", as_of_date)
        error_message = "Could not load AP aging data. Check server logs for details."
    return templates.TemplateResponse(
        request, "reports/ap_aging.html",
        {"as_of": as_of_date, "rows": rows, "totals": totals, "error_message": error_message},
    )


@router.get("/ap-aging/export.csv")
def reports_ap_aging_export(as_of: str | None = None, db: Session = Depends(get_db)):
    as_of_date = _parse_date(as_of) or date.today()
    data = ReportService(db).get_ap_aging(as_of_date)
    return _csv_response(
        ["vendor", "bill_count", "current", "1_30", "31_60", "61_90", "over_90", "total"],
        [
            [
                r["vendor"].name if r["vendor"] else "",
                r["bill_count"], f"{r['current']:.2f}", f"{r['1_30']:.2f}",
                f"{r['31_60']:.2f}", f"{r['61_90']:.2f}", f"{r['over_90']:.2f}",
                f"{r['total']:.2f}",
            ]
            for r in data["rows"]
        ],
        f"ap_aging_{as_of_date.isoformat()}.csv",
    )


# ── Quote conversion + Vendor performance (§21.10) ────────────────────────────

@router.get("/quote-conversion", response_class=HTMLResponse)
def reports_quote_conversion(request: Request, start: str | None = None,
                             end: str | None = None, db: Session = Depends(get_db)):
    start_date, end_date = _resolve_range(start, end)
    error_message = None
    data = {"total": 0, "won": 0, "lost": 0, "pending": 0, "no_decision": 0,
            "conversion_rate": None, "won_value": 0.0, "total_value": 0.0}
    try:
        data = ReportService(db).get_quote_conversion(start_date, end_date)
    except Exception:
        log.exception("reports_quote_conversion failed (%s–%s)", start_date, end_date)
        error_message = "Could not load quote conversion data."
    return templates.TemplateResponse(
        request, "reports/quote_conversion.html",
        {"start_date": start_date, "end_date": end_date, "data": data,
         "error_message": error_message},
    )


@router.get("/vendor-performance", response_class=HTMLResponse)
def reports_vendor_performance(request: Request, start: str | None = None,
                               end: str | None = None, db: Session = Depends(get_db)):
    start_date, end_date = _resolve_range(start, end)
    error_message = None
    rows: list = []
    try:
        rows = ReportService(db).get_vendor_performance(start_date, end_date)["rows"]
    except Exception:
        log.exception("reports_vendor_performance failed (%s–%s)", start_date, end_date)
        error_message = "Could not load vendor performance data."
    return templates.TemplateResponse(
        request, "reports/vendor_performance.html",
        {"start_date": start_date, "end_date": end_date, "rows": rows,
         "error_message": error_message},
    )


@router.get("/vendor-performance/export.csv")
def reports_vendor_performance_export(start: str | None = None, end: str | None = None,
                                      db: Session = Depends(get_db)):
    start_date, end_date = _resolve_range(start, end)
    data = ReportService(db).get_vendor_performance(start_date, end_date)
    return _csv_response(
        ["vendor", "po_count", "po_value", "qty_ordered", "qty_received",
         "fill_rate_pct", "bills", "discrepancy_bills"],
        [
            [
                r["vendor"].name if r["vendor"] else "", r["po_count"],
                f"{r['po_value']:.2f}", r["qty_ordered"], r["qty_received"],
                f"{r['fill_rate']:.1f}" if r["fill_rate"] is not None else "",
                r["bills"], r["discrepancy_bills"],
            ]
            for r in data["rows"]
        ],
        f"vendor_performance_{start_date.isoformat()}_{end_date.isoformat()}.csv",
    )


# ── Sales by Customer ─────────────────────────────────────────────────────────

@router.get("/sales-by-customer", response_class=HTMLResponse)
def reports_sales_by_customer(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    error_message = None
    rows: list = []
    totals = {"invoice_count": 0, "gross_sales": 0.0, "payments_received": 0.0,
              "balance_due": 0.0, "cost": 0.0, "margin": 0.0, "margin_pct": None}
    cost_estimated_lines = zero_cost_lines = 0
    try:
        data = ReportService(db).get_sales_by_customer(start_date, end_date)
        rows = data["rows"]
        totals = data["totals"]
        cost_estimated_lines = data["cost_estimated_lines"]
        zero_cost_lines = data["zero_cost_lines"]
    except Exception:
        log.exception("reports_sales_by_customer failed (%s–%s)", start_date, end_date)
        error_message = "Could not load sales data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/sales_by_customer.html",
        {
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "cost_estimated_lines": cost_estimated_lines,
            "zero_cost_lines": zero_cost_lines,
            "error_message": error_message,
        },
    )


# ── Sales by Product ──────────────────────────────────────────────────────────

@router.get("/sales-by-product", response_class=HTMLResponse)
def reports_sales_by_product(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    error_message = None
    rows: list = []
    totals = {"qty_sold": 0, "revenue": 0.0, "cost": 0.0, "margin": 0.0, "margin_pct": None}
    cost_estimated_lines = zero_cost_lines = 0
    try:
        data = ReportService(db).get_sales_by_product(start_date, end_date)
        rows = data["rows"]
        totals = data["totals"]
        cost_estimated_lines = data["cost_estimated_lines"]
        zero_cost_lines = data["zero_cost_lines"]
    except Exception:
        log.exception("reports_sales_by_product failed (%s–%s)", start_date, end_date)
        error_message = "Could not load product sales data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/sales_by_product.html",
        {
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "cost_estimated_lines": cost_estimated_lines,
            "zero_cost_lines": zero_cost_lines,
            "error_message": error_message,
        },
    )


# ── Inventory Valuation ───────────────────────────────────────────────────────

@router.get("/inventory-valuation", response_class=HTMLResponse)
def reports_inventory_valuation(request: Request, db: Session = Depends(get_db)):
    error_message = None
    rows: list = []
    totals = {"sku_count": 0, "in_stock_skus": 0, "total_units": 0, "total_value": 0.0, "zero_cost_count": 0}
    try:
        data = ReportService(db).get_inventory_valuation()
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_inventory_valuation failed")
        error_message = "Could not load inventory data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/inventory_valuation.html",
        {
            "today": date.today(),
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


# ── Open POs ──────────────────────────────────────────────────────────────────

@router.get("/open-pos", response_class=HTMLResponse)
def reports_open_pos(request: Request, db: Session = Depends(get_db)):
    error_message = None
    today = date.today()
    rows: list = []
    totals = {"po_count": 0, "qty_remaining": 0, "outstanding_value": 0.0}
    try:
        data = ReportService(db).get_open_pos()
        today = data["as_of"]
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_open_pos failed")
        error_message = "Could not load open PO data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/open_pos.html",
        {
            "today": today,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


# ── Outstanding Cores ─────────────────────────────────────────────────────────

@router.get("/outstanding-cores", response_class=HTMLResponse)
def reports_outstanding_cores(request: Request, db: Session = Depends(get_db)):
    error_message = None
    today = date.today()
    rows: list = []
    totals = {"core_count": 0, "qty_outstanding": 0, "amount": 0.0, "overdue_count": 0}
    try:
        data = ReportService(db).get_core_charges_outstanding()
        today = data["as_of"]
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_outstanding_cores failed")
        error_message = "Could not load core charge data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/outstanding_cores.html",
        {
            "today": today,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


# ── Overdue Invoices + Accrued Interest ──────────────────────────────────────

@router.get("/overdue-invoices", response_class=HTMLResponse)
def reports_overdue_invoices(
    request: Request,
    as_of: str | None = None,
    db: Session = Depends(get_db),
):
    as_of_date = _parse_date(as_of) or date.today()
    error_message = None
    rows: list = []
    totals = {"invoice_count": 0, "balance_due": 0.0, "interest_accrued": 0.0, "total_owed": 0.0}
    try:
        data = ReportService(db).get_overdue_invoices(as_of_date)
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_overdue_invoices failed (as_of=%s)", as_of_date)
        error_message = "Could not load overdue invoices data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/overdue_invoices.html",
        {
            "as_of": as_of_date,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


@router.get("/overdue-invoices/export.csv")
def reports_overdue_invoices_export(
    as_of: str | None = None,
    db: Session = Depends(get_db),
):
    as_of_date = _parse_date(as_of) or date.today()
    data = ReportService(db).get_overdue_invoices(as_of_date)
    return _csv_response(
        ["invoice_number", "customer", "due_date", "days_overdue",
         "balance_due", "interest_accrued", "total_owed"],
        [
            [
                r["invoice_number"],
                r["customer"].company_name if r["customer"] else "",
                r["due_date"].isoformat(),
                r["days_overdue"],
                f"{r['balance_due']:.2f}",
                f"{r['interest_accrued']:.2f}",
                f"{r['total_owed']:.2f}",
            ]
            for r in data["rows"]
        ],
        f"overdue_invoices_{as_of_date.isoformat()}.csv",
    )


# ── Sales Tax Collected ───────────────────────────────────────────────────────

@router.get("/sales-tax", response_class=HTMLResponse)
def reports_sales_tax(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    error_message = None
    rows: list = []
    totals = {"invoice_count": 0, "taxable_revenue": 0.0, "tax_collected": 0.0, "effective_rate_pct": None}
    try:
        data = ReportService(db).get_sales_tax_collected(start_date, end_date)
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_sales_tax failed (%s–%s)", start_date, end_date)
        error_message = "Could not load sales tax data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/sales_tax.html",
        {
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


@router.get("/sales-tax/export.csv")
def reports_sales_tax_export(
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    data = ReportService(db).get_sales_tax_collected(start_date, end_date)
    return _csv_response(
        ["invoice_number", "customer", "invoice_date",
         "taxable_revenue", "tax_collected", "invoice_total"],
        [
            [
                r["invoice_number"],
                r["customer"].company_name if r["customer"] else "",
                r["invoice_date"].isoformat(),
                f"{r['taxable_revenue']:.2f}",
                f"{r['tax_collected']:.2f}",
                f"{r['invoice_total']:.2f}",
            ]
            for r in data["rows"]
        ],
        f"sales_tax_{start_date.isoformat()}_{end_date.isoformat()}.csv",
    )


# ── Lost Sales Log ───────────────────────────────────────────────────────────

@router.get("/lost-sales", response_class=HTMLResponse)
def reports_lost_sales(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    error_message = None
    rows: list = []
    totals = {"count": 0, "with_competitor": 0, "top_reasons": {}}
    try:
        data = ReportService(db).get_lost_sales(start_date, end_date)
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_lost_sales failed (%s–%s)", start_date, end_date)
        error_message = "Could not load lost sales data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/lost_sales.html",
        {
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


# ── Low Stock / Reorder ──────────────────────────────────────────────────────

@router.get("/low-stock", response_class=HTMLResponse)
def reports_low_stock(request: Request, db: Session = Depends(get_db)):
    error_message = None
    today = date.today()
    rows: list = []
    totals = {"item_count": 0, "stockout_count": 0, "total_suggested_qty": 0,
              "total_order_cost": 0.0, "no_vendor_count": 0}
    try:
        data = ReportService(db).get_low_stock()
        today = data["as_of"]
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_low_stock failed")
        error_message = "Could not load low stock data. Check server logs for details."

    return templates.TemplateResponse(
        request,
        "reports/low_stock.html",
        {
            "today": today,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
    )


@router.get("/low-stock/export.csv")
def reports_low_stock_export(db: Session = Depends(get_db)):
    data = ReportService(db).get_low_stock()
    return _csv_response(
        ["sku", "title", "category", "qty_on_hand", "qty_committed",
         "qty_available", "qty_on_order", "reorder_point", "max_stock_level",
         "suggested_order_qty", "vendor", "vendor_part_number", "vendor_cost",
         "est_order_cost"],
        [
            [
                r["sku"],
                r["title"],
                r["category"],
                r["qty_on_hand"],
                r["qty_committed"],
                r["qty_available"],
                r["qty_on_order"],
                r["reorder_point"],
                r["max_stock_level"] if r["max_stock_level"] is not None else "",
                r["suggested_qty"],
                r["vendor_name"] or "",
                r["vendor_part_number"] or "",
                f"{r['vendor_cost']:.2f}" if r["vendor_cost"] is not None else "",
                f"{r['est_order_cost']:.2f}",
            ]
            for r in data["rows"]
        ],
        f"low_stock_{data['as_of'].isoformat()}.csv",
    )


# ── §21 — the 6 previously-missing CSV exports (mirror the ar-aging pattern) ──

@router.get("/sales-by-customer/export.csv")
def reports_sales_by_customer_export(
    start: str | None = None, end: str | None = None, db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    data = ReportService(db).get_sales_by_customer(start_date, end_date)
    return _csv_response(
        ["customer", "invoice_count", "gross_sales", "payments_received",
         "balance_due", "cost", "margin", "margin_pct"],
        [
            [
                r["customer"].company_name if r["customer"] else "",
                r["invoice_count"], f"{r['gross_sales']:.2f}",
                f"{r['payments_received']:.2f}", f"{r['balance_due']:.2f}",
                f"{r['cost']:.2f}", f"{r['margin']:.2f}",
                f"{r['margin_pct']:.1f}" if r["margin_pct"] is not None else "",
            ]
            for r in data["rows"]
        ],
        f"sales_by_customer_{start_date.isoformat()}_{end_date.isoformat()}.csv",
    )


@router.get("/sales-by-product/export.csv")
def reports_sales_by_product_export(
    start: str | None = None, end: str | None = None, db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    data = ReportService(db).get_sales_by_product(start_date, end_date)
    return _csv_response(
        ["sku", "description", "qty_sold", "revenue", "cost", "margin", "margin_pct"],
        [
            [
                r["sku"], r["description"], r["qty_sold"], f"{r['revenue']:.2f}",
                f"{r['cost']:.2f}", f"{r['margin']:.2f}",
                f"{r['margin_pct']:.1f}" if r["margin_pct"] is not None else "",
            ]
            for r in data["rows"]
        ],
        f"sales_by_product_{start_date.isoformat()}_{end_date.isoformat()}.csv",
    )


@router.get("/inventory-valuation/export.csv")
def reports_inventory_valuation_export(db: Session = Depends(get_db)):
    data = ReportService(db).get_inventory_valuation()
    return _csv_response(
        ["sku", "title", "qty_on_hand", "qty_committed", "qty_available",
         "avg_cost", "last_cost", "total_value", "warning"],
        [
            [
                r["sku"], r["title"], r["qty_on_hand"], r["qty_committed"],
                r["qty_available"], f"{r['avg_cost']:.2f}", f"{r['last_cost']:.2f}",
                f"{r['total_value']:.2f}", r.get("warning") or "",
            ]
            for r in data["rows"]
        ],
        f"inventory_valuation_{date.today().isoformat()}.csv",
    )


@router.get("/open-pos/export.csv")
def reports_open_pos_export(db: Session = Depends(get_db)):
    data = ReportService(db).get_open_pos()
    return _csv_response(
        ["po_number", "vendor", "status", "ordered_at", "expected_at", "overdue",
         "qty_ordered", "qty_received", "qty_remaining", "outstanding_value"],
        [
            [
                r["po_number"], r["vendor"].name if r["vendor"] else "",
                r["status"],
                r["ordered_at"].date().isoformat() if r["ordered_at"] else "",
                r["expected_at"].date().isoformat() if r["expected_at"] else "",
                "yes" if r["overdue"] else "",
                r["qty_ordered"], r["qty_received"], r["qty_remaining"],
                f"{r['outstanding_value']:.2f}",
            ]
            for r in data["rows"]
        ],
        f"open_pos_{data['as_of'].isoformat()}.csv",
    )


@router.get("/outstanding-cores/export.csv")
def reports_outstanding_cores_export(db: Session = Depends(get_db)):
    data = ReportService(db).get_core_charges_outstanding()
    return _csv_response(
        ["sku", "description", "customer", "invoice_number", "core_slip_number",
         "qty_outstanding", "amount", "age_days", "return_deadline", "overdue"],
        [
            [
                r["sku"], r["description"],
                r["customer"].company_name if r["customer"] else "",
                r["invoice_number"] or "", r["core_slip_number"] or "",
                r["qty_outstanding"], f"{r['amount']:.2f}", r["age_days"],
                r["return_deadline"].date().isoformat() if r["return_deadline"] else "",
                "yes" if r["overdue"] else "",
            ]
            for r in data["rows"]
        ],
        f"outstanding_cores_{data['as_of'].isoformat()}.csv",
    )


@router.get("/lost-sales/export.csv")
def reports_lost_sales_export(
    start: str | None = None, end: str | None = None, db: Session = Depends(get_db),
):
    start_date, end_date = _resolve_range(start, end)
    data = ReportService(db).get_lost_sales(start_date, end_date)
    return _csv_response(
        ["logged_at", "customer", "product_sku", "product_title", "reason",
         "competitor_name", "competitor_price", "quote_number"],
        [
            [
                r["logged_at"].date().isoformat() if r["logged_at"] else "",
                r["customer_name"], r["product_sku"], r["product_title"],
                r["reason"], r["competitor_name"],
                f"{r['competitor_price']:.2f}" if r["competitor_price"] is not None else "",
                r["quote_number"] or "",
            ]
            for r in data["rows"]
        ],
        f"lost_sales_{start_date.isoformat()}_{end_date.isoformat()}.csv",
    )


# ── Back-compat redirects (legacy URLs from sidebar/bookmarks) ───────────────
#
# Keep these until the sidebar links migrate to canonical paths. Then they can
# be removed. 308 keeps query strings intact (e.g. ?filter=low_stock); the body
# remains read-only so the SAFE-REDIRECT vs side-effect concern doesn't apply.

@router.get("/sales", include_in_schema=False)
@router.get("/sales/", include_in_schema=False)
def _legacy_sales(request: Request):
    qs = request.url.query
    target = "/reports/sales-by-customer" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=308)


@router.get("/inventory", include_in_schema=False)
@router.get("/inventory/", include_in_schema=False)
def _legacy_inventory(request: Request):
    qs = request.url.query
    target = "/reports/inventory-valuation" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=308)


@router.get("/open-pos/", include_in_schema=False)
def _legacy_open_pos_slash():
    return RedirectResponse("/reports/open-pos", status_code=308)
