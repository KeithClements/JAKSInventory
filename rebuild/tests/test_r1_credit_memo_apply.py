"""
tests/test_r1_credit_memo_apply.py
==================================
R1-4 — credit memos must be financially ACTIONABLE, not inert.

Route-level coverage for the new apply/close surface wired onto the
(already-implemented) CreditMemoService:

  POST /credit-memos/{id}/apply   — allocate to an open invoice (same customer)
  POST /credit-memos/{id}/close   — push remainder to customer.credit_balance

Verifies: apply reduces invoice balance_due + CM unapplied; blank amount
defaults to min(remaining, invoice balance); over-apply and cross-customer
invoices are rejected; close moves the remainder to credit_balance and marks
the CM APPLIED; idempotency guards (re-close no-op, apply-after-APPLIED
rejected) hold.

A module TestClient runs app startup (admin user #1 → ISSUE_CREDIT_MEMO via
the ADMIN role under the in-memory test bypass).
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
_UID = 1
_SEQ = {"n": 0}


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


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"R1 Cust {_uniq()}", contact_name="QA",
                 email=f"r1c{_uniq()}@test.local", is_active=True)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, price: float = 100.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"R1-{_uniq():06d}", title="R1 Part", description="R1 Part",
                cost=40.0, price_override=price, qty_on_hand=100,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _finalized_invoice(db, customer, price: float = 100.0, qty: int = 1):
    """Build → add a line → finalise FOR the given customer. Returns OPEN invoice."""
    from app.constants import InvoiceStatus
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService
    prod = _product(db, price=price)
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(customer.id)
    svc.add_line(inv.id, prod.id, {"qty": qty})
    svc.finalise(inv.id)
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.status in (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)
    return inv


def _credit_memo(db, customer, amount: float):
    """Issue a manual CM directly through the service (creation is not under test)."""
    from app.constants import CreditMemoTrigger
    from app.services.credit_memo_service import CreditMemoService
    return CreditMemoService(db, _UID).create_credit_memo(
        customer_id=customer.id,
        trigger_type=CreditMemoTrigger.MANUAL,
        lines=[{"description": "R1 test credit", "qty": 1, "unit_price": amount}],
        reason="r1 apply/close coverage",
    )


def _reload_cm(db, cm_id: int):
    from app.models.credit_memo import CreditMemo
    db.expire_all()
    return db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()


def _reload_inv(db, inv_id: int):
    from app.models.invoice import Invoice
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv_id).first()


# ── Apply ─────────────────────────────────────────────────────────────────────

def test_apply_reduces_invoice_balance_and_cm_remaining(client, db):
    from app.constants import CreditMemoStatus
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, price=100.0)   # balance 100
    cm = _credit_memo(db, cust, 60.0)

    resp = client.post(
        f"/credit-memos/{cm.id}/apply",
        data={"invoice_id": str(inv.id), "amount": "25"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == f"/credit-memos/{cm.id}?saved=1"

    cm2 = _reload_cm(db, cm.id)
    inv2 = _reload_inv(db, inv.id)
    assert cm2.applied_amount == 25.0
    assert cm2.unapplied_amount == 35.0
    assert cm2.status == CreditMemoStatus.PARTIAL
    assert inv2.balance_due == 75.0, "allocation must reduce the invoice balance"


def test_apply_blank_amount_defaults_to_min(client, db):
    """Blank amount → min(CM remaining, invoice balance). CM bigger than invoice."""
    from app.constants import CreditMemoStatus, InvoiceStatus
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, price=100.0)   # balance 100
    cm = _credit_memo(db, cust, 150.0)

    resp = client.post(
        f"/credit-memos/{cm.id}/apply",
        data={"invoice_id": str(inv.id), "amount": ""},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert resp.headers["location"].endswith("?saved=1")

    cm2 = _reload_cm(db, cm.id)
    inv2 = _reload_inv(db, inv.id)
    assert cm2.applied_amount == 100.0
    assert cm2.unapplied_amount == 50.0
    assert cm2.status == CreditMemoStatus.PARTIAL
    assert inv2.balance_due == 0.0
    assert inv2.status == InvoiceStatus.PAID


def test_over_apply_rejected(client, db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, price=100.0)
    cm = _credit_memo(db, cust, 40.0)

    resp = client.post(
        f"/credit-memos/{cm.id}/apply",
        data={"invoice_id": str(inv.id), "amount": "55"},  # > CM's 40 unapplied
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert f"/credit-memos/{cm.id}?error=" in resp.headers["location"]

    cm2 = _reload_cm(db, cm.id)
    inv2 = _reload_inv(db, inv.id)
    assert cm2.applied_amount == 0.0
    assert cm2.unapplied_amount == 40.0
    assert inv2.balance_due == 100.0, "rejected apply must not move money"


def test_apply_beyond_invoice_balance_rejected(client, db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, price=50.0)    # balance 50
    cm = _credit_memo(db, cust, 200.0)

    resp = client.post(
        f"/credit-memos/{cm.id}/apply",
        data={"invoice_id": str(inv.id), "amount": "75"},  # > invoice's 50 due
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert f"/credit-memos/{cm.id}?error=" in resp.headers["location"]

    cm2 = _reload_cm(db, cm.id)
    inv2 = _reload_inv(db, inv.id)
    assert cm2.unapplied_amount == 200.0
    assert inv2.balance_due == 50.0


def test_cross_customer_invoice_rejected(client, db):
    cust_a = _customer(db)
    cust_b = _customer(db)
    inv_b = _finalized_invoice(db, cust_b, price=100.0)
    cm_a = _credit_memo(db, cust_a, 60.0)

    resp = client.post(
        f"/credit-memos/{cm_a.id}/apply",
        data={"invoice_id": str(inv_b.id), "amount": "60"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert f"/credit-memos/{cm_a.id}?error=" in resp.headers["location"]

    cm2 = _reload_cm(db, cm_a.id)
    inv2 = _reload_inv(db, inv_b.id)
    assert cm2.applied_amount == 0.0, "cross-customer apply must be rejected"
    assert inv2.balance_due == 100.0


def test_apply_without_invoice_rejected(client, db):
    cust = _customer(db)
    cm = _credit_memo(db, cust, 30.0)

    resp = client.post(
        f"/credit-memos/{cm.id}/apply", data={}, follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert f"/credit-memos/{cm.id}?error=" in resp.headers["location"]
    assert _reload_cm(db, cm.id).unapplied_amount == 30.0


# ── Close ─────────────────────────────────────────────────────────────────────

def test_close_moves_remainder_to_credit_balance(client, db):
    from app.constants import CreditMemoStatus
    from app.models.customer import Customer
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, price=100.0)
    cm = _credit_memo(db, cust, 80.0)

    # Apply 30 first, then close — only the 50 residual goes to credit_balance.
    client.post(f"/credit-memos/{cm.id}/apply",
                data={"invoice_id": str(inv.id), "amount": "30"},
                follow_redirects=False)

    resp = client.post(f"/credit-memos/{cm.id}/close", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == f"/credit-memos/{cm.id}?saved=1"

    cm2 = _reload_cm(db, cm.id)
    cust2 = db.query(Customer).filter(Customer.id == cust.id).first()
    assert cm2.status == CreditMemoStatus.APPLIED
    assert cm2.unapplied_amount == 0.0
    assert cm2.applied_amount == 30.0
    assert cust2.credit_balance == 50.0, "residual must land on customer credit_balance"


def test_close_idempotent_no_double_credit(client, db):
    from app.constants import CreditMemoStatus
    from app.models.customer import Customer
    cust = _customer(db)
    cm = _credit_memo(db, cust, 45.0)

    r1 = client.post(f"/credit-memos/{cm.id}/close", follow_redirects=False)
    r2 = client.post(f"/credit-memos/{cm.id}/close", follow_redirects=False)
    assert r1.status_code in (302, 303) and r2.status_code in (302, 303)
    # Second close is a service-level no-op — still a friendly redirect, no error.
    assert r2.headers["location"] == f"/credit-memos/{cm.id}?saved=1"

    cm2 = _reload_cm(db, cm.id)
    cust2 = db.query(Customer).filter(Customer.id == cust.id).first()
    assert cm2.status == CreditMemoStatus.APPLIED
    assert cust2.credit_balance == 45.0, "re-close must not credit twice"


def test_apply_after_applied_rejected(client, db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, price=100.0)
    cm = _credit_memo(db, cust, 20.0)
    client.post(f"/credit-memos/{cm.id}/close", follow_redirects=False)

    resp = client.post(
        f"/credit-memos/{cm.id}/apply",
        data={"invoice_id": str(inv.id), "amount": "10"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert f"/credit-memos/{cm.id}?error=" in resp.headers["location"]
    assert _reload_inv(db, inv.id).balance_due == 100.0


def test_routes_missing_cm_redirect_to_list(client):
    for path in ("/credit-memos/99999/apply", "/credit-memos/99999/close"):
        resp = client.post(path, data={"invoice_id": "1"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert resp.headers["location"] == "/credit-memos/"


# ── Detail page surface ───────────────────────────────────────────────────────

def test_detail_shows_apply_form_when_open(client, db):
    cust = _customer(db)
    _finalized_invoice(db, cust, price=100.0)
    cm = _credit_memo(db, cust, 60.0)

    page = client.get(f"/credit-memos/{cm.id}")
    assert page.status_code == 200
    assert "Apply Credit" in page.text
    assert "Close to Account Credit" in page.text
    assert f"/credit-memos/{cm.id}/apply" in page.text
    assert f"/credit-memos/{cm.id}/close" in page.text


def test_detail_hides_apply_form_when_applied(client, db):
    cust = _customer(db)
    _finalized_invoice(db, cust, price=100.0)
    cm = _credit_memo(db, cust, 60.0)
    client.post(f"/credit-memos/{cm.id}/close", follow_redirects=False)

    page = client.get(f"/credit-memos/{cm.id}")
    assert page.status_code == 200
    assert "Apply Credit" not in page.text
    assert "Close to Account Credit" not in page.text
