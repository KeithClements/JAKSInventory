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
