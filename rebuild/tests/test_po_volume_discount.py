"""
tests/test_po_volume_discount.py
================================
Vendor volume discount on a Purchase Order (e.g. PAI: 5% off every part when a
PO exceeds $5,000).

  - The rule lives on the vendor as a VendorProgram (threshold_amount +
    discount_percent). POService.eligible_volume_program picks the best active
    program the PO's list subtotal qualifies for.
  - apply_volume_discount LOWERS each line's unit_cost by the %, snapshotting the
    pre-discount price in POLine.list_unit_cost (reversible). Parts only — core
    charges and freight-in are untouched.
  - Lowering the real cost keeps the 3-way match clean: when the vendor bills the
    discounted price, the bill line matches po_line.unit_cost (no discrepancy).

Fixture pattern mirrors tests/test_po_core_return.py.
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


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _vendor_with_program(db, *, pct=5.0, threshold=5000.0, active=True,
                         with_program=True):
    from app.models.vendor import Vendor, VendorProgram
    from app.constants import VendorProgramType
    v = Vendor(name=f"VolDisc Vendor {_uniq()}", is_active=True)
    db.add(v); db.commit(); db.refresh(v)
    if with_program:
        db.add(VendorProgram(
            vendor_id=v.id,
            program_name=f"Volume discount {pct:g}% over ${threshold:g}",
            program_type=VendorProgramType.TIER_DISCOUNT,
            threshold_amount=threshold,
            discount_percent=pct,
            is_active=active,
        ))
        db.commit()
    db.refresh(v)
    return v


def _product(db, *, cost=100.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"VOLD-{_uniq():04d}", title="Diesel Part", description="Diesel Part",
                cost=cost, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _draft_po(db, vendor, lines, *, freight=0.0):
    """lines = list of (product, qty, unit_cost, core_per_unit)."""
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder, POLine
    po = PurchaseOrder(po_number=f"PO-VD-{_uniq()}", vendor_id=vendor.id,
                       status=POStatus.DRAFT, freight_in_cost=freight)
    db.add(po); db.commit(); db.refresh(po)
    for product, qty, unit_cost, core in lines:
        db.add(POLine(po_id=po.id, product_id=product.id, description=product.title,
                      qty_ordered=qty, unit_cost=unit_cost, core_charge_per_unit=core))
    db.commit(); db.refresh(po)
    return po


def _svc(db):
    from app.services.po_service import POService
    return POService(db, current_user_id=None)


# ===========================================================================
# Eligibility
# ===========================================================================

def test_eligible_program_when_over_threshold(db):
    from app.services.po_service import POService
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a, b = _product(db), _product(db)
    po = _draft_po(db, ven, [(a, 10, 400.0, 0.0), (b, 10, 200.0, 0.0)])  # $6,000
    prog = POService.eligible_volume_program(po)
    assert prog is not None
    assert prog.discount_percent == 5.0


def test_no_program_when_under_threshold(db):
    from app.services.po_service import POService
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 1, 400.0, 0.0)])  # $400 < $5,000
    assert POService.eligible_volume_program(po) is None


def test_inactive_program_not_eligible(db):
    from app.services.po_service import POService
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0, active=False)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 0.0)])  # $10,000 but program off
    assert POService.eligible_volume_program(po) is None


# ===========================================================================
# Apply / remove
# ===========================================================================

def test_apply_lowers_each_part_cost_5pct(db):
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a, b = _product(db), _product(db)
    po = _draft_po(db, ven, [(a, 10, 400.0, 0.0), (b, 10, 200.0, 0.0)])  # $6,000
    _svc(db).apply_volume_discount(po.id)
    db.refresh(po)
    by_product = {ln.product_id: ln for ln in po.lines}
    la, lb = by_product[a.id], by_product[b.id]
    assert la.unit_cost == 380.0 and la.list_unit_cost == 400.0
    assert lb.unit_cost == 190.0 and lb.list_unit_cost == 200.0
    assert po.volume_discount_pct == 5.0
    # subtotal reflects discounted costs: 3800 + 1900
    assert po.subtotal == 5700.0


def test_apply_leaves_core_and_freight_untouched(db):
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 25.0)], freight=300.0)  # $10,000 parts
    _svc(db).apply_volume_discount(po.id)
    db.refresh(po)
    ln = po.lines[0]
    assert ln.unit_cost == 95.0           # parts discounted
    assert ln.core_charge_per_unit == 25.0  # core untouched
    assert po.freight_in_cost == 300.0      # freight untouched
    assert po.core_total == 2500.0          # 100 * 25 unchanged


def test_remove_restores_exact_list_price(db):
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a, b = _product(db), _product(db)
    po = _draft_po(db, ven, [(a, 10, 400.0, 0.0), (b, 10, 200.0, 0.0)])
    svc = _svc(db)
    svc.apply_volume_discount(po.id)
    svc.remove_volume_discount(po.id)
    db.refresh(po)
    by_product = {ln.product_id: ln for ln in po.lines}
    assert by_product[a.id].unit_cost == 400.0
    assert by_product[b.id].unit_cost == 200.0
    assert all(ln.list_unit_cost is None for ln in po.lines)
    assert po.volume_discount_pct == 0.0
    assert po.subtotal == 6000.0


def test_reapply_sweeps_in_newly_added_line(db):
    from app.models.purchase_order import POLine
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 0.0)])  # $10,000
    svc = _svc(db)
    svc.apply_volume_discount(po.id)
    # A line added after the first apply is at full price...
    b = _product(db)
    db.add(POLine(po_id=po.id, product_id=b.id, description=b.title,
                  qty_ordered=10, unit_cost=50.0, core_charge_per_unit=0.0))
    db.commit()
    # ...re-applying discounts it too, while leaving the already-discounted line as-is.
    svc.apply_volume_discount(po.id)
    db.refresh(po)
    by_product = {ln.product_id: ln for ln in po.lines}
    assert by_product[a.id].unit_cost == 95.0   # unchanged by the second apply
    assert by_product[b.id].unit_cost == 47.5   # newly discounted
    assert by_product[b.id].list_unit_cost == 50.0


def test_apply_below_threshold_raises(db):
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 1, 100.0, 0.0)])  # $100
    with pytest.raises(ValueError):
        _svc(db).apply_volume_discount(po.id)


def test_apply_on_sent_po_raises(db):
    from app.constants import POStatus
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 0.0)])
    po.status = POStatus.SENT
    db.commit()
    with pytest.raises(ValueError):
        _svc(db).apply_volume_discount(po.id)


def test_pct_snapshot_survives_later_program_change(db):
    """The PO records the % it was discounted at; editing the vendor's rule later
    must not retro-change an already-applied PO."""
    from app.services.po_service import POService
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 0.0)])
    _svc(db).apply_volume_discount(po.id)
    db.refresh(po)
    assert po.volume_discount_pct == 5.0
    # Owner later bumps PAI's rule to 10%.
    prog = POService.eligible_volume_program(po)
    prog.discount_percent = 10.0
    db.commit(); db.refresh(po)
    # Already-applied PO is unchanged until re-applied.
    assert po.volume_discount_pct == 5.0
    assert po.lines[0].unit_cost == 95.0


def test_manual_cost_edit_clears_snapshot(db):
    """A hand-typed unit cost on a discounted line drops its list snapshot so a
    later Remove can't clobber the manual value."""
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 0.0)])
    svc = _svc(db)
    svc.apply_volume_discount(po.id)
    line_id = po.lines[0].id
    svc.update_line(line_id, {"unit_cost": 88.0})
    db.refresh(po)
    ln = po.lines[0]
    assert ln.unit_cost == 88.0
    assert ln.list_unit_cost is None
    # Remove leaves the manually-set cost alone.
    svc.remove_volume_discount(po.id)
    db.refresh(po)
    assert po.lines[0].unit_cost == 88.0


