"""
tests/test_so_cancel_line_status.py
===================================
Regression: cancelling a single SO line (e.g. the backordered remainder after the
in-stock qty has already been invoiced) must recompute SalesOrder.status.

Before the fix, cancel_line froze the line's qty_ordered at qty_invoiced but never
touched SO.status — so an order that was actually finished (invoiced what it could,
cancelled the rest) stayed OPEN/PARTIAL forever, lingering in the Open tab and still
offering fulfil/invoice actions on a closed order.

Guards locked here:
  - cancel the open remainder of a partially-invoiced SO  -> SO flips to INVOICED
  - cancel a line on an SO with ZERO invoiced qty         -> status NOT flipped
    (stays OPEN); we never claim an order is invoiced when nothing has been.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_so_cancel_line_status.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.constants import LineType, SOStatus
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import SalesOrder, SOLine
from app.services.sales_order_service import SalesOrderService
from tests.conftest import activate, fresh_engine

_UID = 1
_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _so_two_lines(db, *, status, a_invoiced, b_invoiced):
    """An SO with two product lines:
        line A: qty_ordered=5, qty_invoiced=a_invoiced (the in-stock line)
        line B: qty_ordered=3, qty_invoiced=b_invoiced, committed (the backorder)
    Returns (so, line_a, line_b)."""
    n = next(_seq)
    cust = Customer(company_name=f"Line Cancel Co {n}", is_active=True)
    db.add(cust); db.flush()
    pa = Product(sku=f"LCA-{n:05d}", title="In-stock part", cost=10.0,
                 qty_on_hand=20, qty_committed=0)
    pb = Product(sku=f"LCB-{n:05d}", title="Backordered part", cost=10.0,
                 qty_on_hand=10, qty_committed=3)
    db.add_all([pa, pb]); db.flush()
    so = SalesOrder(so_number=f"SO-LC-{n:04d}", customer_id=cust.id,
                    status=status, notes="", internal_notes="")
    db.add(so); db.flush()
    la = SOLine(so_id=so.id, product_id=pa.id, line_type=LineType.PRODUCT,
                description="In-stock part", qty_ordered=5, qty_committed=0,
                qty_fulfilled=a_invoiced, qty_invoiced=a_invoiced, unit_price=50.0)
    lb = SOLine(so_id=so.id, product_id=pb.id, line_type=LineType.PRODUCT,
                description="Backordered part", qty_ordered=3, qty_committed=3,
                qty_fulfilled=b_invoiced, qty_invoiced=b_invoiced, unit_price=50.0)
    db.add_all([la, lb]); db.commit()
    return so, la, lb


def test_cancel_backorder_line_flips_so_to_invoiced(db):
    # line A fully invoiced (5/5), line B backordered & uninvoiced -> SO is PARTIAL.
    so, la, lb = _so_two_lines(db, status=SOStatus.PARTIAL, a_invoiced=5, b_invoiced=0)
    SalesOrderService(db, _UID).cancel_line(lb.id)
    db.refresh(so); db.refresh(lb)
    assert lb.qty_ordered == 0                  # frozen at qty_invoiced
    assert so.is_fully_invoiced is True
    assert so.status == SOStatus.INVOICED       # no longer stranded in PARTIAL/Open


def test_cancel_line_does_not_flip_zero_invoiced_so(db):
    # Nothing invoiced yet; cancelling a line must NOT claim the order is invoiced.
    so, la, lb = _so_two_lines(db, status=SOStatus.OPEN, a_invoiced=0, b_invoiced=0)
    SalesOrderService(db, _UID).cancel_line(lb.id)
    db.refresh(so); db.refresh(lb)
    assert lb.qty_ordered == 0
    assert so.status == SOStatus.OPEN           # stays OPEN, not falsely INVOICED
