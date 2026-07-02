"""
tests/test_qbo_visibility.py
============================
QBO visibility + resilience lane:

  1. Surcharge-receipt idempotency via Payment.qbo_surcharge_receipt_id: the
     first push PERSISTS the SalesReceipt id; a second push returns it from the
     column with ZERO Intuit traffic (no DocNumber query, no create) — closing
     the query-then-create double-book window. Legacy receipts (pushed before
     the column existed) are still recovered by DocNumber and then persisted.
  2. The background retry worker covers payments, vendor bills, and credit
     memos — not just invoices. Driven through app.main._qbo_retry_tick (the
     extracted tick function, no thread).
  3. connection_summary() reports per-entity pending/failed counts (gated to
     pushable statuses — a DRAFT invoice never counts as "pending sync"), keeps
     the legacy flat invoice keys, and exposes needs-manual-reversal payments
     (synced to QBO, later REVERSED/NSF here) as count + ids.
  4. payments/detail.html renders the red "Needs manual fix in QuickBooks"
     callout for a synced-then-reversed payment, incl. the JAKS-SC-{id}
     surcharge DocNumber when a surcharge was collected.
  5. app.main._run_inventory_resync is defensive: logs drift as a warning,
     survives (and logs ONCE) a not-yet-shipped resync_all_products.

All Intuit HTTP is faked (no live calls) — same shim pattern as
tests/test_qbo_safety_fixes.py, extended with Bill / CreditMemo / Vendor
surfaces and a query log so "no API traffic" can be asserted.
"""
from __future__ import annotations

import itertools
import logging
import pathlib
import re
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

import app.database as _appdb
import app.main as app_main
import app.services.qbo_client as qc
from app.constants import (
    CreditMemoStatus, InvoiceStatus, LineType, PaymentMethod, PaymentStatus,
    QBOSyncStatus, VendorBillStatus,
)
from app.main import app
from app.models.credit_memo import CreditMemo, CreditMemoLine
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.models.purchase_order import VendorBill, VendorBillLine
from app.models.vendor import Vendor
from app.services.qbo_service import DEFAULT_ITEM_MAP, SURCHARGE_ITEM, QBOSyncService
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)
_ITEM_IDS = {name: f"ITEM-{i}"
             for i, name in enumerate(sorted(set(DEFAULT_ITEM_MAP.values())), 1)}
