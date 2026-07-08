"""
tests/test_zero_cost_vendor_fallback.py
=======================================
QA HIGH #3 — a new/special-order part used to quote and sell at $0.00 until the
first PO receipt, because the estimated sell price keys off ``product.cost`` (the
moving-average COGS, which is 0 pre-receipt) even though the vendor's quoted cost
is on file at ``ProductVendorSource.vendor_cost``.

Fix: ``Product.effective_cost`` falls back to the preferred — else any active —
vendor source cost when COGS is still 0, and both pricing layers
(``Product.selling_price`` model property and ``PricingService.sell_price_for``)
price off that basis. Once stock is received, the real COGS wins again.

Rules under test:
  * cost == 0 + preferred vendor source → prices off vendor_cost × markup.
  * cost == 0 + only a NON-preferred active source → still falls back.
  * cost == 0 + a soft-deleted (is_active=False) source → does NOT use it.
  * cost == 0 + no vendor source at all → stays $0 (unchanged; nothing to use).
  * cost > 0 (received) → uses real COGS even if a cheaper/dearer source exists.
  * price_override still wins over everything.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.models.customer import Customer  # noqa: F401  (model registration)
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.services.pricing_service import PricingService
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    # Match the startup seed so the NULL-markup default resolves to 30%.
    set_setting_value_db(s, "default_markup_pct", "30.0", label="Default Markup %")
    s.commit()
    try:
        yield s
    finally:
        s.close()


_seq = iter(range(1, 10_000))


def _vendor(db) -> Vendor:
    v = Vendor(name=f"PAI {next(_seq)}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db, *, cost, markup_pct=None) -> Product:
    p = Product(sku=f"ZC-{next(_seq)}", title="C15 Cylinder Head", cost=cost,
                markup_pct=markup_pct, qty_on_hand=0, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _source(db, product, vendor, *, vendor_cost, is_preferred=True, is_active=True):
    s = ProductVendorSource(
        product_id=product.id, vendor_id=vendor.id,
        vendor_part_number=product.sku, vendor_sku=product.sku,
        vendor_cost=vendor_cost, is_preferred=is_preferred, is_active=is_active,
    )
    db.add(s); db.commit(); db.refresh(product)
    return s


# ── the reported bug: un-received special-order part ──────────────────────────

def test_unreceived_part_prices_off_preferred_vendor_cost(db):
    # Exactly the QA repro: cost 0, vendor source at $850, default 30% markup.
    p = _product(db, cost=0.0, markup_pct=None)
    _source(db, p, _vendor(db), vendor_cost=850.0)

    assert p.effective_cost == 850.0
    # model property (the quote/SO/invoice line default path)
    assert p.selling_price == 1105.0          # 850 × 1.30 — was $0.00
    # settings-aware service path (search/CSV/pickers/preview)
    assert PricingService(db).sell_price_for(p) == 1105.0


def test_unreceived_part_falls_back_to_any_active_source_when_none_preferred(db):
    p = _product(db, cost=0.0, markup_pct=None)
    _source(db, p, _vendor(db), vendor_cost=400.0, is_preferred=False)

    assert p.effective_cost == 400.0
    assert p.selling_price == 520.0           # 400 × 1.30
    assert PricingService(db).sell_price_for(p) == 520.0


def test_soft_deleted_source_is_not_used_as_fallback(db):
    p = _product(db, cost=0.0, markup_pct=None)
    _source(db, p, _vendor(db), vendor_cost=999.0, is_preferred=True, is_active=False)

    # An inactive (soft-deleted) source must never resurface as a price basis.
    assert p.effective_cost == 0.0
    assert p.selling_price == 0.0
    assert PricingService(db).sell_price_for(p) == 0.0


def test_no_vendor_source_still_zero(db):
    # Nothing on file to fall back to → unchanged $0 (genuinely no cost known).
    p = _product(db, cost=0.0, markup_pct=None)
    assert p.effective_cost == 0.0
    assert p.selling_price == 0.0
    assert PricingService(db).sell_price_for(p) == 0.0


# ── no regression: received stock keeps using real COGS ───────────────────────

def test_received_part_uses_real_cogs_not_vendor_cost(db):
    # COGS is set (received); a vendor source exists at a DIFFERENT number.
    # The moving-average COGS must win — the fallback is only for cost==0.
    p = _product(db, cost=900.0, markup_pct=None)
    _source(db, p, _vendor(db), vendor_cost=850.0)

    assert p.effective_cost == 900.0
    assert p.selling_price == 1170.0          # 900 × 1.30, NOT 850-based
    assert PricingService(db).sell_price_for(p) == 1170.0


def test_price_override_wins_over_vendor_fallback(db):
    p = _product(db, cost=0.0, markup_pct=None)
    _source(db, p, _vendor(db), vendor_cost=850.0)
    p.price_override = 1500.0
    db.commit()

    assert p.selling_price == 1500.0
    assert PricingService(db).sell_price_for(p) == 1500.0


def test_zero_markup_still_honored_with_vendor_fallback(db):
    # 0% markup is a real value → sell AT the fallback cost, not bumped to 30%.
    p = _product(db, cost=0.0, markup_pct=0.0)
    _source(db, p, _vendor(db), vendor_cost=850.0)

    assert p.selling_price == 850.0
    assert PricingService(db).sell_price_for(p) == 850.0
