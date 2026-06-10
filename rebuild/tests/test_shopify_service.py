"""
tests/test_shopify_service.py
=============================
ShopifyService maps an ERP product onto Shopify's productSet input. These cover the
PURE mapping (no network): vendor-neutral listing, draft-first status, SKU + cost on
the variant inventory item, self-hosted image source, metafields, and idempotent
update via shopify_product_id. The live push is fail-soft and not exercised here.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.constants import CrossRefType
from app.models.product import (
    Product, ProductVendorSource, ProductImage, CrossReference,
    ProductApplication, ProductCategory,
)
from app.models.vendor import Vendor
from app.services.shopify_service import ShopifyService
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db):
    n = next(_seq)
    cat = ProductCategory(name="O-Ring", code="ORING")
    db.add(cat); db.flush()
    pai = Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9")
    db.add(pai); db.flush()
    p = Product(
        sku=f"JAKS-CAT-ORING-9{n:04d}", title="O-RING",
        engine_manufacturer="Caterpillar", engine_model="",
        price_override=6.99, barcode="", weight_lbs=0.07,
        supplier_warranty_months=24, category_id=cat.id, shopify_product_id="",
    )
    db.add(p); db.flush()
    db.add(ProductVendorSource(product_id=p.id, vendor_id=pai.id,
                               vendor_part_number="121250", vendor_sku="JAKS-PAI-121250",
                               vendor_cost=2.03, is_preferred=True))
    db.add(ProductImage(product_id=p.id,
                        file_path="https://cache.paiindustries.com/x/121250_01.jpg",
                        is_primary=True))
    db.add(CrossReference(product_id=p.id, ref_type=CrossRefType.OEM,
                          ref_number="061-9455", brand="CATERPILLAR"))
    db.add(ProductApplication(product_id=p.id, engine_make="CATERPILLAR", engine_model="C15"))
    db.commit()
    return p


def test_build_listing_maps_core_fields(db):
    p = _product(db)
    L = ShopifyService(db).build_listing(p)
    assert L["sku"] == p.sku
    assert L["price"] == round(p.selling_price, 2)
    assert L["cost"] == 2.03                                   # from preferred vendor source
    assert L["vendor"] == "Caterpillar"                       # engine make, NOT "PAI" (no leak)
    assert L["product_type"] == "O-Ring"
    assert L["status"] == "DRAFT"                             # draft-first
    assert "C15" in L["tags"]
    assert L["images"] == ["https://cache.paiindustries.com/x/121250_01.jpg"]
    assert L["metafields"]["pai_part_no"] == "121250"
    assert "CATERPILLAR 061-9455" in L["metafields"]["oem_references"]


def test_listing_never_leaks_pai_as_vendor(db):
    p = _product(db)
    L = ShopifyService(db).build_listing(p)
    assert "PAI" not in L["vendor"]
    assert all("PAI" not in t for t in L["tags"])             # PAI is hidden from the storefront


def test_product_set_input_shape(db):
    p = _product(db)
    svc = ShopifyService(db)
    inp = svc.to_product_set_input(svc.build_listing(p))
    assert inp["status"] == "DRAFT"
    v = inp["variants"][0]
    assert v["inventoryItem"]["sku"] == p.sku
    assert v["inventoryItem"]["cost"] == "2.03"
    assert v["price"] == "6.99"
    assert inp["files"][0]["originalSource"].startswith("https://")
    assert any(m["key"] == "pai_part_no" for m in inp["metafields"])
    assert "id" not in inp                                    # create (no shopify id yet)


def test_idempotent_update_includes_id(db):
    p = _product(db)
    p.shopify_product_id = "gid://shopify/Product/123456"
    db.commit()
    svc = ShopifyService(db)
    inp = svc.to_product_set_input(svc.build_listing(p))
    assert inp["id"] == "gid://shopify/Product/123456"        # update-in-place


def test_publish_is_failsoft_when_unconfigured(db):
    p = _product(db)
    svc = ShopifyService(db)              # current_user_id None → permission bypass
    assert svc.is_configured() is False
    res = svc.publish_product(p)
    assert res["ok"] is False
    assert "not configured" in res["error"].lower()
    assert p.shopify_product_id == ""     # nothing written
