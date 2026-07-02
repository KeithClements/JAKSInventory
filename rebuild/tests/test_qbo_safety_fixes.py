"""
tests/test_qbo_safety_fixes.py
==============================
QBO safety sprint — three confirmed defects locked in:

  1. RBAC: every user-triggerable push (invoice / payment / vendor-bill /
     credit-memo) asserts Permission.REPUSH_QBO up-front. A real SALES user
     raises PermissionDeniedError before any lookup or network call; the system
     actor (current_user_id=None — how the background retry worker constructs
     the service, app/main.py) and BOOKKEEPING/ADMIN pass.
  2. Core charges are pass-through liability, not income: ensure_default_items
     binds (or sparse-rebinds) 'JAKS Core Charge' to the owner-mapped
     qbo_core_charge_liability_account; unmapped keeps the income binding but
     push_invoice logs a loud warning on every core-carrying push.
  3. Card-surcharge cash reaches QBO: a payment with surcharge_amount > 0 also
     pushes a one-line SalesReceipt (no DepositToAccountRef — must land in the
     same Undeposited Funds default as the pushed Payment so the combined bank
     deposit reconciles), idempotent by deterministic DocNumber; surcharge=0
     produces nothing extra; a receipt failure never fails the payment sync.

Regression pins added after the safety sprint:

  4. push_payment's status!=APPLIED refusal runs BEFORE the already-synced
     healing branch: a REVERSED payment that already carries a qbo_payment_id
     (synced, then bounced) must be refused outright — the healing branch mints
     QBO documents (the surcharge SalesReceipt) for money that was never kept.
  5. Route-level RBAC (forged-session pattern from tests/test_money_route_rbac):
     a real SALES user POSTing /qbo/invoices/{id}/push is bounced with the
     denial flash and the fake Intuit client sees ZERO created documents;
     ADMIN/BOOKKEEPING pass the permission gate (the denial message is never
     their refusal).

All Intuit HTTP is faked (no live calls), same shim pattern as
tests/test_qbo_sync.py.
"""
from __future__ import annotations

import itertools
import logging
import pathlib
import re
import sys
import types
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

import app.database as _appdb
import app.services.qbo_client as qc
from app.auth import SESSION_COOKIE, make_session_token
from app.constants import (
    InvoiceStatus, LineType, PaymentMethod, PaymentStatus, QBOSyncStatus,
    UserRole,
)
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.models.user import User
from app.services.base import PermissionDeniedError
from app.services.invoice_service import InvoiceService
from app.services.qbo_service import (
    CORE_CHARGE_ITEM, DEFAULT_ITEM_MAP, QBO_ACCOUNT_FIELDS, SURCHARGE_ITEM,
    QBOSyncService,
)
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # admin, seeded by the app-startup hook
_seq = itertools.count(1)
_ITEM_IDS = {name: f"ITEM-{i}" for i, name in enumerate(sorted(set(DEFAULT_ITEM_MAP.values())), 1)}
_INCOME_ACCT = {"Id": "77", "Name": "Sales of Product Income", "AccountType": "Income"}
_LIABILITY_ACCT = {"Id": "88", "Name": "Core Deposits", "AccountType": "Other Current Liability"}
_SURCHARGE_ACCT = {"Id": "99", "Name": "Card Surcharge Income", "AccountType": "Income"}


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


def _where(q: str, field: str) -> str | None:
    m = re.search(rf"where\s+{field}\s*=\s*'([^']*)'", q, re.IGNORECASE)
    return m.group(1) if m else None


