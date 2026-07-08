"""
tests/test_document_links.py
============================
Cross-document linking resolver (app.services.document_links.related_documents),
the single source of truth behind the "Linked" chip strip on every workspace.

Locks down each direction of the document graph and, in particular, the PO -> SO
reverse traversal recovered from SOLine.linked_po_line_id (a link that was never
stored on the PO side), plus re-ordering after the sourcing PO is cancelled.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_document_links.py -v
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401

import pytest
from app.constants import (
    FulfillmentSource, InvoiceStatus, LineType, POStatus,
    SOLineSource, SOLineStatus, SOPaymentMode, SOStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product, ProductVendorSource
from app.models.purchase_order import PurchaseOrder, VendorBill
from app.models.quote import Quote, SalesOrder, SOLine
from app.models.vendor import Vendor
from app.services.document_links import related_documents
from app.services.sales_order_service import SalesOrderService

_counter = itertools.count(1)
_UID = 1


# ── Builders ──────────────────────────────────────────────────────────────────

def _customer(db) -> Customer:
    c = Customer(company_name=f"DL-{next(_counter)}", is_active=True)
    db.add(c); db.flush()
    return c


def _vendor(db) -> Vendor:
    v = Vendor(name=f"V-{next(_counter)}", is_active=True)
    db.add(v); db.flush()
    return v


def _product(db, vendor) -> Product:
    n = next(_counter)
    p = Product(sku=f"SKU-{n:06d}", title=f"Part {n}", qty_on_hand=0, qty_committed=0, cost=10.0)
    db.add(p); db.flush()
    db.add(ProductVendorSource(
        product_id=p.id, vendor_id=vendor.id, vendor_part_number=f"VPN-{n}",
        vendor_cost=42.0, is_preferred=True, is_active=True,
    ))
    db.flush()
    return p


def _backordered_line(db, so, product, qty=2) -> SOLine:
    line = SOLine(
        so_id=so.id, product_id=product.id, line_type=LineType.PRODUCT,
        description=product.title, qty_ordered=qty, unit_price=80.0, unit_cost=10.0,
        source=SOLineSource.BACKORDER, fulfillment_source=FulfillmentSource.BACKORDER,
        line_status=SOLineStatus.BACKORDER,
    )
    db.add(line); db.commit()
    return line


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _numbers(links):
    return {ln.number for ln in links}


def _by_number(links, number):
    return next(ln for ln in links if ln.number == number)


# ── Quote → SO / Invoice (downstream) ──────────────────────────────────────────

def test_quote_links_to_converted_so_and_invoice(db):
    cust = _customer(db)
    q = Quote(quote_number=f"Q-{next(_counter):04d}", customer_id=cust.id)
    db.add(q); db.flush()
    so = SalesOrder(so_number=f"SO-{next(_counter):04d}", customer_id=cust.id,
                    quote_id=q.id, status=SOStatus.OPEN)
    db.add(so); db.flush()
    inv = Invoice(invoice_number=f"INV-{next(_counter):04d}", customer_id=cust.id,
                  status=InvoiceStatus.OPEN)
    db.add(inv); db.flush()
    q.converted_to_so_id = so.id
    q.converted_to_invoice_id = inv.id
    db.commit()

    links = related_documents(db, q)
    assert _numbers(links) == {so.so_number, inv.invoice_number}
    assert all(ln.group == "downstream" for ln in links)
    assert _by_number(links, so.so_number).url == f"/sales-orders/{so.id}"


# ── SO → Quote (up) + Invoice (down) + sourcing PO (down) ──────────────────────

def test_so_links_to_quote_invoice_and_sourcing_po(db):
    cust = _customer(db)
    vend = _vendor(db)
    prod = _product(db, vend)
    q = Quote(quote_number=f"Q-{next(_counter):04d}", customer_id=cust.id)
    db.add(q); db.flush()
    so = SalesOrder(so_number=f"SO-{next(_counter):04d}", customer_id=cust.id,
                    quote_id=q.id, status=SOStatus.OPEN, payment_mode=SOPaymentMode.NONE)
    db.add(so); db.flush()
    inv = Invoice(invoice_number=f"INV-{next(_counter):04d}", customer_id=cust.id,
                  sales_order_id=so.id, status=InvoiceStatus.OPEN)
    db.add(inv); db.flush()
    line = _backordered_line(db, so, prod, qty=2)

    po = SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()

    so_r = db.get(SalesOrder, so.id)
    links = related_documents(db, so_r)
    assert q.quote_number in _numbers(links)
    assert inv.invoice_number in _numbers(links)
    assert po.po_number in _numbers(links)
    # Source quote is upstream and sorts first; invoice + PO are downstream.
    assert _by_number(links, q.quote_number).group == "upstream"
    assert links[0].group == "upstream"


# ── PO → SO  (the reverse link this feature recovers) ──────────────────────────

def test_po_links_back_to_sourcing_so(db):
    cust = _customer(db)
    vend = _vendor(db)
    prod = _product(db, vend)
    so = SalesOrder(so_number=f"SO-{next(_counter):04d}", customer_id=cust.id,
                    status=SOStatus.OPEN, payment_mode=SOPaymentMode.NONE)
    db.add(so); db.flush()
    line = _backordered_line(db, so, prod, qty=3)

    po = SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()

    po_r = db.get(PurchaseOrder, po.id)
    links = related_documents(db, po_r)
    assert _numbers(links) == {so.so_number}
    link = _by_number(links, so.so_number)
    assert link.group == "upstream"
    assert link.relation == "Sourcing"
    assert link.kind == "SO"
    assert link.url == f"/sales-orders/{so.id}"


# ── PO → Vendor Bill (downstream; links to the PO's bills-card anchor) ─────────

def test_po_links_to_vendor_bills(db):
    cust = _customer(db)
    vend = _vendor(db)
    prod = _product(db, vend)
    so = SalesOrder(so_number=f"SO-{next(_counter):04d}", customer_id=cust.id,
                    status=SOStatus.OPEN, payment_mode=SOPaymentMode.NONE)
    db.add(so); db.flush()
    line = _backordered_line(db, so, prod, qty=2)
    po = SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()

    bill = VendorBill(po_id=po.id, vendor_id=vend.id, bill_number="VB-TEST-1",
                      status="discrepancy", total_amount=100.0)
    db.add(bill); db.commit()

    links = related_documents(db, db.get(PurchaseOrder, po.id))
    nums = _numbers(links)
    assert "VB-TEST-1" in nums          # the bill chip is present
    assert so.so_number in nums         # the sourcing SO is still there (upstream)
    bl = _by_number(links, "VB-TEST-1")
    assert bl.kind == "Bill"
    assert bl.group == "downstream"
    assert bl.tone == "danger"          # discrepancy → red
    assert bl.url == f"/purchase-orders/{po.id}#vendor-bills"


# ── Invoice → SO + Quote (upstream) ────────────────────────────────────────────

def test_invoice_links_to_sales_order_and_quote(db):
    cust = _customer(db)
    q = Quote(quote_number=f"Q-{next(_counter):04d}", customer_id=cust.id)
    db.add(q); db.flush()
    so = SalesOrder(so_number=f"SO-{next(_counter):04d}", customer_id=cust.id, status=SOStatus.OPEN)
    db.add(so); db.flush()
    inv = Invoice(invoice_number=f"INV-{next(_counter):04d}", customer_id=cust.id,
                  sales_order_id=so.id, quote_id=q.id, status=InvoiceStatus.OPEN)
    db.add(inv); db.commit()

    links = related_documents(db, inv)
    assert _numbers(links) == {so.so_number, q.quote_number}
    assert all(ln.group == "upstream" for ln in links)


# ── Empty neighbourhood resolves to [] ─────────────────────────────────────────

def test_standalone_so_has_no_links(db):
    cust = _customer(db)
    so = SalesOrder(so_number=f"SO-{next(_counter):04d}", customer_id=cust.id, status=SOStatus.OPEN)
    db.add(so); db.commit()
    assert related_documents(db, so) == []


# ── Re-order after the sourcing PO is cancelled ────────────────────────────────

def test_reorder_after_cancelled_po(db):
    cust = _customer(db)
    vend = _vendor(db)
    prod = _product(db, vend)
    so = SalesOrder(so_number=f"SO-{next(_counter):04d}", customer_id=cust.id,
                    status=SOStatus.OPEN, payment_mode=SOPaymentMode.NONE)
    db.add(so); db.flush()
    line = _backordered_line(db, so, prod, qty=2)

    svc = SalesOrderService(db, _UID)
    po1 = svc.create_po_for_line(so.id, line.id)
    db.expire_all()

    # Cancel the sourcing PO, then re-order: must succeed onto a fresh PO rather
    # than raise "already linked" (the stale-link fix).
    db.get(PurchaseOrder, po1.id).status = POStatus.CANCELLED
    db.commit()

    po2 = svc.create_po_for_line(so.id, line.id)
    db.expire_all()
    assert po2.id != po1.id

    line_r = db.get(SOLine, line.id)
    assert line_r.linked_po_line_id is not None
    assert line_r.linked_po_line.po_id == po2.id
    # The SO now points only at the live PO, not the cancelled one.
    assert _numbers(related_documents(db, db.get(SalesOrder, so.id))) == {po2.po_number}
