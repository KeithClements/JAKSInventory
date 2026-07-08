"""
tests/test_s21_bulk_statements.py
=================================
§21.10 — month-end bulk statement generation (one per customer with a balance).
"""
from __future__ import annotations

import datetime as _dt
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
from app.constants import InvoiceStatus, LineType
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.statement import CustomerStatement
from app.services.statement_service import StatementService

_ENGINE = fresh_engine()
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


_WHEN = _dt.datetime.utcnow() - _dt.timedelta(days=3)   # firmly inside the period


def _customer_with_open_invoice(db) -> Customer:
    c = Customer(company_name=f"Bulk Co {next(_seq)}")
    db.add(c); db.flush()
    inv = Invoice(invoice_number=f"INV-BLK-{next(_seq):05d}", customer_id=c.id,
                  status=InvoiceStatus.OPEN, tax_rate=0.0, tax_rate_snapshot=0.0,
                  tax_exempt_snapshot=True, created_at=_WHEN)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT, description="p",
                       qty=1, unit_price=250.0, unit_cost=150.0, is_taxable=False))
    db.commit()
    return c


def test_bulk_generate_persists_one_per_customer_with_balance(db):
    a = _customer_with_open_invoice(db)
    b = _customer_with_open_invoice(db)
    Customer.__table__  # noqa
    # A customer with NO open invoice should be skipped.
    none_cust = Customer(company_name=f"Zero {next(_seq)}")
    db.add(none_cust); db.commit()

    # UTC-based, wide period that brackets _WHEN regardless of local/UTC skew.
    start = (_WHEN - _dt.timedelta(days=7)).date()
    end = (_dt.datetime.utcnow() + _dt.timedelta(days=1)).date()
    summary = StatementService(db).generate_bulk_statements(start, end, generated_by_user_id=1)
    assert summary["generated"] >= 2
    assert not summary["errors"]
    # Statements exist for the two customers with balances.
    for cust in (a, b):
        assert db.query(CustomerStatement).filter_by(customer_id=cust.id).count() >= 1
    assert db.query(CustomerStatement).filter_by(customer_id=none_cust.id).count() == 0


def test_bulk_statements_route(client, db):
    _customer_with_open_invoice(db)
    r = client.post("/customers/statements/bulk-generate", follow_redirects=False)
    assert r.status_code == 303
    assert "/reports/ar-aging" in r.headers["location"]
    assert "ok=" in r.headers["location"]
