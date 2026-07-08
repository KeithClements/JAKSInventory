"""
tests/test_shopify_reconcile_availability.py
============================================
ShopifyService.reconcile_availability closes the "de-list gap": flagging a part
out_of_stock/discontinued used to only EXCLUDE it from the next sync — the
already-live ACTIVE listing stayed buyable. Reconcile flips it to DRAFT, and
re-lists (ACTIVE + published) ONLY parts the ERP itself hid that are back in
stock. These exercise the engine with the network mocked (no live Shopify call).
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


def _mk(db, *, gid="gid://shopify/Product/100", status="ACTIVE",
        availability="", is_active=True, prod_status=ProductStatus.ACTIVE,
        hidden_by_erp=False):
    n = next(_seq)
    p = Product(
        sku=f"JAKS-PAI-RC{n:04d}", title="Reman Turbo",
        shopify_product_id=gid, shopify_status=status,
        vendor_availability=availability, is_active=is_active,
        status=prod_status, shopify_hidden_by_erp=hidden_by_erp,
    )
    db.add(p)
    db.commit()
    return p


def _configured(svc, graphql_recorder=None, rest_recorder=None):
    """Wire a service to 'configured' with network mocked."""
    svc.is_configured = lambda: True  # type: ignore[assignment]

    def _fake_graphql(query, variables):
        if graphql_recorder is not None:
            graphql_recorder.append(variables)
        return {"data": {"productUpdate": {"product": {"id": "x"}, "userErrors": []}}}

    def _fake_rest(numeric_id, fields):
        if rest_recorder is not None:
            rest_recorder.append((numeric_id, fields))
        return True, ""

    svc._graphql = _fake_graphql            # type: ignore[assignment]
    svc._rest_product_update = _fake_rest   # type: ignore[assignment]
    return svc


# ── HIDE direction ─────────────────────────────────────────────────────────────

def test_hides_active_listing_that_went_out_of_stock(db):
    p = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.reconcile_availability()
    assert res["ok"] and res["hidden"] == 1 and res["relisted"] == 0
    db.refresh(p)
    assert p.shopify_status == "DRAFT"           # live listing pulled down
    assert p.shopify_hidden_by_erp is True        # marked as ERP-hidden (gates re-list)
    # the mutation we sent set status DRAFT on this product's GID
    assert calls and calls[0]["input"]["status"] == "DRAFT"
    assert calls[0]["input"]["id"] == p.shopify_product_id


def test_hides_discontinued_and_deactivated(db):
    # discontinued products are deactivated by the importer (is_active=False,
    # status=DISCONTINUED) — both must drive a hide.
    p = _mk(db, status="ACTIVE", is_active=False, prod_status=ProductStatus.DISCONTINUED)
    svc = _configured(ShopifyService(db))
    res = svc.reconcile_availability()
    assert res["hidden"] == 1
    db.refresh(p)
    assert p.shopify_status == "DRAFT"


def test_in_stock_active_listing_is_left_alone(db):
    p = _mk(db, status="ACTIVE", availability=VendorAvailability.IN_STOCK)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.reconcile_availability()
    assert res["hidden"] == 0 and res["relisted"] == 0
    assert calls == []                            # no network write at all
    db.refresh(p)
    assert p.shopify_status == "ACTIVE"


def test_already_draft_oos_listing_is_not_re_hidden(db):
    # An OOS part already DRAFT needs no action (idempotent).
    p = _mk(db, status="DRAFT", availability=VendorAvailability.OUT_OF_STOCK)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.reconcile_availability()
    assert res["hidden"] == 0 and calls == []


# ── RE-LIST direction (the safety gate) ─────────────────────────────────────────

def test_relists_only_when_erp_hid_it(db):
    p = _mk(db, status="DRAFT", availability=VendorAvailability.IN_STOCK,
            hidden_by_erp=True)
    rest: list = []
    svc = _configured(ShopifyService(db), rest_recorder=rest)
    res = svc.reconcile_availability()
    assert res["relisted"] == 1 and res["hidden"] == 0
    db.refresh(p)
    assert p.shopify_status == "ACTIVE"
    assert p.shopify_hidden_by_erp is False
    # re-listed via REST publish (status active + published:true)
    assert rest and rest[0][1] == {"status": "active", "published": True}


def test_draft_in_stock_not_relisted_when_human_drafted(db):
    # The SAFETY GATE: a DRAFT listing the ERP did NOT hide (hidden_by_erp=False)
    # is NEVER auto-resurrected, even though the part is in stock.
    p = _mk(db, status="DRAFT", availability=VendorAvailability.IN_STOCK,
            hidden_by_erp=False)
    rest: list = []
    svc = _configured(ShopifyService(db), rest_recorder=rest)
    res = svc.reconcile_availability()
    assert res["relisted"] == 0 and rest == []
    db.refresh(p)
    assert p.shopify_status == "DRAFT"            # left hidden, as a human intended


# ── dry-run + scoping + fail-soft ───────────────────────────────────────────────

def test_dry_run_reports_plan_but_writes_nothing(db):
    p = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK)
    calls: list = []
    svc = _configured(ShopifyService(db), graphql_recorder=calls)
    res = svc.reconcile_availability(dry_run=True)
    assert res["hidden"] == 1 and res["dry_run"] is True
    assert calls == []                            # no network call on a dry run
    db.refresh(p)
    assert p.shopify_status == "ACTIVE"           # nothing changed
    assert p.shopify_hidden_by_erp is False


def test_unlinked_products_are_ignored(db):
    # No Shopify GID → nothing to reconcile (parked handle is not a gid://).
    _mk(db, gid="jaks-pai-parked-handle", status="",
        availability=VendorAvailability.OUT_OF_STOCK)
    svc = _configured(ShopifyService(db))
    res = svc.reconcile_availability()
    assert res["considered"] == 0 and res["hidden"] == 0


def test_explicit_id_list_scopes_the_reconcile(db):
    a = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK)
    b = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK)
    svc = _configured(ShopifyService(db))
    res = svc.reconcile_availability([a.id])
    assert res["considered"] == 1 and res["hidden"] == 1
    db.refresh(a); db.refresh(b)
    assert a.shopify_status == "DRAFT"
    assert b.shopify_status == "ACTIVE"           # out of scope → untouched


def test_failsoft_when_unconfigured(db):
    _mk(db, availability=VendorAvailability.OUT_OF_STOCK)
    res = ShopifyService(db).reconcile_availability()   # not configured
    assert res["ok"] is False and "not configured" in res["error"].lower()


def test_earlier_success_persists_when_a_later_product_fails(db):
    # Durability: reconcile commits per CHANGED product, so a failure partway
    # through a run must NOT roll back an already-hidden listing (which would lose
    # shopify_hidden_by_erp and strand it as un-re-listable forever).
    a = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK)
    b = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK)
    svc = ShopifyService(db)
    svc.is_configured = lambda: True  # type: ignore[assignment]
    calls = {"n": 0}

    def _graphql(q, v):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"data": {"productUpdate": {"product": {"id": "x"}, "userErrors": []}}}
        return {"data": {"productUpdate": {"userErrors": [{"message": "boom"}]}}}

    svc._graphql = _graphql  # type: ignore[assignment]
    res = svc.reconcile_availability([a.id, b.id])
    assert res["hidden"] == 1 and res["failed"] == 1
    # Re-read from a fresh session to prove the first hide was actually COMMITTED.
    db.expire_all()
    a2 = db.get(type(a), a.id)
    assert a2.shopify_status == "DRAFT" and a2.shopify_hidden_by_erp is True


def test_graphql_usererror_counts_as_failed_not_silent(db):
    p = _mk(db, status="ACTIVE", availability=VendorAvailability.OUT_OF_STOCK)
    svc = ShopifyService(db)
    svc.is_configured = lambda: True  # type: ignore[assignment]
    svc._graphql = lambda q, v: {                       # type: ignore[assignment]
        "data": {"productUpdate": {"userErrors": [{"message": "boom"}]}}}
    res = svc.reconcile_availability()
    assert res["hidden"] == 0 and res["failed"] == 1
    db.refresh(p)
    assert p.shopify_status == "ACTIVE"           # not marked hidden on failure
    assert p.shopify_hidden_by_erp is False
