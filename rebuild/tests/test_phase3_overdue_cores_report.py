"""
tests/test_phase3_overdue_cores_report.py
=========================================
§23.3 Phase 3 — standalone Overdue Cores report (the chase list).

ReportService.get_overdue_cores() derives from get_core_charges_outstanding()
(single source of truth) and keeps ONLY the past-deadline rows, adding
days_overdue + the customer's phone. Routes: GET /reports/overdue-cores (+CSV).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


_SEQ = {"n": 0}


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db, phone="406-555-0142"):
    from app.models.customer import Customer
    c = Customer(company_name=f"P3 OC Cust {_uniq()}", phone=phone)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"P3OC-{_uniq():04d}", title="Core Part", description="Core Part",
                cost=30.0, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _core(db, *, customer, product, deadline_days: int, qty=1, cust_charge=50.0):
    """Open customer core with a return deadline offset from now (negative =
    already overdue)."""
    from app.constants import CoreDirection, CoreStatus
    from app.models.core import CoreCharge
    core = CoreCharge(
        product_id=product.id, customer_id=customer.id,
        qty_charged=qty, qty_returned=0,
        customer_unit_charge=cust_charge, vendor_unit_charge=30.0,
        direction=CoreDirection.CUSTOMER_OWES_RETURN, status=CoreStatus.OPEN,
        return_deadline=datetime.utcnow() + timedelta(days=deadline_days),
    )
    db.add(core); db.commit(); db.refresh(core)
    return core


# ===========================================================================
# Service
# ===========================================================================

def test_only_overdue_cores_included(db):
    from app.services.report_service import ReportService
    cust = _customer(db); prod = _product(db)
    overdue = _core(db, customer=cust, product=prod, deadline_days=-10)
    _core(db, customer=cust, product=prod, deadline_days=+10)  # inside window

    data = ReportService(db).get_overdue_cores()
    ids = {r["core"].id for r in data["rows"]}
    assert overdue.id in ids
    # the not-yet-due core must NOT be on the chase list
    all_deadlines_past = all(r["days_overdue"] >= 1 for r in data["rows"])
    assert all_deadlines_past


def test_days_overdue_amount_and_phone(db):
    from app.services.report_service import ReportService
    cust = _customer(db, phone="406-555-0199")
    prod = _product(db)
    core = _core(db, customer=cust, product=prod, deadline_days=-30, qty=2,
                 cust_charge=75.0)

    data = ReportService(db).get_overdue_cores()
    row = next(r for r in data["rows"] if r["core"].id == core.id)
    assert row["days_overdue"] in (29, 30, 31)  # date-level arithmetic
    assert row["amount"] == 150.0               # 2 × $75 outstanding
    assert row["customer_phone"] == "406-555-0199"


def test_sorted_most_overdue_first_and_totals(db):
    from app.services.report_service import ReportService
    cust = _customer(db); prod = _product(db)
    _core(db, customer=cust, product=prod, deadline_days=-5)
    _core(db, customer=cust, product=prod, deadline_days=-60)

    data = ReportService(db).get_overdue_cores()
    days = [r["days_overdue"] for r in data["rows"]]
    assert days == sorted(days, reverse=True)

    totals = data["totals"]
    assert totals["core_count"] == len(data["rows"])
    assert totals["amount"] == pytest.approx(
        round(sum(r["amount"] for r in data["rows"]), 2))
    assert totals["oldest_days_overdue"] == max(days)


def test_partially_returned_core_counts_outstanding_only(db):
    from app.constants import CoreStatus
    from app.services.report_service import ReportService
    cust = _customer(db); prod = _product(db)
    core = _core(db, customer=cust, product=prod, deadline_days=-15, qty=3,
                 cust_charge=40.0)
    core.qty_returned = 2
    core.status = CoreStatus.PARTIAL
    db.commit()

    data = ReportService(db).get_overdue_cores()
    row = next(r for r in data["rows"] if r["core"].id == core.id)
    assert row["qty_outstanding"] == 1
    assert row["amount"] == 40.0


# ===========================================================================
# Routes
# ===========================================================================

def test_report_page_renders(client, db):
    cust = _customer(db); prod = _product(db)
    _core(db, customer=cust, product=prod, deadline_days=-7)

    r = client.get("/reports/overdue-cores")
    assert r.status_code == 200
    assert "Overdue Customer Cores" in r.text
    assert cust.company_name in r.text


def test_csv_export(client, db):
    cust = _customer(db, phone="406-555-0777"); prod = _product(db)
    _core(db, customer=cust, product=prod, deadline_days=-7)

    r = client.get("/reports/overdue-cores/export.csv")
    assert r.status_code == 200
    assert "days_overdue" in r.text.splitlines()[0]
    assert "406-555-0777" in r.text
