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
    # Weight rides on the inventory item's measurement → drives Shopify's
    # weight-based shipping rates. Must be POUNDS (the storefront brackets are lb).
    assert v["inventoryItem"]["measurement"]["weight"] == {"value": 0.07, "unit": "POUNDS"}
    assert inp["files"][0]["originalSource"].startswith("https://")
    assert any(m["key"] == "pai_part_no" for m in inp["metafields"])
    assert "id" not in inp                                    # create (no shopify id yet)


def test_product_set_input_omits_zero_weight(db):
    """A 0/blank ERP weight must NOT be sent — sending weight:0 would CLOBBER a
    weight a merchant set by hand on Shopify. Omitting it leaves the store value
    untouched (same don't-overwrite-with-blank rule as vendor/tags)."""
    p = _product(db)
    p.weight_lbs = 0.0
    db.commit()
    svc = ShopifyService(db)
    inp = svc.to_product_set_input(svc.build_listing(p))
    assert "measurement" not in inp["variants"][0]["inventoryItem"]


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


# ── Phase A — SEO mapping + Connect & Link (2026-06-12) ─────────────────────────

def test_seo_fields_flow_into_product_set_input(db):
    p = _product(db)
    p.seo_title = "CAT C15 O-Ring | 2-Yr Warranty"
    p.seo_description = "OEM-quality O-ring for CAT C15 engines."
    p.search_keywords = "cat, c15, o-ring"
    db.commit()
    svc = ShopifyService(db)
    L = svc.build_listing(p)
    assert L["seo_title"] == "CAT C15 O-Ring | 2-Yr Warranty"
    inp = svc.to_product_set_input(L)
    assert inp["seo"] == {"title": "CAT C15 O-Ring | 2-Yr Warranty",
                          "description": "OEM-quality O-ring for CAT C15 engines."}
    # owner search keywords land as tags (deduped, case-insensitive)
    lower_tags = [t.lower() for t in inp["tags"]]
    assert "c15" in lower_tags and "o-ring" in lower_tags
    assert lower_tags.count("c15") == 1


def test_no_seo_block_when_blank(db):
    p = _product(db)
    svc = ShopifyService(db)
    inp = svc.to_product_set_input(svc.build_listing(p))
    assert "seo" not in inp


def test_parked_handle_is_not_sent_as_update_id(db):
    """Imports park the HANDLE in shopify_product_id — productSet would reject it
    as an invalid GID, so it must never be sent as the update id."""
    p = _product(db)
    p.shopify_product_id = "jaks-pai-121250"
    db.commit()
    svc = ShopifyService(db)
    inp = svc.to_product_set_input(svc.build_listing(p))
    assert "id" not in inp


def _fake_variants():
    return [
        {"variant_id": "gid://shopify/ProductVariant/11", "sku": "JAKS-PAI-121250",
         "product_id": "gid://shopify/Product/1", "handle": "jaks-pai-121250", "status": "ACTIVE"},
        {"variant_id": "gid://shopify/ProductVariant/22", "sku": "OTHER-SKU",
         "product_id": "gid://shopify/Product/2", "handle": "other-part", "status": "DRAFT"},
    ]


def test_match_and_link_by_vendor_sku(db, monkeypatch):
    p = _product(db)                       # vendor_sku = JAKS-PAI-121250
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_fetch_all_variants", _fake_variants)
    summary = svc.match_and_link()
    assert summary["ok"] is True
    assert summary["linked"] == 1 and summary["unmatched"] == 0
    db.refresh(p)
    assert p.shopify_product_id == "gid://shopify/Product/1"
    assert p.shopify_variant_id == "gid://shopify/ProductVariant/11"
    assert p.shopify_status == "ACTIVE"
    # idempotent — second run has nothing to do
    summary2 = svc.match_and_link()
    assert summary2["candidates"] == 0 and summary2["linked"] == 0


def test_match_and_link_by_handle_fallback(db, monkeypatch):
    p = _product(db)
    # break the SKU path; leave the parked handle from import
    for src in p.vendor_sources:
        src.vendor_sku = "NO-MATCH"
        src.vendor_part_number = "NO-MATCH-2"
    p.shopify_product_id = "other-part"
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_fetch_all_variants", _fake_variants)
    summary = svc.match_and_link()
    assert summary["linked"] == 1
    db.refresh(p)
    assert p.shopify_product_id == "gid://shopify/Product/2"


