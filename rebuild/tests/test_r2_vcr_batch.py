"""
tests/test_r2_vcr_batch.py
==========================
R2 — Vendor Core Return (VCR) batch flow.

  (1) create_vcr() batches ready-to-ship cores onto one VendorCoreReturn:
      mints VCR-YYYY-XXXX via the settings counter, links cores (vcr_id),
      snapshots one VendorCoreReturnLine per core (the print doc renders
      vcr.lines) and computes expected_credit = Σ vendor_unit_charge × qty.
      Rejects mixed-vendor, not-ready, and already-batched cores.
  (2) ship_vcr() — DRAFT → SHIPPED + tracking, every core SHIPPED_TO_VENDOR.
  (3) record_vcr_vendor_decision() reuses the SINGLE-CORE money paths per core
      (record_vendor_acceptance / record_vendor_denial) so the double-credit
      guards and the R1-9 chargeback stay the single source of truth.

Fixture pattern mirrors tests/test_r1_core_money.py: module-isolated in-memory
engine (conftest fresh_engine/activate) + TestClient startup, which seeds the
CoreLocation rows and admin user. Services run with current_user_id=None
(system event — permission gates pass).
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
# Seed helpers (mirrors test_r1_core_money.py)
# ---------------------------------------------------------------------------

_SEQ = {"n": 0}


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"R2 VCR Cust {_uniq()}", contact_name="QA",
                 email=f"r2vcr{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"R2 VCR Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"R2VCR-{_uniq():04d}", title="Core Part", description="Core Part",
                cost=30.0, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _make_core(db, *, customer, vendor, product, qty=1, cust_charge=50.0,
               vend_charge=30.0):
    """Create a CoreCharge directly (mirrors what invoice-finalize auto-creates).
    vendor=None leaves vendor_id unset (real customer cores are created that way)."""
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


def _accepted_return(db, core, qty=None):
    from app.constants import CoreInspectionOutcome
    _svc(db).record_customer_return(
        core.id, qty_returned=(qty if qty is not None else core.qty_charged),
        inspection_outcome=CoreInspectionOutcome.ACCEPTED)


def _ready_core(db, *, customer, vendor, product, qty=1, cust_charge=50.0,
                vend_charge=30.0):
    """Core fully returned + accepted → ready-to-ship stage (RETURNED/PENDING)."""
    core = _make_core(db, customer=customer, vendor=vendor, product=product,
                      qty=qty, cust_charge=cust_charge, vend_charge=vend_charge)
    _accepted_return(db, core)
    db.refresh(core)
    return core


# ===========================================================================
# (1) create_vcr — number, links, lines, expected credit, validation
# ===========================================================================

def test_create_vcr_mints_number_links_cores_computes_credit(db):
    from app.constants import VCRStatus, VCRLineOutcome
    cust = _customer(db); ven = _vendor(db)
    c1 = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                     qty=1, vend_charge=30.0)
    # vendor_id=None core adopts the VCR's vendor when batched
    c2 = _ready_core(db, customer=cust, vendor=None, product=_product(db),
                     qty=2, vend_charge=40.0)

    vcr = _svc(db).create_vcr(ven.id, [c1.id, c2.id], notes="first PAI box")

    assert _VCR_NUMBER_RE.match(vcr.vcr_number), vcr.vcr_number
    assert vcr.status == VCRStatus.DRAFT
    assert vcr.vendor_id == ven.id
    assert vcr.expected_credit == 110.0  # 1×30 + 2×40

    db.refresh(c1); db.refresh(c2)
    assert c1.vcr_id == vcr.id and c2.vcr_id == vcr.id
    assert c2.vendor_id == ven.id, "vendor-less core adopts the VCR vendor"

    # One snapshot line per core — the print doc renders vcr.lines
    lines = sorted(vcr.lines, key=lambda ln: ln.core_charge_id)
    assert [ln.core_charge_id for ln in lines] == sorted([c1.id, c2.id])
    by_core = {ln.core_charge_id: ln for ln in lines}
    assert by_core[c1.id].qty == 1 and by_core[c1.id].expected_unit_credit == 30.0
    assert by_core[c2.id].qty == 2 and by_core[c2.id].expected_unit_credit == 40.0
    assert by_core[c1.id].part_number == c1.product.sku  # no vendor source → SKU
    assert all(ln.vendor_outcome == VCRLineOutcome.PENDING for ln in lines)


def test_create_vcr_numbers_increment(db):
    cust = _customer(db); ven = _vendor(db)
    c1 = _ready_core(db, customer=cust, vendor=ven, product=_product(db))
    c2 = _ready_core(db, customer=cust, vendor=ven, product=_product(db))

    svc = _svc(db)
    v1 = svc.create_vcr(ven.id, [c1.id])
    v2 = svc.create_vcr(ven.id, [c2.id])
    n1 = int(v1.vcr_number.rsplit("-", 1)[1])
    n2 = int(v2.vcr_number.rsplit("-", 1)[1])
    assert n2 == n1 + 1


def test_create_vcr_rejects_mixed_vendor(db):
    cust = _customer(db)
    ven_a = _vendor(db); ven_b = _vendor(db)
    c_a = _ready_core(db, customer=cust, vendor=ven_a, product=_product(db))
    c_b = _ready_core(db, customer=cust, vendor=ven_b, product=_product(db))

    with pytest.raises(ValueError, match="different vendor"):
        _svc(db).create_vcr(ven_a.id, [c_a.id, c_b.id])
    db.rollback()
    db.refresh(c_a); db.refresh(c_b)
    assert c_a.vcr_id is None and c_b.vcr_id is None


def test_create_vcr_rejects_not_ready_cores(db):
    from app.constants import CoreInspectionOutcome
    cust = _customer(db); ven = _vendor(db)
    svc = _svc(db)

    # still awaiting the customer return (OPEN)
    open_core = _make_core(db, customer=cust, vendor=ven, product=_product(db))
    with pytest.raises(ValueError, match="not ready to ship"):
        svc.create_vcr(ven.id, [open_core.id])
    db.rollback()

    # physically received but HOLD — not ready until inspection passes
    held = _make_core(db, customer=cust, vendor=ven, product=_product(db))
    svc.record_customer_return(held.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.HOLD)
    with pytest.raises(ValueError, match="not ready to ship"):
        svc.create_vcr(ven.id, [held.id])
    db.rollback()


def test_create_vcr_rejects_core_already_on_open_vcr(db):
    cust = _customer(db); ven = _vendor(db)
    core = _ready_core(db, customer=cust, vendor=ven, product=_product(db))

    svc = _svc(db)
    first = svc.create_vcr(ven.id, [core.id])
    with pytest.raises(ValueError, match=f"already batched on open VCR {first.vcr_number}"):
        svc.create_vcr(ven.id, [core.id])
    db.rollback()


# ===========================================================================
# (2) ship_vcr — batch shipment in one transaction
# ===========================================================================

def test_ship_vcr_sets_status_tracking_and_core_transitions(db):
    from app.constants import CoreStatus, CoreVendorStatus, VCRStatus
    cust = _customer(db); ven = _vendor(db)
    c1 = _ready_core(db, customer=cust, vendor=ven, product=_product(db))
    c2 = _ready_core(db, customer=cust, vendor=ven, product=_product(db))

    svc = _svc(db)
    vcr = svc.create_vcr(ven.id, [c1.id, c2.id])
    vcr = svc.ship_vcr(vcr.id, tracking_number="1Z-VCR-1", rma_number="RMA-77")

    assert vcr.status == VCRStatus.SHIPPED
    assert vcr.tracking_number == "1Z-VCR-1"
    assert vcr.rma_number == "RMA-77"
    assert vcr.shipped_at is not None

    for c in (c1, c2):
        db.refresh(c)
        assert c.status == CoreStatus.SHIPPED_TO_VENDOR
        assert c.vendor_status == CoreVendorStatus.PENDING
        assert c.core_tracking_number == "1Z-VCR-1"

    # already shipped — can't ship twice
    with pytest.raises(ValueError, match="already shipped"):
        svc.ship_vcr(vcr.id, tracking_number="1Z-VCR-1-AGAIN")
    db.rollback()


# ===========================================================================
# (3) vendor decision — reuses the single-core money paths per core
# ===========================================================================

def test_vcr_decision_accept_all_records_vendor_credits(db):
    from app.constants import (
        CoreStatus, CoreVendorStatus, VCRLineOutcome, VCRStatus, VendorCreditType,
    )
    from app.models.vendor import VendorCredit
    cust = _customer(db); ven = _vendor(db)
    c1 = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                     qty=1, vend_charge=30.0)
    c2 = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                     qty=2, vend_charge=40.0)

    svc = _svc(db)
    vcr = svc.create_vcr(ven.id, [c1.id, c2.id])
    svc.ship_vcr(vcr.id, tracking_number="1Z-VCR-2")
    vcr = svc.record_vcr_vendor_decision(vcr.id, actual_credit=110.0)

    assert vcr.status == VCRStatus.CREDITED
    assert vcr.actual_credit == 110.0
    assert vcr.credit_difference == 0.0
    assert vcr.vendor_decision_at is not None

    # one VendorCredit per accepted core, at the expected per-core amount
    credits = (
        db.query(VendorCredit)
        .filter(VendorCredit.vendor_id == ven.id,
                VendorCredit.credit_type == VendorCreditType.RETURN)
        .all()
    )
    assert sorted(c.amount for c in credits) == [30.0, 80.0]

    for c in (c1, c2):
        db.refresh(c)
        assert c.vendor_status == CoreVendorStatus.ACCEPTED
        assert c.status == CoreStatus.VENDOR_ACCEPTED
    assert all(ln.vendor_outcome == VCRLineOutcome.ACCEPTED for ln in vcr.lines)


def test_vcr_decision_denied_charged_to_customer_chargeback_fires_once(db):
    """Denied core + CHARGED_TO_CUSTOMER flows through record_vendor_denial →
    the R1-9 chargeback claws back the issued account credit EXACTLY once;
    the accepted core in the same batch still gets its vendor credit."""
    from app.constants import (
        CoreDenialResolution, CoreStatus, CoreVendorStatus, VCRStatus,
        VendorCreditType,
    )
    from app.models.vendor import VendorCredit
    cust = _customer(db); ven = _vendor(db)
    good = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                       cust_charge=50.0, vend_charge=30.0)
    bad = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                      cust_charge=50.0, vend_charge=30.0)
    db.refresh(cust)
    start = cust.credit_balance  # both returns credited: includes +100

    svc = _svc(db)
    vcr = svc.create_vcr(ven.id, [good.id, bad.id])
    svc.ship_vcr(vcr.id, tracking_number="1Z-VCR-3")
    vcr = svc.record_vcr_vendor_decision(
        vcr.id, actual_credit=30.0,
        denied_core_ids=[bad.id],
        denial_reason="cracked housing",
        denial_resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER,
    )

    db.refresh(cust)
    assert cust.credit_balance == start - 50.0, \
        "denied core's issued credit clawed back once"
    db.refresh(good); db.refresh(bad)
    assert good.vendor_status == CoreVendorStatus.ACCEPTED
    assert bad.vendor_status == CoreVendorStatus.REJECTED
    assert bad.status == CoreStatus.VENDOR_REJECTED
    assert bad.denial_resolution == CoreDenialResolution.CHARGED_TO_CUSTOMER

    # accepted core got its vendor credit; denied core did not
    credits = (
        db.query(VendorCredit)
        .filter(VendorCredit.vendor_id == ven.id,
                VendorCredit.credit_type == VendorCreditType.RETURN)
        .all()
    )
    assert [c.amount for c in credits] == [30.0]

    assert vcr.status == VCRStatus.CREDITED
    assert vcr.credit_difference == 30.0  # expected 60 - actual 30

    # a second decision is refused — the chargeback cannot fire twice
    with pytest.raises(ValueError, match="already settled"):
        svc.record_vcr_vendor_decision(
            vcr.id, actual_credit=30.0, denied_core_ids=[bad.id],
            denial_reason="cracked housing",
            denial_resolution=CoreDenialResolution.CHARGED_TO_CUSTOMER,
        )
    db.rollback()
    db.refresh(cust)
    assert cust.credit_balance == start - 50.0


def test_vcr_decision_requires_shipped(db):
    cust = _customer(db); ven = _vendor(db)
    core = _ready_core(db, customer=cust, vendor=ven, product=_product(db))

    svc = _svc(db)
    vcr = svc.create_vcr(ven.id, [core.id])
    with pytest.raises(ValueError, match="has not shipped yet"):
        svc.record_vcr_vendor_decision(vcr.id, actual_credit=30.0)
    db.rollback()


def test_vcr_disputed_denial_keeps_vcr_open(db):
    from app.constants import CoreDenialResolution, VCRStatus
    cust = _customer(db); ven = _vendor(db)
    core = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                       vend_charge=30.0)

    svc = _svc(db)
    vcr = svc.create_vcr(ven.id, [core.id])
    svc.ship_vcr(vcr.id, tracking_number="1Z-VCR-4")
    vcr = svc.record_vcr_vendor_decision(
        vcr.id, actual_credit=0.0,
        denied_core_ids=[core.id],
        denial_reason="no core tag",
        denial_resolution=CoreDenialResolution.DISPUTED,
    )
    assert vcr.status == VCRStatus.DISPUTED, \
        "disputed denials keep the VCR open for follow-up (vendor_recoveries tile)"
    assert vcr.credit_difference == 30.0


# ===========================================================================
# Routes — form posts, flash redirects, list page renders the new surfaces
# ===========================================================================

def test_vcr_routes_end_to_end(client, db):
    from app.constants import VCRStatus
    from app.models.core import VendorCoreReturn
    cust = _customer(db); ven = _vendor(db)
    c1 = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                     qty=1, vend_charge=30.0)
    c2 = _ready_core(db, customer=cust, vendor=ven, product=_product(db),
                     qty=2, vend_charge=40.0)
    # stays unbatched so the ready-to-ship checklist (and batch form) still renders
    spare = _ready_core(db, customer=cust, vendor=ven, product=_product(db))

    # create
    r = client.post("/cores/vcr/create",
                    data={"vendor_id": str(ven.id),
                          "core_ids": [str(c1.id), str(c2.id)]},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    db.expire_all()
    vcr = (
        db.query(VendorCoreReturn)
        .filter(VendorCoreReturn.vendor_id == ven.id)
        .order_by(VendorCoreReturn.id.desc())
        .first()
    )
    assert vcr is not None and vcr.expected_credit == 110.0

    # list page renders: dollar tiles, the batch form, and the Open VCRs card
    page = client.get("/cores/")
    assert page.status_code == 200
    assert "Outstanding Core Liability" in page.text
    assert "Open Vendor Core Returns" in page.text
    assert vcr.vcr_number in page.text
    assert 'id="vcr-create-form"' in page.text
    # batched cores leave the checklist; the spare one is still checkable
    assert f'name="core_ids" value="{spare.id}"' in page.text
    assert f'name="core_ids" value="{c1.id}"' not in page.text

    # ship → redirects straight to the print doc for the box
    r = client.post(f"/cores/vcr/{vcr.id}/ship",
                    data={"tracking_number": "1Z-ROUTE-1"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/cores/vcr/{vcr.id}/print"
    assert client.get(r.headers["location"]).status_code == 200

    # vendor decision — accept everything at the expected amount
    r = client.post(f"/cores/vcr/{vcr.id}/vendor-decision",
                    data={"actual_credit": "110.00"},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    db.expire_all()
    vcr = db.query(VendorCoreReturn).filter(VendorCoreReturn.id == vcr.id).first()
    assert vcr.status == VCRStatus.CREDITED
    assert vcr.actual_credit == 110.0
