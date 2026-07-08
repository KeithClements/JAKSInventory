"""
tests/test_core_qty_cascade.py
==============================
Regression guard — when qty changes on a parent PRODUCT line, the linked
CORE_CHARGE child line must follow.

Before the fix, owner reported a quote with product qty=6 and a core child
qty=6; editing the product qty did not update the core line, leaving the
core deposit stuck at the original count.  Quote and SO both had the same
gap; invoice already cascaded via InvoiceService.update_line.
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


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"Cascade Co {_uid()}", contact_name="QA",
                 email=f"c{_uid()}@t.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, has_core=True, cust_core=250.0, vend_core=180.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(
        sku=f"CASCADE-{_uid():04d}",
        title=f"Cascade Part {_uid()}",
        cost=100.0, markup_pct=50.0,
        qty_on_hand=20,
        status=ProductStatus.ACTIVE, is_active=True,
        has_core=has_core,
        customer_core_charge=cust_core,
        vendor_core_charge=vend_core,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── Quote ─────────────────────────────────────────────────────────────────────

def test_quote_qty_change_cascades_to_core(db):
    """Changing parent product qty syncs the auto-core child qty."""
    from app.constants import LineType
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db)

    svc = QuoteService(db, None)
    q = svc.create_quote(cust.id, data={})
    svc.add_line(q.id, prod.id, {"qty": 6, "unit_price": 600.0})
    db.refresh(q)

    product_line = next(ln for ln in q.lines if ln.line_type == LineType.PRODUCT)
    core_line = next(ln for ln in q.lines if ln.line_type == LineType.CORE_CHARGE)
    assert product_line.qty == 6
    assert core_line.qty == 6

    # Edit parent qty 6 → 4
    line, cascaded = svc.update_line(product_line.id, {"qty": 4})
    db.refresh(core_line)
    assert cascaded is True, "Service should report a cascade so the route refreshes the tbody"
    assert line.qty == 4
    assert core_line.qty == 4, "Core child must follow the parent qty"


def test_quote_auto_core_marked_locked_to_parent(db):
    """New auto-cores must be flagged is_auto_generated + is_locked_to_parent
    so cascade/cancel/remove behaviour matches SO and Invoice."""
    from app.constants import LineType
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db)

    svc = QuoteService(db, None)
    q = svc.create_quote(cust.id, data={})
    svc.add_line(q.id, prod.id, {"qty": 2, "unit_price": 500.0})
    db.refresh(q)

    core = next(ln for ln in q.lines if ln.line_type == LineType.CORE_CHARGE)
    assert core.is_auto_generated is True
    assert core.is_locked_to_parent is True


def test_quote_no_cascade_when_qty_unchanged(db):
    """Editing description / price (no qty change) must not return cascaded=True."""
    from app.constants import LineType
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db)

    svc = QuoteService(db, None)
    q = svc.create_quote(cust.id, data={})
    svc.add_line(q.id, prod.id, {"qty": 3, "unit_price": 500.0})
    db.refresh(q)

    product_line = next(ln for ln in q.lines if ln.line_type == LineType.PRODUCT)
    _, cascaded = svc.update_line(product_line.id, {"unit_price": 550.0})
    assert cascaded is False


def test_quote_cascade_does_not_run_on_child_line_edit(db):
    """Editing the child core line itself must not loop back through cascade."""
    from app.constants import LineType
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db)

    svc = QuoteService(db, None)
    q = svc.create_quote(cust.id, data={})
    svc.add_line(q.id, prod.id, {"qty": 3, "unit_price": 500.0})
    db.refresh(q)

    core = next(ln for ln in q.lines if ln.line_type == LineType.CORE_CHARGE)
    # Editing the child directly — qty_changed is True but parent_line_id is set
    # so the cascade branch should NOT fire (children of children make no sense
    # here, and the side-effect-free return path matches the invoice behaviour).
    _, cascaded = svc.update_line(core.id, {"qty": 5})
    assert cascaded is False


# ── Sales Order ───────────────────────────────────────────────────────────────

def test_so_qty_change_cascades_to_core(db):
    """SOService.update_line must sync qty_ordered on the auto-core child."""
    from app.constants import LineType, SOPaymentMode
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db)

    svc = SalesOrderService(db, None)
    so = svc.create_sales_order(
        customer_id=cust.id, payment_mode=SOPaymentMode.NONE,
        data={"lines": []},
    )
    svc.add_line(so.id, prod.id, {"qty_ordered": 6, "unit_price": 600.0})
    db.refresh(so)

    product_line = next(ln for ln in so.lines if ln.line_type == LineType.PRODUCT)
    core_line = next(ln for ln in so.lines if ln.line_type == LineType.CORE_CHARGE)
    assert product_line.qty_ordered == 6
    assert core_line.qty_ordered == 6

    svc.update_line(product_line.id, {"qty_ordered": 4})
    db.refresh(core_line)
    assert core_line.qty_ordered == 4, "SO core child must follow parent qty_ordered"