def test_match_and_link_unmatched_is_reported(db, monkeypatch):
    p = _product(db)
    for src in p.vendor_sources:
        src.vendor_sku = "ZZZ"
        src.vendor_part_number = "ZZZ2"
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_fetch_all_variants", lambda: [])
    summary = svc.match_and_link()
    assert summary["linked"] == 0 and summary["unmatched"] == 1
    assert p.sku in summary["sample_unmatched"]


def test_match_and_link_failsoft_unconfigured(db):
    _product(db)
    res = ShopifyService(db).match_and_link()
    assert res["ok"] is False and "not configured" in res["error"].lower()


def test_link_status_counts(db):
    p = _product(db)
    svc = ShopifyService(db)
    s1 = svc.link_status()
    assert s1["total"] == 1 and s1["linked"] == 0 and s1["unlinked"] == 1
    p.shopify_product_id = "gid://shopify/Product/9"
    db.commit()
    s2 = svc.link_status()
    assert s2["linked"] == 1 and s2["unlinked"] == 0


def test_store_domain_normalizes_pasted_inputs(db, monkeypatch):
    """Owners paste browser URLs — every common shape must resolve to the
    {slug}.myshopify.com host the Admin API answers on."""
    from app.services import shopify_service as mod
    svc = ShopifyService(db)
    cases = {
        "https://admin.shopify.com/store/jaks-diesel-3/settings/domains/123": "jaks-diesel-3.myshopify.com",
        "admin.shopify.com/store/jaks-diesel-3": "jaks-diesel-3.myshopify.com",
        "https://jaks-diesel-3.myshopify.com/": "jaks-diesel-3.myshopify.com",
        "jaks-diesel-3.myshopify.com/admin": "jaks-diesel-3.myshopify.com",
        "jaks-diesel-3": "jaks-diesel-3.myshopify.com",
        "www.jaksdiesel.com": "www.jaksdiesel.com",
        "": "",
    }
    for raw, want in cases.items():
        monkeypatch.setattr(mod, "get_setting_value_db", lambda db, k, d="", _raw=raw: _raw)
        assert svc._store_domain() == want, raw


def test_match_and_link_surfaces_shopify_error(db, monkeypatch):
    _product(db)
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_token", lambda: "atkn_not_an_admin_token")
    def _fail():
        svc._last_error = "Invalid API key or access token"
        return None
    monkeypatch.setattr(svc, "_fetch_all_variants", _fail)
    res = svc.match_and_link()
    assert res["ok"] is False
    assert "Invalid API key or access token" in res["error"]
    assert "shpat_" in res["error"]          # the wrong-token-type hint


def test_match_and_link_by_store_sku_shape(db, monkeypatch):
    """The LIVE store lists JAKS-<part#> (no vendor segment) — the matcher must
    try that shape built from the vendor part number."""
    p = _product(db)                       # vendor_part_number = 121250
    for src in p.vendor_sources:
        src.vendor_sku = "JAKS-PAI-121250"  # old shape — NOT in the store index
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_fetch_all_variants", lambda: [
        {"variant_id": "gid://shopify/ProductVariant/77", "sku": "JAKS-121250",
         "product_id": "gid://shopify/Product/7", "handle": "x", "status": "ACTIVE"},
    ])
    summary = svc.match_and_link()
    assert summary["linked"] == 1
    db.refresh(p)
    assert p.shopify_product_id == "gid://shopify/Product/7"


