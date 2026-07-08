"""
tests/test_r2_ops_fixes.py
==========================
R2 — two verified operational fixes.

(1) RA over-credit on partial returns:
    ReturnAuthorization.total_credit is now EXPECTED-vs-ACTUAL — before any
    goods are received it shows the full authorized credit (expected); once
    RAService.receive_goods has recorded a receipt, only qty_returned_to_stock
    earns credit (actual). close_ra issues the credit memo under the same rule:
    a customer returning 2 of 5 items is credited for 2, not 5. Closing
    straight from OPEN (no receive step) still credits the full authorization.

(2) PO receiving slip:
    GET /purchase-orders/{po_id}/receiving-slip renders a print-styled dock
    check-off sheet (SKU, title, vendor part #, ordered, received, outstanding,
    blank check-off column). The Receiving Queue ghost button is now a live
    new-tab link.

Patterns follow tests/test_returns_ra.py (module in-memory engine + TestClient
startup) and tests/test_r1_vendor_bills.py (direct PO/line model builders).
"""
from __future__ import annotations

import pathlib
import sys

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


def _uniq():
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"R2 Cust {_uniq()}", contact_name="QA",
                 email=f"r2{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, qty_on_hand=0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"R2-{_uniq():04d}", title="R2 Part", description="R2 Part",
                cost=20.0, markup_pct=50.0, qty_on_hand=qty_on_hand,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _vendor(db):
    from app.models.vendor import Vendor
    n = _uniq()
    v = Vendor(name=f"R2 Vendor {n}", vendor_code=f"R{n:03d}"[:4])
    db.add(v); db.commit(); db.refresh(v)
    return v


def _po(db, vendor, lines, status=None, expected_at=None):
    """PO with explicit lines: [(product, qty_ordered, qty_received), ...]."""
    from datetime import datetime
    from app.constants import POStatus
    from app.models.purchase_order import POLine, PurchaseOrder
    n = _uniq()
    po = PurchaseOrder(
        po_number=f"PO-R2-{n:04d}",
        vendor_id=vendor.id,
        status=status or POStatus.SENT,
        ordered_at=datetime(2026, 6, 1, 9, 0, 0),
        expected_at=expected_at,
        freight_in_cost=0.0,
        notes="", internal_notes="",
    )
    db.add(po); db.flush()
    for product, qty_ordered, qty_received in lines:
        db.add(POLine(
            po_id=po.id,
            product_id=product.id,
            description=product.title,
            qty_ordered=qty_ordered,
            qty_received=qty_received,
            qty_billed=0,
            unit_cost=product.cost,
            core_charge_per_unit=0.0,
            notes="",
        ))
    db.commit(); db.refresh(po)
    return po


def _partial_ra(db, qty=5, unit_price=10.0, fee=5.0):
    """Customer + RA with one line (qty @ unit_price, flat restocking fee)."""
    from app.constants import ReturnDisposition
    from app.services.ra_service import RAService
    cust = _customer(db)
    prod = _product(db, qty_on_hand=0)
    ra = RAService(db, None).create_ra(
        customer_id=cust.id, reason="R2 partial return",
        lines=[{"product_id": prod.id, "description": "R2 Part", "qty": qty,
                "unit_price": unit_price, "restocking_fee": fee,
                "disposition": ReturnDisposition.RETURN_TO_STOCK}],
    )
    return cust, prod, ra


# ===========================================================================
# (1a) total_credit — EXPECTED before any goods come back
# ===========================================================================

def test_total_credit_expected_on_draft_and_open(db):
    from app.services.ra_service import RAService

    cust, prod, ra = _partial_ra(db, qty=5, unit_price=10.0, fee=5.0)
    # DRAFT: expected credit = 5*10 - 5 = 45 (restocking fee honored)
    assert ra.total_credit == 45.0
    assert ra.goods_received is False

    # OPEN (approved, still nothing received): unchanged — never $0 pre-receive
    RAService(db, None).approve_ra(ra.id)
    db.refresh(ra)
    assert ra.total_credit == 45.0


# ===========================================================================
# (1b) total_credit — ACTUAL after a partial receive (2 of 5)
# ===========================================================================

def test_total_credit_actual_after_partial_receive(db):
    from app.constants import RAStatus, ReturnDisposition
    from app.services.ra_service import RAService

    cust, prod, ra = _partial_ra(db, qty=5, unit_price=10.0, fee=5.0)
    svc = RAService(db, None)
    svc.approve_ra(ra.id)
    svc.receive_goods(ra.id, line_updates=[{
        "line_id": ra.lines[0].id,
        "disposition": ReturnDisposition.RETURN_TO_STOCK,
        "qty_returned_to_stock": 2,
    }])
    db.refresh(ra)

    assert ra.status == RAStatus.RECEIVED
    assert ra.goods_received is True
    # ACTUAL: only the 2 returned units earn credit = 2*10 - 5 = 15 (not 45)
    assert ra.total_credit == 15.0
    # per-line display stays consistent with the header total
    assert ra.lines[0].line_credit == 15.0


def test_total_credit_zero_when_nothing_came_back(db):
    """Received with 0 to stock (e.g. scrapped/no-show) → $0, never -fee or full."""
    from app.constants import ReturnDisposition
    from app.services.ra_service import RAService

    cust, prod, ra = _partial_ra(db, qty=5, unit_price=10.0, fee=5.0)
    svc = RAService(db, None)
    svc.approve_ra(ra.id)
    svc.receive_goods(ra.id, line_updates=[{
        "line_id": ra.lines[0].id,
        "disposition": ReturnDisposition.SCRAP,
        "qty_returned_to_stock": 0,
    }])
    db.refresh(ra)
    assert ra.total_credit == 0.0
    assert ra.lines[0].line_credit == 0.0


