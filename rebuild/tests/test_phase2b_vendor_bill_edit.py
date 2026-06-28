"""
Phase 2 — edit a posted vendor bill
===================================
`POService.edit_vendor_bill` corrects a bill after creation (header + line
qty/cost), re-validates the 3-way match, re-derives the PO billed status, and
re-flags an already-synced bill for QBO. Covered:

  - Editing a clean APPROVED bill's cost to differ from the PO → DISCREPANCY.
  - Editing a DISCREPANCY bill's cost back to the PO cost → clears to PENDING.
  - Header bill_number edit persists; a duplicate vendor invoice # is rejected.
  - Reducing billed qty rolls the PO back off BILLED.
  - An already-QBO-synced bill is re-flagged PENDING on edit.
  - PAID bills are not editable (use a vendor credit); reason is required.
"""
from __future__ import annotations

import datetime as dt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import fresh_engine, activate
from app.models.vendor import Vendor
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine, VendorBill
from app.models.audit import AuditLog
from app.services.po_service import POService
from app.constants import (
    POStatus, VendorBillStatus, QBOSyncStatus, EntityType, AuditAction,
)

_UID = 1


@pytest.fixture()
def Session():
    return activate(fresh_engine())


@pytest.fixture()
def client():
    return TestClient(app)


def _billed_po(db, *, qty=2, cost=100.0, bill_qty=None, bill_cost=None):
    """Seed a PO → send → receive `qty` → create a vendor bill. Returns
    (po_id, po_line_id, bill_id, bill_line_id)."""
    bill_qty = qty if bill_qty is None else bill_qty
    bill_cost = cost if bill_cost is None else bill_cost
    vendor = Vendor(name="PAI", vendor_code="PAI", vendor_number="9")
    db.add(vendor); db.flush()
    product = Product(sku="JAKS-GAS", title="GASKET")
    db.add(product); db.flush()
    svc = POService(db, _UID)
    po = svc.create_po(vendor_id=vendor.id, data={})
    line = svc.add_line(po_id=po.id, product_id=product.id,
                        data={"qty_ordered": qty, "unit_cost": cost})
    svc.send_to_vendor(po.id)
    svc.create_receipt(vendor_id=po.vendor_id, po_line_quantities={line.id: qty}, data={})
    bill = svc.create_vendor_bill(
        po_id=po.id, vendor_id=po.vendor_id, bill_number="INV-1",
        bill_date=dt.datetime(2026, 6, 27), due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": bill_qty, "unit_cost": bill_cost}],
    )
    db.flush()
    return po.id, line.id, bill.id, bill.lines[0].id


def test_edit_clean_bill_cost_creates_discrepancy(Session):
    db = Session()
    try:
        po_id, _, bill_id, bl_id = _billed_po(db, cost=100.0)
        assert db.get(VendorBill, bill_id).status == VendorBillStatus.APPROVED

        POService(db, _UID).edit_vendor_bill(
            bill_id, reason="keyed wrong cost",
            line_edits=[{"bill_line_id": bl_id, "unit_cost": 120.0}],
        )
        db.expire_all()
        bill = db.get(VendorBill, bill_id)
        assert bill.status == VendorBillStatus.DISCREPANCY
        assert bill.total_amount == pytest.approx(240.0)  # 2 × 120
    finally:
        db.close()


def test_edit_discrepancy_bill_back_to_match_clears_to_pending(Session):
    db = Session()
    try:
        # Bill cost (110) differs from PO cost (100) → created as DISCREPANCY.
        po_id, _, bill_id, bl_id = _billed_po(db, cost=100.0, bill_cost=110.0)
        assert db.get(VendorBill, bill_id).status == VendorBillStatus.DISCREPANCY

        POService(db, _UID).edit_vendor_bill(
            bill_id, reason="vendor honored quote",
            line_edits=[{"bill_line_id": bl_id, "unit_cost": 100.0}],
        )
        db.expire_all()
        # Cleared discrepancy opens the approve gate (PENDING), not auto-approve.
        assert db.get(VendorBill, bill_id).status == VendorBillStatus.PENDING
    finally:
        db.close()


def test_edit_header_bill_number_and_dup_guard(Session):
    db = Session()
    try:
        po_id, _, bill_id, _ = _billed_po(db)
        svc = POService(db, _UID)
        svc.edit_vendor_bill(bill_id, reason="typo in invoice #",
                             header={"bill_number": "INV-1A"})
        db.expire_all()
        assert db.get(VendorBill, bill_id).bill_number == "INV-1A"

        # A second bill for the same vendor; renaming it onto INV-1A must fail.
        vendor_id = db.get(VendorBill, bill_id).vendor_id
        other = VendorBill(vendor_id=vendor_id, bill_number="INV-2",
                           status=VendorBillStatus.PENDING)
        db.add(other); db.commit()
        with pytest.raises(ValueError, match="already exists"):
            svc.edit_vendor_bill(other.id, reason="x", header={"bill_number": "inv-1a"})
    finally:
        db.close()