def test_update_listing_fields_sends_only_price_seo_tags(db, monkeypatch):
    """Partial update must touch ONLY tags/seo (productUpdate) + variant price —
    never title, description, or images (respects 'Price + SEO/tags' choice)."""
    p = _product(db)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = "gid://shopify/ProductVariant/66"
    p.seo_title = "CAT O-Ring"
    db.commit()
    svc = ShopifyService(db)
    calls = []
    def fake_graphql(query, variables):
        calls.append((query, variables))
        if "productUpdate" in query:
            return {"data": {"productUpdate": {"product": {"id": "gid://shopify/Product/55"}, "userErrors": []}}}
        return {"data": {"productVariantsBulkUpdate": {"productVariants": [{"id": "x", "price": "6.99"}], "userErrors": []}}}
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", fake_graphql)
    res = svc.update_listing_fields(p)
    assert res["ok"] is True
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert upd["id"] == "gid://shopify/Product/55"
    assert "tags" in upd and upd["seo"]["title"] == "CAT O-Ring"
    # critically — NOT a full overwrite:
    assert "descriptionHtml" not in upd and "title" not in upd and "files" not in upd
    price_call = next(v for q, v in calls if "productVariantsBulkUpdate" in q)
    assert price_call["variants"][0]["price"] == "6.99"


def test_build_listing_prefers_manufacturer_over_engine_manufacturer(db):
    """The Pricing-Update import writes the canonical vendor to
    Product.manufacturer (Cummins / Caterpillar / International / …). The
    Shopify listing's vendor must read from that field first, so the
    storefront facet tracks the value the owner curates in the product form."""
    p = _product(db)
    p.manufacturer = "Caterpillar"
    p.engine_manufacturer = "CAT / Caterpillar"  # legacy classifier output
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["vendor"] == "Caterpillar"


def test_build_listing_falls_back_to_engine_manufacturer(db):
    """Legacy products that pre-date the manufacturer field still publish
    with the engine_manufacturer value, so nothing regresses for products
    that haven't been touched by the new importer."""
    p = _product(db)
    p.manufacturer = ""
    p.engine_manufacturer = "Cummins"
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["vendor"] == "Cummins"


def test_update_listing_fields_pushes_vendor_when_set(db, monkeypatch):
    """A manufacturer refresh in the ERP must reach Shopify via the partial
    update path — without this, sync_linked would silently drop the change
    because the previous behavior only sent tags + SEO."""
    p = _product(db)
    p.manufacturer = "International"
    p.shopify_product_id = "gid://shopify/Product/77"
    p.shopify_variant_id = "gid://shopify/ProductVariant/88"
    db.commit()
    svc = ShopifyService(db)
    calls = []
    def fake_graphql(query, variables):
        calls.append((query, variables))
        if "productUpdate" in query:
            return {"data": {"productUpdate": {"product": {"id": "gid://shopify/Product/77"}, "userErrors": []}}}
        return {"data": {"productVariantsBulkUpdate": {"productVariants": [{"id": "x", "price": "6.99"}], "userErrors": []}}}
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", fake_graphql)
    res = svc.update_listing_fields(p)
    assert res["ok"] is True
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert upd["vendor"] == "International"


def test_update_listing_fields_skips_vendor_when_only_fallback(db, monkeypatch):
    """A product with no manufacturer must NOT clobber a Shopify-side
    vendor a merchant set by hand — the partial update can't tell whether
    the Shopify value is curated, so the safe default is to omit the field
    entirely when the ERP has no real value to push."""
    p = _product(db)
    p.manufacturer = ""
    p.engine_manufacturer = ""              # no signal → would fall back to "JAK's Diesel"
    p.shopify_product_id = "gid://shopify/Product/99"
    p.shopify_variant_id = ""
    db.commit()
    svc = ShopifyService(db)
    calls = []
    def fake_graphql(query, variables):
        calls.append((query, variables))
        return {"data": {"productUpdate": {"product": {"id": "gid://shopify/Product/99"}, "userErrors": []}}}
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", fake_graphql)
    res = svc.update_listing_fields(p)
    assert res["ok"] is True
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert "vendor" not in upd


def test_update_listing_fields_rejects_unlinked(db, monkeypatch):
    p = _product(db)                 # shopify_product_id = "" (a fresh handle, not gid)
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    res = svc.update_listing_fields(p)
    assert res["ok"] is False and "not linked" in res["error"]


def test_store_title_enriches_generic_imb_titles(db):
    from app.models.product import ProductApplication
    p = _product(db)
    p.title = "Turbocharger"
    p.brand = "Interstate-McBee"
    p.engine_manufacturer = "Cummins"
    p.engine_model = "N14"
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["title"] == "Interstate-McBee Cummins N14 Turbocharger"


