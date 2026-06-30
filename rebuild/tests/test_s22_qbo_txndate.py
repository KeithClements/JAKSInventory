"""
tests/test_s22_qbo_txndate.py
=============================
Lane E (Risk #6, HIGH) — QBO transaction dates.

Before this fix, invoices and payments pushed to QuickBooks carried no TxnDate,
so QBO stamped each one with the *push* date ("today") rather than the date the
sale was actually invoiced / the payment was actually taken. Posting a prior-
month sale under today's date corrupts period totals, sales-tax filing, and AR
aging.

These tests build the QBO payloads via the private builders (no network) and
assert TxnDate equals the document's authoritative date — the exact formatted
string for a fixture dated in a prior month — and is NOT today.
"""
from __future__ import annotations

import datetime
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest

from app.constants import InvoiceStatus, LineType, PaymentMethod
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.services.qbo_service import QBOSyncService

_ENGINE = fresh_engine()
_seq = itertools.count(1)

# A fixed date well in the past so it can never collide with "today".
PRIOR = datetime.datetime(2025, 11, 4, 9, 30, 0)
PRIOR_STR = "2025-11-04"


@pytest.fixture()
def db():
    activate(_ENGINE)
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _finalized_invoice(db, *, locked_at, created_at=None) -> Invoice:
    """A pushable (OPEN) invoice with one taxable product line. locked_at is the
    finalize stamp QBO should post under; created_at is the defensive fallback."""
    c = Customer(company_name=f"TxnDate Co {next(_seq)}")
    db.add(c)
    db.flush()
    inv = Invoice(
        invoice_number=f"INV-TXN-{next(_seq):05d}",
        customer_id=c.id,
        status=InvoiceStatus.OPEN,
        is_taxable=False,
        locked_at=locked_at,
    )
    if created_at is not None:
        inv.created_at = created_at
    db.add(inv)
    db.flush()
    line = InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Test part",
        qty=2,
        unit_price=50.0,
        is_taxable=False,
        sort_order=0,
    )
    db.add(line)
    db.commit()
    db.refresh(inv)
    return inv


def _payment(db, *, payment_date) -> Payment:
    c = Customer(company_name=f"Pay Co {next(_seq)}")
    db.add(c)
    db.flush()
    pmt = Payment(
        customer_id=c.id,
        payment_method=PaymentMethod.CHECK,
        amount_received=100.0,
        payment_date=payment_date,
    )
    db.add(pmt)
    db.commit()
    db.refresh(pmt)
    return pmt


# ── Invoice ─────────────────────────────────────────────────────────────────

def test_invoice_payload_has_txndate_of_finalize_date(db):
    inv = _finalized_invoice(db, locked_at=PRIOR)
    svc = QBOSyncService(db, current_user_id=None)
    payload = svc._build_invoice_payload(inv, {"value": "1"}, {})
    assert payload["TxnDate"] == PRIOR_STR


def test_invoice_txndate_is_not_today(db):
    inv = _finalized_invoice(db, locked_at=PRIOR)
    svc = QBOSyncService(db, current_user_id=None)
    payload = svc._build_invoice_payload(inv, {"value": "1"}, {})
    today = datetime.date.today().strftime("%Y-%m-%d")
    assert payload["TxnDate"] != today


def test_invoice_txndate_falls_back_to_created_at(db):
    # Defensive path: no finalize stamp → use the created/issue date.
    created = datetime.datetime(2025, 10, 1, 8, 0, 0)
    inv = _finalized_invoice(db, locked_at=None, created_at=created)
    svc = QBOSyncService(db, current_user_id=None)
    payload = svc._build_invoice_payload(inv, {"value": "1"}, {})
    assert payload["TxnDate"] == "2025-10-01"


# ── Payment ─────────────────────────────────────────────────────────────────

def test_payment_payload_has_txndate_of_payment_date(db):
    pmt = _payment(db, payment_date=PRIOR)
    svc = QBOSyncService(db, current_user_id=None)
    payload = svc._build_payment_payload(
        pmt, {"value": "1"}, [("99", 100.0)]
    )
    assert payload["TxnDate"] == PRIOR_STR


def test_payment_txndate_is_not_today(db):
    pmt = _payment(db, payment_date=PRIOR)
    svc = QBOSyncService(db, current_user_id=None)
    payload = svc._build_payment_payload(
        pmt, {"value": "1"}, [("99", 100.0)]
    )
    today = datetime.date.today().strftime("%Y-%m-%d")
    assert payload["TxnDate"] != today