_INCOME_ACCT = {"Id": "77", "Name": "Sales of Product Income", "AccountType": "Income"}
_COGS_ACCT = {"Id": "55", "Name": "Cost of Goods Sold", "AccountType": "Cost of Goods Sold"}


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
    """tests/test_qbo_safety_fixes.py's fake, extended with: a query LOG (so
    zero-traffic idempotency is assertable), Bill + CreditMemo creates, and a
    Vendor query/create surface for the vendor-bill push."""

    def __init__(self):
        self.items = dict(_ITEM_IDS)
        self.item_accounts = {n: _INCOME_ACCT["Id"] for n in self.items}
        self.customers: dict[str, str] = {}
        self.vendors: dict[str, str] = {}
        self.accounts = [dict(_INCOME_ACCT), dict(_COGS_ACCT)]
        self.receipts: dict[str, str] = {}      # DocNumber → id
        self.created: dict[str, dict] = {}      # entity → last payload posted
        self.receipt_creates: list[dict] = []
        self.queries: list[str] = []
        self._n = itertools.count(1)

    def salesreceipt_query_count(self) -> int:
        return sum(1 for q in self.queries if "from salesreceipt" in q.lower())

    # API surface
    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        u = url.lower()
        if u.endswith("/query"):
            return self._query((params or {}).get("query", ""))
        if u.endswith("/item"):
            new_id = f"NEW-ITEM-{next(self._n)}"
            self.items[json["Name"]] = new_id
            self.item_accounts[json["Name"]] = json["IncomeAccountRef"]["value"]
            return _Resp(200, {"Item": {"Id": new_id}})
        if u.endswith("/salesreceipt"):
            self.receipt_creates.append(json)
            new_id = f"QBO-SR-{next(self._n)}"
            self.receipts[json.get("DocNumber", "")] = new_id
            return _Resp(200, {"SalesReceipt": {"Id": new_id}})
        if u.endswith("/invoice"):
            self.created["Invoice"] = json
            return _Resp(200, {"Invoice": {"Id": f"QBO-INV-{next(self._n)}"}})
        if u.endswith("/payment"):
            self.created["Payment"] = json
            return _Resp(200, {"Payment": {"Id": f"QBO-PAY-{next(self._n)}"}})
        if u.endswith("/bill"):
            self.created["Bill"] = json
            return _Resp(200, {"Bill": {"Id": f"QBO-BILL-{next(self._n)}"}})
        if u.endswith("/creditmemo"):
            self.created["CreditMemo"] = json
            return _Resp(200, {"CreditMemo": {"Id": f"QBO-CM-{next(self._n)}"}})
        if u.endswith("/customer"):
            new_id = f"QBO-CUST-{next(self._n)}"
            self.customers[json.get("DisplayName", "")] = new_id
            return _Resp(200, {"Customer": {"Id": new_id}})
        if u.endswith("/vendor"):
            new_id = f"QBO-VEND-{next(self._n)}"
            self.vendors[json.get("DisplayName", "")] = new_id
            return _Resp(200, {"Vendor": {"Id": new_id}})
        return _Resp(200, {})

    def _query(self, q: str) -> _Resp:
        self.queries.append(q)
        ql = q.lower()
        if "from item" in ql:
            name = _where(q, "Name")
            rows = [{"Id": i, "Name": n,
                     "IncomeAccountRef": {"value": self.item_accounts.get(n, "")}}
                    for n, i in self.items.items() if name is None or n == name]
            return _Resp(200, {"QueryResponse": {"Item": rows}})
        if "from customer" in ql:
            name = _where(q, "DisplayName")
            rows = [{"Id": i, "DisplayName": n} for n, i in self.customers.items()
                    if name is None or n == name]
            return _Resp(200, {"QueryResponse": {"Customer": rows}})
        if "from vendor" in ql:
            name = _where(q, "DisplayName")
            rows = [{"Id": i, "DisplayName": n} for n, i in self.vendors.items()
                    if name is None or n == name]
            return _Resp(200, {"QueryResponse": {"Vendor": rows}})
        if "from account" in ql:
            name = _where(q, "Name")
            rows = [a for a in self.accounts
                    if (name is None or a["Name"] == name)
                    and ("accounttype = 'income'" not in ql
                         or a["AccountType"] == "Income")]
            return _Resp(200, {"QueryResponse": {"Account": rows}})
        if "from salesreceipt" in ql:
            doc = _where(q, "DocNumber")
            rows = [{"Id": i, "DocNumber": d} for d, i in self.receipts.items()
                    if doc is None or d == doc]
            return _Resp(200, {"QueryResponse": {"SalesReceipt": rows}})
        return _Resp(200, {"QueryResponse": {}})

    # token surface
    def post(self, url, headers=None, data=None, json=None, timeout=None):
        if "revoke" in url:
            return _Resp(200, {})
        return _Resp(200, {
            "access_token": "AT-NEW", "refresh_token": "RT-NEW",
            "expires_in": 3600, "x_refresh_token_expires_in": 8640000,
        })


def _install(monkeypatch, fake: FakeQBO) -> FakeQBO:
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


