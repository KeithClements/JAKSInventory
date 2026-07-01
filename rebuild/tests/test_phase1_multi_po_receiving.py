"""
tests/test_phase1_multi_po_receiving.py
=======================================
MASTER_PLAN §23.3 Phase 1 — multi-PO single-shipment receiving (backend half).

The data model already supports it: ``POReceipt`` carries a ``vendor_id`` (not a
po_id) and ``POReceiptLine`` carries ``po_id`` per line — "one receipt may span
multiple POs from the same vendor." These pin the SERVICE contract behind the
"receive a whole truck in one action" goal:

  * ``create_receipt`` re-evaluates the status of EVERY PO touched by the
    receipt independently — a multi-PO receipt can close one PO (RECEIVED) while
    leaving another PARTIAL. (Previously only the last PO seen in the loop was
    updated, so sibling POs kept a stale status.)
  * ``create_receipt`` rejects a line whose PO belongs to a different vendor than
    the receipt — a receipt spans a single vendor.
  * ``get_open_receivable_lines_for_vendor`` returns the open lines a vendor
    could still deliver, grouped by PO, dropping settled/cancelled lines and
    non-receivable POs — the data behind the Receive Shipment screen.
"""
from __future__ import annotations

import pytest

from app.constants import POStatus
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POReceipt
from app.models.vendor import Vendor
from app.services.po_service import POService
from tests.conftest import activate, fresh_engine

_UID = 1


@pytest.fixture()
def Session():
    return activate(fresh_engine())


def _vendor(db, code):
    v = Vendor(name=f"Vendor {code}", vendor_code=code, vendor_number=code)
    db.add(v); db.flush()
    return v


def _product(db, sku):
    p = Product(sku=sku, title=sku, qty_on_hand=0, qty_on_order=0)
    db.add(p); db.flush()
    return p


def _po_with_line(db, vendor, product, qty, cost=100.0, status=POStatus.SENT):
    svc = POService(db, current_user_id=_UID)
    po = svc.create_po(vendor_id=vendor.id, data={})
    line = svc.add_line(
        po_id=po.id, product_id=product.id,
        data={"description": product.title, "qty_ordered": qty, "unit_cost": cost},
    )
    po.status = status
    db.commit()
    return po, line


# ── create_receipt: independent per-PO status across a multi-PO receipt ───────

def test_multi_po_receipt_updates_each_po_status_independently(Session):
    db = Session()
    try:
        v = _vendor(db, "PAI")
        pa, pb = _product(db, "SKU-A"), _product(db, "SKU-B")
        po1, la = _po_with_line(db, v, pa, qty=3)
        po2, lb = _po_with_line(db, v, pb, qty=5)

        receipt = POService(db, current_user_id=_UID).create_receipt(
            vendor_id=v.id,
            po_line_quantities={la.id: 3, lb.id: 2},   # PO1 fully, PO2 partially
            data={},
        )

        db.refresh(po1); db.refresh(po2); db.refresh(pa); db.refresh(pb)
        assert po1.status == POStatus.RECEIVED     # every PO1 line settled
        assert po2.status == POStatus.PARTIAL      # PO2 still owes 3
        assert pa.qty_on_hand == 3
        assert pb.qty_on_hand == 2
        # ONE receipt, ONE vendor, spanning BOTH POs
        assert receipt.vendor_id == v.id
        assert {rl.po_id for rl in receipt.lines} == {po1.id, po2.id}
    finally:
        db.close()


def test_multi_po_receipt_can_close_both_pos(Session):
    db = Session()
    try:
        v = _vendor(db, "PAI")
        pa, pb = _product(db, "SKU-A"), _product(db, "SKU-B")
        po1, la = _po_with_line(db, v, pa, qty=3)
        po2, lb = _po_with_line(db, v, pb, qty=5)

        POService(db, current_user_id=_UID).create_receipt(
            vendor_id=v.id, po_line_quantities={la.id: 3, lb.id: 5}, data={},
        )

        db.refresh(po1); db.refresh(po2)
        assert po1.status == POStatus.RECEIVED
        assert po2.status == POStatus.RECEIVED
    finally:
        db.close()


def test_receipt_rejects_a_line_from_another_vendor(Session):
    db = Session()
    try:
        v1, v2 = _vendor(db, "PAI"), _vendor(db, "IMB")
        pa, pc = _product(db, "SKU-A"), _product(db, "SKU-C")
        po1, la = _po_with_line(db, v1, pa, qty=3)
        po2, lc = _po_with_line(db, v2, pc, qty=4)     # a DIFFERENT vendor's PO

        svc = POService(db, current_user_id=_UID)
        with pytest.raises(ValueError):
            svc.create_receipt(
                vendor_id=v1.id,
                po_line_quantities={la.id: 1, lc.id: 1},   # cross-vendor line → reject
                data={},
            )
    finally:
        db.close()


# ── get_open_receivable_lines_for_vendor: grouping + filtering ────────────────

def test_open_receivable_lines_grouped_and_filtered(Session):
    db = Session()
    try:
        v = _vendor(db, "PAI")
        pa, pb = _product(db, "SKU-A"), _product(db, "SKU-B")
        pc, pd = _product(db, "SKU-C"), _product(db, "SKU-D")

        # PO1 SENT — one fully open line (included)
        po1, l1 = _po_with_line(db, v, pa, qty=4, status=POStatus.SENT)

        # PO2 PARTIAL — l2 settled via cancellation (excluded), l3 still open (included)
        svc = POService(db, current_user_id=_UID)
        po2 = svc.create_po(vendor_id=v.id, data={})
        l2 = svc.add_line(po_id=po2.id, product_id=pb.id,
                          data={"description": "B", "qty_ordered": 5, "unit_cost": 10.0})
        l3 = svc.add_line(po_id=po2.id, product_id=pc.id,
                          data={"description": "C", "qty_ordered": 2, "unit_cost": 10.0})
        l2.qty_received = 2
        l2.qty_cancelled = 3          # 2 + 3 >= 5 → settled → dropped
        po2.status = POStatus.PARTIAL
        db.commit()

        # PO3 RECEIVED — not a receivable status → excluded entirely
        po3, l4 = _po_with_line(db, v, pd, qty=1, status=POStatus.RECEIVED)

        rows = POService(db, current_user_id=_UID).get_open_receivable_lines_for_vendor(v.id)
        by_po = {r["po"].id: r for r in rows}

        assert set(by_po) == {po1.id, po2.id}          # PO3 (RECEIVED) excluded
        assert [ln.id for ln in by_po[po1.id]["open_lines"]] == [l1.id]
        assert [ln.id for ln in by_po[po2.id]["open_lines"]] == [l3.id]  # l2 dropped
    finally:
        db.close()


def test_open_receivable_lines_empty_for_vendor_with_no_open_pos(Session):
    db = Session()
    try:
        v = _vendor(db, "PAI")
        pa = _product(db, "SKU-A")
        _po_with_line(db, v, pa, qty=1, status=POStatus.RECEIVED)   # nothing open
        rows = POService(db, current_user_id=_UID).get_open_receivable_lines_for_vendor(v.id)
        assert rows == []
    finally:
        db.close()
