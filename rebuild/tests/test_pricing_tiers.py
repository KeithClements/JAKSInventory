"""
tests/test_pricing_tiers.py
===========================
PricingService cost-bracket markup grid (the owner's tiered pricing) — the
resolution rules that decide a product's markup %:

  1. product.markup_pct ALWAYS wins when set — even 0 % (a real "sell at cost").
  2. else, IF markup_tiers_active → the MarkupTier grid, by COGS bracket.
  3. else → the flat default_markup_pct setting (nothing re-prices).

Default grid (settings.seed_markup_tiers, brackets are [min, max) — min inclusive):
    < $15 → 40 % · $15–40 → 35 % · $40–75 → 32.5 % · $75+ → 30 %

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_pricing_tiers.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.models.product import Product
from app.routers.settings import seed_markup_tiers
from app.services.pricing_service import PricingService
from app.settings_utils import set_setting_value_db
from app.utils import calc_sell_price
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    SessionLocal = activate(fresh_engine())
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, cost, markup_pct=None) -> Product:
    p = Product(sku=f"PT-{next(_seq)}", title="part", cost=cost, markup_pct=markup_pct)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _activate_tiers(db, on=True):
    seed_markup_tiers(db)
    set_setting_value_db(db, "markup_tiers_active", "true" if on else "false")
    db.commit()


# ── The grid: cost → markup % ────────────────────────────────────────────────

def test_tier_grid_markup_by_cost(db):
    seed_markup_tiers(db)
    svc = PricingService(db)
    assert svc.resolve_markup_pct_for_cost(10.0) == 40.0
    assert svc.resolve_markup_pct_for_cost(30.0) == 35.0
    assert svc.resolve_markup_pct_for_cost(60.0) == 32.5
    assert svc.resolve_markup_pct_for_cost(90.0) == 30.0


def test_tier_boundaries_are_min_inclusive(db):
    seed_markup_tiers(db)
    svc = PricingService(db)
    # a cost landing exactly on a boundary belongs to the UPPER tier ([min, max))
    assert svc.resolve_markup_pct_for_cost(15.0) == 35.0
    assert svc.resolve_markup_pct_for_cost(40.0) == 32.5
    assert svc.resolve_markup_pct_for_cost(75.0) == 30.0
    # …a hair below stays in the lower tier
    assert svc.resolve_markup_pct_for_cost(14.99) == 40.0
    assert svc.resolve_markup_pct_for_cost(39.99) == 35.0
    assert svc.resolve_markup_pct_for_cost(74.99) == 32.5


# ── Resolution precedence on a product ───────────────────────────────────────

def test_per_product_markup_overrides_the_grid(db):
    _activate_tiers(db, on=True)
    svc = PricingService(db)
    p = _product(db, cost=10.0, markup_pct=99.0)   # grid for $10 would be 40 %
    assert svc.resolve_markup_pct(p) == 99.0
    assert svc.sell_price_for(p) == calc_sell_price(10.0, 99.0)
    # 0 % is a real override (sell at cost), not "unset"
    p0 = _product(db, cost=10.0, markup_pct=0.0)
    assert svc.resolve_markup_pct(p0) == 0.0


def test_unmarked_product_uses_grid_when_active(db):
    _activate_tiers(db, on=True)
    svc = PricingService(db)
    p = _product(db, cost=60.0)                     # no markup_pct → grid → 32.5 %
    assert svc.resolve_markup_pct(p) == 32.5
    assert svc.sell_price_for(p) == calc_sell_price(60.0, 32.5)


def test_tiers_inactive_keeps_flat_default(db):
    _activate_tiers(db, on=False)
    set_setting_value_db(db, "default_markup_pct", "20")   # distinct from any tier
    db.commit()
    svc = PricingService(db)
    p = _product(db, cost=60.0)                     # grid would say 32.5, flag OFF → flat 20
    assert svc.resolve_markup_pct(p) == 20.0
    assert svc.sell_price_for(p) == calc_sell_price(60.0, 20.0)
