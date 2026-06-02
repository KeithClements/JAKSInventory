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


# ── D-4b: billing the over-received qty exceeds the PO ceiling ───────────────
#
# Bug: create_vendor_bill checks `qty_billed > qty_received` but NOT
# `qty_billed > qty_ordered`.  When a vendor over-delivers (recv=7, ordered=5)
# and then bills for every unit received (bill=7), the check
#   7 > 7  → False
# passes with APPROVED.  The vendor billed for 2 units that were never ordered —
# that is a discrepancy that AP must review.
#
# Two layers need the same fix:
#   • create_vendor_bill (po_service.py) — sets bill.status
#   • VendorBillLine.has_discrepancy (models/purchase_order.py) — model property
#
# xfail(strict=True): flip these two marks to plain `assert` in the same PR that
# adds `qty_billed > open_qty` to both locations.

def _po_with_over_received_line(db, *, ordered=5, received=7, unit_cost=10.0):
    """PO line where more was received than ordered (simulate R6 over-receipt)."""
    from app.models.vendor import Vendor
    from app.models.product import Product
    from app.models.purchase_order import PurchaseOrder, POLine
    vendor = Vendor(name="Over-Recv Vendor")
    prod   = Product(sku="OVRRECV-1", title="Widget", cost=unit_cost,
                     qty_on_hand=0, is_active=True)
    db.add_all([vendor, prod]); db.commit(); db.refresh(vendor); db.refresh(prod)
    po   = PurchaseOrder(po_number="PO-OVRRECV-1", vendor_id=vendor.id,
                         status=POStatus.RECEIVED)
    db.add(po); db.commit(); db.refresh(po)
    line = POLine(po_id=po.id, product_id=prod.id,
                  qty_ordered=ordered, qty_received=received, qty_billed=0,
                  unit_cost=unit_cost,
                  over_received=True, over_received_qty=received - ordered)
    db.add(line); db.commit(); db.refresh(line)
    return vendor, po, line


@pytest.mark.xfail(
    strict=True,
    reason=(
        "D-4b: create_vendor_bill and VendorBillLine.has_discrepancy both use "
        "`qty_billed > qty_received` as the ceiling, ignoring qty_ordered. "
        "When over-receipt happens (recv=7, ordered=5) and the vendor bills all "
        "received units (bill=7), `7 > 7` is False → APPROVED. "
        "Fix: also flag when qty_billed > open_qty (= qty_ordered - qty_cancelled). "
        "Flip these marks to plain asserts in the same PR that lands the fix."
    ),
)
def test_over_po_qty_bill_flags_discrepancy(db):
    """
    PO ordered 5 → received 7 (over-receipt allowed) → vendor bills 7 @ correct cost.

    Current (buggy) behaviour:  bill.status == APPROVED   ← xfail
    Correct behaviour:          bill.status == DISCREPANCY

    The vendor billed for two units that were never on the purchase order.
    Even though qty_billed == qty_received, the bill exceeds the PO ceiling.
    Both the service (status) and the model property (has_discrepancy) must flag it.
    """
    vendor, po, line = _po_with_over_received_line(db, ordered=5, received=7, unit_cost=10.0)
    bill = _bill(db, vendor, po, line, billed_cost=10.0, qty_billed=7)

    # Service must set status = DISCREPANCY
    assert bill.status == VendorBillStatus.DISCREPANCY, (
        f"Billing 7 units when only 5 were ordered must be DISCREPANCY; "
        f"got {bill.status!r} — fix create_vendor_bill qty ceiling check."
    )
    # Model property must agree (both layers checked)
    assert bill.has_discrepancy is True, (
        "VendorBill.has_discrepancy must be True for a PO-ceiling-exceeding bill; "
        "fix VendorBillLine.has_discrepancy to also check qty_billed > qty_ordered."
    )
    assert bill.lines[0].has_discrepancy is True


def test_over_receipt_billed_within_order_qty_approves(db):
    """
    Guard: ordered=5, received=7 (over-receipt), billed=5.
    Billing within the ordered qty must remain APPROVED even when more
    was received — the over-receipt warning is separate from over-billing.
    """
    vendor, po, line = _po_with_over_received_line(db, ordered=5, received=7, unit_cost=10.0)
    bill = _bill(db, vendor, po, line, billed_cost=10.0, qty_billed=5)
    assert bill.status == VendorBillStatus.APPROVED, (
        f"Billing within the ordered qty (bill=5, ordered=5, recv=7) must be "
        f"APPROVED; got {bill.status!r}."
    )
    assert bill.has_discrepancy is False


def test_clean_match_still_approves(db):
    """Guard: ordered=5, received=5, billed=5 — the normal path stays APPROVED."""
    vendor, po, line = _po_with_received_line(db, ordered_cost=10.0, qty=5)
    bill = _bill(db, vendor, po, line, billed_cost=10.0, qty_billed=5)
    assert bill.status == VendorBillStatus.APPROVED
    assert bill.has_discrepancy is False


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
