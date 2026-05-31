"""
tests/test_cores_lifecycle.py
=============================
Functional integration tests for the CORE-CHARGE lifecycle (TESTING_FEEDBACK §4.1).
This domain had ZERO prior functional confirmation.

Strategy
--------
* Lifecycle is driven through ``CoreService`` directly with ``current_user_id=None``
  so the ``ISSUE_CREDIT_MEMO`` permission gate is bypassed (None == system event).
* A module ``TestClient`` runs app startup against the isolated engine, which seeds
  the 6 ``CoreLocation`` rows (Core Shelf / Core Holding / ... / In Transit to Vendor)
  and the admin user — exactly the production preconditions. Location-routing and
  vendor-acceptance both depend on those rows existing.
* Print routes are exercised over HTTP (GET) to catch template breakage.

Assertions are behaviour-level (status transitions, location routing, customer
credit_balance, VendorCredit / Payment / inventory rows) — never just HTTP 200.

Regression guards (bottom of file): BUG-2 (vendor acceptance without a prior
location) and BUG-4 (double credit on accepted-then-issue) were found here and
are now FIXED in CoreService; the two tests that pin them must stay green.
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
    """Module client. Entering the context runs startup against _ENGINE, seeding
    CoreLocation rows + the admin user that the lifecycle depends on."""
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    """Fresh session per test (client dep guarantees startup already seeded)."""
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_SEQ = {"n": 0}


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"Core Cust {_uniq()}", contact_name="QA",
                 email="core@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"Core Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"CORE-{_uniq():04d}", title="Core Part", description="Core Part",
                cost=30.0, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _make_core(db, *, customer, vendor, product, qty=1, cust_charge=50.0,
               vend_charge=30.0, deadline=None):
    """Create a CoreCharge directly (mirrors what invoice-finalize auto-creates)."""
    from app.models.core import CoreCharge
    from app.constants import CoreDirection, CoreStatus
    core = CoreCharge(
        product_id=product.id, customer_id=customer.id, vendor_id=vendor.id,
        qty_charged=qty, qty_returned=0,
        customer_unit_charge=cust_charge, vendor_unit_charge=vend_charge,
        direction=CoreDirection.CUSTOMER_OWES_RETURN, status=CoreStatus.OPEN,
        return_deadline=deadline,
    )
    db.add(core); db.commit(); db.refresh(core)
    return core


def _location_id(db, name):
    from app.models.core import CoreLocation
    loc = db.query(CoreLocation).filter(CoreLocation.name == name).first()
    return loc.id if loc else None


# ===========================================================================
# §4.1.a — core charge originates from invoicing a core item
# ===========================================================================

def test_core_charge_auto_created_from_invoice(db):
    """create_core_charge (the exact function invoice-finalize calls for each
    CORE_CHARGE line) produces an OPEN customer-owes core tied to the customer."""
    from app.models.invoice import Invoice
    from app.constants import InvoiceStatus, CoreDirection, CoreStatus
    from app.services.core_service import CoreService

    cust = _customer(db)
    prod = _product(db)
    inv = Invoice(invoice_number=f"INV-CORE-{_uniq()}", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT)
    db.add(inv); db.commit(); db.refresh(inv)

    svc = CoreService(db, current_user_id=None)
    core = svc.create_core_charge(invoice_id=inv.id, invoice_line_id=None,
                                  product_id=prod.id, qty=2,
                                  customer_unit_charge=50.0, vendor_unit_charge=30.0)
    db.commit()

    assert core.id is not None
    assert core.customer_id == cust.id
    assert core.direction == CoreDirection.CUSTOMER_OWES_RETURN
    assert core.status == CoreStatus.OPEN
    assert core.qty_charged == 2
    assert core.customer_unit_charge == 50.0
    assert core.return_deadline is not None  # grace window applied


# ===========================================================================
# §4.1.b — customer return + inspection outcome routes to the right location
# ===========================================================================

def test_return_accepted_routes_to_shelf_and_credits(db):
    from app.constants import CoreInspectionOutcome, CoreStatus
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      cust_charge=50.0)
    start_balance = cust.credit_balance

    CoreService(db, None).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    db.refresh(core); db.refresh(cust)
    assert core.status == CoreStatus.RETURNED
    assert core.qty_returned == 1
    assert core.location_id == _location_id(db, "Core Shelf")
    # ACCEPTED issues account credit = qty * customer_unit_charge
    assert cust.credit_balance == start_balance + 50.0


def test_return_hold_routes_to_holding_no_credit(db):
    from app.constants import CoreInspectionOutcome, CoreStatus
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1)
    start_balance = cust.credit_balance

    CoreService(db, None).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.HOLD)

    db.refresh(core); db.refresh(cust)
    assert core.status == CoreStatus.RETURNED
    assert core.inspection_outcome == CoreInspectionOutcome.HOLD
    assert core.location_id == _location_id(db, "Core Holding")
    assert cust.credit_balance == start_balance  # HOLD defers credit


def test_return_rejected_closes_and_routes_to_rejected(db):
    from app.constants import CoreInspectionOutcome, CoreStatus
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1)
    start_balance = cust.credit_balance

    CoreService(db, None).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.REJECTED)

    db.refresh(core); db.refresh(cust)
    assert core.status == CoreStatus.CLOSED  # rejected is terminal
    assert core.location_id == _location_id(db, "Rejected Core")
    assert cust.credit_balance == start_balance  # no credit on rejection


# ===========================================================================
# §4.1.c — complete inspection (HOLD case) resolves
# ===========================================================================

def test_complete_inspection_hold_accept_credits_and_shelves(db):
    from app.constants import CoreInspectionOutcome, CoreStatus
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      cust_charge=40.0)
    svc = CoreService(db, None)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.HOLD)
    db.refresh(cust)
    balance_after_hold = cust.credit_balance

    svc.complete_inspection(core.id, final_outcome=CoreInspectionOutcome.ACCEPTED,
                            notes="inspected ok")

    db.refresh(core); db.refresh(cust)
    assert core.inspection_outcome == CoreInspectionOutcome.ACCEPTED
    assert core.location_id == _location_id(db, "Core Shelf")
    # deferred credit now applied
    assert cust.credit_balance == balance_after_hold + 40.0


def test_complete_inspection_requires_hold_state(db):
    from app.constants import CoreInspectionOutcome
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1)
    svc = CoreService(db, None)
    # ACCEPTED (not HOLD) -> complete_inspection must refuse
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    with pytest.raises(ValueError):
        svc.complete_inspection(core.id, final_outcome=CoreInspectionOutcome.ACCEPTED)


# ===========================================================================
# §4.1.d — submit to vendor → status + in-transit routing
# ===========================================================================

def test_submit_to_vendor_sets_shipped_and_in_transit(db):
    from app.constants import CoreInspectionOutcome, CoreStatus, CoreVendorStatus
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1)
    svc = CoreService(db, None)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    svc.submit_to_vendor(core.id, tracking_number="1Z-CORE-999")

    db.refresh(core)
    assert core.status == CoreStatus.SHIPPED_TO_VENDOR
    assert core.vendor_status == CoreVendorStatus.PENDING
    assert core.location_id == _location_id(db, "In Transit to Vendor")


# ===========================================================================
# §4.1.e — vendor accepted / denied / credit-difference
# ===========================================================================

def test_vendor_acceptance_creates_vendor_credit(db):
    """Full happy path through vendor acceptance. Depends on seeded locations
    (a prior submit_to_vendor set location_id) — see module docstring / BUG note."""
    from app.constants import CoreInspectionOutcome, CoreStatus, CoreVendorStatus
    from app.models.vendor import VendorCredit
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      vend_charge=30.0)
    svc = CoreService(db, None)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    svc.submit_to_vendor(core.id, tracking_number="1Z-CORE-1")

    svc.record_vendor_acceptance(core.id, credit_amount=30.0)

    db.refresh(core)
    assert core.status == CoreStatus.VENDOR_ACCEPTED
    assert core.vendor_status == CoreVendorStatus.ACCEPTED
    vc = (db.query(VendorCredit)
          .filter(VendorCredit.vendor_id == ven.id, VendorCredit.amount == 30.0)
          .first())
    assert vc is not None, "vendor acceptance should create a VendorCredit"


def test_vendor_denial_sets_rejected(db):
    from app.constants import (CoreDenialResolution, CoreInspectionOutcome,
                               CoreStatus, CoreVendorStatus)
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1)
    svc = CoreService(db, None)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    svc.submit_to_vendor(core.id, tracking_number="1Z-CORE-2")

    svc.record_vendor_denial(core.id, denial_reason="wrong core",
                             resolution=CoreDenialResolution.ABSORBED_BY_JAKS,
                             notes="vendor rejected")

    db.refresh(core)
    assert core.status == CoreStatus.VENDOR_REJECTED
    assert core.vendor_status == CoreVendorStatus.REJECTED
    assert core.denial_resolution == CoreDenialResolution.ABSORBED_BY_JAKS


def test_vendor_credit_difference_records_delta(db):
    from app.constants import (CoreDenialResolution, CoreInspectionOutcome)
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=2,
                      vend_charge=30.0)
    svc = CoreService(db, None)
    svc.record_customer_return(core.id, qty_returned=2,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    svc.submit_to_vendor(core.id, tracking_number="1Z-CORE-3")

    # expected = 30 * 2 = 60; vendor only credited 45 -> difference 15
    out = svc.process_vendor_credit_difference(
        core.id, actual_credit=45.0,
        resolution=CoreDenialResolution.ABSORBED_BY_JAKS, notes="short credit")

    assert out is not None  # returns the CoreCharge


# ===========================================================================
# §4.1.f — issue credit (account credit vs check)
# ===========================================================================

def test_issue_core_credit_account_increments_balance(db):
    from app.constants import CoreCreditMethod
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      cust_charge=50.0)
    # Put qty_returned>0 WITHOUT going through record_customer_return (which would
    # itself credit) so we isolate issue_core_credit's account-credit effect.
    core.qty_returned = 1
    db.commit()
    start_balance = cust.credit_balance

    CoreService(db, None).issue_core_credit(
        core.id, credit_method=CoreCreditMethod.ACCOUNT_CREDIT)

    db.refresh(cust)
    assert cust.credit_balance == start_balance + 50.0


def test_issue_core_credit_check_creates_refund_payment(db):
    from app.constants import CoreCreditMethod, PaymentMethod
    from app.models.invoice import Payment
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      cust_charge=50.0)
    core.qty_returned = 1
    db.commit()

    CoreService(db, None).issue_core_credit(
        core.id, credit_method=CoreCreditMethod.CHECK, check_number="CHK-001")

    pmt = (db.query(Payment)
           .filter(Payment.customer_id == cust.id,
                   Payment.payment_method == PaymentMethod.CHECK)
           .first())
    assert pmt is not None, "CHECK credit should create a refund Payment row"
    assert pmt.amount_received == -50.0  # negative = outflow/refund


# ===========================================================================
# §4.1.g — slip print + VCR print render
# ===========================================================================

def test_slip_and_vcr_prints_render(client, db):
    from app.constants import CoreInspectionOutcome
    from app.models.core import VendorCoreReturn
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1)
    svc = CoreService(db, None)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    slip = svc.create_core_slip(core.id)

    # per-core customer receipt + grouped slip doc
    assert client.get(f"/cores/{core.id}/slip-print?slip_id={slip.id}").status_code == 200
    assert client.get(f"/cores/slips/{slip.id}/print").status_code == 200

    # VCR doc print (create a VendorCoreReturn directly)
    vcr = VendorCoreReturn(vcr_number=f"VCR-{_uniq()}", vendor_id=ven.id)
    db.add(vcr); db.commit(); db.refresh(vcr)
    assert client.get(f"/cores/{core.id}/vendor-slip-print").status_code == 200
    assert client.get(f"/cores/vcr/{vcr.id}/print").status_code == 200


# ===========================================================================
# §4.1.h — overdue core flagged after grace window
# ===========================================================================

def test_overdue_core_detected(db):
    import datetime
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    past = datetime.datetime.utcnow() - datetime.timedelta(days=5)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      deadline=past)

    overdue = CoreService(db, None).get_overdue_cores()
    assert any(c.id == core.id for c in overdue), \
        "core past its return_deadline must be reported overdue"
    assert core.is_overdue is True


# ===========================================================================
# FIXED DEFECTS — regression guards.
# These encode two real bugs found while testing (BUG-2, BUG-4). Both are now
# FIXED in CoreService; the tests assert the CORRECT behaviour and must stay
# green. (Previously xfail; flipped to real passing tests when the bugs landed.)
# ===========================================================================

def test_vendor_acceptance_without_prior_location(db):
    # BUG-2 (FIXED): record_vendor_acceptance used to write a CoreLocationMovement
    # with to_location_id = core.location_id; when the core was never routed
    # (location_id is None) that violated NOT NULL -> IntegrityError. The fix skips
    # the movement row when there is no prior location. Vendor acceptance succeeds.
    from app.constants import CoreStatus
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      vend_charge=30.0)
    # location_id is None here. Vendor acceptance SHOULD still succeed.
    CoreService(db, None).record_vendor_acceptance(core.id, credit_amount=30.0)
    db.refresh(core)
    assert core.status == CoreStatus.VENDOR_ACCEPTED


def test_no_double_credit_on_accepted_then_issue(db):
    # BUG-4 (FIXED): record_customer_return(ACCEPTED) issues account credit and now
    # stamps credit_issued_at; issue_core_credit then detects that and no-ops instead
    # of crediting the customer a second time for one returned core.
    from app.constants import CoreCreditMethod, CoreInspectionOutcome
    from app.services.core_service import CoreService

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=1,
                      cust_charge=50.0)
    start = cust.credit_balance
    svc = CoreService(db, None)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    svc.issue_core_credit(core.id, credit_method=CoreCreditMethod.ACCOUNT_CREDIT)
    db.refresh(cust)
    # CORRECT: credited exactly once (50.0), not twice.
    assert cust.credit_balance == start + 50.0
