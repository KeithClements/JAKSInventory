"""
tests/test_product_core_charge_guard.py
========================================
Money guard: a product's CUSTOMER core charge may never be LESS than its VENDOR
core charge. The core charge is a deposit JAKS recovers from the vendor when the
old core is returned; charging the customer less means every unreturned core is a
loss. Owner-reported regression (the old inline warning even had the comparison
backwards — see app/templates/products/detail.html).

Rule: customer_core_charge >= vendor_core_charge (equal is allowed — pass-through).
Enforced once in ProductService so create, update, AND quick-create all inherit it.

Run (isolated in-memory engine — never touches jaks.db):
    .venv\\Scripts\\python.exe -m pytest tests/test_product_core_charge_guard.py -v
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.main import app
from app.services.product_service import ProductService
from app.models.product import Product
from fastapi.testclient import TestClient

_ENGINE = fresh_engine()


@pytest.fixture(scope="module", autouse=True)
def _activate():
    activate(_ENGINE)


def _svc():
    return ProductService(_appdb.SessionLocal(), current_user_id=1)


# ── create_product ──────────────────────────────────────────────────────────────

def test_create_blocks_customer_core_below_vendor():
    with pytest.raises(ValueError) as exc:
        _svc().create_product({
            "sku": "CORE-LOW", "title": "Reman widget", "cost": 100.0,
            "has_core": True, "vendor_core_charge": 10.0, "customer_core_charge": 7.0,
        })
    msg = str(exc.value).lower()
    assert "core charge" in msg and "less than" in msg
    # Nothing persisted on a rejected save.
    assert _appdb.SessionLocal().query(Product).filter(Product.sku == "CORE-LOW").first() is None


def test_create_allows_customer_core_equal_to_vendor():
    svc = _svc()  # hold the service so its session stays bound after commit
    p = svc.create_product({
        "sku": "CORE-EQ", "title": "Even widget", "cost": 100.0,
        "has_core": True, "vendor_core_charge": 25.0, "customer_core_charge": 25.0,
    })
    assert p.id is not None and p.customer_core_charge == 25.0


def test_create_allows_customer_core_above_vendor():
    svc = _svc()
    p = svc.create_product({
        "sku": "CORE-HI", "title": "Marked-up widget", "cost": 100.0,
        "has_core": True, "vendor_core_charge": 20.0, "customer_core_charge": 30.0,
    })
    assert p.id is not None and p.customer_core_charge == 30.0


def test_create_ignores_charges_when_no_core():
    """has_core off → the mismatched charges are irrelevant and must not block."""
    svc = _svc()
    p = svc.create_product({
        "sku": "NO-CORE", "title": "Plain widget", "cost": 100.0,
        "has_core": False, "vendor_core_charge": 10.0, "customer_core_charge": 0.0,
    })
    assert p.id is not None


# ── update_product ──────────────────────────────────────────────────────────────

def test_update_blocks_lowering_customer_core_below_vendor():
    svc = _svc()
    p = svc.create_product({
        "sku": "CORE-UPD", "title": "Editable widget", "cost": 100.0,
        "has_core": True, "vendor_core_charge": 50.0, "customer_core_charge": 60.0,
    })
    with pytest.raises(ValueError):
        _svc().update_product(p.id, {"customer_core_charge": 40.0})
    # The bad edit left the stored value untouched.
    fresh = _appdb.SessionLocal().query(Product).filter(Product.id == p.id).first()
    assert fresh.customer_core_charge == 60.0


def test_update_allows_valid_core_change():
    svc = _svc()
    p = svc.create_product({
        "sku": "CORE-UPD2", "title": "Editable widget 2", "cost": 100.0,
        "has_core": True, "vendor_core_charge": 50.0, "customer_core_charge": 60.0,
    })
    _svc().update_product(p.id, {"vendor_core_charge": 70.0, "customer_core_charge": 80.0})
    fresh = _appdb.SessionLocal().query(Product).filter(Product.id == p.id).first()
    assert fresh.vendor_core_charge == 70.0 and fresh.customer_core_charge == 80.0


# ── route surfaces the error (422, product not created) ───────────────────────────

def test_create_route_rejects_with_422():
    client = TestClient(app)
    resp = client.post("/products/new", data={
        "sku": "ROUTE-CORE-LOW", "title": "Route widget", "cost": "100",
        "markup_pct": "30", "reorder_point": "0",
        "has_core": "on", "vendor_core_charge": "10", "customer_core_charge": "7",
    })
    assert resp.status_code == 422
    assert "core charge" in resp.text.lower()
    assert _appdb.SessionLocal().query(Product).filter(Product.sku == "ROUTE-CORE-LOW").first() is None
