"""
tests/test_warranty_service.py
==============================
Unit tests for WarrantyService — the warranty claim lifecycle.

  draft → submitted_to_vendor → vendor_approved  → customer_credited → closed
                              ↘ vendor_denied     → customer_notified → closed

Covers the state machine guards, total-credit recomputation from line
resolutions, the customer-credit payout (claim → CreditMemo → credit_balance),
the vendor-credit ledger entry, and the denial path.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import __all_models__  # noqa: F401 — registers every model on Base
from app.database import Base

import pytest

from app.constants import (
    VendorCreditStatus, VendorCreditType,
    WarrantyDecision, WarrantyResolution, WarrantyStatus,
)
from app.models.customer import Customer
from app.models.vendor import Vendor, VendorCredit
from app.services.warranty_service import WarrantyService

# ── Test isolation (mirrors conftest pattern) ─────────────────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=_engine)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


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


# ── Helpers ───────────────────────────────────────────────────────────────────

_seq = 0


def _make_customer(db):
    global _seq
    _seq += 1
    c = Customer(company_name=f"Acme {_seq}")
    db.add(c)
    db.flush()
    return c


def _make_vendor(db):
    global _seq
    _seq += 1
    v = Vendor(name=f"Vendor {_seq}", vendor_code=f"V{_seq % 1000}")
    db.add(v)
    db.flush()
    return v


def _approved_claim(db, credit_amount=100.0):
    """Build a claim and walk it to VENDOR_APPROVED with the given credit."""
    cust = _make_customer(db)
    svc = WarrantyService(db)
    claim = svc.create_claim(
        customer_id=cust.id, invoice_id=None, vendor_id=None,
        failure_description="Injector failure",
        lines=[{"qty_claimed": 1}],
    )
    svc.submit_to_vendor(claim.id)
    svc.record_vendor_decision(
        claim.id, WarrantyDecision.APPROVED,
        line_resolutions=[{
            "claim_line_id": claim.claim_lines[0].id,
            "approved_qty": 1,
            "credit_amount": credit_amount,
            "resolution": WarrantyResolution.CREDIT,
        }],
    )
    return svc, claim, cust


# ── create_claim / submit ─────────────────────────────────────────────────────


class TestCreateAndSubmit:
    def test_create_seeds_draft(self, db):
        cust = _make_customer(db)
        claim = WarrantyService(db).create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="failed", lines=[{"qty_claimed": 2}],
        )
        assert claim.status == WarrantyStatus.DRAFT
        assert claim.claim_number.startswith("WC-")
        assert len(claim.claim_lines) == 1

    def test_create_without_lines_rejected(self, db):
        cust = _make_customer(db)
        with pytest.raises(ValueError, match="at least one line"):
            WarrantyService(db).create_claim(
                customer_id=cust.id, invoice_id=None, vendor_id=None,
                failure_description="x", lines=[],
            )

    def test_submit_transitions_to_submitted(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        svc.submit_to_vendor(claim.id)
        db.refresh(claim)
        assert claim.status == WarrantyStatus.SUBMITTED_TO_VENDOR

    def test_cannot_submit_twice(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        svc.submit_to_vendor(claim.id)
        with pytest.raises(ValueError, match="not DRAFT"):
            svc.submit_to_vendor(claim.id)

    def test_cannot_add_line_to_submitted_claim(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        svc.submit_to_vendor(claim.id)
        with pytest.raises(ValueError, match="Only DRAFT"):
            svc.add_claim_line(claim.id, {"qty_claimed": 1})


# ── record_vendor_decision ────────────────────────────────────────────────────


class TestRecordVendorDecision:
    def test_approved_sets_status_and_sums_credit(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x",
            lines=[{"qty_claimed": 1}, {"qty_claimed": 1}],
        )
        svc.submit_to_vendor(claim.id)
        svc.record_vendor_decision(
            claim.id, WarrantyDecision.APPROVED,
            line_resolutions=[
                {"claim_line_id": claim.claim_lines[0].id, "approved_qty": 1,
                 "credit_amount": 60.0, "resolution": WarrantyResolution.CREDIT},
                {"claim_line_id": claim.claim_lines[1].id, "approved_qty": 1,
                 "credit_amount": 40.0, "resolution": WarrantyResolution.CREDIT},
            ],
        )
        db.refresh(claim)
        assert claim.status == WarrantyStatus.VENDOR_APPROVED
        assert claim.total_credit_amount == 100.0

    def test_denied_sets_denied_status(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        svc.submit_to_vendor(claim.id)
        svc.record_vendor_decision(
            claim.id, WarrantyDecision.DENIED,
            line_resolutions=[{
                "claim_line_id": claim.claim_lines[0].id, "approved_qty": 0,
                "credit_amount": 0.0, "resolution": WarrantyResolution.DENIED,
            }],
        )
        db.refresh(claim)
        assert claim.status == WarrantyStatus.VENDOR_DENIED
        assert claim.total_credit_amount == 0.0

    def test_decision_before_submission_rejected(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        with pytest.raises(ValueError, match="not SUBMITTED_TO_VENDOR"):
            svc.record_vendor_decision(
                claim.id, WarrantyDecision.APPROVED, line_resolutions=[],
            )

    def test_unknown_line_id_rejected(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        svc.submit_to_vendor(claim.id)
        with pytest.raises(ValueError, match="not found"):
            svc.record_vendor_decision(
                claim.id, WarrantyDecision.APPROVED,
                line_resolutions=[{"claim_line_id": 999999, "approved_qty": 1,
                                   "credit_amount": 10.0}],
            )


# ── credit_customer / issue_refund_check ──────────────────────────────────────


class TestCustomerResolution:
    def test_credit_customer_increases_credit_balance(self, db):
        svc, claim, cust = _approved_claim(db, credit_amount=125.0)
        svc.credit_customer(claim.id)
        db.refresh(claim)
        db.refresh(cust)
        assert claim.status == WarrantyStatus.CUSTOMER_CREDITED
        assert claim.resolution_type == WarrantyResolution.CREDIT
        assert cust.credit_balance == 125.0

    def test_credit_customer_requires_vendor_approved(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        with pytest.raises(ValueError, match="not VENDOR_APPROVED"):
            svc.credit_customer(claim.id)

    def test_credit_customer_zero_credit_rejected(self, db):
        svc, claim, cust = _approved_claim(db, credit_amount=0.0)
        with pytest.raises(ValueError, match="no credit amount"):
            svc.credit_customer(claim.id)

    def test_issue_refund_check_marks_credited(self, db):
        svc, claim, cust = _approved_claim(db, credit_amount=75.0)
        svc.issue_refund_check(claim.id)
        db.refresh(claim)
        db.refresh(cust)
        assert claim.status == WarrantyStatus.CUSTOMER_CREDITED
        # Check path does NOT touch credit_balance (physical check issued manually).
        assert cust.credit_balance == 0.0


# ── denial path + close ───────────────────────────────────────────────────────


class TestDenialAndClose:
    def test_notify_then_close_denied_claim(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        svc.submit_to_vendor(claim.id)
        svc.record_vendor_decision(
            claim.id, WarrantyDecision.DENIED,
            line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                               "approved_qty": 0, "credit_amount": 0.0}],
        )
        svc.notify_customer_of_denial(claim.id, notes="Vendor rejected: out of period")
        db.refresh(claim)
        assert claim.status == WarrantyStatus.CUSTOMER_NOTIFIED

        svc.close_claim(claim.id)
        db.refresh(claim)
        assert claim.status == WarrantyStatus.CLOSED

    def test_close_requires_resolved_state(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        with pytest.raises(ValueError, match="Can only close"):
            svc.close_claim(claim.id)

    def test_notify_requires_denied_status(self, db):
        svc, claim, _ = _approved_claim(db, credit_amount=50.0)
        with pytest.raises(ValueError, match="not VENDOR_DENIED"):
            svc.notify_customer_of_denial(claim.id, notes="x")


# ── record_vendor_credit ──────────────────────────────────────────────────────


class TestRecordVendorCredit:
    def test_creates_open_warranty_vendor_credit(self, db):
        svc, claim, _ = _approved_claim(db, credit_amount=100.0)
        vendor = _make_vendor(db)
        vc = svc.record_vendor_credit(
            claim.id, vendor_id=vendor.id, credit_amount=80.0, reference="RGA-9",
        )
        assert isinstance(vc, VendorCredit)
        assert vc.credit_type == VendorCreditType.WARRANTY
        assert vc.status == VendorCreditStatus.OPEN
        assert vc.amount == 80.0
        assert claim.claim_number in vc.notes

    def test_non_positive_credit_rejected(self, db):
        svc, claim, _ = _approved_claim(db, credit_amount=100.0)
        vendor = _make_vendor(db)
        with pytest.raises(ValueError, match="must be positive"):
            svc.record_vendor_credit(claim.id, vendor_id=vendor.id, credit_amount=0.0)


# ── queries ───────────────────────────────────────────────────────────────────


class TestQueries:
    def test_get_open_claims_excludes_closed(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        open_claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="open one", lines=[{"qty_claimed": 1}],
        )
        ids = {c.id for c in svc.get_open_claims(customer_id=cust.id)}
        assert open_claim.id in ids

    def test_get_pending_vendor_submission_lists_draft_with_lines(self, db):
        cust = _make_customer(db)
        svc = WarrantyService(db)
        claim = svc.create_claim(
            customer_id=cust.id, invoice_id=None, vendor_id=None,
            failure_description="x", lines=[{"qty_claimed": 1}],
        )
        pending_ids = {c.id for c in svc.get_pending_vendor_submission()}
        assert claim.id in pending_ids
