"""
tests/test_credit_memo_routes.py
================================
Route-level coverage for the R8 customer credit-memo surface that was wired up
on top of the (already-implemented) CreditMemoService / InvoiceService:

  POST /invoices/{id}/issue-credit-memo   — issue a CM against a finalized invoice
  GET  /credit-memos/                      — read-only list
  GET  /credit-memos/{id}                  — read-only detail

Plus the workspace button visibility rule (OPEN/PARTIAL/PAID only — never on a
DRAFT or VOID invoice).

A module TestClient runs app startup (admin user #1 → ISSUE_CREDIT_MEMO via the
ADMIN role, so the route's permission gate passes under the in-memory test
bypass). Invoices are built directly through InvoiceService.
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
    c = Customer(company_name=f"CM Cust {_uniq()}", contact_name="QA",
                 email=f"cm{_uniq()}@test.local", is_active=True)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, price: float = 100.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"CM-{_uniq():06d}", title="CM Part", description="CM Part",
                cost=40.0, price_override=price, qty_on_hand=100,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _finalized_invoice(db, price: float = 100.0, qty: int = 1):
    """Build → add a line → finalise. Returns the OPEN invoice."""
    from app.constants import InvoiceStatus
    from app.services.invoice_service import InvoiceService
    cust = _customer(db); prod = _product(db, price=price)
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": qty})
    svc.finalise(inv.id)
    db.expire_all()
    from app.models.invoice import Invoice
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.status in (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)
    return inv


def _draft_invoice(db, price: float = 100.0, qty: int = 1):
    from app.services.invoice_service import InvoiceService
    cust = _customer(db); prod = _product(db, price=price)
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": qty})
    db.commit()
    return inv


# ── Issue Credit Memo ─────────────────────────────────────────────────────────

def test_issue_credit_memo_full_invoice(client, db):
    from app.models.credit_memo import CreditMemo
    inv = _finalized_invoice(db, price=100.0, qty=1)  # total = 100 + tax(0) = 100

    resp = client.post(
        f"/invoices/{inv.id}/issue-credit-memo",
        data={"reason": "overcharge correction"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    loc = resp.headers["location"]
    assert loc.startswith("/credit-memos/")

    db.expire_all()
    cm = (db.query(CreditMemo)
            .filter(CreditMemo.original_invoice_id == inv.id)
            .first())
    assert cm is not None, "a credit memo must be created"
    assert cm.reason == "overcharge correction"
    assert cm.total_amount == 100.0
    # redirect points at THIS credit memo's detail page
    assert loc == f"/credit-memos/{cm.id}"


def test_issue_credit_memo_override_amount(client, db):
    from app.models.credit_memo import CreditMemo
    inv = _finalized_invoice(db, price=100.0, qty=2)  # total = 200

    resp = client.post(
        f"/invoices/{inv.id}/issue-credit-memo",
        data={"reason": "partial goodwill", "amount": "25.50"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    db.expire_all()
    cm = (db.query(CreditMemo)
            .filter(CreditMemo.original_invoice_id == inv.id)
            .order_by(CreditMemo.id.desc())
            .first())
    assert cm is not None
    assert cm.total_amount == 25.50, "override amount must drive the CM total"


def test_issue_credit_memo_requires_reason(client, db):
    from app.models.credit_memo import CreditMemo
    inv = _finalized_invoice(db, price=100.0, qty=1)

    resp = client.post(
        f"/invoices/{inv.id}/issue-credit-memo",
        data={"reason": "   "},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    # bounced back to the invoice with an error — NOT to a new credit memo
    assert resp.headers["location"].startswith(f"/invoices/{inv.id}?error=")

    db.expire_all()
    cm = (db.query(CreditMemo)
            .filter(CreditMemo.original_invoice_id == inv.id)
            .first())
    assert cm is None, "a blank reason must not create a credit memo"


def test_issue_credit_memo_blocked_on_void(client, db):
    from app.constants import InvoiceStatus
    from app.models.credit_memo import CreditMemo
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    inv = _finalized_invoice(db, price=100.0, qty=1)  # OPEN, no payments
    InvoiceService(db, _UID).void_invoice(inv.id, "duplicate")
    db.expire_all()
    assert db.get(Invoice, inv.id).status == InvoiceStatus.VOID

    resp = client.post(
        f"/invoices/{inv.id}/issue-credit-memo",
        data={"reason": "should be rejected"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert resp.headers["location"].startswith(f"/invoices/{inv.id}?error=")

    db.expire_all()
    cm = (db.query(CreditMemo)
            .filter(CreditMemo.original_invoice_id == inv.id)
            .first())
    assert cm is None, "a void invoice cannot spawn a credit memo"


# ── List + detail ─────────────────────────────────────────────────────────────

def test_credit_memo_list_and_detail(client, db):
    from app.models.credit_memo import CreditMemo
    inv = _finalized_invoice(db, price=100.0, qty=1)
    client.post(f"/invoices/{inv.id}/issue-credit-memo",
                data={"reason": "list/detail smoke"}, follow_redirects=False)
    db.expire_all()
    cm = (db.query(CreditMemo)
            .filter(CreditMemo.original_invoice_id == inv.id)
            .first())
    assert cm is not None

    # List
    lst = client.get("/credit-memos/")
    assert lst.status_code == 200
    assert cm.cm_number in lst.text

    # Status filter tab still renders the row (it's OPEN)
    open_tab = client.get("/credit-memos/?status=open")
    assert open_tab.status_code == 200
    assert cm.cm_number in open_tab.text

    # Detail
    det = client.get(f"/credit-memos/{cm.id}")
    assert det.status_code == 200
    assert cm.cm_number in det.text
    assert "list/detail smoke" in det.text


def test_credit_memo_detail_missing_redirects(client):
    resp = client.get("/credit-memos/99999", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == "/credit-memos/"


# ── Workspace button visibility ───────────────────────────────────────────────

def test_button_present_on_open_invoice(client, db):
    inv = _finalized_invoice(db, price=100.0, qty=1)
    page = client.get(f"/invoices/{inv.id}")
    assert page.status_code == 200
    assert "Issue Credit Memo" in page.text


def test_button_absent_on_draft_invoice(client, db):
    inv = _draft_invoice(db, price=100.0, qty=1)
    page = client.get(f"/invoices/{inv.id}")
    assert page.status_code == 200
    assert "Issue Credit Memo" not in page.text
