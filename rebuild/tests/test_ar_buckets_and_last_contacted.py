"""
tests/test_ar_buckets_and_last_contacted.py
===========================================
Round-1 LOCKING tests for two contracts Backend is landing — authored against the
spec NOW so they flip from xfail → hard guard the moment the contracts land.

  #3 AR aging buckets — CRMService.get_account_balance(cid) must gain a 'buckets'
     dict that (a) sums to total_open and (b) MATCHES ReportService.get_ar_aging()
     bucket-for-bucket for the SAME customer + dated invoices. This is the guard
     that keeps the customer account-health WIDGET and the AR aging REPORT from
     drifting apart (one is the dashboard tile, the other the finance report —
     they must bucket identically).

  #5 last_contacted — the customer LIST route must expose a `last_contacted` map
     == max(CustomerCallLog.logged_at) per customer (None when no calls).

Both are xfail(strict=False) TODAY: get_account_balance() returns no 'buckets'
key and customer_list() puts no 'last_contacted' in its context yet. When Backend
lands them, these xpass — DROP the xfail marker to lock them as hard guards.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_ar_buckets_and_last_contacted.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from starlette.requests import Request

import app.database as _appdb
from app.constants import CallOutcome, CallType, InvoiceStatus, LineType
from app.main import app
from app.models.customer import Customer, CustomerCallLog
from app.models.invoice import Invoice, InvoiceLine
from app.routers.customers import customer_list
from app.services.crm_service import CRMService
from app.services.report_service import ReportService, _AGING_BUCKETS
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
    c = Customer(company_name=f"AR Co {next(_seq)}", contact_name="QA")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id, amount, *, due_days_ago) -> Invoice:
    """An OPEN (unpaid) invoice whose due_date is `due_days_ago` days before today
    (negative = future). balance_due == amount."""
    inv = Invoice(invoice_number=f"INV-AR-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True,
                  due_date=datetime.utcnow() - timedelta(days=due_days_ago))
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _list_context(db) -> dict:
    req = Request({"type": "http", "method": "GET", "path": "/customers/", "headers": [],
                   "query_string": b"", "scheme": "http", "server": ("test", 80)})
    return customer_list(req, db=db).context


# ── #3 — AR aging buckets: widget must match the report, sum to total ─────────

@pytest.mark.xfail(
    strict=False,
    reason="Contract #3 not landed: CRMService.get_account_balance() returns no "
           "'buckets' key yet. When it does, this locks it to (a) sum to total_open "
           "and (b) match ReportService.get_ar_aging() bucket-for-bucket — drop the "
           "marker to make it a hard anti-drift guard.",
)
def test_account_balance_buckets_match_ar_aging_and_sum_to_total(db):
    cust = _customer(db)
    # one unpaid invoice per aging bucket, distinct balances (sum = 1500)
    _open_invoice(db, cust.id, 100.0, due_days_ago=-1)    # due tomorrow → current
    _open_invoice(db, cust.id, 200.0, due_days_ago=15)    # 1_30
    _open_invoice(db, cust.id, 300.0, due_days_ago=45)    # 31_60
    _open_invoice(db, cust.id, 400.0, due_days_ago=75)    # 61_90
    _open_invoice(db, cust.id, 500.0, due_days_ago=120)   # over_90

    bal = CRMService(db).get_account_balance(cust.id)
    ar = ReportService(db).get_ar_aging(as_of_date=date.today())
    row = next(r for r in ar["rows"] if r["customer_id"] == cust.id)

    buckets = bal["buckets"]                                   # ← the contract key
    # (a) buckets partition the open balance exactly
    assert round(sum(buckets.values()), 2) == bal["total_open"] == 1500.0
    # (b) widget buckets == AR report buckets, key for key (anti-drift)
    assert buckets == {k: round(row[k], 2) for k in _AGING_BUCKETS}


# ── #5 — last_contacted: list-route map == max(logged_at) per customer ───────

@pytest.mark.xfail(
    strict=False,
    reason="Contract #5 not landed: customer_list() puts no 'last_contacted' in its "
           "context yet. When it does, this locks it to max(CustomerCallLog.logged_at) "
           "per customer (None when no calls) — drop the marker to make it a hard guard.",
)
def test_last_contacted_map_is_max_logged_at_per_customer(db):
    called = _customer(db)
    never_called = _customer(db)
    crm = CRMService(db, 1)
    crm.log_call(customer_id=called.id, call_type=CallType.INBOUND,
                 outcome=CallOutcome.OTHER, notes="first")
    crm.log_call(customer_id=called.id, call_type=CallType.INBOUND,
                 outcome=CallOutcome.OTHER, notes="second")

    expected = (db.query(func.max(CustomerCallLog.logged_at))
                .filter(CustomerCallLog.customer_id == called.id).scalar())
    assert expected is not None   # sanity: the calls were logged

    last_contacted = _list_context(db)["last_contacted"]       # ← the contract key
    assert last_contacted[called.id] == expected               # newest call
    assert last_contacted.get(never_called.id) is None         # no calls → None
