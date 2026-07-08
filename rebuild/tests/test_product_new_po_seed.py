"""
tests/test_product_new_po_seed.py
=================================
Owner-reported bug (2026-06-01): selecting a product and hitting "New PO" in the
product preview dock should open the New-PO picker to *order that part* — instead
it dumped the user on the PO list.

Root cause: the preview "New PO" button was a plain ``<a href="/purchase-orders/new">``.
``GET /purchase-orders/new`` redirects to the PO list for any non-HTMX request, so a
normal link click always landed on the list. The button now opens the slide-over
picker via ``hx-get`` (like the PO list does), pre-seeded with the part + its
preferred vendor, and ``POST /purchase-orders/new`` adds that part as the PO's
first line.

These tests lock in:
  1. POST with ``product_id`` creates the PO **and** a line for that product, with the
     unit cost pulled from the chosen vendor's source.
  2. GET the picker with ``product_id`` renders the part banner, a hidden product_id,
     and pre-selects the vendor.
  3. The product preview panel emits an ``hx-get`` New-PO button (not a plain link) —
     guards against silently regressing to the broken navigation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.product import Product, ProductVendorSource
from app.models.purchase_order import POLine, PurchaseOrder
from app.models.vendor import Vendor
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db_session():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    # follow_redirects=False so the 303 → workspace contract is assertable directly.
    return TestClient(app, follow_redirects=False)


def _seed(db, *, vendor_cost=42.0):
    """A vendor + a product sourced from that vendor (preferred). Returns ids."""
    vendor = Vendor(name="Seed Diesel", vendor_code="SEED", is_active=True)
    product = Product(sku="SEED-PART-1", title="Seeded Injector", cost=10.0,
                      qty_on_hand=0, is_active=True)
    db.add_all([vendor, product])
    db.commit()
    src = ProductVendorSource(product_id=product.id, vendor_id=vendor.id,
                              is_preferred=True, is_active=True,
                              vendor_cost=vendor_cost, vendor_part_number="VP-1")
    db.add(src)
    db.commit()
    return vendor.id, product.id


# ── POST seeds the part as the first line ──────────────────────────────────────

def test_new_po_with_product_creates_seeded_line(client, db_session):
    vendor_id, product_id = _seed(db_session, vendor_cost=42.0)

    resp = client.post(
        "/purchase-orders/new",
        data={"vendor_id": str(vendor_id), "product_id": str(product_id)},
    )

    # Redirects to the new PO's workspace, not the PO list.
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("/purchase-orders/")
    po_id = int(loc.rsplit("/", 1)[-1])

    po = db_session.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    assert po is not None
    assert po.vendor_id == vendor_id

    lines = db_session.query(POLine).filter(POLine.po_id == po_id).all()
    assert len(lines) == 1, "the selected part should be on the PO"
    line = lines[0]
    assert line.product_id == product_id
    assert line.qty_ordered == 1
    # Unit cost auto-filled from THIS vendor's source.
    assert line.unit_cost == 42.0
    # Description backfilled from the product.
    assert line.description == "Seeded Injector"


def test_new_po_without_product_creates_empty_po(client, db_session):
    # The PO-list "New PO" path (no product_id) must still create a bare PO.
    vendor_id, _ = _seed(db_session)
    resp = client.post("/purchase-orders/new", data={"vendor_id": str(vendor_id)})
    assert resp.status_code == 303
    po_id = int(resp.headers["location"].rsplit("/", 1)[-1])
    assert db_session.query(POLine).filter(POLine.po_id == po_id).count() == 0


# ── GET picker pre-fills the part + vendor ─────────────────────────────────────

def test_picker_prefills_part_and_vendor(client, db_session):
    vendor_id, product_id = _seed(db_session)
    resp = client.get(
        f"/purchase-orders/new?product_id={product_id}&vendor_id={vendor_id}",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "Ordering this part" in html
    assert "SEED-PART-1" in html
    assert f'name="product_id" value="{product_id}"' in html
    # Vendor pre-selected via the Alpine x-data init.
    assert f"vendorId: '{vendor_id}'" in html


def test_get_new_without_htmx_redirects_to_list(client, db_session):
    # Documents the guard the button works around: a non-HTMX GET → PO list.
    resp = client.get("/purchase-orders/new")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/purchase-orders/"


# ── template guard: button uses hx-get, not a plain link ───────────────────────

def test_preview_new_po_button_uses_htmx(client, db_session):
    _vendor_id, product_id = _seed(db_session)
    html = client.get(f"/products/preview/{product_id}").text
    # Drives the slide-over via an explicit htmx.ajax() (declarative hx-* aren't
    # wired on this htmx-injected panel) — and must NOT be a plain navigation link
    # back to the broken GET that redirects to the PO list.
    assert f'data-new-po-url="/purchase-orders/new?product_id={product_id}' in html
    assert "htmx.ajax('GET', $el.dataset.newPoUrl, {target: '#create-slide-content'" in html
    assert '<a href="/purchase-orders/new' not in html
