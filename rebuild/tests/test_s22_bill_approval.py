"""
tests/test_s22_bill_approval.py
===============================
Risk #5 (HIGH) — vendor bills route through AP.

Before: a clean 3-way match (qty + unit cost matched the PO) auto-APPROVED the
bill, making it immediately payable and QBO-eligible with no human in the loop.

After:
  * create_vendor_bill on a clean match lands the bill PENDING (not APPROVED).
    The DISCREPANCY path is unchanged (a mismatch still lands DISCREPANCY).
  * The owning PO is NOT advanced to BILLED at bill creation — that only
    happens on explicit approval.
  * approve_bill transitions PENDING -> APPROVED and flags it QBO-eligible
    (qbo_sync_status = PENDING), advancing the PO to BILLED.
  * approve_bill is gated on Permission.APPROVE_VENDOR_BILL: a SALES (counter)
    user cannot approve; ADMIN and BOOKKEEPING can.
  * The POST /purchase-orders/{po}/bills/{bill}/approve route drives it and the
    workspace surfaces an Approve button for PENDING bills.
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

from app.constants import (
    POStatus,
    QBOSyncStatus,
    UserRole,
    VendorBillStatus,
)
from app.models.product import Product
from app.models.purchase_order import POLine, PurchaseOrder, VendorBill
from app.models.user import User
from app.models.vendor import Vendor
from app.services.base import PermissionDeniedError
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

def _user(db, role: str) -> User:
    n = next(_counter)
    u = User(name=f"{role}-{n}", username=f"{role}_{n}",
             password_hash="x", role=role)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _make_vendor(db) -> Vendor:
    n = next(_counter)
    v = Vendor(name=f"Vendor-S22-{n}", vendor_code=f"S{n:03d}"[:4])
    db.add(v); db.flush()
    return v


def _make_product(db) -> Product:
    n = next(_counter)
    p = Product(sku=f"SKU-S22-{n:06d}", title=f"Widget-S22 {n}",
                qty_on_hand=0, qty_committed=0, cost=10.0)
    db.add(p); db.flush()
    return p


def _make_po(db, vendor: Vendor, product: Product, *, qty=5) -> tuple:
    """Fully-received PO with one line, ready to be billed."""
    n = next(_counter)
    po = PurchaseOrder(po_number=f"PO-S22-{n:04d}", vendor_id=vendor.id,
                       status=POStatus.RECEIVED, freight_in_cost=0.0,
                       notes="", internal_notes="")
    db.add(po); db.flush()
    line = POLine(po_id=po.id, product_id=product.id, description=product.title,
                  qty_ordered=qty, qty_received=qty, qty_billed=0,
                  unit_cost=product.cost, core_charge_per_unit=0.0, notes="")
    db.add(line); db.commit()
    db.refresh(po); db.refresh(line)
    return po, line


def _clean_bill(db, vendor, po, line, *, bill_number, actor=_UID) -> VendorBill:
    """A bill that exactly matches the PO line (qty + cost) → clean 3-way match."""
    return POService(db, current_user_id=actor).create_vendor_bill(
        po_id=po.id,
        vendor_id=vendor.id,
        bill_number=bill_number,
        bill_date=None,
        due_date=None,
        lines=[{
            "po_line_id": line.id,
            "qty_billed": line.qty_received,
            "unit_cost": line.unit_cost,
        }],
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1 — a clean match now lands PENDING (not APPROVED)
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanBillLandsPending:

    def test_exact_match_bill_is_pending(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)

        bill = _clean_bill(db, vendor, po, line, bill_number="CLEAN-1")
        assert bill.status == VendorBillStatus.PENDING, (
            "A clean 3-way match must route through AP (PENDING), not auto-approve"
        )

    def test_clean_bill_not_yet_qbo_eligible(self, db):
        # PENDING/DISCREPANCY bills are never QBO-pushable — eligibility comes
        # only with approval. Sanity-check it isn't APPROVED on creation.
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)

        bill = _clean_bill(db, vendor, po, line, bill_number="CLEAN-2")
        assert bill.status != VendorBillStatus.APPROVED

    def test_po_not_advanced_to_billed_until_approved(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)

        _clean_bill(db, vendor, po, line, bill_number="CLEAN-3")
        db.refresh(po)
        assert po.status == POStatus.RECEIVED, (
            "An unapproved (PENDING) bill must not advance the PO to BILLED"
        )

    def test_discrepancy_path_unchanged(self, db):
        # Billing more than received is still a DISCREPANCY (not PENDING).
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product, qty=5)

        bill = POService(db, current_user_id=_UID).create_vendor_bill(
            po_id=po.id, vendor_id=vendor.id, bill_number="DISC-1",
            bill_date=None, due_date=None,
            lines=[{"po_line_id": line.id, "qty_billed": 7,
                    "unit_cost": line.unit_cost}],
        )
        assert bill.status == VendorBillStatus.DISCREPANCY


# ══════════════════════════════════════════════════════════════════════════════
# 2 — approve_bill: PENDING -> APPROVED + QBO-eligible + advances PO
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveBill:

    def test_approve_transitions_pending_to_approved(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _clean_bill(db, vendor, po, line, bill_number="APR-1")
        assert bill.status == VendorBillStatus.PENDING

        POService(db, current_user_id=_UID).approve_bill(bill.id)
        db.refresh(bill)
        assert bill.status == VendorBillStatus.APPROVED

    def test_approve_makes_bill_qbo_eligible(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _clean_bill(db, vendor, po, line, bill_number="APR-2")

        POService(db, current_user_id=_UID).approve_bill(bill.id)
        db.refresh(bill)
        assert bill.status == VendorBillStatus.APPROVED
        assert bill.qbo_sync_status == QBOSyncStatus.PENDING, (
            "Approval must flag the bill for QBO sync"
        )

    def test_approve_advances_po_to_billed(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _clean_bill(db, vendor, po, line, bill_number="APR-3")
        db.refresh(po)
        assert po.status == POStatus.RECEIVED  # not yet

        POService(db, current_user_id=_UID).approve_bill(bill.id)
        db.refresh(po)
        assert po.status == POStatus.BILLED


# ══════════════════════════════════════════════════════════════════════════════
# 3 — RBAC: SALES cannot approve; ADMIN + BOOKKEEPING can
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveRBAC:

    def test_sales_user_cannot_approve(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _clean_bill(db, vendor, po, line, bill_number="RBAC-1")
        sales = _user(db, UserRole.SALES)

        with pytest.raises(PermissionDeniedError):
            POService(db, current_user_id=sales.id).approve_bill(bill.id)
        db.rollback()
        db.refresh(bill)
        assert bill.status == VendorBillStatus.PENDING, (
            "A denied approval must leave the bill PENDING"
        )

    def test_admin_user_can_approve(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _clean_bill(db, vendor, po, line, bill_number="RBAC-2")
        admin = _user(db, UserRole.ADMIN)

        POService(db, current_user_id=admin.id).approve_bill(bill.id)
        db.refresh(bill)
        assert bill.status == VendorBillStatus.APPROVED

    def test_bookkeeping_user_can_approve(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _clean_bill(db, vendor, po, line, bill_number="RBAC-3")
        bk = _user(db, UserRole.BOOKKEEPING)

        POService(db, current_user_id=bk.id).approve_bill(bill.id)
        db.refresh(bill)
        assert bill.status == VendorBillStatus.APPROVED


# ══════════════════════════════════════════════════════════════════════════════
# 4 — the approve route drives the PENDING -> APPROVED transition
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveRoute:

    def test_approve_route_approves_clean_pending_bill(self, db):
        vendor = _make_vendor(db)
        product = _make_product(db)
        po, line = _make_po(db, vendor, product)
        bill = _clean_bill(db, vendor, po, line, bill_number="ROUTE-A1")
        assert bill.status == VendorBillStatus.PENDING
        po_id, bill_id = po.id, bill.id

        resp = _client.post(
            f"/purchase-orders/{po_id}/bills/{bill_id}/approve",
            follow_redirects=False,
        )
        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        assert "ok=bill_approved" in resp.headers["location"]

        db.expire_all()
        bill = db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        assert bill.status == VendorBillStatus.APPROVED
        assert bill.qbo_sync_status == QBOSyncStatus.PENDING