def _customer(db, *, qbo_id="QBO-CUST-9") -> Customer:
    c = Customer(company_name=f"QBO Vis Co {next(_seq)}", contact_name="QA",
                 email=f"qbo-vis-{next(_seq)}@test.local", qbo_customer_id=qbo_id)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _synced_invoice(db, customer_id) -> Invoice:
    inv = Invoice(
        invoice_number=f"INV-QVIS-{next(_seq):05d}", customer_id=customer_id,
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


def _vendor(db) -> Vendor:
    v = Vendor(name=f"QBO Vis Vendor {next(_seq)}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _bill(db, vendor_id, *, status=VendorBillStatus.APPROVED,
          sync_status=QBOSyncStatus.ERROR, retry=1) -> VendorBill:
    b = VendorBill(vendor_id=vendor_id, bill_number=f"B-VIS-{next(_seq)}",
                   status=status, total_amount=20.0,
                   qbo_sync_status=sync_status, qbo_sync_retry_count=retry)
    db.add(b); db.flush()
    db.add(VendorBillLine(bill_id=b.id, qty_billed=2, unit_cost=10.0))
    db.commit(); db.refresh(b)
    return b


def _cm(db, customer_id, *, status=CreditMemoStatus.OPEN,
        sync_status=QBOSyncStatus.ERROR, retry=1) -> CreditMemo:
    cm = CreditMemo(cm_number=f"CM-VIS-{next(_seq):05d}", customer_id=customer_id,
                    status=status, total_amount=25.0,
                    qbo_sync_status=sync_status, qbo_sync_retry_count=retry)
    db.add(cm); db.flush()
    db.add(CreditMemoLine(credit_memo_id=cm.id, description="credit line", qty=1,
                          unit_price=25.0, is_taxable=False))
    db.commit(); db.refresh(cm)
    return cm


def _fresh(db, model, doc_id):
    db.expire_all()
    return db.query(model).filter(model.id == doc_id).first()


# ── 1. surcharge-receipt idempotency via the persisted column ─────────────────

def test_surcharge_receipt_persisted_and_second_push_skips_api(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0, surcharge=3.5)

    first = QBOSyncService(db).push_payment(pmt.id)
    assert first["ok"], first
    assert first.get("surcharge_qbo_id")
    assert _fresh(db, Payment, pmt.id).qbo_surcharge_receipt_id == first["surcharge_qbo_id"]

    sr_queries_before = fake.salesreceipt_query_count()
    creates_before = len(fake.receipt_creates)
    second = QBOSyncService(db).push_payment(pmt.id)
    assert second["ok"] and second.get("skipped") == "already synced"
    assert second.get("surcharge_qbo_id") == first["surcharge_qbo_id"]
    # The stored column short-circuits BEFORE any Intuit traffic: no new
    # DocNumber query and no new SalesReceipt create (the double-book window).
    assert fake.salesreceipt_query_count() == sr_queries_before, (
        "second push re-queried QBO for the surcharge receipt — the persisted "
        "qbo_surcharge_receipt_id is not being checked first")
    assert len(fake.receipt_creates) == creates_before


def test_legacy_receipt_recovered_by_docnumber_then_persisted(db, monkeypatch):
    """Receipt pushed BEFORE the column existed: no stored id, but the
    deterministic DocNumber query finds it — and the recovery now persists the
    id so the next push skips the API entirely."""
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=60.0, surcharge=2.0)
    pmt.qbo_payment_id = "QBO-PAY-LEGACY"
    pmt.qbo_sync_status = QBOSyncStatus.SYNCED
    db.commit()
    fake.receipts[f"JAKS-SC-{pmt.id}"] = "QBO-SR-LEGACY"   # pre-column receipt

    res = QBOSyncService(db).push_payment(pmt.id)
    assert res["ok"] and res.get("skipped") == "already synced"
    assert res.get("surcharge_qbo_id") == "QBO-SR-LEGACY"
    assert fake.receipt_creates == []                       # recovered, not re-created
    assert _fresh(db, Payment, pmt.id).qbo_surcharge_receipt_id == "QBO-SR-LEGACY"


# ── 2. retry tick covers payments / vendor bills / credit memos ───────────────

