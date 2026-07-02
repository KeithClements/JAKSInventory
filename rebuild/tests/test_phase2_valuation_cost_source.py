"""
tests/test_phase2_valuation_cost_source.py
============================================
MASTER_PLAN §23.3 Phase 2 — inventory-valuation "cost-source callout".

The valuation MATH is unchanged (still raw Product.cost, never effective_cost
— see the locked rule comment in ReportService.get_inventory_valuation_summary).
This only adds VISIBILITY:
  * totals["cost_source_breakdown"] — active-product counts by Product.cost_source
    ("receipt" = real moving-avg from a PO receipt, "manual" = user-set OR the
    column default/never-touched, "vendor" = legacy).
  * totals["zero_cost_recoverable_count"] — the subset of zero_cost_count that
    DOES have an active vendor source with a real cost, i.e. Product.
    effective_cost would price these non-zero even though this report's own
    $0 total does not change.
  * Per-row (get_inventory_valuation): cost_source + recoverable_cost (the
    vendor-fallback cost, or None if no vendor cost exists either).
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


def _product(db, *, sku, qty_on_hand=0, cost=0.0, cost_source="manual", is_active=True):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title=f"Part {sku}", description=f"Part {sku}",
                cost=cost, cost_source=cost_source, markup_pct=50.0,
                qty_on_hand=qty_on_hand, status=ProductStatus.ACTIVE, is_active=is_active)
    db.add(p); db.flush()
    return p


def _vendor(db, name="PAI Industries", code="PAI"):
    from app.models.vendor import Vendor
    v = Vendor(name=name, vendor_code=code, vendor_number="9", is_active=True)
    db.add(v); db.flush()
    return v


def _source(db, product_id, vendor_id, *, vendor_cost=0.0, is_active=True):
    from app.models.product import ProductVendorSource
    s = ProductVendorSource(product_id=product_id, vendor_id=vendor_id,
                            vendor_cost=vendor_cost, is_active=is_active,
                            vendor_part_number="X1")
    db.add(s); db.flush()
    return s


# ── cost_source_breakdown ──────────────────────────────────────────────────

def test_cost_source_breakdown_counts_by_value(db):
    _product(db, sku="R-1", cost_source="receipt")
    _product(db, sku="R-2", cost_source="receipt")
    _product(db, sku="M-1", cost_source="manual")
    _product(db, sku="V-1", cost_source="vendor")
    _product(db, sku="OFF-1", cost_source="receipt", is_active=False)   # excluded
    db.commit()

    totals = ReportService(db).get_inventory_valuation_summary()["totals"]
    bd = totals["cost_source_breakdown"]
    assert bd["receipt"] == 2
    assert bd["manual"] == 1
    assert bd["vendor"] == 1
    assert sum(bd.values()) == 4   # active only


def test_valuation_math_unchanged_by_cost_source(db):
    """The dollar totals must be IDENTICAL to the pre-existing behavior —
    cost_source is purely informational, never a pricing input."""
    _product(db, sku="A", qty_on_hand=5, cost=10.0, cost_source="receipt")
    _product(db, sku="B", qty_on_hand=3, cost=10.0, cost_source="manual")
    db.commit()

    totals = ReportService(db).get_inventory_valuation_summary()["totals"]
    assert totals["total_value"] == 80.0   # 5*10 + 3*10, regardless of cost_source


# ── zero_cost_recoverable_count ────────────────────────────────────────────

def test_zero_cost_recoverable_counts_products_with_vendor_cost(db):
    v = _vendor(db)
    recoverable = _product(db, sku="ZC-1", qty_on_hand=4, cost=0.0)
    _source(db, recoverable.id, v.id, vendor_cost=850.0)

    no_vendor = _product(db, sku="ZC-2", qty_on_hand=2, cost=0.0)   # no source at all
    db.commit()

    totals = ReportService(db).get_inventory_valuation_summary()["totals"]
    assert totals["zero_cost_count"] == 2
    assert totals["zero_cost_recoverable_count"] == 1


def test_zero_cost_recoverable_ignores_inactive_or_zero_vendor_sources(db):
    v = _vendor(db)
    p1 = _product(db, sku="ZC-3", qty_on_hand=1, cost=0.0)
    _source(db, p1.id, v.id, vendor_cost=100.0, is_active=False)   # inactive source
    p2 = _product(db, sku="ZC-4", qty_on_hand=1, cost=0.0)
    _source(db, p2.id, v.id, vendor_cost=0.0, is_active=True)      # zero vendor cost
    db.commit()

    totals = ReportService(db).get_inventory_valuation_summary()["totals"]
    assert totals["zero_cost_count"] == 2
    assert totals["zero_cost_recoverable_count"] == 0


def test_zero_cost_recoverable_does_not_double_count_multiple_sources(db):
    """A product with SEVERAL vendor sources must still count once — the
    correlated EXISTS must not fan out the surrounding GROUP BY aggregates."""
    v1 = _vendor(db, name="PAI Industries", code="PAI")
    v2 = _vendor(db, name="Interstate-McBee", code="IMB")
    p = _product(db, sku="ZC-5", qty_on_hand=3, cost=0.0)
    _source(db, p.id, v1.id, vendor_cost=100.0)
    _source(db, p.id, v2.id, vendor_cost=150.0)
    db.commit()

    totals = ReportService(db).get_inventory_valuation_summary()["totals"]
    assert totals["sku_count"] == 1              # no fan-out from the 2 sources
    assert totals["zero_cost_recoverable_count"] == 1
    assert totals["total_value"] == 0.0           # valuation math still untouched


# ── per-row cost_source + recoverable_cost ─────────────────────────────────

def test_row_level_cost_source_and_recoverable_cost(db):
    v = _vendor(db)
    p_recoverable = _product(db, sku="ROW-1", qty_on_hand=2, cost=0.0, cost_source="manual")
    _source(db, p_recoverable.id, v.id, vendor_cost=75.0)
    _product(db, sku="ROW-2", qty_on_hand=1, cost=0.0, cost_source="manual")   # no vendor cost
    _product(db, sku="ROW-3", qty_on_hand=1, cost=20.0, cost_source="receipt")  # not zero-cost
    db.commit()

    rows = {r["sku"]: r for r in ReportService(db).get_inventory_valuation(page_size=100)["rows"]}

    assert rows["ROW-1"]["warning"] == "zero_cost"
    assert rows["ROW-1"]["cost_source"] == "manual"
    assert rows["ROW-1"]["recoverable_cost"] == 75.0

    assert rows["ROW-2"]["warning"] == "zero_cost"
    assert rows["ROW-2"]["recoverable_cost"] is None   # no vendor cost anywhere

    assert rows["ROW-3"]["warning"] is None
    assert rows["ROW-3"]["cost_source"] == "receipt"
    assert rows["ROW-3"]["recoverable_cost"] is None   # not a zero-cost row at all
