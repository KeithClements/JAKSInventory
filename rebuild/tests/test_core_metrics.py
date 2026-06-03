"""
tests/test_core_metrics.py
==========================
Phase 2 §5.4 — CoreMetricsService.dashboard_metrics().

dashboard_metrics aggregates ALL cores in the DB, so the empty-state test runs
first (before any core exists) and the full scenario is built in a single test
to keep the absolute values deterministic.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    CoreDirection, CoreStatus, CoreVendorStatus, CoreInspectionOutcome, VCRStatus,
)
from app.models.customer import Customer
from app.models.vendor import Vendor
from app.models.product import Product
from app.models.core import CoreCharge, CoreReturnEvent, VendorCoreReturn
from app.services.core_metrics_service import CoreMetricsService, CORE_DASHBOARD_KEYS
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)
_PAST = datetime(2020, 1, 1)
_FUTURE = datetime(2099, 1, 1)


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


def _cust(db):
    c = Customer(company_name=f"Core Co {next(_seq)}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _prod(db):
    p = Product(sku=f"CP{next(_seq)}", title="core part", cost=5.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _core(db, cust, prod, **kw):
    c = CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=cust.id,
                   product_id=prod.id, **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


# ── 1. Empty DB (runs first — no cores yet) ────────────────────────────────────

def test_empty_dashboard_all_zero(db):
    m = CoreMetricsService(db).dashboard_metrics()
    assert set(m.keys()) == set(CORE_DASHBOARD_KEYS)
    assert all(v == 0 for v in m.values())


# ── 2. Full scenario (single test — global aggregate) ──────────────────────────

def test_dashboard_full(db):
    cust = _cust(db); prod = _prod(db); vend = Vendor(name=f"V{next(_seq)}")
    db.add(vend); db.commit(); db.refresh(vend)

    # awaiting_return: 2 open; one overdue (past deadline), one not
    _core(db, cust, prod, status=CoreStatus.OPEN, customer_unit_charge=50.0,
          qty_charged=2, qty_returned=0, return_deadline=_PAST)        # overdue, liab 100
    _core(db, cust, prod, status=CoreStatus.OPEN, customer_unit_charge=30.0,
          qty_charged=1, qty_returned=0, return_deadline=_FUTURE)      # not overdue, liab 30
    # pending_inspection: RETURNED + HOLD
    held = _core(db, cust, prod, status=CoreStatus.RETURNED, customer_unit_charge=20.0,
                 qty_charged=1, inspection_outcome=CoreInspectionOutcome.HOLD)
    # ready_to_ship: RETURNED + vendor PENDING + inspection ACCEPTED
    _core(db, cust, prod, status=CoreStatus.RETURNED, customer_unit_charge=20.0,
          qty_charged=1, vendor_status=CoreVendorStatus.PENDING,
          inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    # awaiting_vendor: shipped to vendor
    _core(db, cust, prod, status=CoreStatus.SHIPPED_TO_VENDOR, customer_unit_charge=20.0,
          qty_charged=1)

    # core credits issued: two return events 40 + 60 = 100
    db.add(CoreReturnEvent(core_charge_id=held.id, qty_returned=1, credit_amount=40.0))
    db.add(CoreReturnEvent(core_charge_id=held.id, qty_returned=1, credit_amount=60.0))

    # vendor recoveries: open VCRs outstanding = (200-50) + (80-0) = 230; a CREDITED one excluded
    db.add(VendorCoreReturn(vcr_number=f"VCR-{next(_seq)}", vendor_id=vend.id,
                            status=VCRStatus.SHIPPED, expected_credit=200.0, actual_credit=50.0))
    db.add(VendorCoreReturn(vcr_number=f"VCR-{next(_seq)}", vendor_id=vend.id,
                            status=VCRStatus.DRAFT, expected_credit=80.0, actual_credit=0.0))
    db.add(VendorCoreReturn(vcr_number=f"VCR-{next(_seq)}", vendor_id=vend.id,
                            status=VCRStatus.CREDITED, expected_credit=999.0, actual_credit=999.0))
    db.commit()

    m = CoreMetricsService(db).dashboard_metrics()
    assert m["awaiting_return"] == 2
    assert m["overdue"] == 1
    assert m["pending_inspection"] == 1
    assert m["ready_to_ship"] == 1
    assert m["awaiting_vendor"] == 1
    assert m["outstanding_core_liability"] == 130.0     # 100 + 30
    assert m["aging_value"] == 100.0                    # overdue core only
    assert m["core_credits_issued"] == 100.0
    assert m["vendor_recoveries"] == 230.0
    assert m["vendor_recovery_count"] == 2              # SHIPPED + DRAFT (CREDITED excluded)


def test_cores_list_route_renders_with_metrics(client, db):
    # The route swapped its inline dict for the service — confirm it still renders.
    r = client.get("/cores/")
    assert r.status_code == 200
