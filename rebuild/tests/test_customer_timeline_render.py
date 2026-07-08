"""
tests/test_customer_timeline_render.py
======================================
#8 — the unified customer Timeline, now WIRED into customer_detail (@bc7da6b):
the route passes ctx `timeline` = CRMService.get_unified_timeline(customer_id)
and customers/detail.html renders it in the Timeline tab via _timeline.html.

The SERVICE is guarded by test_list_seams.py. This pins the UI seam: that the
rendered customer-detail page actually SHOWS a quote, SO, invoice, and payment
entry in the Timeline tab. Assertions are scoped to that tab (the page's Quotes
and Invoices tabs link the same docs elsewhere) and check the entry links/badges,
not ordering, so they're robust.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_customer_timeline_render.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    InvoiceStatus, LineType, PaymentMethod, PaymentStatus, QuoteStatus, SOStatus,
)
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.models.quote import Quote, SalesOrder
from tests.conftest import activate, fresh_engine

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


def _timeline_scope(html: str) -> str:
    """The customer-detail Timeline tab section only (so the Quotes/Invoices tabs,
    which link the same docs, don't leak into the assertions)."""
    m = re.search(r"x-show=\"tab === 'timeline'\"(.*?)(?:x-show=\"tab ===|\Z)",
                  html, re.DOTALL)
    return m.group(1) if m else html


def test_customer_timeline_renders_quote_so_invoice_payment(client, db):
    cust = Customer(company_name=f"Timeline Co {next(_seq)}", contact_name="QA")
    db.add(cust); db.commit(); db.refresh(cust)

    t0 = datetime(2026, 3, 1, 9, 0, 0)
    q = Quote(quote_number=f"Q-TLR-{next(_seq):05d}", customer_id=cust.id,
              status=QuoteStatus.SENT); q.created_at = t0
    so = SalesOrder(so_number=f"SO-TLR-{next(_seq):05d}", customer_id=cust.id,
                    status=SOStatus.OPEN); so.created_at = t0 + timedelta(hours=1)
    inv = Invoice(invoice_number=f"INV-TLR-{next(_seq):05d}", customer_id=cust.id,
                  status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    inv.created_at = t0 + timedelta(hours=2)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=200.0,
                                 unit_cost=0.0, is_taxable=False))
    pmt = Payment(customer_id=cust.id, amount_received=42.00,
                  payment_method=PaymentMethod.CASH, status=PaymentStatus.APPLIED,
                  payment_date=t0 + timedelta(hours=3))
    db.add_all([q, so, inv, pmt]); db.commit(); db.refresh(so)

    tl = _timeline_scope(client.get(f"/customers/{cust.id}").text)

    # the three linked doc kinds appear as timeline entries…
    assert f"/quotes/{q.id}" in tl, "quote entry missing from timeline"
    assert f"/sales-orders/{so.id}" in tl, "sales-order entry missing from timeline"
    assert f"/invoices/{inv.id}" in tl, "invoice entry missing from timeline"
    # …and the (unlinked) payment shows via its badge + amount
    assert "Payment" in tl and "42.00" in tl, "payment entry missing from timeline"
    # all four document-kind badges are present in the feed
    for badge in ("Quote", "Sales Order", "Invoice", "Payment"):
        assert badge in tl, f"timeline missing the {badge!r} badge"


def test_customer_timeline_empty_state_renders(client, db):
    cust = Customer(company_name=f"Empty TL Co {next(_seq)}", contact_name="QA")
    db.add(cust); db.commit(); db.refresh(cust)
    # No docs/activity → the Timeline tab still renders its empty state, page is 200.
    r = client.get(f"/customers/{cust.id}")
    assert r.status_code == 200
    assert "No activity yet" in _timeline_scope(r.text)
