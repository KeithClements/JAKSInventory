"""
tests/test_payments.py
======================
Suite D-5 net — the customer-payment money path.

WHY THIS EXISTS
---------------
Payments were the one financial workflow with NO automated regression net
(the standing Suite-D D-5 gap). Every other money surface — invoice totals,
discounts, the void/AR path, the 3-way match — is guarded; this closes the
last hole. These tests pin the *behaviour the code actually has* (verified
against app/services/payment_service.py), so a future refactor that quietly
changes how money is recorded, allocated, reversed, or credited will trip a
red here instead of silently corrupting A/R.

Paths covered (PaymentService is the source of truth for all of them):
  - record_payment           — unapplied credit + non-positive guard
  - record_payment(list)     — auto-spread across two invoices
  - allocate()               — manual allocation across two invoices + guards
  - reverse_payment()        — reopens invoice, restores balance_due
  - reverse_payment() (R2)   — ACCOUNT_CREDIT payment restores credit_balance
  - apply_account_credit()   — draws customer.credit_balance, pays invoice
  - process_nsf()            — marks payment NSF + creates NSF-fee invoice
  - overpay                  — surplus becomes allocatable unapplied credit
                               (NOT a customer.credit_balance write — documented)
  - HTTP contract            — POST /payments/new + /reverse end-to-end

Harness: the in-memory isolation engine from tests/conftest.py, seeded exactly
like tests/test_invoice_void.py (module fresh_engine; DRAFT invoice → finalise
→ OPEN). _UID = 1 is the startup-seeded admin, who holds REVERSE_PAYMENT.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_payments.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    InvoiceStatus, LineType, PaymentMethod, PaymentStatus,
    AuditAction, EntityType,
)
from app.main import app
from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.services.payment_service import PaymentService
from app.services.invoice_service import InvoiceService
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

def _customer(db, *, credit: float = 0.0) -> Customer:
    c = Customer(company_name=f"Pay Test Co {next(_seq)}", contact_name="QA",
                 email="pay@test.local", credit_balance=credit)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id: int, amount: float) -> Invoice:
    """Create + finalise a non-taxable OPEN invoice with balance_due == amount.

    Mirrors tests/test_invoice_void.py::_open_invoice so the seeding stays
    consistent with the rest of the money-path suite.
    """
    inv = Invoice(
        invoice_number=f"INV-PAY-{next(_seq):05d}",
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


# ── 1. record_payment — unapplied credit + validation ─────────────────────────

def test_record_payment_with_no_invoices_sits_as_unapplied_credit(db):
    cust = _customer(db)
    pmt = _svc(db).record_payment(
        customer_id=cust.id, amount_received=100.0,
        payment_method=PaymentMethod.CASH, data={}, invoice_ids=None,
    )
    assert pmt.status == PaymentStatus.APPLIED
    assert pmt.amount_received == 100.0
    # No allocations → the whole payment is unapplied customer credit.
    assert pmt.amount_unallocated == 100.0
    assert _svc(db).get_unapplied_balance(cust.id) == 100.0


def test_record_payment_rejects_non_positive_amount(db):
    cust = _customer(db)
    with pytest.raises(ValueError):
        _svc(db).record_payment(cust.id, 0.0, PaymentMethod.CASH, {})
    with pytest.raises(ValueError):
        _svc(db).record_payment(cust.id, -25.0, PaymentMethod.CASH, {})


# ── 2. Allocation across two invoices (auto-spread via invoice_ids) ───────────

def test_record_payment_auto_spreads_across_two_invoices(db):
    cust = _customer(db)
    inv1 = _open_invoice(db, cust.id, 100.0)
    inv2 = _open_invoice(db, cust.id, 150.0)

    pmt = _svc(db).record_payment(
        customer_id=cust.id, amount_received=250.0,
        payment_method=PaymentMethod.CHECK,
        data={"check_number": "5001"},
        invoice_ids=[inv1.id, inv2.id],
    )

    inv1 = _reget_invoice(db, inv1.id)
    inv2 = _reget_invoice(db, inv2.id)
    assert inv1.status == InvoiceStatus.PAID and inv1.balance_due == 0.0
    assert inv2.status == InvoiceStatus.PAID and inv2.balance_due == 0.0

    pmt = _reget_payment(db, pmt.id)
    assert pmt.amount_unallocated == 0.0
    assert _svc(db).get_unapplied_balance(cust.id) == 0.0
    active = [a for a in pmt.allocations if not a.is_reversed]
    assert sorted(round(a.amount_applied, 2) for a in active) == [100.0, 150.0]


# ── 3. Allocation across two invoices (manual allocate()) + guards ────────────

def test_manual_allocate_across_two_invoices(db):
    cust = _customer(db)
    inv1 = _open_invoice(db, cust.id, 100.0)
    inv2 = _open_invoice(db, cust.id, 150.0)

    # Record the cash unapplied, then hand-allocate it.
    pmt = _svc(db).record_payment(cust.id, 250.0, PaymentMethod.CHECK, {})
    assert _reget_payment(db, pmt.id).amount_unallocated == 250.0

    _svc(db).allocate(pmt.id, inv1.id, 100.0)
    _svc(db).allocate(pmt.id, inv2.id, 150.0)

    assert _reget_invoice(db, inv1.id).status == InvoiceStatus.PAID
    assert _reget_invoice(db, inv2.id).status == InvoiceStatus.PAID
    assert _reget_payment(db, pmt.id).amount_unallocated == 0.0
    assert _svc(db).get_unapplied_balance(cust.id) == 0.0


def test_allocate_over_invoice_balance_is_rejected(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 80.0)
    pmt = _svc(db).record_payment(cust.id, 500.0, PaymentMethod.CASH, {})
    # Cannot allocate more than the invoice owes, even with plenty unapplied.
    with pytest.raises(ValueError):
        _svc(db).allocate(pmt.id, inv.id, 80.01)


def test_allocate_over_unapplied_balance_is_rejected(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 500.0)
    pmt = _svc(db).record_payment(cust.id, 50.0, PaymentMethod.CASH, {})
    # Plenty of invoice balance, but the payment only has $50 left to give.
    with pytest.raises(ValueError):
        _svc(db).allocate(pmt.id, inv.id, 50.01)


def test_partial_allocation_marks_invoice_partial(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 200.0)
    pmt = _svc(db).record_payment(cust.id, 75.0, PaymentMethod.CASH, {},
                                  invoice_ids=[inv.id])
    inv = _reget_invoice(db, inv.id)
    assert inv.status == InvoiceStatus.PARTIAL
    assert inv.amount_paid == 75.0
    assert inv.balance_due == 125.0


# ── 4. Reversal reopens the invoice ───────────────────────────────────────────

def test_reverse_payment_reopens_invoice_and_restores_balance(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 200.0)
    pmt = _svc(db).record_payment(cust.id, 200.0, PaymentMethod.CHECK,
                                  {"check_number": "6001"}, invoice_ids=[inv.id])
    assert _reget_invoice(db, inv.id).status == InvoiceStatus.PAID

    _svc(db).reverse_payment(pmt.id, "stop payment")

    pmt = _reget_payment(db, pmt.id)
    assert pmt.status == PaymentStatus.REVERSED
    assert pmt.reversed_at is not None
    assert pmt.reversal_reason == "stop payment"
    assert all(a.is_reversed for a in pmt.allocations)

    inv = _reget_invoice(db, inv.id)
    assert inv.status == InvoiceStatus.OPEN, "reversed payment must re-open the invoice"
    assert inv.amount_paid == 0.0
    assert inv.balance_due == 200.0


def test_reverse_already_reversed_payment_is_rejected(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 100.0)
    pmt = _svc(db).record_payment(cust.id, 100.0, PaymentMethod.CASH, {},
                                  invoice_ids=[inv.id])
    _svc(db).reverse_payment(pmt.id, "first")
    with pytest.raises(ValueError):
        _svc(db).reverse_payment(pmt.id, "second")


# ── 5. Account credit: apply draws balance; reverse restores it (R2) ──────────

def test_apply_account_credit_draws_balance_and_pays_invoice(db):
    cust = _customer(db, credit=200.0)
    inv = _open_invoice(db, cust.id, 80.0)

    alloc = _svc(db).apply_account_credit(cust.id, inv.id, 80.0)

    db.expire_all()
    cust = db.query(Customer).filter(Customer.id == cust.id).first()
    assert cust.credit_balance == 120.0, "credit_balance must drop by the applied amount"
    inv = _reget_invoice(db, inv.id)
    assert inv.status == InvoiceStatus.PAID and inv.balance_due == 0.0
    pmt = _reget_payment(db, alloc.payment_id)
    assert pmt.payment_method == PaymentMethod.ACCOUNT_CREDIT
    assert pmt.status == PaymentStatus.APPLIED


def test_apply_account_credit_over_balance_is_rejected(db):
    cust = _customer(db, credit=10.0)
    inv = _open_invoice(db, cust.id, 100.0)
    with pytest.raises(ValueError):
        _svc(db).apply_account_credit(cust.id, inv.id, 50.0)  # only $10 of credit


def test_reverse_account_credit_payment_restores_credit_balance(db):
    """R2 — reversing a payment that was drawn FROM credit_balance must put the
    money back so the customer is made whole."""
    cust = _customer(db, credit=300.0)
    inv = _open_invoice(db, cust.id, 120.0)
    alloc = _svc(db).apply_account_credit(cust.id, inv.id, 120.0)
    pmt_id = alloc.payment_id

    db.expire_all()
    assert db.query(Customer).filter(Customer.id == cust.id).first().credit_balance == 180.0

    _svc(db).reverse_payment(pmt_id, "credit applied in error")

    db.expire_all()
    cust = db.query(Customer).filter(Customer.id == cust.id).first()
    assert cust.credit_balance == 300.0, "reversal must restore the drawn-down credit"
    inv = _reget_invoice(db, inv.id)
    assert inv.status == InvoiceStatus.OPEN and inv.balance_due == 120.0


# ── 6. NSF — reverse + create an NSF-fee invoice ──────────────────────────────

def test_process_nsf_marks_payment_and_creates_fee_invoice(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 200.0)
    pmt = _svc(db).record_payment(cust.id, 200.0, PaymentMethod.CHECK,
                                  {"check_number": "7777"}, invoice_ids=[inv.id])
    assert _reget_invoice(db, inv.id).status == InvoiceStatus.PAID

    nsf_inv = _svc(db).process_nsf(pmt.id, 35.0)
    nsf_inv_id = nsf_inv.id

    # The bounced payment is flagged NSF and its allocation reversed.
    pmt = _reget_payment(db, pmt.id)
    assert pmt.status == PaymentStatus.NSF
    assert pmt.nsf_fee == 35.0
    assert all(a.is_reversed for a in pmt.allocations)

    # The original invoice re-opens (the money it "collected" bounced).
    inv = _reget_invoice(db, inv.id)
    assert inv.status == InvoiceStatus.OPEN and inv.balance_due == 200.0

    # A brand-new NSF-fee invoice exists for the same customer at the fee amount.
    nsf_inv = db.query(Invoice).filter(Invoice.id == nsf_inv_id).first()
    assert nsf_inv.customer_id == cust.id
    assert nsf_inv.total == 35.0
    fee_lines = [ln for ln in nsf_inv.lines if ln.line_type == LineType.NSF_FEE]
    assert len(fee_lines) == 1 and fee_lines[0].unit_price == 35.0

    # An NSF audit row was written against the original payment.
    nsf_audit = (db.query(AuditLog)
                 .filter(AuditLog.entity_type == EntityType.PAYMENT,
                         AuditLog.entity_id == pmt.id,
                         AuditLog.action == AuditAction.NSF)
                 .first())
    assert nsf_audit is not None


# ── 7. Overpayment becomes allocatable unapplied credit ───────────────────────

def test_overpayment_becomes_allocatable_unapplied_credit(db):
    """A payment larger than the invoice it's applied to leaves the surplus as
    UNAPPLIED payment credit (PaymentService.get_unapplied_balance), which can
    then be allocated to other invoices.

    NOTE — in this system overpayment does NOT post to customer.credit_balance;
    that field is reserved for issued credits (cores, warranties, RA, refund
    reversals). This test pins both halves so a future "auto-convert overpay to
    credit_balance" change can't slip through unnoticed.
    """
    cust = _customer(db)
    inv1 = _open_invoice(db, cust.id, 100.0)

    pmt = _svc(db).record_payment(cust.id, 150.0, PaymentMethod.CASH, {},
                                  invoice_ids=[inv1.id])

    inv1 = _reget_invoice(db, inv1.id)
    assert inv1.status == InvoiceStatus.PAID and inv1.amount_paid == 100.0
    pmt = _reget_payment(db, pmt.id)
    assert pmt.amount_unallocated == 50.0
    assert _svc(db).get_unapplied_balance(cust.id) == 50.0

    # The surplus is real money on account — it pays a later invoice.
    inv2 = _open_invoice(db, cust.id, 40.0)
    _svc(db).allocate(pmt.id, inv2.id, 40.0)
    assert _reget_invoice(db, inv2.id).status == InvoiceStatus.PAID
    assert _reget_payment(db, pmt.id).amount_unallocated == 10.0
    assert _svc(db).get_unapplied_balance(cust.id) == 10.0

    # ...and it did NOT touch the discrete credit_balance ledger.
    assert db.query(Customer).filter(Customer.id == cust.id).first().credit_balance == 0.0


# ── 8. HTTP contract — record + reverse through the routes ────────────────────

def test_http_record_then_reverse_payment(client, db):
    """End-to-end through the real routes: proves the form contract and that the
    signed-out test actor (DEFAULT admin) can reverse."""
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 90.0)

    r = client.post("/payments/new", data={
        "customer_id": str(cust.id),
        "amount": "90.00",
        "method": PaymentMethod.CHECK.value,
        "check_number": "8001",
        "invoice_ids": [str(inv.id)],
    }, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    location = r.headers["location"]
    assert "/payments/" in location
    pmt_id = int(location.split("/payments/")[1].split("?")[0])

    # Invoice is paid through the HTTP path.
    assert _reget_invoice(db, inv.id).status == InvoiceStatus.PAID

    # Detail page renders (template smoke).
    assert client.get(f"/payments/{pmt_id}").status_code == 200

    # Reverse through the route.
    r = client.post(f"/payments/{pmt_id}/reverse", data={"reason": "bounced"},
                    follow_redirects=False)
    assert r.status_code == 303

    pmt = _reget_payment(db, pmt_id)
    assert pmt.status == PaymentStatus.REVERSED
    assert _reget_invoice(db, inv.id).status == InvoiceStatus.OPEN
