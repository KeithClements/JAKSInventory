"""
tests/test_shopify_pack_listings.py
===================================
Vendor sell packs on the storefront (2026-07-01). PAI sells some parts only in
multiples (SELL PACK: 5 PIECE) — order #1007-F1 sold 2 × $3.26 of a part whose
minimum vendor buy was a $12.10 5-pack, a guaranteed loss. The listing must BE
the pack: price/cost/weight × pack_qty, "(Pack of N)" title, pack note in the
description, stock in whole packs, and the partial (nightly) update must push
title/description WITH the pack price so the price never rides alone.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.models.product import Product, ProductVendorSource, ProductCategory
from app.models.vendor import Vendor
from app.services.shopify_service import ShopifyService, _PRODUCT_TITLE_MAX
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


def _pack_product(db, *, pack_qty=5, price=3.26, cost=2.42, weight=0.071,
                  title="THERMOSTAT GASKET"):
    n = next(_seq)
    cat = ProductCategory(name=f"Gasket {n}", code=f"GSK{n}")
    db.add(cat); db.flush()
    pai = Vendor(name=f"PAI Industries {n}", vendor_code=f"P{n}", vendor_number="9")
    db.add(pai); db.flush()
    p = Product(
        sku=f"JAKS-PAI-33135{n}", title=title,
        engine_manufacturer="Caterpillar", engine_model="C7",
        price_override=price, weight_lbs=weight, pack_qty=pack_qty,
        category_id=cat.id, shopify_product_id="",
    )
    db.add(p); db.flush()
    db.add(ProductVendorSource(product_id=p.id, vendor_id=pai.id,
                               vendor_part_number="331351",
                               vendor_sku=f"JAKS-PAI-33135{n}",
                               vendor_cost=cost, is_preferred=True))
    db.commit()
    return p


# ── build_listing: the listing IS the pack ──────────────────────────────────────

def test_pack_listing_multiplies_price_cost_weight(db):
    p = _pack_product(db)                       # 5-pack, $3.26/pc, $2.42/pc cost
    L = ShopifyService(db).build_listing(p)
    assert L["price"] == 16.30                  # 5 × 3.26 — covers the $12.10 vendor pack
    assert L["cost"] == 12.10                   # 5 × 2.42 = PAI's sell-pack total
    assert L["weight_lbs"] == pytest.approx(0.355)   # 5 × 0.071 — shipping bracket honest
    assert L["metafields"]["pack_qty"] == 5


def test_pack_listing_title_and_description_call_out_the_pack(db):
    p = _pack_product(db)
    L = ShopifyService(db).build_listing(p)
    assert L["title"].endswith("(Pack of 5)")
    assert "Sold in a pack of 5" in L["description_html"]
    assert "5 pieces" in L["description_html"]


def test_single_piece_product_unchanged(db):
    p = _pack_product(db, pack_qty=1, price=6.99, cost=2.03)
    L = ShopifyService(db).build_listing(p)
    assert L["price"] == 6.99
    assert L["cost"] == 2.03
    assert "Pack of" not in L["title"]
    assert "Sold in a pack" not in L["description_html"]
    assert L["metafields"]["pack_qty"] == 0     # falsy → metafield omitted
    inp = ShopifyService(db).to_product_set_input(L)
    assert not any(m["key"] == "pack_qty" for m in inp["metafields"])


def test_pack_suffix_not_duplicated_when_title_already_says_pack(db):
    p = _pack_product(db, title="THERMOSTAT GASKET (PACK OF 5)")
    L = ShopifyService(db).build_listing(p)
    assert L["title"].lower().count("pack of 5") == 1


def test_pack_suffix_survives_title_length_cap(db):
    p = _pack_product(db, title="X" * 300)      # base alone exceeds the cap
    L = ShopifyService(db).build_listing(p)
    assert L["title"].endswith("(Pack of 5)")
    assert len(L["title"]) <= _PRODUCT_TITLE_MAX


def test_bulk_part_number_weight_not_multiplied(db):
    """PAI bulk part numbers (SKU suffix == pack size, e.g. 900069HP-040 = the
    40-pack) already carry the WHOLE PACK's weight in the vendor weight field —
    multiplying again would list a 14,200 lb parcel. Price is still per-piece
    on these, so price/cost DO multiply."""
    n = next(_seq)
    p = _pack_product(db, pack_qty=40, price=94.80, cost=70.22, weight=355.0)
    p.sku = f"JAKS-PAI-900069HP-040-{n}"[:0] or "JAKS-PAI-900069HP-040"
    db.commit()
    L = ShopifyService(db).build_listing(p)
    assert L["weight_lbs"] == 355.0                  # pack weight kept, NOT × 40
    assert L["price"] == round(94.80 * 40, 2)        # price still per-piece × pack
    assert L["cost"] == round(70.22 * 40, 2)


def test_product_set_input_carries_pack_price_and_metafield(db):
    p = _pack_product(db)
    svc = ShopifyService(db)
    inp = svc.to_product_set_input(svc.build_listing(p))
    v = inp["variants"][0]
    assert v["price"] == "16.30"
    assert v["inventoryItem"]["cost"] == "12.10"
    assert v["inventoryItem"]["measurement"]["weight"]["value"] == pytest.approx(0.355)
    mf = next(m for m in inp["metafields"] if m["key"] == "pack_qty")
    assert mf["value"] == "5" and mf["type"] == "number_integer"


# ── stock: storefront sells whole packs ─────────────────────────────────────────

def test_sellable_qty_floors_to_whole_packs(db):
    p = _pack_product(db)
    p.qty_on_hand = 12
    p.qty_committed = 0
    db.commit()
    assert ShopifyService._sellable_qty(p) == 2     # 12 pieces = 2 sellable 5-packs
    p.qty_on_hand = 4
    db.commit()
    assert ShopifyService._sellable_qty(p) == 0     # a broken pack is NOT sellable online


def test_sellable_qty_unchanged_for_single_piece(db):
    p = _pack_product(db, pack_qty=1)
    p.qty_on_hand = 7
    p.qty_committed = 2
    db.commit()
    assert ShopifyService._sellable_qty(p) == 5


# ── nightly partial update: pack price never rides alone ────────────────────────

_LIVE_TITLE = "331351 Caterpillar 3100 / C7 / 3126 Thermostat Gasket | PAI | JAK's Diesel"
_LIVE_DESC = "<p>THERMOSTAT GASKET</p><p><strong>Fits:</strong></p><ul><li>CAT C7</li></ul>"


def _fake_graphql(calls, *, live_title=_LIVE_TITLE, live_desc=_LIVE_DESC):
    def fg(query, variables):
        calls.append((query, variables))
        if "packContent" in query:
            return {"data": {"product": {"title": live_title,
                                         "descriptionHtml": live_desc}}}
        if "productUpdate" in query:
            return {"data": {"productUpdate": {"product": {"id": "x"}, "userErrors": []}}}
        return {"data": {"productVariantsBulkUpdate":
                         {"productVariants": [{"id": "x"}], "userErrors": []}}}
    return fg


def test_update_listing_fields_appends_pack_marker_to_live_content(db, monkeypatch):
    """The live title/description are SEO-curated ON Shopify — the pack marker is
    APPENDED to them (read-modify-write), never replaced by the thin ERP title."""
    p = _pack_product(db)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = "gid://shopify/ProductVariant/66"
    db.commit()
    svc = ShopifyService(db)
    calls: list = []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", _fake_graphql(calls))
    res = svc.update_listing_fields(p)
    assert res["ok"] is True and res["price_synced"] is True
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert upd["title"] == _LIVE_TITLE + " (Pack of 5)"      # curated title KEPT
    assert upd["descriptionHtml"].startswith(
        "<p><strong>Sold in a pack of 5.</strong>")
    assert _LIVE_DESC in upd["descriptionHtml"]              # curated desc KEPT
    price_call = next(v for q, v in calls if "productVariantsBulkUpdate" in q)
    assert price_call["variants"][0]["price"] == "16.30"


def test_pack_marker_is_idempotent_once_live(db, monkeypatch):
    """Second sync after conversion: listing already marked → no title/desc in
    the productUpdate (nothing to change), price still refreshes."""
    p = _pack_product(db)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = "gid://shopify/ProductVariant/66"
    db.commit()
    svc = ShopifyService(db)
    calls: list = []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", _fake_graphql(
        calls,
        live_title=_LIVE_TITLE + " (Pack of 5)",
        live_desc="<p><strong>Sold in a pack of 5.</strong> Price shown is for "
                  "5 pieces.</p>" + _LIVE_DESC))
    assert svc.update_listing_fields(p)["ok"] is True
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert "title" not in upd and "descriptionHtml" not in upd
    price_call = next(v for q, v in calls if "productVariantsBulkUpdate" in q)
    assert price_call["variants"][0]["price"] == "16.30"


def test_pack_marker_self_heals_changed_pack_size(db, monkeypatch):
    """Vendor changes the sell pack 5 → 6: the stale '(Pack of 5)' marker and
    note are replaced, the curated base content survives."""
    p = _pack_product(db, pack_qty=6)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = "gid://shopify/ProductVariant/66"
    db.commit()
    svc = ShopifyService(db)
    calls: list = []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", _fake_graphql(
        calls,
        live_title=_LIVE_TITLE + " (Pack of 5)",
        live_desc="<p><strong>Sold in a pack of 5.</strong> Price shown is for "
                  "5 pieces.</p>" + _LIVE_DESC))
    svc.update_listing_fields(p)
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert upd["title"] == _LIVE_TITLE + " (Pack of 6)"
    assert "Sold in a pack of 6" in upd["descriptionHtml"]
    assert "Sold in a pack of 5" not in upd["descriptionHtml"]
    assert _LIVE_DESC in upd["descriptionHtml"]


def test_pack_content_read_failure_is_failsoft(db, monkeypatch):
    """Live content read fails → no title/desc clobber, price still pushes
    (the next sync heals the marker)."""
    p = _pack_product(db)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = "gid://shopify/ProductVariant/66"
    db.commit()
    svc = ShopifyService(db)
    calls: list = []
    def fg(query, variables):
        calls.append((query, variables))
        if "packContent" in query:
            return {"errors": [{"message": "boom"}]}
        if "productUpdate" in query:
            return {"data": {"productUpdate": {"product": {"id": "x"}, "userErrors": []}}}
        return {"data": {"productVariantsBulkUpdate":
                         {"productVariants": [{"id": "x"}], "userErrors": []}}}
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", fg)
    res = svc.update_listing_fields(p)
    assert res["ok"] is True and res["price_synced"] is True
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert "title" not in upd and "descriptionHtml" not in upd


def test_update_listing_fields_still_partial_for_single_piece(db, monkeypatch):
    """The pack exception must not weaken the partial-update contract for the
    other ~26k products — no live-content read, no title/description push when
    pack_qty == 1."""
    p = _pack_product(db, pack_qty=1)
    p.shopify_product_id = "gid://shopify/Product/55"
    p.shopify_variant_id = "gid://shopify/ProductVariant/66"
    db.commit()
    svc = ShopifyService(db)
    calls: list = []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "_graphql", _fake_graphql(calls))
    assert svc.update_listing_fields(p)["ok"] is True
    assert not any("packContent" in q for q, _v in calls)    # no extra read
    upd = next(v for q, v in calls if "productUpdate" in q)["input"]
    assert "title" not in upd and "descriptionHtml" not in upd
