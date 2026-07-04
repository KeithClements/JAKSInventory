"""
tests/test_c2_draft_payment_gate.py
===================================
C2 regression net — a DRAFT invoice must never accept money.

WHY THIS EXISTS (FULL_ERP_REVIEW_2026-07-04, finding C2)
--------------------------------------------------------
POST /invoices/{id}/payment on a DRAFT invoice used to flip it straight to
status=paid + locked(reason=paid) while bypassing the entire finalize gate:
qty_on_hand never decremented, per-line tax_amount never frozen, no
INVOICE_SALE ledger row, and qbo_sync_status=pending queued a never-finalized
invoice for QBO push. Recovery was hard because void_invoice rejects PAID.

The gate now lives at the service layer (finalize is the money+stock gate):
  - PaymentService._assert_invoice_payable  — record_payment / allocate /
    apply_account_credit reject DRAFT and VOID invoices
  - CreditMemoService.apply_credit_memo     — rejects DRAFT (it builds its own
    synthetic Payment, so the PaymentService gate alone can't cover it)
  - InvoiceService.refresh_payment_status   — backstop: raises on DRAFT rather
    than silently flipping it to PAID; returns without mutation on VOID so
    payment math can never resurrect a voided invoice

Every test here pins one of those doors shut, plus one no-regression check
that paying a properly finalized invoice still lands PAID+locked.

Harness: in-memory isolation engine from tests/conftest.py, seeded like
tests/test_payments.py. _UID = 1 is the startup-seeded admin.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_c2_draft_payment_gate.py -v
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
    EntityType, InventoryTxnType, InvoiceStatus, LineType,
    PaymentMethod, ProductStatus,
)
from app.main import app
from app.models.customer import Customer
from app.models.inventory import InventoryTransaction
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.models.product import Product
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # admin, seeded by the startup hook
_seq = itertools.count(1)

STOCK = 10  # qty_on_hand seeded on every test product


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
    n = next(_seq)
    c = Customer(company_name=f"C2 Test Co {n}", contact_name="QA",
                 email=f"c2-{n}@test.local", credit_balance=credit)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db) -> Product:
    n = next(_seq)
    p = Product(sku=f"C2-{n:05d}", title=f"C2 Part {n}", description="C2 Part",
                cost=60.0, last_cost=60.0, markup_pct=50.0, qty_on_hand=STOCK,
                qty_committed=0, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _draft_invoice(db, customer_id: int, product: Product, amount: float = 100.0) -> Invoice:
    """A DRAFT invoice with one taxable product line — finalise() never ran,
    so tax_amount is unfrozen (0.0) and stock is untouched."""
    inv = Invoice(
        invoice_number=f"INV-C2-{next(_seq):05d}",
        customer_id=customer_id,
        status=InvoiceStatus.DRAFT,
        is_taxable=True,
        tax_rate=8.25,
        tax_rate_snapshot=8.25,
        tax_exempt_snapshot=False,
    )
    db.add(inv); db.flush()
    db.add(InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        product_id=product.id,
        description="C2 test part",
        qty=1,
        unit_price=amount,
        unit_cost=60.0,
        is_taxable=True,
    ))
    db.commit()
    db.refresh(inv)
    return inv


def _reget(db, model, obj_id: int):
    db.expire_all()
    return db.query(model).filter(model.id == obj_id).first()


def _assert_draft_untouched(db, inv_id: int, product_id: int):
    """The full C2 damage checklist: status, lock, stock, tax, ledger, money."""
    inv = _reget(db, Invoice, inv_id)
    assert inv.status == InvoiceStatus.DRAFT
    assert inv.locked_at is None and inv.lock_reason is None
    assert all(ln.tax_amount == 0.0 for ln in inv.lines)
    assert inv.amount_paid == 0.0

    product = _reget(db, Product, product_id)
    assert product.qty_on_hand == STOCK

    sale_txns = (
        db.query(InventoryTransaction)
        .filter(
            InventoryTransaction.transaction_type == InventoryTxnType.INVOICE_SALE,
            InventoryTransaction.reference_type == EntityType.INVOICE,
            InventoryTransaction.reference_id == inv_id,
        )
        .count()
    )
    assert sale_txns == 0

    allocs = db.query(PaymentAllocation).filter(PaymentAllocation.invoice_id == inv_id).count()
    assert allocs == 0


# ── 1. The reported repro — HTTP payment on a draft ──────────────────────────

def test_http_payment_on_draft_is_refused_and_leaves_state_untouched(client, db):
    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)
    payments_before = db.query(Payment).count()

    resp = client.post(
        f"/invoices/{inv.id}/payment",
        data={"amount": "108.25", "method": PaymentMethod.CASH},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "draft" in resp.headers["location"].lower()

    _assert_draft_untouched(db, inv.id, product.id)
    # Router rolled back — the flushed Payment row must not have been committed.
    assert db.query(Payment).count() == payments_before


def test_http_apply_credit_on_draft_is_refused(client, db):
    cust = _customer(db, credit=500.0)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)

    resp = client.post(
        f"/invoices/{inv.id}/apply-credit",
        data={"amount": "100.0"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    _assert_draft_untouched(db, inv.id, product.id)
    assert _reget(db, Customer, cust.id).credit_balance == 500.0


# ── 2. Service-layer doors ────────────────────────────────────────────────────

def test_record_payment_rejects_draft_invoice(db):
    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)

    with pytest.raises(ValueError, match="draft"):
        PaymentService(db, _UID).record_payment(
            customer_id=cust.id, amount_received=100.0,
            payment_method=PaymentMethod.CASH, data={}, invoice_ids=[inv.id],
        )
    db.rollback()
    _assert_draft_untouched(db, inv.id, product.id)


def test_allocate_rejects_draft_invoice_and_payment_stays_unapplied(db):
    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)
    svc = PaymentService(db, _UID)
    pmt = svc.record_payment(  # unapplied credit — legal
        customer_id=cust.id, amount_received=100.0,
        payment_method=PaymentMethod.CASH, data={}, invoice_ids=None,
    )

    with pytest.raises(ValueError, match="draft"):
        svc.allocate(pmt.id, inv.id, 100.0)
    db.rollback()

    _assert_draft_untouched(db, inv.id, product.id)
    assert _reget(db, Payment, pmt.id).amount_unallocated == 100.0


def test_apply_account_credit_rejects_draft_invoice(db):
    cust = _customer(db, credit=500.0)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)

    with pytest.raises(ValueError, match="draft"):
        PaymentService(db, _UID).apply_account_credit(
            customer_id=cust.id, invoice_id=inv.id, amount=100.0,
        )
    db.rollback()

    _assert_draft_untouched(db, inv.id, product.id)
    assert _reget(db, Customer, cust.id).credit_balance == 500.0


def test_apply_credit_memo_rejects_draft_invoice(db):
    from app.constants import CreditMemoTrigger
    from app.services.credit_memo_service import CreditMemoService

    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)

    cm_svc = CreditMemoService(db, _UID)
    cm = cm_svc.create_credit_memo(
        customer_id=cust.id,
        trigger_type=CreditMemoTrigger.MANUAL,
        lines=[{"description": "C2 CM", "qty": 1, "unit_price": 100.0}],
        reason="c2 regression",
    )

    with pytest.raises(ValueError, match="draft"):
        cm_svc.apply_credit_memo(cm.id, inv.id, 100.0)
    db.rollback()
    _assert_draft_untouched(db, inv.id, product.id)


# ── 3. Backstop — refresh_payment_status itself ──────────────────────────────

def test_refresh_payment_status_raises_on_draft(db):
    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)

    with pytest.raises(ValueError, match="draft"):
        InvoiceService(db, _UID).refresh_payment_status(inv.id)
    db.rollback()
    _assert_draft_untouched(db, inv.id, product.id)


def test_refresh_payment_status_never_resurrects_void(db):
    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)
    inv_svc = InvoiceService(db, _UID)
    inv_svc.finalise(inv.id)
    inv_svc.void_invoice(inv.id, "c2 regression")

    inv_svc.refresh_payment_status(inv.id)  # must be a no-op, not OPEN/PAID
    assert _reget(db, Invoice, inv.id).status == InvoiceStatus.VOID


def test_void_invoice_rejects_payment(db):
    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)
    inv_svc = InvoiceService(db, _UID)
    inv_svc.finalise(inv.id)
    inv_svc.void_invoice(inv.id, "c2 regression")

    with pytest.raises(ValueError, match="void"):
        PaymentService(db, _UID).record_payment(
            customer_id=cust.id, amount_received=50.0,
            payment_method=PaymentMethod.CASH, data={}, invoice_ids=[inv.id],
        )
    db.rollback()


# ── 4. No regression — the legal path still works ─────────────────────────────

def test_payment_on_finalized_invoice_still_pays_and_locks(client, db):
    cust = _customer(db)
    product = _product(db)
    inv = _draft_invoice(db, cust.id, product)
    InvoiceService(db, _UID).finalise(inv.id)
    inv = _reget(db, Invoice, inv.id)
    assert inv.status == InvoiceStatus.OPEN
    assert _reget(db, Product, product.id).qty_on_hand == STOCK - 1

    resp = client.post(
        f"/invoices/{inv.id}/payment",
        data={"amount": f"{inv.balance_due:.2f}", "method": PaymentMethod.CASH},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "saved=1" in resp.headers["location"]

    inv = _reget(db, Invoice, inv.id)
    assert inv.status == InvoiceStatus.PAID
    assert inv.is_locked
    assert inv.balance_due <= 0.001
