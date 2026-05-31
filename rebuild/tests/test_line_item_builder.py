"""
tests/test_line_item_builder.py
================================
Backend contract for the unified line-item builder (Quote / SO / Invoice / PO):

  1. normalize_part() — separator/case folding ("OK-1" == "ok1").
  2. SearchService — normalized SKU **and** OEM/cross-ref matching.
  3. GET /line-items/product-search — single JSON contract incl. core fields.
  4. Immediate add-on-select — POSTing only product_id + qty to each document's
     add-line route yields a complete line (description + price/cost auto-filled
     by the service layer).

Run (in-process TestClient, isolated in-memory engine — never touches jaks.db):
    .venv\\Scripts\\python.exe -m pytest tests/test_line_item_builder.py -v
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.main import app
from fastapi.testclient import TestClient

from app.utils import normalize_part

_ENGINE = fresh_engine()


@pytest.fixture(scope="module", autouse=True)
def _activate():
    activate(_ENGINE)


@pytest.fixture(scope="module")
def ids():
    """Seed products (with a cross-ref + a core product) and one draft of each
    document type.  REAL COLUMNS ONLY — selling_price/qty_available are @property."""
    db = _appdb.SessionLocal()

    from app.models.customer import Customer
    from app.models.vendor import Vendor
    from app.models.product import Product, CrossReference
    from app.constants import CrossRefType, SOStatus
    from app.models.purchase_order import PurchaseOrder, POStatus
    from app.models.quote import Quote, QuoteStatus, SalesOrder
    from app.models.invoice import Invoice, InvoiceStatus

    cust = Customer(company_name="LineItem Test Co", contact_name="QA", email="li@test.local")
    vendor = Vendor(name="LineItem Vendor")
    db.add_all([cust, vendor]); db.flush()

    # P1 — SKU stored WITH a dash; markup 50% → selling 15.00
    p1 = Product(sku="OK-1", title="Widget One", description="first widget",
                 cost=10.0, markup_pct=50.0, qty_on_hand=7, is_active=True)
    # P2 — found only via an OEM cross-ref that contains a space
    p2 = Product(sku="ZX-99", title="Gizmo Two", description="second part",
                 cost=20.0, markup_pct=25.0, qty_on_hand=3, is_active=True)
    # P3 — core-bearing product (exercises PO core fill + JSON core fields)
    p3 = Product(sku="CORE-1", title="Reman Unit", description="has a core",
                 cost=50.0, markup_pct=0.0, qty_on_hand=2, is_active=True,
                 has_core=True, vendor_core_charge=20.0, customer_core_charge=30.0)
    db.add_all([p1, p2, p3]); db.flush()

    db.add(CrossReference(product_id=p2.id, ref_type=CrossRefType.OEM,
                          ref_number="AC Delco 5000"))

    quote = Quote(quote_number="Q-LI-001", customer_id=cust.id, status=QuoteStatus.DRAFT)
    so = SalesOrder(so_number="SO-LI-001", customer_id=cust.id, status=SOStatus.OPEN)
    inv = Invoice(invoice_number="INV-LI-001", customer_id=cust.id, status=InvoiceStatus.DRAFT)
    po = PurchaseOrder(po_number="PO-LI-001", vendor_id=vendor.id, status=POStatus.DRAFT)
    db.add_all([quote, so, inv, po]); db.flush()

    db.commit()
    result = dict(customer_id=cust.id, vendor_id=vendor.id,
                  p1=p1.id, p2=p2.id, p3=p3.id,
                  quote_id=quote.id, so_id=so.id, invoice_id=inv.id, po_id=po.id)
    db.close()
    return result


@pytest.fixture(scope="module")
def client(ids):
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. normalize_part — pure helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("OK-1", "ok1"),
    ("ok 1", "ok1"),
    ("OK.1", "ok1"),
    ("ok/1", "ok1"),
    ("ok1",  "ok1"),
    ("  OK-1  ", "ok1"),
    ("", ""),
    (None, ""),
])
def test_normalize_part(raw, expected):
    assert normalize_part(raw) == expected


# ---------------------------------------------------------------------------
# 2. SearchService — normalized SKU + OEM/cross-ref matching
# ---------------------------------------------------------------------------

def test_search_sku_normalized(ids):
    """'ok1' must find P1 whose SKU is stored 'OK-1' (separator + case)."""
    from app.services.search_service import SearchService
    db = _appdb.SessionLocal()
    try:
        hits = SearchService(db).search_products("ok1")
        match = next((r for r in hits if r.product_id == ids["p1"]), None)
        assert match is not None, "P1 not found by normalized SKU 'ok1'"
        assert match.match_type == "part_number"
        # The dashed form must find it too.
        assert any(r.product_id == ids["p1"] for r in SearchService(db).search_products("OK-1"))
    finally:
        db.close()


def test_search_oem_xref_normalized(ids):
    """'acdelco5000' must find P2 via its OEM cross-ref 'AC Delco 5000'."""
    from app.services.search_service import SearchService
    db = _appdb.SessionLocal()
    try:
        hits = SearchService(db).search_products("acdelco5000")
        match = next((r for r in hits if r.product_id == ids["p2"]), None)
        assert match is not None, "P2 not found by normalized OEM cross-ref"
        assert match.match_type == "cross_ref"
        assert match.cross_ref_number == "AC Delco 5000"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. Single JSON endpoint — shape + core fields + min-length guard
# ---------------------------------------------------------------------------

_CONTRACT_KEYS = {
    "product_id", "sku", "title", "unit_cost", "suggested_sell",
    "qty_on_hand", "qty_available", "vendor_name", "match_type",
    "cross_ref_number", "last_sold_price", "last_sold_date",
    "has_core", "vendor_core_charge", "customer_core_charge",
}


def test_endpoint_short_query_returns_empty(client):
    assert client.get("/line-items/product-search").json() == []
    assert client.get("/line-items/product-search?q=o").json() == []


def test_endpoint_json_contract_and_core_fields(client, ids):
    rows = client.get("/line-items/product-search?q=core1").json()
    assert rows, "expected a hit for the core product"
    core = next((r for r in rows if r["product_id"] == ids["p3"]), None)
    assert core is not None
    assert _CONTRACT_KEYS.issubset(core.keys()), _CONTRACT_KEYS - core.keys()
    assert core["has_core"] is True
    assert core["vendor_core_charge"] == 20.0
    assert core["customer_core_charge"] == 30.0
    assert core["sku"] == "CORE-1"


# ---------------------------------------------------------------------------
# 4. Immediate add-on-select — product_id + qty only → complete line
# ---------------------------------------------------------------------------

def test_immediate_add_quote(client, ids):
    r = client.post(f"/quotes/{ids['quote_id']}/lines",
                    data={"product_id": str(ids["p1"]), "qty": "1"})
    assert r.status_code < 400, r.text
    from app.models.quote import QuoteLine
    db = _appdb.SessionLocal()
    try:
        line = (db.query(QuoteLine)
                .filter(QuoteLine.quote_id == ids["quote_id"],
                        QuoteLine.product_id == ids["p1"]).first())
        assert line is not None
        assert line.description == "Widget One"
        assert line.unit_price == 15.0      # selling_price (cost 10 + 50% markup)
        assert line.unit_cost == 10.0
    finally:
        db.close()


def test_immediate_add_invoice(client, ids):
    r = client.post(f"/invoices/{ids['invoice_id']}/lines",
                    data={"product_id": str(ids["p1"]), "qty": "1"})
    assert r.status_code < 400, r.text
    from app.models.invoice import InvoiceLine
    db = _appdb.SessionLocal()
    try:
        line = (db.query(InvoiceLine)
                .filter(InvoiceLine.invoice_id == ids["invoice_id"],
                        InvoiceLine.product_id == ids["p1"]).first())
        assert line is not None
        assert line.description == "Widget One"
        assert line.unit_price == 15.0
        assert line.unit_cost == 10.0
    finally:
        db.close()


def test_immediate_add_sales_order(client, ids):
    r = client.post(f"/sales-orders/{ids['so_id']}/lines",
                    data={"product_id": str(ids["p1"]), "qty": "1"})
    assert r.status_code < 400, r.text
    from app.models.quote import SOLine
    db = _appdb.SessionLocal()
    try:
        line = (db.query(SOLine)
                .filter(SOLine.so_id == ids["so_id"],
                        SOLine.product_id == ids["p1"]).first())
        assert line is not None
        assert line.description == "Widget One"
        assert line.unit_price == 15.0
        assert line.unit_cost == 10.0
        assert line.qty_ordered == 1        # canonical `qty` mapped to qty_ordered
    finally:
        db.close()


def test_immediate_add_po_fills_cost_and_core(client, ids):
    """PO is cost-only: description + unit_cost + core_charge auto-fill; no sell price."""
    r = client.post(f"/purchase-orders/{ids['po_id']}/lines",
                    data={"product_id": str(ids["p3"]), "qty": "1"})
    assert r.status_code < 400, r.text
    from app.models.purchase_order import POLine
    db = _appdb.SessionLocal()
    try:
        line = (db.query(POLine)
                .filter(POLine.po_id == ids["po_id"],
                        POLine.product_id == ids["p3"]).first())
        assert line is not None
        assert line.description == "Reman Unit"
        assert line.unit_cost == 50.0
        assert line.core_charge_per_unit == 20.0   # ← product.vendor_core_charge
        assert line.qty_ordered == 1
    finally:
        db.close()
