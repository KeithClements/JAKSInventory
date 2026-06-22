"""
tests/test_credit_memo_service.py
=================================
Unit tests for CreditMemoService — R8 customer-side credit memos.

A credit memo is an independent financial document (not a negative invoice).
Money only reaches customer.credit_balance when close_credit_memo() runs with
an unapplied residual, so the create / close / reverse transitions and their
guards are the high-value paths to pin down.

Coverage
--------
  - create_credit_memo: totals (incl. discount), status/unapplied seeding,
    validation (no lines, bad customer, bad trigger, non-positive total),
    permission gate, and the auto_close_to_credit → credit_balance path
  - close_credit_memo: residual → credit_balance, idempotency, reversed guard
  - reverse_credit_memo: credit_balance deduction + REVERSED, double-reverse guard
  - apply_credit_memo: early validation guards (amount, unapplied, missing invoice)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import __all_models__  # noqa: F401 — registers every model on Base
from app.database import Base

import pytest

from app.constants import (
    CreditMemoStatus, CreditMemoTrigger, Permission, UserRole,
)
from app.models.customer import Customer
from app.models.user import User
from app.services.base import PermissionDeniedError
from app.services.credit_memo_service import CreditMemoService

# ── Test isolation (mirrors conftest pattern) ─────────────────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=_engine)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def _activate_engine():
    from tests.conftest import activate

    activate(_engine)


@pytest.fixture()
def db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

_seq = 0


def _make_customer(db, credit_balance=0.0):
    global _seq
    _seq += 1
    c = Customer(company_name=f"Acme {_seq}", credit_balance=credit_balance)
    db.add(c)
    db.flush()
    return c


def _make_user(db, role):
    global _seq
    _seq += 1
    u = User(name=f"U{_seq}", username=f"user{_seq}", password_hash="x", role=role)
    db.add(u)
    db.flush()
    return u


def _one_line(unit_price=100.0, qty=1, discount_pct=0.0):
    return [{"description": "Part", "qty": qty, "unit_price": unit_price,
             "discount_pct": discount_pct}]


# ── create_credit_memo ────────────────────────────────────────────────────────


class TestCreateCreditMemo:
    def test_basic_create_seeds_open_status(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)  # system user → permission bypass
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(unit_price=250.0),
        )
        assert cm.status == CreditMemoStatus.OPEN
        assert cm.total_amount == 250.0
        assert cm.unapplied_amount == 250.0
        assert cm.applied_amount == 0.0
        assert cm.cm_number.startswith("CM-")

    def test_total_applies_discount(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        # 2 × 50 × (1 - 10%) = 90.00
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.OVERCHARGE,
            lines=_one_line(unit_price=50.0, qty=2, discount_pct=10.0),
        )
        assert cm.total_amount == 90.0

    def test_empty_lines_rejected(self, db):
        cust = _make_customer(db)
        with pytest.raises(ValueError, match="at least one line"):
            CreditMemoService(db).create_credit_memo(
                customer_id=cust.id,
                trigger_type=CreditMemoTrigger.MANUAL,
                lines=[],
            )

    def test_unknown_customer_rejected(self, db):
        with pytest.raises(ValueError, match="not found"):
            CreditMemoService(db).create_credit_memo(
                customer_id=999999,
                trigger_type=CreditMemoTrigger.MANUAL,
                lines=_one_line(),
            )

    def test_invalid_trigger_rejected(self, db):
        cust = _make_customer(db)
        with pytest.raises(ValueError, match="Invalid credit memo trigger"):
            CreditMemoService(db).create_credit_memo(
                customer_id=cust.id,
                trigger_type="not_a_real_trigger",
                lines=_one_line(),
            )

    def test_non_positive_total_rejected(self, db):
        cust = _make_customer(db)
        with pytest.raises(ValueError, match="must be positive"):
            CreditMemoService(db).create_credit_memo(
                customer_id=cust.id,
                trigger_type=CreditMemoTrigger.MANUAL,
                lines=_one_line(unit_price=0.0),
            )

    def test_permission_denied_for_non_privileged_user(self, db):
        cust = _make_customer(db)
        user = _make_user(db, UserRole.READ_ONLY)
        svc = CreditMemoService(db, current_user_id=user.id)
        with pytest.raises(PermissionDeniedError):
            svc.create_credit_memo(
                customer_id=cust.id,
                trigger_type=CreditMemoTrigger.MANUAL,
                lines=_one_line(),
            )

    def test_admin_user_permitted(self, db):
        cust = _make_customer(db)
        admin = _make_user(db, UserRole.ADMIN)
        svc = CreditMemoService(db, current_user_id=admin.id)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(),
        )
        assert cm.created_by_user_id == admin.id

    def test_auto_close_pushes_to_credit_balance(self, db):
        cust = _make_customer(db, credit_balance=0.0)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.ACCEPTED_RA,
            lines=_one_line(unit_price=120.0),
            auto_close_to_credit=True,
        )
        db.refresh(cust)
        assert cm.status == CreditMemoStatus.APPLIED
        assert cm.unapplied_amount == 0.0
        assert cust.credit_balance == 120.0


# ── close_credit_memo ─────────────────────────────────────────────────────────


class TestCloseCreditMemo:
    def test_close_moves_residual_to_credit_balance(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(unit_price=80.0),
        )
        svc.close_credit_memo(cm.id)
        db.refresh(cust)
        db.refresh(cm)
        assert cm.status == CreditMemoStatus.APPLIED
        assert cust.credit_balance == 80.0

    def test_close_is_idempotent(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(unit_price=80.0),
        )
        svc.close_credit_memo(cm.id)
        svc.close_credit_memo(cm.id)  # second close must not double-credit
        db.refresh(cust)
        assert cust.credit_balance == 80.0

    def test_close_reversed_rejected(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(),
            auto_close_to_credit=True,
        )
        svc.reverse_credit_memo(cm.id, reason="oops")
        with pytest.raises(ValueError, match="reversed"):
            svc.close_credit_memo(cm.id)


# ── reverse_credit_memo ───────────────────────────────────────────────────────


class TestReverseCreditMemo:
    def test_reverse_deducts_credit_balance(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(unit_price=200.0),
            auto_close_to_credit=True,
        )
        db.refresh(cust)
        assert cust.credit_balance == 200.0

        svc.reverse_credit_memo(cm.id, reason="entered in error")
        db.refresh(cust)
        db.refresh(cm)
        assert cm.status == CreditMemoStatus.REVERSED
        assert cust.credit_balance == 0.0

    def test_double_reverse_rejected(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(),
            auto_close_to_credit=True,
        )
        svc.reverse_credit_memo(cm.id, reason="first")
        with pytest.raises(ValueError, match="already reversed"):
            svc.reverse_credit_memo(cm.id, reason="second")


# ── apply_credit_memo guards ──────────────────────────────────────────────────


class TestApplyCreditMemoGuards:
    def test_non_positive_amount_rejected(self, db):
        with pytest.raises(ValueError, match="must be positive"):
            CreditMemoService(db).apply_credit_memo(cm_id=1, invoice_id=1, amount=0.0)

    def test_amount_exceeding_unapplied_rejected(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(unit_price=100.0),
        )
        with pytest.raises(ValueError, match="exceeds unapplied"):
            svc.apply_credit_memo(cm_id=cm.id, invoice_id=1, amount=500.0)

    def test_missing_invoice_rejected(self, db):
        cust = _make_customer(db)
        svc = CreditMemoService(db)
        cm = svc.create_credit_memo(
            customer_id=cust.id,
            trigger_type=CreditMemoTrigger.MANUAL,
            lines=_one_line(unit_price=100.0),
        )
        with pytest.raises(ValueError, match="not found"):
            svc.apply_credit_memo(cm_id=cm.id, invoice_id=999999, amount=50.0)
