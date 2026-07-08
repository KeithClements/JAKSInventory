"""
tests/test_pricing_grid.py
==========================
MarkupTier model + PricingService.resolve_markup_pct tiered precedence
+ GET /settings/pricing/preview dry-run endpoint.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.models.pricing import MarkupTier
from app.models.product import Product
from app.models.setting import Setting
from app.services.pricing_service import PricingService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, cost, markup_pct=None):
    p = Product(sku=f"PG-{next(_seq)}", title="t", cost=cost, markup_pct=markup_pct)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _activate_grid(db):
    row = db.query(Setting).filter(Setting.key == "markup_tiers_active").first()
    if row:
        row.value = "true"
    else:
        db.add(Setting(key="markup_tiers_active", value="true", label="test"))
    db.commit()


def _deactivate_grid(db):
    row = db.query(Setting).filter(Setting.key == "markup_tiers_active").first()
    if row:
        row.value = "false"
        db.commit()


# ── Seed ──────────────────────────────────────────────────────────────────────

def test_default_tiers_seeded(db):
    tiers = db.query(MarkupTier).order_by(MarkupTier.sort_order).all()
    assert len(tiers) == 4
    assert tiers[0].min_cost == 0.0 and tiers[0].max_cost == 15.0 and tiers[0].markup_pct == 40.0
    assert tiers[3].max_cost is None    # open-ended


def test_seed_is_idempotent(db):
    from app.routers.settings import seed_markup_tiers
    before = db.query(MarkupTier).count()
    seed_markup_tiers(db)
    assert db.query(MarkupTier).count() == before   # no duplicate rows


# ── MarkupTier.matches ────────────────────────────────────────────────────────

def test_tier_matches_bracket(db):
    tiers = db.query(MarkupTier).order_by(MarkupTier.sort_order).all()
    low, mid, high, top = tiers
    assert low.matches(0.0) and low.matches(14.99)
    assert not low.matches(15.0)
    assert mid.matches(15.0) and mid.matches(39.99)
    assert not mid.matches(40.0)
    assert top.matches(75.0) and top.matches(999.0)    # open-ended


# ── PricingService.resolve_markup_pct_for_cost ────────────────────────────────

def test_tiered_lookup_all_brackets(db):
    svc = PricingService(db)
    assert svc.resolve_markup_pct_for_cost(5.0)   == 40.0
    assert svc.resolve_markup_pct_for_cost(15.0)  == 35.0
    assert svc.resolve_markup_pct_for_cost(40.0)  == 32.5
    assert svc.resolve_markup_pct_for_cost(100.0) == 30.0


def test_tiered_fallback_when_no_tier_matches(db):
    # Temporarily remove all tiers to force fallback to flat default
    db.query(MarkupTier).delete(); db.commit()
    svc = PricingService(db)
    flat = svc.default_markup_pct()
    assert svc.resolve_markup_pct_for_cost(50.0) == flat
    # Restore
    from app.routers.settings import seed_markup_tiers, _DEFAULT_TIERS
    from app.models.pricing import MarkupTier as MT
    for min_c, max_c, pct, order in _DEFAULT_TIERS:
        db.add(MT(min_cost=min_c, max_cost=max_c, markup_pct=pct, sort_order=order))
    db.commit()


# ── PricingService.resolve_markup_pct precedence ──────────────────────────────

def test_per_product_markup_always_wins(db):
    _activate_grid(db)
    p = _product(db, cost=5.0, markup_pct=20.0)   # would be 40 from tier
    assert PricingService(db).resolve_markup_pct(p) == 20.0
    _deactivate_grid(db)


def test_grid_used_when_active_and_no_product_markup(db):
    _activate_grid(db)
    p = _product(db, cost=5.0, markup_pct=None)   # cost < 15 → 40%
    assert PricingService(db).resolve_markup_pct(p) == 40.0
    _deactivate_grid(db)


def test_flat_default_when_grid_inactive(db):
    _deactivate_grid(db)
    p = _product(db, cost=5.0, markup_pct=None)
    flat = PricingService(db).default_markup_pct()
    assert PricingService(db).resolve_markup_pct(p) == flat


def test_zero_markup_not_treated_as_unset(db):
    # markup_pct=0 means "sell at cost" — must win over both grid and flat default
    _activate_grid(db)
    p = _product(db, cost=5.0, markup_pct=0.0)
    assert PricingService(db).resolve_markup_pct(p) == 0.0
    _deactivate_grid(db)


# ── GET /settings/pricing/preview ─────────────────────────────────────────────

def test_preview_returns_200_json(client):
    r = client.get("/settings/pricing/preview")
    assert r.status_code == 200
    data = r.json()
    assert "tiers" in data and "affected_count" in data
    assert "moved_count" in data and "sample" in data


def test_preview_detects_price_movement(client, db):
    # Create a product whose flat-default sell differs from the grid sell
    flat = PricingService(db).default_markup_pct()
    # cost=5 → grid gives 40%; flat default is 30 by seed — they differ
    p = _product(db, cost=5.0, markup_pct=None)
    r = client.get("/settings/pricing/preview")
    data = r.json()
    moved_skus = {row["sku"] for row in data["sample"]}
    if flat != 40.0:      # only true if flat default != grid tier for cost=5
        assert p.sku in moved_skus
    # Preview never activates the grid
    assert not PricingService(db).markup_tiers_active()


def test_preview_excludes_products_with_explicit_markup(client, db):
    # Products with markup_pct set are unaffected → shouldn't appear in sample
    p = _product(db, cost=5.0, markup_pct=15.0)   # explicit → excluded
    r = client.get("/settings/pricing/preview")
    moved_skus = {row["sku"] for row in r.json()["sample"]}
    assert p.sku not in moved_skus


def test_preview_is_readonly(client, db):
    from app.utils import calc_sell_price
    p = _product(db, cost=5.0, markup_pct=None)
    sell_before = p.selling_price
    client.get("/settings/pricing/preview")
    db.refresh(p)
    assert p.selling_price == sell_before   # nothing changed
    assert not PricingService(db).markup_tiers_active()
