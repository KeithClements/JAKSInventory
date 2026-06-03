"""
tests/test_so_metrics_gaps.py
=============================
QA gap guards for SalesOrderMetricsService.dashboard_metrics() (§5.1).

EXTENDS Backend's test_so_metrics.py, which asserts the whole strip against one
combined world. dashboard_metrics() aggregates the ENTIRE DB, so that single
world can't isolate the per-line / per-status branches. These tests each build a
pristine DB and pin one branch:

  • a MIXED SO (one shippable + one backordered line) counts as WAITING, not
    ready, and backordered_value picks up ONLY the unfulfilled line;
  • a fully-invoiced backorder line (remaining 0) is NOT waiting;
  • source==BACKORDER alone makes a line waiting (the OR with line_status);
  • a HOLD SO's backordered value IS counted, but the SO is NOT in the
    waiting/ready counts (those gate on OPEN/PARTIAL);
  • open_core_liability filters to OPEN/PARTIAL customer-owed cores and counts
    outstanding qty (vendor-side + CLOSED excluded);
  • an empty DB yields every key at 0.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_so_metrics_gaps.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.constants import (
    CoreDirection, CoreStatus, SOLineSource, SOLineStatus, SOStatus,
)
from app.models.core import CoreCharge
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.services.sales_order_metrics_service import (
    SalesOrderMetricsService, SO_DASHBOARD_KEYS,
)
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    """Pristine DB per test — dashboard_metrics aggregates the whole DB."""
    SessionLocal = activate(fresh_engine())
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db, **kw) -> Customer:
    c = Customer(company_name=f"SO Gap Co {next(_seq)}", contact_name="QA", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _so(db, cust, status, lines) -> SalesOrder:
    """lines = [{qty_ordered, unit_price, line_status, source, qty_invoiced}, ...]."""
    so = SalesOrder(so_number=f"SO-GAP-{next(_seq):05d}", customer_id=cust.id, status=status)
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


def _core(db, cust, **kw):
    prod = Product(sku=f"SOG-{next(_seq)}", title="core", cost=5.0)
    db.add(prod); db.commit(); db.refresh(prod)
    db.add(CoreCharge(customer_id=cust.id, product_id=prod.id, **kw))
    db.commit()


def _m(db) -> dict:
    return SalesOrderMetricsService(db).dashboard_metrics()


# ── Mixed SO: waiting wins; only the unfulfilled line is backordered ─────────

def test_mixed_so_counts_waiting_not_ready_and_backorders_only_the_unfulfilled_line(db):
    cust = _customer(db)
    _so(db, cust, SOStatus.OPEN, [
        {"qty_ordered": 2, "unit_price": 100.0, "line_status": SOLineStatus.RESERVED_STOCK},   # 200 shippable
        {"qty_ordered": 1, "unit_price": 300.0,                                                  # 300 waiting
         "line_status": SOLineStatus.AWAITING_PO_RECEIPT, "source": SOLineSource.BACKORDER},
    ])
    m = _m(db)
    assert m["open_so_value"] == 500.0
    assert m["backordered_value"] == 300.0          # ONLY the waiting line, not the whole SO
    assert m["waiting_on_inventory"] == 1
    assert m["ready_to_ship"] == 0                   # a waiting line makes the whole SO "waiting"
    assert m["open_count"] == 1


# ── A fully-invoiced backorder line is no longer waiting ─────────────────────

def test_fully_invoiced_backorder_line_is_not_waiting(db):
    cust = _customer(db)
    _so(db, cust, SOStatus.OPEN, [
        {"qty_ordered": 1, "qty_invoiced": 1, "unit_price": 300.0,   # remaining 0
         "line_status": SOLineStatus.AWAITING_PO_RECEIPT, "source": SOLineSource.BACKORDER},
    ])
    m = _m(db)
    assert m["backordered_value"] == 0.0
    assert m["waiting_on_inventory"] == 0
    assert m["ready_to_ship"] == 0      # nothing remaining to ship either


# ── source==BACKORDER alone (non-waiting line_status) still counts as waiting ─

def test_source_backorder_alone_marks_line_waiting(db):
    cust = _customer(db)
    _so(db, cust, SOStatus.OPEN, [
        {"qty_ordered": 1, "unit_price": 250.0,
         "line_status": SOLineStatus.STOCK, "source": SOLineSource.BACKORDER},
    ])
    m = _m(db)
    assert m["backordered_value"] == 250.0
    assert m["waiting_on_inventory"] == 1
    assert m["ready_to_ship"] == 0


# ── HOLD SO: value counts, but it's not in the waiting/ready counts ──────────

def test_hold_so_backorder_adds_value_but_not_waiting_count(db):
    cust = _customer(db)
    _so(db, cust, SOStatus.HOLD, [
        {"qty_ordered": 1, "unit_price": 300.0,
         "line_status": SOLineStatus.AWAITING_PO_RECEIPT, "source": SOLineSource.BACKORDER},
    ])
    m = _m(db)
    assert m["on_hold"] == 1
    assert m["open_so_value"] == 300.0
    assert m["backordered_value"] == 300.0      # HOLD waiting lines DO add to value
    assert m["waiting_on_inventory"] == 0       # ...but HOLD isn't counted (gate: OPEN/PARTIAL)
    assert m["ready_to_ship"] == 0


# ── open_core_liability: direction + status + partial-return filter ──────────

def test_open_core_liability_filters_and_counts_outstanding(db):
    cust = _customer(db)
    _core(db, cust, direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_unit_charge=50.0,
          qty_charged=3, qty_returned=1, status=CoreStatus.OPEN)        # 50×2 = 100
    _core(db, cust, direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_unit_charge=20.0,
          qty_charged=2, qty_returned=0, status=CoreStatus.PARTIAL)     # 20×2 = 40
    _core(db, cust, direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_unit_charge=99.0,
          qty_charged=1, qty_returned=0, status=CoreStatus.CLOSED)      # excluded
    _core(db, cust, direction=CoreDirection.VENDOR_OWES_CREDIT, customer_unit_charge=88.0,
          qty_charged=1, qty_returned=0, status=CoreStatus.OPEN)        # excluded (vendor)
    assert _m(db)["open_core_liability"] == 140.0


# ── Empty DB → every key present, all zero ───────────────────────────────────

def test_empty_db_all_zero(db):
    m = _m(db)
    assert set(m.keys()) == set(SO_DASHBOARD_KEYS)
    assert all(m[k] == 0 for k in SO_DASHBOARD_KEYS)
