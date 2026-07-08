"""
tests/test_s21_reports2.py
==========================
§21.10 — quote-conversion-rate + vendor-performance reports.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.constants import POStatus, QuoteOutcome, QuoteStatus, VendorBillStatus
from app.models.customer import Customer
from app.models.purchase_order import PurchaseOrder, POLine, VendorBill, VendorBillLine
from app.models.quote import Quote
from app.models.vendor import Vendor
from app.services.report_service import ReportService

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


def _quote(db, outcome) -> Quote:
    c = Customer(company_name=f"Q Co {next(_seq)}")
    db.add(c); db.flush()
    q = Quote(quote_number=f"Q-{next(_seq):05d}", customer_id=c.id,
              status=QuoteStatus.DRAFT, outcome=outcome)
    db.add(q); db.commit(); db.refresh(q)
    return q


def test_quote_conversion(db):
    for _ in range(3):
        _quote(db, QuoteOutcome.WON)
    _quote(db, QuoteOutcome.LOST)
    _quote(db, QuoteOutcome.PENDING)
    today = _dt.date.today()
    data = ReportService(db).get_quote_conversion(
        today - _dt.timedelta(days=1), today + _dt.timedelta(days=1))
    assert data["won"] >= 3 and data["lost"] >= 1
    # 3 won / (3 won + 1 lost) = 75%
    assert data["conversion_rate"] == pytest.approx(75.0)


def test_vendor_performance_fill_and_discrepancy(db):
    v = Vendor(name=f"VP {next(_seq)}")
    db.add(v); db.commit(); db.refresh(v)
    po = PurchaseOrder(po_number=f"PO-VP-{next(_seq)}", vendor_id=v.id, status=POStatus.PARTIAL)
    db.add(po); db.flush()
    db.add(POLine(po_id=po.id, qty_ordered=10, qty_received=8, unit_cost=5.0))
    # a discrepancy bill (qty_billed > received)
    bill = VendorBill(vendor_id=v.id, bill_number=f"B-{next(_seq)}",
                      status=VendorBillStatus.PENDING, total_amount=60.0)
    db.add(bill); db.flush()
    db.commit()

    today = _dt.date.today()
    rows = ReportService(db).get_vendor_performance(
        today - _dt.timedelta(days=1), today + _dt.timedelta(days=1))["rows"]
    row = next(r for r in rows if r["vendor_id"] == v.id)
    assert row["qty_ordered"] == 10 and row["qty_received"] == 8
    assert row["fill_rate"] == pytest.approx(80.0)
    assert row["bills"] == 1


def test_report_routes(client):
    assert client.get("/reports/quote-conversion").status_code == 200
    assert client.get("/reports/vendor-performance").status_code == 200
    c = client.get("/reports/vendor-performance/export.csv")
    assert c.status_code == 200 and "text/csv" in c.headers.get("content-type", "")
