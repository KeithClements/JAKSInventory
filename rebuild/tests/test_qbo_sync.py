"""
tests/test_qbo_sync.py
======================
Phase 1B — QuickBooks Online OAuth + invoice push (accounting-summary mapping).

All Intuit HTTP is faked (no live calls). The guards that matter:
  * Strategy-B mapping: each line_type → its generic income item; cc_surcharge
    and tax lines are NOT pushed as lines; SKU rides in the Description.
  * Success → invoice marked SYNCED + qbo_invoice_id stored.
  * THE SAFETY GATE: a QBO failure marks the invoice ERROR (bumps retry), never
    raises, and never changes the invoice's money or status.
  * OAuth: authorize URL + CSRF state, code exchange, refresh-on-expiry.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

import app.database as _appdb
import app.services.qbo_client as qc
from app.constants import InvoiceStatus, LineType, QBOSyncStatus
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.services.invoice_service import InvoiceService
from app.services.qbo_service import DEFAULT_ITEM_MAP, QBOSyncService
from app.settings_utils import get_setting_value_db, set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1
_seq = itertools.count(1)
_ITEM_IDS = {name: f"ITEM-{i}" for i, name in enumerate(sorted(set(DEFAULT_ITEM_MAP.values())), 1)}


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


# ── fake Intuit HTTP ──────────────────────────────────────────────────────────

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

    def __init__(self, *, items=None, customers=None, accounts=None,
                 fail_invoice=False, network_error=False):
        # items: dict name->id present in QBO
        self.items = items if items is not None else dict(_ITEM_IDS)
        self.customers = customers or {}          # DisplayName -> id
        self.accounts = accounts or [("77", "Sales of Product Income")]
        self.fail_invoice = fail_invoice
        self.network_error = network_error
        self.created: dict[str, dict] = {}        # entity -> last payload posted
        self.token_calls: list[dict] = []

    # API surface
    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        if self.network_error:
            raise httpx.ConnectError("boom")
        u = url.lower()
        if u.endswith("/query"):
            q = (params or {}).get("query", "").lower()
            if "from item" in q:
                return _Resp(200, {"QueryResponse": {"Item": [
                    {"Id": i, "Name": n} for n, i in self.items.items()]}})
            if "from customer" in q:
                return _Resp(200, {"QueryResponse": {"Customer": [
                    {"Id": i, "DisplayName": n} for n, i in self.customers.items()]}})
            if "from account" in q:
                return _Resp(200, {"QueryResponse": {"Account": [
                    {"Id": i, "Name": n} for i, n in self.accounts]}})
            return _Resp(200, {"QueryResponse": {}})
        if u.endswith("/invoice"):
            self.created["Invoice"] = json
            if self.fail_invoice:
                return _Resp(400, {"Fault": {"Error": [{"Message": "Invalid"}]}},
                             text="Invalid invoice")
            return _Resp(200, {"Invoice": {"Id": "QBO-INV-1", "DocNumber": json.get("DocNumber")}})
        if u.endswith("/customer"):
            self.created["Customer"] = json
            return _Resp(200, {"Customer": {"Id": "QBO-CUST-NEW"}})
        if u.endswith("/item"):
            self.created.setdefault("Item", []).append(json) if isinstance(
                self.created.get("Item"), list) else self.created.update({"Item": [json]})
            return _Resp(200, {"Item": {"Id": "NEW-ITEM"}})
        return _Resp(200, {})

    # token surface
    def post(self, url, headers=None, data=None, json=None, timeout=None):
        self.token_calls.append({"url": url, "data": data, "json": json})
        if "revoke" in url:
            return _Resp(200, {})
        return _Resp(200, {
            "access_token": "AT-NEW", "refresh_token": "RT-NEW",
            "expires_in": 3600, "x_refresh_token_expires_in": 8640000,
        })


def _install(monkeypatch, fake: FakeQBO):
    """Swap only qbo_client's httpx reference — keeps the real httpx (and the
    TestClient's transport) untouched."""
    shim = types.SimpleNamespace(
        request=fake.request, post=fake.post,
        QueryParams=httpx.QueryParams, HTTPError=httpx.HTTPError,
        ConnectError=httpx.ConnectError,
    )
    monkeypatch.setattr(qc, "httpx", shim)
    return fake


# ── builders ──────────────────────────────────────────────────────────────────

def _connect(db, *, expires_at="2999-01-01T00:00:00", environment="sandbox"):
    for k, v in {
        "qbo_client_id": "CID", "qbo_client_secret": "CSEC", "qbo_realm_id": "REALM-1",
        "qbo_access_token": "AT-OLD", "qbo_refresh_token": "RT-OLD",
        "qbo_token_expires_at": expires_at, "qbo_environment": environment,
    }.items():
        set_setting_value_db(db, k, v)
    db.commit()


