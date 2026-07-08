"""
tests/test_multiterm_search.py
==============================
Multi-term keyword search — the query is split on whitespace and EVERY token
must match somewhere across the human-readable fields (title, description,
manufacturer, engine make, brand, category name).

Counter staff search the way they think ("a STUD kit for a CAT"), so "stud cat"
must find an "EXHAUST MANIFOLD STUD KIT" whose manufacturer is "Caterpillar"
even though no single field contains the contiguous phrase "stud cat". Before
this, the keyword step matched the whole query as one substring against
title/description only — so "stud cat" found nothing and the manufacturer field
was never searched at all.
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
from app.models.product import Product, ProductCategory
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


def _category(db, name) -> ProductCategory:
    c = ProductCategory(name=name)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, sku, title="Part", description="", manufacturer="",
             brand="", engine_manufacturer="", category=None) -> Product:
    p = Product(
        sku=sku, title=title, description=description or title,
        manufacturer=manufacturer, brand=brand,
        engine_manufacturer=engine_manufacturer,
        category_id=category.id if category else None,
        cost=20.0, markup_pct=50.0,
        status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_token_in_title_plus_manufacturer(db):
    # The reported case: word in the title + word in the manufacturer field.
    _product(db, sku="JAKS-PAI-340105", title="EXHAUST MANIFOLD STUD KIT",
             manufacturer="Caterpillar", brand="PAI")
    res = SearchService(db).search_products("stud cat")
    assert any(r.part_number == "JAKS-PAI-340105" for r in res), \
        "'stud' (title) + 'cat' (manufacturer) should AND-match"


def test_manufacturer_single_token(db):
    _product(db, sku="MFR-CAT-1", title="EXHAUST MANIFOLD STUD KIT",
             manufacturer="Caterpillar", brand="PAI")
    res = SearchService(db).search_products("caterpillar")
    assert any(r.part_number == "MFR-CAT-1" for r in res)


def test_all_tokens_required_and_semantics(db):
    # 'stud' matches, 'ford' matches nothing on this Caterpillar part → no hit.
    _product(db, sku="AND-CAT-1", title="EXHAUST MANIFOLD STUD KIT",
             manufacturer="Caterpillar", brand="PAI")
    res = SearchService(db).search_products("stud ford")
    assert not any(r.part_number == "AND-CAT-1" for r in res)


def test_brand_token(db):
    _product(db, sku="WP-1", title="Water Pump", brand="Interstate-McBee")
    res = SearchService(db).search_products("pump mcbee")
    assert any(r.part_number == "WP-1" for r in res)


def test_category_name_token(db):
    cat = _category(db, "Head Bolts / Fasteners")
    _product(db, sku="CATG-CAT-1", title="EXHAUST MANIFOLD STUD KIT",
             manufacturer="Caterpillar", category=cat)
    # 'fastener' lives only on the category name; 'cat' on the manufacturer.
    res = SearchService(db).search_products("fastener cat")
    assert any(r.part_number == "CATG-CAT-1" for r in res)


def test_exact_sku_outranks_keyword(db):
    # A real SKU match must still rank first, ahead of any keyword match.
    _product(db, sku="STUD-1", title="Some Stud", manufacturer="Caterpillar")
    _product(db, sku="OTHER-9", title="Caterpillar Stud Kit")
    res = SearchService(db).search_products("STUD-1")
    assert res[0].part_number == "STUD-1"
    assert res[0].match_type == "part_number"


def test_keyword_match_type_is_description(db):
    # The chip/badge templates key off match_type — keep keyword hits labelled
    # 'description' so the existing gray badge + DESC label still apply.
    _product(db, sku="GK-1", title="Upper Gasket Kit", manufacturer="Cummins")
    res = SearchService(db).search_products("gasket cummins")
    hit = next(r for r in res if r.part_number == "GK-1")
    assert hit.match_type == "description"
