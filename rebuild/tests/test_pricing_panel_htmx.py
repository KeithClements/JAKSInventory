"""
tests/test_pricing_panel_htmx.py
================================
Phase 3 — Stream 1: HTMX panel refresh for the customer "Pricing & Deals" panel.

Proves the dual-mode contract on the price-rule mutation routes:

  • WITH the ``HX-Request: true`` header  → the route returns the re-rendered
    ``customers/_pricing_deals_panel.html`` partial (HTML carrying
    ``id="pricing-deals-panel"`` and the new rule's human label) so the client can
    swap ``#pricing-deals-panel`` in place — no full page reload.

  • WITHOUT the header (the existing Phase-1/2 callers) → the route keeps
    returning the legacy JSON ``{ok: true, ...}`` contract verbatim. Backward
    compatibility for the acceptance suite, which never sends HX-Request.

Covers create / update / deactivate / bulk. Self-contained, isolated in-memory
engine (conftest pattern) so it never shares data with another lane's file.

Run:  python -m pytest tests/test_pricing_panel_htmx.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.constants import ProductStatus
from app.models.customer import Customer
from app.models.product import Product, ProductCategory
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_SEQ = {"n": 0}
_HX = {"HX-Request": "true"}


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db) -> Customer:
    c = Customer(
        company_name=f"HX Cust {_uniq()}", contact_name="Counter",
        email=f"hx{_uniq()}@test.local", pricing_tier="standard",
        is_active=True, is_tax_exempt=True, tax_rate=0.0,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _category(db, name: str) -> ProductCategory:
    cat = ProductCategory(name=f"{name}-{_uniq()}", parent_id=None, level=1, is_active=True)
    db.add(cat); db.commit(); db.refresh(cat)
    return cat


def _product(db, *, cost: float = 100.0, category_id: int | None = None) -> Product:
    p = Product(
        sku=f"HX-{_uniq():06d}", title="HX Part", description="HX Part",
        cost=cost, category_id=category_id,
        qty_on_hand=100, status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


# ════════════════════════════════════════════════════════════════════════════════
# CREATE — header → HTML partial; no header → JSON
# ════════════════════════════════════════════════════════════════════════════════

def test_create_with_hx_returns_panel_partial(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 12, "note": "counter deal"},
        headers=_HX,
    )
    assert r.status_code == 201, r.text
    # It's the panel partial — HTML, not JSON.
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert 'id="pricing-deals-panel"' in body
    # The just-created rule is rendered: its SKU shows up in the scope label.
    assert prod.sku in body


def test_create_without_hx_returns_json(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 12},
    )
    assert r.status_code == 201, r.text
    assert "application/json" in r.headers["content-type"]
    payload = r.json()
    assert payload["ok"] is True
    assert payload["rule"]["rule_id"]
    # No HTML leaked into the JSON path.
    assert "pricing-deals-panel" not in r.text


# ════════════════════════════════════════════════════════════════════════════════
# UPDATE — header → HTML partial; no header → JSON
# ════════════════════════════════════════════════════════════════════════════════

def _make_rule(client, customer_id: int, product_id: int) -> int:
    r = client.post(
        f"/customers/{customer_id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(product_id),
              "price_method": "markup", "price_value": 10},
    )
    assert r.status_code == 201, r.text
    return r.json()["rule"]["rule_id"]


def test_update_with_hx_returns_panel_partial(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    rid = _make_rule(client, cust.id, prod.id)

    # EDIT mode re-sends the full payload (matches the customerPriceDeal factory;
    # update_rule re-validates every field).
    r = client.post(
        f"/customers/{cust.id}/price-rules/{rid}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 22},
        headers=_HX,
    )
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert 'id="pricing-deals-panel"' in r.text
    assert prod.sku in r.text


def test_update_without_hx_returns_json(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    rid = _make_rule(client, cust.id, prod.id)

    r = client.post(
        f"/customers/{cust.id}/price-rules/{rid}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 22},
    )
    assert r.status_code == 200, r.text
    assert "application/json" in r.headers["content-type"]
    assert r.json()["ok"] is True
    assert r.json()["rule"]["price_value"] == 22


# ════════════════════════════════════════════════════════════════════════════════
# DEACTIVATE — header → HTML partial (rule drops out); no header → JSON
# ════════════════════════════════════════════════════════════════════════════════

def test_deactivate_with_hx_returns_panel_partial(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    rid = _make_rule(client, cust.id, prod.id)

    r = client.post(f"/customers/{cust.id}/price-rules/{rid}/deactivate", headers=_HX)
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert 'id="pricing-deals-panel"' in r.text
    # The deactivated rule no longer appears in the active panel → SKU gone, and
    # the empty-state copy surfaces.
    assert prod.sku not in r.text
    assert "No deals yet" in r.text


def test_deactivate_without_hx_returns_json(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    rid = _make_rule(client, cust.id, prod.id)

    r = client.post(f"/customers/{cust.id}/price-rules/{rid}/deactivate")
    assert r.status_code == 200, r.text
    assert "application/json" in r.headers["content-type"]
    assert r.json()["ok"] is True
    assert r.json()["rule"]["is_active"] is False


# ════════════════════════════════════════════════════════════════════════════════
# BULK — header → HTML partial; no header → JSON
# ════════════════════════════════════════════════════════════════════════════════

def test_bulk_with_hx_returns_panel_partial(client, db):
    cust = _customer(db)
    cat = _category(db, "Turbos")

    r = client.post(
        f"/customers/{cust.id}/price-rules/bulk",
        json={"rules": [
            {"scope_type": "CATEGORY", "scope_ref": str(cat.id),
             "price_method": "margin", "price_value": 30},
        ]},
        headers=_HX,
    )
    assert r.status_code == 201, r.text
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert 'id="pricing-deals-panel"' in body
    # The category rule's human label shows the category path.
    assert cat.name in body


def test_bulk_without_hx_returns_json(client, db):
    cust = _customer(db)
    cat = _category(db, "Turbos")

    r = client.post(
        f"/customers/{cust.id}/price-rules/bulk",
        json={"rules": [
            {"scope_type": "CATEGORY", "scope_ref": str(cat.id),
             "price_method": "margin", "price_value": 30},
        ]},
    )
    assert r.status_code == 201, r.text
    assert "application/json" in r.headers["content-type"]
    payload = r.json()
    assert payload["ok"] is True
    assert len(payload["created"]) == 1


# ════════════════════════════════════════════════════════════════════════════════
# Validation error keeps the JSON 400 contract EVEN with the HX header — the
# client expects JSON to read .error for the toast on a bad form.
# ════════════════════════════════════════════════════════════════════════════════

def test_invalid_create_with_hx_still_returns_json_400(client, db):
    cust = _customer(db)

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        # margin >= 100 is invalid per the service.
        json={"scope_type": "CUSTOMER", "scope_ref": "",
              "price_method": "margin", "price_value": 150},
        headers=_HX,
    )
    assert r.status_code == 400, r.text
    assert "application/json" in r.headers["content-type"]
    assert r.json()["ok"] is False
    assert r.json().get("error")