# ===========================================================================
# 3-way match stays clean (the whole reason we lower unit_cost)
# ===========================================================================

def test_discounted_bill_does_not_flag_but_list_bill_does(db):
    from app.models.purchase_order import VendorBill, VendorBillLine
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 0.0)])
    _svc(db).apply_volume_discount(po.id)
    db.refresh(po)
    pol = po.lines[0]
    pol.qty_received = 100
    db.commit()

    bill = VendorBill(po_id=po.id, vendor_id=ven.id, bill_number=f"INV-{_uniq()}")
    db.add(bill); db.commit(); db.refresh(bill)

    # Vendor bills the DISCOUNTED price → matches po_line.unit_cost → no flag.
    good = VendorBillLine(bill_id=bill.id, po_line_id=pol.id, qty_billed=100, unit_cost=95.0)
    db.add(good); db.flush()
    assert good.has_discrepancy is False

    # Vendor bills the LIST price → cost variance → flagged (proves the discount
    # MUST live in unit_cost, not as a cosmetic bottom-line credit).
    bad = VendorBillLine(bill_id=bill.id, po_line_id=pol.id, qty_billed=100, unit_cost=100.0)
    db.add(bad); db.flush()
    assert bad.has_discrepancy is True


# ===========================================================================
# Routes — apply / remove re-render the lines section
# ===========================================================================

def test_apply_and_remove_routes(client, db):
    ven = _vendor_with_program(db, pct=5.0, threshold=5000.0)
    a = _product(db)
    po = _draft_po(db, ven, [(a, 100, 100.0, 0.0)])

    # Workspace shows the qualify nudge before applying.
    page = client.get(f"/purchase-orders/{po.id}")
    assert page.status_code == 200
    assert "volume discount" in page.text.lower()

    r = client.post(f"/purchase-orders/{po.id}/apply-volume-discount")
    assert r.status_code == 200
    assert "applied to all parts" in r.text
    db.expire_all()
    db.refresh(po)
    assert po.volume_discount_pct == 5.0
    assert po.lines[0].unit_cost == 95.0

    r2 = client.post(f"/purchase-orders/{po.id}/remove-volume-discount")
    assert r2.status_code == 200
    db.expire_all()
    db.refresh(po)
    assert po.volume_discount_pct == 0.0
    assert po.lines[0].unit_cost == 100.0