class FakeQBO:
    """Same two call surfaces as tests/test_qbo_sync.py's fake, plus:
    where-Name/DocNumber filtering (the surcharge + account resolvers query by
    exact name), Item sparse updates, and SalesReceipt create/query."""

    def __init__(self, *, items=None, item_accounts=None, customers=None,
                 accounts=None, fail_receipt=False):
        self.items = dict(items) if items is not None else dict(_ITEM_IDS)
        # item name → the account id its IncomeAccountRef points at
        self.item_accounts = dict(item_accounts) if item_accounts is not None else {
            n: _INCOME_ACCT["Id"] for n in self.items
        }
        self.customers = customers or {}
        self.accounts = list(accounts) if accounts is not None else [
            dict(_INCOME_ACCT), dict(_LIABILITY_ACCT), dict(_SURCHARGE_ACCT),
        ]
        self.fail_receipt = fail_receipt
        self.receipts: dict[str, str] = {}     # DocNumber → id
        self.created: dict[str, dict] = {}     # entity → last payload posted
        self.item_creates: list[dict] = []
        self.item_updates: list[dict] = []
        self.receipt_creates: list[dict] = []
        self._n = itertools.count(1)
        self.token_calls: list[dict] = []

    # API surface
    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        u = url.lower()
        if u.endswith("/query"):
            return self._query((params or {}).get("query", ""))
        if "/item/" in u and method.upper() == "GET":
            item_id = url.rsplit("/", 1)[-1]
            name = next((n for n, i in self.items.items() if i == item_id), "")
            return _Resp(200, {"Item": {"Id": item_id, "SyncToken": "0", "Name": name}})
        if u.endswith("/item"):
            if json.get("Id"):  # sparse rebind
                self.item_updates.append(json)
                name = next((n for n, i in self.items.items() if i == json["Id"]), "")
                if name:
                    self.item_accounts[name] = json["IncomeAccountRef"]["value"]
                return _Resp(200, {"Item": {"Id": json["Id"]}})
            self.item_creates.append(json)
            new_id = f"NEW-ITEM-{next(self._n)}"
            self.items[json["Name"]] = new_id
            self.item_accounts[json["Name"]] = json["IncomeAccountRef"]["value"]
            return _Resp(200, {"Item": {"Id": new_id}})
        if u.endswith("/salesreceipt"):
            self.receipt_creates.append(json)
            if self.fail_receipt:
                return _Resp(400, {"Fault": {"Error": [{"Message": "Invalid"}]}},
                             text="Invalid sales receipt")
            new_id = f"QBO-SR-{next(self._n)}"
            self.receipts[json.get("DocNumber", "")] = new_id
            return _Resp(200, {"SalesReceipt": {"Id": new_id}})
        if u.endswith("/invoice"):
            self.created["Invoice"] = json
            return _Resp(200, {"Invoice": {"Id": "QBO-INV-1"}})
        if u.endswith("/payment"):
            self.created["Payment"] = json
            return _Resp(200, {"Payment": {"Id": "QBO-PAY-1"}})
        if u.endswith("/customer"):
            self.created["Customer"] = json
            return _Resp(200, {"Customer": {"Id": "QBO-CUST-NEW"}})
        return _Resp(200, {})

    def _query(self, q: str) -> _Resp:
        ql = q.lower()
        if "from item" in ql:
            name = _where(q, "Name")
            rows = [
                {"Id": i, "Name": n,
                 "IncomeAccountRef": {"value": self.item_accounts.get(n, "")}}
                for n, i in self.items.items()
                if name is None or n == name
            ]
            return _Resp(200, {"QueryResponse": {"Item": rows}})
        if "from customer" in ql:
            return _Resp(200, {"QueryResponse": {"Customer": [
                {"Id": i, "DisplayName": n} for n, i in self.customers.items()]}})
        if "from account" in ql:
            name = _where(q, "Name")
            rows = [
                a for a in self.accounts
                if (name is None or a["Name"] == name)
                and ("accounttype = 'income'" not in ql or a["AccountType"] == "Income")
            ]
            return _Resp(200, {"QueryResponse": {"Account": rows}})
        if "from salesreceipt" in ql:
            doc = _where(q, "DocNumber")
            rows = [{"Id": i, "DocNumber": d} for d, i in self.receipts.items()
                    if doc is None or d == doc]
            return _Resp(200, {"QueryResponse": {"SalesReceipt": rows}})
        return _Resp(200, {"QueryResponse": {}})

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
    shim = types.SimpleNamespace(
        request=fake.request, post=fake.post,
        QueryParams=httpx.QueryParams, HTTPError=httpx.HTTPError,
        ConnectError=httpx.ConnectError,
    )
    monkeypatch.setattr(qc, "httpx", shim)
    return fake