def test_store_title_no_duplication_and_hides_pai(db):
    p = _product(db)
    # rich title already containing the make → make not doubled; PAI brand hidden
    p.title = "Cummins ISX15 Front Housing Gasket Kit"
    p.brand = "PAI"
    p.engine_manufacturer = "Cummins"
    p.engine_model = ""
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["title"] == "Cummins ISX15 Front Housing Gasket Kit"   # unchanged
    assert "PAI" not in L["title"]


def test_store_title_make_only_when_no_model(db):
    p = _product(db)
    p.title = "Follower"
    p.brand = "Interstate-McBee"
    p.engine_manufacturer = "Detroit Diesel"
    p.engine_model = ""
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["title"] == "Interstate-McBee Detroit Diesel Follower"


# ── Review-fix regression tests (2026-06-13) ────────────────────────────────────

def test_empty_tags_not_sent_so_merchant_tags_preserved(db, monkeypatch):
    """ProductInput.tags REPLACES — sending [] would wipe merchant tags. When the
    ERP derives no tags, the tags key must be omitted entirely."""
    p = _product(db)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = "gid://shopify/ProductVariant/66"
    # strip everything that produces a tag
    p.engine_manufacturer = ""
    p.engine_model = ""
    p.search_keywords = ""
    p.category_id = None
    for a in list(p.applications):
        db.delete(a)
    db.commit()
    svc = ShopifyService(db)
    calls = []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", lambda q, v: (calls.append((q, v)) or (
        {"data": {"productUpdate": {"product": {"id": "x"}, "userErrors": []}}} if "productUpdate" in q
        else {"data": {"productVariantsBulkUpdate": {"productVariants": [{"id": "x", "price": "6.99"}], "userErrors": []}}})))
    res = svc.update_listing_fields(p)
    assert res["ok"] is True
    upd_calls = [v for q, v in calls if "productUpdate" in q]
    # no tags + no seo => productUpdate skipped entirely (only price runs)
    assert upd_calls == [] or "tags" not in upd_calls[0]["input"]


def test_price_skipped_surfaced_when_no_variant_gid(db, monkeypatch):
    p = _product(db)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = ""          # linked product, but no variant gid
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", lambda q, v:
        {"data": {"productUpdate": {"product": {"id": "x"}, "userErrors": []}}})
    res = svc.update_listing_fields(p)
    assert res["ok"] is True and res["price_synced"] is False
    summ = svc.update_batch([p.id])
    assert summ["updated"] == 1 and summ["price_skipped"] == 1


def test_word_present_respects_token_boundaries():
    from app.services.shopify_service import _word_present
    assert _word_present("C7", "c7 turbocharger")
    assert not _word_present("C7", "c70 series filter")   # not a substring match
    assert not _word_present("CAT", "locator pin")        # 'cat' inside 'locator'
    assert _word_present("CAT", "cat c15 gasket")
    assert _word_present("Series 60", "detroit series 60 head")


def test_store_title_keeps_short_model_not_in_title(db):
    p = _product(db)
    p.title = "C70 Filter"            # contains 'c7' as substring only
    p.brand = "Interstate-McBee"
    p.engine_manufacturer = "Caterpillar"
    p.engine_model = "C7"
    db.commit()
    L = ShopifyService(db).build_listing(p)
    # C7 is genuinely absent (C70 != C7) so it must be added
    assert "C7 " in L["title"] or L["title"].endswith("C7 Filter") or " C7 " in (" " + L["title"])
    assert L["title"].startswith("Interstate-McBee Caterpillar C7")


def test_store_title_hides_non_brand_vendor_names(db):
    p = _product(db)
    p.title = "Turbocharger"
    p.brand = "HHP Parts"             # a vendor/distributor name, NOT a real brand
    p.engine_manufacturer = "Cummins"
    p.engine_model = "N14"
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert "HHP" not in L["title"]
    assert L["title"] == "Cummins N14 Turbocharger"


def test_store_title_falls_back_to_sku_when_all_blank(db):
    p = _product(db)
    p.title = "   "
    p.brand = ""
    p.engine_manufacturer = ""
    p.engine_model = ""
    for a in list(p.applications):
        db.delete(a)
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["title"] == p.sku


