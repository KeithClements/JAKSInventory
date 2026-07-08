"""
tests/test_vendor_returns.py
============================
Functional integration tests for the Vendor Return lifecycle
(TESTING_FEEDBACK §5.3): Create -> Ship -> vendor Decision -> Close.
This is the NON-CORE merchandise return path (wrong/defective part), distinct
from VendorCoreReturn. Zero prior functional confirmation.

Strategy: VendorReturnService driven directly with current_user_id=None
(bypasses the ISSUE_CREDIT_MEMO gate). Shipping decrements inventory, so we
assert the ledger row. The vendor-decision step optionally auto-creates a
VendorCreditMemo (auto_create_vcm) — we test both the isolated decision
(auto_create_vcm=False) and the auto-VCM path.
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


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"VR Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db, qty_on_hand=10):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"VR-{_uniq():04d}", title="VR Part", description="VR Part",
                cost=20.0, markup_pct=50.0, qty_on_hand=qty_on_hand,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _make_vr(db, vendor, product, *, qty=3, unit_credit=20.0):
    from app.services.vendor_return_service import VendorReturnService
    return VendorReturnService(db, None).create_vendor_return(
        vendor_id=vendor.id, reason="Defective batch",
        lines=[{"product_id": product.id, "description": "VR Part", "qty": qty,
                "expected_unit_credit": unit_credit}],
    )


# ===========================================================================
# §5.3 — Create
# ===========================================================================

def test_create_vendor_return(db):
    from app.constants import VendorReturnStatus
    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod, qty=3, unit_credit=20.0)
    db.refresh(vr)
    assert vr.status == VendorReturnStatus.DRAFT
    assert vr.vr_number.startswith("VR-")
    assert len(vr.lines) == 1
    # expected_credit = qty * expected_unit_credit = 3 * 20 = 60
    assert vr.expected_credit == 60.0


def test_create_requires_vendor_and_lines(db):
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db)
    with pytest.raises(ValueError):
        VendorReturnService(db, None).create_vendor_return(
            vendor_id=ven.id, reason="x", lines=[])
    db.rollback()
    with pytest.raises(ValueError):
        VendorReturnService(db, None).create_vendor_return(
            vendor_id=999999, reason="x",
            lines=[{"description": "a", "qty": 1, "expected_unit_credit": 1.0}])
    db.rollback()


# ===========================================================================
# §5.3 — Ship (decrements inventory)
# ===========================================================================

def test_ship_decrements_inventory_and_writes_ledger(db):
    from app.constants import InventoryTxnType, VendorReturnStatus
    from app.models.inventory import InventoryTransaction
    from app.models.product import Product
    from app.services.vendor_return_service import VendorReturnService

    ven = _vendor(db); prod = _product(db, qty_on_hand=10)
    vr = _make_vr(db, ven, prod, qty=3)

    VendorReturnService(db, None).ship_return(
        vr.id, tracking_number="1Z-VR-1", decrement_inventory=True)

    db.refresh(vr); db.refresh(prod)
    assert vr.status == VendorReturnStatus.SHIPPED
    assert vr.shipped_at is not None
    assert prod.qty_on_hand == 7  # 10 - 3
    txn = (db.query(InventoryTransaction)
           .filter(InventoryTransaction.product_id == prod.id,
                   InventoryTransaction.transaction_type == InventoryTxnType.MANUAL_ADJUSTMENT)
           .first())
    assert txn is not None and txn.qty_change == -3


def test_ship_requires_tracking_number(db):
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod)
    with pytest.raises(ValueError):
        VendorReturnService(db, None).ship_return(vr.id, tracking_number="")
    db.rollback()


# ===========================================================================
# §5.3 — Vendor decision
# ===========================================================================

def test_vendor_decision_all_accepted(db):
    from app.constants import VendorReturnLineOutcome, VendorReturnStatus
    from app.services.vendor_return_service import VendorReturnService

    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod, qty=3, unit_credit=20.0)
    svc = VendorReturnService(db, None)
    svc.ship_return(vr.id, tracking_number="1Z-VR-2")

    # isolate the decision from the cross-service VCM creation
    svc.record_vendor_decision(
        vr.id,
        line_outcomes=[{"line_id": vr.lines[0].id,
                        "outcome": VendorReturnLineOutcome.ACCEPTED.value,
                        "actual_unit_credit": 20.0}],
        auto_create_vcm=False)

    db.refresh(vr)
    assert vr.status == VendorReturnStatus.ACCEPTED
    assert vr.actual_credit == 60.0
    assert vr.credit_difference == 0.0  # expected 60, actual 60


def test_vendor_decision_partial(db):
    from app.constants import VendorReturnLineOutcome, VendorReturnStatus
    from app.services.vendor_return_service import VendorReturnService

    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod, qty=3, unit_credit=20.0)
    svc = VendorReturnService(db, None)
    svc.ship_return(vr.id, tracking_number="1Z-VR-3")
    # vendor credits less than expected -> PARTIAL with a positive difference
    svc.record_vendor_decision(
        vr.id,
        line_outcomes=[{"line_id": vr.lines[0].id,
                        "outcome": VendorReturnLineOutcome.PARTIAL.value,
                        "actual_unit_credit": 15.0}],
        auto_create_vcm=False)
    db.refresh(vr)
    assert vr.status == VendorReturnStatus.PARTIAL
    assert vr.actual_credit == 45.0
    assert vr.credit_difference == 15.0  # 60 - 45


def test_vendor_decision_auto_creates_credit_memo(db):
    """The decision step (not close) is what posts the vendor credit memo."""
    from app.constants import VendorReturnLineOutcome, VendorReturnStatus
    from app.models.vendor_credit import VendorCreditMemo
    from app.services.vendor_return_service import VendorReturnService

    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod, qty=2, unit_credit=20.0)
    svc = VendorReturnService(db, None)
    svc.ship_return(vr.id, tracking_number="1Z-VR-4")
    svc.record_vendor_decision(
        vr.id,
        line_outcomes=[{"line_id": vr.lines[0].id,
                        "outcome": VendorReturnLineOutcome.ACCEPTED.value,
                        "actual_unit_credit": 20.0}],
        auto_create_vcm=True)
    db.refresh(vr)
    assert vr.status == VendorReturnStatus.ACCEPTED
    vcm = (db.query(VendorCreditMemo)
           .filter(VendorCreditMemo.vendor_return_id == vr.id).first())
    assert vcm is not None, "accepted vendor return should post a VendorCreditMemo"


# ===========================================================================
# §5.3 — Close (full happy path)
# ===========================================================================

def test_full_create_ship_decision_close(db):
    from app.constants import VendorReturnLineOutcome, VendorReturnStatus
    from app.services.vendor_return_service import VendorReturnService

    ven = _vendor(db); prod = _product(db, qty_on_hand=5)
    svc = VendorReturnService(db, None)
    vr = _make_vr(db, ven, prod, qty=2, unit_credit=20.0)
    svc.ship_return(vr.id, tracking_number="1Z-VR-5")
    svc.record_vendor_decision(
        vr.id,
        line_outcomes=[{"line_id": vr.lines[0].id,
                        "outcome": VendorReturnLineOutcome.ACCEPTED.value,
                        "actual_unit_credit": 20.0}],
        auto_create_vcm=False)
    svc.close_vendor_return(vr.id)
    db.refresh(vr)
    assert vr.status == VendorReturnStatus.CLOSED


# ===========================================================================
# Edit (DRAFT only) — attach product, change lines, recompute credit
# ===========================================================================

def test_update_draft_replaces_lines_and_recomputes_credit(db):
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); p1 = _product(db); p2 = _product(db)
    vr = _make_vr(db, ven, p1, qty=1, unit_credit=10.0)
    assert vr.expected_credit == 10.0

    VendorReturnService(db, None).update_vendor_return(
        vr.id,
        reason="Updated reason",
        lines=[
            {"product_id": p1.id, "description": "Part 1", "qty": 2, "expected_unit_credit": 20.0},
            {"product_id": p2.id, "description": "Part 2", "qty": 1, "expected_unit_credit": 5.0},
        ],
    )
    db.refresh(vr)
    assert vr.reason == "Updated reason"
    assert len(vr.lines) == 2
    assert vr.expected_credit == 45.0  # 2*20 + 1*5
    assert {ln.product_id for ln in vr.lines} == {p1.id, p2.id}


def test_update_rejected_after_ship(db):
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod)
    VendorReturnService(db, None).ship_return(vr.id, tracking_number="1Z-VR-EDIT")
    with pytest.raises(ValueError):
        VendorReturnService(db, None).update_vendor_return(vr.id, reason="too late")
    db.rollback()


def test_update_stores_restocking_fee(db):
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod, qty=2, unit_credit=20.0)
    VendorReturnService(db, None).update_vendor_return(vr.id, restocking_fee=7.5)
    db.refresh(vr)
    assert vr.restocking_fee == 7.5
    # Net expected (computed in the router) = 40 - 7.5 = 32.5
    assert round(vr.expected_credit - vr.restocking_fee, 2) == 32.5


# ===========================================================================
# Void / cancel — DRAFT no-op, SHIPPED restocks exactly what shipped
# ===========================================================================

def test_void_draft_no_inventory_effect(db):
    from app.constants import VendorReturnStatus
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); prod = _product(db, qty_on_hand=10)
    vr = _make_vr(db, ven, prod, qty=3)
    VendorReturnService(db, None).void_vendor_return(vr.id, reason="mistake")
    db.refresh(vr); db.refresh(prod)
    assert vr.status == VendorReturnStatus.VOIDED
    assert prod.qty_on_hand == 10  # untouched — never shipped


def test_void_shipped_restocks_inventory(db):
    from app.constants import VendorReturnStatus
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); prod = _product(db, qty_on_hand=10)
    svc = VendorReturnService(db, None)
    vr = _make_vr(db, ven, prod, qty=3)
    svc.ship_return(vr.id, tracking_number="1Z-VOID", decrement_inventory=True)
    db.refresh(prod)
    assert prod.qty_on_hand == 7  # shipped out

    svc.void_vendor_return(vr.id, reason="vendor lost it, keeping parts")
    db.refresh(vr); db.refresh(prod)
    assert vr.status == VendorReturnStatus.VOIDED
    assert prod.qty_on_hand == 10  # restored exactly what shipped


def test_void_shipped_without_decrement_restores_nothing(db):
    """A ship that skipped the decrement must not phantom-add stock on void."""
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); prod = _product(db, qty_on_hand=10)
    svc = VendorReturnService(db, None)
    vr = _make_vr(db, ven, prod, qty=3)
    svc.ship_return(vr.id, tracking_number="1Z-NODEC", decrement_inventory=False)
    db.refresh(prod)
    assert prod.qty_on_hand == 10
    svc.void_vendor_return(vr.id)
    db.refresh(prod)
    assert prod.qty_on_hand == 10  # nothing was decremented → nothing restored


def test_void_rejected_from_closed(db):
    from app.constants import VendorReturnLineOutcome
    from app.services.vendor_return_service import VendorReturnService
    ven = _vendor(db); prod = _product(db)
    svc = VendorReturnService(db, None)
    vr = _make_vr(db, ven, prod, qty=1, unit_credit=10.0)
    svc.ship_return(vr.id, tracking_number="1Z-CL")
    svc.record_vendor_decision(
        vr.id,
        line_outcomes=[{"line_id": vr.lines[0].id,
                        "outcome": VendorReturnLineOutcome.ACCEPTED.value,
                        "actual_unit_credit": 10.0}],
        auto_create_vcm=False)
    svc.close_vendor_return(vr.id)
    with pytest.raises(ValueError):
        svc.void_vendor_return(vr.id)
    db.rollback()


# ===========================================================================
# Route layer — product picker attaches product, edit + void + PO seed
# ===========================================================================

def test_create_route_attaches_product(client, db):
    """The array-style picker POST must persist line_product_id[] onto the line —
    the exact gap the owner reported (couldn't attach a product)."""
    from app.models.vendor_return import VendorReturn, VendorReturnLine
    ven = _vendor(db); prod = _product(db)
    resp = client.post("/vendor-returns/new", data={
        "vendor_id": str(ven.id),
        "reason": "Wrong part shipped",
        "restocking_fee": "5.00",
        "line_product_id[]": [str(prod.id)],
        "line_description[]": ["VR Part"],
        "line_qty[]": ["2"],
        "line_unit_credit[]": ["20.00"],
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    vr = db.query(VendorReturn).filter(VendorReturn.vendor_id == ven.id).order_by(VendorReturn.id.desc()).first()
    assert vr is not None
    line = db.query(VendorReturnLine).filter(VendorReturnLine.vendor_return_id == vr.id).first()
    assert line.product_id == prod.id  # <-- product actually attached
    assert vr.restocking_fee == 5.0
    assert vr.expected_credit == 40.0


def test_edit_route_updates_draft(client, db):
    from app.models.vendor_return import VendorReturn
    ven = _vendor(db); p1 = _product(db); p2 = _product(db)
    vr = _make_vr(db, ven, p1, qty=1, unit_credit=10.0)
    resp = client.post(f"/vendor-returns/{vr.id}/edit", data={
        "vendor_id": str(ven.id),
        "reason": "Corrected reason",
        "line_product_id[]": [str(p2.id)],
        "line_description[]": ["Swapped part"],
        "line_qty[]": ["3"],
        "line_unit_credit[]": ["15.00"],
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    db.expire_all()
    fresh = db.query(VendorReturn).filter(VendorReturn.id == vr.id).first()
    assert fresh.reason == "Corrected reason"
    assert len(fresh.lines) == 1
    assert fresh.lines[0].product_id == p2.id
    assert fresh.expected_credit == 45.0  # 3 * 15


def test_void_route(client, db):
    from app.constants import VendorReturnStatus
    from app.models.vendor_return import VendorReturn
    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod)
    resp = client.post(f"/vendor-returns/{vr.id}/void", data={"reason": "duplicate"},
                       follow_redirects=False)
    assert resp.status_code == 303
    db.expire_all()
    assert db.query(VendorReturn).filter(VendorReturn.id == vr.id).first().status == VendorReturnStatus.VOIDED


def test_new_route_seeds_from_po(client, db):
    """The 'Return to Vendor' entry point pre-fills the vendor + PO lines."""
    from app.models.purchase_order import PurchaseOrder, POLine
    ven = _vendor(db); prod = _product(db)
    po = PurchaseOrder(po_number=f"PO-SEED-{_uniq()}", vendor_id=ven.id, status="draft")
    db.add(po); db.flush()
    db.add(POLine(po_id=po.id, product_id=prod.id, description="Seeded part",
                  qty_ordered=4, qty_received=4, unit_cost=12.5))
    db.commit()

    resp = client.get(f"/vendor-returns/new?po_id={po.id}")
    assert resp.status_code == 200
    # The seed JSON carries the product's SKU and the PO link is shown.
    assert prod.sku in resp.text
    assert f"PO #{po.id}" in resp.text


def test_edit_route_blocked_after_ship(client, db):
    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod)
    from app.services.vendor_return_service import VendorReturnService
    VendorReturnService(db, None).ship_return(vr.id, tracking_number="1Z-BLK")
    resp = client.get(f"/vendor-returns/{vr.id}/edit", follow_redirects=False)
    # Non-draft edit GET redirects back to the workspace with an error.
    assert resp.status_code == 303
    assert "/vendor-returns/" in resp.headers.get("location", "")


def test_print_route_renders(client, db):
    ven = _vendor(db); prod = _product(db)
    vr = _make_vr(db, ven, prod)
    resp = client.get(f"/vendor-returns/{vr.id}/print")
    assert resp.status_code == 200
    assert vr.vr_number in resp.text
    assert "Vendor Return" in resp.text
