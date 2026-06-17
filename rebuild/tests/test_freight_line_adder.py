"""
tests/test_freight_line_adder.py
================================
Contract guard for the shared line adder's "+ Freight" control
(app/templates/line_items/_line_adder.html → addFreight()).

The button POSTs the freight payload — ``{description: 'Freight', qty: 1,
line_type: 'freight'}`` — to the same add-line route the part search uses, on
Quote / SO / Invoice. These tests pin that contract at the ROUTE level (exactly
what the button hits) for all three sell documents:

  * a ``freight``-typed line is created (lands in the "Freight / Delivery"
    totals bucket, not "Fees / Adjustments"),
  * it is never discounted (FREIGHT ∈ NON_DISCOUNTABLE_LINE_TYPES), and
  * on the invoice it defaults non-taxable (FREIGHT ∈ NON_TAXABLE_LINE_TYPES)
    and its amount is summed into ``freight_subtotal``.

The PO workspace deliberately does NOT enable the control (PO freight is its own
field), so there is no PO case here.

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_freight_line_adder.py -v
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_SEQ = {"n": 0}

# What the addFreight() button sends (unit_price omitted → defaults $0, typed inline).
FREIGHT_PAYLOAD = {"description": "Freight", "qty": 1, "line_type": "freight"}


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _uid():
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db, *, tax_rate=0.0):
    from app.models.customer import Customer
    c = Customer(company_name=f"Freight Co {_uid()}", contact_name="QA",
                 email="freight@t.local", tax_rate=tax_rate,
                 is_tax_exempt=(tax_rate == 0.0))
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, cost=100.0, qty=5):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(
        sku=f"FRT-{_uid():04d}", title=f"Freight Part {_uid()}",
        cost=cost, markup_pct=50.0, qty_on_hand=qty,
        status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── Quote ─────────────────────────────────────────────────────────────────────

def test_quote_freight_button_creates_freight_line(client, db):
    from app.constants import LineType
    from app.models.quote import Quote
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    quote = QuoteService(db, None).create_quote(cust.id, data={})

    resp = client.post(f"/quotes/{quote.id}/lines", data=FREIGHT_PAYLOAD)
    assert resp.status_code == 200

    db.expire_all()
    quote = db.query(Quote).filter(Quote.id == quote.id).first()
    freight = [ln for ln in quote.lines if ln.line_type == LineType.FREIGHT]
    assert len(freight) == 1, (
        f"freight line not created — lines: "
        f"{[(ln.line_type, ln.description) for ln in quote.lines]}"
    )
    ln = freight[0]
    assert ln.description == "Freight"
    assert ln.discount_pct == 0.0      # FREIGHT ∈ NON_DISCOUNTABLE_LINE_TYPES
    assert ln.unit_price == 0.0        # typed inline afterward


# ── Sales Order ─────────────────────────────────────────────────────────────

def test_so_freight_button_creates_freight_line(client, db):
    from app.constants import LineType, SOPaymentMode
    from app.models.quote import SalesOrder
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db)
    qsvc = QuoteService(db, None)
    quote = qsvc.create_quote(cust.id, data={})
    qsvc.add_line(quote.id, prod.id, {"qty": 1, "unit_price": 150.0})
    so = qsvc.convert_to_sales_order(quote.id, payment_mode=SOPaymentMode.NONE)

    resp = client.post(f"/sales-orders/{so.id}/lines", data=FREIGHT_PAYLOAD)
    assert resp.status_code == 200

    db.expire_all()
    so = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
    freight = [ln for ln in so.lines if ln.line_type == LineType.FREIGHT]
    assert len(freight) == 1, (
        f"freight line not created — lines: "
        f"{[(ln.line_type, ln.description) for ln in so.lines]}"
    )
    assert freight[0].description == "Freight"
    assert freight[0].discount_pct == 0.0


# ── Invoice (non-taxable default + freight bucket) ──────────────────────────

def test_invoice_freight_button_is_nontaxable_and_buckets(client, db):
    from app.constants import LineType
    from app.invoice_totals import compute_invoice_totals
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    cust = _customer(db, tax_rate=8.0)   # taxable customer
    isvc = InvoiceService(db, 1)
    inv = isvc.create_invoice(cust.id, {}, lines=[
        {"line_type": LineType.PRODUCT, "description": "Reman injector",
         "qty": 1, "unit_price": 400.0},
    ])

    # Button payload + a typed amount (the inline edit) in one POST.
    resp = client.post(f"/invoices/{inv.id}/lines",
                       data={**FREIGHT_PAYLOAD, "unit_price": "75.00"})
    assert resp.status_code == 200

    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    freight = [ln for ln in inv.lines if ln.line_type == LineType.FREIGHT]
    assert len(freight) == 1
    ln = freight[0]
    assert ln.unit_price == 75.0
    assert ln.discount_pct == 0.0
    assert ln.is_taxable is False      # FREIGHT defaults non-taxable

    totals = compute_invoice_totals(inv)
    assert totals["freight_subtotal"] == 75.0, (
        f"freight not bucketed into freight_subtotal: {totals}"
    )
    # Freight sits in its own bucket, not lumped into parts or fees.
    assert totals["parts_subtotal"] == 400.0
    assert totals["other_subtotal"] == 0.0
