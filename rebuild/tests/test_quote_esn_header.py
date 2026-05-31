"""
tests/test_quote_esn_header.py
==============================
Regression: the Quote ESN / engine / customer-reference header fields were never
persisted (the columns existed only on SalesOrder/Invoice). This locks the fix:

  1. update_header persists esn/engine_manufacturer/engine_model +
     customer_po_number/customer_job_number on a Quote.
  2. POST /quotes/{id} (header) and the HTMX autosave both round-trip esn.
  3. convert_to_sales_order copies the fields into the new SO.
  4. quote → SO → invoice carries the ESN end-to-end.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import QuoteStatus, SOPaymentMode
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


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


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name="ESN Co", contact_name="QA", email="esn@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _quote(db, customer, number):
    from app.models.quote import Quote
    q = Quote(quote_number=number, customer_id=customer.id, status=QuoteStatus.DRAFT)
    db.add(q); db.commit(); db.refresh(q)
    return q


# ── 1. service-level persistence ──────────────────────────────────────────────

def test_update_header_persists_esn_and_engine(db):
    from app.services.quote_service import QuoteService
    cust = _customer(db)
    q = _quote(db, cust, "Q-ESN-1")

    QuoteService(db, None).update_header(q.id, {
        "esn": "ESN-12345",
        "engine_manufacturer": "Cummins",
        "engine_model": "ISX15",
        "customer_po_number": "PO-777",
        "customer_job_number": "JOB-9",
    })

    db.expire_all()
    from app.models.quote import Quote
    reloaded = db.query(Quote).filter(Quote.id == q.id).first()
    assert reloaded.esn == "ESN-12345"
    assert reloaded.engine_manufacturer == "Cummins"
    assert reloaded.engine_model == "ISX15"
    assert reloaded.customer_po_number == "PO-777"
    assert reloaded.customer_job_number == "JOB-9"


def test_blank_optional_refs_persist_as_none(db):
    from app.services.quote_service import QuoteService
    from app.models.quote import Quote
    cust = _customer(db)
    q = _quote(db, cust, "Q-ESN-blank")
    QuoteService(db, None).update_header(q.id, {"esn": None, "customer_po_number": None})
    db.expire_all()
    reloaded = db.query(Quote).filter(Quote.id == q.id).first()
    assert reloaded.esn is None
    assert reloaded.customer_po_number is None


# ── 2. HTTP header POST + autosave round-trip (DoD: POST → reload → persists) ──

def test_post_header_route_persists_esn(client, db):
    from app.models.quote import Quote
    cust = _customer(db)
    q = _quote(db, cust, "Q-ESN-http")
    r = client.post(f"/quotes/{q.id}", data={
        "notes": "", "internal_notes": "", "discount_pct": "0", "validity_days": "30",
        "esn": "ESN-HTTP-1", "engine_manufacturer": "Detroit", "engine_model": "DD15",
        "customer_po_number": "PO-HTTP", "customer_job_number": "JOB-HTTP",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    reloaded = db.query(Quote).filter(Quote.id == q.id).first()
    assert reloaded.esn == "ESN-HTTP-1"
    assert reloaded.engine_manufacturer == "Detroit"
    assert reloaded.customer_po_number == "PO-HTTP"


def test_autosave_route_persists_esn(client, db):
    from app.models.quote import Quote
    cust = _customer(db)
    q = _quote(db, cust, "Q-ESN-autosave")
    r = client.post(f"/quotes/{q.id}/autosave", data={
        "notes": "", "internal_notes": "", "discount_pct": "0", "validity_days": "30",
        "esn": "ESN-AUTO-2", "engine_manufacturer": "Cat", "engine_model": "C15",
    }, follow_redirects=False)
    assert r.status_code == 200
    assert "Saved" in r.text
    db.expire_all()
    reloaded = db.query(Quote).filter(Quote.id == q.id).first()
    assert reloaded.esn == "ESN-AUTO-2"
    assert reloaded.engine_model == "C15"


# ── 3 & 4. convert carries the fields quote → SO → invoice ────────────────────

def test_convert_to_so_copies_esn(db):
    from app.services.quote_service import QuoteService
    from app.models.quote import SalesOrder
    cust = _customer(db)
    q = _quote(db, cust, "Q-ESN-so")
    svc = QuoteService(db, None)
    svc.update_header(q.id, {
        "esn": "ESN-SO-9", "engine_manufacturer": "Cummins", "engine_model": "X15",
        "customer_po_number": "PO-SO", "customer_job_number": "JOB-SO",
    })
    so = svc.convert_to_sales_order(q.id, payment_mode=SOPaymentMode.NONE)

    db.expire_all()
    reloaded = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
    assert reloaded.esn == "ESN-SO-9"
    assert reloaded.engine_manufacturer == "Cummins"
    assert reloaded.engine_model == "X15"
    assert reloaded.customer_po_number == "PO-SO"
    assert reloaded.customer_job_number == "JOB-SO"


def test_quote_to_so_to_invoice_carries_esn(db):
    """End-to-end DoD: ESN survives quote → SO → invoice."""
    from app.services.quote_service import QuoteService
    from app.services.sales_order_service import SalesOrderService
    from app.models.invoice import Invoice
    from app.models.product import Product

    cust = _customer(db)
    # SO must have a fulfillable product line to invoice.
    prod = Product(sku="ESN-PART-1", title="ESN Part", cost=10.0, markup_pct=50.0,
                   qty_on_hand=10, is_active=True)
    db.add(prod); db.commit(); db.refresh(prod)

    q = _quote(db, cust, "Q-ESN-e2e")
    svc = QuoteService(db, None)
    svc.update_header(q.id, {"esn": "ESN-E2E-42", "engine_manufacturer": "Cummins"})
    # add an included product line so the SO has something to fulfill into an invoice
    svc.add_line(q.id, prod.id, {"qty": 1, "unit_price": 15.0})

    so = svc.convert_to_sales_order(q.id, payment_mode=SOPaymentMode.NONE)
    db.expire_all()
    from app.models.quote import SalesOrder
    so_row = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
    assert so_row.esn == "ESN-E2E-42"
    assert so_row.lines, "converted SO should have the product line"

    # Fulfill the SO into an invoice and confirm the ESN carried the final leg.
    so_svc = SalesOrderService(db, None)
    inv = so_svc.fulfill_and_invoice(so.id, {so_row.lines[0].id: 1})
    db.expire_all()
    inv_row = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv_row.esn == "ESN-E2E-42"
