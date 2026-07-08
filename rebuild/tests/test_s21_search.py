"""
tests/test_s21_search.py
========================
§21 counter-findability — barcode/UPC lookup + engine-application search, plus
the precomputed sku_norm column. These were the audit's top missing daily-counter
search features (ProductApplication was populated but never queried; the Scan
button had no backing lookup).
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest

from app.constants import ProductStatus
from app.models.product import Product, ProductApplication
from app.services.search_service import SearchService

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(_ENGINE)
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, *, sku, title="Part", barcode=None) -> Product:
    p = Product(sku=sku, title=title, description=title, cost=20.0, markup_pct=50.0,
                barcode=barcode, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_sku_norm_populated_by_listener(db):
    p = _product(db, sku="OK-1234")
    assert p.sku_norm == "ok1234"        # lower + separators stripped
    p.sku = "AB.99/9"
    db.commit(); db.refresh(p)
    assert p.sku_norm == "ab999"         # kept in sync on update


def test_barcode_exact_match(db):
    _product(db, sku="WP-100", barcode="012345678905")
    res = SearchService(db).search_products("012345678905")
    assert res and res[0].match_type == "barcode"
    assert res[0].part_number == "WP-100"


def test_barcode_match_ignores_separators(db):
    _product(db, sku="WP-200", barcode="0123-4567")
    res = SearchService(db).search_products("0123 4567")
    assert any(r.match_type == "barcode" and r.part_number == "WP-200" for r in res)


def test_engine_application_search(db):
    prod = _product(db, sku="INJ-500", title="Injector")
    db.add(ProductApplication(product_id=prod.id, engine_make="Cummins",
                              engine_model="ISX", cpl="2732"))
    db.commit()
    # Full "make model"
    res = SearchService(db).search_products("Cummins ISX")
    assert any(r.match_type == "engine_app" and r.part_number == "INJ-500" for r in res)
    # Model only
    res2 = SearchService(db).search_products("ISX")
    assert any(r.part_number == "INJ-500" for r in res2)


def test_part_number_outranks_engine_application(db):
    # A real SKU match must rank above an engine-fit match.
    _product(db, sku="ISX", title="Literal ISX SKU")
    other = _product(db, sku="ZZ-9", title="Other")
    db.add(ProductApplication(product_id=other.id, engine_make="Cummins",
                              engine_model="ISX", cpl="1"))
    db.commit()
    res = SearchService(db).search_products("ISX")
    assert res[0].part_number == "ISX"            # SKU match wins
    assert res[0].match_type == "part_number"
