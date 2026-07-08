"""
tests/test_s18_products_filters.py
==================================
§18 Products List — category / manufacturer / brand filters, Needs-Review &
Uncategorized tabs, and the bulk Assign Category/Manufacturer action.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine
from app.main import app
from app.models.product import Product, ProductCategory

_ENGINE = fresh_engine()


@pytest.fixture(autouse=True)
def _clean():
    SL = activate(_ENGINE)
    db = SL()
    db.query(Product).delete()
    db.query(ProductCategory).delete()
    db.commit()
    db.close()
    yield


def _session():
    return activate(_ENGINE)()


def _seed_catalog():
    db = _session()
    eng = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(eng)
    db.flush()
    cool = ProductCategory(name="Cooling", parent_id=eng.id, level=2, is_active=True)
    db.add(cool)
    db.flush()
    db.add_all([
        Product(sku="TESTWP", title="Water Pump", is_active=True,
                category_id=cool.id, engine_manufacturer="Cummins", brand="PAI"),
        Product(sku="TESTGK", title="Gasket", is_active=True,
                category_id=None, engine_manufacturer="", brand="PAI", needs_review=True),
        Product(sku="TESTLN", title="Liner", is_active=True,
                category_id=eng.id, engine_manufacturer="CAT / Caterpillar", brand="SAMPA"),
    ])
    db.commit()
    ids = {"eng": eng.id, "cool": cool.id,
           "gk": db.query(Product).filter(Product.sku == "TESTGK").first().id}
    db.close()
    return ids


def test_category_filter_includes_descendants():
    ids = _seed_catalog()
    client = TestClient(app)
    # Top-level Engine → includes its Cooling subcategory → WP + LN, not GK
    r = client.get(f"/products/?category_id={ids['eng']}")
    assert r.status_code == 200
    assert "TESTWP" in r.text and "TESTLN" in r.text and "TESTGK" not in r.text
    # Subcategory Cooling → only WP
    r = client.get(f"/products/?category_id={ids['cool']}")
    assert "TESTWP" in r.text and "TESTLN" not in r.text


def test_manufacturer_and_brand_filters():
    _seed_catalog()
    client = TestClient(app)
    r = client.get("/products/?manufacturer=Cummins")
    assert "TESTWP" in r.text and "TESTLN" not in r.text
    r = client.get("/products/?brand=SAMPA")
    assert "TESTLN" in r.text and "TESTWP" not in r.text


def test_needs_review_and_uncategorized_tabs():
    _seed_catalog()
    client = TestClient(app)
    r = client.get("/products/?tab=needs_review")
    assert "TESTGK" in r.text and "TESTWP" not in r.text
    r = client.get("/products/?tab=uncategorized")
    assert "TESTGK" in r.text and "TESTWP" not in r.text


def test_bulk_assign_sets_category_manufacturer_and_clears_review():
    ids = _seed_catalog()
    client = TestClient(app)
    r = client.post(
        "/products/bulk-assign",
        data={"product_ids": str(ids["gk"]),
              "category_id": str(ids["cool"]),
              "manufacturer": "Cummins"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = _session()
    gk = db.get(Product, ids["gk"])
    assert gk.category_id == ids["cool"]
    assert gk.engine_manufacturer == "Cummins"
    assert gk.needs_review is False        # triaged → leaves the review queue
    db.close()


def test_import_review_queue_renders_and_assign_returns_to_queue():
    ids = _seed_catalog()
    client = TestClient(app)
    r = client.get("/products/review")
    assert r.status_code == 200
    assert "Flagged Products" in r.text   # renamed from "Import Review" (import module consolidation)
    assert "TESTGK" in r.text          # the needs_review item is queued
    assert "TESTWP" not in r.text      # a confident item is not

    # Assign via the review form → returns to the queue and clears the flag.
    r2 = client.post(
        "/products/bulk-assign",
        data={"product_ids": str(ids["gk"]), "category_id": str(ids["cool"]),
              "return_to": "/products/review"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/products/review"
    db = _session()
    assert db.get(Product, ids["gk"]).needs_review is False
    db.close()

    # Queue is now empty.
    r3 = client.get("/products/review")
    assert "Nothing to review" in r3.text
