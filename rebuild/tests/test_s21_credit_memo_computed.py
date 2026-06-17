"""
tests/test_s21_credit_memo_computed.py
======================================
§21 — CreditMemo.applied_amount is now COMPUTED from the allocations (can't drift
from the CreditMemoAllocation ledger). unapplied_amount stays stored (close moves
the residual to credit_balance, a state not derivable from allocations).
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest

from app.constants import CreditMemoStatus, CreditMemoTrigger, InvoiceStatus, LineType, ProductStatus
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.services.credit_memo_service import CreditMemoService
from app.services.invoice_service import InvoiceService

_ENGINE = fresh_engine()
_UID = None  # system actor → permission gates pass
_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(_ENGINE)
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db) -> Customer:
    c = Customer(company_name=f"CM Co {next(_seq)}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id, amount) -> Invoice:
    inv = Invoice(invoice_number=f"INV-CM-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.DRAFT, tax_rate=0.0, tax_rate_snapshot=0.0,
                  tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT, description="p",
                       qty=1, unit_price=amount, unit_cost=amount * 0.6, is_taxable=False))
    db.commit()
    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).get(inv.id)


def _reget(db, cm_id):
    db.expire_all()
    from app.models.credit_memo import CreditMemo
    return db.query(CreditMemo).get(cm_id)


def test_new_credit_memo_applied_is_zero(db):
    c = _customer(db)
    cm = CreditMemoService(db, _UID).create_credit_memo(
        c.id, CreditMemoTrigger.MANUAL,
        lines=[{"description": "credit", "qty": 1, "unit_price": 200.0}], reason="x")
    assert cm.applied_amount == 0.0
    assert cm.unapplied_amount == pytest.approx(200.0)


def test_applied_amount_tracks_allocations(db):
    c = _customer(db)
    inv = _open_invoice(db, c.id, 500.0)
    svc = CreditMemoService(db, _UID)
    cm = svc.create_credit_memo(
        c.id, CreditMemoTrigger.MANUAL,
        lines=[{"description": "credit", "qty": 1, "unit_price": 300.0}], reason="x")

    svc.apply_credit_memo(cm.id, inv.id, 120.0)
    cm = _reget(db, cm.id)
    assert cm.applied_amount == pytest.approx(120.0)      # computed from the allocation
    assert cm.unapplied_amount == pytest.approx(180.0)
    assert cm.status == CreditMemoStatus.PARTIAL

    svc.apply_credit_memo(cm.id, inv.id, 180.0)
    cm = _reget(db, cm.id)
    assert cm.applied_amount == pytest.approx(300.0)
    assert cm.unapplied_amount == 0.0
    assert cm.status == CreditMemoStatus.APPLIED


def test_applied_amount_drops_when_reversed(db):
    c = _customer(db)
    inv = _open_invoice(db, c.id, 500.0)
    svc = CreditMemoService(db, _UID)
    cm = svc.create_credit_memo(
        c.id, CreditMemoTrigger.MANUAL,
        lines=[{"description": "credit", "qty": 1, "unit_price": 200.0}], reason="x")
    svc.apply_credit_memo(cm.id, inv.id, 200.0)
    assert _reget(db, cm.id).applied_amount == pytest.approx(200.0)

    svc.reverse_credit_memo(cm.id, "test reverse")
    cm = _reget(db, cm.id)
    # All allocations reversed → computed applied falls to 0; status REVERSED.
    assert cm.applied_amount == 0.0
    assert cm.status == CreditMemoStatus.REVERSED
