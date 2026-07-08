"""
tests/test_reports.py
=====================
Functional integration tests for all 9 reports (TESTING_FEEDBACK §6):
AR Aging, Sales by Customer, Sales by Product, Inventory Valuation, Open POs,
Outstanding Cores, Overdue Invoices, Sales Tax, Lost Sales.

§6 has two columns: "Loads?" and "Numbers correct?".
  * Loads?          -> the no-param GET route returning 200 is already covered by
                       tests/test_regression_b1_b2.py's dynamic route sweep. Here we
                       additionally assert the route renders WITHOUT the swallowed-
                       error banner ("Could not load ... Check server logs").
  * Numbers correct -> each report is driven through ReportService directly against
                       a precisely-seeded, ISOLATED in-memory DB and the totals are
                       asserted exactly.

Reports aggregate across the whole DB, so each test gets a FRESH engine (no shared
state, no startup seed needed — ReportService only reads). Invoice totals are
@property (computed from lines + allocations), so we seed InvoiceLine rows, never a
"total" column.
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
# Seed helpers
# ---------------------------------------------------------------------------

def _customer(db, **kw):
    from app.models.customer import Customer
    c = Customer(company_name="Rpt Cust", contact_name="QA",
                 email="rpt@test.local", **kw)
    db.add(c); db.flush()
    return c


def _product(db, *, sku="RPT-1", qty_on_hand=0, cost=5.0, is_active=True):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title="Rpt Part", description="Rpt Part", cost=cost,
                markup_pct=50.0, qty_on_hand=qty_on_hand,
                status=ProductStatus.ACTIVE, is_active=is_active)
    db.add(p); db.flush()
    return p


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


# ===========================================================================
# §6.a — AR Aging
# ===========================================================================

def test_ar_aging_buckets_and_total(db):
    as_of = datetime.date(2026, 5, 30)
    cust = _customer(db)
    from app.constants import InvoiceStatus
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN,
                   due_date=datetime.datetime(2026, 4, 15))  # 45 days late
    _line(db, inv, qty=1, unit_price=100.0)
    db.commit()

    data = ReportService(db).get_ar_aging(as_of_date=as_of)
    assert data["totals"]["total"] == 100.0
    assert data["totals"]["31_60"] == 100.0  # 45 days -> 31_60 bucket
    assert data["totals"]["current"] == 0.0


# ===========================================================================
# §6.b — Sales by Customer
# ===========================================================================

def test_sales_by_customer_numbers(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN,
                   created_at=datetime.datetime(2026, 5, 15))
    _line(db, inv, qty=2, unit_price=50.0, unit_cost=30.0)
    db.commit()

    data = ReportService(db).get_sales_by_customer(
        datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
    assert data["totals"]["gross_sales"] == 100.0
    assert data["totals"]["cost"] == 60.0
    assert data["totals"]["margin"] == 40.0


# ===========================================================================
# §6.c — Sales by Product
# ===========================================================================

def test_sales_by_product_numbers(db):
    from app.constants import InvoiceStatus
    cust = _customer(db); prod = _product(db, sku="RPT-PROD")
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN,
                   created_at=datetime.datetime(2026, 5, 15))
    _line(db, inv, prod, qty=2, unit_price=50.0, unit_cost=30.0)
    db.commit()

    data = ReportService(db).get_sales_by_product(
        datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
    assert data["totals"]["qty_sold"] == 2
    assert data["totals"]["revenue"] == 100.0
    assert data["totals"]["cost"] == 60.0
    assert data["totals"]["margin"] == 40.0


# ===========================================================================
# §6.d — Inventory Valuation
# ===========================================================================

def test_inventory_valuation_numbers(db):
    _product(db, sku="RPT-INV", qty_on_hand=10, cost=5.0, is_active=True)
    db.commit()

    data = ReportService(db).get_inventory_valuation()
    assert data["totals"]["total_value"] == 50.0   # 10 * 5
    assert data["totals"]["total_units"] == 10
    assert data["totals"]["in_stock_skus"] == 1


def test_inventory_valuation_flags_zero_cost(db):
    _product(db, sku="RPT-ZC", qty_on_hand=4, cost=0.0, is_active=True)
    db.commit()
    data = ReportService(db).get_inventory_valuation()
    assert data["totals"]["zero_cost_count"] == 1


# ===========================================================================
# §6.e — Open POs
# ===========================================================================

def test_open_pos_numbers(db):
    from app.constants import POStatus
    from app.models.vendor import Vendor
    from app.models.purchase_order import PurchaseOrder, POLine
    ven = Vendor(name="Rpt Vendor"); db.add(ven); db.flush()
    po = PurchaseOrder(po_number="PO-RPT-1", vendor_id=ven.id, status=POStatus.SENT)
    db.add(po); db.flush()
    db.add(POLine(po_id=po.id, description="x", qty_ordered=10, qty_received=3,
                  qty_cancelled=2, unit_cost=4.0))
    db.commit()

    data = ReportService(db).get_open_pos()
    assert data["totals"]["po_count"] == 1
    assert data["totals"]["qty_remaining"] == 5          # 10 - 3 - 2
    assert data["totals"]["outstanding_value"] == 20.0   # 5 * 4


# ===========================================================================
# §6.f — Outstanding Cores
# ===========================================================================

def test_outstanding_cores_numbers(db):
    from app.constants import CoreDirection, CoreStatus
    from app.models.core import CoreCharge
    from app.models.vendor import Vendor
    cust = _customer(db); prod = _product(db, sku="RPT-CORE")
    ven = Vendor(name="Core V"); db.add(ven); db.flush()
    db.add(CoreCharge(product_id=prod.id, customer_id=cust.id, vendor_id=ven.id,
                      direction=CoreDirection.CUSTOMER_OWES_RETURN,
                      status=CoreStatus.OPEN, qty_charged=2, qty_returned=0,
                      customer_unit_charge=25.0, vendor_unit_charge=20.0))
    db.commit()

    data = ReportService(db).get_core_charges_outstanding()
    assert data["totals"]["core_count"] == 1
    assert data["totals"]["qty_outstanding"] == 2
    assert data["totals"]["amount"] == 50.0   # 25 * 2


# ===========================================================================
# §6.g — Overdue Invoices (with interest)
# ===========================================================================

def test_overdue_invoices_numbers_with_interest(db):
    from app.constants import InvoiceStatus
    as_of = datetime.date(2026, 5, 30)
    cust = _customer(db, interest_rate=12.0, interest_grace_days=10)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN,
                   due_date=datetime.datetime(2026, 4, 20))  # 40 days overdue
    _line(db, inv, qty=1, unit_price=100.0)
    db.commit()

    data = ReportService(db).get_overdue_invoices(as_of_date=as_of)
    assert data["totals"]["invoice_count"] == 1
    assert data["totals"]["balance_due"] == 100.0
    # interest = 100 * (12/100/30) * (40 - 10) = 12.00
    assert data["totals"]["interest_accrued"] == 12.0
    assert data["totals"]["total_owed"] == 112.0


# ===========================================================================
# §6.h — Sales Tax
# ===========================================================================

def test_sales_tax_numbers(db):
    from app.constants import InvoiceStatus
    cust = _customer(db)
    inv = _invoice(db, cust, status=InvoiceStatus.OPEN,
                   created_at=datetime.datetime(2026, 5, 15))
    _line(db, inv, qty=1, unit_price=100.0, is_taxable=True, tax_amount=8.0,
          discount_pct=0.0)
    db.commit()

    data = ReportService(db).get_sales_tax_collected(
        datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
    assert data["totals"]["tax_collected"] == 8.0
    assert data["totals"]["taxable_revenue"] == 100.0
    assert data["totals"]["effective_rate_pct"] == 8.0


# ===========================================================================
# §6.i — Lost Sales
# ===========================================================================

def test_lost_sales_numbers(db):
    from app.models.quote import LostSaleLog
    db.add(LostSaleLog(reason="price", competitor_name="Acme",
                       competitor_price=90.0,
                       logged_at=datetime.datetime(2026, 5, 15)))
    db.commit()

    data = ReportService(db).get_lost_sales(
        datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
    assert data["totals"]["count"] == 1
    assert data["totals"]["with_competitor"] == 1
    assert data["totals"]["top_reasons"].get("price") == 1


# ===========================================================================
# §6 (all) — routes render WITHOUT the swallowed-error banner
# ===========================================================================

_REPORT_ROUTES = [
    "/reports/ar-aging", "/reports/sales-by-customer", "/reports/sales-by-product",
    "/reports/inventory-valuation", "/reports/open-pos", "/reports/outstanding-cores",
    "/reports/overdue-invoices", "/reports/sales-tax", "/reports/lost-sales",
]


def test_report_routes_render_without_error_banner():
    """Each report route returns 200 AND does not show the 'Could not load ...'
    banner the handlers fall back to when the service raises. (The router swallows
    exceptions into a 200 + banner, so status code alone is not enough.)

    Self-contained (does NOT use the `db` fixture): seeds + commits + CLOSES its
    session before entering the TestClient. With SQLite StaticPool's single shared
    connection, holding the seeding session open across the route's get_db session
    interferes with reads — closing first mirrors real request isolation.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.constants import InvoiceStatus

    Session = activate(fresh_engine())
    s = Session()
    cust = _customer(s)
    inv = _invoice(s, cust, status=InvoiceStatus.OPEN,
                   due_date=datetime.datetime(2026, 4, 1),
                   created_at=datetime.datetime(2026, 5, 15))
    _line(s, inv, _product(s, sku="RPT-RENDER", qty_on_hand=3, cost=2.0),
          qty=1, unit_price=100.0, is_taxable=True, tax_amount=8.0)
    s.commit()
    s.close()

    with TestClient(app, raise_server_exceptions=False) as c:
        for route in _REPORT_ROUTES:
            r = c.get(route)
            assert r.status_code == 200, f"{route} -> {r.status_code}"
            # The report error banners all end with this exact phrase; matching it
            # (not a generic "Could not load", which also appears in an unrelated
            # HTMX error JS string on every page) detects a truly-raised report.
            assert "Check server logs for details" not in r.text, (
                f"{route} rendered the swallowed-error banner (service raised):\n"
                f"{r.text[:300]}")
