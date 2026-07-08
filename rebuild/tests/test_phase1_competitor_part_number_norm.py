"""
tests/test_phase1_competitor_part_number_norm.py
=================================================
MASTER_PLAN §23.3 Phase 1 #2 — competitor_part_number_norm column + index +
normalization listener + search_index._TARGETS.

CompetitorPrice.competitor_part_number search used to normalize every row with
a SQL function on every keystroke (search_service._norm_col), the same O(rows)
cost problem sku_norm/ref_number_norm/vendor_*_norm were built to fix — the
last unindexed part-number search path. These pin the fix: a precomputed,
indexed competitor_part_number_norm column, kept in sync by an ORM listener
(same convention as the other three _norm columns in app/models/product.py),
feeding SearchService's competitor-number strategy.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401
from app.constants import ProductStatus
from app.models.competitor import CompetitorPrice
from app.models.product import Product
from app.services.search_index import _TARGETS, ensure_search_norm_columns
from app.services.search_service import SearchService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, sku: str, title: str = "Test Part") -> Product:
    p = Product(sku=sku, title=title, cost=100.0, markup_pct=40.0,
                qty_on_hand=2, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _comp_price(db, product_id: int, part: str, name: str = "HHP", price: float = 88.0) -> CompetitorPrice:
    cp = CompetitorPrice(product_id=product_id, competitor_name=name,
                         competitor_part_number=part, price=price)
    db.add(cp); db.commit(); db.refresh(cp)
    return cp


def test_norm_column_populated_on_insert(db):
    """The before_insert listener stamps competitor_part_number_norm the
    moment a CompetitorPrice row is created — no separate backfill step needed
    for rows written by the app."""
    prod = _product(db, "JAKS-ISX-INJ-90001")
    cp = _comp_price(db, prod.id, "HHP-2026(A)")
    assert cp.competitor_part_number_norm == "hhp2026a"


def test_norm_column_updated_on_update(db):
    prod = _product(db, "JAKS-ISX-INJ-90002")
    cp = _comp_price(db, prod.id, "OLD-123")
    assert cp.competitor_part_number_norm == "old123"
    cp.competitor_part_number = "NEW-456"
    db.commit(); db.refresh(cp)
    assert cp.competitor_part_number_norm == "new456"


def test_competitor_prices_registered_in_search_index_targets():
    """search_index._TARGETS must carry this column so
    ensure_search_norm_columns (startup) adds/backfills/indexes it — the same
    mechanism sku_norm/ref_number_norm/vendor_*_norm rely on."""
    assert (
        "competitor_prices", "competitor_part_number", "competitor_part_number_norm"
    ) in _TARGETS


def test_ensure_search_norm_columns_indexes_competitor_prices(db):
    """Runs the real startup routine against the test engine and confirms the
    index actually gets created (not just the column)."""
    conn = db.get_bind().connect()
    try:
        ensure_search_norm_columns(conn)
        conn.commit()
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='competitor_prices'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "ix_competitor_prices_competitor_part_number_norm" in names
    finally:
        conn.close()


def test_search_by_normalized_competitor_number_uses_indexed_column(db):
    """End-to-end: SearchService finds the product via the indexed norm column,
    matching separators/case exactly like the old _norm_col() query did."""
    prod = _product(db, "JAKS-ISX-INJ-90003", "Injector")
    _comp_price(db, prod.id, "3683512(C)", name="HHP")

    svc = SearchService(db)
    results = svc.search_products("3683512C")   # fully-normalized query form
    assert any(r.product_id == prod.id and r.match_type == "competitor" for r in results)


def test_search_by_competitor_number_only_matches_active_prices(db):
    prod = _product(db, "JAKS-ISX-INJ-90004", "Injector")
    cp = _comp_price(db, prod.id, "ATL-999", name="ATL Diesel")
    cp.is_active = False
    db.commit()

    svc = SearchService(db)
    results = svc.search_products("ATL-999")
    assert not any(r.product_id == prod.id and r.match_type == "competitor" for r in results)
