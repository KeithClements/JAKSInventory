"""
tests/test_s18_category_maintenance.py
======================================
§18 Inventory → Category Maintenance — screen renders + CRUD round-trips for the
three maintained axes (category tree, brands, manufacturers/engine-makes).
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine
from app.main import app
from app.models.product import Brand, Manufacturer, ProductCategory
from app.seeds import seed_brands, seed_default_categories, seed_manufacturers

_ENGINE = fresh_engine()


@pytest.fixture(autouse=True)
def _clean():
    """Fresh, empty classification tables per test (immune to import order)."""
    SessionLocal = activate(_ENGINE)
    db = SessionLocal()
    db.query(ProductCategory).delete()
    db.query(Brand).delete()
    db.query(Manufacturer).delete()
    db.commit()
    db.close()
    yield


def _session():
    return activate(_ENGINE)()


def test_maintenance_page_renders_with_seeded_lists():
    db = _session()
    seed_default_categories(db)
    seed_brands(db)
    seed_manufacturers(db)
    db.close()

    client = TestClient(app)
    r = client.get("/categories/")
    assert r.status_code == 200
    assert "Category Maintenance" in r.text
    # NB: "JAK'S" renders HTML-escaped (JAK&#39;S) — assert on apostrophe-free names.
    assert "PAI" in r.text and "SAMPA" in r.text and "Interstate-McBee" in r.text
    assert "Cummins" in r.text                             # manufacturer list rendered
    assert "Engine" in r.text                              # seeded category tree rendered


def test_create_category_then_subcategory():
    client = TestClient(app)
    r = client.post(
        "/categories/category/create",
        data={"name": "Cooling System", "sort_order": "3",
              "import_keywords": "water pump, radiator"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = _session()
    parent = db.query(ProductCategory).filter(ProductCategory.name == "Cooling System").first()
    assert parent is not None
    assert parent.level == 1 and parent.sort_order == 3
    assert "water pump" in parent.import_keywords
    pid = parent.id
    db.close()

    # child → level 2 (Subcategory)
    r2 = client.post(
        "/categories/category/create",
        data={"name": "Water Pumps", "parent_id": str(pid)},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    db = _session()
    sub = db.query(ProductCategory).filter(ProductCategory.name == "Water Pumps").first()
    assert sub is not None and sub.level == 2 and sub.parent_id == pid
    db.close()


def test_update_and_toggle_and_delete_category():
    client = TestClient(app)
    client.post("/categories/category/create", data={"name": "Temp Cat"}, follow_redirects=False)
    db = _session()
    cat = db.query(ProductCategory).filter(ProductCategory.name == "Temp Cat").first()
    cid = cat.id
    db.close()

    # update
    client.post(f"/categories/category/{cid}/update",
                data={"name": "Renamed Cat", "sort_order": "9", "default_markup_pct": "40",
                      "import_keywords": "foo"}, follow_redirects=False)
    db = _session()
    cat = db.get(ProductCategory, cid)
    assert cat.name == "Renamed Cat" and cat.sort_order == 9 and cat.default_markup_pct == 40.0
    db.close()

    # deactivate
    client.post(f"/categories/category/{cid}/toggle", data={"active": "0"}, follow_redirects=False)
    db = _session()
    assert db.get(ProductCategory, cid).is_active is False
    db.close()

    # delete (no children / no products → hard delete)
    client.post(f"/categories/category/{cid}/delete", follow_redirects=False)
    db = _session()
    assert db.get(ProductCategory, cid) is None
    db.close()


def test_brand_and_manufacturer_crud():
    client = TestClient(app)
    client.post("/categories/brand/create",
                data={"name": "Test Brand", "is_house_brand": "on"}, follow_redirects=False)
    client.post("/categories/manufacturer/create",
                data={"name": "Test Mfr"}, follow_redirects=False)
    db = _session()
    b = db.query(Brand).filter(Brand.name == "Test Brand").first()
    m = db.query(Manufacturer).filter(Manufacturer.name == "Test Mfr").first()
    assert b is not None and b.is_house_brand is True
    assert m is not None
    db.close()

    # duplicate brand rejected (case-insensitive) → flash error, no second row
    client.post("/categories/brand/create", data={"name": "test brand"}, follow_redirects=False)
    db = _session()
    assert db.query(Brand).filter(Brand.name.ilike("test brand")).count() == 1
    db.close()
