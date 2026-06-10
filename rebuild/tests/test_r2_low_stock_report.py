"""
tests/test_r2_low_stock_report.py
=================================
R2 — Low Stock / Reorder report (ReportService.get_low_stock + routes).

Mirrors tests/test_reports.py: service math is asserted against a precisely
seeded, ISOLATED in-memory engine; routes are exercised through TestClient in
self-contained tests that seed, commit, and CLOSE the session before the
client's get_db session reads (SQLite StaticPool single-connection rule).

Threshold contract (matches the dashboard low-stock counter):
  is_active AND reorder_point > 0 AND qty_on_hand <= reorder_point.
Stockouts (qty 0) are INCLUDED — unlike the products-list "Low stock" tab,
which parks them under its separate "Out of stock" tab.
"""
from __future__ import annotations

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

def _product(db, *, sku, qty_on_hand=0, reorder_point=0, max_stock_level=None,
             qty_on_order=0, qty_committed=0, last_cost=0.0, is_active=True):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title=f"Part {sku}", description=f"Part {sku}",
                cost=0.0, last_cost=last_cost, markup_pct=50.0,
                qty_on_hand=qty_on_hand, qty_committed=qty_committed,
                qty_on_order=qty_on_order, reorder_point=reorder_point,
                max_stock_level=max_stock_level,
                status=ProductStatus.ACTIVE, is_active=is_active)
    db.add(p); db.flush()
    return p


def _vendor_source(db, product, *, vendor_name="LS Vendor", part_number="VP-1",
                   vendor_cost=10.0, is_preferred=True, is_active=True):
    from app.models.product import ProductVendorSource
    from app.models.vendor import Vendor
    v = db.query(Vendor).filter(Vendor.name == vendor_name).first()
    if v is None:
        v = Vendor(name=vendor_name)
        db.add(v); db.flush()
    src = ProductVendorSource(product_id=product.id, vendor_id=v.id,
                              vendor_part_number=part_number,
                              vendor_cost=vendor_cost,
                              is_preferred=is_preferred, is_active=is_active)
    db.add(src); db.flush()
    return src


# ===========================================================================
# Threshold logic
# ===========================================================================

def test_threshold_logic(db):
    _product(db, sku="LS-UNDER", qty_on_hand=2, reorder_point=5)   # below -> in
    _product(db, sku="LS-AT", qty_on_hand=5, reorder_point=5)      # at -> in
    _product(db, sku="LS-OVER", qty_on_hand=6, reorder_point=5)    # above -> out
    _product(db, sku="LS-NORP", qty_on_hand=0, reorder_point=0)    # rp unset -> out
    _product(db, sku="LS-INACTIVE", qty_on_hand=1, reorder_point=5,
             is_active=False)                                       # inactive -> out
    _product(db, sku="LS-STOCKOUT", qty_on_hand=0, reorder_point=3)  # stockout -> in
    db.commit()

    data = ReportService(db).get_low_stock()
    skus = {r["sku"] for r in data["rows"]}
    assert skus == {"LS-UNDER", "LS-AT", "LS-STOCKOUT"}
    assert data["totals"]["item_count"] == 3
    assert data["totals"]["stockout_count"] == 1


# ===========================================================================
# Vendor columns from the preferred ACTIVE source
# ===========================================================================

def test_vendor_columns_from_preferred_active_source(db):
    p = _product(db, sku="LS-VEND", qty_on_hand=1, reorder_point=4)
    _vendor_source(db, p, vendor_name="Backup V", part_number="ALT-9",
                   vendor_cost=99.0, is_preferred=False)
    _vendor_source(db, p, vendor_name="PAI", part_number="PAI-123",
                   vendor_cost=12.5, is_preferred=True)
    # Ghost preferred: a soft-deleted (inactive) preferred source must NOT surface.
    g = _product(db, sku="LS-GHOST", qty_on_hand=1, reorder_point=4, last_cost=7.0)
    _vendor_source(db, g, vendor_name="Gone V", part_number="GONE-1",
                   vendor_cost=5.0, is_preferred=True, is_active=False)
    db.commit()

    data = ReportService(db).get_low_stock()
    rows = {r["sku"]: r for r in data["rows"]}

    v = rows["LS-VEND"]
    assert v["vendor_name"] == "PAI"
    assert v["vendor_part_number"] == "PAI-123"
    assert v["vendor_cost"] == 12.5
    # no max -> restock to reorder point: suggested 4-1=3; est cost 3 * 12.50
    assert v["suggested_qty"] == 3
    assert v["est_order_cost"] == 37.5

    ghost = rows["LS-GHOST"]
    assert ghost["vendor_name"] is None
    assert ghost["vendor_part_number"] is None
    assert ghost["vendor_cost"] is None
    # est unit cost falls back to last_cost when no active preferred source
    assert ghost["est_unit_cost"] == 7.0

    assert data["totals"]["no_vendor_count"] == 1


