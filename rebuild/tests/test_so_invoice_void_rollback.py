"""
tests/test_so_invoice_void_rollback.py
=======================================
Regression gate: voiding an invoice that was created from a Sales Order must
roll the SO back to an un-invoiced state.

The bug this locks down:
  - fulfill_and_invoice() bumps SOLine.qty_invoiced and sets SO.status=INVOICED.
  - void_invoice() restored inventory but left the SO untouched, so:
      * SOLine.qty_invoiced stayed full  → qty_remaining == 0 → the SO could
        never be re-invoiced ("No lines selected for invoicing").
      * The SO list "Invoiced" badge (derived from qty_invoiced) stayed lit
        while the status tab (SO.status) showed it under Open — a contradiction.

After the fix, voiding an SO-linked invoice returns qty_invoiced/qty_fulfilled
to zero, restores SO.status to OPEN, and the SO can be fulfilled again.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_so_invoice_void_rollback.py -v
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# ── Patch BEFORE any app.* imports (mirrors test_workflow_so_invoice.py) ──────
import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401

import pytest
from app.constants import (
    FulfillmentSource,
    InvoiceStatus,
    LineType,
    SOLineStatus,
    SOPaymentMode,
    SOStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.services.invoice_service import InvoiceService
from app.services.sales_order_service import SalesOrderService

_counter = itertools.count(1)
_UID = 1


# ── Builders ──────────────────────────────────────────────────────────────────

def _make_customer(db) -> Customer:
    c = Customer(company_name=f"VoidRollback-{next(_counter)}", is_active=True)
    db.add(c)
    db.flush()
    return c


def _make_product(db, qty_on_hand: int = 10) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"SKU-{n:06d}",
        title=f"Widget {n}",
        qty_on_hand=qty_on_hand,
        qty_committed=0,
        cost=10.0,
    )
    db.add(p)
    db.flush()
    return p


def _make_so(db, customer: Customer) -> SalesOrder:
    n = next(_counter)
    so = SalesOrder(
        so_number=f"SO-VR-{n:04d}",
        customer_id=customer.id,
        status=SOStatus.OPEN,
        payment_mode=SOPaymentMode.NONE,
        deposit_amount=0.0,
        notes="",
        internal_notes="",
    )
    db.add(so)
    db.flush()
    return so


def _make_so_line(db, so, product, qty=3, price=50.0) -> SOLine:
    line = SOLine(
        so_id=so.id,
        product_id=product.id,
        line_type=LineType.PRODUCT,
        description=product.title,
        qty_ordered=qty,
        qty_committed=qty,
        qty_fulfilled=0,
        qty_invoiced=0,
        unit_price=price,
        unit_cost=product.cost,
        discount_pct=0.0,
        core_charge=0.0,
        source="stock",
        fulfillment_source=FulfillmentSource.STOCK,
        line_status=SOLineStatus.RESERVED_STOCK,
        sort_order=0,
    )
    product.qty_committed += qty
    db.add(line)
    db.flush()
    return line


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Tests ───────────────────────────────────────────────────────────────────

def test_void_rolls_so_back_to_open(db):
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=10)
    so = _make_so(db, cust)
    line = _make_so_line(db, so, prod, qty=3)
    db.commit()

    inv = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 3})
    db.expire_all()

    # Sanity: SO is fully invoiced after fulfillment.
    so_r = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert so_r.status == SOStatus.INVOICED
    assert line_r.qty_invoiced == 3
    assert line_r.qty_remaining == 0

    # Void the invoice.
    InvoiceService(db, _UID).void_invoice(inv.id, reason="test void rollback")
    db.expire_all()

    so_r = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()

    assert so_r.status == SOStatus.OPEN, "SO must return to OPEN once its only invoice is void"
    assert line_r.qty_invoiced == 0, "qty_invoiced must roll back to 0 on void"
    assert line_r.qty_fulfilled == 0, "qty_fulfilled must roll back to 0 on void"
    assert line_r.qty_remaining == 3, "the full ordered qty is invoiceable again"
    assert so_r.is_fully_invoiced is False


def test_void_restores_inventory_on_hand(db):
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=10)
    so = _make_so(db, cust)
    line = _make_so_line(db, so, prod, qty=4)
    db.commit()

    inv = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 4})
    db.expire(prod)
    assert prod.qty_on_hand == 6  # 10 - 4 sold

    InvoiceService(db, _UID).void_invoice(inv.id, reason="restock check")
    db.expire(prod)
    assert prod.qty_on_hand == 10, "voiding the sale must put the 4 units back on hand"


def test_so_can_be_reinvoiced_after_void(db):
    """The functional payoff: a voided SO-invoice does not strand the SO."""
    cust = _make_customer(db)
    prod = _make_product(db, qty_on_hand=10)
    so = _make_so(db, cust)
    line = _make_so_line(db, so, prod, qty=2, price=75.0)
    db.commit()

    inv1 = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 2})
    InvoiceService(db, _UID).void_invoice(inv1.id, reason="redo")
    db.expire_all()

    # Re-fulfill — previously failed with "No lines selected for invoicing".
    inv2 = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 2})
    db.expire_all()

    assert inv2.id != inv1.id
    assert inv2.status == InvoiceStatus.OPEN
    so_r = db.query(SalesOrder).filter(SalesOrder.id == so.id).first()
    assert so_r.status == SOStatus.INVOICED

    inv1_r = db.query(Invoice).filter(Invoice.id == inv1.id).first()
    assert inv1_r.status == InvoiceStatus.VOID, "the original invoice stays void"
