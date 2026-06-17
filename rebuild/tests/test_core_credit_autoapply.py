"""Auto-apply of core-return account credit onto the originating invoice.

Owner decision 2026-06-16 — when a customer returns a core at drop-off, the
account credit issued for it should land straight on that core's originating
invoice (reducing Balance Due) instead of floating on the account, so the
customer only pays the parts balance. See
CoreService._auto_apply_core_credit_to_invoice.

These tests assert the FINANCIAL outcome (invoice.balance_due, status, the
ACCOUNT_CREDIT Payment + allocation, and the customer's net credit_balance) —
never just that nothing blew up.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_core_credit_autoapply.py -v
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
    CoreInspectionOutcome, InvoiceStatus, LineType, PaymentMethod, PaymentStatus,
    ProductStatus,
)
from app.main import app
from app.models.core import CoreCharge
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.models.product import Product
from app.services.core_service import CoreService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # admin, seeded by the startup hook
_seq = itertools.count(1)


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
    c = Customer(company_name=f"Core AA Co {next(_seq)}", contact_name="QA",
                 email=f"aa{next(_seq)}@test.local", credit_balance=credit)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db) -> Product:
    p = Product(sku=f"AA-{next(_seq):04d}", title="Core Part", description="Core Part",
                cost=30.0, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _open_invoice(db, customer_id: int, amount: float) -> Invoice:
    """Create + finalise a non-taxable OPEN invoice with balance_due == amount."""
    inv = Invoice(
        invoice_number=f"INV-AA-{next(_seq):05d}",
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


def _linked_core(db, *, invoice_id: int, product_id: int, qty: int,
                 cust_charge: float) -> CoreCharge:
    """A core tied to its originating invoice, exactly as finalize creates it
    (create_core_charge stamps credit_invoice_id = invoice_id)."""
    core = CoreService(db, _UID).create_core_charge(
        invoice_id=invoice_id, invoice_line_id=None, product_id=product_id,
        qty=qty, customer_unit_charge=cust_charge, vendor_unit_charge=cust_charge * 0.6,
    )
    db.commit()
    return core


def _reget_invoice(db, inv_id: int) -> Invoice:
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv_id).first()


def _credit_payments_for(db, inv_id: int) -> list[Payment]:
    """ACCOUNT_CREDIT payments allocated (live) to this invoice."""
    rows = (
        db.query(Payment)
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .filter(
            PaymentAllocation.invoice_id == inv_id,
            PaymentAllocation.is_reversed.is_(False),
            Payment.payment_method == PaymentMethod.ACCOUNT_CREDIT,
        )
        .all()
    )
    return rows


# ── 1. Full accepted return auto-applies onto the OPEN invoice ─────────────────

def test_accepted_return_autoapplies_to_open_invoice(db):
    cust = _customer(db)
    prod = _product(db)
    inv = _open_invoice(db, cust.id, amount=500.0)
    assert inv.status == InvoiceStatus.OPEN
    assert inv.balance_due == 500.0

    core = _linked_core(db, invoice_id=inv.id, product_id=prod.id, qty=1,
                        cust_charge=150.0)
    start_credit = db.query(Customer).filter(Customer.id == cust.id).first().credit_balance

    CoreService(db, _UID).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    inv = _reget_invoice(db, inv.id)
    cust = db.query(Customer).filter(Customer.id == cust.id).first()
    # $150 credit landed on the invoice → balance drops, status flips to PARTIAL.
    assert inv.balance_due == 350.0
    assert inv.status == InvoiceStatus.PARTIAL
    assert inv.amount_paid == 150.0
    # Issued (+150) then applied (-150) → net credit_balance unchanged.
    assert cust.credit_balance == start_credit
    # The application is a real ACCOUNT_CREDIT payment row against the invoice.
    pmts = _credit_payments_for(db, inv.id)
    assert len(pmts) == 1
    assert pmts[0].status == PaymentStatus.APPLIED
    assert pmts[0].amount_received == 150.0


# ── 2. Partial return applies only the returned portion ───────────────────────

def test_partial_return_autoapplies_partial(db):
    cust = _customer(db)
    prod = _product(db)
    inv = _open_invoice(db, cust.id, amount=500.0)
    core = _linked_core(db, invoice_id=inv.id, product_id=prod.id, qty=2,
                        cust_charge=150.0)

    CoreService(db, _UID).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    inv = _reget_invoice(db, inv.id)
    assert inv.balance_due == 350.0          # only 1 × $150 applied
    assert inv.status == InvoiceStatus.PARTIAL


# ── 3. Already-PAID invoice → credit floats (the pay-full-then-return flow) ────

def test_paid_invoice_leaves_floating_credit(db):
    cust = _customer(db)
    prod = _product(db)
    inv = _open_invoice(db, cust.id, amount=200.0)
    core = _linked_core(db, invoice_id=inv.id, product_id=prod.id, qty=1,
                        cust_charge=150.0)

    # Pay the invoice in full FIRST — mirrors the customer who paid for the core.
    PaymentService(db, _UID).record_payment(
        customer_id=cust.id, amount_received=200.0,
        payment_method=PaymentMethod.CASH, data={}, invoice_ids=[inv.id])
    assert _reget_invoice(db, inv.id).status == InvoiceStatus.PAID

    start_credit = db.query(Customer).filter(Customer.id == cust.id).first().credit_balance
    CoreService(db, _UID).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    inv = _reget_invoice(db, inv.id)
    cust = db.query(Customer).filter(Customer.id == cust.id).first()
    assert inv.balance_due == 0.0
    assert inv.status == InvoiceStatus.PAID            # untouched
    assert cust.credit_balance == start_credit + 150.0  # floats for later use
    assert _credit_payments_for(db, inv.id) == []


# ── 4. Core with no originating-invoice link → credit floats ──────────────────

def test_core_without_invoice_link_floats(db):
    from app.constants import CoreDirection, CoreStatus
    cust = _customer(db)
    prod = _product(db)
    # Direct construction → credit_invoice_id stays NULL (no link to apply to).
    core = CoreCharge(
        product_id=prod.id, customer_id=cust.id,
        qty_charged=1, qty_returned=0,
        customer_unit_charge=150.0, vendor_unit_charge=90.0,
        direction=CoreDirection.CUSTOMER_OWES_RETURN, status=CoreStatus.OPEN,
    )
    db.add(core); db.commit(); db.refresh(core)
    assert core.credit_invoice_id is None

    start_credit = db.query(Customer).filter(Customer.id == cust.id).first().credit_balance
    CoreService(db, _UID).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    cust = db.query(Customer).filter(Customer.id == cust.id).first()
    assert cust.credit_balance == start_credit + 150.0  # floats, nothing to apply


# ── 5. Credit larger than balance due → applies up to balance, remainder floats ─

def test_credit_capped_at_balance_due(db):
    cust = _customer(db)
    prod = _product(db)
    inv = _open_invoice(db, cust.id, amount=100.0)   # only $100 owed
    core = _linked_core(db, invoice_id=inv.id, product_id=prod.id, qty=1,
                        cust_charge=150.0)            # but $150 credit
    start_credit = db.query(Customer).filter(Customer.id == cust.id).first().credit_balance

    CoreService(db, _UID).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    inv = _reget_invoice(db, inv.id)
    cust = db.query(Customer).filter(Customer.id == cust.id).first()
    assert inv.balance_due == 0.0
    assert inv.status == InvoiceStatus.PAID
    # $100 applied, $50 remainder stays on the account.
    assert cust.credit_balance == start_credit + 50.0


# ── 6. HOLD → complete_inspection ACCEPTED also auto-applies ──────────────────

def test_complete_inspection_autoapplies(db):
    cust = _customer(db)
    prod = _product(db)
    inv = _open_invoice(db, cust.id, amount=500.0)
    core = _linked_core(db, invoice_id=inv.id, product_id=prod.id, qty=1,
                        cust_charge=150.0)

    svc = CoreService(db, _UID)
    # Received but held — credit deferred, invoice untouched.
    svc.record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.HOLD)
    assert _reget_invoice(db, inv.id).balance_due == 500.0

    # Inspection passes → deferred credit issued AND auto-applied.
    svc.complete_inspection(core.id, final_outcome=CoreInspectionOutcome.ACCEPTED)

    inv = _reget_invoice(db, inv.id)
    assert inv.balance_due == 350.0
    assert inv.status == InvoiceStatus.PARTIAL
