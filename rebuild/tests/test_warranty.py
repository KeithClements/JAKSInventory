"""
tests/test_warranty.py
======================
Functional integration tests for the Warranty Claim lifecycle
(TESTING_FEEDBACK §5.2). Zero prior functional confirmation.

Strategy: WarrantyService driven directly with current_user_id=None (bypasses
the ISSUE_CREDIT_MEMO gate on credit_customer -> CreditMemoService). Module
TestClient runs startup + serves print/PDF GETs. Both lifecycle branches are
covered: approve -> credit -> close, and deny -> notify -> close.
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


def _uniq():
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"WC Cust {_uniq()}", contact_name="QA",
                 email="wc@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"WC Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"WC-{_uniq():04d}", title="WC Part", description="WC Part",
                cost=20.0, markup_pct=50.0, qty_on_hand=1,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _make_claim(db, *, customer, vendor, product):
    from app.services.warranty_service import WarrantyService
    return WarrantyService(db, None).create_claim(
        customer_id=customer.id, invoice_id=None, vendor_id=vendor.id,
        failure_description="unit failed",
        lines=[{"product_id": product.id, "qty_claimed": 1, "credit_amount": 0.0}],
    )


# ===========================================================================
# §5.2.a — create claim with product lines + credit amounts
# ===========================================================================

def test_create_claim_with_lines(db):
    from app.constants import WarrantyStatus
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    claim = _make_claim(db, customer=cust, vendor=ven, product=prod)
    db.refresh(claim)
    assert claim.status == WarrantyStatus.DRAFT
    assert claim.claim_number.startswith("WC-")
    assert len(claim.claim_lines) == 1
    assert claim.total_credit_amount == 0.0  # totals computed at decision time


# ===========================================================================
# §5.2.b — submit to vendor → vendor decision (approve / deny)
# ===========================================================================

def test_submit_then_vendor_approve(db):
    from app.constants import WarrantyDecision, WarrantyResolution, WarrantyStatus
    from app.services.warranty_service import WarrantyService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    claim = _make_claim(db, customer=cust, vendor=ven, product=prod)
    svc = WarrantyService(db, None)

    svc.submit_to_vendor(claim.id)
    db.refresh(claim); assert claim.status == WarrantyStatus.SUBMITTED_TO_VENDOR

    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.APPROVED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 1, "credit_amount": 100.0,
                           "resolution": WarrantyResolution.CREDIT}])
    db.refresh(claim)
    assert claim.status == WarrantyStatus.VENDOR_APPROVED
    assert claim.total_credit_amount == 100.0


def test_submit_then_vendor_deny(db):
    from app.constants import WarrantyDecision, WarrantyStatus
    from app.services.warranty_service import WarrantyService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    claim = _make_claim(db, customer=cust, vendor=ven, product=prod)
    svc = WarrantyService(db, None)
    svc.submit_to_vendor(claim.id)
    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.DENIED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 0, "credit_amount": 0.0,
                           "resolution": "denied"}])
    db.refresh(claim)
    assert claim.status == WarrantyStatus.VENDOR_DENIED


# ===========================================================================
# §5.2.c — credit customer on approval
# ===========================================================================

def test_credit_customer_on_approval(db):
    from app.constants import (CreditMemoStatus, WarrantyDecision,
                               WarrantyResolution, WarrantyStatus)
    from app.models.credit_memo import CreditMemo
    from app.services.warranty_service import WarrantyService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    start_balance = cust.credit_balance
    svc = WarrantyService(db, None)
    claim = _make_claim(db, customer=cust, vendor=ven, product=prod)
    svc.submit_to_vendor(claim.id)
    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.APPROVED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 1, "credit_amount": 100.0,
                           "resolution": WarrantyResolution.CREDIT}])

    svc.credit_customer(claim.id)

    db.refresh(claim); db.refresh(cust)
    assert claim.status == WarrantyStatus.CUSTOMER_CREDITED
    assert cust.credit_balance == start_balance + 100.0
    cm = (db.query(CreditMemo)
          .filter(CreditMemo.warranty_claim_id == claim.id).first())
    assert cm is not None and cm.status == CreditMemoStatus.APPLIED


# ===========================================================================
# §5.2.d — notify denial / close
# ===========================================================================

def test_deny_notify_close(db):
    from app.constants import WarrantyDecision, WarrantyStatus
    from app.services.warranty_service import WarrantyService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    svc = WarrantyService(db, None)
    claim = _make_claim(db, customer=cust, vendor=ven, product=prod)
    svc.submit_to_vendor(claim.id)
    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.DENIED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 0, "credit_amount": 0.0,
                           "resolution": "denied"}])

    svc.notify_customer_of_denial(claim.id, notes="vendor denied; out of warranty")
    db.refresh(claim); assert claim.status == WarrantyStatus.CUSTOMER_NOTIFIED

    svc.close_claim(claim.id)
    db.refresh(claim); assert claim.status == WarrantyStatus.CLOSED


def test_credited_claim_can_close(db):
    from app.constants import (WarrantyDecision, WarrantyResolution,
                               WarrantyStatus)
    from app.services.warranty_service import WarrantyService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    svc = WarrantyService(db, None)
    claim = _make_claim(db, customer=cust, vendor=ven, product=prod)
    svc.submit_to_vendor(claim.id)
    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.APPROVED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 1, "credit_amount": 75.0,
                           "resolution": WarrantyResolution.CREDIT}])
    svc.credit_customer(claim.id)
    svc.close_claim(claim.id)
    db.refresh(claim); assert claim.status == WarrantyStatus.CLOSED


# ===========================================================================
# §5.2.e — print / PDF render
# ===========================================================================

def test_warranty_print_and_pdf_render(client, db):
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    claim = _make_claim(db, customer=cust, vendor=ven, product=prod)
    assert client.get(f"/warranty/{claim.id}/print").status_code == 200
    assert client.get(f"/warranty/{claim.id}/pdf").status_code == 200
