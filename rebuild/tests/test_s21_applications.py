"""
tests/test_s21_applications.py
==============================
§21 post-PAI cleanup — ProductApplication (engine fitment) CRUD on product detail.
Adds engines a part fits so it surfaces in the engine-application search.
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
from fastapi.testclient import TestClient

from app.main import app
from app.constants import ProductStatus
from app.models.product import Product, ProductApplication
from app.services.product_service import ProductService

_ENGINE = fresh_engine()
_UID = 1
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db) -> Product:
    p = Product(sku=f"APP-{next(_seq):04d}", title="Part", description="Part",
                cost=10.0, markup_pct=50.0, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_add_application(db):
    p = _product(db)
    svc = ProductService(db, _UID)
    app_row = svc.add_application(p.id, "Cummins", "ISX", cpl="2732", esn_range="79000-")
    assert app_row.id is not None
    assert app_row.engine_make == "Cummins" and app_row.engine_model == "ISX"
    assert db.query(ProductApplication).filter_by(product_id=p.id).count() == 1


def test_add_application_dedups_on_grain(db):
    p = _product(db)
    svc = ProductService(db, _UID)
    svc.add_application(p.id, "Detroit", "DD15", cpl="A1")
    # Same make/model/cpl (case-insensitive) → refresh, not a duplicate row.
    svc.add_application(p.id, "detroit", "dd15", cpl="A1", notes="updated")
    rows = db.query(ProductApplication).filter_by(product_id=p.id).all()
    assert len(rows) == 1
    assert rows[0].notes == "updated"


def test_add_application_requires_make(db):
    p = _product(db)
    with pytest.raises(ValueError, match="Engine make is required"):
        ProductService(db, _UID).add_application(p.id, "  ")


def test_remove_application(db):
    p = _product(db)
    svc = ProductService(db, _UID)
    a = svc.add_application(p.id, "CAT", "C15")
    svc.remove_application(a.id)
    assert db.query(ProductApplication).filter_by(product_id=p.id).count() == 0


def test_application_routes_add_and_delete(client, db):
    p = _product(db)
    # Add via the route → returns the re-rendered list containing the new row.
    r = client.post(f"/products/{p.id}/applications",
                    data={"engine_make": "Paccar", "engine_model": "MX-13", "cpl": "5"})
    assert r.status_code == 200
    assert "Paccar" in r.text and 'id="apps-list"' in r.text

    app_row = db.query(ProductApplication).filter_by(product_id=p.id).first()
    r = client.delete(f"/products/{p.id}/applications/{app_row.id}")
    assert r.status_code == 200
    assert "Paccar" not in r.text          # gone from the re-rendered list
    assert db.query(ProductApplication).filter_by(product_id=p.id).count() == 0


def test_add_application_route_requires_make(client, db):
    p = _product(db)
    r = client.post(f"/products/{p.id}/applications", data={"engine_make": ""})
    assert r.status_code == 422
    assert "Engine make is required" in r.text
