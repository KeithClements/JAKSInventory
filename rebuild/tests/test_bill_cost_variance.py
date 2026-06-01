"""
tests/test_bill_cost_variance.py
================================
Money bug — 3-way-match cost variance must flag a vendor bill as DISCREPANCY and
block approval, not only an over-billed quantity.

Before the fix, create_vendor_bill set DISCREPANCY only when qty_billed >
qty_received; a vendor that billed the right quantity at an inflated unit cost
sailed through as APPROVED. Now a cost difference beyond COST_VARIANCE_TOLERANCE
(1 cent) is a discrepancy too, and approve_bill blocks it like a qty discrepancy.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.constants import POStatus, VendorBillStatus
from app.models.purchase_order import COST_VARIANCE_TOLERANCE
from app.services.po_service import POService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _po_with_received_line(db, *, ordered_cost=10.0, qty=5):
    """Create a vendor + PO with one line ordered & fully received at ordered_cost."""
    from app.models.vendor import Vendor
    from app.models.product import Product
    from app.models.purchase_order import PurchaseOrder, POLine

    vendor = Vendor(name="Cost Vendor")
    prod = Product(sku="COSTVAR-1", title="Widget", cost=ordered_cost,
                   qty_on_hand=0, is_active=True)
    db.add_all([vendor, prod]); db.commit(); db.refresh(vendor); db.refresh(prod)

    po = PurchaseOrder(po_number="PO-COST-1", vendor_id=vendor.id,
                       status=POStatus.RECEIVED)
    db.add(po); db.commit(); db.refresh(po)
    line = POLine(po_id=po.id, product_id=prod.id, qty_ordered=qty,
                  qty_received=qty, qty_billed=0, unit_cost=ordered_cost)
    db.add(line); db.commit(); db.refresh(line)
    return vendor, po, line


def _bill(db, vendor, po, line, *, billed_cost, qty_billed):
    return POService(db, current_user_id=None).create_vendor_bill(
        po_id=po.id,
        vendor_id=vendor.id,
        bill_number=f"BILL-{billed_cost}-{qty_billed}",
        bill_date=None,
        due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": qty_billed, "unit_cost": billed_cost}],
    )


# ── cost variance flags discrepancy ───────────────────────────────────────────

def test_matching_cost_auto_approves(db):
    # Billed at the exact ordered cost + correct qty → no discrepancy → APPROVED.
    vendor, po, line = _po_with_received_line(db, ordered_cost=10.0, qty=5)
    bill = _bill(db, vendor, po, line, billed_cost=10.0, qty_billed=5)
    assert bill.status == VendorBillStatus.APPROVED
    assert bill.has_discrepancy is False


def test_cost_overcharge_flags_discrepancy(db):
    # Correct qty, but vendor billed $12 on a $10 line → DISCREPANCY (not approved).
    vendor, po, line = _po_with_received_line(db, ordered_cost=10.0, qty=5)
    bill = _bill(db, vendor, po, line, billed_cost=12.0, qty_billed=5)
    assert bill.status == VendorBillStatus.DISCREPANCY
    assert bill.has_discrepancy is True
    assert bill.lines[0].has_discrepancy is True


def test_cost_undercharge_also_flags(db):
    # A vendor UNDERcharge is still a mismatch worth AP review.
    vendor, po, line = _po_with_received_line(db, ordered_cost=10.0, qty=5)
    bill = _bill(db, vendor, po, line, billed_cost=8.0, qty_billed=5)
    assert bill.status == VendorBillStatus.DISCREPANCY


def test_subcent_difference_within_tolerance_approves(db):
    # A difference smaller than tolerance (e.g. rounding) must NOT flag.
    vendor, po, line = _po_with_received_line(db, ordered_cost=10.0, qty=5)
    tiny = COST_VARIANCE_TOLERANCE / 2
    bill = _bill(db, vendor, po, line, billed_cost=10.0 + tiny, qty_billed=5)
    assert bill.status == VendorBillStatus.APPROVED


# ── approve_bill blocks the cost discrepancy ──────────────────────────────────

def test_approve_blocks_cost_overcharge(db):
    vendor, po, line = _po_with_received_line(db, ordered_cost=10.0, qty=5)
    bill = _bill(db, vendor, po, line, billed_cost=12.0, qty_billed=5)
    svc = POService(db, current_user_id=None)
    # Must NOT auto-approve a cost overcharge.
    with pytest.raises(ValueError, match="unresolved match discrepanc"):
        svc.approve_bill(bill.id)
    db.refresh(bill)
    assert bill.status == VendorBillStatus.DISCREPANCY  # still blocked


def test_match_line_reports_cost_variance_state(db):
    vendor, po, line = _po_with_received_line(db, ordered_cost=10.0, qty=5)
    _bill(db, vendor, po, line, billed_cost=12.0, qty_billed=5)
    db.refresh(line)
    row = POService.compute_match_line(line)
    assert row["state"] == "cost_variance"
    assert row["is_flag"] is True
    assert row["cost_var"] == 2.0
    # suggested credit = overcharge × billed qty = 2.00 × 5
    assert row["suggested_credit"] == 10.0