def test_store_domain_normalizes_case_and_query(db, monkeypatch):
    from app.services import shopify_service as mod
    svc = ShopifyService(db)
    cases = {
        "https://Admin.Shopify.com/store/Jaks-Diesel-3/settings": "jaks-diesel-3.myshopify.com",
        "jaks-diesel-3.myshopify.com?foo=bar": "jaks-diesel-3.myshopify.com",
        "JAKS-DIESEL-3": "jaks-diesel-3.myshopify.com",
    }
    for raw, want in cases.items():
        monkeypatch.setattr(mod, "get_setting_value_db", lambda db, k, d="", _raw=raw: _raw)
        assert svc._store_domain() == want, raw


def test_match_and_link_flags_total_miss(db, monkeypatch):
    # 60 unlinked products, a big store, but nothing matches -> ok:False guard.
    from app.models.product import Product
    from app.constants import ProductStatus
    for i in range(60):
        db.add(Product(sku=f"NOMATCH-{i:03d}", title="x", status=ProductStatus.ACTIVE, is_active=True))
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    fake = [{"variant_id": f"gid://shopify/ProductVariant/{i}", "sku": f"STORE-{i}",
             "product_id": f"gid://shopify/Product/{i}", "handle": f"h{i}", "status": "ACTIVE"}
            for i in range(600)]
    monkeypatch.setattr(svc, "_fetch_all_variants", lambda: fake)
    summary = svc.match_and_link()
    assert summary["linked"] == 0
    assert summary["ok"] is False and "warning" in summary


def test_metafield_lists_capped_at_128(db):
    from app.models.product import CrossReference, ProductApplication
    from app.constants import CrossRefType
    p = _product(db)
    for i in range(200):
        db.add(CrossReference(product_id=p.id, ref_type=CrossRefType.OEM,
                              ref_number=f"REF{i}", brand="CUMMINS"))
        db.add(ProductApplication(product_id=p.id, engine_make="CUMMINS", engine_model=f"M{i}"))
    db.commit()
    inp = ShopifyService(db).to_product_set_input(ShopifyService(db).build_listing(p))
    import json as _json
    for m in inp["metafields"]:
        if m["key"] in ("oem_references", "engine_applications"):
            assert len(_json.loads(m["value"])) <= 128, m["key"]


# ── Inventory sync — ERP-is-master overwrite (2026-06-13) ───────────────────────

def _link_with_inventory(p, item="gid://shopify/InventoryItem/1", on_hand=5, committed=2):
    p.shopify_product_id = "gid://shopify/Product/9"
    p.shopify_variant_id = "gid://shopify/ProductVariant/9"
    p.shopify_inventory_item_id = item
    p.qty_on_hand = on_hand
    p.qty_committed = committed


def test_sync_inventory_sets_absolute_available(db, monkeypatch):
    p = _product(db)
    _link_with_inventory(p, on_hand=5, committed=2)        # qty_available = 3
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_location_id", lambda: "gid://shopify/Location/1")
    calls = []
    monkeypatch.setattr(svc, "_graphql", lambda q, v: (calls.append((q, v)) or
        {"data": {"inventorySetOnHandQuantities": {"userErrors": []}}}))
    res = svc.sync_inventory([p.id])
    assert res["ok"] and res["synced"] == 1 and res["skipped"] == 0 and res["failed"] == 0
    setq = next(v for q, v in calls
                if "inventorySetOnHandQuantities" in q)["input"]["setQuantities"][0]
    assert setq == {"inventoryItemId": "gid://shopify/InventoryItem/1",
                    "locationId": "gid://shopify/Location/1", "quantity": 3}


def test_sync_inventory_clamps_negative_to_zero(db, monkeypatch):
    p = _product(db)
    _link_with_inventory(p, on_hand=1, committed=5)        # available = -4 → clamp 0
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_location_id", lambda: "gid://shopify/Location/1")
    grabbed = {}
    def fg(q, v):
        if "inventorySetOnHandQuantities" in q:
            grabbed.update(v)
        return {"data": {"inventorySetOnHandQuantities": {"userErrors": []}}}
    monkeypatch.setattr(svc, "_graphql", fg)
    svc.sync_inventory([p.id])
    assert grabbed["input"]["setQuantities"][0]["quantity"] == 0


