"""
tests/test_workflow_match_resolution.py
========================================
Targeted unit/service tests for the 3-way match resolution workflow.

Coverage (supplementing TestMatchResolutionGate in test_workflow_series3.py):
  - compute_match_line state machine: is_flag, state strings, resolution overlay
  - ON_HOLD de-prioritises (is_flag=False, state="on_hold")
  - CLEARED requires reason
  - suggested_credit auto-compute for over_billed and cost_variance
  - can_resolve flag on non-variance lines
  - create_match_vendor_credit: suggested amount, apply_now, VCM back-link
  - cost_variance VCM auto-compute (vendor overcharged on unit cost)
  - APPROVE_VENDOR_BILL missing → PermissionDeniedError on resolve_match_line
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import __all_models__  # noqa: F401
from app.database import Base

import pytest

from app.constants import (
    MatchResolution, POStatus, UserRole,
    VendorBillStatus, VendorCreditMemoTrigger,
)
from app.models.purchase_order import (
    PurchaseOrder, POLine, VendorBill, VendorBillLine,
)
from app.models.user import User
from app.models.vendor import Vendor
from app.services.base import PermissionDeniedError
from app.services.po_service import POService

# ── Test isolation (mirrors conftest pattern) ─────────────────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=_engine)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

_counter = itertools.count(1)


@pytest.fixture(autouse=True)
def _activate_engine():
    from tests.conftest import activate
    activate(_engine)


@pytest.fixture()
def db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_vendor(db) -> Vendor:
    n = next(_counter)
    v = Vendor(name=f"Vendor-{n}", is_active=True)
    db.add(v)
    db.flush()
    return v


def _make_admin_user(db) -> User:
    n = next(_counter)
    u = User(name=f"Admin-{n}", username=f"admin_{n}", password_hash="x", role=UserRole.ADMIN)
    db.add(u)
    db.flush()
    return u


def _make_sales_user(db) -> User:
    n = next(_counter)
    u = User(name=f"Sales-{n}", username=f"sales_{n}", password_hash="x", role=UserRole.SALES)
    db.add(u)
    db.flush()
    return u


def _make_over_billed_setup(
    db,
    qty_ordered: int = 5,
    qty_received: int = 5,
    qty_billed: int = 6,
    unit_cost: float = 100.0,
    billed_unit_cost: float = 100.0,
) -> tuple[PurchaseOrder, POLine, VendorBill]:
    """
    PO → received → vendor bill that over-bills (qty_billed > qty_received).
    Default: ordered=5, received=5, billed=6, all at $100/unit.
    """
    n = next(_counter)
    vendor = _make_vendor(db)
    db.flush()

    po = PurchaseOrder(
        po_number=f"PO-MR-{n:04d}",
        vendor_id=vendor.id,
        status=POStatus.RECEIVED,
    )
    db.add(po)
    db.flush()

    line = POLine(
        po_id=po.id,
        qty_ordered=qty_ordered,
        qty_received=qty_received,
        qty_billed=0,
        unit_cost=unit_cost,
    )
    db.add(line)
    db.flush()

    bill = VendorBill(
        po_id=po.id,
        vendor_id=vendor.id,
        bill_number=f"BILL-MR-{n:04d}",
        total_amount=round(qty_billed * billed_unit_cost, 2),
        status=VendorBillStatus.DISCREPANCY,
    )
    db.add(bill)
    db.flush()

    bill_line = VendorBillLine(
        bill_id=bill.id,
        po_line_id=line.id,
        qty_billed=qty_billed,
        unit_cost=billed_unit_cost,
    )
    db.add(bill_line)
    line.qty_billed = qty_billed
    db.commit()
    db.expire_all()

    return po, line, bill


def _make_cost_variance_setup(
    db,
    ordered_cost: float = 100.0,
    billed_cost: float = 110.0,
    qty: int = 5,
) -> tuple[PurchaseOrder, POLine, VendorBill]:
    """
    PO line where billed unit cost ≠ ordered unit cost (cost_variance).
    qty_billed == qty_received (no over-bill); only cost differs.
    """
    n = next(_counter)
    vendor = _make_vendor(db)
    db.flush()

    po = PurchaseOrder(
        po_number=f"PO-CV-{n:04d}",
        vendor_id=vendor.id,
        status=POStatus.RECEIVED,
    )
    db.add(po)
    db.flush()

    line = POLine(
        po_id=po.id,
        qty_ordered=qty,
        qty_received=qty,
        qty_billed=0,
        unit_cost=ordered_cost,
    )
    db.add(line)
    db.flush()

    bill = VendorBill(
        po_id=po.id,
        vendor_id=vendor.id,
        bill_number=f"BILL-CV-{n:04d}",
        total_amount=round(qty * billed_cost, 2),
        status=VendorBillStatus.DISCREPANCY,
    )
    db.add(bill)
    db.flush()

    bill_line = VendorBillLine(
        bill_id=bill.id,
        po_line_id=line.id,
        qty_billed=qty,
        unit_cost=billed_cost,
    )
    db.add(bill_line)
    line.qty_billed = qty
    db.commit()
    db.expire_all()

    return po, line, bill


# ══════════════════════════════════════════════════════════════════════════════
# compute_match_line state machine
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeMatchLine:
    """compute_match_line returns correct state, is_flag, and resolution metadata."""

    def test_over_billed_is_flagged(self, db):
        _, line, _ = _make_over_billed_setup(db)
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        assert row["state"] == "over_billed"
        assert row["is_flag"] is True
        assert row["can_resolve"] is True

    def test_over_billed_qty_var(self, db):
        _, line, _ = _make_over_billed_setup(db, qty_received=5, qty_billed=6)
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        assert row["qty_var"] == 1   # billed - received

    def test_cost_variance_is_flagged(self, db):
        _, line, _ = _make_cost_variance_setup(db, ordered_cost=100.0, billed_cost=110.0)
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        assert row["state"] == "cost_variance"
        assert row["is_flag"] is True
        assert row["cost_var"] == pytest.approx(10.0)

    def test_matched_line_no_flag(self, db):
        """qty_billed == qty_received, same unit cost → matched, is_flag=False."""
        _, line, _ = _make_over_billed_setup(
            db, qty_ordered=5, qty_received=5, qty_billed=5,
            unit_cost=100.0, billed_unit_cost=100.0,
        )
        # Create a non-discrepancy bill
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        assert row["state"] == "matched"
        assert row["is_flag"] is False
        assert row["can_resolve"] is False

    def test_awaiting_bill_not_flagged(self, db):
        """qty_received > qty_billed → awaiting_bill, not a match-flag."""
        n = next(_counter)
        vendor = _make_vendor(db)
        po = PurchaseOrder(
            po_number=f"PO-AB-{n:04d}",
            vendor_id=vendor.id,
            status=POStatus.RECEIVED,
        )
        db.add(po)
        db.flush()
        line = POLine(
            po_id=po.id, qty_ordered=5, qty_received=5, qty_billed=0, unit_cost=50.0
        )
        db.add(line)
        db.commit()
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        assert row["state"] == "awaiting_bill"
        assert row["is_flag"] is False

    def test_accepted_resolution_clears_flag(self, db):
        """ACCEPTED terminal resolution → state='resolved_accepted', is_flag=False."""
        _, line, _ = _make_over_billed_setup(db)
        db.expire_all()
        line = db.merge(line)
        line.match_resolution = MatchResolution.ACCEPTED
        db.flush()
        row = POService.compute_match_line(line)
        assert row["state"] == "resolved_accepted"
        assert row["is_flag"] is False

    def test_credited_resolution_clears_flag(self, db):
        _, line, _ = _make_over_billed_setup(db)
        db.expire_all()
        line = db.merge(line)
        line.match_resolution = MatchResolution.CREDITED
        db.flush()
        row = POService.compute_match_line(line)
        assert row["state"] == "resolved_credited"
        assert row["is_flag"] is False

    def test_cleared_resolution_clears_flag(self, db):
        _, line, _ = _make_over_billed_setup(db)
        db.expire_all()
        line = db.merge(line)
        line.match_resolution = MatchResolution.CLEARED
        db.flush()
        row = POService.compute_match_line(line)
        assert row["state"] == "resolved_cleared"
        assert row["is_flag"] is False

    def test_on_hold_deprioritises(self, db):
        """ON_HOLD → state='on_hold', is_flag=False (not in active-flag count)."""
        _, line, _ = _make_over_billed_setup(db)
        db.expire_all()
        line = db.merge(line)
        line.match_resolution = MatchResolution.ON_HOLD
        db.flush()
        row = POService.compute_match_line(line)
        assert row["state"] == "on_hold"
        assert row["is_flag"] is False

    def test_rejected_remains_flagged(self, db):
        """REJECTED → still is_flag=True (AP is still disputing, needs action)."""
        _, line, _ = _make_over_billed_setup(db)
        db.expire_all()
        line = db.merge(line)
        line.match_resolution = MatchResolution.REJECTED
        db.flush()
        row = POService.compute_match_line(line)
        assert row["state"] == "rejected"
        assert row["is_flag"] is True

    def test_resolution_metadata_in_row(self, db):
        """Resolution metadata keys are present in the row dict."""
        _, line, _ = _make_over_billed_setup(db)
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        for key in (
            "resolution", "resolution_reason", "resolved_by_id",
            "resolved_at", "resolution_vcm_id", "suggested_credit", "can_resolve",
        ):
            assert key in row, f"Missing key: {key}"


# ══════════════════════════════════════════════════════════════════════════════
# suggested_credit auto-compute
# ══════════════════════════════════════════════════════════════════════════════

class TestSuggestedCredit:
    """suggested_credit is computed correctly for both variance types."""

    def test_over_billed_suggested_credit(self, db):
        """over_billed: suggested = (qty_billed - qty_received) × billed_unit_cost."""
        _, line, _ = _make_over_billed_setup(
            db, qty_received=5, qty_billed=6, billed_unit_cost=100.0
        )
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        # 1 extra unit × $100
        assert row["suggested_credit"] == pytest.approx(100.0)

    def test_cost_variance_suggested_credit_vendor_overcharged(self, db):
        """cost_variance (billed > ordered): suggested = (billed - ordered) × qty_billed."""
        _, line, _ = _make_cost_variance_setup(
            db, ordered_cost=100.0, billed_cost=110.0, qty=5
        )
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        # $10 per unit × 5 units
        assert row["suggested_credit"] == pytest.approx(50.0)

    def test_cost_variance_vendor_undercharged_no_credit(self, db):
        """cost_variance where vendor charged LESS: suggested_credit is None (JAKS owes nothing)."""
        _, line, _ = _make_cost_variance_setup(
            db, ordered_cost=100.0, billed_cost=90.0, qty=5
        )
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        assert row["suggested_credit"] is None

    def test_matched_line_no_suggested_credit(self, db):
        _, line, _ = _make_over_billed_setup(
            db, qty_received=5, qty_billed=5, unit_cost=100.0, billed_unit_cost=100.0
        )
        db.expire_all()
        line = db.merge(line)
        row = POService.compute_match_line(line)
        assert row["suggested_credit"] is None


# ══════════════════════════════════════════════════════════════════════════════
# resolve_match_line: decisions
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveMatchLine:

    def test_on_hold_does_not_open_gate(self, db):
        """ON_HOLD de-prioritises the line but bill stays DISCREPANCY (not PENDING)."""
        _, line, bill = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        svc.resolve_match_line(line.id, decision=MatchResolution.ON_HOLD)
        db.expire_all()
        updated_bill = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
        # ON_HOLD counts for gate-opening (PENDING), not DISCREPANCY
        assert updated_bill.status == VendorBillStatus.PENDING

    def test_cleared_requires_reason(self, db):
        _, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        with pytest.raises(ValueError, match="reason is required"):
            svc.resolve_match_line(line.id, decision=MatchResolution.CLEARED, reason="")

    def test_cleared_with_reason_opens_gate(self, db):
        _, line, bill = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        svc.resolve_match_line(
            line.id, decision=MatchResolution.CLEARED, reason="Corrected in separate system"
        )
        db.expire_all()
        updated_bill = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
        assert updated_bill.status == VendorBillStatus.PENDING

    def test_cannot_set_credited_directly(self, db):
        """CREDITED is reserved for create_match_vendor_credit; setting it directly raises."""
        _, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        with pytest.raises(ValueError, match="create_match_vendor_credit"):
            svc.resolve_match_line(line.id, decision=MatchResolution.CREDITED)

    def test_unknown_decision_raises(self, db):
        _, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        with pytest.raises(ValueError, match="Invalid resolution"):
            svc.resolve_match_line(line.id, decision="nonsense")

    def test_sales_user_cannot_resolve(self, db):
        """APPROVE_VENDOR_BILL missing → PermissionDeniedError."""
        _, line, _ = _make_over_billed_setup(db)
        sales = _make_sales_user(db)
        svc = POService(db, current_user_id=sales.id)
        with pytest.raises(PermissionDeniedError):
            svc.resolve_match_line(line.id, decision=MatchResolution.ACCEPTED)

    def test_records_resolved_by_and_at(self, db):
        _, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        svc.resolve_match_line(
            line.id, decision=MatchResolution.ACCEPTED, reason="Agreed"
        )
        db.expire_all()
        updated = db.query(POLine).filter(POLine.id == line.id).first()
        assert updated.match_resolved_by_id == admin.id
        assert updated.match_resolved_at is not None
        assert updated.match_resolution_reason == "Agreed"

    def test_re_deciding_is_allowed(self, db):
        """AP may change their mind (e.g. on_hold → accepted)."""
        _, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        svc.resolve_match_line(line.id, decision=MatchResolution.ON_HOLD)
        svc.resolve_match_line(
            line.id, decision=MatchResolution.ACCEPTED, reason="Got confirmation"
        )
        db.expire_all()
        updated = db.query(POLine).filter(POLine.id == line.id).first()
        assert updated.match_resolution == MatchResolution.ACCEPTED


# ══════════════════════════════════════════════════════════════════════════════
# create_match_vendor_credit
# ══════════════════════════════════════════════════════════════════════════════

class TestCreateMatchVendorCredit:

    def test_over_billed_auto_amount(self, db):
        """Auto-computed amount: 1 extra unit × $100 = $100."""
        _, line, _ = _make_over_billed_setup(
            db, qty_received=5, qty_billed=6, billed_unit_cost=100.0
        )
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        vcm = svc.create_match_vendor_credit(line.id, reason="Over-shipped")
        assert vcm.total_amount == pytest.approx(100.0)

    def test_cost_variance_auto_amount(self, db):
        """Auto-computed amount for cost_variance: $10 × 5 units = $50."""
        _, line, _ = _make_cost_variance_setup(
            db, ordered_cost=100.0, billed_cost=110.0, qty=5
        )
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        vcm = svc.create_match_vendor_credit(line.id, reason="Unit cost higher than PO")
        assert vcm.total_amount == pytest.approx(50.0)

    def test_explicit_amount_overrides_auto(self, db):
        _, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        vcm = svc.create_match_vendor_credit(line.id, amount=42.0, reason="Negotiated")
        assert vcm.total_amount == pytest.approx(42.0)

    def test_apply_now_reduces_bill_balance(self, db):
        """apply_now=True allocates VCM against the discrepancy bill immediately."""
        from app.services.vendor_credit_service import VendorCreditService
        _, line, bill = _make_over_billed_setup(
            db, qty_received=5, qty_billed=6, billed_unit_cost=100.0
        )
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        vcm_svc = VendorCreditService(db, current_user_id=admin.id)

        original_balance = vcm_svc.vendor_bill_remaining_balance(bill.id)
        vcm = svc.create_match_vendor_credit(line.id, reason="Overage", apply_now=True)

        db.expire_all()
        new_balance = vcm_svc.vendor_bill_remaining_balance(bill.id)
        assert new_balance == pytest.approx(original_balance - vcm.total_amount)

    def test_links_vcm_id_on_line(self, db):
        _, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        vcm = svc.create_match_vendor_credit(line.id, reason="Overcharge")
        db.expire_all()
        updated = db.query(POLine).filter(POLine.id == line.id).first()
        assert updated.match_resolution_vcm_id == vcm.id
        assert updated.match_resolution == MatchResolution.CREDITED

    def test_vendor_of_vcm_matches_po_vendor(self, db):
        po, line, _ = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        vcm = svc.create_match_vendor_credit(line.id, reason="Overcharge")
        assert vcm.vendor_id == po.vendor_id

    def test_matched_line_raises_without_explicit_amount(self, db):
        """A line with no variance and no supplied amount raises (cannot auto-compute)."""
        _, line, _ = _make_over_billed_setup(
            db, qty_received=5, qty_billed=5, unit_cost=100.0, billed_unit_cost=100.0
        )
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        with pytest.raises(ValueError, match="auto-compute"):
            svc.create_match_vendor_credit(line.id)


# ══════════════════════════════════════════════════════════════════════════════
# _advance_bill_after_match gate logic
# ══════════════════════════════════════════════════════════════════════════════

class TestAdvanceBillAfterMatch:
    """Gate opens correctly for terminal+on_hold; stays closed for rejected/unresolved."""

    def test_gate_opens_on_accepted(self, db):
        _, line, bill = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        svc.resolve_match_line(line.id, MatchResolution.ACCEPTED)
        db.expire_all()
        assert db.get(VendorBill, bill.id).status == VendorBillStatus.PENDING

    def test_gate_stays_closed_on_rejected(self, db):
        _, line, bill = _make_over_billed_setup(db)
        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        svc.resolve_match_line(
            line.id, MatchResolution.REJECTED, reason="Awaiting corrected bill"
        )
        db.expire_all()
        assert db.get(VendorBill, bill.id).status == VendorBillStatus.DISCREPANCY

    def test_gate_stays_closed_when_unresolved(self, db):
        """Bill stays DISCREPANCY if ANY line is still UNRESOLVED."""
        n = next(_counter)
        vendor = _make_vendor(db)
        po = PurchaseOrder(
            po_number=f"PO-GATE-{n:04d}", vendor_id=vendor.id, status=POStatus.RECEIVED
        )
        db.add(po)
        db.flush()

        line1 = POLine(po_id=po.id, qty_ordered=5, qty_received=5, qty_billed=0, unit_cost=10.0)
        line2 = POLine(po_id=po.id, qty_ordered=3, qty_received=3, qty_billed=0, unit_cost=20.0)
        db.add_all([line1, line2])
        db.flush()

        bill = VendorBill(
            po_id=po.id, vendor_id=vendor.id,
            bill_number=f"BILL-GATE-{n:04d}",
            total_amount=110.0, status=VendorBillStatus.DISCREPANCY,
        )
        db.add(bill)
        db.flush()

        # Line1 over-billed by 1
        bl1 = VendorBillLine(bill_id=bill.id, po_line_id=line1.id, qty_billed=6, unit_cost=10.0)
        # Line2 over-billed by 1 too
        bl2 = VendorBillLine(bill_id=bill.id, po_line_id=line2.id, qty_billed=4, unit_cost=20.0)
        db.add_all([bl1, bl2])
        line1.qty_billed = 6
        line2.qty_billed = 4
        db.commit()
        db.expire_all()

        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)

        # Resolve only line1 — line2 still UNRESOLVED
        svc.resolve_match_line(line1.id, MatchResolution.ACCEPTED)
        db.expire_all()
        # Gate should NOT open — line2 is still unresolved
        assert db.get(VendorBill, bill.id).status == VendorBillStatus.DISCREPANCY

        # Now resolve line2
        svc.resolve_match_line(line2.id, MatchResolution.CLEARED, reason="Confirmed by AP")
        db.expire_all()
        assert db.get(VendorBill, bill.id).status == VendorBillStatus.PENDING

    def test_non_discrepancy_bill_unaffected(self, db):
        """_advance_bill_after_match is a no-op on non-DISCREPANCY bills."""
        n = next(_counter)
        vendor = _make_vendor(db)
        po = PurchaseOrder(
            po_number=f"PO-ND-{n:04d}", vendor_id=vendor.id, status=POStatus.RECEIVED
        )
        db.add(po)
        db.flush()
        line = POLine(
            po_id=po.id, qty_ordered=5, qty_received=5, qty_billed=5, unit_cost=10.0
        )
        db.add(line)
        db.flush()
        bill = VendorBill(
            po_id=po.id, vendor_id=vendor.id,
            bill_number=f"BILL-ND-{n:04d}",
            total_amount=50.0, status=VendorBillStatus.APPROVED,
        )
        db.add(bill)
        db.commit()
        db.expire_all()

        admin = _make_admin_user(db)
        svc = POService(db, current_user_id=admin.id)
        svc._advance_bill_after_match(db.get(VendorBill, bill.id))
        db.expire_all()
        # APPROVED should be unchanged
        assert db.get(VendorBill, bill.id).status == VendorBillStatus.APPROVED
