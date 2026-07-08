"""
tests/test_match_correct.py
===========================
Service tests for "Correct & Reconcile" — POService.correct_match_line.

The 3-way match flags a line as over_billed or cost_variance. resolve_match_line
only records a *decision* (accept/reject/hold/clear) and leaves the divergent
numbers in place. correct_match_line instead EDITS the real numbers on the PO
and/or the bill so the line genuinely reconciles, records resolution=CORRECTED
with a mandatory reason, recomputes the bill total, and opens the approve gate
(DISCREPANCY -> PENDING). It REFUSES to write anything if the edit does not
reconcile, and it does NOT re-cost already-received inventory (records-only).

Owner decisions (2026-06-06 interview):
  - support all three: update PO to match bill, correct the bill entry, fix qty
  - inventory: records-only (no re-cost of received stock)
  - must-match: correcting forces variance=0 before approval unlocks
  - sits alongside Accept/Reject/Hold/Clear/Create-Credit as a new action
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401

import pytest

from app.constants import MatchResolution, POStatus, UserRole, VendorBillStatus
from app.models.product import Product
from app.models.purchase_order import (
    PurchaseOrder, POLine, VendorBill, VendorBillLine,
)
from app.models.user import User
from app.models.vendor import Vendor
from app.services.base import PermissionDeniedError
from app.services.po_service import POService

_counter = itertools.count(1)


@pytest.fixture()
def db():
    activate(fresh_engine())
    session = _appdb.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _admin(db) -> User:
    n = next(_counter)
    u = User(name=f"Admin-{n}", username=f"admin_{n}", password_hash="x", role=UserRole.ADMIN)
    db.add(u); db.flush()
    return u


def _sales(db) -> User:
    n = next(_counter)
    u = User(name=f"Sales-{n}", username=f"sales_{n}", password_hash="x", role=UserRole.SALES)
    db.add(u); db.flush()
    return u


def _setup(
    db,
    *,
    qty_ordered: int = 5,
    qty_received: int = 5,
    qty_billed: int = 5,
    po_cost: float = 100.0,
    billed_cost: float = 100.0,
    with_product_cost: float | None = None,
):
    """PO (received) + one line + a DISCREPANCY vendor bill with one bill line."""
    n = next(_counter)
    vendor = Vendor(name=f"Vendor-{n}", is_active=True)
    db.add(vendor); db.flush()

    product = None
    if with_product_cost is not None:
        product = Product(sku=f"CORR-{n}", title="Widget", cost=with_product_cost)
        db.add(product); db.flush()

    po = PurchaseOrder(po_number=f"PO-CORR-{n:04d}", vendor_id=vendor.id, status=POStatus.RECEIVED)
    db.add(po); db.flush()

    line = POLine(
        po_id=po.id,
        product_id=product.id if product else None,
        qty_ordered=qty_ordered, qty_received=qty_received, qty_billed=0,
        unit_cost=po_cost,
    )
    db.add(line); db.flush()

    bill = VendorBill(
        po_id=po.id, vendor_id=vendor.id, bill_number=f"BILL-CORR-{n:04d}",
        total_amount=round(qty_billed * billed_cost, 2),
        status=VendorBillStatus.DISCREPANCY,
    )
    db.add(bill); db.flush()

    bl = VendorBillLine(bill_id=bill.id, po_line_id=line.id, qty_billed=qty_billed, unit_cost=billed_cost)
    db.add(bl)
    line.qty_billed = qty_billed
    db.commit(); db.expire_all()

    return po, line, bill, product


# ══════════════════════════════════════════════════════════════════════════════
# Update PO to match bill (vendor's price rose; bill is right)
# ══════════════════════════════════════════════════════════════════════════════

class TestCorrectUpdatePO:

    def test_raise_po_cost_reconciles(self, db):
        _, line, bill, _ = _setup(db, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_admin(db).id)
        svc.correct_match_line(
            line.id, bill.id, new_po_unit_cost=110.0,
            reason="Vendor raised price; PO updated to invoice",
        )
        db.expire_all()
        line2 = db.get(POLine, line.id)
        assert line2.unit_cost == pytest.approx(110.0)
        assert line2.match_resolution == MatchResolution.CORRECTED
        assert POService.compute_match_line(line2)["is_flag"] is False
        assert POService.compute_match_line(line2)["state"] == "resolved_corrected"

    def test_opens_approve_gate(self, db):
        _, line, bill, _ = _setup(db, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_admin(db).id)
        svc.correct_match_line(line.id, bill.id, new_po_unit_cost=110.0, reason="price up")
        db.expire_all()
        assert db.get(VendorBill, bill.id).status == VendorBillStatus.PENDING

    def test_bill_total_unchanged_when_only_po_edited(self, db):
        # 5 @ 110 billed = 550; raising the PO cost doesn't change what the vendor billed
        _, line, bill, _ = _setup(db, qty_billed=5, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_admin(db).id)
        svc.correct_match_line(line.id, bill.id, new_po_unit_cost=110.0, reason="price up")
        db.expire_all()
        assert db.get(VendorBill, bill.id).total_amount == pytest.approx(550.0)

    def test_records_attribution_and_reason(self, db):
        _, line, bill, _ = _setup(db, po_cost=100.0, billed_cost=110.0)
        admin = _admin(db)
        svc = POService(db, current_user_id=admin.id)
        svc.correct_match_line(line.id, bill.id, new_po_unit_cost=110.0, reason="vendor PA-2025 increase")
        db.expire_all()
        line2 = db.get(POLine, line.id)
        assert line2.match_resolved_by_id == admin.id
        assert line2.match_resolved_at is not None
        assert line2.match_resolution_reason == "vendor PA-2025 increase"


# ══════════════════════════════════════════════════════════════════════════════
# Correct the bill entry (mis-keyed; PO is right)
# ══════════════════════════════════════════════════════════════════════════════

class TestCorrectBillEntry:

    def test_fix_billed_cost_reconciles_and_lowers_total(self, db):
        # Bill was keyed at 110 but the paper invoice really says 100 (== PO).
        _, line, bill, _ = _setup(db, qty_billed=5, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_admin(db).id)
        svc.correct_match_line(
            line.id, bill.id, new_billed_unit_cost=100.0,
            reason="Keyed 110, invoice says 100",
        )
        db.expire_all()
        line2 = db.get(POLine, line.id)
        bill2 = db.get(VendorBill, bill.id)
        assert line2.unit_cost == pytest.approx(100.0)       # PO untouched
        assert bill2.total_amount == pytest.approx(500.0)    # 5 @ 100 — what we now owe
        assert bill2.status == VendorBillStatus.PENDING
        assert line2.match_resolution == MatchResolution.CORRECTED


# ══════════════════════════════════════════════════════════════════════════════
# Fix a qty over-bill
# ══════════════════════════════════════════════════════════════════════════════

class TestCorrectQtyOverBill:

    def test_reduce_billed_qty_reconciles(self, db):
        # received 5, billed 6 -> over by 1; correct the billed qty down to 5
        _, line, bill, _ = _setup(
            db, qty_ordered=5, qty_received=5, qty_billed=6, po_cost=100.0, billed_cost=100.0
        )
        svc = POService(db, current_user_id=_admin(db).id)
        svc.correct_match_line(
            line.id, bill.id, new_billed_qty=5,
            reason="Vendor billed 6, only 5 shipped/received",
        )
        db.expire_all()
        line2 = db.get(POLine, line.id)
        bill2 = db.get(VendorBill, bill.id)
        assert line2.qty_billed == 5                          # cumulative counter decremented
        assert bill2.total_amount == pytest.approx(500.0)     # 5 @ 100
        assert bill2.status == VendorBillStatus.PENDING
        assert POService.compute_match_line(line2)["is_flag"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Must-match gate — correcting forces variance to 0, or writes nothing
# ══════════════════════════════════════════════════════════════════════════════

class TestMustMatchGate:

    def test_non_reconciling_cost_raises(self, db):
        _, line, bill, _ = _setup(db, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_admin(db).id)
        with pytest.raises(ValueError, match="does not reconcile"):
            # PO 105 vs bill 110 -> still $5 apart
            svc.correct_match_line(line.id, bill.id, new_po_unit_cost=105.0, reason="halfway")

    def test_non_reconciling_writes_nothing(self, db):
        _, line, bill, _ = _setup(db, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_admin(db).id)
        with pytest.raises(ValueError):
            svc.correct_match_line(line.id, bill.id, new_po_unit_cost=105.0, reason="halfway")
        db.rollback(); db.expire_all()
        line2 = db.get(POLine, line.id)
        bill2 = db.get(VendorBill, bill.id)
        assert line2.unit_cost == pytest.approx(100.0)                 # unchanged
        assert line2.match_resolution == MatchResolution.UNRESOLVED    # not marked corrected
        assert bill2.status == VendorBillStatus.DISCREPANCY            # gate stayed shut
        assert bill2.total_amount == pytest.approx(550.0)             # total untouched

    def test_qty_still_over_received_raises(self, db):
        _, line, bill, _ = _setup(
            db, qty_ordered=5, qty_received=5, qty_billed=6, po_cost=100.0, billed_cost=100.0
        )
        svc = POService(db, current_user_id=_admin(db).id)
        with pytest.raises(ValueError, match="exceeds received"):
            svc.correct_match_line(line.id, bill.id, new_billed_qty=7, reason="nope")


# ══════════════════════════════════════════════════════════════════════════════
# Guards — mandatory reason + permission
# ══════════════════════════════════════════════════════════════════════════════

class TestGuards:

    def test_reason_required(self, db):
        _, line, bill, _ = _setup(db, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_admin(db).id)
        with pytest.raises(ValueError, match="reason is required"):
            svc.correct_match_line(line.id, bill.id, new_po_unit_cost=110.0, reason="  ")

    def test_sales_user_cannot_correct(self, db):
        _, line, bill, _ = _setup(db, po_cost=100.0, billed_cost=110.0)
        svc = POService(db, current_user_id=_sales(db).id)
        with pytest.raises(PermissionDeniedError):
            svc.correct_match_line(line.id, bill.id, new_po_unit_cost=110.0, reason="x")


# ══════════════════════════════════════════════════════════════════════════════
# Records-only — correcting the PO cost must NOT re-cost received inventory
# ══════════════════════════════════════════════════════════════════════════════

class TestRecordsOnly:

    def test_po_cost_correction_does_not_recost_inventory(self, db):
        # product moving-avg cost booked at 100 at receipt; raising the PO cost to
        # 110 must NOT change product.cost (records-only per owner decision).
        _, line, bill, product = _setup(
            db, po_cost=100.0, billed_cost=110.0, with_product_cost=100.0
        )
        svc = POService(db, current_user_id=_admin(db).id)
        svc.correct_match_line(
            line.id, bill.id, new_po_unit_cost=110.0,
            reason="price up; do not re-cost stock",
        )
        db.expire_all()
        assert db.get(Product, product.id).cost == pytest.approx(100.0)   # inventory untouched
        assert db.get(POLine, line.id).unit_cost == pytest.approx(110.0)  # PO record fixed
