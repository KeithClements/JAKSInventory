"""
tests/test_customer_surcharge_field.py
=======================================
D-7 (O6 completion): the customer edit form + update handler can now set the
per-customer card-surcharge default. Backend invoice creation already honored
`customer.card_surcharge_pct` (NULL → system default); this guards the UI seam:
  - a value persists,
  - blank clears it back to NULL (= use the system default),
  - explicit 0 is a real value (= no surcharge for this customer), not NULL.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.auth as auth
from app.main import app
from app.models.customer import Customer
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def client_db():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    auth.reset_secret_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, SessionLocal


def _make_customer(SessionLocal) -> int:
    db = SessionLocal()
    try:
        c = Customer(company_name="Surcharge Co", contact_name="QA")
        db.add(c); db.commit()
        return c.id
    finally:
        db.close()


def _surcharge(SessionLocal, cid):
    db = SessionLocal()
    try:
        return db.query(Customer).filter(Customer.id == cid).first().card_surcharge_pct
    finally:
        db.close()


def test_customer_update_sets_card_surcharge(client_db):
    client, SessionLocal = client_db
    cid = _make_customer(SessionLocal)

    r = client.post(f"/customers/{cid}",
                    data={"company_name": "Surcharge Co", "card_surcharge_pct": "2.5"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _surcharge(SessionLocal, cid) == 2.5


def test_blank_clears_to_null_system_default(client_db):
    client, SessionLocal = client_db
    cid = _make_customer(SessionLocal)
    client.post(f"/customers/{cid}",
                data={"company_name": "Surcharge Co", "card_surcharge_pct": "3.0"},
                follow_redirects=False)
    assert _surcharge(SessionLocal, cid) == 3.0

    # Blank → NULL (use the system default), not 0.
    r = client.post(f"/customers/{cid}",
                    data={"company_name": "Surcharge Co", "card_surcharge_pct": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _surcharge(SessionLocal, cid) is None


def test_explicit_zero_is_a_real_value_not_null(client_db):
    client, SessionLocal = client_db
    cid = _make_customer(SessionLocal)

    r = client.post(f"/customers/{cid}",
                    data={"company_name": "Surcharge Co", "card_surcharge_pct": "0"},
                    follow_redirects=False)
    assert r.status_code == 303
    # 0 means "this customer pays no surcharge" — distinct from NULL (= system default).
    assert _surcharge(SessionLocal, cid) == 0.0
