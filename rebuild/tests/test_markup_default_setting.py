"""
tests/test_markup_default_setting.py
====================================
O5 — the global default markup now comes from the default_markup_pct SETTING,
not a hardcoded 30. Locks the resolver semantics and the surfaces that use it.

Key rules under test:
  * product with its own markup_pct → uses it (0% is a REAL value → sell at cost).
  * product with markup_pct = NULL → uses the default_markup_pct setting.
  * changing the setting changes the resolved price (no hardcoded 30).
  * search results' suggested_sell reflects the setting-backed price.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.models.customer import Customer  # noqa: F401  (model registration)
from app.models.product import Product
from app.models.setting import Setting
from app.services.pricing_service import PricingService
from app.services.search_service import SearchService
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    # Seed the setting the app would seed on startup.
    set_setting_value_db(s, "default_markup_pct", "30.0", label="Default Markup %")
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _product(db, *, cost, markup_pct):
    p = Product(sku=f"MK-{markup_pct}-{cost}", title="Part", cost=cost,
                markup_pct=markup_pct, qty_on_hand=1, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── resolver semantics ────────────────────────────────────────────────────────

def test_own_markup_is_used(db):
    p = _product(db, cost=100.0, markup_pct=50.0)
    assert PricingService(db).resolve_markup_pct(p) == 50.0
    assert PricingService(db).sell_price_for(p) == 150.0


def test_zero_markup_is_honored_not_defaulted(db):
    # 0% is a real, deliberate value → sell at cost (NOT bumped to 30%).
    p = _product(db, cost=100.0, markup_pct=0.0)
    assert PricingService(db).resolve_markup_pct(p) == 0.0
    assert PricingService(db).sell_price_for(p) == 100.0


def test_null_markup_falls_back_to_setting(db):
    p = _product(db, cost=100.0, markup_pct=None)
    # default setting is 30.0 → 130.00
    assert PricingService(db).resolve_markup_pct(p) == 30.0
    assert PricingService(db).sell_price_for(p) == 130.0


def test_changing_the_setting_changes_the_price(db):
    p = _product(db, cost=100.0, markup_pct=None)
    set_setting_value_db(db, "default_markup_pct", "42.0")
    db.commit()
    assert PricingService(db).default_markup_pct() == 42.0
    assert PricingService(db).sell_price_for(p) == 142.0  # was 130 at 30%


def test_price_override_wins(db):
    p = _product(db, cost=100.0, markup_pct=None)
    p.price_override = 999.0
    db.commit()
    assert PricingService(db).sell_price_for(p) == 999.0


def test_missing_setting_uses_hard_fallback(db):
    # Delete the setting row entirely → resolver must not crash, uses 30.0 literal.
    db.query(Setting).filter(Setting.key == "default_markup_pct").delete()
    db.commit()
    p = _product(db, cost=100.0, markup_pct=None)
    assert PricingService(db).default_markup_pct() == 30.0
    assert PricingService(db).sell_price_for(p) == 130.0


# ── search surface reflects the setting ───────────────────────────────────────

def test_search_suggested_sell_uses_setting(db):
    _product(db, cost=100.0, markup_pct=None)  # SKU MK-None-100.0
    set_setting_value_db(db, "default_markup_pct", "20.0")
    db.commit()
    results = SearchService(db).search_products("MK-None")
    assert results, "expected the seeded product in search results"
    hit = next(r for r in results if r.current_cost == 100.0)
    # 100 * 1.20 = 120.00 — driven by the setting, not a hardcoded 30.
    assert hit.suggested_sell == 120.0
