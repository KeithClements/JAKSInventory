"""
tests/test_phase2_dead_stock.py
=================================
MASTER_PLAN §23.3 Phase 2 — dead-stock report.

Companion to get_low_stock (too little stock); this flags too MUCH of the
wrong stock — active products with qty_on_hand > 0 that have not sold via a
finalized, non-core invoice line within the window (default 90 days). A
product that's never sold at all is treated as maximally dead (sorted first,
days_since_sale is None so the UI reads "Never Sold" not a huge day count).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.constants import InvoiceStatus, LineType
from app.main import app
from app.services.report_service import ReportService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    Session = activate(fresh_engine())
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client():
    # Context-manager form triggers the app's startup event (seeds the
    # DEFAULT_USER_ID=1 admin the test-env auth bypass resolves to) — a bare
    # TestClient(app) skips it and every admin-gated route redirects to /login.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _product(db, *, sku, qty_on_hand=1, cost=10.0, is_active=True):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title=f"Part {sku}", description=f"Part {sku}",
                cost=cost, markup_pct=50.0, qty_on_hand=qty_on_hand,
                status=ProductStatus.ACTIVE, is_active=is_active)
    db.add(p); db.flush()
    return p


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name="Dead Stock Co")
    db.add(c); db.flush()
    return c


def _sale(db, customer_id, product_id, *, days_ago, status=InvoiceStatus.PAID,
          is_core_line=False):
    from app.models.invoice import Invoice, InvoiceLine
    # Anchored at midnight (not "now") so days_since_sale math is exact —
    # the service compares against midnight-today, and this fixture runs at
    # an arbitrary time of day; anchoring both sides avoids an off-by-one.
    # Must be the LOCAL date (date.today()), matching get_dead_stock's as_of —
    # utcnow().date() is a day ahead after ~6pm Mountain time (off-by-one flake).
    today_midnight = datetime.combine(date.today(), datetime.min.time())
    created = today_midnight - timedelta(days=days_ago)
    inv = Invoice(invoice_number=f"INV-DS-{product_id}-{days_ago}",
                  customer_id=customer_id, status=status, is_taxable=False,
                  created_at=created)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, product_id=product_id,
                                 qty=1, unit_price=50.0, unit_cost=10.0,
                                 is_taxable=False, is_core_line=is_core_line))
    db.add(inv); db.flush()
    return inv


# ── Filter correctness ─────────────────────────────────────────────────────

def test_never_sold_product_with_stock_is_dead(db):
    _product(db, sku="NEVER-1", qty_on_hand=5)
    db.commit()

    data = ReportService(db).get_dead_stock(days=90)
    skus = {r["sku"] for r in data["rows"]}
    assert "NEVER-1" in skus
    row = next(r for r in data["rows"] if r["sku"] == "NEVER-1")
    assert row["last_sold_at"] is None
    assert row["days_since_sale"] is None
    assert data["totals"]["never_sold_count"] == 1


def test_recently_sold_product_is_not_dead(db):
    c = _customer(db)
    p = _product(db, sku="FRESH-1", qty_on_hand=3)
    _sale(db, c.id, p.id, days_ago=5)
    db.commit()

    data = ReportService(db).get_dead_stock(days=90)
    assert "FRESH-1" not in {r["sku"] for r in data["rows"]}


def test_sold_outside_window_is_dead(db):
    c = _customer(db)
    p = _product(db, sku="STALE-1", qty_on_hand=2)
    _sale(db, c.id, p.id, days_ago=120)   # older than the 90-day default window
    db.commit()

    data = ReportService(db).get_dead_stock(days=90)
    row = next(r for r in data["rows"] if r["sku"] == "STALE-1")
    assert row["last_sold_at"] is not None
    assert row["days_since_sale"] == 120


def test_zero_qty_on_hand_never_appears_even_if_never_sold(db):
    """Nothing to report on stock that's already gone — matches get_low_stock's
    own out-of-scope filter symmetry (this report only cares about qty > 0)."""
    _product(db, sku="EMPTY-1", qty_on_hand=0)
    db.commit()
    data = ReportService(db).get_dead_stock(days=90)
    assert "EMPTY-1" not in {r["sku"] for r in data["rows"]}


def test_inactive_product_excluded(db):
    _product(db, sku="OFF-1", qty_on_hand=5, is_active=False)
    db.commit()
    data = ReportService(db).get_dead_stock(days=90)
    assert "OFF-1" not in {r["sku"] for r in data["rows"]}


def test_draft_invoice_does_not_count_as_a_sale(db):
    """Only FINALIZED invoices count — a draft is not a real sale."""
    c = _customer(db)
    p = _product(db, sku="DRAFT-1", qty_on_hand=2)
    _sale(db, c.id, p.id, days_ago=1, status=InvoiceStatus.DRAFT)
    db.commit()

    data = ReportService(db).get_dead_stock(days=90)
    row = next(r for r in data["rows"] if r["sku"] == "DRAFT-1")
    assert row["last_sold_at"] is None   # the draft invoice doesn't count


def test_core_only_line_does_not_count_as_a_product_sale(db):
    """A core-charge line isn't a sale of the PART — same exclusion rule as
    get_sales_by_product's revenue calculation."""
    c = _customer(db)
    p = _product(db, sku="CORE-1", qty_on_hand=2)
    _sale(db, c.id, p.id, days_ago=1, is_core_line=True)
    db.commit()

    data = ReportService(db).get_dead_stock(days=90)
    row = next(r for r in data["rows"] if r["sku"] == "CORE-1")
    assert row["last_sold_at"] is None   # core line doesn't count as a real sale


