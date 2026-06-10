"""
tests/test_r1_core_money.py
===========================
R1-9 — core money fixes (service half).

  (1) Vendor-denial chargeback: when a vendor DENIES a core (or shorts the
      credit) and staff resolves CHARGED_TO_CUSTOMER, the account credit issued
      by record_customer_return / complete_inspection is REVERSED through
      CRMService.deduct_credit(allow_negative=True). Idempotent (a second call
      never double-deducts); does nothing when no credit was ever issued;
      CHECK refunds are not auto-reversed.
  (2) create_core_charge stamps CoreCharge.credit_invoice_id with the
      originating invoice so printed core slips (CoreSlip.invoice_id is copied
      from it in create_core_slip) carry an invoice reference.

Fixture pattern mirrors tests/test_cores_lifecycle.py: module-isolated
in-memory engine (conftest fresh_engine/activate) + TestClient startup, which
seeds the CoreLocation rows and admin user the lifecycle paths expect.
Services run with current_user_id=None (system event — permission gates pass).
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
    """Module client. Entering the context runs startup against _ENGINE,
    seeding CoreLocation rows + the admin user."""
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
    c = Customer(company_name=f"R1 Core Cust {_uniq()}", contact_name="QA",
                 email="r1core@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"R1 Core Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"R1CORE-{_uniq():04d}", title="Core Part", description="Core Part",
                cost=30.0, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _invoice(db, customer):
    from app.constants import InvoiceStatus
    from app.models.invoice import Invoice
    inv = Invoice(invoice_number=f"INV-R1-{_uniq()}", customer_id=customer.id,
                  status=InvoiceStatus.DRAFT)
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _make_core(db, *, customer, vendor, product, qty=1, cust_charge=50.0,
               vend_charge=30.0):
    """Create a CoreCharge directly (mirrors what invoice-finalize auto-creates)."""
    from app.models.core import CoreCharge
    from app.constants import CoreDirection, CoreStatus
    core = CoreCharge(
        product_id=product.id, customer_id=customer.id, vendor_id=vendor.id,
        qty_charged=qty, qty_returned=0,
        customer_unit_charge=cust_charge, vendor_unit_charge=vend_charge,
        direction=CoreDirection.CUSTOMER_OWES_RETURN, status=CoreStatus.OPEN,
    )
    db.add(core); db.commit(); db.refresh(core)
    return core


def _svc(db):
    from app.services.core_service import CoreService
    return CoreService(db, current_user_id=None)


def _accepted_return(db, core, qty=1):
    from app.constants import CoreInspectionOutcome
    _svc(db).record_customer_return(
        core.id, qty_returned=qty,
        inspection_outcome=CoreInspectionOutcome.ACCEPTED)


# ===========================================================================
# (2) credit_invoice_id — invoice link on the core + the printed slip
# ===========================================================================

def test_create_core_charge_sets_credit_invoice_id(db):
    cust = _customer(db); prod = _product(db)
    inv = _invoice(db, cust)

    core = _svc(db).create_core_charge(
        invoice_id=inv.id, invoice_line_id=None, product_id=prod.id, qty=1,
        customer_unit_charge=50.0, vendor_unit_charge=30.0)
    db.commit()

    assert core.credit_invoice_id == inv.id


def test_core_slip_carries_invoice_reference(db):
    """CoreSlip.invoice_id is copied from core.credit_invoice_id — before R1-9
    it was always NULL because credit_invoice_id was never written."""
    from app.constants import CoreInspectionOutcome
    cust = _customer(db); prod = _product(db)
    inv = _invoice(db, cust)

    svc = _svc(db)
    core = svc.create_core_charge(
        invoice_id=inv.id, invoice_line_id=None, product_id=prod.id, qty=1,
        customer_unit_charge=50.0, vendor_unit_charge=30.0)
    db.commit()
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)

    slip = svc.create_core_slip(core.id)
    assert slip.invoice_id == inv.id


# ===========================================================================
# (1) vendor denial + CHARGED_TO_CUSTOMER — credit clawed back exactly once
# ===========================================================================

def test_denial_charged_to_customer_reverses_credit(db):
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=50.0)
    start = cust.credit_balance

    svc = _svc(db)
    _accepted_return(db, core)
    db.refresh(cust)
    assert cust.credit_balance == start + 50.0  # credit issued on acceptance
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-1")

    svc.record_vendor_denial(core.id, denial_reason="cracked housing",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)

    db.refresh(cust)
    assert cust.credit_balance == start, \
        "CHARGED_TO_CUSTOMER must claw back the issued account credit"


def test_denial_chargeback_is_idempotent(db):
    """A second denial call AND a later credit-difference call with the same
    resolution must not deduct again."""
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=50.0)
    start = cust.credit_balance

    svc = _svc(db)
    _accepted_return(db, core)
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-2")
    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start  # deducted once

    # second denial — no double deduction
    svc.record_vendor_denial(core.id, denial_reason="cracked (resubmit)",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start

    # credit-difference resolution after the denial — still no double deduction
    svc.process_vendor_credit_difference(
        core.id, actual_credit=0.0,
        resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start


def test_denial_no_deduction_when_no_credit_issued(db):
    """HOLD return = no credit issued — CHARGED_TO_CUSTOMER must not deduct."""
    from app.constants import CoreDenialResolution, CoreInspectionOutcome
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=50.0)
    start = cust.credit_balance

    svc = _svc(db)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.HOLD)
    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)

    db.refresh(cust)
    assert cust.credit_balance == start, \
        "no credit was ever issued — nothing to charge back"


def test_denial_absorbed_by_jaks_leaves_credit_alone(db):
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=50.0)
    start = cust.credit_balance

    svc = _svc(db)
    _accepted_return(db, core)
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-3")
    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.ABSORBED_BY_JAKS)

    db.refresh(cust)
    assert cust.credit_balance == start + 50.0, \
        "ABSORBED_BY_JAKS keeps the customer's credit — JAKS eats the loss"


def test_chargeback_goes_negative_when_credit_already_spent(db):
    """The customer spent the credit before the vendor denied the core — the
    chargeback still posts in full, leaving a negative balance (they owe it)."""
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=50.0)

    svc = _svc(db)
    _accepted_return(db, core)
    db.refresh(cust)
    # simulate the credit being spent down to $10 (e.g. applied to an invoice)
    cust.credit_balance = 10.0
    db.commit()

    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)

    db.refresh(cust)
    assert cust.credit_balance == -40.0


def test_credit_difference_charged_to_customer_reverses_once(db):
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=2,
                      cust_charge=50.0, vend_charge=30.0)
    start = cust.credit_balance

    svc = _svc(db)
    _accepted_return(db, core, qty=2)   # credit issued: 2 × 50 = 100
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-4")

    # expected vendor credit 60, vendor paid 0 — shortfall 60 passes to the
    # customer (owner decision 2026-06-10: charge the shortfall, never the
    # whole credit) — customer keeps 100 - 60 = 40 of the issued credit
    svc.process_vendor_credit_difference(
        core.id, actual_credit=0.0,
        resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start + 40.0

    # second call — idempotent
    svc.process_vendor_credit_difference(
        core.id, actual_credit=0.0,
        resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start + 40.0


def test_partial_shortfall_charges_only_the_shortfall(db):
    """Vendor pays 40 of the expected 60 — only the $20 shortfall is pulled
    back from the customer's issued credit; an outright denial via
    record_vendor_denial still reverses in full (covered elsewhere)."""
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=2,
                      cust_charge=50.0, vend_charge=30.0)
    start = cust.credit_balance

    svc = _svc(db)
    _accepted_return(db, core, qty=2)   # credit issued: 2 × 50 = 100
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-SF-1")

    svc.process_vendor_credit_difference(
        core.id, actual_credit=40.0,
        resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start + 80.0, \
        "only the 20.00 vendor shortfall comes back from the customer"


def test_no_shortfall_means_no_chargeback(db):
    """Vendor paid in full — CHARGED_TO_CUSTOMER has nothing to pass on."""
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod,
                      cust_charge=50.0, vend_charge=30.0)
    start = cust.credit_balance

    svc = _svc(db)
    _accepted_return(db, core)
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-SF-2")

    svc.process_vendor_credit_difference(
        core.id, actual_credit=30.0,
        resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start + 50.0, \
        "no shortfall → customer keeps the full issued credit"


def test_check_refund_not_auto_reversed(db):
    """Credit issued as a CHECK refund (money already left as a Payment row) —
    the chargeback must NOT touch credit_balance; Phase 2 owns that recovery."""
    from app.constants import CoreCreditMethod, CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=50.0)
    # qty_returned > 0 without record_customer_return so no account credit exists
    core.qty_returned = 1
    db.commit()
    start = cust.credit_balance

    svc = _svc(db)
    svc.issue_core_credit(core.id, credit_method=CoreCreditMethod.CHECK,
                          check_number="CHK-R1-1")
    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)

    db.refresh(cust)
    assert cust.credit_balance == start


def test_chargeback_after_hold_then_accept_inspection(db):
    """HOLD → complete_inspection(ACCEPTED) issues a deferred credit (and now
    stamps credit_issued_at) — a later denial chargeback must find and reverse it."""
    from app.constants import CoreDenialResolution, CoreInspectionOutcome
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=40.0)
    start = cust.credit_balance

    svc = _svc(db)
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.HOLD)
    svc.complete_inspection(core.id, final_outcome=CoreInspectionOutcome.ACCEPTED)
    db.refresh(cust)
    assert cust.credit_balance == start + 40.0  # deferred credit applied

    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-5")
    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start


def test_partial_return_chargeback_only_issued_amount(db):
    """2 charged, only 1 returned/credited — chargeback reverses exactly the
    credit that was issued (1 × 50), not the full charge."""
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, qty=2,
                      cust_charge=50.0)
    start = cust.credit_balance

    svc = _svc(db)
    _accepted_return(db, core, qty=1)
    db.refresh(cust)
    assert cust.credit_balance == start + 50.0

    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start


# ===========================================================================
# (3) Verifier edge — second return→denial cycle must net out the first
#     chargeback (negative event), never double-deduct round one.
# ===========================================================================

def test_second_denial_cycle_nets_prior_chargeback(db):
    """2-core charge: +50 credit, -50 chargeback, +50 second return,
    second chargeback must deduct only the NEW 50 (sum of all positive
    events minus already-reversed), leaving the customer at net 0 —
    not -50 as a raw sum-of-events would."""
    from app.constants import CoreDenialResolution
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod,
                      qty=2, cust_charge=50.0)
    start = cust.credit_balance
    svc = _svc(db)

    # Cycle 1: return one core, credit issued, vendor denies, charged back.
    _accepted_return(db, core)
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-NET-1")
    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start

    # Cycle 2: customer returns the second core (re-stamps ACCOUNT_CREDIT),
    # vendor denies again.
    _accepted_return(db, core)
    db.refresh(cust)
    assert cust.credit_balance == start + 50.0
    svc.record_vendor_denial(core.id, denial_reason="cracked again",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)
    db.refresh(cust)
    assert cust.credit_balance == start, \
        "second chargeback must deduct only the second 50, not 100"


def test_chargeback_negative_event_excluded_from_metrics(db):
    """core_credits_issued reads GROSS issued credit — the negative
    chargeback event must not net the dashboard tile down."""
    from app.constants import CoreDenialResolution
    from app.services.core_metrics_service import CoreMetricsService
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    core = _make_core(db, customer=cust, vendor=ven, product=prod, cust_charge=50.0)
    svc = _svc(db)

    before = CoreMetricsService(db).dashboard_metrics()["core_credits_issued"]
    _accepted_return(db, core)
    svc.submit_to_vendor(core.id, tracking_number="1Z-R1-NET-2")
    svc.record_vendor_denial(core.id, denial_reason="cracked",
                             resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER)

    after = CoreMetricsService(db).dashboard_metrics()["core_credits_issued"]
    assert after == before + 50.0, \
        "tile shows gross issued credit; chargeback must not subtract"