# ===========================================================================
# (1c) close_ra credits the ACTUAL partial amount (money assertion on the CM)
# ===========================================================================

def test_close_ra_credits_actual_partial_amount(db):
    from app.constants import CreditMemoStatus, RAStatus, ReturnDisposition
    from app.models.credit_memo import CreditMemo
    from app.services.ra_service import RAService

    cust = _customer(db)
    prod_a = _product(db); prod_b = _product(db)
    svc = RAService(db, None)
    # Two lines: A = 5 @ $10 with $5 fee (2 come back), B = 3 @ $20 no fee
    # (nothing comes back). Old code credited B in full via the `or qty`
    # fallback and could mis-pair fees across filtered lines.
    ra = svc.create_ra(
        customer_id=cust.id, reason="R2 partial return",
        lines=[
            {"product_id": prod_a.id, "description": "Line A", "qty": 5,
             "unit_price": 10.0, "restocking_fee": 5.0,
             "disposition": ReturnDisposition.RETURN_TO_STOCK},
            {"product_id": prod_b.id, "description": "Line B", "qty": 3,
             "unit_price": 20.0, "restocking_fee": 0.0,
             "disposition": ReturnDisposition.RETURN_TO_STOCK},
        ],
    )
    # Pre-receive expected: (5*10 - 5) + (3*20) = 105
    assert ra.total_credit == 105.0

    start_balance = cust.credit_balance
    svc.approve_ra(ra.id)
    svc.receive_goods(ra.id, line_updates=[
        {"line_id": ra.lines[0].id,
         "disposition": ReturnDisposition.RETURN_TO_STOCK,
         "qty_returned_to_stock": 2},
        {"line_id": ra.lines[1].id,
         "disposition": ReturnDisposition.RETURN_TO_STOCK,
         "qty_returned_to_stock": 0},
    ])
    svc.close_ra(ra.id)
    db.refresh(ra); db.refresh(cust)

    # ACTUAL money: line A = 2*10 - 5 = 15; line B = 0 (nothing returned)
    assert ra.status == RAStatus.CLOSED
    assert cust.credit_balance == start_balance + 15.0

    cms = db.query(CreditMemo).filter(CreditMemo.ra_id == ra.id).all()
    assert len(cms) == 1
    assert cms[0].total_amount == 15.0
    assert cms[0].status == CreditMemoStatus.APPLIED


def test_close_ra_from_open_credits_expected_full(db):
    """No receive step (OPEN → CLOSED): full authorized credit is preserved."""
    from app.models.credit_memo import CreditMemo
    from app.services.ra_service import RAService

    cust, prod, ra = _partial_ra(db, qty=5, unit_price=10.0, fee=5.0)
    start_balance = cust.credit_balance
    svc = RAService(db, None)
    svc.approve_ra(ra.id)
    svc.close_ra(ra.id)  # straight from OPEN — goods never received

    db.refresh(ra); db.refresh(cust)
    assert ra.total_credit == 45.0
    assert cust.credit_balance == start_balance + 45.0
    cm = db.query(CreditMemo).filter(CreditMemo.ra_id == ra.id).first()
    assert cm is not None and cm.total_amount == 45.0


# ===========================================================================
# (2) Receiving slip print route + queue wiring
# ===========================================================================

def test_receiving_slip_route_renders_outstanding(client, db):
    from app.models.product import ProductVendorSource

    vendor = _vendor(db)
    prod = _product(db)
    db.add(ProductVendorSource(product_id=prod.id, vendor_id=vendor.id,
                               vendor_part_number="VP-R2-077",
                               vendor_cost=12.5, is_preferred=True))
    db.commit()
    po = _po(db, vendor, [(prod, 23, 9)])  # outstanding = 14

    resp = client.get(f"/purchase-orders/{po.id}/receiving-slip")
    assert resp.status_code == 200
    body = resp.text
    assert "Receiving Slip" in body
    assert po.po_number in body
    assert vendor.name in body
    assert prod.sku in body
    assert "VP-R2-077" in body          # vendor part #
    assert ">23<" in body               # qty ordered
    assert ">9<" in body                # qty received so far
    assert ">14<" in body               # qty outstanding (unit totals row)
    # dock slip carries no money columns
    assert "Unit Cost" not in body


def test_receiving_slip_has_blank_checkoff_column(client, db):
    vendor = _vendor(db)
    prod = _product(db)
    po = _po(db, vendor, [(prod, 10, 0)])

    body = client.get(f"/purchase-orders/{po.id}/receiving-slip").text
    assert "Check-Off" in body          # column header
    assert "checkoff-line" in body      # blank write-in line
    assert "received" in body           # '___ received' label


def test_receiving_slip_missing_po_redirects(client):
    resp = client.get("/purchase-orders/999999/receiving-slip",
                      follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/purchase-orders/"


def test_receiving_queue_button_links_to_slip(client, db):
    vendor = _vendor(db)
    prod = _product(db)
    po = _po(db, vendor, [(prod, 4, 1)])

    body = client.get("/purchase-orders/receiving").text
    assert f"/purchase-orders/{po.id}/receiving-slip" in body
    assert "coming soon" not in body
