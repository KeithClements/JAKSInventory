"""
tests/test_shopify_sold_out_model.py
====================================
The sold-out availability model (owner decision 2026-07-04): out-of-stock parts
keep a LIVE 'Sold out' page instead of a 404, only discontinued parts are hidden,
and the variant inventory policy + availability_state metafield ride the normal
price update. Network mocked — no live Shopify call.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.constants import ProductStatus, VendorAvailability
from app.models.product import Product
from app.services.availability_policy import availability_mode
from app.services.shopify_service import ShopifyService
from app.settings_utils import set_setting_value_db
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


def _sold_out_mode(db):
    set_setting_value_db(db, "shopify_availability_mode", "sold_out")
    db.commit()


def _mk(db, *, gid="gid://shopify/Product/200", vid="gid://shopify/ProductVariant/2",
        status="ACTIVE", availability="", is_active=True,
        prod_status=ProductStatus.ACTIVE, hidden_by_erp=False,
        on_hand=0, committed=0, pack=1):
    n = next(_seq)
    p = Product(
        sku=f"JAKS-PAI-SO{n:04d}", title="Reman Head",
        shopify_product_id=gid, shopify_variant_id=vid, shopify_status=status,
        vendor_availability=availability, is_active=is_active, status=prod_status,
        shopify_hidden_by_erp=hidden_by_erp, qty_on_hand=on_hand,
        qty_committed=committed, pack_qty=pack,
    )
    db.add(p)
    db.commit()
    return p


def _configured(svc, graphql_recorder=None, rest_recorder=None):
    svc.is_configured = lambda: True  # type: ignore[assignment]

    def _fake_graphql(query, variables):
        if graphql_recorder is not None:
            graphql_recorder.append((query, variables))
        return {"data": {"productUpdate": {"product": {"id": "x"}, "userErrors": []},
                         "productVariantsBulkUpdate": {"productVariants": [{"id": "v"}],
                                                       "userErrors": []}}}

    def _fake_rest(numeric_id, fields):
        if rest_recorder is not None:
            rest_recorder.append((numeric_id, fields))
        return True, ""

    svc._graphql = _fake_graphql            # type: ignore[assignment]
    svc._rest_product_update = _fake_rest   # type: ignore[assignment]
    return svc


# ── availability_mode setting ────────────────────────────────────────────────

def test_mode_defaults_to_hide(db):
    assert availability_mode(db) == "hide"


def test_mode_reads_sold_out(db):
    _sold_out_mode(db)
    assert availability_mode(db) == "sold_out"


# ── reconcile_states: the behavior change ────────────────────────────────────

def test_out_of_stock_part_stays_live_not_hidden(db):
    # THE headline change vs the legacy model: an OOS part is NOT pulled down.
    p = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK, on_hand=0)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.reconcile_states()
    assert res["hidden"] == 0 and res["relisted"] == 0
    assert calls == []                       # no status write — it stays a live page
    db.refresh(p)
    assert p.shopify_status == "ACTIVE"


def test_discontinued_part_is_hidden(db):
    p = _mk(db, status="ACTIVE", availability=VendorAvailability.DISCONTINUED)
    svc = _configured(ShopifyService(db))
    res = svc.reconcile_states()
    assert res["hidden"] == 1
    db.refresh(p)
    assert p.shopify_status == "DRAFT" and p.shopify_hidden_by_erp is True


def test_relists_sold_out_page_the_erp_hid(db):
    # A part the ERP previously hid (legacy model) that is out-of-stock should be
    # RE-LISTED as a live sold-out page under the new model.
    p = _mk(db, status="DRAFT", availability=VendorAvailability.OUT_OF_STOCK,
            hidden_by_erp=True, on_hand=0)
    rest: list = []
    svc = _configured(ShopifyService(db), rest_recorder=rest)
    res = svc.reconcile_states()
    assert res["relisted"] == 1 and res["hidden"] == 0
    db.refresh(p)
    assert p.shopify_status == "ACTIVE" and p.shopify_hidden_by_erp is False
    assert rest and rest[0][1] == {"status": "active", "published": True}


def test_in_stock_part_untouched(db):
    p = _mk(db, status="ACTIVE", availability=VendorAvailability.IN_STOCK, on_hand=5)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.reconcile_states()
    assert res["hidden"] == 0 and res["relisted"] == 0 and calls == []


def test_human_drafted_page_not_relisted(db):
    # Safety gate preserved: a DRAFT the ERP did NOT hide is never resurrected.
    p = _mk(db, status="DRAFT", availability=VendorAvailability.OUT_OF_STOCK,
            hidden_by_erp=False)
    rest: list = []
    svc = _configured(ShopifyService(db), rest_recorder=rest)
    res = svc.reconcile_states()
    assert res["relisted"] == 0 and rest == []


# ── update_listing_fields carries policy + metafield in sold-out mode ────────

def test_update_listing_fields_sets_policy_and_metafield(db):
    _sold_out_mode(db)
    # own 0, vendor out → sold_out → DENY policy + availability_state=sold_out
    p = _mk(db, availability=VendorAvailability.OUT_OF_STOCK, on_hand=0)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.update_listing_fields(p)
    assert res["ok"]
    # productUpdate carried the availability_state metafield
    prod_calls = [v for (_q, v) in calls if "input" in v and "id" in v["input"]]
    assert prod_calls, "expected a productUpdate call"
    metas = prod_calls[0]["input"].get("metafields", [])
    assert any(m["key"] == "availability_state" and m["value"] == "sold_out" for m in metas)
    # variant update carried the DENY inventory policy
    var_calls = [v for (_q, v) in calls if "variants" in v]
    assert var_calls and var_calls[0]["variants"][0]["inventoryPolicy"] == "DENY"


def test_update_listing_fields_available_to_order_is_continue(db):
    _sold_out_mode(db)
    p = _mk(db, availability=VendorAvailability.IN_STOCK, on_hand=0)   # available to order
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    svc.update_listing_fields(p)
    var_calls = [v for (_q, v) in calls if "variants" in v]
    assert var_calls and var_calls[0]["variants"][0]["inventoryPolicy"] == "CONTINUE"


def test_hide_mode_does_not_set_policy_or_metafield(db):
    # Legacy default: no availability_state metafield, no inventoryPolicy on the variant.
    p = _mk(db, availability=VendorAvailability.OUT_OF_STOCK)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    svc.update_listing_fields(p)
    for (_q, v) in calls:
        if "input" in v:
            assert "metafields" not in v["input"]
        if "variants" in v:
            assert "inventoryPolicy" not in v["variants"][0]


# ── push_product_now ─────────────────────────────────────────────────────────

def test_push_product_now_requires_link(db):
    p = _mk(db, gid="jaks-parked-handle")   # not a gid:// → unlinked
    svc = _configured(ShopifyService(db))
    res = svc.push_product_now(p.id)
    assert res["ok"] is False and "not linked" in res["error"].lower()


def test_push_product_now_updates_linked(db):
    p = _mk(db, availability=VendorAvailability.IN_STOCK, on_hand=1)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.push_product_now(p.id)
    assert res["ok"] and calls, "linked product should push to Shopify"


# ── fixes from adversarial review ─────────────────────────────────────────────

def test_policy_skipped_and_no_badge_when_variant_gid_missing(db):
    # Finding #3: a linked product with no variant GID can't have its inventoryPolicy
    # set, so we must NOT publish an availability_state badge the cart can't enforce.
    _sold_out_mode(db)
    p = _mk(db, availability=VendorAvailability.OUT_OF_STOCK, on_hand=0, vid="")
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.update_listing_fields(p)
    assert res["ok"] and res["policy_skipped"] is True
    # No availability_state metafield anywhere in what we sent.
    for (_q, v) in calls:
        metas = (v.get("input", {}) or {}).get("metafields", [])
        assert not any(m.get("key") == "availability_state" for m in metas)


def test_sync_inventory_all_linked_keeps_oos_in_sold_out_mode(db):
    # Finding #2: OOS parts must stay in the stock feed in sold_out mode (so their
    # live page gets qty 0), unlike legacy hide mode.
    _sold_out_mode(db)
    p = _mk(db, availability=VendorAvailability.OUT_OF_STOCK, on_hand=0)
    p.shopify_inventory_item_id = "gid://shopify/InventoryItem/7"
    db.commit()
    svc = ShopifyService(db)
    captured: dict = {}
    svc.sync_inventory = lambda ids, **kw: (captured.__setitem__("ids", ids)  # type: ignore[assignment]
                                            or {"ok": True})
    svc.sync_inventory_all_linked()
    assert p.id in captured["ids"]


def test_sync_inventory_all_linked_excludes_oos_in_hide_mode(db):
    p = _mk(db, availability=VendorAvailability.OUT_OF_STOCK, on_hand=0)
    p.shopify_inventory_item_id = "gid://shopify/InventoryItem/8"
    db.commit()
    svc = ShopifyService(db)
    captured: dict = {}
    svc.sync_inventory = lambda ids, **kw: (captured.__setitem__("ids", ids)  # type: ignore[assignment]
                                            or {"ok": True})
    svc.sync_inventory_all_linked()
    assert p.id not in captured["ids"]
