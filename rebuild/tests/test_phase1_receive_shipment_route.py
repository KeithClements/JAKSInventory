"""
tests/test_phase1_receive_shipment_route.py
============================================
MASTER_PLAN §23.3 Phase 1 — multi-PO "Receive Shipment" screen (route/E2E half).

Drives the vendor-scoped receiving screen end to end through the HTTP routes:
  * GET /purchase-orders/receive-shipment            → vendor picker
  * GET /purchase-orders/receive-shipment?vendor_id= → grouped multi-PO form
  * POST /purchase-orders/receive-shipment           → one action receives lines
    across SEVERAL of the vendor's POs; each PO's status advances independently.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.constants import POStatus
from app.main import app
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, POLine
from app.models.vendor import Vendor
from app.services.po_service import POService
from tests.conftest import activate, fresh_engine

_UID = 1


@pytest.fixture()
def Session():
    return activate(fresh_engine())


@pytest.fixture()
def client():
    return TestClient(app)


def _seed_two_pos(Session):
    """One vendor, two SENT POs (line A qty 3, line B qty 5)."""
    db = Session()
    try:
        v = Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9")
        db.add(v); db.flush()
        pa = Product(sku="SKU-A", title="Head Gasket", qty_on_hand=0, qty_on_order=0)
        pb = Product(sku="SKU-B", title="Oil Pump", qty_on_hand=0, qty_on_order=0)
        db.add_all([pa, pb]); db.flush()
        svc = POService(db, current_user_id=_UID)
        po1 = svc.create_po(vendor_id=v.id, data={})
        la = svc.add_line(po_id=po1.id, product_id=pa.id,
                          data={"description": "Head Gasket", "qty_ordered": 3, "unit_cost": 100.0})
        po2 = svc.create_po(vendor_id=v.id, data={})
        lb = svc.add_line(po_id=po2.id, product_id=pb.id,
                          data={"description": "Oil Pump", "qty_ordered": 5, "unit_cost": 200.0})
        po1.status = POStatus.SENT
        po2.status = POStatus.SENT
        db.commit()
        return {"vendor": v.id, "po1": po1.id, "po2": po2.id,
                "la": la.id, "lb": lb.id, "pa": pa.id, "pb": pb.id}
    finally:
        db.close()


def test_vendor_picker_lists_vendor_with_open_pos(Session, client):
    ids = _seed_two_pos(Session)
    r = client.get("/purchase-orders/receive-shipment")
    assert r.status_code == 200
    assert "PAI Industries" in r.text
    # links through to the vendor-scoped form
    assert f"/purchase-orders/receive-shipment?vendor_id={ids['vendor']}" in r.text


def test_vendor_form_shows_lines_from_every_open_po(Session, client):
    ids = _seed_two_pos(Session)
    r = client.get(f"/purchase-orders/receive-shipment?vendor_id={ids['vendor']}")
    assert r.status_code == 200
    # both POs' line inputs are present in the one form
    assert f'name="recv_{ids["la"]}"' in r.text
    assert f'name="recv_{ids["lb"]}"' in r.text
    assert "SKU-A" in r.text and "SKU-B" in r.text


def test_one_submit_receives_across_two_pos_and_sets_each_status(Session, client):
    ids = _seed_two_pos(Session)
    r = client.post(
        "/purchase-orders/receive-shipment",
        data={
            "vendor_id": str(ids["vendor"]),
            f"recv_{ids['la']}": "3",   # fully receive PO1
            f"recv_{ids['lb']}": "2",   # partially receive PO2
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/purchase-orders/receive-shipment?vendor_id=" in r.headers["location"]

    db = Session()
    try:
        po1 = db.get(PurchaseOrder, ids["po1"])
        po2 = db.get(PurchaseOrder, ids["po2"])
        pa = db.get(Product, ids["pa"])
        pb = db.get(Product, ids["pb"])
        assert po1.status == POStatus.RECEIVED     # every PO1 line settled
        assert po2.status == POStatus.PARTIAL      # PO2 still owes 3
        assert pa.qty_on_hand == 3
        assert pb.qty_on_hand == 2
    finally:
        db.close()


def test_all_zero_submit_is_rejected_without_a_receipt(Session, client):
    ids = _seed_two_pos(Session)
    r = client.post(
        "/purchase-orders/receive-shipment",
        data={"vendor_id": str(ids["vendor"]),
              f"recv_{ids['la']}": "0", f"recv_{ids['lb']}": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]

    db = Session()
    try:
        assert db.get(PurchaseOrder, ids["po1"]).status == POStatus.SENT
        assert db.get(Product, ids["pa"]).qty_on_hand == 0
    finally:
        db.close()
