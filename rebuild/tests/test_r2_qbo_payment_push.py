"""
tests/test_r2_qbo_payment_push.py
=================================
R2 — three verified QBO gaps, all with Intuit HTTP faked (no live calls):

  1. QBOSyncService.push_payment — pushes a customer payment to QBO as a QBO
     Payment that LINKS (LinkedTxn) to the already-synced invoice(s) it paid.
       * happy path → QBO Payment created with the right payload shape and the
         JAKS payment marked SYNCED;
       * refuses (fail-soft, recorded via mark_sync_failed) a payment whose
         allocated invoice is NOT yet in QBO, and a REVERSED / NSF payment;
       * a QBO API failure marks the payment ERROR, NEVER raises, and never
         touches the money path (amount / status / allocations unchanged).

  2. Fernet token encryption at rest in qbo_client — encrypt-on-write /
     decrypt-on-read keyed by JAKS_FERNET_KEY, backward + forward compatible:
       * round-trips with a key set (stored value is enc:-prefixed ciphertext);
       * reads a legacy PLAINTEXT value transparently even with a key set;
       * with NO key, stores plaintext and logs a one-line warning (no behaviour
         change on a box that never sets the key).

  3. Customer-match disambiguation — when >1 QBO customer shares the DisplayName,
     refuse the auto-bind (which would post AR to the wrong fleet account) with a
     clear 'resolve manually' error rather than silently binding the first match.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r2_qbo_payment_push.py -q
"""
from __future__ import annotations

import itertools
import logging
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

import app.database as _appdb
import app.services.qbo_client as qc
from app.constants import (
    InvoiceStatus, LineType, PaymentMethod, PaymentStatus, QBOSyncStatus,
)
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.services.qbo_service import QBOSyncService
from app.settings_utils import get_setting_value_db, set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
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


# ── fake Intuit HTTP (mirrors tests/test_qbo_sync.py) ───────────────────────────

class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or ""
        self.content = b"x" if payload is not None else b""

    def json(self):
        return self._payload


class FakeQBO:
    """Routes the two call surfaces qbo_client uses: httpx.request (API) and
    httpx.post (token endpoints)."""

    def __init__(self, *, customers=None, customer_rows=None,
                 fail_payment=False, network_error=False):
        self.customers = customers or {}            # DisplayName -> id
        self.customer_rows = customer_rows          # explicit override for the Customer query
        self.fail_payment = fail_payment
        self.network_error = network_error
        self.created: dict[str, dict] = {}          # entity -> last payload posted
        self.token_calls: list[dict] = []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        if self.network_error:
            raise httpx.ConnectError("boom")
        u = url.lower()
        if u.endswith("/query"):
            q = (params or {}).get("query", "").lower()
            if "from customer" in q:
                rows = (self.customer_rows if self.customer_rows is not None
                        else [{"Id": i, "DisplayName": n} for n, i in self.customers.items()])
                return _Resp(200, {"QueryResponse": {"Customer": rows}})
            return _Resp(200, {"QueryResponse": {}})
        if u.endswith("/payment"):
            self.created["Payment"] = json
            if self.fail_payment:
                return _Resp(400, {"Fault": {"Error": [{"Message": "Invalid"}]}},
                             text="Invalid payment")
            return _Resp(200, {"Payment": {"Id": "QBO-PMT-1"}})
        if u.endswith("/customer"):
            self.created["Customer"] = json
            return _Resp(200, {"Customer": {"Id": "QBO-CUST-NEW"}})
        return _Resp(200, {})

    def post(self, url, headers=None, data=None, json=None, timeout=None):
        self.token_calls.append({"url": url, "data": data, "json": json})
        if "revoke" in url:
            return _Resp(200, {})
        return _Resp(200, {
            "access_token": "AT-NEW", "refresh_token": "RT-NEW",
            "expires_in": 3600, "x_refresh_token_expires_in": 8640000,
        })


def _install(monkeypatch, fake: FakeQBO):
    shim = types.SimpleNamespace(
        request=fake.request, post=fake.post,
        QueryParams=httpx.QueryParams, HTTPError=httpx.HTTPError,
        ConnectError=httpx.ConnectError,
    )
    monkeypatch.setattr(qc, "httpx", shim)
    return fake


# ── builders ────────────────────────────────────────────────────────────────

def _connect(db, *, expires_at="2999-01-01T00:00:00"):
    for k, v in {
        "qbo_client_id": "CID", "qbo_client_secret": "CSEC", "qbo_realm_id": "REALM-1",
        "qbo_access_token": "AT-OLD", "qbo_refresh_token": "RT-OLD",
        "qbo_token_expires_at": expires_at, "qbo_environment": "sandbox",
    }.items():
        set_setting_value_db(db, k, v)
    db.commit()


