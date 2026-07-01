"""
tests/test_phase1_spine_fixes.py
================================
MASTER_PLAN §23.3 Phase 1 — "Stabilize the spine" contained correctness fixes.

Covers two self-contained hardening fixes landed together:

  #4 — Available credit now NETS the customer's held account credit
       (``credit_balance``) into every part of the credit picture: the
       ``available_credit`` headroom, the ``would_exceed_credit`` over-limit
       test, and the projected-balance message — so all three stay consistent.
       Money we already hold on a customer's behalf offsets their AR exposure,
       so it raises how much they can still charge. (customer_metrics_service /
       customer_service)

  #3 — POST /qbo/invoices/push-batch offloads its BLOCKING push loop off the
       event loop via run_in_threadpool. Each push is a synchronous httpx
       round-trip to Intuit; looping N of them inline on the event loop would
       freeze every other request for the batch's duration. The blocking work
       is a plain (non-coroutine) helper; the route stays async and its JSON
       contract is unchanged. (routers/qbo)

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_phase1_spine_fixes.py -v
"""
from __future__ import annotations

import inspect
import itertools
import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, LineType
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.routers.qbo import _run_push_batch, qbo_push_batch
from app.services.customer_metrics_service import CustomerMetricsService
from app.services.customer_service import CustomerService
from app.services.qbo_service import QBOSyncService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db, **kw) -> Customer:
    c = Customer(company_name=f"Phase1 Co {next(_seq)}", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, cust, amount) -> Invoice:
    """An OPEN, unpaid invoice that contributes ``amount`` to the customer's
    open AR (mirrors the tax-exempt fixture used across the credit tests)."""
    inv = Invoice(invoice_number=f"INV-P1-{next(_seq):05d}", customer_id=cust.id,
                  status=InvoiceStatus.OPEN, is_taxable=False,
                  created_at=datetime(datetime.utcnow().year, 1, 2))
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1,
                                 unit_price=amount, unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit()
    return inv


# ═══════════════════════════════════════════════════════════════════════════
# #4 — available credit nets the customer's held account credit
# ═══════════════════════════════════════════════════════════════════════════

def test_metrics_available_credit_nets_account_credit(db):
    # limit 1000, open AR 300, and $200 of held account credit →
    # available = 1000 - 300 + 200 = 900 (the credit RAISES the headroom).
    c = _customer(db, credit_limit=1000.0, credit_balance=200.0)
    _open_invoice(db, c, 300.0)
    m = CustomerMetricsService(db).metrics_for(c)
    assert m["open_ar"] == 300.0
    assert m["credit_balance"] == 200.0
    assert m["available_credit"] == 900.0


def test_credit_status_available_nets_account_credit(db):
    c = _customer(db, credit_limit=1000.0, credit_balance=200.0)
    _open_invoice(db, c, 300.0)
    status = CustomerService(db).credit_status(c)
    assert status["open_ar"] == 300.0
    assert status["available_credit"] == 900.0
    assert status["warn"] is False


def test_account_credit_relaxes_the_over_limit_decision(db):
    # Without netting: 800 open + 300 new = 1100 > 1000 → OVER limit.
    # With the customer's $200 account credit netted in, effective exposure is
    # 800 - 200 = 600, so 600 + 300 = 900 <= 1000 → NOT over limit.
    c = _customer(db, credit_limit=1000.0, credit_balance=200.0)
    _open_invoice(db, c, 800.0)
    svc = CustomerService(db)
    assert svc.would_exceed_credit(c, 300.0) is False
    status = svc.credit_status(c, prospective_amount=300.0)
    assert status["over_limit"] is False
    assert status["warn"] is False
    assert status["message"] == ""


def test_over_limit_projected_message_uses_netted_balance(db):
    # 900 open, $100 credit → net 800; a 400 charge → 1200 > 1000 → over limit,
    # and the projected figure in the message is the NET balance (1200), not the
    # gross (which would double-count by ignoring the credit).
    c = _customer(db, credit_limit=1000.0, credit_balance=100.0)
    _open_invoice(db, c, 900.0)
    svc = CustomerService(db)
    assert svc.would_exceed_credit(c, 400.0) is True
    status = svc.credit_status(c, prospective_amount=400.0)
    assert status["over_limit"] is True
    assert status["warn"] is True
    assert "1,200" in status["message"]      # 900 - 100 + 400


def test_no_limit_ignores_account_credit(db):
    # A 0 limit means "no limit enforced" — available stays None regardless of
    # any held credit (the netting only applies once a real limit exists).
    c = _customer(db, credit_limit=0.0, credit_balance=500.0)
    status = CustomerService(db).credit_status(c, prospective_amount=999_999.0)
    assert status["available_credit"] is None
    assert status["over_limit"] is False


# ═══════════════════════════════════════════════════════════════════════════
# #3 — QBO batch push offloads the blocking loop off the event loop
# ═══════════════════════════════════════════════════════════════════════════

def _qbo_invoice(db, customer_id, amount) -> Invoice:
    inv = Invoice(invoice_number=f"INV-P1QBO-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def test_blocking_loop_is_a_plain_helper_route_stays_async():
    # The refactor's whole point: the BLOCKING work is a plain function (safe to
    # hand to run_in_threadpool) while the route itself stays async (it awaits
    # the request body). If either flips, the offload guarantee is broken.
    assert inspect.iscoroutinefunction(_run_push_batch) is False
    assert inspect.iscoroutinefunction(qbo_push_batch) is True


def test_push_batch_contract_preserved_after_threadpool_offload(client, db, monkeypatch):
    cust = _customer(db)
    a = _qbo_invoice(db, cust.id, 100.0)
    b = _qbo_invoice(db, cust.id, 200.0)

    def fake_push(self, iid):
        return {"ok": True, "qbo_id": f"QBO-{iid}"}

    monkeypatch.setattr(QBOSyncService, "push_invoice", fake_push)

    r = client.post("/qbo/invoices/push-batch",
                    json={"invoice_ids": [a.id, b.id]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["pushed"] == 2 and body["failed"] == 0
    by_id = {e["id"]: e for e in body["results"]}
    assert by_id[a.id]["qbo_id"] == f"QBO-{a.id}"
    assert by_id[b.id]["qbo_id"] == f"QBO-{b.id}"


def test_push_batch_all_unsynced_mode_resolves_inside_the_helper(client, db, monkeypatch):
    # mode=all_unsynced resolution moved INTO the offloaded helper (it's a DB
    # read, kept on the worker thread with the push loop). Verify the route
    # still honors it: the helper resolves the id list, not the caller.
    cust = _customer(db)
    inv = _qbo_invoice(db, cust.id, 175.0)

    monkeypatch.setattr(QBOSyncService, "unsynced_invoice_ids",
                        lambda self, pending_only=True: [inv.id])
    monkeypatch.setattr(QBOSyncService, "push_invoice",
                        lambda self, iid: {"ok": True, "qbo_id": f"QBO-{iid}"})

    r = client.post("/qbo/invoices/push-batch", json={"mode": "all_unsynced"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["pushed"] == 1
    assert body["results"][0]["id"] == inv.id
