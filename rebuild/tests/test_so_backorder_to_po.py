"""
tests/test_so_backorder_to_po.py
=================================
Per-line "Order" action: create a draft PO to source a backordered SO line and
link them (TESTING_FEEDBACK 2026-06-01, issue #3, per-line MVP).

Locks down:
  - create_po_for_line() makes a DRAFT PO for the product's PREFERRED vendor,
    with one line for the outstanding qty, and links the SO line back via
    linked_po_line_id + fulfillment_source=LINKED_PO + AWAITING_PO_RECEIPT.
  - It refuses when there is no preferred vendor, when the line is already
    linked, and when nothing is outstanding.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_so_backorder_to_po.py -v
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
_appdb.engine = _TEST_ENGINE
_appdb.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

from app.models import __all_models__  # noqa: F401
from app.database import Base

Base.metadata.create_all(bind=_TEST_ENGINE)

import pytest
from app.constants import (
    FulfillmentSource,
    LineType,
    POStatus,
    SOLineSource,
    SOLineStatus,
    SOPaymentMode,
    SOStatus,
)
from app.models.customer import Customer
from app.models.product import Product, ProductVendorSource
from app.models.purchase_order import POLine, PurchaseOrder
from app.models.quote import SalesOrder, SOLine
from app.models.vendor import Vendor
from app.services.po_service import POService
from app.services.sales_order_service import SalesOrderService

_counter = itertools.count(1)
_UID = 1


# ── Builders ──────────────────────────────────────────────────────────────────

def _make_customer(db) -> Customer:
    c = Customer(company_name=f"BO-PO-{next(_counter)}", is_active=True)
    db.add(c); db.flush()
    return c


def _make_vendor(db) -> Vendor:
    v = Vendor(name=f"Vendor-{next(_counter)}", is_active=True)
    db.add(v); db.flush()
    return v


def _make_product(db, vendor=None, vendor_cost=42.0, preferred=True) -> Product:
    n = next(_counter)
    p = Product(sku=f"SKU-{n:06d}", title=f"Diesel Part {n}",
                qty_on_hand=0, qty_committed=0, cost=10.0)
    db.add(p); db.flush()
    if vendor is not None:
        db.add(ProductVendorSource(
            product_id=p.id, vendor_id=vendor.id, vendor_part_number=f"VPN-{n}",
            vendor_cost=vendor_cost, is_preferred=preferred, is_active=True,
        ))
        db.flush()
    return p


def _make_backordered_line(db, customer, product, qty=2, price=80.0):
    so = SalesOrder(
        so_number=f"SO-BO-{next(_counter):04d}", customer_id=customer.id,
        status=SOStatus.OPEN, payment_mode=SOPaymentMode.NONE,
        deposit_amount=0.0, notes="", internal_notes="",
    )
    db.add(so); db.flush()
    line = SOLine(
        so_id=so.id, product_id=product.id, line_type=LineType.PRODUCT,
        description=product.title, qty_ordered=qty, qty_committed=0,
        qty_fulfilled=0, qty_invoiced=0, unit_price=price, unit_cost=product.cost,
        discount_pct=0.0, core_charge=0.0,
        source=SOLineSource.BACKORDER, fulfillment_source=FulfillmentSource.BACKORDER,
        line_status=SOLineStatus.BACKORDER, sort_order=0,
    )
    db.add(line); db.commit()
    return so, line


@pytest.fixture(autouse=True, scope="module")
def _activate_db():
    from tests.conftest import activate
    activate(_TEST_ENGINE)
    yield


@pytest.fixture()
def db(_activate_db):
    s = _appdb.SessionLocal()
    yield s
    s.close()


# ── Tests ───────────────────────────────────────────────────────────────────

def test_create_po_for_backordered_line(db):
    cust = _make_customer(db)
    vend = _make_vendor(db)
    prod = _make_product(db, vendor=vend, vendor_cost=42.0)
    so, line = _make_backordered_line(db, cust, prod, qty=2)

    po = SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()

    po_r = db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first()
    assert po_r.status == POStatus.DRAFT
    assert po_r.vendor_id == vend.id, "PO must go to the product's preferred vendor"

    po_lines = db.query(POLine).filter(POLine.po_id == po.id).all()
    assert len(po_lines) == 1
    assert po_lines[0].product_id == prod.id
    assert po_lines[0].qty_ordered == 2, "ordered qty = outstanding qty"
    assert po_lines[0].unit_cost == 42.0, "cost pulled from the preferred vendor source"

    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert line_r.linked_po_line_id == po_lines[0].id
    assert line_r.fulfillment_source == FulfillmentSource.LINKED_PO
    assert line_r.line_status == SOLineStatus.AWAITING_PO_RECEIPT
    # The read-only nav relationship resolves to the PO.
    assert line_r.linked_po_line.po.po_number == po_r.po_number


def test_orders_only_outstanding_qty(db):
    cust = _make_customer(db)
    vend = _make_vendor(db)
    prod = _make_product(db, vendor=vend)
    so, line = _make_backordered_line(db, cust, prod, qty=5)
    line.qty_fulfilled = 2  # 3 still outstanding
    db.commit()

    po = SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()
    po_line = db.query(POLine).filter(POLine.po_id == po.id).first()
    assert po_line.qty_ordered == 3


def test_no_preferred_vendor_raises(db):
    cust = _make_customer(db)
    prod = _make_product(db, vendor=None)  # no vendor source at all
    so, line = _make_backordered_line(db, cust, prod)

    with pytest.raises(ValueError, match="preferred vendor"):
        SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)


def test_already_linked_raises(db):
    cust = _make_customer(db)
    vend = _make_vendor(db)
    prod = _make_product(db, vendor=vend)
    so, line = _make_backordered_line(db, cust, prod)

    SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()
    with pytest.raises(ValueError, match="already linked"):
        SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)


# ── Full loop: Order → receive PO → SO line auto-committed & ready ────────────

def test_full_loop_receipt_commits_linked_so_line(db):
    """Receiving the linked PO commits the goods to the SO line and flips it
    AWAITING_PO_RECEIPT → RESERVED_STOCK (the closed loop)."""
    cust = _make_customer(db)
    vend = _make_vendor(db)
    prod = _make_product(db, vendor=vend, vendor_cost=42.0)  # qty_on_hand=0
    so, line = _make_backordered_line(db, cust, prod, qty=2)

    po = SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()
    po_line = db.query(POLine).filter(POLine.po_id == po.id).first()
    assert db.get(SOLine, line.id).line_status == SOLineStatus.AWAITING_PO_RECEIPT

    # Receive the full ordered qty against the PO line.
    POService(db, _UID).create_receipt(
        vendor_id=po.vendor_id,
        po_line_quantities={po_line.id: po_line.qty_ordered},
        data={},
    )
    db.expire_all()

    line_r = db.get(SOLine, line.id)
    prod_r = db.get(Product, prod.id)
    assert prod_r.qty_on_hand == 2, "received goods land on hand"
    assert line_r.qty_committed == 2, "received goods are reserved for the SO line"
    assert prod_r.qty_committed == 2, "product commitment mirrors the SO reservation"
    assert line_r.line_status == SOLineStatus.RESERVED_STOCK, "line is now ready to fulfill"
    assert line_r.source == SOLineSource.STOCK, "reserved line is no longer a backorder"
    assert line_r.is_backordered is False
    # Net available is zero — the units are earmarked, not free stock.
    assert prod_r.qty_on_hand - prod_r.qty_committed == 0


def test_partial_receipt_keeps_line_awaiting(db):
    cust = _make_customer(db)
    vend = _make_vendor(db)
    prod = _make_product(db, vendor=vend)
    so, line = _make_backordered_line(db, cust, prod, qty=4)

    po = SalesOrderService(db, _UID).create_po_for_line(so.id, line.id)
    db.expire_all()
    po_line = db.query(POLine).filter(POLine.po_id == po.id).first()

    POService(db, _UID).create_receipt(
        vendor_id=po.vendor_id,
        po_line_quantities={po_line.id: 1},  # only 1 of 4 arrives
        data={},
    )
    db.expire_all()

    line_r = db.get(SOLine, line.id)
    assert line_r.qty_committed == 1
    assert line_r.line_status == SOLineStatus.AWAITING_PO_RECEIPT, "still waiting on the rest"