def _customer(db, *, name=None, qbo_id="QBO-CUST-9") -> Customer:
    c = Customer(company_name=name or f"QBO Co {next(_seq)}", contact_name="QA",
                 email="cust@test.local", qbo_customer_id=qbo_id)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _invoice(db, customer_id, *, qbo_invoice_id="QBO-INV-1", total=100.0) -> Invoice:
    """A finalized invoice; qbo_invoice_id=None models one not yet pushed to QBO."""
    inv = Invoice(
        invoice_number=f"INV-{next(_seq):05d}", customer_id=customer_id,
        status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
        tax_rate_snapshot=0.0, tax_exempt_snapshot=True,
        qbo_invoice_id=qbo_invoice_id,
        qbo_sync_status=QBOSyncStatus.SYNCED if qbo_invoice_id else QBOSyncStatus.PENDING,
    )
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=total,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _payment(db, customer_id, allocations, *, status=PaymentStatus.APPLIED,
             method=PaymentMethod.CHECK, check_number="1234",
             qbo_payment_id=None) -> Payment:
    """allocations: list of (invoice, amount)."""
    pmt = Payment(
        customer_id=customer_id,
        amount_received=round(sum(a for _, a in allocations), 2),
        payment_method=method, status=status, check_number=check_number,
        qbo_payment_id=qbo_payment_id,
    )
    db.add(pmt); db.flush()
    for inv, amt in allocations:
        db.add(PaymentAllocation(payment_id=pmt.id, invoice_id=inv.id,
                                 amount_applied=round(amt, 2)))
    db.commit(); db.refresh(pmt)
    return pmt


# ── 1. push_payment — happy path: payload shape + marked synced ───────────────

