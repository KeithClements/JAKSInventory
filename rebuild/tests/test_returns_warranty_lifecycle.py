"""
tests/test_returns_warranty_lifecycle.py
========================================
Full STATE-MACHINE coverage for the two never-owner-tested back-office flows:
Return Authorizations and Warranty Claims. The per-transition happy paths are
covered in test_returns_ra.py / test_warranty.py; this file is the authoritative
end-to-end walk PLUS the guards those files miss — that illegal / out-of-order
transitions are REJECTED, so the lifecycle can't be skipped (exactly what the
owner's §8 acceptance will probe).

  RA:        DRAFT → OPEN → RECEIVED → CLOSED
  Warranty:  DRAFT → SUBMITTED_TO_VENDOR → VENDOR_APPROVED → CUSTOMER_CREDITED → CLOSED
             DRAFT → SUBMITTED_TO_VENDOR → VENDOR_DENIED  → CUSTOMER_NOTIFIED → CLOSED

Services are driven with current_user_id=None (bypasses the ISSUE_CREDIT_MEMO
gate on close_ra / credit_customer), mirroring test_returns_ra / test_warranty.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_returns_warranty_lifecycle.py -v
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.constants import (
    ProductStatus, RAStatus, ReturnDisposition, WarrantyDecision,
    WarrantyResolution, WarrantyStatus,
)
from app.main import app
from app.models.customer import Customer
from app.models.product import Product
from app.models.vendor import Vendor
from app.services.ra_service import RAService
from app.services.warranty_service import WarrantyService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_SEQ = {"n": 0}


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


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


def _customer(db) -> Customer:
    c = Customer(company_name=f"Life Cust {_uniq()}", contact_name="QA", email="life@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db) -> Vendor:
    v = Vendor(name=f"Life Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db, qty_on_hand=5) -> Product:
    p = Product(sku=f"LIFE-{_uniq():04d}", title="Life Part", description="Life Part",
                cost=20.0, markup_pct=50.0, qty_on_hand=qty_on_hand,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── RA helpers ───────────────────────────────────────────────────────────────

def _new_ra(db):
    cust, prod = _customer(db), _product(db)
    ra = RAService(db, None).create_ra(
        customer_id=cust.id, reason="Defective",
        lines=[{"product_id": prod.id, "description": "Life Part", "qty": 2,
                "unit_price": 40.0, "restocking_fee": 0.0,
                "disposition": ReturnDisposition.RETURN_TO_STOCK}],
    )
    db.refresh(ra)
    return ra


def _receive_all(svc, ra):
    svc.receive_goods(ra.id, line_updates=[{
        "line_id": ra.lines[0].id, "disposition": ReturnDisposition.RETURN_TO_STOCK,
        "qty_returned_to_stock": 2, "condition_notes": "ok"}])


# ════════════════════════════════════════════════════════════════════════════
# RA — full state chain + guards
# ════════════════════════════════════════════════════════════════════════════

def test_ra_state_chain_draft_open_received_closed(db):
    svc = RAService(db, None)
    ra = _new_ra(db)
    assert ra.status == RAStatus.DRAFT
    svc.approve_ra(ra.id);   db.refresh(ra); assert ra.status == RAStatus.OPEN
    _receive_all(svc, ra);   db.refresh(ra); assert ra.status == RAStatus.RECEIVED
    svc.close_ra(ra.id);     db.refresh(ra); assert ra.status == RAStatus.CLOSED


def test_ra_guards_reject_out_of_order_transitions(db):
    svc = RAService(db, None)

    # close before received → rejected (close requires RECEIVED/OPEN, not DRAFT)
    draft = _new_ra(db)
    with pytest.raises(ValueError):
        svc.close_ra(draft.id)
    db.rollback()

    # a fully CLOSED RA can't be re-approved or re-received
    ra = _new_ra(db)
    svc.approve_ra(ra.id); _receive_all(svc, ra); svc.close_ra(ra.id)
    db.refresh(ra); assert ra.status == RAStatus.CLOSED
    with pytest.raises(ValueError):
        svc.approve_ra(ra.id)          # approve requires DRAFT
    db.rollback()
    with pytest.raises(ValueError):
        svc.receive_goods(ra.id, line_updates=[])   # receive requires OPEN/DRAFT
    db.rollback()


# ════════════════════════════════════════════════════════════════════════════
# Warranty — both full state chains + guards
# ════════════════════════════════════════════════════════════════════════════

def _new_claim(db):
    cust, ven, prod = _customer(db), _vendor(db), _product(db)
    claim = WarrantyService(db, None).create_claim(
        customer_id=cust.id, invoice_id=None, vendor_id=ven.id,
        failure_description="unit failed",
        lines=[{"product_id": prod.id, "qty_claimed": 1, "credit_amount": 0.0}],
    )
    db.refresh(claim)
    return claim


def _approve(svc, claim, credit=100.0):
    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.APPROVED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 1, "credit_amount": credit,
                           "resolution": WarrantyResolution.CREDIT}])


def _deny(svc, claim):
    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.DENIED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 0, "credit_amount": 0.0,
                           "resolution": "denied"}])


def test_warranty_state_chain_approve_credit_close(db):
    svc = WarrantyService(db, None)
    claim = _new_claim(db)
    assert claim.status == WarrantyStatus.DRAFT
    svc.submit_to_vendor(claim.id); db.refresh(claim)
    assert claim.status == WarrantyStatus.SUBMITTED_TO_VENDOR
    _approve(svc, claim);           db.refresh(claim)
    assert claim.status == WarrantyStatus.VENDOR_APPROVED
    svc.credit_customer(claim.id);  db.refresh(claim)
    assert claim.status == WarrantyStatus.CUSTOMER_CREDITED
    svc.close_claim(claim.id);      db.refresh(claim)
    assert claim.status == WarrantyStatus.CLOSED


def test_warranty_state_chain_deny_notify_close(db):
    svc = WarrantyService(db, None)
    claim = _new_claim(db)
    svc.submit_to_vendor(claim.id)
    _deny(svc, claim);              db.refresh(claim)
    assert claim.status == WarrantyStatus.VENDOR_DENIED
    svc.notify_customer_of_denial(claim.id, notes="out of warranty"); db.refresh(claim)
    assert claim.status == WarrantyStatus.CUSTOMER_NOTIFIED
    svc.close_claim(claim.id);      db.refresh(claim)
    assert claim.status == WarrantyStatus.CLOSED


def test_warranty_guards_reject_out_of_order_transitions(db):
    svc = WarrantyService(db, None)

    # vendor decision before submit → rejected (requires SUBMITTED_TO_VENDOR)
    draft = _new_claim(db)
    with pytest.raises(ValueError):
        _approve(svc, draft)
    db.rollback()

    # credit before approval → rejected (credit requires VENDOR_APPROVED)
    submitted = _new_claim(db)
    svc.submit_to_vendor(submitted.id)
    with pytest.raises(ValueError):
        svc.credit_customer(submitted.id)
    db.rollback()

    # can't re-submit an already-submitted claim (submit requires DRAFT)
    submitted2 = _new_claim(db)
    svc.submit_to_vendor(submitted2.id)
    with pytest.raises(ValueError):
        svc.submit_to_vendor(submitted2.id)
    db.rollback()

    # notify-of-denial on an APPROVED claim → rejected (requires VENDOR_DENIED)
    approved = _new_claim(db)
    svc.submit_to_vendor(approved.id); _approve(svc, approved)
    with pytest.raises(ValueError):
        svc.notify_customer_of_denial(approved.id, notes="wrong path")
    db.rollback()
