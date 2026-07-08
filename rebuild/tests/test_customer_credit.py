"""
tests/test_customer_credit.py
=============================
Phase 2 §4.5 / P2-D4, P2-D5 — credit visibility + warn (WARN ONLY, never blocks).

Covers would_exceed_credit (incl. the "0 = no limit" convention), credit_status
(available / over-limit / hold / warn / message), and that credit-hold is the
manual CustomerFlag (P2-D5). None of these ever raise.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import CustomerFlag, InvoiceStatus, LineType
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.services.customer_service import CustomerService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


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
    c = Customer(company_name=f"Credit Co {next(_seq)}", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, cust, amount):
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=cust.id,
                  status=InvoiceStatus.OPEN, is_taxable=False,
                  created_at=datetime(datetime.utcnow().year, 1, 2))
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1,
                                 unit_price=amount, is_taxable=False))
    db.add(inv); db.commit()
    return inv


def test_no_limit_never_exceeds(db):
    c = _customer(db, credit_limit=0.0)   # 0 = no limit enforced
    _open_invoice(db, c, 5000.0)
    svc = CustomerService(db)
    assert svc.would_exceed_credit(c, 999_999.0) is False
    status = svc.credit_status(c, prospective_amount=999_999.0)
    assert status["available_credit"] is None
    assert status["over_limit"] is False
    assert status["warn"] is False


def test_within_limit_no_warn(db):
    c = _customer(db, credit_limit=1000.0)
    _open_invoice(db, c, 400.0)
    svc = CustomerService(db)
    assert svc.would_exceed_credit(c, 500.0) is False   # 400 + 500 = 900 <= 1000
    status = svc.credit_status(c, prospective_amount=500.0)
    assert status["open_ar"] == 400.0
    assert status["available_credit"] == 600.0
    assert status["warn"] is False
    assert status["message"] == ""


def test_exceeding_limit_warns(db):
    c = _customer(db, credit_limit=1000.0)
    _open_invoice(db, c, 800.0)
    svc = CustomerService(db)
    assert svc.would_exceed_credit(c, 300.0) is True    # 800 + 300 = 1100 > 1000
    status = svc.credit_status(c, prospective_amount=300.0)
    assert status["over_limit"] is True
    assert status["warn"] is True
    assert "credit limit" in status["message"]


def test_credit_hold_is_a_flag_and_warns(db):
    c = _customer(db, credit_limit=0.0)   # even with no limit, hold still warns
    svc = CustomerService(db)
    svc.set_flag(c, CustomerFlag.CREDIT_HOLD, True)
    db.commit()
    status = svc.credit_status(c)
    assert status["on_hold"] is True
    assert status["warn"] is True
    assert "Credit Hold" in status["message"]


def test_credit_status_never_raises_and_is_advisory(db):
    # P2-D4 — display + warn only. The service exposes the warning; it never
    # raises or signals a block.
    c = _customer(db, credit_limit=100.0)
    _open_invoice(db, c, 1000.0)
    svc = CustomerService(db)
    status = svc.credit_status(c, prospective_amount=50.0)
    assert status["over_limit"] is True
    assert status["available_credit"] == -900.0   # negative available is surfaced, not clamped
