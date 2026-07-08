"""
tests/test_s21_ap.py
====================
§21 payables — standalone vendor-bill list + AP aging (mirror of AR aging, net of
applied vendor credits).
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
from app.constants import VendorBillStatus
from app.models.purchase_order import VendorBill
from app.models.vendor import Vendor
from app.models.vendor_credit import VendorCreditMemo, VendorCreditMemoAllocation
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


def _vendor(db) -> Vendor:
    v = Vendor(name=f"AP Vend {next(_seq)}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _bill(db, vendor_id, *, total, status=VendorBillStatus.APPROVED, days_old=0) -> VendorBill:
    due = _dt.datetime.utcnow() - _dt.timedelta(days=days_old)
    b = VendorBill(vendor_id=vendor_id, bill_number=f"BILL-{next(_seq)}",
                   bill_date=due, due_date=due, status=status, total_amount=total)
    db.add(b); db.commit(); db.refresh(b)
    return b


def test_ap_aging_buckets_unpaid_bills(db):
    v = _vendor(db)
    _bill(db, v.id, total=100.0, days_old=0)     # current
    _bill(db, v.id, total=200.0, days_old=45)    # 31-60
    _bill(db, v.id, total=50.0, status=VendorBillStatus.PAID, days_old=10)  # excluded

    data = ReportService(db).get_ap_aging()
    row = next(r for r in data["rows"] if r["vendor_id"] == v.id)
    assert row["current"] == pytest.approx(100.0)
    assert row["31_60"] == pytest.approx(200.0)
    assert row["total"] == pytest.approx(300.0)   # paid bill excluded


def test_ap_aging_nets_vendor_credits(db):
    v = _vendor(db)
    b = _bill(db, v.id, total=500.0, days_old=5)
    cm = VendorCreditMemo(vcm_number=f"VCM-{next(_seq)}", vendor_id=v.id,
                          original_vendor_bill_id=b.id, total_amount=120.0)
    db.add(cm); db.commit(); db.refresh(cm)
    db.add(VendorCreditMemoAllocation(vendor_credit_memo_id=cm.id, vendor_bill_id=b.id,
                                      amount_applied=120.0))
    db.commit()

    data = ReportService(db).get_ap_aging()
    row = next(r for r in data["rows"] if r["vendor_id"] == v.id)
    assert row["total"] == pytest.approx(380.0)   # 500 − 120 credit


def test_ap_aging_route_and_csv(client, db):
    v = _vendor(db)
    _bill(db, v.id, total=75.0, days_old=2)
    r = client.get("/reports/ap-aging")
    assert r.status_code == 200 and "AP Aging" in r.text
    c = client.get("/reports/ap-aging/export.csv")
    assert c.status_code == 200 and "text/csv" in c.headers.get("content-type", "")


def test_vendor_bill_list_route(client, db):
    v = _vendor(db)
    b = _bill(db, v.id, total=300.0, days_old=1)
    r = client.get("/purchase-orders/bills?tab=open")
    assert r.status_code == 200
    assert b.bill_number in r.text
    # paid tab should not show an open bill
    r2 = client.get("/purchase-orders/bills?tab=paid")
    assert b.bill_number not in r2.text