def test_push_payment_happy_path_marks_synced_and_payload_shape(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-77", total=100.0)
    pmt = _payment(db, cust.id, [(inv, 100.0)], check_number="5150")

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res == {"ok": True, "qbo_id": "QBO-PMT-1"}, res

    payload = fake.created["Payment"]
    assert payload["CustomerRef"]["value"] == "QBO-CUST-9"
    assert payload["TotalAmt"] == pytest.approx(100.0, abs=0.01)
    assert len(payload["Line"]) == 1
    line = payload["Line"][0]
    assert line["Amount"] == pytest.approx(100.0, abs=0.01)
    assert line["LinkedTxn"] == [{"TxnId": "QBO-INV-77", "TxnType": "Invoice"}]
    assert payload["PaymentRefNum"] == "5150"

    db.expire_all()
    fresh = db.query(Payment).filter(Payment.id == pmt.id).first()
    assert fresh.qbo_payment_id == "QBO-PMT-1"
    assert fresh.qbo_sync_status == QBOSyncStatus.SYNCED


def test_push_payment_links_each_synced_invoice(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv_a = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-A", total=100.0)
    inv_b = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-B", total=60.0)
    pmt = _payment(db, cust.id, [(inv_a, 100.0), (inv_b, 60.0)])

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"], res
    payload = fake.created["Payment"]
    assert payload["TotalAmt"] == pytest.approx(160.0, abs=0.01)
    linked = {l["LinkedTxn"][0]["TxnId"]: l["Amount"] for l in payload["Line"]}
    assert linked == {"QBO-INV-A": pytest.approx(100.0), "QBO-INV-B": pytest.approx(60.0)}


# ── 1b. push_payment — refusals (fail-soft, recorded via mark_sync_failed) ────

def test_refuses_when_allocated_invoice_not_synced(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _invoice(db, cust.id, qbo_invoice_id=None, total=100.0)   # NOT in QBO yet
    pmt = _payment(db, cust.id, [(inv, 100.0)])

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"] is False
    assert "not yet synced" in res["error"].lower()
    assert "Payment" not in fake.created               # never reached the QBO API
    db.expire_all()
    fresh = db.query(Payment).filter(Payment.id == pmt.id).first()
    assert fresh.qbo_payment_id is None
    assert fresh.qbo_sync_status == QBOSyncStatus.ERROR   # refusal recorded on the payment


def test_refuses_reversed_payment(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-9")
    pmt = _payment(db, cust.id, [(inv, 100.0)], status=PaymentStatus.REVERSED)

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"] is False
    assert "applied" in res["error"].lower()          # "only an APPLIED payment can be pushed"
    assert "Payment" not in fake.created
    db.expire_all()
    assert db.query(Payment).filter(Payment.id == pmt.id).first().qbo_payment_id is None


def test_refuses_nsf_payment(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-9")
    pmt = _payment(db, cust.id, [(inv, 100.0)], status=PaymentStatus.NSF)

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"] is False and "applied" in res["error"].lower()
    assert "Payment" not in fake.created


# ── 1c. THE SAFETY GATE: API failure marks ERROR, never raises, money untouched ─

def test_failure_path_marks_sync_failed_and_never_raises(db, monkeypatch):
    _install(monkeypatch, FakeQBO(fail_payment=True))
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-9")
    pmt = _payment(db, cust.id, [(inv, 100.0)])
    amount_before, status_before = pmt.amount_received, pmt.status
    alloc_before = pmt.amount_allocated

    res = QBOSyncService(db).push_payment(pmt.id)        # must NOT raise
    assert res["ok"] is False and "400" in res["error"]

    db.expire_all()
    fresh = db.query(Payment).filter(Payment.id == pmt.id).first()
    assert fresh.qbo_sync_status == QBOSyncStatus.ERROR
    assert fresh.qbo_sync_retry_count == 1
    assert fresh.qbo_payment_id is None
    # money path untouched
    assert fresh.amount_received == amount_before
    assert fresh.status == status_before
    assert fresh.amount_allocated == alloc_before


def test_network_error_is_soft(db, monkeypatch):
    _install(monkeypatch, FakeQBO(network_error=True))
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-9")
    pmt = _payment(db, cust.id, [(inv, 100.0)])

    res = QBOSyncService(db).push_payment(pmt.id)        # must NOT raise
    assert res["ok"] is False
    db.expire_all()
    assert db.query(Payment).filter(Payment.id == pmt.id).first().qbo_sync_status == QBOSyncStatus.ERROR


# ── 2. Fernet token encryption at rest ────────────────────────────────────────

def test_fernet_round_trip_with_key_set(db, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("JAKS_FERNET_KEY", Fernet.generate_key().decode())

    # encrypt-on-write: the stored value is enc:-prefixed ciphertext, not the secret
    set_setting_value_db(db, "qbo_access_token", qc._encrypt("SUPER-SECRET-AT"))
    db.commit()
    raw = get_setting_value_db(db, "qbo_access_token")
    assert raw.startswith("enc:")
    assert "SUPER-SECRET-AT" not in raw

    # decrypt-on-read via load_config
    cfg = qc.load_config(db)
    assert cfg.access_token == "SUPER-SECRET-AT"


def test_legacy_plaintext_read_with_key_set(db, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("JAKS_FERNET_KEY", Fernet.generate_key().decode())

    # a value written BEFORE encryption existed has no enc: prefix → read as-is
    set_setting_value_db(db, "qbo_refresh_token", "LEGACY-PLAINTEXT-RT")
    db.commit()
    cfg = qc.load_config(db)
    assert cfg.refresh_token == "LEGACY-PLAINTEXT-RT"


def test_plaintext_and_warning_when_no_key(db, monkeypatch, caplog):
    monkeypatch.delenv("JAKS_FERNET_KEY", raising=False)
    monkeypatch.setattr(qc, "_warned_no_key", False)   # allow the one-time warning to fire

    with caplog.at_level(logging.WARNING, logger=qc.log.name):
        stored = qc._encrypt("PLAIN-AT")
    assert stored == "PLAIN-AT"                         # no enc: prefix, no behaviour change
    assert not stored.startswith("enc:")
    assert any("JAKS_FERNET_KEY" in r.message for r in caplog.records)

    # round-trips back out as the same plaintext
    set_setting_value_db(db, "qbo_access_token", stored)
    db.commit()
    assert qc.load_config(db).access_token == "PLAIN-AT"


# ── 3. Customer-match disambiguation ──────────────────────────────────────────

def test_multi_match_customer_is_refused(db, monkeypatch):
    # Two QBO customers share the DisplayName → must refuse, not auto-bind the first.
    fake = _install(monkeypatch, FakeQBO(customer_rows=[
        {"Id": "111", "DisplayName": "Acme Diesel"},
        {"Id": "222", "DisplayName": "Acme Diesel"},
    ]))
    _connect(db)
    cust = _customer(db, name="Acme Diesel", qbo_id="")   # unbound → triggers DisplayName match
    inv = _invoice(db, cust.id, qbo_invoice_id="QBO-INV-9")
    pmt = _payment(db, cust.id, [(inv, 100.0)])

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"] is False
    assert "multiple qbo customers match" in res["error"].lower()
    assert "resolve manually" in res["error"].lower()
    assert "Payment" not in fake.created                  # no payment posted on a bad match

    db.expire_all()
    # the wrong-customer binding must NOT have been written
    assert (db.query(Customer).filter(Customer.id == cust.id).first().qbo_customer_id or "") == ""
    assert db.query(Payment).filter(Payment.id == pmt.id).first().qbo_sync_status == QBOSyncStatus.ERROR
