"""
tests/test_credit_seam.py
=========================
Phase 2 §4.5 — credit_status seam on the SO + invoice workspaces.

The credit_status VALUES are unit-tested in test_customer_credit.py. These guard
the wiring: the SO and invoice workspace routes compute credit_status(customer,
doc_total) into their context and still render (no crash) for an over-limit
customer — the path the UI credit_warn macro renders on.
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
from app.constants import InvoiceStatus, LineType, SOStatus
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.quote import SalesOrder, SOLine
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


@pytest.fixture()
def over_limit_customer(db):
    """Customer with a low limit + an existing OPEN invoice that already exceeds
    it, so credit_status warns on any new doc."""
    c = Customer(company_name=f"Credit Seam Co {next(_seq)}", credit_limit=100.0)
    db.add(c); db.commit(); db.refresh(c)
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=c.id,
                  status=InvoiceStatus.OPEN, is_taxable=False,
                  created_at=datetime(datetime.utcnow().year, 1, 2))
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=200.0,
                                 is_taxable=False))
    db.add(inv); db.commit()
    return c


def test_invoice_workspace_renders_with_credit_status(client, db, over_limit_customer):
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=over_limit_customer.id,
                  status=InvoiceStatus.DRAFT, is_taxable=False)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=50.0,
                                 is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    r = client.get(f"/invoices/{inv.id}")
    assert r.status_code == 200


def test_so_workspace_renders_with_credit_status(client, db, over_limit_customer):
    so = SalesOrder(so_number=f"SO-{next(_seq):05d}", customer_id=over_limit_customer.id,
                    status=SOStatus.OPEN)
    so.lines.append(SOLine(qty_ordered=1, unit_price=300.0))
    db.add(so); db.commit(); db.refresh(so)
    r = client.get(f"/sales-orders/{so.id}")
    assert r.status_code == 200
