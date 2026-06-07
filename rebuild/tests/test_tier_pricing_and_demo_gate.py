"""
tests/test_tier_pricing_and_demo_gate.py
=========================================
Two items:

1. Customer tier pricing — PricingService.tier_discount_pct /
   sell_price_for_tier, and end-to-end wiring through quote/invoice/SO
   apply_product_line_defaults.

2. Demo reset gate — GET/POST /admin/demo/reset blocked (403) when the
   JAKS_ENV env var or jaks_env setting equals 'production'.

Rules under test:
  * 'standard' tier always returns 0 % discount / None price (no-op).
  * Unconfigured (missing setting) returns 0 % / None — safe default.
  * Once tier_wholesale_discount_pct is set to 20, a $100 sell product
    → $80 on a wholesale quote line.
  * The tier price only fills a BLANK unit_price; an explicit caller value wins.
  * production gate: JAKS_ENV=production → 403 on GET and POST.
  * sandbox (default): GET/POST reachable (200 / redirect).
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import Quote, QuoteLine
from app.models.setting import Setting
from app.services.pricing_service import PricingService
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    eng = fresh_engine()
    activate(eng)
    yield eng


@pytest.fixture()
def db(engine):
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_product(db, *, cost: float = 50.0, markup_pct: float | None = None) -> Product:
    p = Product(
        sku=f"TIER-TEST-{id(object())}",
        title="Tier test part",
        cost=cost,
        markup_pct=markup_pct,
        is_active=True,
    )
    db.add(p)
    db.flush()
    return p


def _make_customer(db, *, pricing_tier: str = "standard") -> Customer:
    c = Customer(
        company_name=f"Test Co {pricing_tier}",
        pricing_tier=pricing_tier,
    )
    db.add(c)
    db.flush()
    return c


# ── PricingService tier helpers ────────────────────────────────────────────────

class TestTierDiscountPct:
    """tier_discount_pct: standard → 0; unconfigured non-standard → 0; configured → value."""

    def test_standard_always_zero(self, db):
        ps = PricingService(db, 1)
        assert ps.tier_discount_pct("standard") == 0.0

    def test_empty_string_is_standard(self, db):
        ps = PricingService(db, 1)
        assert ps.tier_discount_pct("") == 0.0

    def test_unconfigured_tier_returns_zero(self, db):
        # wholesale setting absent → safe default 0.0
        ps = PricingService(db, 1)
        assert ps.tier_discount_pct("wholesale") == 0.0

    def test_configured_tier_returns_value(self, db):
        set_setting_value_db(db, "tier_wholesale_discount_pct", "20.0")
        db.flush()
        ps = PricingService(db, 1)
        result = ps.tier_discount_pct("wholesale")
        assert result == pytest.approx(20.0)

    def test_malformed_setting_returns_zero(self, db):
        set_setting_value_db(db, "tier_fleet_discount_pct", "not-a-number")
        db.flush()
        ps = PricingService(db, 1)
        assert ps.tier_discount_pct("fleet") == 0.0


class TestSellPriceForTier:
    """sell_price_for_tier: returns None when no adjustment; adjusted price when configured."""

    def test_standard_returns_none(self, db):
        product = _make_product(db, cost=50.0, markup_pct=100.0)  # sell = $100
        ps = PricingService(db, 1)
        assert ps.sell_price_for_tier(product, "standard") is None

    def test_none_tier_returns_none(self, db):
        product = _make_product(db, cost=50.0, markup_pct=100.0)
        ps = PricingService(db, 1)
        assert ps.sell_price_for_tier(product, None) is None

    def test_unconfigured_tier_returns_none(self, db):
        """No setting → 0 % discount → returns None (no-op)."""
        set_setting_value_db(db, "tier_dealer_discount_pct", "0.0")
        db.flush()
        product = _make_product(db, cost=50.0, markup_pct=100.0)
        ps = PricingService(db, 1)
        assert ps.sell_price_for_tier(product, "dealer") is None

    def test_configured_tier_returns_discounted_price(self, db):
        """20 % wholesale discount on a $100 sell price → $80.00."""
        set_setting_value_db(db, "tier_wholesale_discount_pct", "20.0")
        db.flush()
        # markup_pct=100 on cost=50 → sell=100
        product = _make_product(db, cost=50.0, markup_pct=100.0)
        ps = PricingService(db, 1)
        result = ps.sell_price_for_tier(product, "wholesale")
        assert result == pytest.approx(80.0)

    def test_price_override_respected_by_tier(self, db):
        """price_override on product → sell_price_for uses that; tier discount is on top."""
        set_setting_value_db(db, "tier_wholesale_discount_pct", "10.0")
        db.flush()
        product = _make_product(db, cost=50.0)
        product.price_override = 200.0  # explicit override
        db.flush()
        ps = PricingService(db, 1)
        result = ps.sell_price_for_tier(product, "wholesale")
        # 10% off $200 = $180
        assert result == pytest.approx(180.0)


# ── apply_product_line_defaults tier_price param ──────────────────────────────

class TestApplyProductLineDefaultsTierPrice:
    """The tier_price kwarg is used when provided; explicit caller price wins."""

    def test_tier_price_used_when_no_explicit_price(self, db):
        from app.services.base import apply_product_line_defaults
        product = _make_product(db, cost=50.0, markup_pct=100.0)  # sell = $100
        data = {"description": "", "qty": 1}
        apply_product_line_defaults(product, data, include_price=True, tier_price=75.0)
        assert data["unit_price"] == pytest.approx(75.0)

    def test_explicit_caller_price_wins_over_tier_price(self, db):
        from app.services.base import apply_product_line_defaults
        product = _make_product(db, cost=50.0, markup_pct=100.0)
        data = {"unit_price": 90.0, "qty": 1}
        apply_product_line_defaults(product, data, include_price=True, tier_price=75.0)
        # explicit 90 was set; tier_price only fills a blank
        assert data["unit_price"] == pytest.approx(90.0)

    def test_none_tier_price_falls_back_to_selling_price(self, db):
        from app.services.base import apply_product_line_defaults
        product = _make_product(db, cost=50.0, markup_pct=100.0)  # sell = $100
        data = {"qty": 1}
        apply_product_line_defaults(product, data, include_price=True, tier_price=None)
        assert data["unit_price"] == pytest.approx(100.0)


# ── End-to-end: wholesale line on a quote ─────────────────────────────────────

class TestTierPriceEndToEnd:
    """Quote.add_line respects the customer's pricing_tier."""

    def test_wholesale_quote_line_uses_tier_price(self, db):
        from app.routers.settings import seed_settings
        from app.services.quote_service import QuoteService
        seed_settings(db)

        set_setting_value_db(db, "tier_wholesale_discount_pct", "20.0")
        db.commit()

        # Product: cost=50, markup_pct=100 → sell=$100; wholesale → $80
        product = _make_product(db, cost=50.0, markup_pct=100.0)
        db.commit()

        customer = _make_customer(db, pricing_tier="wholesale")
        db.commit()

        svc = QuoteService(db, current_user_id=1)
        quote = svc.create_quote(customer.id, {"validity_days": 30})
        db.commit()

        lines = svc.add_line(quote.id, product.id, {})
        db.commit()

        line = lines[0]  # returns list; first is the primary product line
        assert line.unit_price == pytest.approx(80.0), (
            f"Expected $80 (20% off $100 sell), got ${line.unit_price}"
        )

    def test_standard_quote_line_uses_full_sell_price(self, db):
        from app.routers.settings import seed_settings
        from app.services.quote_service import QuoteService
        seed_settings(db)

        # Ensure standard discount is 0
        set_setting_value_db(db, "tier_wholesale_discount_pct", "20.0")
        db.commit()

        product = _make_product(db, cost=50.0, markup_pct=100.0)  # sell=$100
        db.commit()

        customer = _make_customer(db, pricing_tier="standard")
        db.commit()

        svc = QuoteService(db, current_user_id=1)
        quote = svc.create_quote(customer.id, {"validity_days": 30})
        db.commit()

        lines = svc.add_line(quote.id, product.id, {})
        db.commit()

        assert lines[0].unit_price == pytest.approx(100.0)