# ── builders ──────────────────────────────────────────────────────────────────

def _connect(db):
    for k, v in {
        "qbo_client_id": "CID", "qbo_client_secret": "CSEC", "qbo_realm_id": "REALM-1",
        "qbo_access_token": "AT-OLD", "qbo_refresh_token": "RT-OLD",
        "qbo_token_expires_at": "2999-01-01T00:00:00", "qbo_environment": "sandbox",
    }.items():
        set_setting_value_db(db, k, v)
    db.commit()


def _setting(db, key: str, value: str) -> None:
    set_setting_value_db(db, key, value)
    db.commit()


def _user(db, role: str) -> User:
    n = next(_seq)
    u = User(name=f"{role}-{n}", username=f"qbo-safety-{role}-{n}",
             password_hash="x", role=role)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _customer(db, *, qbo_id="QBO-CUST-9") -> Customer:
    c = Customer(company_name=f"QBO Safety Co {next(_seq)}", contact_name="QA",
                 email=f"qbo-safety-{next(_seq)}@test.local", qbo_customer_id=qbo_id)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _line(line_type, price, *, qty=1, desc=""):
    return dict(line_type=line_type, description=desc or "Test line", qty=qty,
                unit_price=price, unit_cost=price * 0.6, is_taxable=False)


def _finalized_invoice(db, customer_id, lines) -> Invoice:
    inv = Invoice(
        invoice_number=f"INV-QSF-{next(_seq):05d}", customer_id=customer_id,
        status=InvoiceStatus.DRAFT, is_taxable=False, tax_rate=0.0,
        tax_rate_snapshot=0.0, tax_exempt_snapshot=True,
    )
    db.add(inv); db.flush()
    for ln in lines:
        db.add(InvoiceLine(invoice_id=inv.id, **ln))
    db.commit()
    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _synced_invoice(db, customer_id) -> Invoice:
    inv = Invoice(
        invoice_number=f"INV-QSF-{next(_seq):05d}", customer_id=customer_id,
        status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
        tax_rate_snapshot=0.0, tax_exempt_snapshot=True,
        qbo_invoice_id=f"QBO-INV-PRE-{next(_seq)}", qbo_sync_status=QBOSyncStatus.SYNCED,
    )
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=100.0,
                                 unit_cost=60.0, is_taxable=False, description="part"))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _payment(db, customer_id, invoice_id, *, amount=100.0, surcharge=0.0) -> Payment:
    pmt = Payment(customer_id=customer_id, amount_received=amount,
                  surcharge_amount=surcharge, payment_method=PaymentMethod.CREDIT_CARD,
                  status=PaymentStatus.APPLIED)
    db.add(pmt); db.flush()
    db.add(PaymentAllocation(payment_id=pmt.id, invoice_id=invoice_id,
                             amount_applied=amount))
    db.commit(); db.refresh(pmt)
    return pmt


# ── 1. RBAC on every push entry point ─────────────────────────────────────────

def test_sales_role_cannot_push_any_document(db):
    """A real SALES User row is role-enforced even under JAKS_SKIP_AUTH. The
    gate fires before any lookup/network call, so bogus ids suffice."""
    sales = _user(db, UserRole.SALES)
    svc = QBOSyncService(db, current_user_id=sales.id)
    for push in (svc.push_invoice, svc.push_payment,
                 svc.push_vendor_bill, svc.push_credit_memo):
        with pytest.raises(PermissionDeniedError):
            push(999999)


def test_read_only_role_cannot_push(db):
    ro = _user(db, UserRole.READ_ONLY)
    with pytest.raises(PermissionDeniedError):
        QBOSyncService(db, current_user_id=ro.id).push_invoice(999999)


