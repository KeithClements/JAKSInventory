"""
tests/test_customer_type_defaults.py
====================================
Phase 2 §4.1 / P2-D1, P2-D6 — Customer Type + type-driven default profiles.

Covers:
  • resolve_defaults returns a complete profile for every type;
  • unknown type → OTHER fallback;
  • a customer_type_defaults row OVERRIDES the in-code defaults;
  • apply_type_defaults stamps the type and fills only non-provided fields
    (explicit values win);
  • ensure_type_defaults_seeded is idempotent and never clobbers a customised row;
  • the /customers/type-defaults/{type} JSON contract.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import CustomerType, PaymentTerms, PricingTier
from app.models.customer import Customer, CustomerTypeDefault
from app.services.customer_service import CustomerService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


_PROFILE_FIELDS = {
    "payment_terms", "pricing_tier", "discount_pct", "credit_limit",
    "is_tax_exempt", "card_surcharge_pct", "apply_card_surcharge_default",
    "status_active", "same_as_billing_default", "customer_type",
}


def test_resolve_defaults_complete_for_every_type(db):
    svc = CustomerService(db)
    for ct in CustomerType:
        profile = svc.resolve_defaults(ct)
        assert _PROFILE_FIELDS <= set(profile), f"{ct} missing fields"
        assert profile["customer_type"] == ct


def test_fleet_and_dealer_profiles(db):
    svc = CustomerService(db)
    fleet = svc.resolve_defaults(CustomerType.FLEET)
    assert fleet["payment_terms"] == PaymentTerms.NET_30
    assert fleet["pricing_tier"] == PricingTier.FLEET
    dealer = svc.resolve_defaults(CustomerType.DEALER)
    assert dealer["is_tax_exempt"] is True       # resale
    assert dealer["pricing_tier"] == PricingTier.DEALER


def test_unknown_type_falls_back_to_other(db):
    svc = CustomerService(db)
    profile = svc.resolve_defaults("not_a_type")
    assert profile["customer_type"] == CustomerType.OTHER


def test_db_row_overrides_in_code_defaults(db):
    # The table is seeded at startup; the owner customises a row by editing it.
    # resolve_defaults must overlay that row over the in-code defaults.
    row = (db.query(CustomerTypeDefault)
           .filter(CustomerTypeDefault.customer_type == CustomerType.FLEET).one())
    row.payment_terms = PaymentTerms.NET_60
    row.discount_pct = 7.5
    row.credit_limit = 5000.0
    row.card_surcharge_pct = 2.0
    db.commit()
    profile = CustomerService(db).resolve_defaults(CustomerType.FLEET)
    assert profile["payment_terms"] == PaymentTerms.NET_60      # overridden
    assert profile["discount_pct"] == 7.5
    assert profile["credit_limit"] == 5000.0
    assert profile["card_surcharge_pct"] == 2.0


def test_apply_type_defaults_fills_only_unprovided(db):
    svc = CustomerService(db)
    c = Customer(company_name="Apply Co", payment_terms=PaymentTerms.COD)
    # Caller already set payment_terms explicitly → must NOT be overridden, even
    # though FLEET's default is NET_30.
    profile = svc.apply_type_defaults(c, CustomerType.FLEET, provided={"payment_terms"})
    assert c.customer_type == CustomerType.FLEET
    assert c.payment_terms == PaymentTerms.COD          # explicit value preserved
    assert c.pricing_tier == PricingTier.FLEET          # filled from default
    assert profile["customer_type"] == CustomerType.FLEET


def test_apply_type_defaults_status_maps_to_is_active(db):
    svc = CustomerService(db)
    c = Customer(company_name="Status Co")
    svc.apply_type_defaults(c, CustomerType.OTHER)
    assert c.is_active is True   # status_active default → is_active


def test_ensure_type_defaults_seeded_idempotent(db):
    svc = CustomerService(db)
    # Startup already seeded every type, so a call now inserts nothing.
    assert svc.ensure_type_defaults_seeded() == 0
    # Remove one type's row and confirm exactly that one is re-seeded, then stable.
    (db.query(CustomerTypeDefault)
       .filter(CustomerTypeDefault.customer_type == CustomerType.INTERNAL).delete())
    db.commit()
    assert svc.ensure_type_defaults_seeded() == 1
    assert svc.ensure_type_defaults_seeded() == 0
    assert db.query(CustomerTypeDefault).count() == len(list(CustomerType))


def test_seed_does_not_clobber_customized_row(db):
    # Customise the (already-seeded) MUNICIPALITY row, then re-run the seeder.
    row = (db.query(CustomerTypeDefault)
           .filter(CustomerTypeDefault.customer_type == CustomerType.MUNICIPALITY).one())
    row.discount_pct = 12.0
    db.commit()
    CustomerService(db).ensure_type_defaults_seeded()
    row2 = (db.query(CustomerTypeDefault)
            .filter(CustomerTypeDefault.customer_type == CustomerType.MUNICIPALITY).one())
    assert row2.discount_pct == 12.0                     # customised value untouched


def test_type_defaults_json_endpoint(client):
    r = client.get(f"/customers/type-defaults/{CustomerType.DEALER.value}")
    assert r.status_code == 200
    data = r.json()
    assert data["customer_type"] == CustomerType.DEALER
    assert data["is_tax_exempt"] is True


def test_type_defaults_json_endpoint_unknown_type(client):
    r = client.get("/customers/type-defaults/bogus")
    assert r.status_code == 200
    assert r.json()["customer_type"] == CustomerType.OTHER