def _customer(db, *, name=None, email="cust@test.local", qbo_id="") -> Customer:
    c = Customer(company_name=name or f"QBO Co {next(_seq)}", contact_name="QA",
                 email=email, qbo_customer_id=qbo_id)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _finalized_invoice(db, customer_id, lines, *, taxable=False, tax_rate=0.0) -> Invoice:
    inv = Invoice(
        invoice_number=f"INV-QBO-{next(_seq):05d}",
        customer_id=customer_id,
        status=InvoiceStatus.DRAFT,
        is_taxable=taxable,
        tax_rate=tax_rate,
        tax_rate_snapshot=tax_rate,
        tax_exempt_snapshot=not taxable,
    )
    db.add(inv); db.flush()
    for ln in lines:
        db.add(InvoiceLine(invoice_id=inv.id, **ln))
    db.commit()
    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _line(line_type, price, *, qty=1, desc="", taxable=False):
    return dict(line_type=line_type, description=desc or "Test line", qty=qty,
                unit_price=price, unit_cost=price * 0.6, is_taxable=taxable)


# ── mapping ───────────────────────────────────────────────────────────────────

def test_strategy_b_mapping_and_exclusions(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [
        _line(LineType.PRODUCT, 100.0, desc="Cylinder head"),
        _line(LineType.CORE_CHARGE, 50.0, desc="Core deposit"),
        _line(LineType.FREIGHT, 25.0, desc="Shipping"),
        _line(LineType.CC_SURCHARGE, 9.0, desc="card fee"),
    ])
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res
    payload = fake.created["Invoice"]
    lines = payload["Line"]
    # cc_surcharge excluded → only 3 lines
    assert len(lines) == 3
    by_amt = {round(l["Amount"], 2): l for l in lines}
    assert by_amt[100.0]["SalesItemLineDetail"]["ItemRef"]["value"] == _ITEM_IDS["JAKS Parts Sales"]
    assert by_amt[50.0]["SalesItemLineDetail"]["ItemRef"]["value"] == _ITEM_IDS["JAKS Core Charge"]
    assert by_amt[25.0]["SalesItemLineDetail"]["ItemRef"]["value"] == _ITEM_IDS["JAKS Freight & Delivery"]
    assert payload["DocNumber"] == inv.invoice_number
    assert payload["CustomerRef"]["value"] == "QBO-CUST-9"


def test_description_carries_sku(db, monkeypatch):
    from app.models.product import Product
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    prod = Product(sku="CYL-123", title="Head", cost=60.0, qty_on_hand=10)
    db.add(prod); db.commit(); db.refresh(prod)
    inv = Invoice(invoice_number=f"INV-QBO-{next(_seq):05d}", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT, tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT, product_id=prod.id,
                       description="Reman head", qty=1, unit_price=100.0, unit_cost=60.0))
    db.commit()
    InvoiceService(db, _UID).finalise(inv.id)
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res
    desc = fake.created["Invoice"]["Line"][0]["Description"]
    assert "CYL-123" in desc


def test_taxable_invoice_sends_txn_tax(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id,
                             [_line(LineType.PRODUCT, 100.0, taxable=True)],
                             taxable=True, tax_rate=8.0)
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res
    payload = fake.created["Invoice"]
    assert payload["GlobalTaxCalculation"] == "TaxExcluded"
    assert payload["TxnTaxDetail"]["TotalTax"] == pytest.approx(8.0, abs=0.01)


def test_push_tax_setting_off_suppresses_tax(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    set_setting_value_db(db, "qbo_push_tax", "false"); db.commit()
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id,
                             [_line(LineType.PRODUCT, 100.0, taxable=True)],
                             taxable=True, tax_rate=8.0)
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res
    assert "TxnTaxDetail" not in fake.created["Invoice"]


# ── success path ────────────────────────────────────────────────────────────

