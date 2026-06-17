"""
tests/test_o6_surcharge.py
==========================
O6 regression guards:
  1. customer.card_surcharge_pct pre-fills the invoice's cc_surcharge_pct.
  2. NULL customer surcharge → system default (cc_surcharge_pct setting).
  3. customer 0.0 explicitly disables the surcharge (not treated as NULL).
  4. §1.9e fix: apply_cc_surcharge can be un-selected (was gated on `if field
     in form:` so unchecked checkbox never cleared the flag).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import PaymentTerms
from app.models.customer import Customer
from app.models.setting import Setting
from app.services.invoice_service import InvoiceService
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
    c = Customer(company_name="Surcharge Co", contact_name="QA", email=f"s{__import__('uuid').uuid4().hex[:8]}@t.local",
                 payment_terms=PaymentTerms.COD, **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _set_sys_surcharge(db, pct: str):
    row = db.query(Setting).filter(Setting.key == "cc_surcharge_pct").first()
    if row:
        row.value = pct
    else:
        db.add(Setting(key="cc_surcharge_pct", value=pct, label="CC Surcharge %"))
    db.commit()


# ── O6 customer.card_surcharge_pct ───────────────────────────────────────────

def test_customer_surcharge_overrides_system_default(db):
    _set_sys_surcharge(db, "3.0")
    cust = _customer(db, card_surcharge_pct=5.0)
    inv = InvoiceService(db, None).create_invoice(cust.id, data={})
    db.refresh(inv)
    assert inv.cc_surcharge_pct == 5.0, f"expected 5.0, got {inv.cc_surcharge_pct}"


def test_customer_null_surcharge_uses_system_default(db):
    _set_sys_surcharge(db, "3.0")
    cust = _customer(db, card_surcharge_pct=None)
    inv = InvoiceService(db, None).create_invoice(cust.id, data={})
    db.refresh(inv)
    assert inv.cc_surcharge_pct == 3.0


def test_customer_zero_surcharge_is_real_zero_not_null(db):
    """0.0 means 'this customer never pays the surcharge', not 'use system default'."""
    _set_sys_surcharge(db, "3.0")
    cust = _customer(db, card_surcharge_pct=0.0)
    inv = InvoiceService(db, None).create_invoice(cust.id, data={})
    db.refresh(inv)
    assert inv.cc_surcharge_pct == 0.0


# ── §1.9e can't-unselect fix ─────────────────────────────────────────────────

def test_apply_cc_surcharge_can_be_disabled_via_header(client, db):
    """Un-checking apply_cc_surcharge must write False (not leave old True intact).

    The §1.9e bug: the handler gated the write on `if 'apply_cc_surcharge' in form:`.
    An unchecked checkbox sends nothing, so the condition was False and the field
    was never cleared.  Fix: always write from the form unconditionally.
    """
    cust = _customer(db)
    inv = InvoiceService(db, None).create_invoice(
        cust.id,
        data={"apply_cc_surcharge": True, "cc_surcharge_pct": 3.0},
    )
    db.commit(); db.refresh(inv)
    assert inv.apply_cc_surcharge is True  # confirm starting state

    # Simulate the unchecked-checkbox form: the hidden-"0" + checkbox-"1" pattern
    # sends only the hidden value ("0") when unchecked.
    r = client.post(f"/invoices/{inv.id}/header", data={
        "is_taxable": "0",
        "apply_cc_surcharge": "0",  # hidden sends 0; checkbox absent
    }, follow_redirects=False)
    assert r.status_code < 500, f"header POST returned {r.status_code}"

    db.expire_all()
    inv_fresh = db.query(inv.__class__).filter_by(id=inv.id).first()
    assert inv_fresh.apply_cc_surcharge is False, (
        "§1.9e: flag was not cleared when unchecked — fix didn't take effect"
    )
