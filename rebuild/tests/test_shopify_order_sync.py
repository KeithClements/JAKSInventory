"""
tests/test_shopify_order_sync.py
================================
The inbound half of the sync: Shopify web orders decrement ERP stock, pack-aware
and idempotent (each order processed exactly once). Network mocked.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.models.product import Product
from app.models.shopify_order import ShopifyProcessedOrder
from app.services.shopify_order_sync import ShopifyOrderSyncService
from app.settings_utils import get_setting_value_db
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


def _mk(db, *, sku, vid="gid://shopify/ProductVariant/5", on_hand=10, pack=1):
    p = Product(sku=sku, title="Part", shopify_variant_id=vid,
                shopify_product_id="gid://shopify/Product/9", qty_on_hand=on_hand,
                pack_qty=pack)
    db.add(p)
    db.commit()
    return p


def _order(oid, *, name, lines, created="2026-07-04T10:00:00Z", cancelled=None):
    return {"id": oid, "name": name, "createdAt": created, "cancelledAt": cancelled,
            "lineItems": {"nodes": lines}}


def _svc(db, nodes, *, recorder=None):
    svc = ShopifyOrderSyncService(db, None)
    svc._shop.is_configured = lambda: True  # type: ignore[assignment]

    def _fake(query, variables):
        if recorder is not None:
            recorder.append(variables)
        return {"data": {"orders": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": nodes}}}

    svc._shop._graphql = _fake  # type: ignore[assignment]
    return svc


# ── decrement + pack expansion ────────────────────────────────────────────────

def test_web_order_decrements_own_stock(db):
    p = _mk(db, sku="JAKS-PAI-OS0001", on_hand=10, pack=1)
    line = {"quantity": 2, "sku": p.sku, "variant": {"id": p.shopify_variant_id}}
    svc = _svc(db, [_order("gid://shopify/Order/1001", name="#1001", lines=[line])])
    res = svc.poll()
    assert res["ok"] and res["orders_processed"] == 1 and res["lines_matched"] == 1
    assert res["pieces_decremented"] == 2
    db.refresh(p)
    assert p.qty_on_hand == 8


def test_pack_qty_expands_pieces(db):
    # One storefront unit == one 5-pack → a qty-2 order consumes 10 pieces.
    p = _mk(db, sku="JAKS-PAI-OS0002", on_hand=50, pack=5)
    line = {"quantity": 2, "sku": p.sku, "variant": {"id": p.shopify_variant_id}}
    svc = _svc(db, [_order("gid://shopify/Order/1002", name="#1002", lines=[line])])
    res = svc.poll()
    assert res["pieces_decremented"] == 10
    db.refresh(p)
    assert p.qty_on_hand == 40


# ── idempotency ───────────────────────────────────────────────────────────────

def test_reprocessing_the_same_order_is_a_noop(db):
    p = _mk(db, sku="JAKS-PAI-OS0003", on_hand=10)
    line = {"quantity": 3, "sku": p.sku, "variant": {"id": p.shopify_variant_id}}
    order = _order("gid://shopify/Order/1003", name="#1003", lines=[line])
    svc = _svc(db, [order])
    svc.poll()
    db.refresh(p)
    assert p.qty_on_hand == 7
    # Second poll sees the same order — must NOT decrement again.
    res2 = svc.poll()
    assert res2["orders_skipped"] == 1 and res2["orders_processed"] == 0
    db.refresh(p)
    assert p.qty_on_hand == 7
    assert db.query(ShopifyProcessedOrder).count() == 1


# ── matching, cancellation, dry-run ───────────────────────────────────────────

def test_unmatched_line_does_not_decrement_but_order_is_marked(db):
    # No product with this SKU/variant → unmatched; order still recorded so it is
    # not retried forever.
    line = {"quantity": 1, "sku": "UNKNOWN-SKU", "variant": {"id": "gid://x/1"}}
    svc = _svc(db, [_order("gid://shopify/Order/1004", name="#1004", lines=[line])])
    res = svc.poll()
    assert res["lines_unmatched"] == 1 and res["pieces_decremented"] == 0
    assert db.query(ShopifyProcessedOrder).count() == 1


def test_cancelled_order_does_not_decrement(db):
    p = _mk(db, sku="JAKS-PAI-OS0005", on_hand=10)
    line = {"quantity": 4, "sku": p.sku, "variant": {"id": p.shopify_variant_id}}
    svc = _svc(db, [_order("gid://shopify/Order/1005", name="#1005", lines=[line],
                           cancelled="2026-07-04T11:00:00Z")])
    res = svc.poll()
    assert res["pieces_decremented"] == 0
    db.refresh(p)
    assert p.qty_on_hand == 10


def test_dry_run_writes_nothing(db):
    p = _mk(db, sku="JAKS-PAI-OS0006", on_hand=10)
    line = {"quantity": 2, "sku": p.sku, "variant": {"id": p.shopify_variant_id}}
    svc = _svc(db, [_order("gid://shopify/Order/1006", name="#1006", lines=[line])])
    res = svc.poll(dry_run=True)
    assert res["lines_matched"] == 1 and res["pieces_decremented"] == 2
    db.refresh(p)
    assert p.qty_on_hand == 10                       # not decremented
    assert db.query(ShopifyProcessedOrder).count() == 0
    assert get_setting_value_db(db, "shopify_order_poll_watermark", "") == ""


def test_watermark_advances_on_apply(db):
    p = _mk(db, sku="JAKS-PAI-OS0007", on_hand=10)
    line = {"quantity": 1, "sku": p.sku, "variant": {"id": p.shopify_variant_id}}
    svc = _svc(db, [_order("gid://shopify/Order/1007", name="#1007", lines=[line],
                           created="2026-07-04T12:34:56Z")])
    svc.poll()
    assert get_setting_value_db(db, "shopify_order_poll_watermark", "") == "2026-07-04T12:34:56Z"


def test_matches_by_master_sku_when_variant_unlinked(db):
    # Variant GID not stored on the product → fall back to master SKU match.
    p = _mk(db, sku="JAKS-PAI-OS0008", vid="gid://shopify/ProductVariant/999", on_hand=6)
    line = {"quantity": 2, "sku": p.sku, "variant": {"id": "gid://shopify/ProductVariant/OTHER"}}
    svc = _svc(db, [_order("gid://shopify/Order/1008", name="#1008", lines=[line])])
    res = svc.poll()
    assert res["lines_matched"] == 1
    db.refresh(p)
    assert p.qty_on_hand == 4


def test_failsoft_when_unconfigured(db):
    svc = ShopifyOrderSyncService(db, None)   # not configured
    res = svc.poll()
    assert res["ok"] is False and "not configured" in res["error"].lower()


# ── cancellation / refund restock (finding #1) ────────────────────────────────

def test_cancelled_after_processing_restocks_once(db):
    p = _mk(db, sku="JAKS-PAI-OS0009", on_hand=10)
    line = {"quantity": 3, "sku": p.sku, "variant": {"id": p.shopify_variant_id}}
    svc = _svc(db, [_order("gid://shopify/Order/1009", name="#1009", lines=[line])])
    svc.poll()
    db.refresh(p)
    assert p.qty_on_hand == 7                      # sold 3

    # Shopify now reports the order cancelled → next poll must restore the 3.
    cancelled = _order("gid://shopify/Order/1009", name="#1009", lines=[line],
                       cancelled="2026-07-04T13:00:00Z")
    svc._shop._graphql = lambda q, v: {"data": {"orders": {  # type: ignore[assignment]
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [cancelled]}}}
    res = svc.poll()
    assert res["orders_reversed"] == 1 and res["pieces_restored"] == 3
    db.refresh(p)
    assert p.qty_on_hand == 10                     # restocked

    # Idempotent — a third poll of the same cancelled order does NOT restock again.
    res3 = svc.poll()
    assert res3["orders_reversed"] == 0
    db.refresh(p)
    assert p.qty_on_hand == 10