# ===========================================================================
# Suggested order qty math
# ===========================================================================

def test_suggested_qty_math(db):
    # max set: 10 - 2 on hand - 1 on order = 7
    _product(db, sku="LS-MAX", qty_on_hand=2, qty_on_order=1,
             reorder_point=5, max_stock_level=10)
    # max unset: restock back to the reorder point -> 5 - 1 - 0 = 4
    _product(db, sku="LS-NOMAX", qty_on_hand=1, reorder_point=5)
    # floored at zero when inbound POs already cover the target
    _product(db, sku="LS-COVERED", qty_on_hand=2, qty_on_order=20,
             reorder_point=5, max_stock_level=10)
    db.commit()

    rows = {r["sku"]: r for r in ReportService(db).get_low_stock()["rows"]}
    assert rows["LS-MAX"]["suggested_qty"] == 7
    assert rows["LS-NOMAX"]["suggested_qty"] == 4
    assert rows["LS-COVERED"]["suggested_qty"] == 0


# ===========================================================================
# Sort — most-negative availability first
# ===========================================================================

def test_sorted_most_negative_availability_first(db):
    _product(db, sku="LS-B", qty_on_hand=2, reorder_point=5)                   # avail 2
    _product(db, sku="LS-A", qty_on_hand=1, qty_committed=5, reorder_point=5)  # avail -4
    _product(db, sku="LS-C", qty_on_hand=0, reorder_point=5)                   # avail 0
    db.commit()

    rows = ReportService(db).get_low_stock()["rows"]
    assert [r["sku"] for r in rows] == ["LS-A", "LS-C", "LS-B"]
    assert [r["qty_available"] for r in rows] == [-4, 0, 2]


# ===========================================================================
# CSV export route
# ===========================================================================

def test_csv_export_route():
    from fastapi.testclient import TestClient
    from app.main import app

    Session = activate(fresh_engine())
    s = Session()
    p = _product(s, sku="LS-CSV", qty_on_hand=1, reorder_point=5, max_stock_level=8)
    _vendor_source(s, p, vendor_name="CSV Vendor", part_number="CSV-77",
                   vendor_cost=4.25)
    s.commit()
    s.close()

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/reports/low-stock/export.csv")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("sku,title,category,qty_on_hand,qty_committed")
    assert "vendor_part_number" in lines[0]
    body = "\n".join(lines[1:])
    assert "LS-CSV" in body
    assert "CSV Vendor" in body
    assert "CSV-77" in body
    assert "4.25" in body  # vendor_cost column


# ===========================================================================
# Report page renders with content (no swallowed-error banner)
# ===========================================================================

def test_report_page_renders_with_content():
    from fastapi.testclient import TestClient
    from app.main import app

    Session = activate(fresh_engine())
    s = Session()
    p = _product(s, sku="LS-PAGE", qty_on_hand=0, reorder_point=3)
    _vendor_source(s, p, vendor_name="Page Vendor", part_number="PG-1",
                   vendor_cost=2.0)
    s.commit()
    s.close()

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/reports/low-stock")

    assert r.status_code == 200
    assert "Check server logs for details" not in r.text
    assert "LS-PAGE" in r.text
    assert "Page Vendor" in r.text


# ===========================================================================
# Reports index shows the Low Stock card + KPI snapshot
# ===========================================================================

def test_reports_index_shows_low_stock_card():
    from fastapi.testclient import TestClient
    from app.main import app

    Session = activate(fresh_engine())
    s = Session()
    _product(s, sku="LS-IDX", qty_on_hand=1, reorder_point=5)
    s.commit()
    s.close()

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/reports")

    assert r.status_code == 200
    assert "Check server logs for details" not in r.text
    assert "/reports/low-stock" in r.text
    assert "Low Stock / Reorder" in r.text
