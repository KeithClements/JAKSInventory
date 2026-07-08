"""
tests/test_so_cancel_deposit_resolution.py
===========================================
Regression: cancelling a SALES ORDER that holds an unapplied customer deposit
must HONOUR the chosen deposit_resolution and actually POST it — not silently
fall through to 'leave_open' and strand the customer's money.

  SalesOrderService.cancel_order(so_id, reason, deposit_resolution=...):
    - "refund"     -> a REFUND_TO_CUSTOMER Payment (negative amount) is created
    - "credit"     -> the unapplied deposit is moved into customer.credit_balance
    - "leave_open" -> nothing is posted (deposit stays unapplied for manual work)
    - (omitted)    -> rejected: a deposited SO cannot be cancelled without a choice

The bug this locks: refund/credit resolutions that did NOT post (behaved like
leave_open) — the deposit money would vanish from the books while the customer
still expected it back. These tests drive the REAL collect_deposit + cancel_order
paths and assert the money lands in the right place for each resolution.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_so_cancel_deposit_resolution.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.constants import LineType, PaymentDirection, PaymentStatus, SOPaymentMode, SOStatus
from app.models.customer import Customer
from app.models.invoice import Payment
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.services.sales_order_service import SalesOrderService
from tests.conftest import activate, fresh_engine

_UID = 1
_seq = itertools.count(1)


@pytest.fixture()
def db():
    # Per-test fresh engine via the shared harness — credit_balance deltas and
    # refund-Payment counts must not leak across tests.
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _deposited_so(db, *, deposit: float = 100.0):
    """An OPEN SO with one committed product line and a real, unapplied deposit
    collected through SalesOrderService.collect_deposit()."""
    n = next(_seq)
    cust = Customer(company_name=f"Deposit Co {n}", is_active=True, credit_balance=0.0)
    db.add(cust); db.flush()
    prod = Product(sku=f"DEP-{n:05d}", title="Reman Turbo", cost=200.0,
                   qty_on_hand=10, qty_committed=3)
    db.add(prod); db.flush()
    so = SalesOrder(so_number=f"SO-DEP-{n:04d}", customer_id=cust.id,
                    status=SOStatus.OPEN, payment_mode=SOPaymentMode.DEPOSIT,
                    deposit_amount=0.0, notes="", internal_notes="")
    db.add(so); db.flush()
    db.add(SOLine(so_id=so.id, product_id=prod.id, line_type=LineType.PRODUCT,
                  description="Reman Turbo", qty_ordered=3, qty_committed=3,
                  qty_fulfilled=0, qty_invoiced=0, unit_price=500.0))
    db.commit()
    # The real deposit path: an unapplied Payment linked to the SO.
    SalesOrderService(db, _UID).collect_deposit(so.id, deposit, "cash")
    db.refresh(so); db.refresh(cust)
    return so, cust


def _refunds_for(db, so_id):
    return (db.query(Payment)
            .filter(Payment.sales_order_id == so_id,
                    Payment.direction == PaymentDirection.REFUND_TO_CUSTOMER)
            .all())


# ── guard: a deposited SO can't be cancelled without choosing a resolution ────

def test_cancel_requires_resolution_when_deposit_is_unapplied(db):
    so, _ = _deposited_so(db)
    with pytest.raises(ValueError):
        SalesOrderService(db, _UID).cancel_order(so.id, "customer changed mind")
    db.rollback()
    db.refresh(so)
    assert so.status != SOStatus.CANCELLED   # rejected, not half-cancelled


# ── refund: posts a REFUND_TO_CUSTOMER payment, not store credit ──────────────

def test_cancel_with_refund_posts_a_refund_payment(db):
    so, cust = _deposited_so(db, deposit=150.0)
    SalesOrderService(db, _UID).cancel_order(so.id, "wrong part", deposit_resolution="refund")
    db.refresh(so); db.refresh(cust)

    refunds = _refunds_for(db, so.id)
    assert len(refunds) == 1, "refund resolution did NOT post a refund payment (fell through to leave_open?)"
    assert refunds[0].amount_received == -150.0          # money going OUT to the customer
    assert refunds[0].status == PaymentStatus.APPLIED
    assert cust.credit_balance == 0.0                    # a refund is NOT store credit
    assert so.status == SOStatus.CANCELLED


# ── credit: moves the deposit into customer.credit_balance, no refund payment ─

def test_cancel_with_credit_posts_to_credit_balance(db):
    so, cust = _deposited_so(db, deposit=120.0)
    before = cust.credit_balance
    SalesOrderService(db, _UID).cancel_order(so.id, "switch to credit", deposit_resolution="credit")
    db.refresh(so); db.refresh(cust)

    assert cust.credit_balance == before + 120.0, "credit resolution did NOT post to credit_balance"
    assert _refunds_for(db, so.id) == [], "credit must not also cut a refund payment"
    assert so.status == SOStatus.CANCELLED


# ── contrast: leave_open is the ONLY resolution that posts nothing ────────────

def test_cancel_with_leave_open_posts_neither(db):
    """Proves the refund/credit branches above are real work: with leave_open the
    deposit stays unapplied on the account and nothing else moves."""
    so, cust = _deposited_so(db, deposit=90.0)
    SalesOrderService(db, _UID).cancel_order(so.id, "manual follow-up", deposit_resolution="leave_open")
    db.refresh(so); db.refresh(cust)

    assert _refunds_for(db, so.id) == []
    assert cust.credit_balance == 0.0
    deposit = (db.query(Payment)
               .filter(Payment.sales_order_id == so.id,
                       Payment.direction != PaymentDirection.REFUND_TO_CUSTOMER)
               .first())
    assert deposit is not None and deposit.amount_unallocated > 0.001   # still open
    assert so.status == SOStatus.CANCELLED
