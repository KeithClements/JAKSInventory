"""
tests/test_so_metrics.py
========================
Phase 2 §5.1 / §5.2 / §5.10 — SalesOrderMetricsService.

Covers the SO dashboard strip (open value, backordered value, waiting,
ready-to-ship, on-hold, fulfilled-today, open core liability), the SO↔PO status
rollup derived from linked_po_line_id, and the SOLine.eta_date seam.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime, date

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    SOStatus, SOLineStatus, SOLineSource, POStatus, CoreDirection, CoreStatus,
)
from app.models.customer import Customer
from app.models.vendor import Vendor
from app.models.quote import SalesOrder, SOLine
from app.models.purchase_order import PurchaseOrder, POLine
from app.models.core import CoreCharge
from app.models.product import Product
from app.services.sales_order_metrics_service import SalesOrderMetricsService, SO_DASHBOARD_KEYS
from app.services.sales_order_service import SalesOrderService
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


def _customer(db):
    c = Customer(company_name=f"SO Metrics Co {next(_seq)}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    v = Vendor(name=f"Vendor {next(_seq)}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _so(db, cust, status, lines, updated_at=None):
    """lines = list of dicts: {qty_ordered, unit_price, line_status, source,
    qty_invoiced, qty_cancelled}."""
    so = SalesOrder(so_number=f"SO-{next(_seq):05d}", customer_id=cust.id, status=status)
    if updated_at is not None:
        so.updated_at = updated_at
    for spec in lines:
        so.lines.append(SOLine(
            qty_ordered=spec.get("qty_ordered", 1),
            qty_invoiced=spec.get("qty_invoiced", 0),
            qty_cancelled=spec.get("qty_cancelled", 0),
            unit_price=spec.get("unit_price", 0.0),
            line_status=spec.get("line_status", SOLineStatus.STOCK),
            source=spec.get("source", SOLineSource.STOCK),
        ))
    db.add(so); db.commit(); db.refresh(so)
    return so


def _po_line(db, vendor, po_status, qty_ordered, qty_received):
    po = PurchaseOrder(po_number=f"PO-{next(_seq):05d}", vendor_id=vendor.id, status=po_status)
    db.add(po); db.commit(); db.refresh(po)
    pol = POLine(po_id=po.id, description="part", qty_ordered=qty_ordered,
                 qty_received=qty_received, unit_cost=10.0)
    db.add(pol); db.commit(); db.refresh(pol)
    return pol


# ── §5.1 Dashboard ─────────────────────────────────────────────────────────────

@pytest.fixture()
def dashboard_world(db):
    cust = _customer(db)
    # Active OPEN, all available → ready to ship (value 200)
    _so(db, cust, SOStatus.OPEN, [
        {"qty_ordered": 2, "unit_price": 100.0, "line_status": SOLineStatus.RESERVED_STOCK},
    ])
    # Active OPEN with a backordered line awaiting PO receipt (value 300)
    _so(db, cust, SOStatus.OPEN, [
        {"qty_ordered": 1, "unit_price": 300.0,
         "line_status": SOLineStatus.AWAITING_PO_RECEIPT, "source": SOLineSource.BACKORDER},
    ])
    # On hold (value 50)
    _so(db, cust, SOStatus.HOLD, [
        {"qty_ordered": 1, "unit_price": 50.0, "line_status": SOLineStatus.STOCK},
    ])
    # Invoiced today → fulfilled_today (NOT active; excluded from open value)
    _so(db, cust, SOStatus.INVOICED, [
        {"qty_ordered": 1, "unit_price": 999.0, "qty_invoiced": 1},
    ])
    # Invoiced long ago → must NOT count as fulfilled today
    _so(db, cust, SOStatus.INVOICED,
        [{"qty_ordered": 1, "unit_price": 999.0, "qty_invoiced": 1}],
        updated_at=datetime(2020, 1, 1))
    # Open customer core liability: 40 × (2-1) = 40; a CLOSED one is ignored
    prod = Product(sku=f"P{next(_seq)}", title="core part", cost=5.0)
    db.add(prod); db.commit(); db.refresh(prod)
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=cust.id,
                      product_id=prod.id, customer_unit_charge=40.0, qty_charged=2,
                      qty_returned=1, status=CoreStatus.OPEN))
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=cust.id,
                      product_id=prod.id, customer_unit_charge=99.0, qty_charged=1,
                      qty_returned=0, status=CoreStatus.CLOSED))
    db.commit()
    return cust


def test_dashboard(db, dashboard_world):
    # Single test (one world). dashboard_metrics() aggregates ALL SOs in the DB,
    # so absolute values are asserted against exactly one built world — splitting
    # into two tests would build the world twice and double the totals.
    m = SalesOrderMetricsService(db).dashboard_metrics()
    assert set(m.keys()) == set(SO_DASHBOARD_KEYS)
    assert m["open_so_value"] == 550.0          # 200 + 300 + 50 (active OPEN/OPEN/HOLD)
    assert m["backordered_value"] == 300.0
    assert m["waiting_on_inventory"] == 1
    assert m["ready_to_ship"] == 1
    assert m["on_hold"] == 1
    assert m["fulfilled_today"] == 1            # today's INVOICED only; 2020 excluded
    assert m["open_core_liability"] == 40.0
    assert m["open_count"] == 3                 # the active SOs


# ── §5.10 SO↔PO rollup ─────────────────────────────────────────────────────────

def _so_with_linked_line(db, cust, pol):
    so = SalesOrder(so_number=f"SO-{next(_seq):05d}", customer_id=cust.id, status=SOStatus.OPEN)
    so.lines.append(SOLine(qty_ordered=pol.qty_ordered, unit_price=100.0,
                           linked_po_line_id=pol.id,
                           fulfillment_source="linked_po", line_status=SOLineStatus.LINKED_PO))
    db.add(so); db.commit(); db.refresh(so)
    return so


@pytest.mark.parametrize("po_status,qty_ord,qty_rec,expected", [
    (POStatus.DRAFT,    5, 0, "draft"),
    (POStatus.SENT,     5, 0, "ordered"),
    (POStatus.PARTIAL,  5, 2, "partial"),
    (POStatus.RECEIVED, 5, 5, "received"),
    (POStatus.CANCELLED,5, 0, "cancelled"),
])
def test_po_rollup_status(db, po_status, qty_ord, qty_rec, expected):
    cust = _customer(db); vend = _vendor(db)
    pol = _po_line(db, vend, po_status, qty_ord, qty_rec)
    so = _so_with_linked_line(db, cust, pol)
    roll = SalesOrderMetricsService(db).po_link_status(so.lines[0])
    assert roll is not None
    assert roll["status"] == expected
    assert roll["po_number"] == pol.po.po_number
    assert roll["qty_ordered"] == qty_ord
    assert roll["qty_received"] == qty_rec


def test_po_rollup_none_when_unlinked(db):
    cust = _customer(db)
    so = _so(db, cust, SOStatus.OPEN, [{"qty_ordered": 1, "unit_price": 10.0}])
    assert SalesOrderMetricsService(db).po_link_status(so.lines[0]) is None


def test_po_link_map_batches_lines(db):
    cust = _customer(db); vend = _vendor(db)
    pol1 = _po_line(db, vend, POStatus.SENT, 3, 0)
    pol2 = _po_line(db, vend, POStatus.RECEIVED, 2, 2)
    so = SalesOrder(so_number=f"SO-{next(_seq):05d}", customer_id=cust.id, status=SOStatus.OPEN)
    so.lines.append(SOLine(qty_ordered=3, unit_price=10.0, linked_po_line_id=pol1.id))
    so.lines.append(SOLine(qty_ordered=2, unit_price=10.0, linked_po_line_id=pol2.id))
    so.lines.append(SOLine(qty_ordered=1, unit_price=10.0))  # unlinked → absent from map
    db.add(so); db.commit(); db.refresh(so)

    m = SalesOrderMetricsService(db).po_link_map(so)
    statuses = sorted(v["status"] for v in m.values())
    assert statuses == ["ordered", "received"]
    assert len(m) == 2  # the unlinked line is not in the map


# ── §5.2 ETA seam ──────────────────────────────────────────────────────────────

def test_set_line_eta_and_clear(db):
    cust = _customer(db)
    so = _so(db, cust, SOStatus.OPEN, [{"qty_ordered": 1, "unit_price": 10.0}])
    line = so.lines[0]
    SalesOrderService(db, None).set_line_eta(line.id, "2026-07-15")
    db.refresh(line)
    assert line.eta_date == date(2026, 7, 15)
    # blank clears it
    SalesOrderService(db, None).set_line_eta(line.id, "")
    db.refresh(line)
    assert line.eta_date is None


def test_eta_surfaces_in_rollup(db):
    cust = _customer(db); vend = _vendor(db)
    pol = _po_line(db, vend, POStatus.SENT, 4, 0)
    so = _so_with_linked_line(db, cust, pol)
    line = so.lines[0]
    SalesOrderService(db, None).set_line_eta(line.id, "2026-08-01")
    db.refresh(line)
    roll = SalesOrderMetricsService(db).po_link_status(line)
    assert roll["eta_date"] == date(2026, 8, 1)


def test_eta_route(client, db):
    cust = _customer(db)
    so = _so(db, cust, SOStatus.OPEN, [{"qty_ordered": 1, "unit_price": 10.0}])
    line_id = so.lines[0].id
    r = client.post(f"/sales-orders/{so.id}/lines/{line_id}/eta",
                    data={"eta_date": "2026-09-09"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    line = db.query(SOLine).get(line_id)
    assert line.eta_date == date(2026, 9, 9)
