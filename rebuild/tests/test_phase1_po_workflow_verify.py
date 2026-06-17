"""
Phase 1 PO workflow verification
================================
Covers the PO-screen changes from the Phase-1 build:
  - vendor_confirmed checkbox persists through the header autosave (checked → True,
    omitted → False), independent of the free-text confirmation number.
  - "Place Order" (POST /send) commits the PO without hanging: 303 redirect,
    status → SENT, inventory on-order incremented.
  - The draft workspace renders the new actions (Place Order, Email to Vendor)
    and the vendor_confirmed field.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import fresh_engine, activate
from app.main import app
from app.models.vendor import Vendor
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.services.po_service import POService
from app.constants import POStatus


@pytest.fixture()
def Session():
    return activate(fresh_engine())


@pytest.fixture()
def client():
    return TestClient(app)


def _seed_po(Session):
    db = Session()
    try:
        vendor = Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9",
                        email="orders@pai.example")
        db.add(vendor)
        db.flush()
        product = Product(sku="JAKS-GAS-90020", title="UPPER GASKET SET", qty_on_order=0)
        db.add(product)
        db.flush()
        svc = POService(db, current_user_id=1)
        po = svc.create_po(vendor_id=vendor.id, data={})
        svc.add_line(po_id=po.id, product_id=product.id,
                     data={"description": "UPPER GASKET SET", "qty_ordered": 3, "unit_cost": 243.04})
        return po.id, product.id
    finally:
        db.close()


def test_vendor_confirmed_checkbox_round_trips(Session, client):
    po_id, _ = _seed_po(Session)

    # Checkbox ticked → present in form body → True
    r = client.post(f"/purchase-orders/{po_id}/header",
                    data={"vendor_confirmed": "1", "freight_in_cost": "0"})
    assert r.status_code == 204
    db = Session()
    try:
        assert db.get(PurchaseOrder, po_id).vendor_confirmed is True
    finally:
        db.close()

    # Checkbox cleared → omitted from form body → False
    r = client.post(f"/purchase-orders/{po_id}/header", data={"freight_in_cost": "0"})
    assert r.status_code == 204
    db = Session()
    try:
        assert db.get(PurchaseOrder, po_id).vendor_confirmed is False
    finally:
        db.close()


def test_place_order_commits_without_hanging(Session, client):
    po_id, product_id = _seed_po(Session)

    r = client.post(f"/purchase-orders/{po_id}/send", follow_redirects=False)
    assert r.status_code == 303
    assert "ok=sent" in r.headers["location"]

    db = Session()
    try:
        po = db.get(PurchaseOrder, po_id)
        assert po.status == POStatus.SENT
        assert db.get(Product, product_id).qty_on_order == 3  # on-order updated
    finally:
        db.close()


def test_draft_workspace_renders_new_actions(Session, client):
    po_id, _ = _seed_po(Session)
    html = client.get(f"/purchase-orders/{po_id}").text
    assert "Place Order" in html
    assert "Email to Vendor" in html
    assert 'name="vendor_confirmed"' in html