def test_success_marks_synced(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res == {"ok": True, "qbo_id": "QBO-INV-1"}
    db.expire_all()
    fresh = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert fresh.qbo_invoice_id == "QBO-INV-1"
    assert fresh.qbo_sync_status == QBOSyncStatus.SYNCED


def test_already_synced_is_skipped(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    QBOSyncService(db).push_invoice(inv.id)
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"] and res.get("skipped") == "already synced"


def test_customer_created_when_absent(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(customers={}))  # none found → must create
    _connect(db)
    cust = _customer(db, name="Brand New Co")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res
    assert fake.created["Customer"]["DisplayName"] == "Brand New Co"
    db.expire_all()
    assert db.query(Customer).get(cust.id).qbo_customer_id == "QBO-CUST-NEW"


# ── THE SAFETY GATE: failure must never touch the invoice ─────────────────────

def test_api_failure_marks_error_without_touching_money(db, monkeypatch):
    _install(monkeypatch, FakeQBO(fail_invoice=True))
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    total_before, status_before = inv.total, inv.status
    res = QBOSyncService(db).push_invoice(inv.id)          # must NOT raise
    assert res["ok"] is False and "400" in res["error"]
    db.expire_all()
    fresh = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert fresh.qbo_sync_status == QBOSyncStatus.ERROR
    assert fresh.qbo_sync_retry_count == 1
    assert fresh.qbo_invoice_id is None
    assert fresh.total == total_before          # money untouched
    assert fresh.status == status_before        # status untouched


def test_network_error_is_soft(db, monkeypatch):
    _install(monkeypatch, FakeQBO(network_error=True))
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    res = QBOSyncService(db).push_invoice(inv.id)          # must NOT raise
    assert res["ok"] is False
    db.expire_all()
    assert db.query(Invoice).get(inv.id).qbo_sync_status == QBOSyncStatus.ERROR


def test_missing_item_is_actionable_and_soft(db, monkeypatch):
    # Only one of the needed generic items exists → resolve fails with guidance.
    _install(monkeypatch, FakeQBO(items={"JAKS Parts Sales": "ITEM-1"}))
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.CORE_CHARGE, 50.0)])
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"] is False
    assert "Set up QBO items" in res["error"]
    db.expire_all()
    assert db.query(Invoice).get(inv.id).qbo_sync_status == QBOSyncStatus.ERROR


def test_not_connected_is_soft(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    for k in ("qbo_access_token", "qbo_refresh_token", "qbo_realm_id"):
        set_setting_value_db(db, k, "")
    db.commit()
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"] is False and "not connected" in res["error"].lower()


def test_draft_invoice_not_pushed(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = Invoice(invoice_number=f"INV-QBO-{next(_seq):05d}", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT, tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT, qty=1,
                       unit_price=100.0, unit_cost=60.0, description="x"))
    db.commit()
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"] is False and "finalize" in res["error"].lower()


# ── OAuth ─────────────────────────────────────────────────────────────────────

def test_authorize_url_and_state(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    url = qc.authorize_url(db)
    assert url.startswith(qc.AUTHORIZE_URL)
    assert "client_id=CID" in url and "scope=com.intuit.quickbooks.accounting" in url
    state = get_setting_value_db(db, "qbo_oauth_state", "")
    assert state and f"state={state}" in url


def test_exchange_code_stores_tokens(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    set_setting_value_db(db, "qbo_client_id", "CID")
    set_setting_value_db(db, "qbo_client_secret", "CSEC")
    set_setting_value_db(db, "qbo_oauth_state", "STATE-123")
    db.commit()
    qc.exchange_code(db, code="AUTH-CODE", realm_id="REALM-9", state="STATE-123")
    assert get_setting_value_db(db, "qbo_access_token") == "AT-NEW"
    assert get_setting_value_db(db, "qbo_refresh_token") == "RT-NEW"
    assert get_setting_value_db(db, "qbo_realm_id") == "REALM-9"
    # state cleared, expiry stamped
    assert get_setting_value_db(db, "qbo_oauth_state") == ""
    assert get_setting_value_db(db, "qbo_token_expires_at")


def test_exchange_code_rejects_bad_state(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    set_setting_value_db(db, "qbo_oauth_state", "GOOD"); db.commit()
    with pytest.raises(qc.QBOError):
        qc.exchange_code(db, code="C", realm_id="R", state="EVIL")


def test_expired_token_triggers_refresh(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db, expires_at="2000-01-01T00:00:00")   # already expired
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res
    # a refresh_token grant was issued and the new token persisted
    assert any(c["data"].get("grant_type") == "refresh_token" for c in fake.token_calls)
    assert get_setting_value_db(db, "qbo_access_token") == "AT-NEW"


# ── one-time item setup ───────────────────────────────────────────────────────

def test_ensure_default_items_creates_missing(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(items={"JAKS Parts Sales": "ITEM-1"}))
    _connect(db)
    res = QBOSyncService(db).ensure_default_items()
    assert res["ok"], res
    assert "JAKS Parts Sales" in res["existing"]
    assert set(res["created"]) == set(_ITEM_IDS) - {"JAKS Parts Sales"}
    assert res["income_account"] == "Sales of Product Income"
