"""
tests/test_r1_payment_reversal_allocation.py
============================================
FIX R1-2 — NSF / reversed payment funds must not be stranded.

THE BUG
-------
Payment.amount_allocated summed allocations WITHOUT filtering is_reversed,
while Invoice.amount_paid DID filter. After a reversal, amount_unallocated
read 0 (funds stranded: allocated-but-not-paid) and the allocate() guard
blocked re-applying funds whose allocation had been legitimately reversed.

THE FIX (pinned here)
---------------------
  1. Payment.amount_allocated now mirrors Invoice.amount_paid — reversed
     allocations release their funds back to amount_unallocated.
  2. PaymentService.allocate() rejects payments with status REVERSED/NSF —
     after fix 1 a bounced payment reads as fully unallocated, but that
     money was never collected and must not be re-applied to invoices.

Harness: module-isolated in-memory engine from tests/conftest.py, seeded
exactly like tests/test_payments.py. _UID = 1 is the startup-seeded admin,
who holds Permission.REVERSE_PAYMENT.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r1_payment_reversal_allocation.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, LineType, PaymentMethod, PaymentStatus
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # admin, seeded by the startup hook → holds Permission.REVERSE_PAYMENT
_seq = itertools.count(1)  # unique invoice numbers across the module


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _customer(db) -> Customer:
    c = Customer(company_name=f"R1 Reversal Co {next(_seq)}", contact_name="QA",
                 email=f"r1rev{next(_seq)}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id: int, amount: float) -> Invoice:
    """Create + finalise a non-taxable OPEN invoice with balance_due == amount."""
    inv = Invoice(
        invoice_number=f"INV-R1-{next(_seq):05d}",
        customer_id=customer_id,
        status=InvoiceStatus.DRAFT,
        tax_rate=0.0,
        tax_rate_snapshot=0.0,
        tax_exempt_snapshot=True,
    )
    db.add(inv); db.flush()
    db.add(InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Test part",
        qty=1,
        unit_price=amount,
        unit_cost=amount * 0.6,
        is_taxable=False,
    ))
    db.commit()

    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _svc(db) -> PaymentService:
    return PaymentService(db, _UID)


def _reget_invoice(db, inv_id: int) -> Invoice:
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv_id).first()


def _reget_payment(db, pmt_id: int) -> Payment:
    db.expire_all()
    return db.query(Payment).filter(Payment.id == pmt_id).first()


# ── 1. Reversed allocation releases funds; re-allocation succeeds ─────────────

def test_reversed_allocation_releases_funds_and_reallocation_succeeds(db):
    """The headline R1-2 path: a misapplied allocation gets reversed (payment
    itself stays good/APPLIED), the funds return to amount_unallocated, and
    the money can be re-applied to the correct invoice."""
    cust = _customer(db)
    inv_wrong = _open_invoice(db, cust.id, 100.0)
    inv_right = _open_invoice(db, cust.id, 100.0)

    pmt = _svc(db).record_payment(cust.id, 100.0, PaymentMethod.CHECK, {})
    alloc = _svc(db).allocate(pmt.id, inv_wrong.id, 100.0)

    pmt = _reget_payment(db, pmt.id)
    assert pmt.amount_allocated == 100.0
    assert pmt.amount_unallocated == 0.0
    assert _reget_invoice(db, inv_wrong.id).amount_paid == 100.0

    # Reverse the allocation the way reverse_payment does (flag, not delete) —
    # payment stays APPLIED, only this application of it is undone.
    alloc = db.merge(alloc)
    alloc.is_reversed = True
    db.commit()
    InvoiceService(db, _UID).refresh_payment_status(inv_wrong.id)

    pmt = _reget_payment(db, pmt.id)
    # THE BUG: amount_allocated used to still read 100 → unallocated 0,
    # stranding the funds. Now the reversed allocation releases them.
    assert pmt.amount_allocated == 0.0
    assert pmt.amount_unallocated == 100.0
    assert _reget_invoice(db, inv_wrong.id).amount_paid == 0.0

    # Re-allocation to the correct invoice succeeds (the old guard raised here).
    _svc(db).allocate(pmt.id, inv_right.id, 100.0)

    pmt = _reget_payment(db, pmt.id)
    assert pmt.amount_unallocated == 0.0
    inv_right = _reget_invoice(db, inv_right.id)
    assert inv_right.amount_paid == 100.0
    assert inv_right.status == InvoiceStatus.PAID
    # The wrongly-paid invoice stays unpaid throughout.
    assert _reget_invoice(db, inv_wrong.id).amount_paid == 0.0


def test_partial_allocation_reversal_arithmetic(db):
    """Reversing one allocation releases exactly that amount, untouched
    allocations keep counting."""
    cust = _customer(db)
    inv1 = _open_invoice(db, cust.id, 120.0)
    inv2 = _open_invoice(db, cust.id, 50.0)

    pmt = _svc(db).record_payment(cust.id, 200.0, PaymentMethod.CASH, {})
    a1 = _svc(db).allocate(pmt.id, inv1.id, 120.0)
    _svc(db).allocate(pmt.id, inv2.id, 50.0)

    pmt = _reget_payment(db, pmt.id)
    assert pmt.amount_allocated == 170.0
    assert pmt.amount_unallocated == 30.0

    a1 = db.merge(a1)
    a1.is_reversed = True
    db.commit()
    InvoiceService(db, _UID).refresh_payment_status(inv1.id)

    pmt = _reget_payment(db, pmt.id)
    assert pmt.amount_allocated == 50.0
    assert pmt.amount_unallocated == 150.0
    assert _reget_invoice(db, inv1.id).amount_paid == 0.0
    assert _reget_invoice(db, inv2.id).amount_paid == 50.0


# ── 2. Full payment reversal via the service ──────────────────────────────────

def test_reverse_payment_unstrands_funds_and_reopens_invoice(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 250.0)

    pmt = _svc(db).record_payment(
        cust.id, 250.0, PaymentMethod.CHECK, {"check_number": "9001"},
        invoice_ids=[inv.id],
    )
    assert _reget_invoice(db, inv.id).status == InvoiceStatus.PAID
    assert _reget_payment(db, pmt.id).amount_unallocated == 0.0

    _svc(db).reverse_payment(pmt.id, "stop_payment")

    pmt = _reget_payment(db, pmt.id)
    assert pmt.status == PaymentStatus.REVERSED
    # Pre-fix this read 0 (stranded); the reversal now releases the funds.
    assert pmt.amount_allocated == 0.0
    assert pmt.amount_unallocated == 250.0

    inv = _reget_invoice(db, inv.id)
    assert inv.amount_paid == 0.0
    assert inv.balance_due == 250.0
    assert inv.status == InvoiceStatus.OPEN


def test_reversed_payment_cannot_be_allocated(db):
    """Guard: after the model fix a REVERSED payment reads fully unallocated,
    but that money bounced — allocate() must refuse it."""
    cust = _customer(db)
    inv1 = _open_invoice(db, cust.id, 100.0)
    inv2 = _open_invoice(db, cust.id, 100.0)

    pmt = _svc(db).record_payment(cust.id, 100.0, PaymentMethod.CHECK, {},
                                  invoice_ids=[inv1.id])
    _svc(db).reverse_payment(pmt.id, "nsf")

    assert _reget_payment(db, pmt.id).amount_unallocated == 100.0
    with pytest.raises(ValueError, match="cannot be allocated"):
        _svc(db).allocate(pmt.id, inv2.id, 100.0)
    assert _reget_invoice(db, inv2.id).amount_paid == 0.0


def test_nsf_payment_cannot_be_allocated(db):
    cust = _customer(db)
    inv1 = _open_invoice(db, cust.id, 75.0)
    inv2 = _open_invoice(db, cust.id, 75.0)

    pmt = _svc(db).record_payment(cust.id, 75.0, PaymentMethod.CHECK, {},
                                  invoice_ids=[inv1.id])
    _svc(db).process_nsf(pmt.id, nsf_fee=25.0)

    pmt = _reget_payment(db, pmt.id)
    assert pmt.status == PaymentStatus.NSF
    assert pmt.amount_unallocated == 75.0
    with pytest.raises(ValueError, match="cannot be allocated"):
        _svc(db).allocate(pmt.id, inv2.id, 75.0)


# ── 3. Reversed payments stay out of the unapplied-credit pool ────────────────

def test_reversed_payment_excluded_from_unapplied_balance(db):
    """get_unapplied_balance filters status==APPLIED — the freed-up
    amount_unallocated on a REVERSED payment must not surface as customer
    credit."""
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 60.0)

    pmt = _svc(db).record_payment(cust.id, 60.0, PaymentMethod.CHECK, {},
                                  invoice_ids=[inv.id])
    assert _svc(db).get_unapplied_balance(cust.id) == 0.0

    _svc(db).reverse_payment(pmt.id, "nsf")
    assert _reget_payment(db, pmt.id).amount_unallocated == 60.0
    assert _svc(db).get_unapplied_balance(cust.id) == 0.0
