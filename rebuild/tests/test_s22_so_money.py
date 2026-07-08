"""
tests/test_s22_so_money.py
==========================
Phase-0 Lane C — Sales-order money correctness (Risks #3 + #4).

Guards locked here:
  Tax/total (C1):
    - SalesOrder.taxable_base / tax_amount / total mirror Quote: a taxable SO's
      total = subtotal + tax; cores/freight excluded from the taxable base; a
      non-taxable SO has tax_amount 0 and total == subtotal.
  FULL-mode deposit gate (#3):
    - A taxable Pay-in-Full SO whose deposit only covers the pre-tax subtotal is
      BLOCKED at fulfill_and_invoice (it still owes sales tax); the error shows
      the tax-inclusive figure.
    - The same SO with deposit == total FULFILLS (gate passes).
  Cancelled lines (#4 / C6):
    - cancel_line records the cancelled portion in qty_cancelled BEFORE freezing
      qty_ordered, so the print view can act on it.
    - A line cancelled before any invoicing does NOT print as a live row.
    - A partially-invoiced-then-cancelled line prints the invoiced qty with an
      "N cancelled" tag.

Run:
    python -m pytest tests/test_s22_so_money.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import LineType, SOStatus, SOPaymentMode
from app.main import app as fastapi_app
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


@pytest.fixture()
def client(db):
    # db fixture has already activated the isolated engine + get_db override.
    return TestClient(fastapi_app)


def _customer(db, name="C1 Money Co"):
    n = next(_seq)
    c = Customer(company_name=f"{name} {n}", is_active=True)
    db.add(c); db.flush()
    return c


# ── Tax / total properties (C1) ──────────────────────────────────────────────

def test_taxable_so_total_includes_tax(db):
    cust = _customer(db)
    so = SalesOrder(
        so_number=f"SO-T-{next(_seq):04d}", customer_id=cust.id,
        status=SOStatus.OPEN, is_taxable=True, tax_rate_snapshot=10.0,
        notes="", internal_notes="",
    )
    db.add(so); db.flush()
    # $1,000 taxable product + a $200 non-taxable core child.
    db.add_all([
        SOLine(so_id=so.id, line_type=LineType.PRODUCT, description="Head",
               qty_ordered=1, unit_price=1000.0),
        SOLine(so_id=so.id, line_type=LineType.CORE_CHARGE, description="Core",
               qty_ordered=1, unit_price=200.0),
    ])
    db.commit(); db.refresh(so)

    assert so.subtotal == 1200.0          # both lines
    assert so.taxable_base == 1000.0      # core excluded
    assert so.tax_amount == 100.0         # 10% of 1000
    assert so.total == 1300.0             # subtotal + tax


def test_non_taxable_so_total_equals_subtotal(db):
    cust = _customer(db)
    so = SalesOrder(
        so_number=f"SO-NT-{next(_seq):04d}", customer_id=cust.id,
        status=SOStatus.OPEN, is_taxable=False, tax_rate_snapshot=0.0,
        notes="", internal_notes="",
    )
    db.add(so); db.flush()
    db.add(SOLine(so_id=so.id, line_type=LineType.PRODUCT, description="Pump",
                  qty_ordered=2, unit_price=500.0))
    db.commit(); db.refresh(so)

    assert so.subtotal == 1000.0
    assert so.taxable_base == 0.0
    assert so.tax_amount == 0.0
    assert so.total == 1000.0


# ── FULL-mode deposit gate (#3) ──────────────────────────────────────────────

def _full_taxable_so(db):
    """Pay-in-Full taxable SO: $1,000 product, 10% tax -> total $1,100."""
    cust = _customer(db, "Full Pay Co")
    prod = Product(sku=f"FP-{next(_seq):05d}", title="Special part",
                   cost=400.0, qty_on_hand=10, qty_committed=1)
    db.add(prod); db.flush()
    so = SalesOrder(
        so_number=f"SO-FP-{next(_seq):04d}", customer_id=cust.id,
        status=SOStatus.OPEN, payment_mode=SOPaymentMode.FULL,
        is_taxable=True, tax_rate_snapshot=10.0,
        deposit_amount=0.0, notes="", internal_notes="",
    )
    db.add(so); db.flush()
    line = SOLine(so_id=so.id, product_id=prod.id, line_type=LineType.PRODUCT,
                  description="Special part", qty_ordered=1, qty_committed=1,
                  unit_price=1000.0, unit_cost=400.0)
    db.add(line); db.commit()
    return so, line


def test_full_deposit_covering_subtotal_only_is_blocked(db):
    so, line = _full_taxable_so(db)
    so.deposit_amount = 1000.0   # == subtotal, but < total ($1,100)
    db.commit()

    with pytest.raises(ValueError) as exc:
        SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 1})
    msg = str(exc.value)
    # The gate must quote the tax-inclusive figure, not the bare subtotal.
    assert "1100.00" in msg
    db.refresh(so)
    assert so.status == SOStatus.OPEN          # nothing fulfilled
    assert line.qty_invoiced == 0


def test_full_deposit_covering_total_fulfills(db):
    so, line = _full_taxable_so(db)
    so.deposit_amount = 1100.0   # == total -> gate passes
    db.commit()

    SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 1})
    db.refresh(so); db.refresh(line)
    assert line.qty_invoiced == 1
    assert so.status == SOStatus.INVOICED


# ── Cancelled lines (#4 / C6) ────────────────────────────────────────────────

def _so_for_cancel(db, *, a_invoiced, b_invoiced):
    """SO with two product lines (A qty 5, B qty 3). Returns (so, line_a, line_b)."""
    cust = _customer(db, "Cancel Print Co")
    pa = Product(sku=f"CPA-{next(_seq):05d}", title="Part A", cost=10.0,
                 qty_on_hand=20, qty_committed=0)
    pb = Product(sku=f"CPB-{next(_seq):05d}", title="Part B", cost=10.0,
                 qty_on_hand=10, qty_committed=3)
    db.add_all([pa, pb]); db.flush()
    so = SalesOrder(so_number=f"SO-CP-{next(_seq):04d}", customer_id=cust.id,
                    status=SOStatus.PARTIAL, notes="", internal_notes="")
    db.add(so); db.flush()
    la = SOLine(so_id=so.id, product_id=pa.id, line_type=LineType.PRODUCT,
                description="Part A", qty_ordered=5, qty_committed=0,
                qty_fulfilled=a_invoiced, qty_invoiced=a_invoiced, unit_price=50.0)
    lb = SOLine(so_id=so.id, product_id=pb.id, line_type=LineType.PRODUCT,
                description="Part B", qty_ordered=3, qty_committed=3,
                qty_fulfilled=b_invoiced, qty_invoiced=b_invoiced, unit_price=50.0)
    db.add_all([la, lb]); db.commit()
    return so, la, lb


def test_cancel_before_invoicing_records_qty_cancelled(db):
    so, la, lb = _so_for_cancel(db, a_invoiced=5, b_invoiced=0)
    SalesOrderService(db, _UID).cancel_line(lb.id)
    db.refresh(lb)
    assert lb.qty_cancelled == 3       # whole order cancelled
    assert lb.qty_ordered == 0         # frozen at qty_invoiced


def test_partial_then_cancel_records_remainder(db):
    so, la, lb = _so_for_cancel(db, a_invoiced=5, b_invoiced=1)
    SalesOrderService(db, _UID).cancel_line(lb.id)
    db.refresh(lb)
    assert lb.qty_cancelled == 2       # 3 ordered - 1 invoiced
    assert lb.qty_ordered == 1         # frozen at the invoiced qty


def test_fully_cancelled_line_not_printed_as_live_row(client, db):
    so, la, lb = _so_for_cancel(db, a_invoiced=5, b_invoiced=0)
    SalesOrderService(db, _UID).cancel_line(lb.id)

    html = client.get(f"/sales-orders/{so.id}/print").text
    # Part A still prints; cancelled-before-invoicing Part B is skipped entirely.
    assert "Part A" in html
    assert "Part B" not in html


def test_partial_cancel_prints_invoiced_qty_with_tag(client, db):
    so, la, lb = _so_for_cancel(db, a_invoiced=5, b_invoiced=1)
    SalesOrderService(db, _UID).cancel_line(lb.id)

    html = client.get(f"/sales-orders/{so.id}/print").text
    # The partially-invoiced line still prints, with a "2 cancelled" tag.
    assert "Part B" in html
    assert "2 cancelled" in html