def test_sync_inventory_self_heals_untracked(db, monkeypatch):
    p = _product(db)
    _link_with_inventory(p, on_hand=7, committed=0)        # available = 7
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_location_id", lambda: "gid://shopify/Location/1")
    seen, state = [], {"set": 0}
    def fg(q, v):
        seen.append(q)
        if "inventorySetOnHandQuantities" in q:
            state["set"] += 1
            if state["set"] == 1:        # first BATCH set fails — item not tracked yet
                return {"data": {"inventorySetOnHandQuantities":
                                 {"userErrors": [{"field": ["setQuantities"], "message": "not tracked"}]}}}
            return {"data": {"inventorySetOnHandQuantities": {"userErrors": []}}}
        if "inventoryItemUpdate" in q:
            return {"data": {"inventoryItemUpdate": {"inventoryItem": {"id": "x", "tracked": True}, "userErrors": []}}}
        if "inventoryActivate" in q:
            return {"data": {"inventoryActivate": {"inventoryLevel": {"id": "y"}, "userErrors": []}}}
        return {}
    monkeypatch.setattr(svc, "_graphql", fg)
    res = svc.sync_inventory([p.id])
    assert res["synced"] == 1 and res["failed"] == 0
    assert any("inventoryItemUpdate" in q for q in seen)   # tracking enabled
    assert any("inventoryActivate" in q for q in seen)     # activated at location


def test_sync_inventory_skips_unlinked(db, monkeypatch):
    p = _product(db)                                       # no inventory item id
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_location_id", lambda: "gid://shopify/Location/1")
    monkeypatch.setattr(svc, "_graphql", lambda q, v: {"data": {}})
    res = svc.sync_inventory([p.id])
    assert res["skipped"] == 1 and res["synced"] == 0


def test_sync_inventory_failsoft_unconfigured(db):
    p = _product(db)
    res = ShopifyService(db).sync_inventory([p.id])
    assert res["ok"] is False and "not configured" in res["error"].lower()


def test_match_and_link_captures_inventory_item_id(db, monkeypatch):
    p = _product(db)                                       # vendor_sku JAKS-PAI-121250
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_fetch_all_variants", lambda: [
        {"variant_id": "gid://shopify/ProductVariant/11", "sku": "JAKS-PAI-121250",
         "product_id": "gid://shopify/Product/1", "handle": "jaks-pai-121250",
         "status": "ACTIVE", "inventory_item_id": "gid://shopify/InventoryItem/55"}])
    svc.match_and_link()
    db.refresh(p)
    assert p.shopify_inventory_item_id == "gid://shopify/InventoryItem/55"


def test_sync_linked_resolves_linked_and_merges(db, monkeypatch):
    p = _product(db)
    p.shopify_product_id = "gid://shopify/Product/9"     # linked
    db.commit()
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    seen = {}
    monkeypatch.setattr(svc, "update_batch", lambda ids, progress=None: (seen.update(upd=list(ids)) or
        {"updated": 1, "price_skipped": 0, "failed": 0, "errors": []}))
    monkeypatch.setattr(svc, "sync_inventory", lambda ids, progress=None: (seen.update(inv=list(ids)) or
        {"synced": 1, "failed": 0, "errors": []}))
    res = svc.sync_linked()                               # None → resolve linked ids
    assert res["ok"] and res["content_updated"] == 1 and res["stock_synced"] == 1
    assert seen["upd"] == [p.id] and seen["inv"] == [p.id]


def test_sync_linked_failsoft_unconfigured(db):
    res = ShopifyService(db).sync_linked([1])
    assert res["ok"] is False and "not configured" in res["error"].lower()


def test_update_batch_fires_progress_callback(db, monkeypatch):
    """update_batch calls the OPTIONAL progress callback once per listing as
    ('price/SEO', done, total) — the hook that drives the live job bar — and a
    callback that raises is swallowed (fail-soft: never breaks the push)."""
    p1, p2 = _product(db), _product(db)
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "update_listing_fields",
                        lambda p: {"ok": True, "price_synced": True})
    ticks = []
    svc.update_batch([p1.id, p2.id], progress=lambda *a: ticks.append(a))
    assert ticks == [("price/SEO", 1, 2), ("price/SEO", 2, 2)]

    def boom(*a):
        raise RuntimeError("bad consumer")

    res = svc.update_batch([p1.id], progress=boom)   # must not raise
    assert res["updated"] == 1


