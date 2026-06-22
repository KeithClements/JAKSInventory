"""
tests/test_pricing_service.py
=============================
Unit tests for PricingService — the centralised money-math layer used by
QuoteService, InvoiceService and SalesOrderService.

Why this file exists
--------------------
`app/services/pricing_service.py` had ZERO test coverage despite feeding the
sell price, margin, surcharge, tax and grand-total numbers on every quote,
sales order and invoice. The math is pure and stateless (no DB writes for the
calculators), which makes it both high-impact-if-wrong and cheap to pin down.

Coverage
--------
  - calculate_sell_price / calculate_margin_pct (incl. zero guards + rounding)
  - calculate_markup_from_margin (incl. the >=100% ValueError + round-trip)
  - apply_discount, calculate_cc_surcharge, calculate_fuel_service_charge
  - calculate_tax, calculate_invoice_total (rounding of summed components)
  - get_best_vendor_cost (preferred → cheapest-active fallback → None)
  - get_sell_price_for_product (price_override > markup override > product > default)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import __all_models__  # noqa: F401 — registers every model on Base
from app.database import Base

import pytest

from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.services.pricing_service import PricingService

# ── Test isolation (mirrors conftest pattern) ─────────────────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=_engine)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def _activate_engine():
    from tests.conftest import activate

    activate(_engine)


@pytest.fixture()
def db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def pricing(db):
    return PricingService(db)


# ── calculate_sell_price ──────────────────────────────────────────────────────


class TestCalculateSellPrice:
    def test_standard_markup(self, pricing):
        # 30% markup on $100 cost → $130.00
        assert pricing.calculate_sell_price(100.0, 30.0) == 130.0

    def test_zero_markup_returns_cost(self, pricing):
        assert pricing.calculate_sell_price(49.99, 0.0) == 49.99

    def test_rounds_to_two_places(self, pricing):
        # 33.33 * 1.30 = 43.329 → rounds to 43.33
        assert pricing.calculate_sell_price(33.33, 30.0) == 43.33

    def test_zero_cost(self, pricing):
        assert pricing.calculate_sell_price(0.0, 30.0) == 0.0


# ── calculate_margin_pct ──────────────────────────────────────────────────────


class TestCalculateMarginPct:
    def test_basic_margin(self, pricing):
        # cost 100, sell 130 → margin = 30/130 = 23.0769 → 23.1
        assert pricing.calculate_margin_pct(100.0, 130.0) == 23.1

    def test_half_margin(self, pricing):
        # cost 50, sell 100 → 50% margin
        assert pricing.calculate_margin_pct(50.0, 100.0) == 50.0

    def test_zero_cost_guard(self, pricing):
        assert pricing.calculate_margin_pct(0.0, 100.0) == 0.0

    def test_zero_sell_guard(self, pricing):
        assert pricing.calculate_margin_pct(100.0, 0.0) == 0.0


# ── calculate_markup_from_margin ──────────────────────────────────────────────


class TestMarkupFromMargin:
    def test_fifty_percent_margin_is_hundred_percent_markup(self, pricing):
        assert pricing.calculate_markup_from_margin(50.0) == 100.0

    def test_twenty_percent_margin(self, pricing):
        # 0.20 / 0.80 * 100 = 25.0
        assert pricing.calculate_markup_from_margin(20.0) == 25.0

    def test_margin_at_or_above_100_raises(self, pricing):
        with pytest.raises(ValueError, match="less than 100"):
            pricing.calculate_markup_from_margin(100.0)
        with pytest.raises(ValueError):
            pricing.calculate_markup_from_margin(150.0)

    def test_round_trip_margin_to_markup_to_margin(self, pricing):
        # Target a 25% margin: derive the markup, apply it, recover the margin.
        markup = pricing.calculate_markup_from_margin(25.0)
        sell = pricing.calculate_sell_price(100.0, markup)
        assert pricing.calculate_margin_pct(100.0, sell) == 25.0


# ── apply_discount ────────────────────────────────────────────────────────────


class TestApplyDiscount:
    def test_ten_percent_off(self, pricing):
        assert pricing.apply_discount(100.0, 10.0) == 90.0

    def test_zero_discount_is_noop(self, pricing):
        assert pricing.apply_discount(123.45, 0.0) == 123.45

    def test_rounds_to_two_places(self, pricing):
        # 19.99 * 0.85 = 16.9915 → 16.99
        assert pricing.apply_discount(19.99, 15.0) == 16.99


# ── surcharges, fuel/service charge, tax ──────────────────────────────────────


class TestSurchargeAndTax:
    def test_cc_surcharge(self, pricing):
        assert pricing.calculate_cc_surcharge(1000.0, 3.0) == 30.0

    def test_cc_surcharge_rounds(self, pricing):
        # 99.99 * 0.03 = 2.9997 → 3.00
        assert pricing.calculate_cc_surcharge(99.99, 3.0) == 3.0

    def test_fuel_service_charge(self, pricing):
        assert pricing.calculate_fuel_service_charge(500.0, 2.5) == 12.5

    def test_tax(self, pricing):
        assert pricing.calculate_tax(1000.0, 8.25) == 82.5

    def test_tax_rounds(self, pricing):
        # 19.99 * 0.0825 = 1.649175 → 1.65
        assert pricing.calculate_tax(19.99, 8.25) == 1.65

    def test_zero_rate_is_zero(self, pricing):
        assert pricing.calculate_tax(1000.0, 0.0) == 0.0
        assert pricing.calculate_cc_surcharge(1000.0, 0.0) == 0.0


# ── calculate_invoice_total ───────────────────────────────────────────────────


class TestInvoiceTotal:
    def test_sums_all_components(self, pricing):
        total = pricing.calculate_invoice_total(
            subtotal=1000.0,
            tax_amount=82.5,
            core_total=50.0,
            shipping_charge=25.0,
            cc_surcharge=30.0,
            fsc_amount=12.5,
        )
        assert total == 1200.0

    def test_only_subtotal(self, pricing):
        total = pricing.calculate_invoice_total(
            subtotal=199.99,
            tax_amount=0.0,
            core_total=0.0,
            shipping_charge=0.0,
            cc_surcharge=0.0,
            fsc_amount=0.0,
        )
        assert total == 199.99

    def test_rounding_of_summed_components(self, pricing):
        # 10.001 + 0.004 = 10.005 → rounds to 10.0 (banker's rounding on .005)
        total = pricing.calculate_invoice_total(10.001, 0.004, 0.0, 0.0, 0.0, 0.0)
        assert total == pytest.approx(10.0, abs=0.01)


# ── get_best_vendor_cost (DB-backed) ──────────────────────────────────────────


def _make_product(db, sku="JAKS-TEST-1", cost=100.0, **kw):
    p = Product(sku=sku, title="Test Part", cost=cost, **kw)
    db.add(p)
    db.flush()
    return p


def _make_vendor(db, code="ACME"):
    v = Vendor(name=f"Vendor {code}", vendor_code=code)
    db.add(v)
    db.flush()
    return v


def _add_source(db, product, vendor, vendor_cost, is_preferred=False, is_active=True):
    s = ProductVendorSource(
        product_id=product.id,
        vendor_id=vendor.id,
        vendor_cost=vendor_cost,
        is_preferred=is_preferred,
        is_active=is_active,
    )
    db.add(s)
    db.flush()
    return s


class TestGetBestVendorCost:
    def test_prefers_preferred_active_source(self, db, pricing):
        p = _make_product(db, sku="JAKS-BVC-1")
        v1 = _make_vendor(db, "AAA")
        v2 = _make_vendor(db, "BBB")
        # Preferred source is NOT the cheapest, but it must win.
        _add_source(db, p, v1, vendor_cost=90.0, is_preferred=True)
        _add_source(db, p, v2, vendor_cost=70.0, is_preferred=False)
        assert pricing.get_best_vendor_cost(p.id) == 90.0

    def test_falls_back_to_cheapest_active_when_no_preferred(self, db, pricing):
        p = _make_product(db, sku="JAKS-BVC-2")
        v1 = _make_vendor(db, "CCC")
        v2 = _make_vendor(db, "DDD")
        _add_source(db, p, v1, vendor_cost=88.0)
        _add_source(db, p, v2, vendor_cost=55.0)
        assert pricing.get_best_vendor_cost(p.id) == 55.0

    def test_ignores_inactive_preferred(self, db, pricing):
        p = _make_product(db, sku="JAKS-BVC-3")
        v1 = _make_vendor(db, "EEE")
        v2 = _make_vendor(db, "FFF")
        # Preferred but inactive → must be skipped; active fallback used.
        _add_source(db, p, v1, vendor_cost=10.0, is_preferred=True, is_active=False)
        _add_source(db, p, v2, vendor_cost=42.0)
        assert pricing.get_best_vendor_cost(p.id) == 42.0

    def test_returns_none_when_no_sources(self, db, pricing):
        p = _make_product(db, sku="JAKS-BVC-4")
        assert pricing.get_best_vendor_cost(p.id) is None


# ── get_sell_price_for_product (DB-backed) ────────────────────────────────────


class TestGetSellPriceForProduct:
    def test_price_override_wins(self, db, pricing):
        p = _make_product(db, sku="JAKS-SPP-1", cost=100.0, markup_pct=30.0)
        p.price_override = 175.0
        db.flush()
        assert pricing.get_sell_price_for_product(p) == 175.0

    def test_uses_explicit_markup_override(self, db, pricing):
        p = _make_product(db, sku="JAKS-SPP-2", cost=100.0, markup_pct=30.0)
        # Override beats the product's own markup_pct → 100 * 1.50
        assert pricing.get_sell_price_for_product(p, markup_pct_override=50.0) == 150.0

    def test_uses_product_markup_when_no_override(self, db, pricing):
        p = _make_product(db, sku="JAKS-SPP-3", cost=100.0, markup_pct=40.0)
        assert pricing.get_sell_price_for_product(p) == 140.0

    def test_defaults_to_thirty_percent_when_markup_missing(self, db, pricing):
        p = _make_product(db, sku="JAKS-SPP-4", cost=100.0, markup_pct=None)
        assert pricing.get_sell_price_for_product(p) == 130.0

    def test_zero_price_override_is_ignored(self, db, pricing):
        p = _make_product(db, sku="JAKS-SPP-5", cost=100.0, markup_pct=30.0)
        p.price_override = 0.0  # falsy → fall through to markup
        db.flush()
        assert pricing.get_sell_price_for_product(p) == 130.0
