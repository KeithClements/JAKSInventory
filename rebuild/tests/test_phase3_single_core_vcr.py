"""
tests/test_phase3_single_core_vcr.py
====================================
§23.3 Phase 3 — the single-core "Mark Shipped" path rides the VCR ledger.

CoreService.submit_single_core_to_vendor() wraps create_vcr() + ship_vcr()
for a one-core batch, so a single-core vendor shipment gets the same
VendorCoreReturn document (expected credit, reconciliation, dispute trail)
as a batched box instead of silently bypassing the ledger.

  (1) happy path — creates + ships a one-core VCR; the core transitions
      exactly as the old bare submit did (SHIPPED_TO_VENDOR, tracking,
      In Transit location) but is now linked to a SHIPPED VCR.
  (2) guards — no vendor on file / not ready-to-ship / already batched.
  (3) decision — record_vcr_vendor_decision settles the single core through
      the same money paths (VendorCredit row on acceptance).
  (4) route — POST /cores/{id}/submit-to-vendor redirects to the VCR print
      doc on success, and back to /cores/ with the error on failure.

Fixture pattern mirrors tests/test_r2_vcr_batch.py (module-isolated engine,
TestClient startup seeds CoreLocations + admin, services run as system).
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()

_VCR_NUMBER_RE = re.compile(r"^VCR-\d{4}-\d{4}$")


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


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"P3 SCV Cust {_uniq()}", contact_name="QA",
                 email=f"p3scv{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"P3 SCV Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"P3SCV-{_uniq():04d}", title="Core Part", description="Core Part",
                cost=30.0, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _make_core(db, *, customer, vendor, product, qty=1, cust_charge=50.0,
               vend_charge=30.0):
    from app.models.core import CoreCharge
    from app.constants import CoreDirection, CoreStatus
    core = CoreCharge(
        product_id=product.id, customer_id=customer.id,
        vendor_id=(vendor.id if vendor else None),
        qty_charged=qty, qty_returned=0,
        customer_unit_charge=cust_charge, vendor_unit_charge=vend_charge,
        direction=CoreDirection.CUSTOMER_OWES_RETURN, status=CoreStatus.OPEN,
    )
    db.add(core); db.commit(); db.refresh(core)
    return core


def _svc(db):
    from app.services.core_service import CoreService
    return CoreService(db, current_user_id=None)


def _ready_core(db, *, customer, vendor, product, qty=1, cust_charge=50.0,
                vend_charge=30.0):
    from app.constants import CoreInspectionOutcome
    core = _make_core(db, customer=customer, vendor=vendor, product=product,
                      qty=qty, cust_charge=cust_charge, vend_charge=vend_charge)
    _svc(db).record_customer_return(
        core.id, qty_returned=qty,
        inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    db.refresh(core)
    return core


# ===========================================================================
# (1) Happy path — one-core VCR, shipped, core transitions intact
# ===========================================================================

def test_single_core_submit_creates_shipped_vcr(db):
    from app.constants import CoreStatus, CoreVendorStatus, VCRStatus, VCRLineOutcome
    cust = _customer(db); ven = _vendor(db)
    core = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                       qty=2, vend_charge=40.0)

    vcr = _svc(db).submit_single_core_to_vendor(core.id, tracking_number="1Z-P3-1")

    assert _VCR_NUMBER_RE.match(vcr.vcr_number), vcr.vcr_number
    assert vcr.status == VCRStatus.SHIPPED
    assert vcr.vendor_id == ven.id
    assert vcr.tracking_number == "1Z-P3-1"
    assert vcr.expected_credit == 80.0  # 2 × $40

    assert len(vcr.lines) == 1
    line = vcr.lines[0]
    assert line.core_charge_id == core.id
    assert line.qty == 2 and line.expected_unit_credit == 40.0
    assert line.vendor_outcome == VCRLineOutcome.PENDING

    # The core transitions exactly as the old bare submit did
    db.refresh(core)
    assert core.vcr_id == vcr.id
    assert core.status == CoreStatus.SHIPPED_TO_VENDOR
    assert core.vendor_status == CoreVendorStatus.PENDING
    assert core.core_tracking_number == "1Z-P3-1"
    assert core.location is not None and core.location.name == "In Transit to Vendor"


# ===========================================================================
# (2) Guards
# ===========================================================================

def test_single_core_submit_requires_vendor(db):
    from app.constants import CoreStatus
    cust = _customer(db)
    core = _ready_core(db, customer=cust, vendor=None, product=_product(db))

    with pytest.raises(ValueError, match="no vendor"):
        _svc(db).submit_single_core_to_vendor(core.id)

    db.rollback()
    db.refresh(core)
    assert core.status == CoreStatus.RETURNED  # unchanged
    assert core.vcr_id is None


def test_single_core_submit_rejects_not_ready(db):
    """An OPEN core (customer hasn't returned it) can't ship — create_vcr's
    ready-to-ship guard applies to the single-core path too."""
    from app.constants import CoreStatus
    cust = _customer(db); ven = _vendor(db)
    core = _make_core(db, customer=cust, vendor=ven, product=_product(db))

    with pytest.raises(ValueError, match="not ready to ship"):
        _svc(db).submit_single_core_to_vendor(core.id)

    db.rollback()
    db.refresh(core)
    assert core.status == CoreStatus.OPEN


def test_single_core_submit_rejects_already_batched(db):
    cust = _customer(db); ven = _vendor(db)
    core = _ready_core(db, customer=cust, vendor=ven, product=_product(db))
    _svc(db).create_vcr(ven.id, [core.id])  # already on an open (DRAFT) VCR

    with pytest.raises(ValueError, match="already batched"):
        _svc(db).submit_single_core_to_vendor(core.id)
    db.rollback()


# ===========================================================================
# (3) Vendor decision settles the single core through the same money paths
# ===========================================================================

def test_vcr_decision_settles_single_core(db):
    from app.constants import CoreStatus, CoreVendorStatus, VCRStatus, VendorCreditType
    from app.models.vendor import VendorCredit
    cust = _customer(db); ven = _vendor(db)
    core = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                       qty=1, vend_charge=30.0)
    vcr = _svc(db).submit_single_core_to_vendor(core.id, tracking_number="1Z-P3-2")

    _svc(db).record_vcr_vendor_decision(vcr.id, actual_credit=30.0)

    db.expire_all()
    from app.models.core import VendorCoreReturn
    vcr = db.query(VendorCoreReturn).filter(VendorCoreReturn.id == vcr.id).first()
    assert vcr.status == VCRStatus.CREDITED
    assert vcr.actual_credit == 30.0 and vcr.credit_difference == 0.0

    db.refresh(core)
    assert core.status == CoreStatus.VENDOR_ACCEPTED
    assert core.vendor_status == CoreVendorStatus.ACCEPTED

    credit = (
        db.query(VendorCredit)
        .filter(VendorCredit.vendor_id == ven.id,
                VendorCredit.credit_type == VendorCreditType.RETURN)
        .order_by(VendorCredit.id.desc())
        .first()
    )
    assert credit is not None and credit.amount == 30.0


# ===========================================================================
# (4) Route — redirects to the VCR print doc / error surface
# ===========================================================================

def test_route_single_core_submit_redirects_to_vcr_print(client, db):
    from app.models.core import CoreCharge
    cust = _customer(db); ven = _vendor(db)
    core = _ready_core(db, customer=cust, vendor=ven, product=_product(db))

    r = client.post(f"/cores/{core.id}/submit-to-vendor",
                    data={"tracking_number": "1Z-P3-ROUTE"},
                    follow_redirects=False)
    assert r.status_code == 303

    db.expire_all()
    core = db.query(CoreCharge).filter(CoreCharge.id == core.id).first()
    assert core.vcr_id is not None
    assert r.headers["location"] == f"/cores/vcr/{core.vcr_id}/print"
    assert client.get(r.headers["location"]).status_code == 200


def test_route_single_core_submit_no_vendor_shows_error(client, db):
    cust = _customer(db)
    core = _ready_core(db, customer=cust, vendor=None, product=_product(db))

    r = client.post(f"/cores/{core.id}/submit-to-vendor",
                    data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
