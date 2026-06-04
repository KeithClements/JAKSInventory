"""
tests/test_list_sort_ordering.py
================================
End-to-end ?sort=&direction= ROW-ORDER guards for the four list screens
(customers / invoices / quotes / payments), mirroring test_product_list_sort.

Why this exists (replaces a flake): test_list_seams.py originally asserted the
customer sort with `html.index(company_name)` under a MODULE-scoped engine —
that combination flaked intermittently in the full suite (the conftest
cross-module engine-repoint hazard + a name that can appear outside the table).
This file uses the robust test_product_list_sort recipe instead:
  • a FUNCTION-scoped fresh engine per test (only the rows this test seeds
    exist; activated immediately before the request → no cross-module repoint);
  • order asserted on the table ROW ids (dedup-preserving-order of the row
    detail-link), never on a substring position.

customers sort via Python `_CUST_SORT_KEYS`; invoices/quotes/payments via the
SQL `apply_sort` helper (whose whitelist/direction logic is unit-tested in
test_list_seams.py::test_apply_sort_whitelist_and_direction).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_list_sort_ordering.py -v
"""
from __future__ import annotations

import itertools
import re

import pytest
from fastapi.testclient import TestClient

from app.constants import InvoiceStatus, LineType, PaymentMethod, PaymentStatus, QuoteStatus
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.models.quote import Quote
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db_session():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    return TestClient(app, follow_redirects=False)


def _row_ids(html: str, entity: str) -> list[int]:
    """Table row order = dedup-preserving-order of the row detail-link ids.
    Robust to the same id appearing again in a per-row action/preview link."""
    out: list[int] = []
    for m in re.findall(rf"/{entity}/(\d+)\b", html):
        i = int(m)
        if i not in out:
            out.append(i)
    return out


def _customer(db, **kw) -> Customer:
    kw.setdefault("company_name", f"Co {next(_seq)}")
    c = Customer(**kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _invoice(db, customer_id, number) -> Invoice:
    inv = Invoice(invoice_number=number, customer_id=customer_id,
                  status=InvoiceStatus.OPEN, is_taxable=False)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=10.0,
                                 is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _quote(db, customer_id, number) -> Quote:
    q = Quote(quote_number=number, customer_id=customer_id, status=QuoteStatus.SENT)
    db.add(q); db.commit(); db.refresh(q)
    return q


def _payment(db, customer_id, amount) -> Payment:
    p = Payment(customer_id=customer_id, amount_received=amount,
                payment_method=PaymentMethod.CASH, status=PaymentStatus.APPLIED)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── Customers (Python _CUST_SORT_KEYS) ───────────────────────────────────────

def test_customer_sort_company_name_both_directions(client, db_session):
    a = _customer(db_session, company_name="AAA Sort Co")
    z = _customer(db_session, company_name="ZZZ Sort Co")
    asc = _row_ids(client.get("/customers/?sort=company_name&direction=asc").text, "customers")
    desc = _row_ids(client.get("/customers/?sort=company_name&direction=desc").text, "customers")
    assert asc.index(a.id) < asc.index(z.id)
    assert desc.index(z.id) < desc.index(a.id)


def test_customer_sort_by_account_number(client, db_session):
    lo = _customer(db_session, company_name="M Co", account_number="AR-0001")
    hi = _customer(db_session, company_name="N Co", account_number="AR-9999")
    asc = _row_ids(client.get("/customers/?sort=account_number&direction=asc").text, "customers")
    desc = _row_ids(client.get("/customers/?sort=account_number&direction=desc").text, "customers")
    assert asc.index(lo.id) < asc.index(hi.id)
    assert desc.index(hi.id) < desc.index(lo.id)


# ── Invoices / Quotes / Payments (SQL apply_sort) ────────────────────────────

def test_invoice_sort_by_number_both_directions(client, db_session):
    cust = _customer(db_session)
    a = _invoice(db_session, cust.id, "INV-AAA-0001")
    z = _invoice(db_session, cust.id, "INV-ZZZ-9999")
    asc = _row_ids(client.get("/invoices/?sort=number&direction=asc").text, "invoices")
    desc = _row_ids(client.get("/invoices/?sort=number&direction=desc").text, "invoices")
    assert asc.index(a.id) < asc.index(z.id)
    assert desc.index(z.id) < desc.index(a.id)


def test_quote_sort_by_number_both_directions(client, db_session):
    cust = _customer(db_session)
    a = _quote(db_session, cust.id, "Q-AAA-0001")
    z = _quote(db_session, cust.id, "Q-ZZZ-9999")
    asc = _row_ids(client.get("/quotes/?sort=number&direction=asc").text, "quotes")
    desc = _row_ids(client.get("/quotes/?sort=number&direction=desc").text, "quotes")
    assert asc.index(a.id) < asc.index(z.id)
    assert desc.index(z.id) < desc.index(a.id)


def test_payment_sort_by_amount_both_directions(client, db_session):
    cust = _customer(db_session)
    lo = _payment(db_session, cust.id, 10.0)
    hi = _payment(db_session, cust.id, 999.0)
    asc = _row_ids(client.get("/payments/?sort=amount&direction=asc").text, "payments")
    desc = _row_ids(client.get("/payments/?sort=amount&direction=desc").text, "payments")
    assert asc.index(lo.id) < asc.index(hi.id)
    assert desc.index(hi.id) < desc.index(lo.id)
