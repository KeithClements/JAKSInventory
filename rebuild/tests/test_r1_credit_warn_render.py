"""
tests/test_r1_credit_warn_render.py
===================================
R1-11 — credit-hold customers were quoted / SO'd / invoiced with ZERO on-screen
warning. The routes compute `CustomerService.credit_status()` (§4.5, WARN-ONLY)
but the workspace templates never rendered `credit_warn()` — the warning was
computed and discarded (the KNOWN GAP flagged in test_credit_status_seam.py).

This pins the RENDER: all three workspaces (quote / SO / invoice) now show the
credit_warn banner for an on-hold customer and stay clean for a normal one.
The on-hold banner text "Customer is on Credit Hold." comes only from
CustomerService.credit_status — the flag CHIP label is "Credit Hold" (no
sentence), so the assertion can't be satisfied by the chip alone.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r1_credit_warn_render.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    CustomerFlag, InvoiceStatus, LineType, QuoteOutcome, QuoteStatus,
    SOLineStatus, SOStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.quote import Quote, QuoteLine, SalesOrder, SOLine
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)

_HOLD_MSG = "Customer is on Credit Hold."


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


def _customer(db, on_hold=False, **kw) -> Customer:
    kw.setdefault("company_name", f"R1 Warn Co {next(_seq)}")
    if on_hold:
        kw.setdefault("flags", CustomerFlag.CREDIT_HOLD.value)
    c = Customer(contact_name="QA", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _quote(db, cust, amount=100.0) -> Quote:
    q = Quote(quote_number=f"Q-R1W-{next(_seq):05d}", customer_id=cust.id,
              status=QuoteStatus.DRAFT, outcome=QuoteOutcome.PENDING)
    q.lines.append(QuoteLine(line_type=LineType.PRODUCT, qty=1,
                             unit_price=amount, description="warn-render line"))
    db.add(q); db.commit(); db.refresh(q)
    return q


def _so(db, cust, amount=100.0) -> SalesOrder:
    so = SalesOrder(so_number=f"SO-R1W-{next(_seq):05d}", customer_id=cust.id,
                    status=SOStatus.OPEN)
    so.lines.append(SOLine(qty_ordered=1, unit_price=amount,
                           line_status=SOLineStatus.STOCK))
    db.add(so); db.commit(); db.refresh(so)
    return so


def _invoice(db, cust, amount=100.0, status=InvoiceStatus.DRAFT) -> Invoice:
    inv = Invoice(invoice_number=f"INV-R1W-{next(_seq):05d}", customer_id=cust.id,
                  status=status, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1,
                                 unit_price=amount, unit_cost=0.0,
                                 is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


# ── Quote workspace ───────────────────────────────────────────────────────────

def test_quote_workspace_on_hold_shows_warn_banner(client, db):
    q = _quote(db, _customer(db, on_hold=True))
    r = client.get(f"/quotes/{q.id}")
    assert r.status_code == 200
    assert _HOLD_MSG in r.text


def test_quote_workspace_normal_customer_no_banner(client, db):
    q = _quote(db, _customer(db))
    r = client.get(f"/quotes/{q.id}")
    assert r.status_code == 200
    assert _HOLD_MSG not in r.text


def test_quote_workspace_over_limit_shows_warn_banner(client, db):
    """Pins the quote seam's PROSPECTIVE amount: quote.subtotal (1500) over a
    $500 limit must trip the over-limit message even with zero open AR."""
    cust = _customer(db, credit_limit=500.0)
    q = _quote(db, cust, amount=1500.0)
    r = client.get(f"/quotes/{q.id}")
    assert r.status_code == 200
    assert "credit limit." in r.text


# ── SO workspace ──────────────────────────────────────────────────────────────

def test_so_workspace_on_hold_shows_warn_banner(client, db):
    so = _so(db, _customer(db, on_hold=True))
    r = client.get(f"/sales-orders/{so.id}")
    assert r.status_code == 200
    assert _HOLD_MSG in r.text


def test_so_workspace_normal_customer_no_banner(client, db):
    so = _so(db, _customer(db))
    r = client.get(f"/sales-orders/{so.id}")
    assert r.status_code == 200
    assert _HOLD_MSG not in r.text


# ── Invoice workspace ─────────────────────────────────────────────────────────

def test_invoice_workspace_on_hold_shows_warn_banner(client, db):
    inv = _invoice(db, _customer(db, on_hold=True))
    r = client.get(f"/invoices/{inv.id}")
    assert r.status_code == 200
    assert _HOLD_MSG in r.text


def test_invoice_workspace_normal_customer_no_banner(client, db):
    inv = _invoice(db, _customer(db))
    r = client.get(f"/invoices/{inv.id}")
    assert r.status_code == 200
    assert _HOLD_MSG not in r.text
