"""
tests/test_phase2_seams.py
==========================
Guards for the Phase-2 backend seams (@9f50216): quote-workspace customer
intelligence, the dashboard top_customers / open_followups context, and the
prepared-by print helper.

Two seams are LANDED-BUT-INCOMPLETE — pinned with xfail + the exact fix so they
flip to a hard guard the moment they're corrected/wired:

  • Quote-ws ctx reads m.get("outstanding_cores")/("last_purchase") off
    CustomerMetricsService.metrics_for, whose keys are actually
    "outstanding_core_credits"/"last_invoice_date" → cores=0 / last_purchase=None
    ALWAYS. The authoritative values live on InvoiceMetricsService.
  • get_prepared_by() landed but no print template renders it yet (placement
    ruled @7759579, UI wiring pending).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_phase2_seams.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.constants import (
    CoreDirection, CoreStatus, InvoiceStatus, LineType, QuoteFollowupStatus,
    QuoteOutcome, QuoteStatus, UserRole,
)
from app.main import app
from app.models.core import CoreCharge
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.quote import Quote
from app.models.user import User
from app.routers.dashboard import dashboard
from app.routers.quotes import _quote_customer_ctx
from app.services.customer_metrics_service import CustomerMetricsService
from app.services.document_render import get_prepared_by
from app.services.invoice_metrics_service import InvoiceMetricsService
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    SessionLocal = activate(fresh_engine())
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(db):
    return TestClient(app, raise_server_exceptions=False)


# ── seeding ──────────────────────────────────────────────────────────────────

def _customer(db, **kw) -> Customer:
    kw.setdefault("company_name", f"Seam Co {next(_seq)}")
    c = Customer(contact_name="QA", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id, amount) -> Invoice:
    inv = Invoice(invoice_number=f"INV-SM-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _quote(db, customer_id, **kw) -> Quote:
    q = Quote(quote_number=f"Q-SM-{next(_seq):05d}", customer_id=customer_id,
              status=kw.pop("status", QuoteStatus.SENT), **kw)
    db.add(q); db.commit(); db.refresh(q)
    return q


def _open_core(db, customer_id):
    prod = Product(sku=f"SM-CORE-{next(_seq)}", title="core", cost=5.0)
    db.add(prod); db.commit(); db.refresh(prod)
    db.add(CoreCharge(customer_id=customer_id, product_id=prod.id,
                      direction=CoreDirection.CUSTOMER_OWES_RETURN,
                      customer_unit_charge=40.0, qty_charged=1, qty_returned=0,
                      status=CoreStatus.OPEN))
    db.commit()


def _dash_ctx(db) -> dict:
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                   "query_string": b"", "scheme": "http", "server": ("test", 80)})
    return dashboard(req, db).context


# ════════════════════════════════════════════════════════════════════════════
# Seam 3 — quote-workspace customer intelligence ctx
# ════════════════════════════════════════════════════════════════════════════

def test_quote_ctx_lifetime_matches_metrics_service(db):
    cust = _customer(db)
    _open_invoice(db, cust.id, 500.0)
    ctx = _quote_customer_ctx(db, _quote(db, cust.id))
    assert ctx["cust_lifetime_sales"] == \
        CustomerMetricsService(db).metrics_for(cust.id)["lifetime_sales"] == 500.0


# Seam-3 fixed: _quote_customer_ctx now sources cores (COUNT) and last purchase
# (DATE) from InvoiceMetricsService — hard guard.
def test_quote_ctx_cores_and_last_purchase_match_metrics_service(db):
    cust = _customer(db)
    _open_invoice(db, cust.id, 200.0)   # a real last purchase
    _open_core(db, cust.id)             # an open owed-back core
    ctx = _quote_customer_ctx(db, _quote(db, cust.id))
    inv = InvoiceMetricsService(db)
    assert inv._outstanding_cores(cust.id) >= 1 and inv._last_purchase(cust.id) is not None
    assert ctx["cust_outstanding_cores"] == inv._outstanding_cores(cust.id)
    assert ctx["cust_last_purchase"] == inv._last_purchase(cust.id)


# ════════════════════════════════════════════════════════════════════════════
# Seam 4 — dashboard top_customers + open_followups (tested via route context)
# ════════════════════════════════════════════════════════════════════════════

def test_dashboard_top_customers_ordered_by_sales_desc(db):
    a = _customer(db); _open_invoice(db, a.id, 300.0)
    b = _customer(db); _open_invoice(db, b.id, 100.0)
    c = _customer(db); _open_invoice(db, c.id, 200.0)

    top = _dash_ctx(db)["top_customers"]
    ids = [t["customer"].id for t in top]
    assert ids[:3] == [a.id, c.id, b.id]          # 300 > 200 > 100
    assert top[0]["lifetime_sales"] == 300.0


def test_dashboard_open_followups_grouped_by_status(db):
    cust = _customer(db)
    for st in (QuoteFollowupStatus.CALLED, QuoteFollowupStatus.CALLED,
               QuoteFollowupStatus.EMAILED):
        _quote(db, cust.id, outcome=QuoteOutcome.PENDING, follow_up_status=st)

    ctx = _dash_ctx(db)
    assert ctx["open_followups"] == {"called": 2, "emailed": 1}
    assert ctx["open_followups_total"] == 3


# ════════════════════════════════════════════════════════════════════════════
# Seam 5 — prepared-by print helper
# ════════════════════════════════════════════════════════════════════════════

def test_get_prepared_by_resolves_name_and_none(db):
    u = User(name="Pat Tester", username="pat", role=UserRole.SALES,
             password_hash="x")
    db.add(u); db.commit(); db.refresh(u)
    assert get_prepared_by(db, u.id) == "Pat Tester"
    assert get_prepared_by(db, None) == "JAKS"        # no user → fallback


# Fixed: get_prepared_by guards a non-existent user ROW (not just None id) —
# hard guard on the 'JAKS' fallback.
def test_get_prepared_by_unknown_user_falls_back(db):
    assert get_prepared_by(db, 999_999) == "JAKS"     # unknown id must not crash


# Seam-5 wired: invoice print renders the get_prepared_by() attribution — hard
# guard so it can't silently drop off the template again.
def test_prepared_by_renders_on_invoice_print(client, db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 100.0)
    html = client.get(f"/invoices/{inv.id}/print").text
    assert "Prepared by" in html