def test_system_actor_push_allowed(db, monkeypatch):
    """current_user_id=None is exactly how the background retry worker
    constructs the service (app/main.py) — the gate must not break it."""
    _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_core_charge_liability_account", "")
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res


def test_bookkeeping_can_push_invoice(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    book = _user(db, UserRole.BOOKKEEPING)
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    res = QBOSyncService(db, current_user_id=book.id).push_invoice(inv.id)
    assert res["ok"], res


def test_admin_can_push_payment(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    admin = _user(db, UserRole.ADMIN)
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id)
    res = QBOSyncService(db, current_user_id=admin.id).push_payment(pmt.id)
    assert res["ok"], res


# ── 1b. status gate runs BEFORE the already-synced healing branch ─────────────

def test_reversed_synced_payment_with_surcharge_refused_no_receipt(db, monkeypatch):
    """A REVERSED payment that already carries a qbo_payment_id (synced in a
    prior life, then bounced/NSF'd) must hit the status refusal BEFORE the
    already-synced healing branch: the healing path mints QBO documents (the
    idempotent surcharge SalesReceipt), and a reversal means that surcharge
    cash was never kept. A revert that re-orders the two branches returns
    ok/skipped AND creates a SalesReceipt here — both assertions trip."""
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_surcharge_income_account", "")
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0, surcharge=3.5)
    pmt.qbo_payment_id = f"QBO-PAY-PRE-{next(_seq)}"
    pmt.status = PaymentStatus.REVERSED
    db.commit()

    res = QBOSyncService(db).push_payment(pmt.id)

    assert res["ok"] is False, f"reversed payment was not refused: {res}"
    assert "reversed" in res.get("error", "").lower()
    assert "skipped" not in res and "surcharge_qbo_id" not in res
    # The fake Intuit client saw NO document creation of any kind.
    assert fake.receipt_creates == [], (
        "the healing branch minted a surcharge SalesReceipt for a REVERSED "
        "payment — the status gate no longer runs first")
    assert fake.created == {}
    assert fake.receipts == {}


# ── 1c. RBAC through the /qbo push ROUTE (forged-session pattern) ─────────────

def _login(client, user_id: int) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, make_session_token(user_id))


def test_qbo_push_route_registered():
    registered = {
        (m, getattr(r, "path", None))
        for r in app.routes
        for m in (getattr(r, "methods", None) or set())
    }
    assert ("POST", "/qbo/invoices/{invoice_id}/push") in registered, (
        "the invoice push route moved — the route-RBAC tests below would "
        "false-pass on a 404")


def test_sales_user_denied_via_push_route_nothing_created(db, client, monkeypatch):
    """Route → service chain: a real SALES User (signed session cookie) POSTing
    the invoice push route gets the denial flash redirect and the fake Intuit
    client sees zero traffic — the PermissionDeniedError is caught by the
    route, never a 500, and never after any QBO work."""
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    sales = _user(db, UserRole.SALES)
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    try:
        _login(client, sales.id)
        r = client.post(f"/qbo/invoices/{inv.id}/push", follow_redirects=False)
        assert r.status_code == 303, (
            f"expected the denial redirect, got {r.status_code}")
        loc = unquote(r.headers.get("location", ""))
        assert f"/invoices/{inv.id}" in loc and "error=" in loc, loc
        assert "bookkeeping or admin" in loc.lower(), (
            f"SALES was not refused with the permission flash: {loc!r}")
        # NOTHING reached QBO.
        assert fake.created == {}
        assert fake.item_creates == [] and fake.receipt_creates == []
        db.expire_all()
        fresh = db.query(Invoice).filter(Invoice.id == inv.id).first()
        assert not fresh.qbo_invoice_id, (
            "SALES user PUSHED an invoice through the route — "
            "POST /qbo/invoices/{id}/push is not enforcing REPUSH_QBO")
    finally:
        client.cookies.clear()


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.BOOKKEEPING])
def test_privileged_roles_pass_push_route_permission_gate(db, client, monkeypatch, role):
    """Control: ADMIN/BOOKKEEPING get PAST the permission gate. Anything after
    that may fail for unrelated reasons — the only pinned invariant is that the
    RBAC denial is never their refusal (proves the SALES bounce above is
    role-based, not seed/route breakage)."""
    _install(monkeypatch, FakeQBO())
    _connect(db)
    user = _user(db, role)
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id, [_line(LineType.PRODUCT, 100.0)])
    try:
        _login(client, user.id)
        r = client.post(f"/qbo/invoices/{inv.id}/push", follow_redirects=False)
        assert r.status_code == 303
        loc = unquote(r.headers.get("location", ""))
        assert "bookkeeping or admin access" not in loc.lower(), (
            f"{role} was refused by the PERMISSION gate: {loc!r}")
    finally:
        client.cookies.clear()