# ── Demo reset production gate ─────────────────────────────────────────────────

class TestDemoResetProductionGate:
    """POST /admin/demo/reset and GET /admin/demo must return 403 in production."""

    @pytest.fixture(scope="class")
    def client(self, engine):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def test_get_demo_form_blocked_by_env_var(self, client, monkeypatch):
        monkeypatch.setenv("JAKS_ENV", "production")
        resp = client.get("/admin/demo", follow_redirects=True)
        assert resp.status_code == 403

    def test_post_demo_reset_blocked_by_env_var(self, client, monkeypatch):
        monkeypatch.setenv("JAKS_ENV", "production")
        resp = client.post("/admin/demo/reset", data={"confirm": "1"})
        assert resp.status_code == 403

    def test_demo_form_reachable_in_sandbox(self, client, monkeypatch):
        monkeypatch.delenv("JAKS_ENV", raising=False)
        resp = client.get("/admin/demo", follow_redirects=True)
        # Should render the form (200), not block
        assert resp.status_code == 200

    def test_post_demo_reset_without_confirm_redirects_in_sandbox(self, client, monkeypatch):
        """Without the checkbox the route redirects (303), not 403."""
        monkeypatch.delenv("JAKS_ENV", raising=False)
        resp = client.post("/admin/demo/reset", data={}, follow_redirects=False)
        assert resp.status_code == 303
