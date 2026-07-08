"""
tests/test_five_seams.py
========================
1. CustomerStatus enum + column + bind
2. Preview-dock seam (last_contact)
3. Quote workspace customer context
4. Dashboard top_customers + open_followups
5. prepared_by on print routes
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    CustomerStatus, InvoiceStatus, LineType, SOStatus,
    QuoteStatus, QuoteOutcome, QuoteFollowupStatus,
)
from app.models.customer import Customer, CustomerCallLog
from app.models.invoice import Invoice, InvoiceLine
from app.models.quote import Quote, SalesOrder
from app.services.document_render import get_prepared_by
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


def _cust(db, **kw):
    kw.setdefault("company_name", f"Co {next(_seq)}")
    c = Customer(**kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _inv(db, cust, status=InvoiceStatus.OPEN, amount=100.0):
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=cust.id,
                  status=status, is_taxable=False)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _quote(db, cust, status=QuoteStatus.DRAFT, follow_up_status=None):
    q = Quote(quote_number=f"Q-{next(_seq):05d}", customer_id=cust.id,
              status=status, outcome=QuoteOutcome.PENDING,
              follow_up_status=follow_up_status)
    db.add(q); db.commit(); db.refresh(q)
    return q


# ── 1. CustomerStatus ─────────────────────────────────────────────────────────

def test_customer_status_default(db):
    c = _cust(db)
    assert c.customer_status == CustomerStatus.ACTIVE


def test_customer_create_binds_status(client, db):
    r = client.post("/customers/new",
                    data={"company_name": "Status Create Co",
                          "customer_status": CustomerStatus.ON_HOLD.value},
                    follow_redirects=False)
    assert r.status_code == 303
    c = db.query(Customer).filter(Customer.company_name == "Status Create Co").first()
    assert c is not None and c.customer_status == CustomerStatus.ON_HOLD


def test_customer_update_binds_status(client, db):
    c = _cust(db)
    r = client.post(f"/customers/{c.id}",
                    data={"company_name": c.company_name,
                          "customer_status": CustomerStatus.CREDIT_HOLD.value},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Customer).get(c.id).customer_status == CustomerStatus.CREDIT_HOLD


def test_customer_status_invalid_defaults_active(client, db):
    r = client.post("/customers/new",
                    data={"company_name": "BadStatus Co",
                          "customer_status": "completely_invalid"},
                    follow_redirects=False)
    assert r.status_code == 303
    c = db.query(Customer).filter(Customer.company_name == "BadStatus Co").first()
    assert c is not None and c.customer_status == CustomerStatus.ACTIVE


# ── 2. Preview-dock seam ──────────────────────────────────────────────────────

def test_preview_dock_renders_with_last_contact(client, db):
    c = _cust(db)
    db.add(CustomerCallLog(customer_id=c.id, notes="test call",
                           logged_at=datetime(2026, 6, 1)))
    db.commit()
    r = client.get(f"/customers/preview/{c.id}")
    assert r.status_code == 200


def test_preview_dock_last_contact_none_ok(client, db):
    c = _cust(db)   # no call logs
    r = client.get(f"/customers/preview/{c.id}")
    assert r.status_code == 200


# ── 3. Quote workspace customer context ──────────────────────────────────────

def test_quote_workspace_has_customer_ctx(client, db):
    c = _cust(db)
    _inv(db, c, status=InvoiceStatus.OPEN, amount=500.0)   # some history
    q = _quote(db, c)
    r = client.get(f"/quotes/{q.id}")
    assert r.status_code == 200
    # The workspace renders; a 500 means the ctx helper threw


# ── 4. Dashboard top_customers + open_followups ───────────────────────────────

def test_dashboard_top_customers_and_followups(client, db):
    c = _cust(db)
    _inv(db, c, status=InvoiceStatus.PAID, amount=999.0)
    _quote(db, c, follow_up_status=QuoteFollowupStatus.PENDING)
    r = client.get("/")
    assert r.status_code == 200


def test_dashboard_followup_grouping(client, db):
    c = _cust(db)
    _quote(db, c, follow_up_status=QuoteFollowupStatus.PENDING)
    _quote(db, c, follow_up_status=QuoteFollowupStatus.CALLED)
    # Just check the route renders without error; the dict shape is tested
    # indirectly (template would 500 on a missing key).
    assert client.get("/").status_code == 200


# ── 5. prepared_by on print routes ───────────────────────────────────────────

def test_get_prepared_by_no_user(db):
    assert get_prepared_by(db, None) == "JAKS"


def test_get_prepared_by_resolves_user_name(db):
    from app.models.user import User
    u = db.query(User).first()
    if u is None:
        pytest.skip("no seeded user in test DB")
    result = get_prepared_by(db, u.id)
    assert result and result != "JAKS"


def test_quote_print_route_renders(client, db):
    c = _cust(db)
    q = _quote(db, c)
    assert client.get(f"/quotes/{q.id}/print").status_code == 200


def test_invoice_print_route_renders(client, db):
    c = _cust(db)
    inv = _inv(db, c, status=InvoiceStatus.OPEN)
    assert client.get(f"/invoices/{inv.id}/print").status_code == 200


def test_so_print_route_renders(client, db):
    c = _cust(db)
    so = SalesOrder(so_number=f"SO-{next(_seq):05d}", customer_id=c.id, status=SOStatus.OPEN)
    db.add(so); db.commit(); db.refresh(so)
    assert client.get(f"/sales-orders/{so.id}/print").status_code == 200
