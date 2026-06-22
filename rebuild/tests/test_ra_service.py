"""
tests/test_ra_service.py
========================
Unit tests for RAService — the Return Authorization lifecycle.

  create_ra()    → DRAFT
  approve_ra()   → OPEN
  receive_goods()→ RECEIVED   (RETURN_TO_STOCK bumps inventory + writes a txn)
  close_ra()     → CLOSED      (issues an auto-closing CreditMemo for the credit)

These paths move inventory (qty_on_hand) and money (customer.credit_balance via
the RA→CreditMemo→CRMService chain), so they are exactly the kind of untested
logic worth locking down.
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

from app.constants import RAStatus, ReturnDisposition
from app.models.customer import Customer
from app.models.inventory import InventoryTransaction
from app.models.product import Product
from app.services.ra_service import RAService

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


def _make_customer(db):
    global _seq
    _seq += 1
    c = Customer(company_name=f"Acme {_seq}")
    db.add(c)
    db.flush()
    return c


def _make_product(db, qty_on_hand=5):
    global _seq
    _seq += 1
    p = Product(sku=f"JAKS-RA-{_seq}", title="Part", cost=10.0, qty_on_hand=qty_on_hand)
    db.add(p)
    db.flush()
    return p


def _line(product_id=None, qty=1, unit_price=100.0, restocking_fee=0.0,
          disposition=ReturnDisposition.QUARANTINE):
    return {"product_id": product_id, "description": "Returned part", "qty": qty,
            "unit_price": unit_price, "restocking_fee": restocking_fee,
            "disposition": disposition}


# ── create_ra ─────────────────────────────────────────────────────────────────


class TestCreateRA:
    def test_creates_draft_with_number(self, db):
        cust = _make_customer(db)
        ra = RAService(db).create_ra(
            customer_id=cust.id, reason="Wrong part", lines=[_line()]
        )
        assert ra.status == RAStatus.DRAFT
        assert ra.ra_number.startswith("RA-")
        assert len(ra.lines) == 1

    def test_empty_lines_rejected(self, db):
        cust = _make_customer(db)
        with pytest.raises(ValueError, match="at least one line"):
            RAService(db).create_ra(customer_id=cust.id, reason="x", lines=[])

    def test_blank_reason_rejected(self, db):
        cust = _make_customer(db)
        with pytest.raises(ValueError, match="reason is required"):
            RAService(db).create_ra(customer_id=cust.id, reason="   ", lines=[_line()])

    def test_zero_quantity_line_rejected(self, db):
        cust = _make_customer(db)
        with pytest.raises(ValueError, match="at least 1"):
            RAService(db).create_ra(
                customer_id=cust.id, reason="x", lines=[_line(qty=0)]
            )


# ── approve_ra ────────────────────────────────────────────────────────────────


class TestApproveRA:
    def test_draft_to_open(self, db):
        cust = _make_customer(db)
        svc = RAService(db, current_user_id=None)
        ra = svc.create_ra(customer_id=cust.id, reason="x", lines=[_line()])
        svc.approve_ra(ra.id)
        db.refresh(ra)
        assert ra.status == RAStatus.OPEN

    def test_cannot_approve_non_draft(self, db):
        cust = _make_customer(db)
        svc = RAService(db)
        ra = svc.create_ra(customer_id=cust.id, reason="x", lines=[_line()])
        svc.approve_ra(ra.id)
        with pytest.raises(ValueError, match="not DRAFT"):
            svc.approve_ra(ra.id)


# ── receive_goods ─────────────────────────────────────────────────────────────


class TestReceiveGoods:
    def test_return_to_stock_increments_inventory_and_writes_txn(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=5)
        svc = RAService(db)
        ra = svc.create_ra(
            customer_id=cust.id, reason="x",
            lines=[_line(product_id=prod.id, qty=3)],
        )
        svc.approve_ra(ra.id)
        line_id = ra.lines[0].id

        svc.receive_goods(ra.id, [{
            "line_id": line_id,
            "disposition": ReturnDisposition.RETURN_TO_STOCK,
            "qty_returned_to_stock": 3,
        }])
        db.refresh(ra)
        db.refresh(prod)

        assert ra.status == RAStatus.RECEIVED
        assert prod.qty_on_hand == 8  # 5 + 3
        txns = db.query(InventoryTransaction).filter(
            InventoryTransaction.product_id == prod.id
        ).all()
        assert len(txns) == 1
        assert txns[0].qty_change == 3
        assert txns[0].qty_after == 8

    def test_qty_to_stock_capped_at_line_qty(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=0)
        svc = RAService(db)
        ra = svc.create_ra(
            customer_id=cust.id, reason="x",
            lines=[_line(product_id=prod.id, qty=2)],
        )
        svc.approve_ra(ra.id)
        # Ask to restock 10 even though only 2 were returned → capped at 2.
        svc.receive_goods(ra.id, [{
            "line_id": ra.lines[0].id,
            "disposition": ReturnDisposition.RETURN_TO_STOCK,
            "qty_returned_to_stock": 10,
        }])
        db.refresh(prod)
        assert prod.qty_on_hand == 2

    def test_non_restock_disposition_leaves_inventory_untouched(self, db):
        cust = _make_customer(db)
        prod = _make_product(db, qty_on_hand=4)
        svc = RAService(db)
        ra = svc.create_ra(
            customer_id=cust.id, reason="x",
            lines=[_line(product_id=prod.id, qty=2)],
        )
        svc.approve_ra(ra.id)
        svc.receive_goods(ra.id, [{
            "line_id": ra.lines[0].id,
            "disposition": ReturnDisposition.SCRAP,
            "qty_returned_to_stock": 2,
        }])
        db.refresh(prod)
        assert prod.qty_on_hand == 4  # scrap → no restock
        assert db.query(InventoryTransaction).filter(
            InventoryTransaction.product_id == prod.id
        ).count() == 0

    def test_cannot_receive_on_closed_ra(self, db):
        cust = _make_customer(db)
        svc = RAService(db)
        ra = svc.create_ra(customer_id=cust.id, reason="x", lines=[_line()])
        svc.approve_ra(ra.id)
        svc.close_ra(ra.id)
        with pytest.raises(ValueError, match="Can only receive"):
            svc.receive_goods(ra.id, [])


# ── close_ra ──────────────────────────────────────────────────────────────────


class TestCloseRA:
    def test_close_with_credit_issues_credit_memo_to_balance(self, db):
        cust = _make_customer(db)
        svc = RAService(db)
        ra = svc.create_ra(
            customer_id=cust.id, reason="x",
            lines=[_line(qty=1, unit_price=150.0)],
        )
        svc.approve_ra(ra.id)
        svc.close_ra(ra.id)
        db.refresh(ra)
        db.refresh(cust)
        assert ra.status == RAStatus.CLOSED
        # total_credit = 150 → flows to credit_balance via auto-closed CreditMemo.
        assert cust.credit_balance == 150.0

    def test_restocking_fee_reduces_credit(self, db):
        cust = _make_customer(db)
        svc = RAService(db)
        ra = svc.create_ra(
            customer_id=cust.id, reason="x",
            lines=[_line(qty=1, unit_price=150.0, restocking_fee=30.0)],
        )
        svc.approve_ra(ra.id)
        svc.close_ra(ra.id)
        db.refresh(cust)
        assert cust.credit_balance == 120.0  # 150 - 30 fee

    def test_close_with_no_credit_leaves_balance_zero(self, db):
        cust = _make_customer(db)
        svc = RAService(db)
        ra = svc.create_ra(
            customer_id=cust.id, reason="x",
            lines=[_line(qty=1, unit_price=0.0)],
        )
        svc.approve_ra(ra.id)
        svc.close_ra(ra.id)
        db.refresh(ra)
        db.refresh(cust)
        assert ra.status == RAStatus.CLOSED
        assert cust.credit_balance == 0.0

    def test_cannot_close_draft(self, db):
        cust = _make_customer(db)
        svc = RAService(db)
        ra = svc.create_ra(customer_id=cust.id, reason="x", lines=[_line()])
        with pytest.raises(ValueError, match="Can only close"):
            svc.close_ra(ra.id)
