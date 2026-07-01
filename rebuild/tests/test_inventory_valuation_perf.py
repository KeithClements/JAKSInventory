"""
tests/test_inventory_valuation_perf.py
======================================
Lane-F perf rebuild of the Inventory Valuation report.

The old report materialised every active product (~31k rows) in Python and dumped
them all into one DOM (~27s load). The rebuild:
  * computes the headline totals + a by-category breakdown in SQL (GROUP BY) via
    ReportService.get_inventory_valuation_summary(), and
  * paginates / category-scopes the per-part detail rows via
    ReportService.get_inventory_valuation(page=, page_size=, category_id=).

These tests pin the valuation RULE (unchanged): active products only, value =
qty_on_hand * cost (avg cost) summed; total_units / in_stock count only qty>0.
They assert (a) the summary total, (b) per-category breakdown values, and
(c) that pagination returns a bounded window, not the whole population.

Each test gets a FRESH isolated engine so aggregate numbers are exact.
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
    Session = activate(fresh_engine())
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _category(db, name, code=""):
    from app.models.product import ProductCategory
    c = ProductCategory(name=name, code=code, level=1)
    db.add(c); db.flush()
    return c


def _product(db, *, sku, qty_on_hand=0, cost=0.0, last_cost=0.0,
             category_id=None, is_active=True):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title=f"Part {sku}", description=f"Part {sku}",
                cost=cost, last_cost=last_cost, markup_pct=50.0,
                qty_on_hand=qty_on_hand, category_id=category_id,
                status=ProductStatus.ACTIVE, is_active=is_active)
    db.add(p); db.flush()
    return p


# ---------------------------------------------------------------------------
# (a) Summary total ties out to sum(qty * cost) over active products
# ---------------------------------------------------------------------------

def test_summary_total_value_matches_sum(db):
    cat_a = _category(db, "Fuel Pumps", "FP")
    cat_b = _category(db, "Gaskets", "GSK")

    # cat_a: 10*5 + 4*25 = 50 + 100 = 150
    _product(db, sku="FP-1", qty_on_hand=10, cost=5.0, category_id=cat_a.id)
    _product(db, sku="FP-2", qty_on_hand=4, cost=25.0, category_id=cat_a.id)
    # cat_b: 3*8 = 24
    _product(db, sku="GSK-1", qty_on_hand=3, cost=8.0, category_id=cat_b.id)
    # uncategorized: 2*100 = 200
    _product(db, sku="UNC-1", qty_on_hand=2, cost=100.0, category_id=None)
    # inactive product MUST be excluded from valuation
    _product(db, sku="OFF-1", qty_on_hand=999, cost=999.0, is_active=False)
    db.commit()

    summary = ReportService(db).get_inventory_valuation_summary()
    t = summary["totals"]

    # 150 + 24 + 200 = 374
    assert t["total_value"] == 374.0
    assert t["sku_count"] == 4          # active only, inactive excluded
    assert t["in_stock_skus"] == 4
    assert t["total_units"] == 10 + 4 + 3 + 2  # 19

    # Headline ties to the legacy definition exactly (per-row sum of qty*cost).
    legacy = round(10 * 5.0 + 4 * 25.0 + 3 * 8.0 + 2 * 100.0, 2)
    assert t["total_value"] == legacy


def test_summary_total_matches_paginated_full_scan(db):
    """The SQL summary total must equal the legacy full-population row sum."""
    cat = _category(db, "Injectors", "INJ")
    for i in range(25):
        _product(db, sku=f"INJ-{i:03d}", qty_on_hand=i, cost=float(i % 7),
                 category_id=cat.id)
    db.commit()

    svc = ReportService(db)
    summary_total = svc.get_inventory_valuation_summary()["totals"]["total_value"]

    # Full unpaginated scan (legacy behaviour) -> sum of per-row values.
    full = svc.get_inventory_valuation()  # no page/page_size => all rows
    row_sum = round(sum(r["total_value"] for r in full["rows"]), 2)

    assert summary_total == row_sum
    # And both equal the hand-computed truth.
    expected = round(sum(i * float(i % 7) for i in range(25)), 2)
    assert summary_total == expected


# ---------------------------------------------------------------------------
# (b) By-category breakdown values are correct
# ---------------------------------------------------------------------------

def test_by_category_breakdown_values(db):
    cat_a = _category(db, "Fuel Pumps", "FP")
    cat_b = _category(db, "Gaskets", "GSK")

    _product(db, sku="FP-1", qty_on_hand=10, cost=5.0, category_id=cat_a.id)   # 50
    _product(db, sku="FP-2", qty_on_hand=4, cost=25.0, category_id=cat_a.id)   # 100
    _product(db, sku="FP-Z", qty_on_hand=2, cost=0.0, category_id=cat_a.id)    # zero-cost
    _product(db, sku="GSK-1", qty_on_hand=3, cost=8.0, category_id=cat_b.id)   # 24
    _product(db, sku="UNC-1", qty_on_hand=2, cost=100.0, category_id=None)     # 200
    db.commit()

    by_cat = ReportService(db).get_inventory_valuation_summary()["by_category"]
    by_name = {c["category"]: c for c in by_cat}

    assert by_name["Fuel Pumps"]["total_value"] == 150.0
    assert by_name["Fuel Pumps"]["sku_count"] == 3
    assert by_name["Fuel Pumps"]["in_stock_skus"] == 3
    assert by_name["Fuel Pumps"]["total_units"] == 16
    assert by_name["Fuel Pumps"]["zero_cost_count"] == 1

    assert by_name["Gaskets"]["total_value"] == 24.0
    assert by_name["Gaskets"]["sku_count"] == 1
    assert by_name["Gaskets"]["zero_cost_count"] == 0

    assert by_name["Uncategorized"]["total_value"] == 200.0
    assert by_name["Uncategorized"]["category_id"] is None

    # by_category is sorted highest-value first.
    assert [c["total_value"] for c in by_cat] == sorted(
        (c["total_value"] for c in by_cat), reverse=True
    )

    # by-category values sum to the grand total.
    assert round(sum(c["total_value"] for c in by_cat), 2) == 374.0


# ---------------------------------------------------------------------------
# (c) Pagination returns a BOUNDED window, not the whole population
# ---------------------------------------------------------------------------

def test_pagination_bounds_detail_rows(db):
    cat = _category(db, "Bulk", "BLK")
    for i in range(50):
        _product(db, sku=f"BLK-{i:03d}", qty_on_hand=1, cost=1.0, category_id=cat.id)
    db.commit()

    svc = ReportService(db)
    page1 = svc.get_inventory_valuation(page=1, page_size=10)

    assert len(page1["rows"]) == 10            # bounded, NOT 50
    assert page1["total_rows"] == 50
    assert page1["total_pages"] == 5
    assert page1["page"] == 1
    # Totals are the full population regardless of paging.
    assert page1["totals"]["sku_count"] == 50
    assert page1["totals"]["total_value"] == 50.0

    page2 = svc.get_inventory_valuation(page=2, page_size=10)
    assert len(page2["rows"]) == 10
    page1_skus = {r["sku"] for r in page1["rows"]}
    page2_skus = {r["sku"] for r in page2["rows"]}
    assert page1_skus.isdisjoint(page2_skus)   # different windows

    # Out-of-range page clamps to the last page (not an error / empty crash).
    last = svc.get_inventory_valuation(page=999, page_size=10)
    assert last["page"] == 5
    assert len(last["rows"]) == 10


def test_pagination_clamps_partial_last_page(db):
    cat = _category(db, "Odd", "ODD")
    for i in range(7):
        _product(db, sku=f"ODD-{i}", qty_on_hand=1, cost=2.0, category_id=cat.id)
    db.commit()

    svc = ReportService(db)
    p = svc.get_inventory_valuation(page=2, page_size=5)
    assert p["total_pages"] == 2
    assert len(p["rows"]) == 2                 # remainder only


def test_category_drilldown_filters_detail_rows(db):
    cat_a = _category(db, "A", "A")
    cat_b = _category(db, "B", "B")
    for i in range(6):
        _product(db, sku=f"A-{i}", qty_on_hand=1, cost=1.0, category_id=cat_a.id)
    for i in range(4):
        _product(db, sku=f"B-{i}", qty_on_hand=1, cost=1.0, category_id=cat_b.id)
    _product(db, sku="U-1", qty_on_hand=1, cost=1.0, category_id=None)
    db.commit()

    svc = ReportService(db)

    only_a = svc.get_inventory_valuation(page_size=100, category_id=cat_a.id)
    assert only_a["total_rows"] == 6
    assert all(r["sku"].startswith("A-") for r in only_a["rows"])
    # Totals still reflect the WHOLE population, not the filtered slice.
    assert only_a["totals"]["sku_count"] == 11

    only_unc = svc.get_inventory_valuation(page_size=100, uncategorized=True)
    assert only_unc["total_rows"] == 1
    assert only_unc["rows"][0]["sku"] == "U-1"


def test_legacy_unpaginated_call_returns_all_rows(db):
    """Back-compat: calling with no paging args returns every active row (CSV export)."""
    cat = _category(db, "Legacy", "LEG")
    for i in range(30):
        _product(db, sku=f"LEG-{i:02d}", qty_on_hand=2, cost=3.0, category_id=cat.id)
    db.commit()

    data = ReportService(db).get_inventory_valuation()
    assert len(data["rows"]) == 30             # NOT paginated
    assert data["page_size"] is None
    assert data["totals"]["total_value"] == 30 * 2 * 3.0


def test_warning_flags_preserved(db):
    """zero_cost and negative_qty flags still set on detail rows + summary count."""
    cat = _category(db, "Flags", "FLG")
    _product(db, sku="OK", qty_on_hand=5, cost=2.0, category_id=cat.id)
    _product(db, sku="ZC", qty_on_hand=4, cost=0.0, category_id=cat.id)   # zero-cost
    _product(db, sku="NEG", qty_on_hand=-3, cost=10.0, category_id=cat.id)  # negative
    db.commit()

    svc = ReportService(db)
    summary = svc.get_inventory_valuation_summary()["totals"]
    assert summary["zero_cost_count"] == 1
    # negative-qty row nets value down: 5*2 + 4*0 + (-3*10) = 10 - 30 = -20
    assert summary["total_value"] == -20.0
    assert summary["total_units"] == 9   # only qty>0 rows (5 + 4)

    rows = {r["sku"]: r for r in svc.get_inventory_valuation(page_size=100)["rows"]}
    assert rows["ZC"]["warning"] == "zero_cost"
    assert rows["NEG"]["warning"] == "negative_qty"
    assert rows["OK"]["warning"] is None
