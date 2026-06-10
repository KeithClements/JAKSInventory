"""
tests/test_r1_quote_convert_lines.py
====================================
Regression guard for R1-1 — quote→SO conversion silently dropped non-PRODUCT lines.

Before the fix, QuoteService.convert_to_sales_order filtered with
``ln.line_type == LineType.PRODUCT and ln.is_included`` so MISC / note /
WARRANTY / FREIGHT-type revenue vanished on the daily quote→SO path with no
warning (convert_to_invoice already carried them).

Fix: the SO filter now mirrors convert_to_invoice —
``ln.is_included and ln.line_type != LineType.CORE_CHARGE``.
  - CORE_CHARGE stays excluded: SalesOrderService re-derives the core child
    line (Bug 1 fix) — carrying the quote's core line would double-count.
  - is_included=False stays excluded: optional / upgrade-option lines the
    customer didn't opt into stay off the order.
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
    c = Customer(company_name=f"R1 Convert Co {_uid()}", contact_name="QA",
                 email="r1@t.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, cost=100.0, qty=5, has_core=False,
             cust_core=0.0, vend_core=0.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(
        sku=f"R1-CONV-{_uid():04d}",
        title=f"R1 Part {_uid()}",
        cost=cost, markup_pct=50.0,
        qty_on_hand=qty,
        status=ProductStatus.ACTIVE, is_active=True,
        has_core=has_core,
        customer_core_charge=cust_core,
        vendor_core_charge=vend_core,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── non-product lines carry on quote→SO ──────────────────────────────────────

def test_convert_carries_misc_note_warranty_freight_lines(db):
    """
    R1-1 regression: a quote with PRODUCT + MISC + note + WARRANTY + FREIGHT
    lines must convert to an SO that carries ALL of them at quoted prices,
    and the SO subtotal must include the non-product revenue.
    """
    from app.constants import LineType, SOPaymentMode
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db, cost=100.0, qty=5)

    svc = QuoteService(db, None)
    quote = svc.create_quote(cust.id, data={})
    svc.add_line(quote.id, prod.id, {"qty": 2, "unit_price": 150.0})
    svc.add_line(quote.id, None, {
        "line_type": LineType.MISC, "description": "Shop supplies",
        "qty": 1, "unit_price": 25.0,
    })
    # LineType has no NOTE member — quote lines store line_type as a free
    # string, so a "note" line must also survive conversion untouched.
    svc.add_line(quote.id, None, {
        "line_type": "note", "description": "Customer will pick up",
        "qty": 1, "unit_price": 0.0,
    })
    svc.add_line(quote.id, None, {
        "line_type": LineType.WARRANTY, "description": "2yr extended warranty",
        "qty": 1, "unit_price": 199.0,
    })
    svc.add_line(quote.id, None, {
        "line_type": LineType.FREIGHT, "description": "Freight",
        "qty": 1, "unit_price": 75.0,
    })

    so = svc.convert_to_sales_order(quote.id, payment_mode=SOPaymentMode.NONE)
    db.refresh(so)

    by_type = {ln.line_type: ln for ln in so.lines}
    for expected in (LineType.PRODUCT, LineType.MISC, "note",
                     LineType.WARRANTY, LineType.FREIGHT):
        assert expected in by_type, (
            f"R1-1: {expected!r} line dropped on quote→SO conversion. "
            f"SO lines: {[(ln.line_type, ln.unit_price) for ln in so.lines]}"
        )
    assert len(so.lines) == 5

    assert by_type[LineType.PRODUCT].qty_ordered == 2
    assert by_type[LineType.PRODUCT].unit_price == 150.0
    assert by_type[LineType.MISC].unit_price == 25.0
    assert by_type[LineType.MISC].description == "Shop supplies"
    assert by_type["note"].unit_price == 0.0
    assert by_type["note"].description == "Customer will pick up"
    assert by_type[LineType.WARRANTY].unit_price == 199.0
    assert by_type[LineType.FREIGHT].unit_price == 75.0

    # Subtotal must include the non-product revenue: 2×150 + 25 + 0 + 199 + 75
    expected_subtotal = 599.0
    assert abs(so.subtotal - expected_subtotal) < 0.01, (
        f"SO subtotal {so.subtotal} ≠ {expected_subtotal} — non-product "
        f"revenue missing from the SO total."
    )


# ── core child line still re-derived, never duplicated ───────────────────────

def test_core_line_rederived_once_not_duplicated(db):
    """
    The widened filter must STILL exclude the quote's CORE_CHARGE child line —
    SalesOrderService re-derives it exactly once (Bug 1 fix). Carrying the
    quote's core line through would produce two core lines / a doubled deposit.
    """
    from app.constants import LineType, SOPaymentMode
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db, cost=80.0, qty=5, has_core=True,
                    cust_core=35.0, vend_core=25.0)

    svc = QuoteService(db, None)
    quote = svc.create_quote(cust.id, data={})
    svc.add_line(quote.id, prod.id, {"qty": 1, "unit_price": 120.0})
    svc.add_line(quote.id, None, {
        "line_type": LineType.MISC, "description": "Gasket kit (misc)",
        "qty": 1, "unit_price": 10.0,
    })
    db.refresh(quote)
    assert any(ln.line_type == LineType.CORE_CHARGE for ln in quote.lines), (
        "Quote add_line should have created a CORE_CHARGE child line"
    )

    so = svc.convert_to_sales_order(quote.id, payment_mode=SOPaymentMode.NONE)
    db.refresh(so)

    core_lines = [ln for ln in so.lines if ln.line_type == LineType.CORE_CHARGE]
    assert len(core_lines) == 1, (
        f"Expected exactly 1 re-derived CORE_CHARGE line, got {len(core_lines)}: "
        f"{[(ln.unit_price, ln.is_auto_generated) for ln in core_lines]}"
    )
    assert core_lines[0].is_auto_generated is True  # SO-side derivation, not the quote's copy
    assert core_lines[0].unit_price == 35.0

    # PRODUCT + re-derived CORE + carried MISC = 3 lines; subtotal covers all.
    assert len(so.lines) == 3
    assert abs(so.subtotal - (120.0 + 35.0 + 10.0)) < 0.01


# ── excluded optional lines stay excluded ────────────────────────────────────

def test_optional_not_included_line_not_carried(db):
    """An optional add-on with is_included=False must NOT carry to the SO."""
    from app.constants import LineType, SOPaymentMode
    from app.services.quote_service import QuoteService

    cust = _customer(db)
    prod = _product(db, cost=100.0, qty=5)
    addon = _product(db, cost=20.0, qty=5)

    svc = QuoteService(db, None)
    quote = svc.create_quote(cust.id, data={})
    parent = svc.add_line(quote.id, prod.id, {"qty": 1, "unit_price": 150.0})[0]
    opt = svc.add_optional_line(parent.id, addon.id, {
        "description": "Optional install kit", "qty": 1, "unit_price": 49.0,
    })
    assert opt.is_included is False  # optionals default to excluded

    so = svc.convert_to_sales_order(quote.id, payment_mode=SOPaymentMode.NONE)
    db.refresh(so)

    assert len(so.lines) == 1, (
        f"Excluded optional line leaked onto the SO: "
        f"{[(ln.line_type, ln.description) for ln in so.lines]}"
    )
    assert so.lines[0].line_type == LineType.PRODUCT
    assert so.lines[0].description != "Optional install kit"
    assert abs(so.subtotal - 150.0) < 0.01
