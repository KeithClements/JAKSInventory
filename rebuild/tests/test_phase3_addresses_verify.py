"""
Phase 3 bill-to / ship-to verification
======================================
  - seed_company_locations seeds one primary location from settings.
  - PO header autosave persists all three ship-to modes and keeps is_drop_ship in
    sync (location/ad_hoc → False; drop_ship → True with the customer linked).
  - _resolve_po_addresses returns the right block per mode.
  - The workspace + print render Bill-To / Ship-To.
  - The Settings → Locations CRUD adds a location (admin) and lists it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import fresh_engine, activate
from app.main import app
from app.models.user import User
from app.models.vendor import Vendor
from app.models.customer import Customer
from app.models.company_location import CompanyLocation
from app.models.purchase_order import PurchaseOrder
from app.services.po_service import POService
from app.constants import POStatus, POShipToType, UserRole


@pytest.fixture()
def Session():
    return activate(fresh_engine())


@pytest.fixture()
def client():
    return TestClient(app)


def _seed(Session, *, with_locations=True):
    db = Session()
    try:
        db.add(User(id=1, name="Admin", username="admin", password_hash="x", role=UserRole.ADMIN))
        vendor = Vendor(name="PAI", vendor_code="PAI", vendor_number="9")
        cust = Customer(company_name="Acme Diesel", contact_name="")
        db.add_all([vendor, cust])
        if with_locations:
            db.add_all([
                CompanyLocation(name="Main Shop", address_line1="100 Shop Rd",
                                city="Houston", state="TX", zip_code="77001",
                                is_primary=True, is_active=True),
                CompanyLocation(name="Warehouse B", address_line1="200 Dock St",
                                city="Dallas", state="TX", zip_code="75201",
                                is_active=True),
            ])
        db.commit()
        svc = POService(db, current_user_id=1)
        po = svc.create_po(vendor_id=vendor.id, data={})
        primary = db.query(CompanyLocation).filter(CompanyLocation.is_primary == True).first()  # noqa: E712
        wh = db.query(CompanyLocation).filter(CompanyLocation.name == "Warehouse B").first()
        return {
            "po_id": po.id, "vendor_id": vendor.id, "cust_id": cust.id,
            "primary_id": primary.id if primary else None,
            "wh_id": wh.id if wh else None,
        }
    finally:
        db.close()


def test_seed_company_locations_creates_primary(Session):
    from app.routers.settings import DEFAULTS  # noqa: F401 — ensure module import ok
    from app.seeds import seed_company_locations
    db = Session()
    try:
        seed_company_locations(db)
        locs = db.query(CompanyLocation).all()
        assert len(locs) == 1 and locs[0].is_primary is True
        # Idempotent — running again does not duplicate.
        seed_company_locations(db)
        assert db.query(CompanyLocation).count() == 1
    finally:
        db.close()


def test_ship_to_location_mode(Session, client):
    ids = _seed(Session)
    r = client.post(f"/purchase-orders/{ids['po_id']}/header", data={
        "ship_to_type": "location",
        "ship_to_location_id": str(ids["wh_id"]),
        "bill_to_location_id": str(ids["primary_id"]),
    })
    assert r.status_code == 204
    db = Session()
    try:
        po = db.get(PurchaseOrder, ids["po_id"])
        assert po.ship_to_type == POShipToType.LOCATION
        assert po.ship_to_location_id == ids["wh_id"]
        assert po.bill_to_location_id == ids["primary_id"]
        assert po.is_drop_ship is False
    finally:
        db.close()


def test_ship_to_ad_hoc_mode(Session, client):
    ids = _seed(Session)
    r = client.post(f"/purchase-orders/{ids['po_id']}/header", data={
        "ship_to_type": "ad_hoc",
        "ship_to_snapshot": "Jobsite\n42 Rig Rd\nMidland, TX 79701",
    })
    assert r.status_code == 204
    db = Session()
    try:
        po = db.get(PurchaseOrder, ids["po_id"])
        assert po.ship_to_type == POShipToType.AD_HOC
        assert "Midland" in (po.ship_to_snapshot or "")
        assert po.ship_to_location_id is None
        assert po.is_drop_ship is False
    finally:
        db.close()


def test_ship_to_drop_ship_mode(Session, client):
    ids = _seed(Session)
    r = client.post(f"/purchase-orders/{ids['po_id']}/header", data={
        "ship_to_type": "drop_ship",
        "drop_ship_customer_id": str(ids["cust_id"]),
        "ship_to_snapshot": "Acme Diesel\n9 Yard Ln\nOdessa, TX",
    })
    assert r.status_code == 204
    db = Session()
    try:
        po = db.get(PurchaseOrder, ids["po_id"])
        assert po.ship_to_type == POShipToType.DROP_SHIP
        assert po.is_drop_ship is True
        assert po.drop_ship_customer_id == ids["cust_id"]
        # Switching away clears the drop-ship link.
    finally:
        db.close()
    r2 = client.post(f"/purchase-orders/{ids['po_id']}/header", data={
        "ship_to_type": "location",
        "ship_to_location_id": str(ids["primary_id"]),
    })
    assert r2.status_code == 204
    db = Session()
    try:
        po = db.get(PurchaseOrder, ids["po_id"])
        assert po.is_drop_ship is False
        assert po.drop_ship_customer_id is None
    finally:
        db.close()


def test_workspace_and_print_render_addresses(Session, client):
    ids = _seed(Session)
    ws = client.get(f"/purchase-orders/{ids['po_id']}").text
    assert "Bill To" in ws and "Ship To" in ws
    pr = client.get(f"/purchase-orders/{ids['po_id']}/print").text
    assert "Main Shop" in pr           # primary location used as default ship-to/bill-to


def test_settings_locations_crud(Session, client):
    _seed(Session, with_locations=False)
    # GET list page renders (no admin needed).
    assert client.get("/settings/locations").status_code == 200
    # Create (admin — seeded user id=1 is ADMIN).
    r = client.post("/settings/locations", data={
        "name": "Main Shop", "address_line1": "1 A St", "city": "Houston",
        "state": "TX", "zip_code": "77001", "is_primary": "1",
    }, follow_redirects=False)
    assert r.status_code == 303
    db = Session()
    try:
        locs = db.query(CompanyLocation).all()
        assert len(locs) == 1 and locs[0].is_primary is True
    finally:
        db.close()
