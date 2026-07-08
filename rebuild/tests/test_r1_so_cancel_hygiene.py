"""
tests/test_r1_so_cancel_hygiene.py
===================================
R1-10 — SO cancel drift fixes (sales_order_service.py):

  1. cancel_line() on a parent PRODUCT line cascade-cancels its auto-generated
     CORE_CHARGE child (created by _maybe_add_core_line) with the same freeze
     semantics, so a phantom core deposit no longer inflates the SO subtotal.
  2. Cancelling a BACKORDER-source line (cancel_line AND cancel_order) gives
     back the un-fulfilled remainder of product.qty_backordered, floored at 0.
     Fulfilled portions and already-allocated (committed) portions are NOT
     double-decremented.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r1_so_cancel_hygiene.py -q
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
    FulfillmentSource,
    LineType,
    SOLineSource,
    SOLineStatus,
    SOPaymentMode,
    SOStatus,
)
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.services.sales_order_service import SalesOrderService

_counter = itertools.count(1)
_UID = 1


# ── Builders ──────────────────────────────────────────────────────────────────

def _make_customer(db) -> Customer:
    c = Customer(company_name=f"R1-Cancel-{next(_counter)}", is_active=True)
    db.add(c); db.flush()
    return c


def _make_product(db, qty_on_hand=0, has_core=False,
                  customer_core=0.0, vendor_core=0.0) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"SKU-{n:06d}", title=f"Diesel Part {n}",
        qty_on_hand=qty_on_hand, qty_committed=0, cost=10.0,
        has_core=has_core,
        customer_core_charge=customer_core,
        vendor_core_charge=vendor_core,
    )
    db.add(p); db.flush()
    return p


def _make_so(db, customer, lines) -> SalesOrder:
    """Create through the service so core-child derivation and backorder
    accounting run for real."""
    svc = SalesOrderService(db, _UID)
    return svc.create_sales_order(
        customer_id=customer.id,
        payment_mode=SOPaymentMode.NONE,
        data={"lines": lines},
    )


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── 1. Core-child cascade on cancel_line ─────────────────────────────────────

def test_cancel_parent_cascades_auto_core_child(db):
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=10, has_core=True,
                         customer_core=50.0, vendor_core=40.0)
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 2, "unit_price": 100.0,
         "description": prod.title},
    ])
    db.expire_all()

    so_r = db.get(SalesOrder, so.id)
    parent = next(ln for ln in so_r.lines if ln.line_type == LineType.PRODUCT)
    child = next(ln for ln in so_r.lines if ln.parent_line_id == parent.id)
    assert child.line_type == LineType.CORE_CHARGE and child.is_auto_generated
    assert so_r.subtotal == 300.0  # 2×100 part + 2×50 core deposit

    SalesOrderService(db, _UID).cancel_line(parent.id)
    db.expire_all()

    parent_r = db.get(SOLine, parent.id)
    child_r = db.get(SOLine, child.id)
    assert parent_r.qty_ordered == 0, "parent frozen at qty_invoiced"
    assert child_r.qty_ordered == 0, "auto core child must be cancelled with its parent"
    assert db.get(SalesOrder, so.id).subtotal == 0.0, \
        "no phantom core deposit may survive the cancel"
    assert db.get(Product, prod.id).qty_committed == 0, "commitment released"


def test_cancel_parent_leaves_manual_child_alone(db):
    """Only auto-generated, locked-to-parent children cascade."""
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=10)
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 1, "unit_price": 100.0,
         "description": prod.title},
    ])
    db.expire_all()
    parent = db.query(SOLine).filter(SOLine.so_id == so.id).first()

    manual_child = SOLine(
        so_id=so.id, product_id=None, line_type=LineType.SHIPPING,
        description="Crating + freight", qty_ordered=3, qty_committed=0,
        qty_fulfilled=0, qty_invoiced=0, unit_price=95.0, unit_cost=0.0,
        discount_pct=0.0, core_charge=0.0,
        source=SOLineSource.STOCK, fulfillment_source=FulfillmentSource.STOCK,
        line_status=SOLineStatus.RESERVED_STOCK, sort_order=5,
        parent_line_id=parent.id, is_auto_generated=False,
        is_locked_to_parent=False,
    )
    db.add(manual_child); db.commit()

    SalesOrderService(db, _UID).cancel_line(parent.id)
    db.expire_all()

    assert db.get(SOLine, parent.id).qty_ordered == 0
    assert db.get(SOLine, manual_child.id).qty_ordered == 3, \
        "manually attached child must NOT be cascade-cancelled"


def test_cancel_partially_invoiced_parent_freezes_child_at_invoiced(db):
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=10, has_core=True, customer_core=25.0)
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 5, "unit_price": 100.0,
         "description": prod.title},
    ])
    db.expire_all()
    so_r = db.get(SalesOrder, so.id)
    parent = next(ln for ln in so_r.lines if ln.line_type == LineType.PRODUCT)
    child = next(ln for ln in so_r.lines if ln.parent_line_id == parent.id)

    # Simulate a prior partial fulfillment of 2 on both lines (the fulfill
    # path releases commitment as it invoices).
    parent.qty_fulfilled = 2; parent.qty_invoiced = 2
    parent.qty_committed = 3
    child.qty_fulfilled = 2; child.qty_invoiced = 2
    prod_r = db.get(Product, prod.id)
    prod_r.qty_committed = 3
    db.commit()

    SalesOrderService(db, _UID).cancel_line(parent.id)
    db.expire_all()

    assert db.get(SOLine, parent.id).qty_ordered == 2
    assert db.get(SOLine, child.id).qty_ordered == 2, \
        "core child freezes at its own qty_invoiced"
    # 5×100 + 5×25 = 625 before; only the invoiced 2×100 + 2×25 survive.
    assert db.get(SalesOrder, so.id).subtotal == 250.0


# ── 2. qty_backordered release on cancel ─────────────────────────────────────

def test_cancel_backorder_line_releases_demand(db):
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=0)  # forces BACKORDER source
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 5, "unit_price": 80.0,
         "description": prod.title},
    ])
    db.expire_all()
    line = db.query(SOLine).filter(SOLine.so_id == so.id).first()
    assert line.fulfillment_source == FulfillmentSource.BACKORDER
    assert db.get(Product, prod.id).qty_backordered == 5

    SalesOrderService(db, _UID).cancel_line(line.id)
    db.expire_all()

    assert db.get(Product, prod.id).qty_backordered == 0
    assert db.get(SOLine, line.id).qty_ordered == 0


def test_whole_so_cancel_releases_demand(db):
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=0)
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 3, "unit_price": 80.0,
         "description": prod.title},
        {"product_id": prod.id, "qty_ordered": 4, "unit_price": 80.0,
         "description": prod.title},
    ])
    db.expire_all()
    assert db.get(Product, prod.id).qty_backordered == 7

    SalesOrderService(db, _UID).cancel_order(so.id, reason="customer bailed")
    db.expire_all()

    assert db.get(Product, prod.id).qty_backordered == 0
    so_r = db.get(SalesOrder, so.id)
    assert so_r.status == SOStatus.CANCELLED
    assert all(ln.line_status == SOLineStatus.CANCELLED for ln in so_r.lines)


def test_backorder_release_floors_at_zero(db):
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=0)
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 5, "unit_price": 80.0,
         "description": prod.title},
    ])
    db.expire_all()
    # Simulate external drift: counter is lower than this line's demand.
    prod_r = db.get(Product, prod.id)
    prod_r.qty_backordered = 2
    db.commit()
    line = db.query(SOLine).filter(SOLine.so_id == so.id).first()

    SalesOrderService(db, _UID).cancel_line(line.id)
    db.expire_all()

    assert db.get(Product, prod.id).qty_backordered == 0, "never below zero"


def test_fulfilled_portion_not_double_released(db):
    """Only the un-fulfilled remainder comes off qty_backordered on cancel."""
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=0)
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 5, "unit_price": 80.0,
         "description": prod.title},
    ])
    db.expire_all()
    line = db.query(SOLine).filter(SOLine.so_id == so.id).first()
    line.qty_fulfilled = 2
    line.qty_invoiced = 2
    db.commit()

    SalesOrderService(db, _UID).cancel_line(line.id)
    db.expire_all()

    # Remainder = 5 ordered − 2 fulfilled = 3 released; the fulfilled 2 stay
    # (the fulfill path's own decrement is a separate, out-of-scope gap).
    assert db.get(Product, prod.id).qty_backordered == 2


def test_allocated_committed_portion_not_double_released(db):
    """Qty already allocated by PO receipt (line.qty_committed) was decremented
    from qty_backordered at allocation time — cancel must not subtract it again."""
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=0)
    so = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 4, "unit_price": 80.0,
         "description": prod.title},
    ])
    db.expire_all()
    line = db.query(SOLine).filter(SOLine.so_id == so.id).first()
    prod_r = db.get(Product, prod.id)
    # Mirror what _allocate_received_qty does for 1 unit, line stays BACKORDER.
    line.qty_committed = 1
    prod_r.qty_committed = 1
    prod_r.qty_on_hand = 1
    prod_r.qty_backordered = 3  # 4 − 1 allocated
    db.commit()

    SalesOrderService(db, _UID).cancel_line(line.id)
    db.expire_all()

    prod_r = db.get(Product, prod.id)
    assert prod_r.qty_backordered == 0, "released remainder = 4 − 0 fulfilled − 1 committed = 3"
    assert prod_r.qty_committed == 0, "the allocated unit's commitment is released too"


def test_so_cancel_after_line_cancel_no_double_release(db):
    """cancel_line then cancel_order on the same SO must not decrement twice —
    demand from OTHER SOs on the same product must survive."""
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=0)
    so_a = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 5, "unit_price": 80.0,
         "description": prod.title},
    ])
    so_b = _make_so(db, cust, [
        {"product_id": prod.id, "qty_ordered": 2, "unit_price": 80.0,
         "description": prod.title},
    ])
    db.expire_all()
    assert db.get(Product, prod.id).qty_backordered == 7

    line_a = db.query(SOLine).filter(SOLine.so_id == so_a.id).first()
    svc = SalesOrderService(db, _UID)
    svc.cancel_line(line_a.id)
    db.expire_all()
    assert db.get(Product, prod.id).qty_backordered == 2, "SO-A's 5 released once"

    svc.cancel_order(so_a.id, reason="dup")
    db.expire_all()
    assert db.get(Product, prod.id).qty_backordered == 2, \
        "whole-SO cancel must not re-release the already-cancelled line"