# ── 2. core charges → liability account ──────────────────────────────────────

def test_setup_binds_core_item_to_liability_when_configured(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(items={}))
    _connect(db)
    _setting(db, "qbo_core_charge_liability_account", _LIABILITY_ACCT["Name"])
    res = QBOSyncService(db).ensure_default_items()
    assert res["ok"], res
    assert res["core_charge_account"] == _LIABILITY_ACCT["Name"]
    by_name = {p["Name"]: p for p in fake.item_creates}
    assert by_name[CORE_CHARGE_ITEM]["IncomeAccountRef"]["value"] == _LIABILITY_ACCT["Id"]
    for name, payload in by_name.items():
        if name != CORE_CHARGE_ITEM:
            assert payload["IncomeAccountRef"]["value"] == _INCOME_ACCT["Id"], name


def test_setup_rebinds_existing_core_item_via_sparse_update(db, monkeypatch):
    # All items already exist, bound to income → only the core item is rebound.
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_core_charge_liability_account", _LIABILITY_ACCT["Name"])
    res = QBOSyncService(db).ensure_default_items()
    assert res["ok"], res
    assert res["rebound"] == [CORE_CHARGE_ITEM]
    assert fake.item_creates == []
    assert len(fake.item_updates) == 1
    upd = fake.item_updates[0]
    assert upd["sparse"] is True
    assert upd["Id"] == _ITEM_IDS[CORE_CHARGE_ITEM]
    assert upd["IncomeAccountRef"]["value"] == _LIABILITY_ACCT["Id"]
    # Idempotent: a second run finds the item already bound and does nothing.
    res2 = QBOSyncService(db).ensure_default_items()
    assert res2["ok"] and res2["rebound"] == []
    assert len(fake.item_updates) == 1


def test_setup_without_liability_setting_keeps_income_binding(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(items={}))
    _connect(db)
    _setting(db, "qbo_core_charge_liability_account", "")
    res = QBOSyncService(db).ensure_default_items()
    assert res["ok"], res
    assert "core_charge_account" not in res
    by_name = {p["Name"]: p for p in fake.item_creates}
    assert by_name[CORE_CHARGE_ITEM]["IncomeAccountRef"]["value"] == _INCOME_ACCT["Id"]
    assert fake.item_updates == []


def test_core_push_warns_when_liability_unmapped(db, monkeypatch, caplog):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_core_charge_liability_account", "")
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id, [
        _line(LineType.PRODUCT, 100.0), _line(LineType.CORE_CHARGE, 50.0),
    ])
    with caplog.at_level(logging.WARNING, logger="app.services.qbo_service"):
        res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res  # warning only — the push itself must not be blocked
    assert any("qbo_core_charge_liability_account" in r.getMessage()
               for r in caplog.records)


def test_core_push_does_not_warn_when_mapped(db, monkeypatch, caplog):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_core_charge_liability_account", _LIABILITY_ACCT["Name"])
    cust = _customer(db)
    inv = _finalized_invoice(db, cust.id, [
        _line(LineType.PRODUCT, 100.0), _line(LineType.CORE_CHARGE, 50.0),
    ])
    with caplog.at_level(logging.WARNING, logger="app.services.qbo_service"):
        res = QBOSyncService(db).push_invoice(inv.id)
    assert res["ok"], res
    assert not any("qbo_core_charge_liability_account" in r.getMessage()
                   for r in caplog.records)