def test_retry_tick_processes_failed_payment_bill_and_credit_memo(db, monkeypatch):
    fake = _install(monkeypatch, FakeQBO())
    _connect(db)
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0)
    pmt.qbo_sync_status = QBOSyncStatus.ERROR
    pmt.qbo_sync_retry_count = 1
    vend = _vendor(db)
    bill = _bill(db, vend.id)                                # APPROVED + ERROR
    cm = _cm(db, cust.id)                                    # OPEN + ERROR
    db.commit()

    summary = app_main._qbo_retry_tick()                     # tick, not thread
    assert summary is not None, "tick refused to run while connected"
    ent = summary["entities"]
    assert ent["payments"]["succeeded"] >= 1, summary
    assert ent["vendor_bills"]["succeeded"] >= 1, summary
    assert ent["credit_memos"]["succeeded"] >= 1, summary
    assert summary["succeeded"] >= 3

    fresh_pmt = _fresh(db, Payment, pmt.id)
    assert fresh_pmt.qbo_sync_status == QBOSyncStatus.SYNCED and fresh_pmt.qbo_payment_id
    fresh_bill = _fresh(db, VendorBill, bill.id)
    assert fresh_bill.qbo_sync_status == QBOSyncStatus.SYNCED and fresh_bill.qbo_id
    fresh_cm = _fresh(db, CreditMemo, cm.id)
    assert fresh_cm.qbo_sync_status == QBOSyncStatus.SYNCED and fresh_cm.qbo_id
    assert fake.created.get("Bill") and fake.created.get("CreditMemo")


def test_failed_id_queues_respect_gates_and_ceiling(db):
    svc = QBOSyncService(db, current_user_id=None)
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)

    reversed_err = _payment(db, cust.id, inv.id, amount=10.0)
    reversed_err.status = PaymentStatus.REVERSED
    reversed_err.qbo_sync_status = QBOSyncStatus.ERROR
    over_ceiling = _payment(db, cust.id, inv.id, amount=10.0)
    over_ceiling.qbo_sync_status = QBOSyncStatus.ERROR
    over_ceiling.qbo_sync_retry_count = 9
    eligible = _payment(db, cust.id, inv.id, amount=10.0)
    eligible.qbo_sync_status = QBOSyncStatus.ERROR
    eligible.qbo_sync_retry_count = 2
    db.commit()

    ids = set(svc.failed_payment_ids(max_retries=5))
    assert eligible.id in ids
    assert reversed_err.id not in ids and over_ceiling.id not in ids

    vend = _vendor(db)
    pending_bill = _bill(db, vend.id, status=VendorBillStatus.PENDING)  # not pushable
    ok_bill = _bill(db, vend.id)
    bill_ids = set(svc.failed_vendor_bill_ids())
    assert ok_bill.id in bill_ids and pending_bill.id not in bill_ids

    reversed_cm = _cm(db, cust.id, status=CreditMemoStatus.REVERSED)
    ok_cm = _cm(db, cust.id)
    cm_ids = set(svc.failed_credit_memo_ids())
    assert ok_cm.id in cm_ids and reversed_cm.id not in cm_ids


# ── 3. connection_summary: per-entity counts + needs-manual-reversal ──────────

def test_connection_summary_reports_per_entity_counts(db):
    before = QBOSyncService(db).connection_summary()["entities"]

    cust = _customer(db)
    db.add(Invoice(invoice_number=f"INV-QVIS-{next(_seq):05d}", customer_id=cust.id,
                   status=InvoiceStatus.OPEN))                       # pending sync
    db.add(Invoice(invoice_number=f"INV-QVIS-{next(_seq):05d}", customer_id=cust.id,
                   status=InvoiceStatus.DRAFT))                      # NOT pushable — excluded
    db.commit()
    inv = _synced_invoice(db, cust.id)                               # synced +1
    pmt = _payment(db, cust.id, inv.id)
    pmt.qbo_sync_status = QBOSyncStatus.ERROR                        # payments failed +1
    vend = _vendor(db)
    _bill(db, vend.id)                                               # vendor_bills failed +1
    _cm(db, cust.id, sync_status=QBOSyncStatus.PENDING, retry=0)     # credit_memos pending +1
    db.commit()

    after_full = QBOSyncService(db).connection_summary()
    after = after_full["entities"]
    assert after["invoices"]["pending"] == before["invoices"]["pending"] + 1, (
        "DRAFT invoice leaked into the pending-sync count")
    assert after["invoices"]["synced"] == before["invoices"]["synced"] + 1
    assert after["payments"]["failed"] == before["payments"]["failed"] + 1
    assert after["vendor_bills"]["failed"] == before["vendor_bills"]["failed"] + 1
    assert after["credit_memos"]["pending"] == before["credit_memos"]["pending"] + 1
    # Legacy flat keys stay invoice-scoped; totals span all four entities.
    assert after_full["pending"] == after["invoices"]["pending"]
    assert after_full["error"] == after["invoices"]["failed"]
    assert after_full["pending_total"] == sum(e["pending"] for e in after.values())
    assert after_full["failed_total"] == sum(e["failed"] for e in after.values())


