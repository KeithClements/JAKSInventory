"""
Phase 2 quick-create overhaul verification
==========================================
  - GET /products/quick-create-form?vendor_id=… renders the VENDOR variant;
    with no vendor it renders the legacy MANUAL variant.
  - POST /products/quick-create in vendor mode sets the SKU to the vendor part #
    (MASTER_PLAN §20) and creates a preferred ProductVendorSource carrying it;
    manual mode still honours the typed JAKS-<suffix>.
  - create_product persists seo_description (the AI meta_description fix).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import fresh_engine, activate
from app.main import app
from app.models.vendor import Vendor
from app.models.product import Product, ProductCategory, ProductVendorSource
from app.services.product_service import ProductService


@pytest.fixture()
def Session():
    return activate(fresh_engine())


@pytest.fixture()
def client():
    return TestClient(app)


def _seed_vendor_and_category(Session):
    db = Session()
    try:
        vendor = Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9")
        cat = ProductCategory(name="Gaskets", is_active=True)
        db.add_all([vendor, cat])
        db.commit()
        return vendor.id, cat.id
    finally:
        db.close()


def test_form_renders_vendor_mode(Session, client):
    vendor_id, _ = _seed_vendor_and_category(Session)
    html = client.get(f"/products/quick-create-form?vendor_id={vendor_id}&doc_type=purchase_order").text
    assert 'name="vendor_part_number"' in html
    assert 'name="category_id"' in html
    assert "generated automatically" in html
    assert "Suggest with AI" in html
    assert 'name="sku_suffix"' not in html      # no manual SKU in vendor mode


def test_form_renders_manual_mode(Session, client):
    _seed_vendor_and_category(Session)
    html = client.get("/products/quick-create-form").text
    assert 'name="sku_suffix"' in html
    assert 'name="vendor_part_number"' not in html


def test_quick_create_vendor_mode_sku_is_vendor_part_number(Session, client):
    vendor_id, cat_id = _seed_vendor_and_category(Session)
    r = client.post("/products/quick-create", data={
        "vendor_id": str(vendor_id),
        "vendor_part_number": "311148",
        "category_id": str(cat_id),
        "title": "Upper Gasket Set ISX",
        "engine_model": "ISX",
        "cost": "243.04",
        "markup_pct": "30",
    })
    assert r.status_code == 200
    db = Session()
    try:
        p = db.query(Product).filter(Product.title == "Upper Gasket Set ISX").first()
        assert p is not None
        # MASTER_PLAN §20: SKU is the vendor part #, not a masked JAKS scheme.
        assert p.sku == "311148"
        src = db.query(ProductVendorSource).filter(
            ProductVendorSource.product_id == p.id).first()
        assert src is not None and src.vendor_id == vendor_id
        assert src.vendor_part_number == "311148"
        assert src.is_preferred is True
        assert src.vendor_cost == pytest.approx(243.04)   # PO unit cost seeds vendor cost
    finally:
        db.close()


def test_quick_create_manual_mode_still_works(Session, client):
    _seed_vendor_and_category(Session)
    r = client.post("/products/quick-create", data={
        "sku_suffix": "misc-1001",
        "title": "Misc shop supply",
        "cost": "5.00",
    })
    assert r.status_code == 200
    db = Session()
    try:
        p = db.query(Product).filter(Product.sku == "JAKS-MISC-1001").first()
        assert p is not None and p.title == "Misc shop supply"
    finally:
        db.close()


def test_create_product_persists_seo_description(Session):
    vendor_id, cat_id = _seed_vendor_and_category(Session)
    db = Session()
    try:
        svc = ProductService(db, current_user_id=1)
        p = svc.create_product({
            "sku": "JAKS-SEO-TEST",
            "title": "SEO test",
            "seo_description": "High-quality upper gasket set for Cummins ISX engines.",
        })
        assert p.seo_description == "High-quality upper gasket set for Cummins ISX engines."
    finally:
        db.close()
