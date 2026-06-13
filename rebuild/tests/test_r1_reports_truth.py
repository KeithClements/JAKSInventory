"""
tests/test_r1_reports_truth.py
==============================
R1-14 + R1-13 (reports/customers half) — margin truth, tax base, overdue
consistency, CSV exports.

Covers:
  1. Sales-by-customer / sales-by-product margin fallback: lines with a frozen
     unit_cost of 0 (sold before any receipt) substitute preferred-vendor
     vendor_cost, then product.last_cost, and are counted as cost_estimated;
     lines with no cost basis at all are counted as zero_cost.
  2. Sales-tax taxable_revenue uses ln.line_total (net of line discount), not
     qty × unit_price.
  3. Dashboard overdue count is date-based (an invoice due today is NOT
     overdue) and agrees with ReportService.get_overdue_invoices.
  4. CSV exports: /reports/ar-aging/export.csv, /reports/overdue-invoices/
     export.csv, /reports/sales-tax/export.csv, /customers/export.csv
     (honoring tab/q) all return text/csv with the expected header + data.

Each test gets a FRESH isolated in-memory engine (reports aggregate across the
whole DB). Invoice totals are @property — we seed InvoiceLine rows.
"""
from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine
from app.services.report_service import ReportService


@pytest.fixture()
def db():
    """Fresh isolated engine per test -> aggregate report numbers are exact."""
    Session = activate(fresh_engine())
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Seed helpers (pattern from tests/test_reports.py)
# ---------------------------------------------------------------------------

def _customer(db, *, company_name="R1 Cust", **kw):
    from app.models.customer import Customer
    c = Customer(company_name=company_name, contact_name="QA",
                 email="r1@test.local", **kw)
    db.add(c); db.flush()
    return c


def _product(db, *, sku="R1-1", cost=0.0, last_cost=0.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title="R1 Part", description="R1 Part", cost=cost,
                last_cost=last_cost, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.flush()
    return p


def _vendor_source(db, product, *, vendor_cost, is_preferred=True, is_active=True):
    from app.models.product import ProductVendorSource
    from app.models.vendor import Vendor
    ven = Vendor(name=f"V-{product.sku}"); db.add(ven); db.flush()
    src = ProductVendorSource(product_id=product.id, vendor_id=ven.id,
                              vendor_cost=vendor_cost, is_preferred=is_preferred,
                              is_active=is_active)
    db.add(src); db.flush()
    return src


def _invoice(db, customer, *, status, due_date=None, created_at=None, number="INV-R1"):
    from app.models.invoice import Invoice
    inv = Invoice(invoice_number=number, customer_id=customer.id, status=status,
                  due_date=due_date, created_at=created_at)
    db.add(inv); db.flush()
    return inv


def _line(db, invoice, product=None, *, qty=1, unit_price=0.0, unit_cost=0.0,
          is_taxable=False, tax_amount=0.0, discount_pct=0.0):
    from app.models.invoice import InvoiceLine
    ln = InvoiceLine(invoice_id=invoice.id,
                     product_id=(product.id if product else None),
                     qty=qty, unit_price=unit_price, unit_cost=unit_cost,
                     is_taxable=is_taxable, tax_amount=tax_amount,
                     discount_pct=discount_pct)
    db.add(ln); db.flush()
    return ln


_MAY = (datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
_MAY_DT = datetime.datetime(2026, 5, 15)


# ===========================================================================
# 1 — Margin fallback (cost_estimated) + zero-cost counting
# ===========================================================================

def test_sales_by_customer_fallback_to_vendor_cost(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="R1-VC")
    _vendor_source(db, prod, vendor_cost=40.0)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, created_at=_MAY_DT)
    _line(db, inv, prod, qty=2, unit_price=100.0, unit_cost=0.0)
    db.commit()

    data = ReportService(db).get_sales_by_customer(*_MAY)
    assert data["totals"]["gross_sales"] == 200.0
    assert data["totals"]["cost"] == 80.0           # 2 × vendor_cost 40
    assert data["totals"]["margin"] == 120.0
    assert data["totals"]["margin_pct"] == 60.0
    assert data["cost_estimated_lines"] == 1
    assert data["zero_cost_lines"] == 0


def test_sales_by_customer_fallback_to_last_cost(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="R1-LC", last_cost=35.0)  # no vendor source
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, created_at=_MAY_DT)
    _line(db, inv, prod, qty=1, unit_price=100.0, unit_cost=0.0)
    db.commit()

    data = ReportService(db).get_sales_by_customer(*_MAY)
    assert data["totals"]["cost"] == 35.0
    assert data["cost_estimated_lines"] == 1
    assert data["zero_cost_lines"] == 0


