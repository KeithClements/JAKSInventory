"""
tests/test_returns_ra.py
========================
Functional integration tests for the Return Authorization (RA) lifecycle
(TESTING_FEEDBACK §5.1). Zero prior functional confirmation.

Strategy: drive RAService directly with current_user_id=None (bypasses the
ISSUE_CREDIT_MEMO gate that close_ra -> CreditMemoService enforces). A module
TestClient runs startup (admin user, settings) and serves the print/PDF GETs.
Assertions are behaviour-level: status transitions, inventory return-to-stock
ledger rows, and the customer credit_balance the credit memo applies.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


_SEQ = {"n": 0}


def _uniq():
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"RA Cust {_uniq()}", contact_name="QA",
                 email=f"ra{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, qty_on_hand=0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"RA-{_uniq():04d}", title="RA Part", description="RA Part",
                cost=20.0, markup_pct=50.0, qty_on_hand=qty_on_hand,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ===========================================================================
# §5.1.a — create RA with lines + restocking fee
# ===========================================================================

def test_create_ra_with_lines_and_restocking_fee(db):
    from app.constants import RAStatus, ReturnDisposition
    from app.services.ra_service import RAService

    cust = _customer(db); prod = _product(db)
    ra = RAService(db, None).create_ra(
        customer_id=cust.id, reason="Defective",
        lines=[{"product_id": prod.id, "description": "RA Part", "qty": 2,
                "unit_price": 50.0, "restocking_fee": 10.0,
                "disposition": ReturnDisposition.RETURN_TO_STOCK}],
    )
    db.refresh(ra)
    assert ra.status == RAStatus.DRAFT
    assert ra.ra_number.startswith("RA-")
    assert len(ra.lines) == 1
    assert ra.lines[0].restocking_fee == 10.0
    # total_credit = unit_price*qty - restocking_fee = 100 - 10 = 90
    assert ra.total_credit == 90.0


def test_create_ra_rejects_empty_lines_and_blank_reason(db):
    from app.services.ra_service import RAService

    cust = _customer(db)
    with pytest.raises(ValueError):
        RAService(db, None).create_ra(customer_id=cust.id, reason="x", lines=[])
    db.rollback()
    with pytest.raises(ValueError):
        RAService(db, None).create_ra(customer_id=cust.id, reason="   ",
                                      lines=[{"description": "a", "qty": 1}])
    db.rollback()


# ===========================================================================
# §5.1.b — Approve → Receive → Close lifecycle (+ return-to-stock inventory)
# ===========================================================================

def test_approve_receive_close_lifecycle(db):
    from app.constants import RAStatus, ReturnDisposition, InventoryTxnType
    from app.models.inventory import InventoryTransaction
    from app.models.product import Product
    from app.services.ra_service import RAService

    cust = _customer(db); prod = _product(db, qty_on_hand=5)
    svc = RAService(db, None)
    ra = svc.create_ra(
        customer_id=cust.id, reason="Wrong part",
        lines=[{"product_id": prod.id, "description": "RA Part", "qty": 3,
                "unit_price": 40.0, "restocking_fee": 0.0,
                "disposition": ReturnDisposition.RETURN_TO_STOCK}],
    )
    line_id = ra.lines[0].id

    svc.approve_ra(ra.id)
    db.refresh(ra); assert ra.status == RAStatus.OPEN

    svc.receive_goods(ra.id, line_updates=[{
        "line_id": line_id, "disposition": ReturnDisposition.RETURN_TO_STOCK,
        "qty_returned_to_stock": 3, "condition_notes": "ok"}])
    db.refresh(ra); assert ra.status == RAStatus.RECEIVED

    # inventory returned to stock: 5 + 3 = 8, with a ledger row
    db.refresh(prod)
    assert prod.qty_on_hand == 8
    txn = (db.query(InventoryTransaction)
           .filter(InventoryTransaction.product_id == prod.id,
                   InventoryTransaction.transaction_type == InventoryTxnType.RETURN_TO_STOCK)
           .first())
    assert txn is not None and txn.qty_change == 3

    svc.close_ra(ra.id)
    db.refresh(ra); assert ra.status == RAStatus.CLOSED


# ===========================================================================
# §5.1.c — closing credits the customer correctly
# ===========================================================================

def test_close_credits_customer_via_credit_memo(db):
    from app.constants import (CreditMemoStatus, ReturnDisposition)
    from app.models.credit_memo import CreditMemo
    from app.services.ra_service import RAService

    cust = _customer(db); prod = _product(db, qty_on_hand=0)
    start_balance = cust.credit_balance
    svc = RAService(db, None)
    ra = svc.create_ra(
        customer_id=cust.id, reason="Defective",
        lines=[{"product_id": prod.id, "description": "RA Part", "qty": 2,
                "unit_price": 50.0, "restocking_fee": 10.0,
                "disposition": ReturnDisposition.RETURN_TO_STOCK}],
    )
    svc.approve_ra(ra.id)
    svc.receive_goods(ra.id, line_updates=[{
        "line_id": ra.lines[0].id, "disposition": ReturnDisposition.RETURN_TO_STOCK,
        "qty_returned_to_stock": 2}])
    svc.close_ra(ra.id)

    db.refresh(cust)
    # credit = unit_price*qty - restocking_fee = 100 - 10 = 90
    assert cust.credit_balance == start_balance + 90.0
    cm = db.query(CreditMemo).filter(CreditMemo.ra_id == ra.id).first()
    assert cm is not None, "closing an RA must create a CreditMemo"
    assert cm.status == CreditMemoStatus.APPLIED
    assert cm.total_amount == 90.0


# ===========================================================================
# §5.1.d — print / PDF render
# ===========================================================================

def test_ra_print_and_pdf_render(client, db):
    from app.constants import ReturnDisposition
    from app.services.ra_service import RAService

    cust = _customer(db); prod = _product(db)
    ra = RAService(db, None).create_ra(
        customer_id=cust.id, reason="Defective",
        lines=[{"product_id": prod.id, "description": "RA Part", "qty": 1,
                "unit_price": 25.0, "disposition": ReturnDisposition.QUARANTINE}],
    )
    assert client.get(f"/returns/{ra.id}/print").status_code == 200
    assert client.get(f"/returns/{ra.id}/pdf").status_code == 200


# ===========================================================================
# §5.1.e — double-credit guard (regression): re-closing an RA must never issue
# a second credit memo. Covers the non-atomic window where the credit memo
# commits but the RA status flip to CLOSED does not (process crash between
# the two commits in close_ra).
# ===========================================================================

def test_close_ra_idempotent_no_double_credit(db):
    from app.constants import RAStatus, ReturnDisposition
    from app.models.credit_memo import CreditMemo
    from app.services.ra_service import RAService

    cust = _customer(db); prod = _product(db, qty_on_hand=0)
    start_balance = cust.credit_balance
    svc = RAService(db, None)
    ra = svc.create_ra(
        customer_id=cust.id, reason="Defective",
        lines=[{"product_id": prod.id, "description": "RA Part", "qty": 2,
                "unit_price": 50.0, "restocking_fee": 0.0,
                "disposition": ReturnDisposition.RETURN_TO_STOCK}],
    )
    svc.approve_ra(ra.id)
    svc.receive_goods(ra.id, line_updates=[{
        "line_id": ra.lines[0].id, "disposition": ReturnDisposition.RETURN_TO_STOCK,
        "qty_returned_to_stock": 2}])
    svc.close_ra(ra.id)

    db.refresh(cust)
    credited_once = cust.credit_balance
    assert credited_once == start_balance + 100.0

    # Simulate the partial-failure window: CM committed, status flip lost.
    ra_row = svc._get_or_404(ra.id)
    ra_row.status = RAStatus.RECEIVED
    db.commit()

    # Re-close must reuse the existing CM — no second credit, no balance change.
    svc.close_ra(ra.id)
    db.refresh(cust)
    assert cust.credit_balance == credited_once, "re-closing the RA double-credited the customer"
    cms = db.query(CreditMemo).filter(CreditMemo.ra_id == ra.id).all()
    assert len(cms) == 1, f"expected exactly one credit memo for the RA, got {len(cms)}"