def test_needs_manual_reversal_count_and_ids(db):
    before = QBOSyncService(db).connection_summary()["needs_reversal"]
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)

    synced_reversed = _payment(db, cust.id, inv.id)
    synced_reversed.qbo_payment_id = "QBO-PAY-REV-1"
    synced_reversed.status = PaymentStatus.REVERSED
    synced_nsf = _payment(db, cust.id, inv.id, amount=40.0)
    synced_nsf.qbo_payment_id = "QBO-PAY-REV-2"
    synced_nsf.status = PaymentStatus.NSF
    unsynced_reversed = _payment(db, cust.id, inv.id, amount=10.0)
    unsynced_reversed.status = PaymentStatus.REVERSED       # never reached QBO
    db.commit()

    summary = QBOSyncService(db).connection_summary()
    assert summary["needs_reversal"] == before + 2
    ids = set(summary["needs_reversal_ids"])
    assert synced_reversed.id in ids and synced_nsf.id in ids
    assert unsynced_reversed.id not in ids, (
        "a never-synced reversal needs no QBO fix — it must not be flagged")


# ── 4. payments/detail.html needs-manual-reversal callout ─────────────────────

def test_reversed_synced_payment_detail_shows_manual_fix_callout(db, client):
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0, surcharge=3.5)
    pmt.qbo_payment_id = "QBO-PAY-BADGE-1"
    pmt.qbo_sync_status = QBOSyncStatus.SYNCED
    pmt.status = PaymentStatus.REVERSED
    db.commit()

    r = client.get(f"/payments/{pmt.id}")
    assert r.status_code == 200
    assert "Needs manual fix in QuickBooks" in r.text
    assert "QBO-PAY-BADGE-1" in r.text
    assert f"JAKS-SC-{pmt.id}" in r.text     # surcharge receipt DocNumber called out


def test_applied_synced_payment_has_no_reversal_callout(db, client):
    cust = _customer(db)
    inv = _synced_invoice(db, cust.id)
    pmt = _payment(db, cust.id, inv.id, amount=100.0)
    pmt.qbo_payment_id = "QBO-PAY-OK-1"
    pmt.qbo_sync_status = QBOSyncStatus.SYNCED
    db.commit()

    r = client.get(f"/payments/{pmt.id}")
    assert r.status_code == 200
    assert "Needs manual fix in QuickBooks" not in r.text


# ── 5. inventory resync tick (defensive) ──────────────────────────────────────

def test_inventory_resync_tick_logs_drift_warning(db, monkeypatch, caplog):
    from app.services.inventory_service import InventoryService
    monkeypatch.setattr(InventoryService, "resync_all_products",
                        lambda self: {"checked": 5, "drifted": 2, "fixed": 2},
                        raising=False)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        out = app_main._run_inventory_resync(db)
    assert out == {"checked": 5, "drifted": 2, "fixed": 2}
    assert any("drift" in r.getMessage().lower() for r in caplog.records)


def test_inventory_resync_tick_survives_missing_method_and_logs_once(db, monkeypatch, caplog):
    from app.services.inventory_service import InventoryService
    monkeypatch.setattr(InventoryService, "resync_all_products", None, raising=False)
    monkeypatch.setattr(app_main, "_inventory_resync_missing_logged", False)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        assert app_main._run_inventory_resync(db) is None   # must not raise
    assert any("resync_all_products" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.main"):
        assert app_main._run_inventory_resync(db) is None
    assert not any("resync_all_products" in r.getMessage() for r in caplog.records), (
        "the missing-method warning must fire once, not every daily tick")
