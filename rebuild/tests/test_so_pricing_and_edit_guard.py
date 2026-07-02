"""
tests/test_so_pricing_and_edit_guard.py
=======================================
Fix-sprint lane so-pricing — two confirmed SO defects.

Defect 1 (pricing): the SO add-line ROUTER pre-filled unit_price from
product.selling_price, so apply_product_line_defaults saw a non-zero "explicit"
price and skipped the Step-0 customer-price-rule / tier waterfall (a wholesale
20%-tier customer was billed $130 instead of $104). The router now forwards a
blank/0 price and the service resolves it; an explicitly typed non-zero price
still wins.

Defect 2 (inventory): update_line committed a qty increase unconditionally
(5 on hand, edit to 50 → qty_available −45 silently). It now mirrors
_add_line_internal's R6 guard: STOCK lines raise when delta > qty_available
unless the caller holds NEGATIVE_INVENTORY_OVERRIDE and passes
allow_negative_inventory=True.

Also locked: discount_pct on SO lines is validated to 0–100 on add and update.

Run:
    .venv/Scripts/python.exe -m pytest tests/test_so_pricing_and_edit_guard.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    FulfillmentSource, LineType, PriceMethod, ProductStatus, ScopeType,
    SOStatus, UserRole,
)
from app.main import app
from app.models.customer import Customer
from app.models.pricing import CustomerPriceRule
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.models.user import User
from app.services.base import PermissionDeniedError
from app.services.sales_order_service import SalesOrderService
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # stub actor (no User row) → system actor under JAKS_SKIP_AUTH
_seq = itertools.count(1)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _uniq() -> int:
    return next(_seq)


def _customer(db, tier: str = "standard") -> Customer:
    c = Customer(
        company_name=f"SO Pricing Co {_uniq()}", contact_name="QA",
        email=f"sop{_uniq()}@test.local", pricing_tier=tier, is_active=True,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, cost: float = 100.0, markup_pct: float = 30.0, qoh: int = 100) -> Product:
    # cost 100 + 30% markup → selling_price / sell_price_for both = 130.00
    p = Product(
        sku=f"SOP-{_uniq():05d}", title=f"SOP Part {_uniq()}",
        description="so pricing part", cost=cost, markup_pct=markup_pct,
        qty_on_hand=qoh, status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def _so(db, customer: Customer) -> SalesOrder:
    so = SalesOrder(
        so_number=f"SO-SOP-{_uniq():04d}", customer_id=customer.id,
        status=SOStatus.OPEN,
    )
    db.add(so); db.commit(); db.refresh(so)
    return so


def _user(db, role: str) -> User:
    n = _uniq()
    u = User(name=f"{role}-{n}", username=f"{role}_{n}", password_hash="x", role=role)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _rule(db, customer, *, method=PriceMethod.MARKUP, value=20.0, qty_min=None) -> CustomerPriceRule:
    r = CustomerPriceRule(
        customer_id=customer.id, scope_type=ScopeType.CUSTOMER, scope_ref=None,
        price_method=method, price_value=value, qty_min=qty_min, is_active=True,
    )
    db.add(r); db.commit(); db.refresh(r)
    return r


def _set_tier_discount(db, tier: str, pct: float) -> None:
    set_setting_value_db(db, f"tier_{tier}_discount_pct", str(pct))
    db.commit()


def _line_for(db, so_id: int, product_id: int) -> SOLine | None:
    return (
        db.query(SOLine)
        .filter(SOLine.so_id == so_id, SOLine.product_id == product_id,
                SOLine.line_type == LineType.PRODUCT)
        .first()
    )


def _fresh_product(db, product_id: int) -> Product:
    db.expire_all()
    return db.query(Product).filter(Product.id == product_id).first()


# ── Defect 1 — pricing waterfall through the ROUTER ──────────────────────────

def test_tier_customer_gets_tier_price_via_router(client, db):
    """product_id + qty only → wholesale 20% tier price ($104), NOT the $130
    selling_price the router used to pre-fill (the verified live defect)."""
    _set_tier_discount(db, "wholesale", 20.0)
    cust = _customer(db, tier="wholesale")
    prod = _product(db)  # sell 130.00
    so = _so(db, cust)

    r = client.post(f"/sales-orders/{so.id}/lines",
                    data={"product_id": str(prod.id), "qty": "1"})
    assert r.status_code < 400, r.text

    line = _line_for(db, so.id, prod.id)
    assert line is not None
    assert line.unit_price == 104.0          # 130 × (1 − 20%)
    assert line.unit_cost == 100.0           # cost backfill still works
    assert line.description == prod.title    # description backfill still works


def test_customer_price_rule_wins_via_router(client, db):
    """A CustomerPriceRule (Step 0) resolves the blank price for a standard-tier
    customer: cost 100 + MARKUP 20 rule → $120, not the $130 selling_price."""
    cust = _customer(db)
    prod = _product(db)
    _rule(db, cust, method=PriceMethod.MARKUP, value=20.0)
    so = _so(db, cust)

    r = client.post(f"/sales-orders/{so.id}/lines",
                    data={"product_id": str(prod.id), "qty": "1"})
    assert r.status_code < 400, r.text

    line = _line_for(db, so.id, prod.id)
    assert line is not None
    assert line.unit_price == 120.0


def test_qty_break_rule_matches_so_qty(client, db):
    """SO lines carry qty as qty_ordered — the volume-break match must see the
    real qty (a qty_min=5 rule applies to a qty-5 POST, not qty=1)."""
    cust = _customer(db)
    prod = _product(db)
    _rule(db, cust, method=PriceMethod.MARKUP, value=10.0, qty_min=5)  # 110.00 at qty ≥ 5
    so = _so(db, cust)

    r = client.post(f"/sales-orders/{so.id}/lines",
                    data={"product_id": str(prod.id), "qty": "5"})
    assert r.status_code < 400, r.text

    line = _line_for(db, so.id, prod.id)
    assert line is not None
    assert line.unit_price == 110.0


def test_explicit_typed_price_wins(client, db):
    """An explicitly typed non-zero price beats the tier waterfall."""
    _set_tier_discount(db, "wholesale", 20.0)
    cust = _customer(db, tier="wholesale")
    prod = _product(db)
    so = _so(db, cust)

    r = client.post(f"/sales-orders/{so.id}/lines",
                    data={"product_id": str(prod.id), "qty": "1",
                          "unit_price": "999.99"})
    assert r.status_code < 400, r.text

    line = _line_for(db, so.id, prod.id)
    assert line is not None
    assert line.unit_price == 999.99


def test_standard_customer_gets_selling_price(client, db):
    """No tier discount, no rules → selling_price fallback unchanged."""
    cust = _customer(db)
    prod = _product(db)
    so = _so(db, cust)

    r = client.post(f"/sales-orders/{so.id}/lines",
                    data={"product_id": str(prod.id), "qty": "1"})
    assert r.status_code < 400, r.text

    line = _line_for(db, so.id, prod.id)
    assert line is not None
    assert line.unit_price == 130.0


# ── Defect 2 — update_line negative-inventory guard ──────────────────────────

def test_update_line_qty_increase_beyond_available_raises(db):
    """5 on hand, line committed at 5 → editing to 50 must raise, not drive
    qty_available to −45 (the verified live defect)."""
    cust = _customer(db)
    prod = _product(db, qoh=5)
    so = _so(db, cust)
    svc = SalesOrderService(db, _UID)
    line = svc.add_line(so.id, prod.id, {"qty_ordered": 5})
    assert line.fulfillment_source == FulfillmentSource.STOCK

    with pytest.raises(ValueError, match="NEGATIVE_INVENTORY_OVERRIDE"):
        svc.update_line(line.id, {"qty_ordered": 50})
    db.rollback()

    p = _fresh_product(db, prod.id)
    assert p.qty_committed == 5
    assert p.qty_available == 0
    assert db.query(SOLine).filter(SOLine.id == line.id).first().qty_ordered == 5


def test_update_line_qty_increase_within_available_commits(db):
    cust = _customer(db)
    prod = _product(db, qoh=10)
    so = _so(db, cust)
    svc = SalesOrderService(db, _UID)
    line = svc.add_line(so.id, prod.id, {"qty_ordered": 5})

    svc.update_line(line.id, {"qty_ordered": 8})
    db.expire_all()

    p = _fresh_product(db, prod.id)
    assert p.qty_committed == 8
    line = db.query(SOLine).filter(SOLine.id == line.id).first()
    assert line.qty_ordered == 8
    assert line.qty_committed == 8


def test_update_line_override_allowed_with_permission(db):
    """ADMIN (holds NEGATIVE_INVENTORY_OVERRIDE) + allow_negative_inventory=True
    goes through — same escape hatch as add-time R6."""
    admin = _user(db, UserRole.ADMIN)
    cust = _customer(db)
    prod = _product(db, qoh=5)
    so = _so(db, cust)
    svc = SalesOrderService(db, admin.id)
    line = svc.add_line(so.id, prod.id, {"qty_ordered": 5})

    svc.update_line(line.id, {"qty_ordered": 50}, allow_negative_inventory=True)
    db.expire_all()

    p = _fresh_product(db, prod.id)
    assert p.qty_committed == 50
    assert db.query(SOLine).filter(SOLine.id == line.id).first().qty_ordered == 50


def test_update_line_override_denied_without_permission(db):
    """A real SALES user lacks NEGATIVE_INVENTORY_OVERRIDE — the flag alone
    must not bypass RBAC."""
    sales = _user(db, UserRole.SALES)
    cust = _customer(db)
    prod = _product(db, qoh=5)
    so = _so(db, cust)
    line = SalesOrderService(db, _UID).add_line(so.id, prod.id, {"qty_ordered": 5})

    with pytest.raises(PermissionDeniedError):
        SalesOrderService(db, sales.id).update_line(
            line.id, {"qty_ordered": 50}, allow_negative_inventory=True,
        )
    db.rollback()

    p = _fresh_product(db, prod.id)
    assert p.qty_committed == 5
    assert db.query(SOLine).filter(SOLine.id == line.id).first().qty_ordered == 5


def test_update_line_backorder_line_not_blocked(db):
    """The guard only fires for STOCK lines — a BACKORDER line never committed
    stock at add time, so a qty edit is not an over-commit."""
    cust = _customer(db)
    prod = _product(db, qoh=2)
    so = _so(db, cust)
    svc = SalesOrderService(db, _UID)
    line = svc.add_line(so.id, prod.id, {"qty_ordered": 5})  # 5 > 2 → BACKORDER
    assert line.fulfillment_source == FulfillmentSource.BACKORDER

    svc.update_line(line.id, {"qty_ordered": 6})  # must not raise
    assert db.query(SOLine).filter(SOLine.id == line.id).first().qty_ordered == 6


# ── discount_pct clamp ────────────────────────────────────────────────────────

def test_discount_pct_clamped_on_update(db):
    cust = _customer(db)
    prod = _product(db)
    so = _so(db, cust)
    svc = SalesOrderService(db, _UID)
    line = svc.add_line(so.id, prod.id, {"qty_ordered": 1})

    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.update_line(line.id, {"discount_pct": 150.0})
    db.rollback()
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.update_line(line.id, {"discount_pct": -5.0})
    db.rollback()

    svc.update_line(line.id, {"discount_pct": 50.0})
    assert db.query(SOLine).filter(SOLine.id == line.id).first().discount_pct == 50.0


def test_discount_pct_clamped_on_add(db):
    cust = _customer(db)
    prod = _product(db)
    so = _so(db, cust)
    svc = SalesOrderService(db, _UID)

    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.add_line(so.id, prod.id, {"qty_ordered": 1, "discount_pct": 150.0})
    db.rollback()
    assert _line_for(db, so.id, prod.id) is None