def test_edit_reducing_billed_qty_rolls_po_back_off_billed(Session):
    db = Session()
    try:
        po_id, _, bill_id, bl_id = _billed_po(db, qty=4)
        # Fully received + fully billed → PO is BILLED.
        assert db.get(PurchaseOrder, po_id).status == POStatus.BILLED

        POService(db, _UID).edit_vendor_bill(
            bill_id, reason="only 3 actually invoiced",
            line_edits=[{"bill_line_id": bl_id, "qty_billed": 3}],
        )
        db.expire_all()
        # 3 of 4 received units billed → no longer fully billed → off BILLED.
        assert db.get(PurchaseOrder, po_id).status == POStatus.RECEIVED
    finally:
        db.close()


def test_edit_reflags_already_synced_bill_for_qbo(Session):
    db = Session()
    try:
        po_id, _, bill_id, bl_id = _billed_po(db)
        bill = db.get(VendorBill, bill_id)
        bill.qbo_id = "QBO-123"
        bill.qbo_sync_status = QBOSyncStatus.SYNCED
        db.commit()

        POService(db, _UID).edit_vendor_bill(
            bill_id, reason="correction after sync",
            line_edits=[{"bill_line_id": bl_id, "unit_cost": 100.0}],
        )
        db.expire_all()
        assert db.get(VendorBill, bill_id).qbo_sync_status == QBOSyncStatus.PENDING
    finally:
        db.close()


def test_edit_audits_old_to_new(Session):
    db = Session()
    try:
        po_id, _, bill_id, bl_id = _billed_po(db, cost=100.0)
        POService(db, _UID).edit_vendor_bill(
            bill_id, reason="cost fix",
            line_edits=[{"bill_line_id": bl_id, "unit_cost": 120.0}],
        )
        db.expire_all()
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_type == EntityType.PURCHASE_ORDER,
                AuditLog.entity_id == po_id,
                AuditLog.action == AuditAction.EDITED,
            )
            .all()
        )
        assert any("cost fix" in (r.notes or "") and str(bill_id) in (r.new_value or "")
                   for r in rows)
    finally:
        db.close()


def test_paid_bill_cannot_be_edited(Session):
    db = Session()
    try:
        po_id, _, bill_id, bl_id = _billed_po(db)  # clean → APPROVED
        svc = POService(db, _UID)
        svc.mark_bill_paid(bill_id)
        with pytest.raises(ValueError, match="already paid"):
            svc.edit_vendor_bill(bill_id, reason="too late",
                                 line_edits=[{"bill_line_id": bl_id, "unit_cost": 99.0}])
    finally:
        db.close()


def test_edit_requires_reason(Session):
    db = Session()
    try:
        _, _, bill_id, bl_id = _billed_po(db)
        with pytest.raises(ValueError, match="reason is required"):
            POService(db, _UID).edit_vendor_bill(
                bill_id, reason="  ",
                line_edits=[{"bill_line_id": bl_id, "unit_cost": 100.0}],
            )
    finally:
        db.close()


# ── Endpoint / UI integration ───────────────────────────────────────────────

def test_edit_endpoint_applies_header_and_line(Session, client):
    db = Session()
    try:
        po_id, _, bill_id, bl_id = _billed_po(db, qty=2, cost=100.0)
    finally:
        db.close()

    r = client.post(
        f"/purchase-orders/{po_id}/bills/{bill_id}/edit",
        data={"bill_number": "INV-7", "reason": "fix #/cost",
              f"cost_{bl_id}": "120.00", f"qty_{bl_id}": "2"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "ok=bill_edited" in r.headers["location"]

    db = Session()
    try:
        bill = db.get(VendorBill, bill_id)
        assert bill.bill_number == "INV-7"
        assert bill.total_amount == pytest.approx(240.0)
        assert bill.status == VendorBillStatus.DISCREPANCY  # cost now differs from PO
    finally:
        db.close()


def test_edit_button_renders_for_approved_bill(Session, client):
    db = Session()
    try:
        po_id, _, bill_id, _ = _billed_po(db)  # clean → APPROVED
    finally:
        db.close()
    html = client.get(f"/purchase-orders/{po_id}").text
    assert f"/purchase-orders/{po_id}/bills/{bill_id}/edit" in html, \
        "approved bill should expose the inline edit form"
