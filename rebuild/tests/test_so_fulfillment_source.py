"""
tests/test_so_fulfillment_source.py
====================================
Service-level regression gate for SO add-line fulfillment-source auto-derive
(TESTING_FEEDBACK §6 item a — "fulfillment source set correctly (stock vs
backorder vs linked PO)").

R7 in SalesOrderService._add_line_internal:
  - product.qty_available >= qty_ordered  →  STOCK
        qty_committed  += qty_ordered
        SO_COMMITTED inventory transaction written
  - product.qty_available < qty_ordered   →  BACKORDER
        qty_backordered += qty_ordered
        (no inventory commitment; demand tracked for purchasing)
  - explicit data["fulfillment_source"] present  →  that value (no auto-derive)

Previously unguarded — callers assumed the correct source but no test verified
the logic fired, the right counters incremented, and the inventory transaction
was written (or not) for each path.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_so_fulfillment_source.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import FulfillmentSource, SOStatus
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1
_counter = itertools.count(1)


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


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"FS Co {next(_counter)}", contact_name="QA",
                 email=f"fs{next(_counter)}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, qty_on_hand=0):
    from app.constants import ProductStatus
    from app.models.product import Product
    n = next(_counter)
    p = Product(
        sku=f"FS-{n:04d}", title=f"FS Part {n}", description="fs test",
        cost=10.0, markup_pct=50.0, qty_on_hand=qty_on_hand,
        qty_committed=0, qty_backordered=0,
        status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def _open_so(db, customer):
    from app.models.quote import SalesOrder
    so = SalesOrder(
        so_number=f"SO-FS-{next(_counter)}",
        customer_id=customer.id,
        status=SOStatus.OPEN,
    )
    db.add(so); db.commit(); db.refresh(so)
    return so


# ---------------------------------------------------------------------------
# 1. In-stock path: STOCK derive + qty_committed++ + SO_COMMITTED txn
# ---------------------------------------------------------------------------

def test_add_line_instock_derives_stock_and_commits(db):
    """qty_available >= qty_ordered → STOCK, qty_committed increases, txn written."""
    from app.constants import InventoryTxnType
    from app.models.inventory import InventoryTransaction
    from app.models.product import Product
    from app.models.quote import SOLine
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=10)      # 10 in stock
    so = _open_so(db, cust)

    committed_before = prod.qty_committed    # 0
    avail_before = prod.qty_available        # 10 - 0 = 10

    svc = SalesOrderService(db, _UID)
    line = svc.add_line(so.id, prod.id, {"qty_ordered": 3, "unit_price": 15.0})
    db.expire_all()

    # Line itself
    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert line_r.fulfillment_source == FulfillmentSource.STOCK, (
        f"Expected STOCK (10 in stock, 3 ordered); got {line_r.fulfillment_source!r}"
    )
    assert line_r.qty_committed == 3

    # Product counters
    prod_r = db.query(Product).filter(Product.id == prod.id).first()
    assert prod_r.qty_committed == committed_before + 3, (
        "qty_committed must increase by qty_ordered on STOCK commit"
    )
    # qty_available = qty_on_hand - qty_committed
    assert prod_r.qty_available == avail_before - 3

    # Inventory transaction
    txn = (
        db.query(InventoryTransaction)
        .filter(
            InventoryTransaction.product_id == prod.id,
            InventoryTransaction.transaction_type == InventoryTxnType.SO_COMMITTED,
        )
        .order_by(InventoryTransaction.id.desc())
        .first()
    )
    assert txn is not None, "STOCK commit must write an SO_COMMITTED inventory transaction"
    assert txn.qty_change == -3, (
        f"SO_COMMITTED qty_change should be -3 (reserved); got {txn.qty_change}"
    )


# ---------------------------------------------------------------------------
# 2. Out-of-stock path: BACKORDER derive + qty_backordered++ + NO txn
# ---------------------------------------------------------------------------

def test_add_line_oos_derives_backorder_and_increments_backordered(db):
    """qty_available < qty_ordered → BACKORDER, qty_backordered increases, no commit txn."""
    from app.constants import InventoryTxnType
    from app.models.inventory import InventoryTransaction
    from app.models.product import Product
    from app.models.quote import SOLine
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=0)       # OOS
    so = _open_so(db, cust)

    backordered_before = prod.qty_backordered   # 0
    committed_before = prod.qty_committed       # 0

    svc = SalesOrderService(db, _UID)
    line = svc.add_line(so.id, prod.id, {"qty_ordered": 5, "unit_price": 20.0})
    db.expire_all()

    # Line itself
    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert line_r.fulfillment_source == FulfillmentSource.BACKORDER, (
        f"Expected BACKORDER (0 in stock, 5 ordered); got {line_r.fulfillment_source!r}"
    )
    assert line_r.qty_committed == 0, (
        "BACKORDER line must NOT commit qty (commitment happens at PO receive)"
    )

    # Product counters
    prod_r = db.query(Product).filter(Product.id == prod.id).first()
    assert prod_r.qty_backordered == backordered_before + 5, (
        "qty_backordered must increase on BACKORDER"
    )
    assert prod_r.qty_committed == committed_before, (
        "qty_committed must NOT change on BACKORDER"
    )

    # No SO_COMMITTED transaction
    txn = (
        db.query(InventoryTransaction)
        .filter(
            InventoryTransaction.product_id == prod.id,
            InventoryTransaction.transaction_type == InventoryTxnType.SO_COMMITTED,
        )
        .order_by(InventoryTransaction.id.desc())
        .first()
    )
    assert txn is None, (
        "BACKORDER line must NOT write an SO_COMMITTED transaction; "
        "inventory is not committed until the goods arrive"
    )


# ---------------------------------------------------------------------------
# 3. Boundary: exactly qty_available → STOCK (not BACKORDER)
# ---------------------------------------------------------------------------

def test_add_line_exact_available_qty_is_stock(db):
    """qty_ordered == qty_available (boundary) → STOCK, not BACKORDER."""
    from app.models.quote import SOLine
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=7)
    so = _open_so(db, cust)

    line = SalesOrderService(db, _UID).add_line(
        so.id, prod.id, {"qty_ordered": 7, "unit_price": 15.0}
    )
    db.expire_all()

    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert line_r.fulfillment_source == FulfillmentSource.STOCK, (
        "qty_ordered == qty_available must derive STOCK (boundary)"
    )


# ---------------------------------------------------------------------------
# 4. Partial stock: 1 needed, 0 available → BACKORDER even with on-hand stock
#    when all on-hand is already committed
# ---------------------------------------------------------------------------

def test_add_line_all_committed_derives_backorder(db):
    """qty_available=0 (all committed) → BACKORDER even though qty_on_hand > 0."""
    from app.models.product import Product
    from app.models.quote import SOLine, SalesOrder
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=5)
    # Manually commit all 5 so qty_available = 0
    db.query(Product).filter(Product.id == prod.id).update({"qty_committed": 5})
    db.commit(); db.expire_all()

    prod_r = db.query(Product).filter(Product.id == prod.id).first()
    assert prod_r.qty_available == 0

    so = _open_so(db, cust)
    line = SalesOrderService(db, _UID).add_line(
        so.id, prod.id, {"qty_ordered": 1, "unit_price": 15.0}
    )
    db.expire_all()

    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert line_r.fulfillment_source == FulfillmentSource.BACKORDER, (
        "qty_available=0 must derive BACKORDER even when qty_on_hand>0"
    )


# ---------------------------------------------------------------------------
# 5. Explicit override: caller may force fulfillment_source (not auto-derived)
# ---------------------------------------------------------------------------

def test_add_line_explicit_source_overrides_auto_derive(db):
    """Explicit fulfillment_source in data must bypass auto-derive."""
    from app.models.quote import SOLine
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=10)   # would auto-derive STOCK
    so = _open_so(db, cust)

    # Force BACKORDER even though stock is available
    line = SalesOrderService(db, _UID).add_line(so.id, prod.id, {
        "qty_ordered": 2,
        "unit_price": 15.0,
        "fulfillment_source": FulfillmentSource.BACKORDER,
    })
    db.expire_all()

    line_r = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert line_r.fulfillment_source == FulfillmentSource.BACKORDER, (
        "Explicit fulfillment_source must override auto-derive"
    )
