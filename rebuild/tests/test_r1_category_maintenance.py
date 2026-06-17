"""
tests/test_r1_category_maintenance.py
=====================================
R1-6 (CRITICAL) + R1-7b — Category Maintenance fixes:

  R1-6  Renaming a Manufacturer (engine make) or Brand lookup row must CASCADE
        to the free-text Product.engine_manufacturer / Product.brand columns
        (no FK exists), or every tagged part silently drops out of the
        engine-make / brand filters across the catalog.

  R1-7b ProductCategory.code (the SKU [CATEGORY] segment) is now editable via
        CategoryService.create_category/update_category and the /categories/
        POST routes — stripped + uppercased server-side; blank keeps the
        derived-fallback behavior (SkuService.derive_category_code).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r1_category_maintenance.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
import app.models  # noqa: F401 — registers all models
from app.main import app
from app.models.product import Brand, Manufacturer, Product, ProductCategory
from app.services.category_service import CategoryService
from app.services.sku_service import SkuService, derive_category_code
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, **kw) -> Product:
    n = next(_seq)
    p = Product(sku=f"R1-SKU-{n}", title=f"R1 Part {n}", **kw)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ══ R1-6 — manufacturer rename cascade ═══════════════════════════════════════
def test_manufacturer_rename_cascades_to_products(db):
    svc = CategoryService(db)
    m = svc.create_manufacturer("CUMMINS R1")
    tagged_a = _product(db, engine_manufacturer="CUMMINS R1")
    tagged_b = _product(db, engine_manufacturer="CUMMINS R1")
    other = _product(db, engine_manufacturer="CAT R1")

    svc.update_manufacturer(m.id, name="Cummins R1")

    db.expire_all()
    assert m.name == "Cummins R1"
    assert tagged_a.engine_manufacturer == "Cummins R1"
    assert tagged_b.engine_manufacturer == "Cummins R1"
    assert other.engine_manufacturer == "CAT R1"  # untouched


def test_manufacturer_cascade_is_case_insensitive(db):
    # §21 — the rename cascade is now case-INSENSITIVE so bulk-imported casing
    # variants ('detroit r1' vs 'Detroit R1') are re-tagged, not orphaned from
    # the engine-make filter. (Was exact-match before §21.)
    svc = CategoryService(db)
    m = svc.create_manufacturer("Detroit R1")
    variant = _product(db, engine_manufacturer="detroit r1")  # different case

    svc.update_manufacturer(m.id, name="Detroit Diesel R1")

    db.expire_all()
    assert variant.engine_manufacturer == "Detroit Diesel R1"  # casing variant re-tagged


def test_manufacturer_update_without_rename_leaves_products(db):
    svc = CategoryService(db)
    m = svc.create_manufacturer("Volvo R1")
    tagged = _product(db, engine_manufacturer="Volvo R1")

    svc.update_manufacturer(m.id, sort_order=5)

    db.expire_all()
    assert m.sort_order == 5
    assert tagged.engine_manufacturer == "Volvo R1"


# ══ R1-6 — brand rename cascade ══════════════════════════════════════════════
def test_brand_rename_cascades_to_products(db):
    svc = CategoryService(db)
    b = svc.create_brand("PAI R1")
    tagged = _product(db, brand="PAI R1")
    other = _product(db, brand="SAMPA R1")

    svc.update_brand(b.id, name="PAI Industries R1")

    db.expire_all()
    assert b.name == "PAI Industries R1"
    assert tagged.brand == "PAI Industries R1"
    assert other.brand == "SAMPA R1"  # untouched


# ══ R1-7b — category code through the service ════════════════════════════════
def test_create_category_with_code_persists_uppercase(db):
    cat = CategoryService(db).create_category(name="Injectors R1", code=" inj ")
    assert cat.code == "INJ"
    # SkuService prefers the explicit code over the derived fallback.
    assert SkuService(db).code_for_category(cat) == "INJ"


def test_create_category_blank_code_keeps_derived_fallback(db):
    cat = CategoryService(db).create_category(name="Gaskets R1")
    assert cat.code == ""
    assert SkuService(db).code_for_category(cat) == derive_category_code("Gaskets R1")


def test_update_category_code_uppercases_and_blank_clears(db):
    svc = CategoryService(db)
    cat = svc.create_category(name="Fuel Pumps R1", code="FP")

    svc.update_category(cat.id, code="fpx ")
    assert cat.code == "FPX"

    # Blanking the input clears the explicit code → derived fallback again.
    svc.update_category(cat.id, code="")
    assert cat.code == ""
    assert SkuService(db).code_for_category(cat) == derive_category_code("Fuel Pumps R1")


# ══ R1-7b — route wiring ═════════════════════════════════════════════════════
def test_category_create_and_update_routes_wire_code(client, db):
    r = client.post("/categories/category/create", data={
        "name": "Turbos R1", "parent_id": "", "sort_order": "0",
        "default_markup_pct": "", "import_keywords": "", "code": "trb",
    })
    assert r.status_code == 303

    cat = db.query(ProductCategory).filter(ProductCategory.name == "Turbos R1").one()
    assert cat.code == "TRB"

    r = client.post(f"/categories/category/{cat.id}/update", data={
        "name": "Turbos R1", "sort_order": "0",
        "default_markup_pct": "", "import_keywords": "", "code": "tur",
    })
    assert r.status_code == 303
    db.expire_all()
    assert cat.code == "TUR"
