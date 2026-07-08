"""
tests/test_qbo_push_batch.py
============================
Phase-1B — POST /qbo/invoices/push-batch PER-INVOICE INDEPENDENCE.

The batch route loops the existing QBOSyncService.push_invoice with NO shared
transaction, so one invoice's failure must never abort or roll back the others.
That independence rests on two contracts, both pinned here:

  1. push_invoice is FAIL-SOFT — it returns {ok: False, error} and NEVER raises
     (a raise would 500 the whole batch). Verified for not-connected QBO, a
     missing invoice, and an unfinalized (DRAFT) invoice.
  2. The batch processes each id independently — a failure in the MIDDLE still
     lets the invoices before AND after it through, with accurate per-invoice
     results and counts. Plus a real (no-QBO) batch returns 200 not 500 and
     never touches the money path or falsely marks an invoice synced.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_qbo_push_batch.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, LineType, QBOSyncStatus
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.services.qbo_service import QBOSyncService
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


def _customer(db) -> Customer:
    c = Customer(company_name=f"QBO Co {next(_seq)}", contact_name="QA")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _invoice(db, customer_id, amount, *, status=InvoiceStatus.OPEN) -> Invoice:
    inv = Invoice(invoice_number=f"INV-QBO-{next(_seq):05d}", customer_id=customer_id,
                  status=status, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


# ── 1. push_invoice fail-soft (the contract that enables batch independence) ──

def test_push_invoice_is_failsoft_and_never_raises(db):
    cust = _customer(db)
    svc = QBOSyncService(db)

    # No QBO connection in tests → returns a dict, does NOT raise.
    open_inv = _invoice(db, cust.id, 100.0, status=InvoiceStatus.OPEN)
    r = svc.push_invoice(open_inv.id)
    assert isinstance(r, dict) and r["ok"] is False and r.get("error")

    # Missing invoice → fail-soft.
    assert svc.push_invoice(999_999)["ok"] is False

    # Unfinalized (DRAFT) invoice → fail-soft, told to finalize first.
    draft = _invoice(db, cust.id, 50.0, status=InvoiceStatus.DRAFT)
    rd = svc.push_invoice(draft.id)
    assert rd["ok"] is False and "finalize" in rd["error"].lower()


# ── 2. The batch processes each id independently (failure in the middle) ─────

def test_push_batch_failure_in_middle_does_not_abort_the_rest(client, db, monkeypatch):
    cust = _customer(db)
    a = _invoice(db, cust.id, 100.0)
    b = _invoice(db, cust.id, 200.0)   # this one will "fail"
    c = _invoice(db, cust.id, 300.0)

    def fake_push(self, iid):
        if iid == b.id:
            return {"ok": False, "error": "simulated QBO error"}
        return {"ok": True, "qbo_id": f"QBO-{iid}"}

    monkeypatch.setattr(QBOSyncService, "push_invoice", fake_push)

    r = client.post("/qbo/invoices/push-batch",
                    json={"invoice_ids": [a.id, b.id, c.id]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3 and body["pushed"] == 2 and body["failed"] == 1

    by_id = {e["id"]: e for e in body["results"]}
    assert by_id[a.id]["ok"] is True and by_id[a.id]["qbo_id"] == f"QBO-{a.id}"
    # the invoice AFTER the failure was still processed — no abort, no rollback
    assert by_id[c.id]["ok"] is True and by_id[c.id]["qbo_id"] == f"QBO-{c.id}"
    assert by_id[b.id]["ok"] is False and by_id[b.id]["error"] == "simulated QBO error"


# ── 3. A real (no-QBO) batch fails soft: 200 not 500, money untouched ────────

def test_push_batch_real_failsoft_no_abort_no_money_touch(client, db):
    cust = _customer(db)
    a = _invoice(db, cust.id, 150.0)
    b = _invoice(db, cust.id, 250.0)
    a_total_before, a_status_before = a.total, a.status

    r = client.post("/qbo/invoices/push-batch", json={"invoice_ids": [a.id, b.id]})
    assert r.status_code == 200, r.text[:300]      # failures don't crash the batch
    body = r.json()
    assert body["count"] == 2 and body["failed"] == 2 and body["pushed"] == 0

    # A failed push must not touch the money path nor falsely mark the invoice synced.
    db.expire_all()
    a2 = db.query(Invoice).filter(Invoice.id == a.id).first()
    assert a2.total == a_total_before
    assert a2.status == a_status_before
    assert a2.qbo_sync_status != QBOSyncStatus.SYNCED
    assert a2.qbo_invoice_id in (None, "")