# ── Totals + sort order ─────────────────────────────────────────────────────

def test_totals_and_tied_up_value(db):
    c = _customer(db)
    _product(db, sku="D-1", qty_on_hand=4, cost=10.0)              # never sold, 40 tied up
    p2 = _product(db, sku="D-2", qty_on_hand=2, cost=25.0)          # stale, 50 tied up
    _sale(db, c.id, p2.id, days_ago=200)
    db.commit()

    totals = ReportService(db).get_dead_stock(days=90)["totals"]
    assert totals["item_count"] == 2
    assert totals["total_units"] == 6
    assert totals["total_tied_up_value"] == 90.0
    assert totals["never_sold_count"] == 1


def test_never_sold_sorts_before_oldest_sale(db):
    c = _customer(db)
    p_recent_stale = _product(db, sku="OLD-90", qty_on_hand=1)
    _sale(db, c.id, p_recent_stale.id, days_ago=91)
    p_very_stale = _product(db, sku="OLD-500", qty_on_hand=1)
    _sale(db, c.id, p_very_stale.id, days_ago=500)
    _product(db, sku="NEVER-2", qty_on_hand=1)   # never sold
    db.commit()

    rows = ReportService(db).get_dead_stock(days=90)["rows"]
    skus_in_order = [r["sku"] for r in rows]
    # never-sold first, then oldest (largest days_since_sale) before newer stale ones
    assert skus_in_order.index("NEVER-2") < skus_in_order.index("OLD-500")
    assert skus_in_order.index("OLD-500") < skus_in_order.index("OLD-90")


def test_days_window_is_configurable(db):
    c = _customer(db)
    p = _product(db, sku="WIN-1", qty_on_hand=1)
    _sale(db, c.id, p.id, days_ago=45)
    db.commit()

    svc = ReportService(db)
    assert "WIN-1" not in {r["sku"] for r in svc.get_dead_stock(days=90)["rows"]}
    assert "WIN-1" in {r["sku"] for r in svc.get_dead_stock(days=30)["rows"]}


# ── Route ───────────────────────────────────────────────────────────────────

def test_dead_stock_route_renders(db, client):
    _product(db, sku="ROUTE-1", qty_on_hand=3)
    db.commit()
    r = client.get("/reports/dead-stock")
    assert r.status_code == 200
    assert "ROUTE-1" in r.text
    assert "Never Sold" in r.text


def test_dead_stock_export_csv(db, client):
    _product(db, sku="CSV-1", qty_on_hand=3, cost=15.0)
    db.commit()
    r = client.get("/reports/dead-stock/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "CSV-1" in r.text
    assert "never" in r.text   # last_sold_at column for a never-sold row


def test_dead_stock_route_accepts_days_param(db, client):
    c = _customer(db)
    p = _product(db, sku="PARAM-1", qty_on_hand=1)
    _sale(db, c.id, p.id, days_ago=45)
    db.commit()

    r90 = client.get("/reports/dead-stock?days=90")
    assert "PARAM-1" not in r90.text
    r30 = client.get("/reports/dead-stock?days=30")
    assert "PARAM-1" in r30.text