def test_sales_by_customer_zero_cost_line_counted_not_estimated(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="R1-ZERO")  # no cost basis anywhere
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, created_at=_MAY_DT)
    _line(db, inv, prod, qty=1, unit_price=100.0, unit_cost=0.0)
    db.commit()

    data = ReportService(db).get_sales_by_customer(*_MAY)
    assert data["totals"]["cost"] == 0.0
    assert data["cost_estimated_lines"] == 0
    assert data["zero_cost_lines"] == 1


def test_frozen_unit_cost_is_never_overridden(db):
    """A real frozen snapshot wins — the fallback only fills in 0 snapshots."""
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="R1-FROZEN", last_cost=99.0)
    _vendor_source(db, prod, vendor_cost=99.0)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, created_at=_MAY_DT)
    _line(db, inv, prod, qty=1, unit_price=100.0, unit_cost=30.0)
    db.commit()

    data = ReportService(db).get_sales_by_customer(*_MAY)
    assert data["totals"]["cost"] == 30.0
    assert data["cost_estimated_lines"] == 0
    assert data["zero_cost_lines"] == 0


def test_sales_by_product_fallback_prefers_vendor_cost_over_last_cost(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="R1-BOTH", last_cost=50.0)
    _vendor_source(db, prod, vendor_cost=40.0)  # preferred source wins
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, created_at=_MAY_DT)
    _line(db, inv, prod, qty=2, unit_price=100.0, unit_cost=0.0)
    db.commit()

    data = ReportService(db).get_sales_by_product(*_MAY)
    assert data["totals"]["revenue"] == 200.0
    assert data["totals"]["cost"] == 80.0           # vendor_cost 40, not last_cost 50
    assert data["totals"]["margin"] == 120.0
    assert data["cost_estimated_lines"] == 1
    assert data["zero_cost_lines"] == 0


def test_sales_by_product_zero_cost_counted(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    prod = _product(db, sku="R1-PZERO")
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, created_at=_MAY_DT)
    _line(db, inv, prod, qty=1, unit_price=75.0, unit_cost=0.0)
    db.commit()

    data = ReportService(db).get_sales_by_product(*_MAY)
    assert data["totals"]["cost"] == 0.0
    assert data["cost_estimated_lines"] == 0
    assert data["zero_cost_lines"] == 1


# ===========================================================================
# 2 — Sales-tax taxable_revenue honors line discounts
# ===========================================================================

def test_sales_tax_taxable_revenue_is_net_of_line_discount(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN, created_at=_MAY_DT)
    # 100 @ 10% line discount -> line_total 90; tax frozen at 7.43 (8.25% of 90)
    _line(db, inv, qty=1, unit_price=100.0, discount_pct=10.0,
          is_taxable=True, tax_amount=7.43)
    db.commit()

    data = ReportService(db).get_sales_tax_collected(*_MAY)
    assert data["totals"]["invoice_count"] == 1
    assert data["rows"][0]["taxable_revenue"] == 90.0   # not 100.0
    assert data["totals"]["taxable_revenue"] == 90.0
    assert data["totals"]["tax_collected"] == 7.43


# ===========================================================================
# 3 — Dashboard overdue count is date-based (agrees with the reports)
# ===========================================================================

