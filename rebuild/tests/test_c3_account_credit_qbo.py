"""
tests/test_c3_account_credit_qbo.py
===================================
C3 (FULL_ERP_REVIEW_2026-07-04) — synthetic ACCOUNT_CREDIT payments must never
reach QBO as cash.

apply_credit_memo() and apply_account_credit() create a synthetic Payment
(method=account_credit) purely to keep invoice.amount_paid math consistent.
No cash moves. Historically those rows were created qbo_sync_status=PENDING and
push_payment had no method filter, so once the invoice synced, the synthetic
payment pushed to QBO as REAL cash into Undeposited Funds — while the credit
memo ALSO pushed as a QBO CreditMemo. Net: phantom cash, duplicate credit,
Undeposited Funds never ties to the bank.

Locked behaviour:
  1. Both creation paths stamp the synthetic payment SKIPPED (terminal), never
     PENDING — it never enters any QBO push lane. ERP-side balance math is
     unchanged (the invoice is still paid down by the credit).
  2. push_payment hard-refuses method=account_credit BEFORE any QBO call, even
     for legacy PENDING rows from a pre-fix database, and stamps them SKIPPED
     (not ERROR — the retry lane must not pick them up).
  3. failed_payment_ids excludes account_credit rows even if a pre-fix refusal
     already marked them ERROR, so retry_failed_pushes can never push them.
  4. The credit memo document itself still syncs (stays PENDING) — it is the
     one and only QBO-side representation of the credit.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_c3_account_credit_qbo.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx
import pytest

import app.database as _appdb
import app.services.qbo_client as qc
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

from app.constants import (
    CreditMemoTrigger, InvoiceStatus, LineType, PaymentMethod, PaymentStatus,
    QBOSyncStatus,
)
from app.models.credit_memo import CreditMemo
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.services.credit_memo_service import CreditMemoService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.qbo_service import QBOSyncService
from app.settings_utils import set_setting_value_db

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


# ── builders ────────────────────────────────────────────────────────────────

def _customer(db, *, credit_balance=0.0) -> Customer:
    c = Customer(company_name=f"C3 Co {next(_seq)}", qbo_customer_id="QBO-CUST-C3")
    if credit_balance:
        c.credit_balance = credit_balance
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id, amount, *, qbo_invoice_id=None) -> Invoice:
    inv = Invoice(invoice_number=f"INV-C3-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.DRAFT, tax_rate=0.0, tax_rate_snapshot=0.0,
                  tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT, description="p",
                       qty=1, unit_price=amount, unit_cost=amount * 0.6, is_taxable=False))
    db.commit()
    InvoiceService(db, _UID).finalise(inv.id)
    if qbo_invoice_id:
        inv = db.query(Invoice).get(inv.id)
        inv.qbo_invoice_id = qbo_invoice_id
        inv.qbo_sync_status = QBOSyncStatus.SYNCED
        db.commit()
    db.expire_all()
    return db.query(Invoice).get(inv.id)


def _cm_applied_to(db, customer_id, invoice_id, amount) -> CreditMemo:
    svc = CreditMemoService(db, _UID)
    cm = svc.create_credit_memo(
        customer_id, CreditMemoTrigger.MANUAL,
        lines=[{"description": "credit", "qty": 1, "unit_price": amount}], reason="c3")
    svc.apply_credit_memo(cm.id, invoice_id, amount)
    db.expire_all()
    return db.query(CreditMemo).get(cm.id)


def _synthetic_payment(db) -> Payment:
    """The one and only account_credit Payment the flows above created."""
    return (db.query(Payment)
            .filter(Payment.payment_method == PaymentMethod.ACCOUNT_CREDIT)
            .order_by(Payment.id.desc()).first())


# ── fake Intuit HTTP (trimmed from tests/test_r2_qbo_payment_push.py) ─────────

class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""
        self.content = b"x" if payload is not None else b""

    def json(self):
        return self._payload


class FakeQBO:
    def __init__(self):
        self.created: dict[str, dict] = {}  # entity -> last payload posted

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        u = url.lower()
        if u.endswith("/query"):
            return _Resp(200, {"QueryResponse": {}})
        if u.endswith("/payment"):
            self.created["Payment"] = json
            return _Resp(200, {"Payment": {"Id": "QBO-PMT-C3"}})
        return _Resp(200, {})

    def post(self, url, headers=None, data=None, json=None, timeout=None):
        return _Resp(200, {
            "access_token": "AT", "refresh_token": "RT",
            "expires_in": 3600, "x_refresh_token_expires_in": 8640000,
        })


def _connect_fake_qbo(db, monkeypatch) -> FakeQBO:
    for k, v in {
        "qbo_client_id": "CID", "qbo_client_secret": "CSEC", "qbo_realm_id": "REALM-1",
        "qbo_access_token": "AT", "qbo_refresh_token": "RT",
        "qbo_token_expires_at": "2999-01-01T00:00:00", "qbo_environment": "sandbox",
    }.items():
        set_setting_value_db(db, k, v)
    db.commit()
    fake = FakeQBO()
    shim = types.SimpleNamespace(
        request=fake.request, post=fake.post,
        QueryParams=httpx.QueryParams, HTTPError=httpx.HTTPError,
        ConnectError=httpx.ConnectError,
    )
    monkeypatch.setattr(qc, "httpx", shim)
    return fake


# ── 1. creation paths stamp SKIPPED, ERP math unchanged ──────────────────────

def test_apply_credit_memo_synthetic_payment_never_enqueues_qbo(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 500.0)
    cm = _cm_applied_to(db, cust.id, inv.id, 200.0)

    pmt = _synthetic_payment(db)
    assert pmt is not None and cm.cm_number in pmt.notes
    # Never PENDING: SKIPPED is terminal — no push lane picks it up.
    assert pmt.qbo_sync_status == QBOSyncStatus.SKIPPED
    assert pmt.qbo_payment_id is None

    # ERP-side balance math is unchanged by the fix.
    inv = db.query(Invoice).get(inv.id)
    assert inv.amount_paid == pytest.approx(200.0)
    assert inv.balance_due == pytest.approx(300.0)
    assert inv.status == InvoiceStatus.PARTIAL

    # The CM document itself is the QBO-side record — it still queues.
    assert cm.qbo_sync_status == QBOSyncStatus.PENDING


def test_apply_account_credit_payment_never_enqueues_qbo(db):
    cust = _customer(db, credit_balance=150.0)
    inv = _open_invoice(db, cust.id, 400.0)

    PaymentService(db, _UID).apply_account_credit(cust.id, inv.id, 80.0)

    pmt = _synthetic_payment(db)
    assert pmt.qbo_sync_status == QBOSyncStatus.SKIPPED
    assert pmt.qbo_payment_id is None

    db.expire_all()
    assert db.query(Customer).get(cust.id).credit_balance == pytest.approx(70.0)
    inv = db.query(Invoice).get(inv.id)
    assert inv.amount_paid == pytest.approx(80.0)
    assert inv.balance_due == pytest.approx(320.0)


# ── 2. push_payment hard gate — even for legacy PENDING rows ─────────────────

def test_push_payment_refuses_account_credit_without_touching_qbo(db, monkeypatch):
    fake = _connect_fake_qbo(db, monkeypatch)
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 250.0, qbo_invoice_id="QBO-INV-C3")

    # Legacy pre-fix row: PENDING account_credit payment allocated to a SYNCED
    # invoice — exactly the state that used to push phantom cash to QBO.
    pmt = Payment(customer_id=cust.id, payment_method=PaymentMethod.ACCOUNT_CREDIT,
                  amount_received=250.0, status=PaymentStatus.APPLIED,
                  notes="legacy synthetic row", qbo_sync_status=QBOSyncStatus.PENDING)
    db.add(pmt); db.flush()
    db.add(PaymentAllocation(payment_id=pmt.id, invoice_id=inv.id, amount_applied=250.0))
    db.commit()

    res = QBOSyncService(db).push_payment(pmt.id)

    assert res.get("ok") is True
    assert "non-cash" in res.get("skipped", "")
    assert "qbo_id" not in res
    assert fake.created == {}  # no QBO document of ANY kind was created

    db.expire_all()
    fresh = db.query(Payment).get(pmt.id)
    assert fresh.qbo_payment_id is None
    assert fresh.qbo_sync_status == QBOSyncStatus.SKIPPED  # terminal, not ERROR
    assert fresh.qbo_sync_error is None
    # Money path untouched.
    assert fresh.amount_received == pytest.approx(250.0)
    assert fresh.status == PaymentStatus.APPLIED


# ── 3. retry lane can never pick account_credit rows up ──────────────────────

def test_failed_payment_ids_excludes_account_credit(db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, 100.0, qbo_invoice_id="QBO-INV-C3-R")

    def _error_payment(method):
        p = Payment(customer_id=cust.id, payment_method=method, amount_received=50.0,
                    status=PaymentStatus.APPLIED, qbo_sync_status=QBOSyncStatus.ERROR,
                    qbo_sync_error="boom", qbo_sync_retry_count=1)
        db.add(p); db.flush()
        db.add(PaymentAllocation(payment_id=p.id, invoice_id=inv.id, amount_applied=50.0))
        db.commit()
        return p

    # Pre-fix databases can hold account_credit payments already marked ERROR.
    legacy_ac = _error_payment(PaymentMethod.ACCOUNT_CREDIT)
    real_check = _error_payment(PaymentMethod.CHECK)

    ids = QBOSyncService(db).failed_payment_ids()
    assert legacy_ac.id not in ids   # excluded → retry_failed_pushes never sees it
    assert real_check.id in ids      # real cash payments still retry
