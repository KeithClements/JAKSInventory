"""
tests/test_po_core_return.py
============================
Feature: vendor core returns driven from the Purchase Order.

  - The PO workspace surfaces cores ready to ship back to THIS PO's vendor and
    a print/mark-shipped panel ("Core Returns to Vendor").
  - POST /purchase-orders/{id}/core-return batches the selected ready cores into
    a VCR (reusing the cores ledger) and redirects to the printable slip
    (?copies=both → vendor copy + office copy).
  - POST /purchase-orders/{id}/core-return/{vcr_id}/ship marks it shipped with
    OPTIONAL tracking (print now, ship later), staying on the PO.
  - /cores/vcr/{id}/print?copies=both renders two copies; the default print is
    unchanged (single vendor copy).

Fixture pattern mirrors tests/test_r2_vcr_batch.py.
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


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"PO-CR Cust {_uniq()}", contact_name="QA",
                 email=f"pocr{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"PO-CR Vendor {_uniq()}", is_active=True)
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"POCR-{_uniq():04d}", title="Reman Turbo", description="Reman Turbo",
                cost=30.0, markup_pct=50.0, qty_on_hand=0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _po(db, vendor, product, *, status=None):
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder, POLine
    po = PurchaseOrder(po_number=f"PO-CR-{_uniq()}", vendor_id=vendor.id,
                       status=status or POStatus.SENT)
    db.add(po); db.commit(); db.refresh(po)
    line = POLine(po_id=po.id, product_id=product.id, description=product.title,
                  qty_ordered=1, unit_cost=30.0, core_charge_per_unit=30.0)
    db.add(line); db.commit(); db.refresh(po)
    return po


def _ready_core(db, *, customer, vendor, product, qty=1, vend_charge=30.0):
    """Core fully returned + accepted → ready-to-ship stage (RETURNED/PENDING)."""
    from app.models.core import CoreCharge
    from app.constants import CoreDirection, CoreStatus, CoreInspectionOutcome
    from app.services.core_service import CoreService
    core = CoreCharge(
        product_id=product.id, customer_id=customer.id, vendor_id=vendor.id,
        qty_charged=qty, qty_returned=0,
        customer_unit_charge=50.0, vendor_unit_charge=vend_charge,
        direction=CoreDirection.CUSTOMER_OWES_RETURN, status=CoreStatus.OPEN,
    )
    db.add(core); db.commit(); db.refresh(core)
    CoreService(db, current_user_id=None).record_customer_return(
        core.id, qty_returned=qty, inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    db.refresh(core)
    return core


# ===========================================================================
# PO workspace surfaces the panel
# ===========================================================================

def test_po_workspace_shows_core_returns_panel(client, db):
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    po = _po(db, ven, prod)
    core = _ready_core(db, customer=cust, vendor=ven, product=prod)

    resp = client.get(f"/purchase-orders/{po.id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Core Returns to Vendor" in body
    assert prod.sku in body
    assert f'name="core_ids" value="{core.id}"' in body


def test_panel_hidden_when_no_cores_and_no_vcrs(client, db):
    ven = _vendor(db); prod = _product(db)
    po = _po(db, ven, prod)  # vendor has no ready cores and no VCRs

    resp = client.get(f"/purchase-orders/{po.id}")
    assert resp.status_code == 200
    assert "Core Returns to Vendor" not in resp.text


# ===========================================================================
# Create core return from the PO → DRAFT VCR + redirect to print (both copies)
# ===========================================================================

def test_po_create_core_return_batches_and_redirects(client, db):
    from app.constants import VCRStatus
    from app.models.core import VendorCoreReturn
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    po = _po(db, ven, prod)
    core = _ready_core(db, customer=cust, vendor=ven, product=prod, vend_charge=40.0)

    r = client.post(f"/purchase-orders/{po.id}/core-return",
                    data={"core_ids": [str(core.id)]}, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/cores/vcr/") and "copies=both" in loc

    db.expire_all()
    vcr = (
        db.query(VendorCoreReturn)
        .filter(VendorCoreReturn.vendor_id == ven.id)
        .order_by(VendorCoreReturn.id.desc())
        .first()
    )
    assert vcr is not None
    assert vcr.status == VCRStatus.DRAFT
    assert vcr.expected_credit == 40.0
    db.refresh(core)
    assert core.vcr_id == vcr.id


# ===========================================================================
# Mark shipped from the PO — tracking is OPTIONAL (print now, ship later)
# ===========================================================================

def test_po_core_return_ship_without_tracking(client, db):
    from app.constants import VCRStatus
    from app.models.core import VendorCoreReturn
    from app.services.core_service import CoreService
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    po = _po(db, ven, prod)
    core = _ready_core(db, customer=cust, vendor=ven, product=prod)
    vcr = CoreService(db, current_user_id=None).create_vcr(ven.id, [core.id])

    # No tracking supplied — must still succeed (DRAFT → SHIPPED)
    r = client.post(f"/purchase-orders/{po.id}/core-return/{vcr.id}/ship",
                    data={}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/purchase-orders/{po.id}")
    assert "ok=" in r.headers["location"]

    db.expire_all()
    vcr = db.query(VendorCoreReturn).filter(VendorCoreReturn.id == vcr.id).first()
    assert vcr.status == VCRStatus.SHIPPED
    assert vcr.tracking_number == ""        # left blank, no error
    assert vcr.shipped_at is not None


def test_po_core_return_ship_with_tracking(client, db):
    from app.constants import VCRStatus
    from app.models.core import VendorCoreReturn
    from app.services.core_service import CoreService
    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    po = _po(db, ven, prod)
    core = _ready_core(db, customer=cust, vendor=ven, product=prod)
    vcr = CoreService(db, current_user_id=None).create_vcr(ven.id, [core.id])

    r = client.post(f"/purchase-orders/{po.id}/core-return/{vcr.id}/ship",
                    data={"tracking_number": "1Z-PO-1", "rma_number": "RMA-9"},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    vcr = db.query(VendorCoreReturn).filter(VendorCoreReturn.id == vcr.id).first()
    assert vcr.status == VCRStatus.SHIPPED
    assert vcr.tracking_number == "1Z-PO-1"
    assert vcr.rma_number == "RMA-9"


# Note: the vendor-slip "both copies" print is covered (PO-independently) in
# tests/test_vcr_print_copies.py.
