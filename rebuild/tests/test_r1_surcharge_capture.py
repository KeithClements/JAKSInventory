"""
tests/test_r1_surcharge_capture.py
==================================
R1-3 — the card surcharge is COLLECTED at payment time through both payment
entry points (invoice "Take Payment" POST /invoices/{id}/payment and the
multi-invoice POST /payments/new form), gated on (card method) AND
(invoice.apply_cc_surcharge). Principal (amount_received) stays what reduces
the invoice balance; the fee lands on Payment.surcharge_amount.

R1-13 (invoice half) — GET /invoices/export.csv streams the invoice list as
CSV honoring the same tab/q filters as the list view.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r1_surcharge_capture.py -q
"""
from __future__ import annotations

import csv
import io
import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, PaymentMethod, UserRole
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment
from app.models.product import Product
from app.models.user import User
from app.services.invoice_service import InvoiceService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)
_UID = 1


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == _UID).first():
        s.add(User(id=_UID, name="Test Admin", username="admin_r1s",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


# ── Builders ──────────────────────────────────────────────────────────────────

def _customer(db) -> Customer:
    c = Customer(company_name=f"R1SCo-{next(_seq)}", is_active=True,
                 tax_rate=0.0, is_tax_exempt=False)
    db.add(c); db.commit()
    return c


def _product(db, price: float = 100.0) -> Product:
    n = next(_seq)
    p = Product(sku=f"R1S-{n:06d}", title=f"R1S Part {n}",
                qty_on_hand=500, qty_committed=0, cost=40.0, price_override=price)
    db.add(p); db.commit()
    return p


def _finalized_invoice(db, cust, prod, qty: int = 2, flagged: bool = True) -> Invoice:
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": qty})
    if flagged:
        svc.update_header(inv.id, {"apply_cc_surcharge": True})
    svc.finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _latest_payment(db) -> Payment:
    db.expire_all()
    return db.query(Payment).order_by(Payment.id.desc()).first()


# ── R1-3 — invoice "Take Payment" route ──────────────────────────────────────

def test_card_payment_on_flagged_invoice_collects_surcharge(client, db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, _product(db), qty=2, flagged=True)  # $200
    total, pct = inv.total, inv.cc_surcharge_pct
    assert total == 200.00 and pct > 0

    resp = client.post(
        f"/invoices/{inv.id}/payment",
        data={"amount": f"{total:.2f}", "method": PaymentMethod.CREDIT_CARD},
    )
    assert resp.status_code == 303 and "saved=1" in resp.headers["location"]

    pmt = _latest_payment(db)
    expected_fee = round(total * pct / 100, 2)
    assert pmt.payment_method == PaymentMethod.CREDIT_CARD
    assert pmt.surcharge_amount == expected_fee
    assert pmt.surcharge_amount > 0
    # Principal unchanged — the fee is ON TOP, never eats into A/R.
    assert pmt.amount_received == total

    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.balance_due == 0.0
    assert inv.status == InvoiceStatus.PAID


def test_card_payment_on_unflagged_invoice_no_surcharge(client, db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, _product(db), qty=2, flagged=False)

    resp = client.post(
        f"/invoices/{inv.id}/payment",
        data={"amount": f"{inv.total:.2f}", "method": PaymentMethod.CREDIT_CARD},
    )
    assert resp.status_code == 303 and "saved=1" in resp.headers["location"]

    pmt = _latest_payment(db)
    assert pmt.surcharge_amount == 0.0
    assert pmt.amount_received == 200.00


def test_cash_payment_on_flagged_invoice_no_surcharge(client, db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, _product(db), qty=2, flagged=True)

    resp = client.post(
        f"/invoices/{inv.id}/payment",
        data={"amount": f"{inv.total:.2f}", "method": PaymentMethod.CASH},
    )
    assert resp.status_code == 303 and "saved=1" in resp.headers["location"]

    pmt = _latest_payment(db)
    assert pmt.payment_method == PaymentMethod.CASH
    assert pmt.surcharge_amount == 0.0


# ── R1-3 — multi-invoice /payments/new route ─────────────────────────────────

def test_multi_invoice_card_payment_collects_surcharge(client, db):
    cust = _customer(db)
    inv_a = _finalized_invoice(db, cust, _product(db), qty=2, flagged=True)   # $200
    inv_b = _finalized_invoice(db, cust, _product(db), qty=1, flagged=True)   # $100
    total = inv_a.total + inv_b.total
    pct = max(inv_a.cc_surcharge_pct, inv_b.cc_surcharge_pct)

    resp = client.post(
        "/payments/new",
        data={
            "customer_id": str(cust.id),
            "amount": f"{total:.2f}",
            "method": PaymentMethod.CREDIT_CARD,
            "payment_date": "2026-06-10",
            "invoice_ids": [str(inv_a.id), str(inv_b.id)],
        },
    )
    assert resp.status_code == 303 and "saved=1" in resp.headers["location"]

    pmt = _latest_payment(db)
    assert pmt.surcharge_amount == round(total * pct / 100, 2)
    assert pmt.surcharge_amount > 0
    assert pmt.amount_received == total

    for inv_id in (inv_a.id, inv_b.id):
        inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
        assert inv.balance_due == 0.0
        assert inv.status == InvoiceStatus.PAID


def test_multi_invoice_mixed_flags_no_surcharge(client, db):
    """One flagged + one unflagged invoice — surcharging the whole principal
    would overcharge the unflagged portion, so no surcharge is applied."""
    cust = _customer(db)
    inv_a = _finalized_invoice(db, cust, _product(db), qty=2, flagged=True)
    inv_b = _finalized_invoice(db, cust, _product(db), qty=1, flagged=False)
    total = inv_a.total + inv_b.total

    resp = client.post(
        "/payments/new",
        data={
            "customer_id": str(cust.id),
            "amount": f"{total:.2f}",
            "method": PaymentMethod.CREDIT_CARD,
            "payment_date": "2026-06-10",
            "invoice_ids": [str(inv_a.id), str(inv_b.id)],
        },
    )
    assert resp.status_code == 303 and "saved=1" in resp.headers["location"]

    pmt = _latest_payment(db)
    assert pmt.surcharge_amount == 0.0
    assert pmt.amount_received == total


# ── R1-13 (invoice half) — CSV export ────────────────────────────────────────

def _csv_rows(resp) -> list[list[str]]:
    return list(csv.reader(io.StringIO(resp.text)))


def test_export_csv_header_and_rows(client, db):
    cust = _customer(db)
    inv = _finalized_invoice(db, cust, _product(db), qty=2, flagged=False)

    resp = client.get("/invoices/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    rows = _csv_rows(resp)
    assert rows[0] == [
        "invoice_number", "status", "customer", "customer_po_number", "esn",
        "created", "due_date", "subtotal", "tax", "total",
        "amount_paid", "balance_due", "overdue",
    ]
    by_number = {r[0]: r for r in rows[1:]}
    assert inv.invoice_number in by_number
    row = by_number[inv.invoice_number]
    assert row[2] == cust.company_name
    assert row[9] == f"{inv.total:.2f}"


def test_export_csv_honors_q_and_tab(client, db):
    cust = _customer(db)
    open_inv = _finalized_invoice(db, cust, _product(db), qty=1, flagged=False)
    draft = InvoiceService(db, _UID).create_draft(cust.id)
    db.expire_all()
    draft = db.query(Invoice).filter(Invoice.id == draft.id).first()

    # q narrows to the matching invoice number only
    resp = client.get(f"/invoices/export.csv?q={open_inv.invoice_number}")
    rows = _csv_rows(resp)
    data_numbers = [r[0] for r in rows[1:]]
    assert data_numbers == [open_inv.invoice_number]

    # tab=draft includes the draft but not the finalized invoice
    resp = client.get("/invoices/export.csv?tab=draft")
    data_numbers = [r[0] for r in _csv_rows(resp)[1:]]
    assert draft.invoice_number in data_numbers
    assert open_inv.invoice_number not in data_numbers

    # tab=void matches nothing we created here
    resp = client.get("/invoices/export.csv?tab=void")
    data_numbers = [r[0] for r in _csv_rows(resp)[1:]]
    assert open_inv.invoice_number not in data_numbers
    assert draft.invoice_number not in data_numbers