def test_new_account_fields_registered():
    """The settings page renders QBO_ACCOUNT_FIELDS generically — presence here
    means the two new mappings appear in Settings → QBO Accounts."""
    keys = {f["key"] for f in QBO_ACCOUNT_FIELDS}
    assert {"qbo_core_charge_liability_account", "qbo_surcharge_income_account"} <= keys


def test_account_mappings_include_new_keys(db):
    m = QBOSyncService(db).account_mappings()
    assert "qbo_core_charge_liability_account" in m
    assert "qbo_surcharge_income_account" in m


# ── 3. card-surcharge SalesReceipt ────────────────────────────────────────────

def test_payment_with_surcharge_pushes_sales_receipt(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_surcharge_income_account", "")
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0, surcharge=3.5)

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"], res
    assert res.get("surcharge_qbo_id")
    assert len(fake.receipt_creates) == 1
    rc = fake.receipt_creates[0]
    assert rc["Line"][0]["Amount"] == pytest.approx(3.5)
    assert rc["Line"][0]["SalesItemLineDetail"]["ItemRef"]["value"] == fake.items[SURCHARGE_ITEM]
    assert rc["DocNumber"] == f"JAKS-SC-{pmt.id}"
    assert rc["CustomerRef"]["value"] == "QBO-CUST-9"
    # Deposit-matching: the pushed Payment carries no DepositToAccountRef
    # (→ Undeposited Funds), so the receipt must not either.
    assert "DepositToAccountRef" not in rc
    assert "DepositToAccountRef" not in fake.created["Payment"]
    # The QBO Payment itself is still principal-only (R1).
    assert fake.created["Payment"]["TotalAmt"] == pytest.approx(100.0)
    # Item was auto-created bound to the default income account (no mapping).
    assert fake.item_accounts[SURCHARGE_ITEM] == _INCOME_ACCT["Id"]


def test_surcharge_item_uses_mapped_income_account(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_surcharge_income_account", _SURCHARGE_ACCT["Name"])
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=50.0, surcharge=1.75)

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"], res
    assert fake.item_accounts[SURCHARGE_ITEM] == _SURCHARGE_ACCT["Id"]


def test_zero_surcharge_creates_nothing_extra(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0, surcharge=0.0)

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"], res
    assert "surcharge_qbo_id" not in res
    assert fake.receipt_creates == []


def test_repush_does_not_duplicate_surcharge_receipt(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    _setting(db, "qbo_surcharge_income_account", "")
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0, surcharge=2.25)

    first = QBOSyncService(db).push_payment(pmt.id)
    assert first["ok"] and first.get("surcharge_qbo_id")
    second = QBOSyncService(db).push_payment(pmt.id)
    assert second["ok"] and second.get("skipped") == "already synced"
    # The repush found the existing receipt (DocNumber guard) — no second create.
    assert len(fake.receipt_creates) == 1
    assert second.get("surcharge_qbo_id") == first["surcharge_qbo_id"]


def test_surcharge_receipt_failure_never_fails_payment_push(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(fail_receipt=True))
    _connect(db)
    _setting(db, "qbo_surcharge_income_account", "")
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0, surcharge=4.0)

    res = QBOSyncService(db).push_payment(pmt.id)   # must NOT raise
    assert res["ok"], res
    assert "surcharge_qbo_id" not in res
    db.expire_all()
    fresh = db.query(Payment).filter(Payment.id == pmt.id).first()
    assert fresh.qbo_sync_status == QBOSyncStatus.SYNCED   # payment sync intact
    # Repush heals the missing receipt (idempotent DocNumber guard on a now-
    # working connection).
    fake.fail_receipt = False
    healed = QBOSyncService(db).push_payment(pmt.id)
    assert healed["ok"] and healed.get("skipped") == "already synced"
    assert healed.get("surcharge_qbo_id")
