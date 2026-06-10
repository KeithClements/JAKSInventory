"""
tests/test_r1_vendor_bills.py
=============================
R1-5 + R1-12 — vendor bill duplicate guard, mark-bill-paid, receive-form safety.

R1-5:  create_vendor_bill must reject a duplicate bill_number for the SAME
       vendor (case/whitespace-insensitive) — the same PAI invoice entered
       twice would create two approvable bills (double-payment risk). The same
       bill_number on a DIFFERENT vendor is fine, and blank bill numbers are
       never deduped.

R1-12: VendorBillStatus.PAID was unreachable — no service method, no route.
       POService.mark_bill_paid transitions APPROVED → PAID (terminal) and
       rejects every other status. POST /purchase-orders/{po_id}/bills/
       {bill_id}/pay drives it. The receive form now defaults qty inputs to 0;
       lines submitted as 0/blank must be ignored (no inventory effect) and an
       all-zero submit must redirect with an error, not "received".
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401 — registers every model on Base

from app.constants import POStatus, UserRole, VendorBillStatus
from app.models.product import Product
from app.models.purchase_order import POLine, POReceiptLine, PurchaseOrder, VendorBill
from app.models.user import User
from app.models.vendor import Vendor
from app.services.po_service import POService

from app.main import app as _fastapi_app

_client = TestClient(_fastapi_app, raise_server_exceptions=False)

_counter = itertools.count(1)
_UID = 1  # admin user id seeded in the db fixture


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == 1).first():
        s.add(User(id=1, name="Test Admin", username="admin",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


# ── Data builders ─────────────────────────────────────────────────────────────

def _make_vendor(db) -> Vendor:
    n = next(_counter)
    v = Vendor(name=f"Vendor-R1-{n}", vendor_code=f"R{n:03d}"[:4])
    db.add(v)
    db.flush()
    return v


def _make_product(db, qty_on_hand: int = 0) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"SKU-R1-{n:06d}",
        title=f"Widget-R1 {n}",
        qty_on_hand=qty_on_hand,
        qty_committed=0,
        cost=10.0,
    )
    db.add(p)
    db.flush()
    return p


def _make_po(db, vendor: Vendor, product: Product, *, qty=5, status=POStatus.RECEIVED,
             qty_received=None):
    """PO with one line; qty_received defaults to fully received."""
    n = next(_counter)
    po = PurchaseOrder(
        po_number=f"PO-R1-{n:04d}",
        vendor_id=vendor.id,
        status=status,
        freight_in_cost=0.0,
        notes="",
        internal_notes="",
    )
    db.add(po)
    db.flush()
    line = POLine(
        po_id=po.id,
        product_id=product.id,
        description=product.title,
        qty_ordered=qty,
        qty_received=qty if qty_received is None else qty_received,
        qty_billed=0,
        unit_cost=product.cost,
        core_charge_per_unit=0.0,
        notes="",
    )
    db.add(line)
    db.commit()
    db.refresh(po); db.refresh(line)
    return po, line


def _bill(db, vendor, po, line, *, bill_number, qty_billed=None, unit_cost=None) -> VendorBill:
    """Clean bill (matches qty/cost → auto-APPROVED unless overridden)."""
    return POService(db, current_user_id=_UID).create_vendor_bill(
        po_id=po.id,
        vendor_id=vendor.id,
        bill_number=bill_number,
        bill_date=None,
        due_date=None,
        lines=[{
            "po_line_id": line.id,
            "qty_billed": line.qty_received if qty_billed is None else qty_billed,
            "unit_cost": line.unit_cost if unit_cost is None else unit_cost,
        }],
    )


# ══════════════════════════════════════════════════════════════════════════════
# R1-5 — duplicate bill_number guard
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicateBillGuard:

    def test_duplicate_bill_same_vendor_rejected(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        _bill(db, vendor, po, line, bill_number="PAI-1001", qty_billed=2)

        # Case/whitespace-insensitive: " pai-1001 " collides with "PAI-1001".
        with pytest.raises(ValueError, match="already exists"):
            _bill(db, vendor, po, line, bill_number="  pai-1001 ", qty_billed=1)
        db.rollback()

        bills = db.query(VendorBill).filter(VendorBill.vendor_id == vendor.id).all()
        assert len(bills) == 1, "Duplicate must not create a second bill"

    def test_duplicate_rejected_before_any_line_mutation(self, db):
        # The guard fires BEFORE qty_billed accumulates — a rejected duplicate
        # must not advance the line's cumulative billed quantity.
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        _bill(db, vendor, po, line, bill_number="PAI-2002", qty_billed=2)
        db.refresh(line)
        billed_before = line.qty_billed

        with pytest.raises(ValueError, match="already exists"):
            _bill(db, vendor, po, line, bill_number="PAI-2002", qty_billed=3)
        db.rollback()
        db.refresh(line)
        assert line.qty_billed == billed_before

    def test_same_bill_number_different_vendor_allowed(self, db):
        vendor_a = _make_vendor(db)
        vendor_b = _make_vendor(db)
        product_a = _make_product(db)
        product_b = _make_product(db)
        po_a, line_a = _make_po(db, vendor_a, product_a)
        po_b, line_b = _make_po(db, vendor_b, product_b)

        _bill(db, vendor_a, po_a, line_a, bill_number="INV-777", qty_billed=2)
        second = _bill(db, vendor_b, po_b, line_b, bill_number="INV-777", qty_billed=2)
        assert second.id is not None
        assert second.vendor_id == vendor_b.id

    def test_blank_bill_number_never_deduped(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)

        first = _bill(db, vendor, po, line, bill_number="", qty_billed=1)
        second = _bill(db, vendor, po, line, bill_number="   ", qty_billed=1)
        assert first.id != second.id


# ══════════════════════════════════════════════════════════════════════════════
# R1-12 — mark_bill_paid (APPROVED → PAID)
# ══════════════════════════════════════════════════════════════════════════════

class TestMarkBillPaid:

    def test_approved_bill_transitions_to_paid(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _bill(db, vendor, po, line, bill_number="PAY-1")  # clean → APPROVED
        assert bill.status == VendorBillStatus.APPROVED

        POService(db, current_user_id=_UID).mark_bill_paid(bill.id)
        db.refresh(bill)
        assert bill.status == VendorBillStatus.PAID

    def test_pending_bill_rejected(self, db):
        vendor = _make_vendor(db)
        bill = VendorBill(vendor_id=vendor.id, bill_number="PEND-1",
                          status=VendorBillStatus.PENDING, total_amount=10.0)
        db.add(bill); db.commit(); db.refresh(bill)

        with pytest.raises(ValueError, match="Only approved bills"):
            POService(db, current_user_id=_UID).mark_bill_paid(bill.id)
        db.rollback()
        db.refresh(bill)
        assert bill.status == VendorBillStatus.PENDING

    def test_discrepancy_bill_rejected(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product, qty=5)
        # Bill more than received → DISCREPANCY
        bill = _bill(db, vendor, po, line, bill_number="DISC-1", qty_billed=7)
        assert bill.status == VendorBillStatus.DISCREPANCY

        with pytest.raises(ValueError, match="Only approved bills"):
            POService(db, current_user_id=_UID).mark_bill_paid(bill.id)
        db.rollback()
        db.refresh(bill)
        assert bill.status == VendorBillStatus.DISCREPANCY

    def test_paid_is_terminal(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _bill(db, vendor, po, line, bill_number="PAY-2")
        svc = POService(db, current_user_id=_UID)
        svc.mark_bill_paid(bill.id)

        with pytest.raises(ValueError, match="Only approved bills"):
            svc.mark_bill_paid(bill.id)
        db.rollback()

    def test_missing_bill_rejected(self, db):
        with pytest.raises(ValueError, match="not found"):
            POService(db, current_user_id=_UID).mark_bill_paid(999999)
        db.rollback()


class TestPayBillRoute:

    def test_pay_route_marks_approved_bill_paid(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _bill(db, vendor, po, line, bill_number="ROUTE-1")
        po_id, bill_id = po.id, bill.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/bills/{bill_id}/pay",
            follow_redirects=False,
        )
        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        assert "ok=bill_paid" in resp.headers["location"]

        db.expire_all()
        bill = db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        assert bill.status == VendorBillStatus.PAID

    def test_pay_route_rejects_unapproved_bill(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, _line = _make_po(db, vendor, product)
        bill = VendorBill(po_id=po.id, vendor_id=vendor.id, bill_number="ROUTE-2",
                          status=VendorBillStatus.PENDING, total_amount=10.0)
        db.add(bill); db.commit()
        po_id, bill_id = po.id, bill.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/bills/{bill_id}/pay",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error" in resp.headers["location"]

        db.expire_all()
        bill = db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        assert bill.status == VendorBillStatus.PENDING


# ══════════════════════════════════════════════════════════════════════════════
# R1-12 — receive route ignores 0/blank quantities
# ══════════════════════════════════════════════════════════════════════════════

class TestReceiveZeroQty:

    def test_all_zero_submit_has_no_inventory_effect(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db, qty_on_hand=3)
        po, line = _make_po(db, vendor, product, qty=5,
                            status=POStatus.SENT, qty_received=0)
        po_id, line_id, product_id = po.id, line.id, product.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/receive",
            data={f"recv_{line_id}": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        # Must NOT flash "received" — nothing happened.
        assert "error" in resp.headers["location"]

        db.expire_all()
        assert db.query(POReceiptLine).filter(POReceiptLine.po_id == po_id).count() == 0
        product = db.query(Product).filter(Product.id == product_id).first()
        assert product.qty_on_hand == 3, "qty_on_hand must be untouched"
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        assert po.status == POStatus.SENT

    def test_blank_qty_treated_as_zero(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db, qty_on_hand=0)
        po, line = _make_po(db, vendor, product, qty=5,
                            status=POStatus.SENT, qty_received=0)
        po_id, line_id = po.id, line.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/receive",
            data={f"recv_{line_id}": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error" in resp.headers["location"]
        db.expire_all()
        assert db.query(POReceiptLine).filter(POReceiptLine.po_id == po_id).count() == 0

    def test_zero_lines_skipped_but_positive_lines_received(self, db):
        vendor = _make_vendor(db)
        product_a = _make_product(db, qty_on_hand=0)
        product_b = _make_product(db, qty_on_hand=0)
        n = next(_counter)
        po = PurchaseOrder(po_number=f"PO-R1-{n:04d}", vendor_id=vendor.id,
                           status=POStatus.SENT, freight_in_cost=0.0,
                           notes="", internal_notes="")
        db.add(po); db.flush()
        line_a = POLine(po_id=po.id, product_id=product_a.id, description=product_a.title,
                        qty_ordered=5, qty_received=0, qty_billed=0,
                        unit_cost=10.0, core_charge_per_unit=0.0, notes="")
        line_b = POLine(po_id=po.id, product_id=product_b.id, description=product_b.title,
                        qty_ordered=5, qty_received=0, qty_billed=0,
                        unit_cost=10.0, core_charge_per_unit=0.0, notes="")
        db.add_all([line_a, line_b]); db.commit()
        po_id = po.id
        line_a_id, line_b_id = line_a.id, line_b.id
        product_a_id, product_b_id = product_a.id, product_b.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/receive",
            data={f"recv_{line_a_id}": "2", f"recv_{line_b_id}": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "ok=received" in resp.headers["location"]

        db.expire_all()
        receipt_lines = db.query(POReceiptLine).filter(POReceiptLine.po_id == po_id).all()
        assert len(receipt_lines) == 1
        assert receipt_lines[0].po_line_id == line_a_id
        assert receipt_lines[0].qty_received == 2

        product_a = db.query(Product).filter(Product.id == product_a_id).first()
        product_b = db.query(Product).filter(Product.id == product_b_id).first()
        assert product_a.qty_on_hand == 2
        assert product_b.qty_on_hand == 0, "Zero-qty line must have no inventory effect"