def test_dashboard_overdue_count_matches_overdue_report():
    """An invoice due TODAY (midnight timestamp) is not overdue; one due
    yesterday is. The old utcnow() compare counted both -> dashboard said 2
    while the AR/overdue reports said 1."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.constants import InvoiceStatus

    Session = activate(fresh_engine())
    s = Session()
    cust = _customer(s)
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    inv1 = _invoice(s, cust, status=InvoiceStatus.OPEN, number="INV-OD-1",
                    due_date=datetime.datetime.combine(yesterday, datetime.time()))
    _line(s, inv1, qty=1, unit_price=50.0)
    inv2 = _invoice(s, cust, status=InvoiceStatus.OPEN, number="INV-OD-2",
                    due_date=datetime.datetime.combine(today, datetime.time()))
    _line(s, inv2, qty=1, unit_price=50.0)
    s.commit()

    report_count = ReportService(s).get_overdue_invoices()["totals"]["invoice_count"]
    assert report_count == 1
    s.close()

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/")
        assert r.status_code == 200
        # Axle redesign (2026-06-11) folded the overdue tile into the AR Balance
        # sub-line. The semantic guarantee — dashboard count == report count —
        # still holds; assert against the rendered sub-line copy instead of the
        # legacy <p class="stat-value text-red-600"> structure that no longer exists.
        assert f"{report_count} overdue invoice" in r.text


# ===========================================================================
# 4 — CSV exports
# ===========================================================================

def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def test_ar_aging_export_csv():
    from app.constants import InvoiceStatus
    Session = activate(fresh_engine())
    s = Session()
    cust = _customer(s, company_name="AR Export Co")
    inv = _invoice(s, cust, status=InvoiceStatus.OPEN,
                   due_date=datetime.datetime(2026, 4, 15))
    _line(s, inv, qty=1, unit_price=100.0)
    s.commit(); s.close()

    with _client() as c:
        r = c.get("/reports/ar-aging/export.csv?as_of=2026-05-30")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines[0] == "customer,invoice_count,current,1_30,31_60,61_90,over_90,total"
        assert lines[1] == "AR Export Co,1,0.00,0.00,100.00,0.00,0.00,100.00"


def test_overdue_invoices_export_csv():
    from app.constants import InvoiceStatus
    Session = activate(fresh_engine())
    s = Session()
    cust = _customer(s, company_name="OD Export Co")
    inv = _invoice(s, cust, status=InvoiceStatus.OPEN, number="INV-OD-CSV",
                   due_date=datetime.datetime(2026, 5, 20))
    _line(s, inv, qty=1, unit_price=80.0)
    s.commit(); s.close()

    with _client() as c:
        r = c.get("/reports/overdue-invoices/export.csv?as_of=2026-05-30")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines[0] == ("invoice_number,customer,due_date,days_overdue,"
                            "balance_due,interest_accrued,total_owed")
        assert lines[1] == "INV-OD-CSV,OD Export Co,2026-05-20,10,80.00,0.00,80.00"


def test_sales_tax_export_csv():
    from app.constants import InvoiceStatus
    Session = activate(fresh_engine())
    s = Session()
    cust = _customer(s, company_name="Tax Export Co")
    inv = _invoice(s, cust, status=InvoiceStatus.OPEN, number="INV-TAX-CSV",
                   created_at=_MAY_DT)
    _line(s, inv, qty=1, unit_price=100.0, discount_pct=10.0,
          is_taxable=True, tax_amount=7.43)
    s.commit(); s.close()

    with _client() as c:
        r = c.get("/reports/sales-tax/export.csv?start=2026-05-01&end=2026-05-31")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines[0] == ("invoice_number,customer,invoice_date,"
                            "taxable_revenue,tax_collected,invoice_total")
        # taxable_revenue is the discounted line_total (90), not qty×price (100)
        assert lines[1] == "INV-TAX-CSV,Tax Export Co,2026-05-15,90.00,7.43,97.43"


def test_customers_export_csv_honors_tab_and_q():
    Session = activate(fresh_engine())
    s = Session()
    _customer(s, company_name="Alpha Diesel")
    _customer(s, company_name="Bravo Trucking")
    inactive = _customer(s, company_name="Gone Fishing")
    inactive.is_active = False
    s.commit(); s.close()

    with _client() as c:
        # Unfiltered: both active customers, inactive excluded
        r = c.get("/customers/export.csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines[0].startswith("company_name,contact_name,account_number,phone,email")
        body = "\n".join(lines[1:])
        assert "Alpha Diesel" in body
        assert "Bravo Trucking" in body
        assert "Gone Fishing" not in body

        # q filter
        r = c.get("/customers/export.csv?q=alpha")
        body = r.text
        assert "Alpha Diesel" in body
        assert "Bravo Trucking" not in body

        # inactive tab surfaces only deactivated customers
        r = c.get("/customers/export.csv?tab=inactive")
        body = r.text
        assert "Gone Fishing" in body
        assert "Alpha Diesel" not in body
