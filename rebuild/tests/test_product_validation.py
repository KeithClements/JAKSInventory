"""
tests/test_product_validation.py
================================
Server-side money-validation guard for products.

QA (headed browser, 2026-06-29) found that saving a product with ``cost=-50``
succeeded — the new/edit form only sets ``min=0`` client-side, so a crafted or
autofilled POST slipped a negative cost straight into the DB. The fix adds
``ProductService._validate_pricing``, called on create AND update (and therefore
inherited by autosave + quick-create), rejecting any negative money value while
still allowing 0 (legitimate pre-receipt cost / 0 % markup / $0 override).

Companion (LOW): the new-product template's spurious core-charge warning banner
— it must only render on a REAL money-guard violation (core enabled AND customer
core < vendor core), never when the core is off and both charges are 0. That's an
Alpine ``coreFlag`` getter; we assert the template ships the guarded condition so
the banner can't unconditionally show.

Run (isolated in-memory engine — never touches jaks.db):
    .venv\\Scripts\\python.exe -m pytest tests/test_product_validation.py -v
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
from app.models.vendor import Vendor
from fastapi.testclient import TestClient

_ENGINE = fresh_engine()


@pytest.fixture(scope="module", autouse=True)
def _activate():
    activate(_ENGINE)


def _svc():
    return ProductService(_appdb.SessionLocal(), current_user_id=1)


# Per-test isolated DB for ROUTE tests — the auto-SKU POST path needs a seeded
# vendor, so each route test gets a fresh engine (mirrors test_products_new_form.py).
@pytest.fixture()
def route_db():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        # Re-point the module globals back at the shared engine for the
        # service-level tests that run after (autouse _activate is module-scoped,
        # so it does not re-run between functions).
        activate(_ENGINE)


@pytest.fixture()
def route_client(route_db):
    return TestClient(app, follow_redirects=False)


def _seed_vendor(db) -> Vendor:
    v = Vendor(name="PAI Industries", vendor_code="PAI",
               vendor_number="9", is_active=True, private_label=False)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


# ── create_product: negative money rejected ───────────────────────────────────

def test_create_rejects_negative_cost():
    with pytest.raises(ValueError) as exc:
        _svc().create_product({
            "sku": "NEG-COST", "title": "Bad widget", "cost": -50.0,
        })
    msg = str(exc.value).lower()
    assert "cost" in msg and "negative" in msg
    # Nothing persisted on a rejected save.
    assert _appdb.SessionLocal().query(Product).filter(Product.sku == "NEG-COST").first() is None


def test_create_rejects_negative_markup():
    with pytest.raises(ValueError):
        _svc().create_product({
            "sku": "NEG-MARKUP", "title": "Bad markup", "cost": 10.0, "markup_pct": -5.0,
        })
    assert _appdb.SessionLocal().query(Product).filter(Product.sku == "NEG-MARKUP").first() is None


def test_create_rejects_negative_price_override():
    with pytest.raises(ValueError):
        _svc().create_product({
            "sku": "NEG-PRICE", "title": "Bad price", "cost": 10.0, "price_override": -1.0,
        })
    assert _appdb.SessionLocal().query(Product).filter(Product.sku == "NEG-PRICE").first() is None


# ── create_product: zero + positive cost still save ───────────────────────────

def test_create_allows_zero_cost():
    """0 is legitimate pre-receipt (moving-avg COGS is 0 until first receipt)."""
    svc = _svc()  # hold the service so its session stays bound after commit
    p = svc.create_product({
        "sku": "ZERO-COST", "title": "Pre-receipt widget", "cost": 0.0,
    })
    assert p.id is not None and p.cost == 0.0


def test_create_allows_positive_cost():
    svc = _svc()
    p = svc.create_product({
        "sku": "POS-COST", "title": "Normal widget", "cost": 123.45, "markup_pct": 30.0,
    })
    assert p.id is not None and p.cost == 123.45


def test_create_allows_zero_markup_and_override():
    """0 % markup and a $0 override are real values, not 'unset' — must save."""
    svc = _svc()
    p = svc.create_product({
        "sku": "ZERO-PRICING", "title": "Sell-at-cost widget", "cost": 5.0,
        "markup_pct": 0.0, "price_override": 0.0,
    })
    assert p.id is not None and p.markup_pct == 0.0


# ── update_product: negative money rejected, stored value untouched ────────────

def test_update_rejects_negative_cost():
    svc = _svc()
    p = svc.create_product({
        "sku": "UPD-COST", "title": "Editable widget", "cost": 100.0,
    })
    with pytest.raises(ValueError) as exc:
        _svc().update_product(p.id, {"cost": -25.0})
    assert "negative" in str(exc.value).lower()
    # The bad edit left the stored value untouched (and no cost-history row).
    fresh = _appdb.SessionLocal().query(Product).filter(Product.id == p.id).first()
    assert fresh.cost == 100.0


def test_update_allows_zero_cost():
    svc = _svc()
    p = svc.create_product({
        "sku": "UPD-ZERO", "title": "Editable widget 2", "cost": 100.0,
    })
    _svc().update_product(p.id, {"cost": 0.0})
    fresh = _appdb.SessionLocal().query(Product).filter(Product.id == p.id).first()
    assert fresh.cost == 0.0


def test_update_allows_positive_cost():
    svc = _svc()
    p = svc.create_product({
        "sku": "UPD-POS", "title": "Editable widget 3", "cost": 100.0,
    })
    _svc().update_product(p.id, {"cost": 250.0})
    fresh = _appdb.SessionLocal().query(Product).filter(Product.id == p.id).first()
    assert fresh.cost == 250.0


def test_update_without_cost_does_not_trip_guard():
    """A partial update that omits cost must never be rejected on a stored value."""
    svc = _svc()
    p = svc.create_product({
        "sku": "UPD-PARTIAL", "title": "Editable widget 4", "cost": 100.0,
    })
    _svc().update_product(p.id, {"title": "Renamed widget"})
    fresh = _appdb.SessionLocal().query(Product).filter(Product.id == p.id).first()
    assert fresh.title == "Renamed widget" and fresh.cost == 100.0


# ── route surfaces the error (422, product not created) ───────────────────────
# These drive the real POST /products/new (auto-SKU path) with a seeded vendor, so
# the negative-cost guard is reached after the vendor/part# pre-checks.

def test_create_route_rejects_negative_cost_with_422(route_client, route_db):
    vendor = _seed_vendor(route_db)
    resp = route_client.post("/products/new", data={
        "vendor_id": str(vendor.id),
        "vendor_part_number": "NEGROUTE-1",
        "title": "Route widget",
        "cost": "-50",
        "reorder_point": "0",
        "vendor_core_charge": "0", "customer_core_charge": "0",
    })
    assert resp.status_code == 422
    assert "negative" in resp.text.lower()
    # Nothing persisted on a rejected save.
    assert route_db.query(Product).count() == 0


def test_create_route_accepts_zero_cost(route_client, route_db):
    """0 cost via the form is valid — must NOT 422 on the pricing guard.
    A redirect 303 means the product was created."""
    vendor = _seed_vendor(route_db)
    resp = route_client.post("/products/new", data={
        "vendor_id": str(vendor.id),
        "vendor_part_number": "ZEROROUTE-1",
        "title": "Zero route widget",
        "cost": "0",
        "reorder_point": "0",
        "vendor_core_charge": "0", "customer_core_charge": "0",
    })
    assert resp.status_code == 303, resp.text
    assert route_db.query(Product).count() == 1


# ── template: phantom core banner is properly guarded (LOW) ────────────────────

def test_new_product_template_core_banner_is_guarded():
    """The core-charge warning banner must be gated on a REAL violation, not shown
    unconditionally. We assert the template's Alpine `coreFlag` getter requires the
    core to be enabled (`hasCore`) AND a strict customer < vendor comparison, and
    that the banner div is bound to `x-show="coreFlag"` (never always-visible)."""
    tpl = (pathlib.Path(__file__).resolve().parents[1]
           / "app" / "templates" / "products" / "new.html").read_text(encoding="utf-8")
    # The getter short-circuits when the core is disabled …
    assert "if (!this.hasCore) return false;" in tpl
    # … and only flags when customer core is strictly below vendor core.
    assert "return cust < vend;" in tpl
    # The warning banner renders only via the guarded flag (not unconditionally).
    assert 'x-show="coreFlag"' in tpl
