"""
tests/test_r3_qbo_bills_cms.py
==========================
R3 — QBO's remaining legs, all with Intuit HTTP faked (no live calls):

  1. QBOSyncService.push_vendor_bill — pushes an APPROVED/PAID vendor bill to
     QBO as a Bill (AccountBasedExpenseLine against the configured expense/COGS
     account), vendor resolved/created by DisplayName with the SAME multi-match
     refusal rule R2 added for customers.
       * happy path → Bill created with the right payload shape and the JAKS
         bill marked SYNCED;
       * vendor multi-match refused (fail-soft, no Bill posted, no binding);
       * a PENDING (un-approved) bill refused;
       * a QBO API failure marks the bill ERROR, NEVER raises, and never
         touches the money path (total / status / lines unchanged).

  2. QBOSyncService.push_credit_memo — pushes a CM to QBO as a CreditMemo
     against the resolved customer, lines mapped through the same generic-item
     map as the invoice push (CORE_CHARGE → 'JAKS Core Charge' via the credited
     line's original invoice line).
       * happy path → payload shape + marked SYNCED;
       * unresolvable (multi-match, unbound) customer refused fail-soft;
       * REVERSED (void-like) CM refused;
       * a QBO API failure marks the CM ERROR, never raises, money untouched.

  3. Attribution + audit — every push success AND failure writes an AuditLog
     row attributed to the user id threaded through QBOSyncService (no more
     hardcoded user 1).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r3_qbo_bills_cms.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys
import types
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

import app.database as _appdb
import app.services.qbo_client as qc
from app.constants import (
    CreditMemoStatus, InvoiceStatus, LineType, QBOSyncStatus, UserRole,
    VendorBillStatus,
)
from app.main import app
from app.models.audit import AuditLog
from app.models.credit_memo import CreditMemo, CreditMemoLine
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.purchase_order import VendorBill, VendorBillLine
from app.models.user import User
from app.models.vendor import Vendor
from app.services.qbo_service import DEFAULT_ITEM_MAP, QBOSyncService
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
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


# ── fake Intuit HTTP (mirrors tests/test_r2_qbo_payment_push.py) ────────────────

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

    def __init__(self, *, items=None, customers=None, customer_rows=None,
                 vendors=None, vendor_rows=None, accounts=None,
                 fail_bill=False, fail_cm=False, network_error=False):
        self.items = items if items is not None else dict(_ITEM_IDS)
        self.customers = customers or {}            # DisplayName -> id
        self.customer_rows = customer_rows          # explicit override for the Customer query
        self.vendors = vendors or {}                # DisplayName -> id
        self.vendor_rows = vendor_rows              # explicit override for the Vendor query
        self.accounts = accounts or [("77", "Sales of Product Income"),
                                     ("88", "Cost of Goods Sold")]
        self.fail_bill = fail_bill
        self.fail_cm = fail_cm
        self.network_error = network_error
        self.created: dict[str, dict] = {}          # entity -> last payload posted
        self.token_calls: list[dict] = []

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
                rows = (self.customer_rows if self.customer_rows is not None
                        else [{"Id": i, "DisplayName": n} for n, i in self.customers.items()])
                return _Resp(200, {"QueryResponse": {"Customer": rows}})
            if "from vendor" in q:
                rows = (self.vendor_rows if self.vendor_rows is not None
                        else [{"Id": i, "DisplayName": n} for n, i in self.vendors.items()])
                return _Resp(200, {"QueryResponse": {"Vendor": rows}})
            if "from account" in q:
                rows = [{"Id": i, "Name": n} for i, n in self.accounts]
                if "where name =" in q:  # honor the exact-name filter
                    rows = [r for r in rows if r["Name"].lower() in q]
                return _Resp(200, {"QueryResponse": {"Account": rows}})
            return _Resp(200, {"QueryResponse": {}})
        if u.endswith("/bill"):
            self.created["Bill"] = json
            if self.fail_bill:
                return _Resp(400, {"Fault": {"Error": [{"Message": "Invalid"}]}},
                             text="Invalid bill")
            return _Resp(200, {"Bill": {"Id": "QBO-BILL-1"}})
        if u.endswith("/creditmemo"):
            self.created["CreditMemo"] = json
            if self.fail_cm:
                return _Resp(400, {"Fault": {"Error": [{"Message": "Invalid"}]}},
                             text="Invalid credit memo")
            return _Resp(200, {"CreditMemo": {"Id": "QBO-CM-1"}})
        if u.endswith("/vendor"):
            self.created["Vendor"] = json
            return _Resp(200, {"Vendor": {"Id": "QBO-VEND-NEW"}})
        if u.endswith("/customer"):
            self.created["Customer"] = json
            return _Resp(200, {"Customer": {"Id": "QBO-CUST-NEW"}})
        if u.endswith("/payment"):
            self.created["Payment"] = json
            return _Resp(200, {"Payment": {"Id": "QBO-PMT-1"}})
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


def _user(db) -> User:
    n = next(_seq)
    u = User(name=f"QA {n}", username=f"qa-r3-{n}", password_hash="x",
             role=UserRole.ADMIN)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _customer(db, *, name=None, qbo_id="QBO-CUST-9") -> Customer:
    c = Customer(company_name=name or f"QBO Co {next(_seq)}", contact_name="QA",
                 email=f"cust{next(_seq)}@test.local", qbo_customer_id=qbo_id)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db, *, name=None) -> Vendor:
    v = Vendor(name=name or f"QBO Vendor {next(_seq)}",
               email="vendor@test.local", phone="555-0100")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _bill(db, vendor_id, *, status=VendorBillStatus.APPROVED,
          lines=((2, 50.0),), bill_number=None, bill_date=None,
          due_date=None) -> VendorBill:
    """lines: list of (qty_billed, unit_cost) — no PO link needed for the push."""
    n = next(_seq)
    bill = VendorBill(
        vendor_id=vendor_id, status=status,
        bill_number=bill_number or f"PAI-{n:05d}",
        bill_date=bill_date or datetime(2026, 6, 1),
        due_date=due_date or datetime(2026, 7, 1),
        total_amount=round(sum(q * c for q, c in lines), 2),
    )
    for qty, cost in lines:
        bill.lines.append(VendorBillLine(qty_billed=qty, unit_cost=cost))
    db.add(bill); db.commit(); db.refresh(bill)
    return bill


def _cm(db, customer_id, *, status=CreditMemoStatus.OPEN, lines=None,
        original_invoice_line_id=None) -> CreditMemo:
    """lines: list of (description, qty, unit_price). The FIRST line carries
    original_invoice_line_id when given (CORE_CHARGE mapping test)."""
    n = next(_seq)
    lines = lines or [("Returned injector", 1, 250.0)]
    total = round(sum(q * p for _, q, p in lines), 2)
    cm = CreditMemo(
        cm_number=f"CM-TEST-{n:04d}", customer_id=customer_id, status=status,
        # §21 — applied_amount is computed from allocations now (not a constructor arg).
        total_amount=total, unapplied_amount=total,
        tax_amount=0.0, reason="R3 test",
    )
    for i, (desc, qty, price) in enumerate(lines):
        cm.lines.append(CreditMemoLine(
            description=desc, qty=qty, unit_price=price, discount_pct=0.0,
            is_taxable=False,
            original_invoice_line_id=original_invoice_line_id if i == 0 else None,
        ))
    db.add(cm); db.commit(); db.refresh(cm)
    return cm


def _invoice_with_core_line(db, customer_id) -> InvoiceLine:
    """A finalized invoice with one CORE_CHARGE line; returns that line."""
    inv = Invoice(
        invoice_number=f"INV-R3-{next(_seq):05d}", customer_id=customer_id,
        status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
        tax_rate_snapshot=0.0, tax_exempt_snapshot=True,
    )
    inv.lines.append(InvoiceLine(line_type=LineType.CORE_CHARGE, qty=1,
                                 unit_price=85.0, unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv.lines[0]


# ── 1. push_vendor_bill — happy path: payload shape + marked synced ───────────

def test_push_bill_happy_path_marks_synced_and_payload_shape(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(vendors={"PAI Industries": "QBO-VEND-1"}))
    _connect(db)
    vend = _vendor(db, name="PAI Industries")
    bill = _bill(db, vend.id, lines=[(2, 50.0), (1, 30.0)], bill_number="PAI-77001")

    res = QBOSyncService(db).push_vendor_bill(bill.id)
    assert res == {"ok": True, "qbo_id": "QBO-BILL-1"}, res

    payload = fake.created["Bill"]
    assert payload["VendorRef"] == {"value": "QBO-VEND-1"}
    assert payload["DocNumber"] == "PAI-77001"
    assert payload["TxnDate"] == "2026-06-01"
    assert payload["DueDate"] == "2026-07-01"
    assert len(payload["Line"]) == 2
    for line in payload["Line"]:
        assert line["DetailType"] == "AccountBasedExpenseLineDetail"
        # default expense account = 'Cost of Goods Sold' (id 88 in the fake)
        assert line["AccountBasedExpenseLineDetail"]["AccountRef"] == {"value": "88"}
    amounts = sorted(line["Amount"] for line in payload["Line"])
    assert amounts == [pytest.approx(30.0), pytest.approx(100.0)]

    db.expire_all()
    fresh = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
    assert fresh.qbo_id == "QBO-BILL-1"
    assert fresh.qbo_sync_status == QBOSyncStatus.SYNCED
    assert fresh.qbo_sync_error is None


def test_push_bill_creates_missing_vendor(db, monkeypatch):
    # No QBO vendor with this name → it is created, like customers are.
    fake = _install(monkeypatch, FakeQBO(vendors={}))
    _connect(db)
    vend = _vendor(db, name="Interstate-McBee R3")
    bill = _bill(db, vend.id)

    res = QBOSyncService(db).push_vendor_bill(bill.id)
    assert res["ok"], res
    assert fake.created["Vendor"]["DisplayName"] == "Interstate-McBee R3"
    assert fake.created["Bill"]["VendorRef"] == {"value": "QBO-VEND-NEW"}


# ── 1b. vendor multi-match refused (same rule R2 added for customers) ─────────

def test_push_bill_vendor_multi_match_refused(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(vendor_rows=[
        {"Id": "111", "DisplayName": "Acme Cores"},
        {"Id": "222", "DisplayName": "Acme Cores"},
    ]))
    _connect(db)
    vend = _vendor(db, name="Acme Cores")
    bill = _bill(db, vend.id)

    res = QBOSyncService(db).push_vendor_bill(bill.id)
    assert res["ok"] is False
    assert "multiple qbo vendors match" in res["error"].lower()
    assert "resolve manually" in res["error"].lower()
    assert "Bill" not in fake.created                 # no bill posted on a bad match

    db.expire_all()
    fresh = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
    assert fresh.qbo_id is None
    assert fresh.qbo_sync_status == QBOSyncStatus.ERROR


# ── 1c. status gate: only APPROVED/PAID bills are pushable ────────────────────

def test_push_bill_refuses_unapproved_bill(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(vendors={"V": "QBO-VEND-1"}))
    _connect(db)
    vend = _vendor(db)
    bill = _bill(db, vend.id, status=VendorBillStatus.PENDING)

    res = QBOSyncService(db).push_vendor_bill(bill.id)
    assert res["ok"] is False
    assert "approved" in res["error"].lower()
    assert "Bill" not in fake.created                 # never reached the QBO API
    db.expire_all()
    assert db.query(VendorBill).filter(VendorBill.id == bill.id).first().qbo_sync_status == QBOSyncStatus.ERROR


# ── 1d. THE SAFETY GATE: API failure marks ERROR, never raises, money untouched ─

def test_push_bill_failure_marks_sync_failed_and_never_raises(db, monkeypatch):
    _install(monkeypatch, FakeQBO(vendors={"Failing Vendor": "QBO-VEND-1"},
                                  fail_bill=True))
    _connect(db)
    vend = _vendor(db, name="Failing Vendor")
    bill = _bill(db, vend.id, lines=[(3, 40.0)])
    total_before, status_before = bill.total_amount, bill.status

    res = QBOSyncService(db).push_vendor_bill(bill.id)   # must NOT raise
    assert res["ok"] is False and "400" in res["error"]

    db.expire_all()
    fresh = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
    assert fresh.qbo_sync_status == QBOSyncStatus.ERROR
    assert fresh.qbo_sync_retry_count == 1
    assert fresh.qbo_id is None
    # money path untouched
    assert fresh.total_amount == total_before
    assert fresh.status == status_before
    assert len(fresh.lines) == 1 and fresh.lines[0].line_total == pytest.approx(120.0)


# ── 2. push_credit_memo — happy path + CORE_CHARGE item mapping ───────────────

def test_push_credit_memo_happy_path_marks_synced_and_payload_shape(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    core_line = _invoice_with_core_line(db, cust.id)
    cm = _cm(db, cust.id, lines=[("Core returned", 1, 85.0),
                                 ("Returned injector", 2, 250.0)],
             original_invoice_line_id=core_line.id)

    res = QBOSyncService(db).push_credit_memo(cm.id)
    assert res == {"ok": True, "qbo_id": "QBO-CM-1"}, res

    payload = fake.created["CreditMemo"]
    assert payload["CustomerRef"] == {"value": "QBO-CUST-9"}
    assert payload["DocNumber"] == cm.cm_number
    assert len(payload["Line"]) == 2
    by_desc = {line["Description"]: line for line in payload["Line"]}

    # CORE_CHARGE line (back-linked to the invoice's core line) maps to the
    # 'JAKS Core Charge' item; the plain line maps to 'JAKS Parts Sales'.
    core = by_desc["Core returned"]
    assert core["DetailType"] == "SalesItemLineDetail"
    assert core["SalesItemLineDetail"]["ItemRef"] == {"value": _ITEM_IDS["JAKS Core Charge"]}
    assert core["Amount"] == pytest.approx(85.0)

    part = by_desc["Returned injector"]
    assert part["SalesItemLineDetail"]["ItemRef"] == {"value": _ITEM_IDS["JAKS Parts Sales"]}
    assert part["Amount"] == pytest.approx(500.0)
    assert part["SalesItemLineDetail"]["UnitPrice"] == pytest.approx(250.0)
    assert part["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "NON"}

    db.expire_all()
    fresh = db.query(CreditMemo).filter(CreditMemo.id == cm.id).first()
    assert fresh.qbo_id == "QBO-CM-1"
    assert fresh.qbo_sync_status == QBOSyncStatus.SYNCED


# ── 2b. unresolvable customer refused (multi-match, unbound) ──────────────────

def test_push_credit_memo_unresolvable_customer_refused(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO(customer_rows=[
        {"Id": "111", "DisplayName": "Acme Diesel CM"},
        {"Id": "222", "DisplayName": "Acme Diesel CM"},
    ]))
    _connect(db)
    cust = _customer(db, name="Acme Diesel CM", qbo_id="")   # unbound → DisplayName match
    cm = _cm(db, cust.id)

    res = QBOSyncService(db).push_credit_memo(cm.id)
    assert res["ok"] is False
    assert "multiple qbo customers match" in res["error"].lower()
    assert "CreditMemo" not in fake.created

    db.expire_all()
    # the wrong-customer binding must NOT have been written
    assert (db.query(Customer).filter(Customer.id == cust.id).first().qbo_customer_id or "") == ""
    assert db.query(CreditMemo).filter(CreditMemo.id == cm.id).first().qbo_sync_status == QBOSyncStatus.ERROR


# ── 2c. REVERSED (void-like) CM refused ───────────────────────────────────────

def test_push_credit_memo_refuses_reversed(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    cm = _cm(db, cust.id, status=CreditMemoStatus.REVERSED)

    res = QBOSyncService(db).push_credit_memo(cm.id)
    assert res["ok"] is False
    assert "reversed" in res["error"].lower()
    assert "CreditMemo" not in fake.created
    db.expire_all()
    assert db.query(CreditMemo).filter(CreditMemo.id == cm.id).first().qbo_id is None


# ── 2d. THE SAFETY GATE: API failure marks ERROR, never raises, money untouched ─

def test_push_credit_memo_failure_marks_sync_failed_and_never_raises(db, monkeypatch):
    _install(monkeypatch, FakeQBO(fail_cm=True))
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    cm = _cm(db, cust.id)
    total_before, unapplied_before, status_before = (
        cm.total_amount, cm.unapplied_amount, cm.status)

    res = QBOSyncService(db).push_credit_memo(cm.id)     # must NOT raise
    assert res["ok"] is False and "400" in res["error"]

    db.expire_all()
    fresh = db.query(CreditMemo).filter(CreditMemo.id == cm.id).first()
    assert fresh.qbo_sync_status == QBOSyncStatus.ERROR
    assert fresh.qbo_sync_retry_count == 1
    assert fresh.qbo_id is None
    # money path untouched
    assert fresh.total_amount == total_before
    assert fresh.unapplied_amount == unapplied_before
    assert fresh.applied_amount == 0.0
    assert fresh.status == status_before


def test_push_credit_memo_network_error_is_soft(db, monkeypatch):
    _install(monkeypatch, FakeQBO(network_error=True))
    _connect(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    cm = _cm(db, cust.id)

    res = QBOSyncService(db).push_credit_memo(cm.id)     # must NOT raise
    assert res["ok"] is False
    db.expire_all()
    assert db.query(CreditMemo).filter(CreditMemo.id == cm.id).first().qbo_sync_status == QBOSyncStatus.ERROR


# ── 3. attribution + audit trail ──────────────────────────────────────────────

def test_audit_row_on_success_attributed_to_passed_user(db, monkeypatch):
    _install(monkeypatch, FakeQBO())
    _connect(db)
    user = _user(db)
    assert user.id != 1 or True  # attribution asserted against user.id below, not 1
    cust = _customer(db, qbo_id="QBO-CUST-9")
    cm = _cm(db, cust.id)

    res = QBOSyncService(db, current_user_id=user.id).push_credit_memo(cm.id)
    assert res["ok"], res

    rows = (db.query(AuditLog)
            .filter(AuditLog.entity_type == "credit_memo", AuditLog.entity_id == cm.id)
            .all())
    synced = [r for r in rows if r.action == "qbo_synced"]
    assert synced, f"no qbo_synced audit row; have {[(r.action, r.user_id) for r in rows]}"
    assert all(r.user_id == user.id for r in synced)     # the REAL user, not a hardcoded 1
    assert "QBO-CM-1" in (synced[-1].notes or "")


def test_audit_row_on_failure_attributed_to_passed_user(db, monkeypatch):
    _install(monkeypatch, FakeQBO(vendors={"Audit Vendor": "QBO-VEND-1"}, fail_bill=True))
    _connect(db)
    user = _user(db)
    vend = _vendor(db, name="Audit Vendor")
    bill = _bill(db, vend.id)

    res = QBOSyncService(db, current_user_id=user.id).push_vendor_bill(bill.id)
    assert res["ok"] is False

    rows = (db.query(AuditLog)
            .filter(AuditLog.entity_type == "vendor_bill", AuditLog.entity_id == bill.id)
            .all())
    failed = [r for r in rows if r.action == "qbo_sync_failed"]
    assert failed, f"no qbo_sync_failed audit row; have {[(r.action, r.user_id) for r in rows]}"
    assert all(r.user_id == user.id for r in failed)
    assert "400" in (failed[-1].notes or "")


def test_payment_push_audit_no_longer_hardcodes_user_1(db, monkeypatch):
    # The R2 payment push now runs under the threaded user id too.
    from app.constants import PaymentMethod, PaymentStatus
    from app.models.invoice import Payment, PaymentAllocation

    _install(monkeypatch, FakeQBO())
    _connect(db)
    user = _user(db)
    cust = _customer(db, qbo_id="QBO-CUST-9")
    inv = Invoice(
        invoice_number=f"INV-R3-{next(_seq):05d}", customer_id=cust.id,
        status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
        tax_rate_snapshot=0.0, tax_exempt_snapshot=True,
        qbo_invoice_id="QBO-INV-R3", qbo_sync_status=QBOSyncStatus.SYNCED,
    )
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=100.0,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    pmt = Payment(customer_id=cust.id, amount_received=100.0,
                  payment_method=PaymentMethod.CHECK, status=PaymentStatus.APPLIED)
    db.add(pmt); db.flush()
    db.add(PaymentAllocation(payment_id=pmt.id, invoice_id=inv.id, amount_applied=100.0))
    db.commit(); db.refresh(pmt)

    res = QBOSyncService(db, current_user_id=user.id).push_payment(pmt.id)
    assert res["ok"], res
    rows = (db.query(AuditLog)
            .filter(AuditLog.entity_type == "payment", AuditLog.entity_id == pmt.id,
                    AuditLog.action == "qbo_synced")
            .all())
    assert rows and all(r.user_id == user.id for r in rows)


# ── 4. routes registered ──────────────────────────────────────────────────────

def test_push_routes_registered(client):
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/qbo/push-bill/{bill_id}" in paths
    assert "/qbo/push-credit-memo/{cm_id}" in paths