# ── Clean image supersedes watermarked (2026-06-13) ─────────────────────────────

_CLEAN_IMG = "https://cdn.shopify.com/s/files/1/0450/clean/121250_01.jpg"


def test_build_listing_drops_watermarked_when_clean_present(db):
    from app.models.product import ProductImage
    p = _product(db)   # _product already adds a watermarked PAI image (primary)
    db.add(ProductImage(product_id=p.id, file_path=_CLEAN_IMG, is_primary=False))
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["images"] == [_CLEAN_IMG]                 # watermarked PAI dropped from push


def test_build_listing_keeps_watermarked_when_no_clean(db):
    p = _product(db)   # only the watermarked PAI image
    L = ShopifyService(db).build_listing(p)
    assert L["images"] == ["https://cache.paiindustries.com/x/121250_01.jpg"]   # better than nothing


def test_build_listing_features_clean_primary_first(db):
    from app.models.product import ProductImage
    p = _product(db)
    db.add(ProductImage(product_id=p.id, file_path=_CLEAN_IMG, is_primary=True))
    db.add(ProductImage(product_id=p.id,
                        file_path="https://cdn.shopify.com/s/files/1/0450/clean/121250_02.jpg",
                        is_primary=False))
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["images"][0] == _CLEAN_IMG                # primary featured first; PAI gone


def test_supersede_primary_with_clean(db):
    from app.models.product import ProductImage
    from app.services.product_service import ProductService
    p = _product(db)   # watermarked PAI image is primary
    db.add(ProductImage(product_id=p.id, file_path=_CLEAN_IMG, is_primary=False))
    db.commit()
    assert ProductService(db).supersede_primary_with_clean(p.id) is True
    db.refresh(p)
    primary = next(i for i in p.images if i.is_primary)
    assert "cdn.shopify.com" in primary.file_path                    # clean is now primary
    assert any("paiindustries.com" in i.file_path for i in p.images)  # watermarked kept
    assert ProductService(db).supersede_primary_with_clean(p.id) is False   # idempotent


def test_republish_batch_preserves_status(db, monkeypatch):
    """A full re-publish (needed to push image changes) must KEEP each product's
    status — a live (ACTIVE) listing stays live, never silently dropped to DRAFT."""
    pa = _product(db); pa.shopify_product_id = "gid://shopify/Product/1"; pa.shopify_status = "ACTIVE"
    pd = _product(db); pd.shopify_product_id = "gid://shopify/Product/2"; pd.shopify_status = "DRAFT"
    pn = _product(db)   # unlinked (shopify_product_id == "")
    db.commit()
    svc = ShopifyService(db)
    seen = {}
    def fake_pub(p, *, status="DRAFT"):
        seen[p.id] = status
        return {"ok": True, "product": {"id": "x"}}
    monkeypatch.setattr(svc, "publish_product", fake_pub)
    res = svc.republish_batch([pa.id, pd.id, pn.id])
    assert res["published"] == 3 and res["failed"] == 0
    assert seen[pa.id] == "ACTIVE"     # live stays live (NOT dropped to draft)
    assert seen[pd.id] == "DRAFT"      # draft stays draft
    assert seen[pn.id] == "DRAFT"      # unlinked/new -> draft (never auto-live)


def test_async_publish_treats_queued_null_product_as_success(db, monkeypatch):
    """synchronous=False: productSet returns a null product (queued) — that is a
    SUCCESS, not a failure. The GID is captured later via re-link."""
    p = _product(db)
    svc = ShopifyService(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_token", lambda: "shpat_x")
    import httpx
    class _Resp:
        def json(self): return {"data": {"productSet": {"product": None, "userErrors": []}}}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    res = svc.publish_product(p, status="DRAFT", synchronous=False)
    assert res["ok"] is True and res.get("queued") is True
    # sync call with null product is still a failure
    res2 = svc.publish_product(p, status="DRAFT", synchronous=True)
    assert res2["ok"] is False
