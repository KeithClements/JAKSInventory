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

Back-compat redirects from the previous URL shape are at the bottom of the file
so existing sidebar/bookmark links keep working.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
    except Exception:
        log.exception("reports_index: ReportService failed")
        error_message = "Could not load report snapshot. Check server logs for details."

    return templates.TemplateResponse(
        "reports/index.html",
        {
            "request": request,
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
        "reports/ar_aging.html",
        {
            "request": request,
            "today": as_of_date,
            "as_of": as_of_date,
            "aging_rows": rows,   # legacy template variable
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
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
    try:
        data = ReportService(db).get_sales_by_customer(start_date, end_date)
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_sales_by_customer failed (%s–%s)", start_date, end_date)
        error_message = "Could not load sales data. Check server logs for details."

    return templates.TemplateResponse(
        "reports/sales_by_customer.html",
        {
            "request": request,
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
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
    try:
        data = ReportService(db).get_sales_by_product(start_date, end_date)
        rows = data["rows"]
        totals = data["totals"]
    except Exception:
        log.exception("reports_sales_by_product failed (%s–%s)", start_date, end_date)
        error_message = "Could not load product sales data. Check server logs for details."

    return templates.TemplateResponse(
        "reports/sales_by_product.html",
        {
            "request": request,
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
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
        "reports/inventory_valuation.html",
        {
            "request": request,
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
        "reports/open_pos.html",
        {
            "request": request,
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
        "reports/outstanding_cores.html",
        {
            "request": request,
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
        "reports/overdue_invoices.html",
        {
            "request": request,
            "as_of": as_of_date,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
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
        "reports/sales_tax.html",
        {
            "request": request,
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
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
        "reports/lost_sales.html",
        {
            "request": request,
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "totals": totals,
            "error_message": error_message,
        },
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
