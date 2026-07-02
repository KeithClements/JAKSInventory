"""
tests/test_cm_allocation_link.py
================================
Credit-memo reversal integrity — CreditMemoAllocation.linked_payment_allocation_id.

apply_credit_memo creates a synthetic ACCOUNT_CREDIT Payment/PaymentAllocation to
keep invoice balance math consistent. Historically reverse_credit_memo re-found
that allocation via a LIKE-on-notes heuristic, which silently no-ops when the
notes text drifts. Now:

  - apply stores the PaymentAllocation id on the CM allocation (hard link)
  - reverse joins on the link when set; NULL-link legacy rows fall back to the
    notes heuristic; if neither resolves, reverse RAISES (never a silent no-op)
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

from app.constants import CreditMemoStatus, CreditMemoTrigger, InvoiceStatus, LineType
from app.models.credit_memo import CreditMemo, CreditMemoAllocation
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
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
    c = Customer(company_name=f"CM Link Co {next(_seq)}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id, amount) -> Invoice:
    inv = Invoice(invoice_number=f"INV-CML-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.DRAFT, tax_rate=0.0, tax_rate_snapshot=0.0,
                  tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT, description="p",
                       qty=1, unit_price=amount, unit_cost=amount * 0.6, is_taxable=False))
    db.commit()
    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).get(inv.id)


def _applied_cm(db, amount=200.0, invoice_amount=500.0):
    """Create customer + invoice + CM, apply the full CM to the invoice.

    Returns (cm, invoice, cm_alloc, pmt_alloc) freshly loaded.
    """
    c = _customer(db)
    inv = _open_invoice(db, c.id, invoice_amount)
    svc = CreditMemoService(db, _UID)
    cm = svc.create_credit_memo(
        c.id, CreditMemoTrigger.MANUAL,
        lines=[{"description": "credit", "qty": 1, "unit_price": amount}], reason="x")
    svc.apply_credit_memo(cm.id, inv.id, amount)
    db.expire_all()
    cm = db.query(CreditMemo).get(cm.id)
    cm_alloc = (db.query(CreditMemoAllocation)
                .filter(CreditMemoAllocation.credit_memo_id == cm.id).one())
    pmt_alloc = db.query(PaymentAllocation).get(cm_alloc.linked_payment_allocation_id)
    return cm, db.query(Invoice).get(inv.id), cm_alloc, pmt_alloc


def _reget_cm(db, cm_id) -> CreditMemo:
    db.expire_all()
    return db.query(CreditMemo).get(cm_id)


# ── Link written at apply time ──────────────────────────────────────────────


def test_apply_stores_link_to_synthetic_payment_allocation(db):
    cm, inv, cm_alloc, pmt_alloc = _applied_cm(db)
    assert cm_alloc.linked_payment_allocation_id is not None
    assert pmt_alloc is not None
    # The link points at the synthetic ACCOUNT_CREDIT allocation for this invoice
    assert pmt_alloc.invoice_id == inv.id
    assert pmt_alloc.amount_applied == pytest.approx(200.0)
    payment = db.query(Payment).get(pmt_alloc.payment_id)
    assert payment.payment_method == "account_credit"
    assert cm.cm_number in payment.notes


# ── Reverse resolves via the link, not the notes heuristic ──────────────────


def test_reverse_resolves_via_link_even_with_corrupted_notes(db):
    cm, inv, cm_alloc, pmt_alloc = _applied_cm(db)
    # Corrupt the payment notes so the legacy LIKE-on-notes heuristic CANNOT
    # match — a successful reversal proves the hard link did the resolving.
    payment = db.query(Payment).get(pmt_alloc.payment_id)
    payment.notes = "corrupted — no CM number here"
    db.commit()

    CreditMemoService(db, _UID).reverse_credit_memo(cm.id, "link-path reverse")

    cm = _reget_cm(db, cm.id)
    assert cm.status == CreditMemoStatus.REVERSED
    assert cm.applied_amount == 0.0
    cm_alloc = (db.query(CreditMemoAllocation)
                .filter(CreditMemoAllocation.credit_memo_id == cm.id).one())
    assert cm_alloc.is_reversed is True
    pmt_alloc = db.query(PaymentAllocation).get(cm_alloc.linked_payment_allocation_id)
    assert pmt_alloc.is_reversed is True
    # Invoice re-opened: the credit no longer counts toward amount_paid
    inv = db.query(Invoice).get(inv.id)
    assert inv.balance_due == pytest.approx(500.0)


# ── Legacy NULL-link rows still reverse via the notes heuristic ─────────────


def test_legacy_null_link_reverses_via_notes_fallback(db):
    cm, inv, cm_alloc, pmt_alloc = _applied_cm(db)
    # Simulate a pre-migration row: link column NULL, notes intact
    cm_alloc.linked_payment_allocation_id = None
    db.commit()

    CreditMemoService(db, _UID).reverse_credit_memo(cm.id, "legacy-path reverse")

    db.expire_all()
    cm = _reget_cm(db, cm.id)
    assert cm.status == CreditMemoStatus.REVERSED
    pmt_alloc = db.query(PaymentAllocation).get(pmt_alloc.id)
    assert pmt_alloc.is_reversed is True
    inv = db.query(Invoice).get(inv.id)
    assert inv.balance_due == pytest.approx(500.0)


# ── Unresolvable allocation → loud failure, no silent no-op ─────────────────


def test_null_link_and_corrupted_notes_raises(db):
    cm, inv, cm_alloc, pmt_alloc = _applied_cm(db)
    cm_alloc.linked_payment_allocation_id = None
    payment = db.query(Payment).get(pmt_alloc.payment_id)
    payment.notes = "corrupted — no CM number here"
    db.commit()

    with pytest.raises(ValueError) as exc:
        CreditMemoService(db, _UID).reverse_credit_memo(cm.id, "should fail")
    msg = str(exc.value)
    assert cm.cm_number in msg
    assert f"invoice #{inv.id}" in msg

    # Nothing committed: CM untouched, payment allocation still live,
    # invoice still carries the credit
    db.rollback()
    db.expire_all()
    cm = _reget_cm(db, cm.id)
    assert cm.status == CreditMemoStatus.APPLIED
    pmt_alloc = db.query(PaymentAllocation).get(pmt_alloc.id)
    assert pmt_alloc.is_reversed is False
    inv = db.query(Invoice).get(inv.id)
    assert inv.balance_due == pytest.approx(300.0)


def test_dangling_link_raises_instead_of_guessing_by_notes(db):
    # Link SET but pointing at a reversed allocation → must raise, NOT fall
    # back to the notes heuristic (a set link that fails means data corruption)
    cm, inv, cm_alloc, pmt_alloc = _applied_cm(db)
    pmt_alloc.is_reversed = True  # simulate an out-of-band reversal
    db.commit()

    with pytest.raises(ValueError) as exc:
        CreditMemoService(db, _UID).reverse_credit_memo(cm.id, "should fail")
    assert cm.cm_number in str(exc.value)


# ── Double reverse still blocked ─────────────────────────────────────────────


def test_double_reverse_still_blocked(db):
    cm, inv, cm_alloc, pmt_alloc = _applied_cm(db)
    svc = CreditMemoService(db, _UID)
    svc.reverse_credit_memo(cm.id, "first")
    with pytest.raises(ValueError, match="already reversed"):
        svc.reverse_credit_memo(cm.id, "second")
