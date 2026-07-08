"""
tests/test_h_print_doc_money_and_phone.py
==========================================
FULL_ERP_REVIEW_2026-07-04 HIGH — two customer-facing print-document defects:

1. Every printed quote/invoice/SO/warranty/RA showed the customer's (or vendor's,
   on POs) phone number TWICE: once as the trailing line of customer_addr_lines /
   vendor_addr_lines (the shared builder in app/services/document_render.py), and
   again on a separate dedicated line the template rendered right after it.
   invoices.py and quotes.py additionally carried their own DUPLICATE inline
   address-line builder instead of using the shared one. Fixed by consolidating
   onto the shared helper and removing the templates' redundant phone line.

2. All five print documents (quote/invoice/SO/PO/customer statement) formatted
   money with bare "%.2f" (Jinja's printf-style |format filter, or Python's %
   operator on statement_print.html) — a $12,500 line printed as "$12500.00".
   Fixed by switching to "{:,.2f}".format(x), matching the convention
   dashboard.html already used for KPI cards.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_h_print_doc_money_and_phone.py -q
"""
from __future__ import annotations

import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_PHONE = "406-555-0142"
_BIG_PRICE = 12500.0


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db):
    from app.models.customer import Customer
    c = Customer(
        company_name=f"Big Sky Diesel {uuid.uuid4().hex[:6]}", contact_name="",
        phone=_PHONE, address_line1="123 Main St", city="Bozeman", state="MT",
        zip_code="59715",
    )
    db.add(c)
    db.flush()
    return c


def test_invoice_print_shows_phone_once_and_comma_money(client, db):
    from app.models.invoice import Invoice, InvoiceLine
    c = _customer(db)
    inv = Invoice(invoice_number=f"INV-{uuid.uuid4().hex[:10]}", customer_id=c.id, status="open")
    db.add(inv)
    db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, description="Cylinder Head", qty=1,
                        unit_price=_BIG_PRICE, unit_cost=0.0, discount_pct=0.0, line_type="product"))
    db.commit()
    inv_id = inv.id
    db.close()

    r = client.get(f"/invoices/{inv_id}/print")
    assert r.status_code == 200
    assert r.text.count(_PHONE) == 1, "phone must appear exactly once on the printed invoice"
    assert "12,500.00" in r.text, "money must be comma-formatted"
    assert "12500.00" not in r.text.replace("12,500.00", ""), "no bare (un-commaed) big number should remain"


def test_quote_print_shows_phone_once_and_comma_money(client, db):
    from app.models.quote import Quote, QuoteLine
    c = _customer(db)
    q = Quote(quote_number=f"Q-{uuid.uuid4().hex[:10]}", customer_id=c.id, status="draft")
    db.add(q)
    db.flush()
    db.add(QuoteLine(quote_id=q.id, description="Cylinder Head", qty=1,
                      unit_price=_BIG_PRICE, unit_cost=0.0, discount_pct=0.0, line_type="product"))
    db.commit()
    q_id = q.id
    db.close()

    r = client.get(f"/quotes/{q_id}/print")
    assert r.status_code == 200
    assert r.text.count(_PHONE) == 1, "phone must appear exactly once on the printed quote"
    assert "12,500.00" in r.text, "money must be comma-formatted"


def test_sales_order_print_shows_phone_once(client, db):
    from app.models.quote import SalesOrder, SOLine
    c = _customer(db)
    so = SalesOrder(so_number=f"SO-{uuid.uuid4().hex[:10]}", customer_id=c.id, status="open")
    db.add(so)
    db.flush()
    db.add(SOLine(so_id=so.id, description="Cylinder Head", qty_ordered=1,
                  unit_price=_BIG_PRICE, unit_cost=0.0, discount_pct=0.0, line_type="product"))
    db.commit()
    so_id = so.id
    db.close()

    r = client.get(f"/sales-orders/{so_id}/print")
    assert r.status_code == 200
    assert r.text.count(_PHONE) == 1, "phone must appear exactly once on the printed sales order"
    assert "12,500.00" in r.text, "money must be comma-formatted"


def test_purchase_order_print_shows_vendor_phone_once(client, db):
    from app.models.vendor import Vendor
    from app.models.purchase_order import PurchaseOrder, POLine
    v = Vendor(name=f"PAI {uuid.uuid4().hex[:6]}", phone=_PHONE)
    db.add(v)
    db.flush()
    po = PurchaseOrder(po_number=f"PO-{uuid.uuid4().hex[:10]}", vendor_id=v.id, status="open")
    db.add(po)
    db.flush()
    db.add(POLine(po_id=po.id, description="Cylinder Head", qty_ordered=1,
                  unit_cost=_BIG_PRICE))
    db.commit()
    po_id = po.id
    db.close()

    r = client.get(f"/purchase-orders/{po_id}/print")
    assert r.status_code == 200
    assert r.text.count(_PHONE) == 1, "vendor phone must appear exactly once on the printed PO"
    assert "12,500.00" in r.text, "money must be comma-formatted"


def test_customer_statement_print_uses_comma_money(client, db):
    from app.models.invoice import Invoice, InvoiceLine
    c = _customer(db)
    inv = Invoice(invoice_number=f"INV-{uuid.uuid4().hex[:10]}", customer_id=c.id, status="open")
    db.add(inv)
    db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, description="Cylinder Head", qty=1,
                        unit_price=_BIG_PRICE, unit_cost=0.0, discount_pct=0.0, line_type="product"))
    db.commit()
    c_id = c.id
    db.close()

    r = client.get(f"/customers/{c_id}/statement/print")
    assert r.status_code == 200
    assert "12,500.00" in r.text, "statement money must be comma-formatted"
