"""
tests/test_customer_flags.py
============================
Phase 2 §4.3 / P2-D2 — structured customer flags.

Covers:
  • flags_for merges stored flags with DERIVED tax-exempt / text-preferred so a
    chip can never contradict the canonical column;
  • set_flag routes TAX_EXEMPT → is_tax_exempt, TEXT_PREFERRED →
    preferred_contact_method, others → the CSV column;
  • set_stored_flags rewrites ONLY the CSV-backed flags (leaves tax/comms alone);
  • the flags CSV never stores derived/junk values;
  • is_on_credit_hold convenience.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import CustomerFlag, PreferredContactMethod
from app.models.customer import Customer
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


def _customer(db, **kw):
    c = Customer(company_name="Flag Co", contact_name="QA", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_new_customer_has_no_flags(db):
    c = _customer(db)
    assert CustomerService(db).flags_for(c) == []


def test_tax_exempt_is_derived(db):
    c = _customer(db, is_tax_exempt=True)
    assert CustomerFlag.TAX_EXEMPT in CustomerService(db).flags_for(c)
    # ...and it is NOT written to the CSV column (derived, not stored)
    assert "tax_exempt" not in (c.flags or "")


def test_text_preferred_is_derived(db):
    c = _customer(db, preferred_contact_method=PreferredContactMethod.TEXT)
    assert CustomerFlag.TEXT_PREFERRED in CustomerService(db).flags_for(c)


def test_set_and_clear_stored_flag(db):
    c = _customer(db)
    svc = CustomerService(db)
    svc.set_flag(c, CustomerFlag.REQUIRES_PO, True)
    svc.set_flag(c, CustomerFlag.WARRANTY_ESCALATION, True)
    db.commit()
    flags = svc.flags_for(c)
    assert CustomerFlag.REQUIRES_PO in flags and CustomerFlag.WARRANTY_ESCALATION in flags
    svc.set_flag(c, CustomerFlag.REQUIRES_PO, False)
    db.commit()
    assert CustomerFlag.REQUIRES_PO not in svc.flags_for(c)


def test_set_flag_routes_tax_exempt_to_canonical_column(db):
    c = _customer(db)
    svc = CustomerService(db)
    svc.set_flag(c, CustomerFlag.TAX_EXEMPT, True)
    db.commit()
    assert c.is_tax_exempt is True
    assert "tax_exempt" not in (c.flags or "")   # canonical, not CSV
    svc.set_flag(c, CustomerFlag.TAX_EXEMPT, False)
    db.commit()
    assert c.is_tax_exempt is False


def test_set_flag_routes_text_preferred_to_contact_method(db):
    c = _customer(db)
    svc = CustomerService(db)
    svc.set_flag(c, CustomerFlag.TEXT_PREFERRED, True)
    db.commit()
    assert c.preferred_contact_method == PreferredContactMethod.TEXT
    svc.set_flag(c, CustomerFlag.TEXT_PREFERRED, False)
    db.commit()
    assert c.preferred_contact_method == PreferredContactMethod.PHONE


def test_flags_for_is_stable_enum_order(db):
    c = _customer(db, is_tax_exempt=True)
    svc = CustomerService(db)
    # set them out of order
    svc.set_flag(c, CustomerFlag.WARRANTY_ESCALATION, True)
    svc.set_flag(c, CustomerFlag.REQUIRES_PO, True)
    db.commit()
    flags = svc.flags_for(c)
    order = list(CustomerFlag)
    assert flags == sorted(flags, key=order.index)
    # REQUIRES_PO precedes TAX_EXEMPT precedes WARRANTY_ESCALATION in the enum
    assert flags.index(CustomerFlag.REQUIRES_PO) < flags.index(CustomerFlag.WARRANTY_ESCALATION)


def test_set_stored_flags_ignores_derived(db):
    c = _customer(db)
    svc = CustomerService(db)
    # passing tax_exempt/text_preferred through the *stored* writer must NOT touch
    # the canonical columns, only the CSV-backed flags it recognises.
    svc.set_stored_flags(c, [
        CustomerFlag.REQUIRES_PO, CustomerFlag.TAX_EXEMPT, CustomerFlag.TEXT_PREFERRED,
    ])
    db.commit()
    assert c.is_tax_exempt is False
    assert c.preferred_contact_method != PreferredContactMethod.TEXT
    assert CustomerFlag.REQUIRES_PO in svc.flags_for(c)
    assert "tax_exempt" not in (c.flags or "")


def test_set_stored_flags_replaces_set(db):
    c = _customer(db)
    svc = CustomerService(db)
    svc.set_stored_flags(c, [CustomerFlag.REQUIRES_PO, CustomerFlag.CALL_FIRST])
    db.commit()
    svc.set_stored_flags(c, [CustomerFlag.CREDIT_HOLD])  # replaces, not merges
    db.commit()
    flags = svc.flags_for(c)
    assert CustomerFlag.CREDIT_HOLD in flags
    assert CustomerFlag.REQUIRES_PO not in flags
    assert CustomerFlag.CALL_FIRST not in flags


def test_flags_column_ignores_junk(db):
    c = _customer(db)
    c.flags = "requires_po,not_a_flag,,tax_exempt"  # junk + a derived value sneaked in
    db.commit()
    flags = CustomerService(db).flags_for(c)
    # only the valid stored flag survives parsing; junk + derived-in-CSV dropped
    assert CustomerFlag.REQUIRES_PO in flags
    assert all(f in CustomerFlag for f in flags)


def test_is_on_credit_hold(db):
    c = _customer(db)
    svc = CustomerService(db)
    assert svc.is_on_credit_hold(c) is False
    svc.set_flag(c, CustomerFlag.CREDIT_HOLD, True)
    db.commit()
    assert svc.is_on_credit_hold(c) is True
