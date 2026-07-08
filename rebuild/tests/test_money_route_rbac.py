"""
tests/test_money_route_rbac.py
==============================
Money-path RBAC pinned THROUGH the routes.

The invoice-finalise / payment-reverse / issue-credit-memo permission gates
live in the services (InvoiceService.finalise → FINALIZE_INVOICE,
PaymentService.reverse_payment → REVERSE_PAYMENT,
CreditMemoService.create_credit_memo → ISSUE_CREDIT_MEMO), and the routes feed
them the acting user id from get_current_user_id. This module drives the FULL
route → service chain with a REAL seeded SALES User row and a signed session
cookie, so a regression anywhere in that chain — a route dropping its user_id
dependency (the customer-import defect), a service losing its assert_can, a
role gaining a permission it must not have — turns a denial into a success and
trips a red here.

RBAC enforcement note: BaseService.assert_can always role-enforces a user id
that maps to a REAL User row, even under JAKS_SKIP_AUTH (the env var only
bypasses the login middleware/CSRF and unknown-stub-actor ids). So these tests
run in the normal suite env; the cookie carries the real uid to the route.

Asserted per route (SALES):  non-500 response AND no state change.
Asserted for ADMIN:          finalize actually succeeds (control — proves the
                             denials above are role-based, not seed breakage).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_money_route_rbac.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE, hash_password, make_session_token
from app.constants import (
    InvoiceStatus, LineType, PaymentMethod, PaymentStatus, UserRole,
)
from app.main import app
from app.models.credit_memo import CreditMemo
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.models.user import User
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)

# Route templates under test — verified registered so a denial can never
# false-pass on a 404/405 (mirrors tests/test_auth_enforcement.py).
_MONEY_ROUTES = [
    ("POST", "/invoices/{invoice_id}/finalise"),
    ("POST", "/invoices/{invoice_id}/issue-credit-memo"),
    ("POST", "/payments/{payment_id}/reverse"),
]


# ── Seeding helpers ───────────────────────────────────────────────────────────

def _seed_user(db, role: str, username: str) -> int:
    u = User(
        name=username,
        username=username,
        password_hash=hash_password(f"{username}-pw-not-default"),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u.id


def _draft_invoice(db, customer_id: int, amount: float) -> int:
    """DRAFT invoice with one non-product line (no inventory involvement)."""
    inv = Invoice(
        invoice_number=f"INV-RBAC-{next(_seq):05d}",
        customer_id=customer_id,
        status=InvoiceStatus.DRAFT,
        tax_rate=0.0,
        tax_rate_snapshot=0.0,
        tax_exempt_snapshot=True,
    )
    db.add(inv)
    db.flush()
    db.add(InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="RBAC test part",
        qty=1,
        unit_price=amount,
        unit_cost=amount * 0.6,
        is_taxable=False,
    ))
    db.commit()
    return inv.id


def _open_invoice(db, customer_id: int, amount: float) -> int:
    """Finalised (OPEN) invoice — seeded as the SYSTEM actor (None), which
    assert_can always allows, so seeding never depends on any User row."""
    inv_id = _draft_invoice(db, customer_id, amount)
    InvoiceService(db, None).finalise(inv_id)
    return inv_id


@pytest.fixture(scope="module")
def ctx():
    """Module engine + seeded users and documents. Returns everything by id so
    each test re-reads fresh state from its own session."""
    SessionLocal = activate(_ENGINE)
    db = SessionLocal()
    try:
        sales_id = _seed_user(db, UserRole.SALES, "rbac-clerk")
        admin_id = _seed_user(db, UserRole.ADMIN, "rbac-admin")

        cust = Customer(company_name="RBAC Money Co", email="rbac@money.test")
        db.add(cust)
        db.commit()

        draft_deny_id = _draft_invoice(db, cust.id, 100.0)   # SALES finalize probe
        draft_allow_id = _draft_invoice(db, cust.id, 120.0)  # ADMIN finalize control
        cm_target_id = _open_invoice(db, cust.id, 60.0)      # SALES credit-memo probe

        paid_inv_id = _open_invoice(db, cust.id, 80.0)       # SALES reverse probe
        pmt = PaymentService(db, None).record_payment(
            customer_id=cust.id,
            amount_received=80.0,
            payment_method=PaymentMethod.CASH,
            data={},
            invoice_ids=[paid_inv_id],
        )
        payment_id = pmt.id

        return {
            "SessionLocal": SessionLocal,
            "sales_id": sales_id,
            "admin_id": admin_id,
            "draft_deny_id": draft_deny_id,
            "draft_allow_id": draft_allow_id,
            "cm_target_id": cm_target_id,
            "paid_inv_id": paid_inv_id,
            "payment_id": payment_id,
        }
    finally:
        db.close()


@pytest.fixture()
def client(ctx):
    # raise_server_exceptions=False: a 500 must be observed as a failure, not
    # masked as a python traceback; follow_redirects=False: the 303 IS the outcome.
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture()
def db(ctx):
    s = ctx["SessionLocal"]()
    try:
        yield s
    finally:
        s.close()


def _as(client: TestClient, user_id: int) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, make_session_token(user_id))


# ── 0. Route sanity ───────────────────────────────────────────────────────────

def test_money_routes_are_registered():
    registered = {
        (m, getattr(r, "path", None))
        for r in app.routes
        for m in (getattr(r, "methods", None) or set())
    }
    missing = [(m, p) for m, p in _MONEY_ROUTES if (m, p) not in registered]
    assert missing == [], f"money routes moved/renamed — denial tests would false-pass: {missing}"


# ── 1. SALES → finalize denied, invoice stays DRAFT ───────────────────────────

def test_sales_cannot_finalize_invoice(ctx, client, db):
    _as(client, ctx["sales_id"])
    inv_id = ctx["draft_deny_id"]

    r = client.post(f"/invoices/{inv_id}/finalise", data={})
    assert r.status_code < 500, f"finalize 500'd for SALES: {r.status_code}"
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", ""), (
        f"SALES finalize did not bounce with an error: {r.headers.get('location')!r}")

    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    assert inv.status == InvoiceStatus.DRAFT, (
        "SALES user FINALIZED an invoice through the route — "
        "POST /invoices/{id}/finalise is not enforcing FINALIZE_INVOICE")


# ── 2. SALES → payment reverse denied, payment stays APPLIED ──────────────────

def test_sales_cannot_reverse_payment(ctx, client, db):
    _as(client, ctx["sales_id"])
    pmt_id = ctx["payment_id"]

    r = client.post(f"/payments/{pmt_id}/reverse", data={"reason": "should be denied"})
    assert r.status_code < 500, f"reverse 500'd for SALES: {r.status_code}"
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", ""), (
        f"SALES reverse did not bounce with an error: {r.headers.get('location')!r}")

    db.expire_all()
    pmt = db.query(Payment).filter(Payment.id == pmt_id).first()
    assert pmt.status == PaymentStatus.APPLIED, (
        "SALES user REVERSED a payment through the route — "
        "POST /payments/{id}/reverse is not enforcing REVERSE_PAYMENT")
    inv = db.query(Invoice).filter(Invoice.id == ctx["paid_inv_id"]).first()
    assert inv.status == InvoiceStatus.PAID, (
        "the paid invoice was reopened despite the denial — state leaked")


# ── 3. SALES → issue credit memo denied, no CM row ────────────────────────────

def test_sales_cannot_issue_credit_memo(ctx, client, db):
    _as(client, ctx["sales_id"])
    inv_id = ctx["cm_target_id"]

    r = client.post(
        f"/invoices/{inv_id}/issue-credit-memo",
        data={"reason": "should be denied"},
    )
    assert r.status_code < 500, f"issue-credit-memo 500'd for SALES: {r.status_code}"
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", ""), (
        f"SALES credit-memo did not bounce with an error: {r.headers.get('location')!r}")

    db.expire_all()
    cm_count = db.query(CreditMemo).count()
    assert cm_count == 0, (
        "SALES user ISSUED a credit memo through the route — "
        "POST /invoices/{id}/issue-credit-memo is not enforcing ISSUE_CREDIT_MEMO")


# ── 4. ADMIN control — finalize succeeds through the same chain ───────────────

def test_admin_can_finalize_invoice(ctx, client, db):
    _as(client, ctx["admin_id"])
    inv_id = ctx["draft_allow_id"]

    r = client.post(f"/invoices/{inv_id}/finalise", data={})
    assert r.status_code == 303
    assert "ok=" in r.headers.get("location", ""), (
        f"ADMIN finalize did not succeed: {r.headers.get('location')!r}")

    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    assert inv.status == InvoiceStatus.OPEN, (
        f"ADMIN finalize left the invoice {inv.status!r}, expected OPEN")
