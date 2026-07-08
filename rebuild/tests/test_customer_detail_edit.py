"""
tests/test_customer_detail_edit.py
==================================
C (next-7-days) — the customer detail edit form can now set customer_type and
the operational flags (Requires-PO / Call-First / Warranty-Escalation /
Credit-Hold), and editing the email to one another customer owns is blocked by
the C10 dedup guard instead of 500-ing.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()


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
        s.close()


_SEQ = {"n": 0}


def _uniq():
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db, **kw):
    from app.models.customer import Customer
    n = _uniq()
    c = Customer(company_name=f"Edit Co {n}", contact_name="QA",
                 email=kw.pop("email", f"edit{n}@test.local"), **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _base_form(c, **overrides):
    """Minimal valid edit-form payload (mirrors the detail edit form fields)."""
    data = {
        "company_name": c.company_name,
        "contact_name": c.contact_name or "",
        "account_number": c.account_number or "",
        "phone": c.phone or "",
        "email": c.email or "",
        "payment_terms": c.payment_terms,
        "pricing_tier": c.pricing_tier or "standard",
        "credit_limit": "0",
        "discount_pct": "0",
        "interest_rate": "0",
    }
    data.update(overrides)
    return data


def test_edit_sets_customer_type(client, db):
    from app.constants import CustomerType
    from app.models.customer import Customer
    c = _customer(db)
    assert c.customer_type == CustomerType.OTHER  # default

    resp = client.post(f"/customers/{c.id}",
                       data=_base_form(c, customer_type=CustomerType.FLEET),
                       follow_redirects=False)
    assert resp.status_code == 303
    db.expire_all()
    refetched = db.query(Customer).filter(Customer.id == c.id).first()
    assert refetched.customer_type == CustomerType.FLEET


def test_edit_sets_and_clears_flags(client, db):
    from app.constants import CustomerFlag
    from app.models.customer import Customer
    from app.services.customer_service import CustomerService
    c = _customer(db)

    # Set Requires-PO + Warranty-Escalation via the chip editor (repeated form
    # field → dict with a list value so httpx sends flags=a&flags=b).
    form = _base_form(c)
    form["flags_submitted"] = "1"
    form["flags"] = [str(CustomerFlag.REQUIRES_PO), str(CustomerFlag.WARRANTY_ESCALATION)]
    resp = client.post(f"/customers/{c.id}", data=form, follow_redirects=False)
    assert resp.status_code == 303
    db.expire_all()
    refetched = db.query(Customer).filter(Customer.id == c.id).first()
    stored = set(CustomerService._parse_stored(refetched.flags))
    assert CustomerFlag.REQUIRES_PO in stored
    assert CustomerFlag.WARRANTY_ESCALATION in stored

    # Submitting the editor with no boxes checked clears them
    cleared = _base_form(c)
    cleared["flags_submitted"] = "1"
    resp = client.post(f"/customers/{c.id}", data=cleared, follow_redirects=False)
    assert resp.status_code == 303
    db.expire_all()
    refetched = db.query(Customer).filter(Customer.id == c.id).first()
    stored = set(CustomerService._parse_stored(refetched.flags))
    assert CustomerFlag.REQUIRES_PO not in stored
    assert CustomerFlag.WARRANTY_ESCALATION not in stored


def test_edit_to_duplicate_email_is_blocked_not_500(client, db):
    from app.models.customer import Customer
    keeper = _customer(db, email="taken@fleet.test")
    other = _customer(db, email="mine@fleet.test")

    # Try to change `other` to keeper's email — must be rejected, not crash.
    resp = client.post(f"/customers/{other.id}",
                       data=_base_form(other, email="taken@fleet.test"),
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    db.expire_all()
    refetched = db.query(Customer).filter(Customer.id == other.id).first()
    assert refetched.email == "mine@fleet.test"  # unchanged


def test_edit_keeping_own_email_succeeds(client, db):
    """Saving without changing the email must not trip the dedup guard on self."""
    from app.models.customer import Customer
    c = _customer(db, email="self@fleet.test")
    resp = client.post(f"/customers/{c.id}",
                       data=_base_form(c, contact_name="Updated Name"),
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "saved=1" in resp.headers["location"]
    db.expire_all()
    refetched = db.query(Customer).filter(Customer.id == c.id).first()
    assert refetched.contact_name == "Updated Name"
