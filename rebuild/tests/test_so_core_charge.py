"""
tests/test_so_core_charge.py
============================
Regression guard for Bug 1 — core charge dropped on quote→SO.

Before the fix, QuoteService.convert_to_sales_order excluded CORE_CHARGE quote
lines (correct) but never re-derived them on the SO side, so the core deposit
was silently lost from the SO total.  SalesOrderService also never auto-added
a core line when a product was added via add_line.

Fix: SalesOrderService._maybe_add_core_line auto-derives a discrete CORE_CHARGE
child SOLine whenever a top-level PRODUCT line is added whose product has
has_core=True and customer_core_charge > 0.  Both create_sales_order (the
convert path) and add_line (manual add) call this helper.

All three guards here are behaviour-level (line present + totals correct).
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
    c = Customer(company_name=f"Core Test Co {_uid()}", contact_name="QA",
                 email=f"c{_uid()}@t.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, cost=100.0, qty=5, has_core=False,
             cust_core=0.0, vend_core=0.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(
        sku=f"CORE-TEST-{_uid():04d}",
        title=f"Core Part {_uid()}",
        cost=cost, markup_pct=50.0,
        qty_on_hand=qty,
        status=ProductStatus.ACTIVE, is_active=True,
        has_core=has_core,
        customer_core_charge=cust_core,
        vendor_core_charge=vend_core,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── quote → SO carries core ───────────────────────────────────────────────────

def test_convert_quote_to_so_includes_core_charge_line(db):
    """
    Bug 1 regression: convert_to_sales_order must produce a discrete CORE_CHARGE
    child SOLine and include the core deposit in SalesOrder.subtotal.
    """
    from app.constants import LineType, SOPaymentMode
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db, cost=100.0, qty=5, has_core=True,
                    cust_core=35.0, vend_core=25.0)

    svc = QuoteService(db, None)
    quote = svc.create_quote(cust.id, data={})
    svc.add_line(quote.id, prod.id, {"qty": 1, "unit_price": 150.0})
    db.refresh(quote)

    # Quote itself should have PRODUCT + CORE_CHARGE
    assert any(ln.line_type == LineType.CORE_CHARGE for ln in quote.lines), (
        "Quote add_line should have created a CORE_CHARGE child line"
    )

    so = svc.convert_to_sales_order(quote.id, payment_mode=SOPaymentMode.NONE)
    db.refresh(so)

    so_types = [ln.line_type for ln in so.lines]
    assert LineType.CORE_CHARGE in so_types, (
        f"Bug 1: CORE_CHARGE missing from SO after quote→SO conversion. "
        f"Lines: {[(ln.line_type, ln.unit_price) for ln in so.lines]}"
    )

    core = next(ln for ln in so.lines if ln.line_type == LineType.CORE_CHARGE)
    assert core.unit_price == 35.0
    assert core.unit_cost == 25.0
    assert core.is_core_line is True
    assert core.is_auto_generated is True
    assert core.parent_line_id is not None

    product_line = next(ln for ln in so.lines if ln.line_type == LineType.PRODUCT)
    expected_subtotal = round(product_line.line_total + core.line_total, 2)
    assert abs(so.subtotal - expected_subtotal) < 0.01, (
        f"Bug 1: SO subtotal {so.subtotal} ≠ expected {expected_subtotal}. "
        f"Core deposit not counted in total."
    )


def test_convert_no_double_core_from_quote_and_so(db):
    """
    Bug 1 (double-count guard): the quote's CORE_CHARGE line is excluded from
    the SO line data; create_sales_order re-derives it exactly once.
    """
    from app.constants import LineType, SOPaymentMode
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db, cost=80.0, qty=5, has_core=True,
                    cust_core=30.0, vend_core=22.0)

    svc = QuoteService(db, None)
    quote = svc.create_quote(cust.id, data={})
    svc.add_line(quote.id, prod.id, {"qty": 2, "unit_price": 120.0})
    so = svc.convert_to_sales_order(quote.id, payment_mode=SOPaymentMode.NONE)
    db.refresh(so)

    core_lines = [ln for ln in so.lines if ln.line_type == LineType.CORE_CHARGE]
    assert len(core_lines) == 1, (
        f"Expected exactly 1 CORE_CHARGE line, got {len(core_lines)}: "
        f"{[(ln.unit_price, ln.is_auto_generated) for ln in core_lines]}"
    )
    assert core_lines[0].qty_ordered == 2   # matches the 2-unit product line


# ── manual add_line derives core ─────────────────────────────────────────────

def test_add_line_to_so_derives_core_line(db):
    """add_line on an existing SO must also auto-add the CORE_CHARGE child."""
    from app.constants import LineType, SOPaymentMode
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, cost=80.0, qty=10, has_core=True,
                    cust_core=30.0, vend_core=20.0)

    svc = SalesOrderService(db, None)
    so = svc.create_sales_order(
        customer_id=cust.id, payment_mode=SOPaymentMode.NONE,
        data={"lines": []},
    )
    svc.add_line(so.id, prod.id, {"qty_ordered": 2, "unit_price": 120.0})
    db.refresh(so)

    core_lines = [ln for ln in so.lines if ln.line_type == LineType.CORE_CHARGE]
    assert len(core_lines) == 1, (
        f"Bug 1 (manual): CORE_CHARGE missing after add_line. "
        f"Lines: {[(ln.line_type, ln.unit_price) for ln in so.lines]}"
    )
    assert core_lines[0].unit_price == 30.0
    assert core_lines[0].qty_ordered == 2
    assert so.subtotal == round(120.0 * 2 + 30.0 * 2, 2)


def test_multiple_core_products_each_get_a_core_line(db):
    """Two core-bearing products on the same SO → each gets its own core line."""
    from app.constants import LineType, SOPaymentMode
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    pa = _product(db, cost=50.0, qty=5, has_core=True, cust_core=20.0, vend_core=15.0)
    pb = _product(db, cost=60.0, qty=5, has_core=True, cust_core=25.0, vend_core=18.0)
    pc = _product(db, cost=30.0, qty=5, has_core=False)

    svc = SalesOrderService(db, None)
    so = svc.create_sales_order(
        customer_id=cust.id, payment_mode=SOPaymentMode.NONE,
        data={"lines": [
            {"product_id": pa.id, "line_type": LineType.PRODUCT,
             "qty_ordered": 1, "unit_price": 75.0, "unit_cost": 50.0},
            {"product_id": pb.id, "line_type": LineType.PRODUCT,
             "qty_ordered": 1, "unit_price": 90.0, "unit_cost": 60.0},
            {"product_id": pc.id, "line_type": LineType.PRODUCT,
             "qty_ordered": 1, "unit_price": 45.0, "unit_cost": 30.0},
        ]},
    )
    db.refresh(so)

    core_lines = [ln for ln in so.lines if ln.line_type == LineType.CORE_CHARGE]
    assert len(core_lines) == 2, (
        f"Expected 2 core lines, got {len(core_lines)}"
    )
    core_prices = {ln.unit_price for ln in core_lines}
    assert 20.0 in core_prices and 25.0 in core_prices

    # pc has no core — total lines = 3 products + 2 cores = 5
    assert len(so.lines) == 5

    # subtotal includes all 5
    expected = 75.0 + 20.0 + 90.0 + 25.0 + 45.0
    assert abs(so.subtotal - expected) < 0.01


def test_non_core_product_gets_no_core_line(db):
    """A product with has_core=False must NOT get a spurious CORE_CHARGE line."""
    from app.constants import LineType, SOPaymentMode
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, cost=50.0, qty=5, has_core=False)
    svc = SalesOrderService(db, None)
    so = svc.create_sales_order(
        customer_id=cust.id, payment_mode=SOPaymentMode.NONE,
        data={"lines": [{"product_id": prod.id, "line_type": LineType.PRODUCT,
                          "qty_ordered": 1, "unit_price": 75.0}]},
    )
    db.refresh(so)
    core_lines = [ln for ln in so.lines if ln.line_type == LineType.CORE_CHARGE]
    assert len(core_lines) == 0, f"Unexpected core line on non-core product: {core_lines}"
    assert len(so.lines) == 1
