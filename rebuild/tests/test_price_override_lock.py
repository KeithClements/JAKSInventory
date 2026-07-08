"""
tests/test_price_override_lock.py
=================================
Scraper-audit bug #11 — operator price lock (products.price_override_locked,
Alembic 0009).

The scraper's nightly push (scripts/import_and_push.py →
ProductImportService.pricing_update_sell) writes product.price_override — the
SAME field an operator edits by hand on the product screen — so a hand-set
price was silently clobbered on the next nightly run. The fix:

  * ProductService.update_product sets price_override_locked=1 automatically
    when an operator CHANGES price_override to a non-empty value, and clears
    the lock when they clear the override. Re-posting the SAME stored value
    (the autosave re-submits the whole form on every debounce) never toggles
    the lock.
  * pricing_update_sell skips the sell-price write for locked products —
    counted as skipped_price_locked — while cost / availability / manufacturer
    on the same row still apply (they are not operator-owned fields).
  * POST /products/{id}/unlock-price clears JUST the lock (price kept) so the
    feed may manage the price again.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401 — register all models
from app.constants import ProductStatus
from app.main import app
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.services.product_import_service import ProductImportService
from app.services.product_service import ProductService
from tests.conftest import activate, fresh_engine


# Mirror the scraper Shopify export header (kept identical to
# test_r3_pricing_update_sell.py so a format drift is caught in both places).
_HEADER = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Variant SKU", "Variant Grams",
    "Variant Inventory Tracker", "Variant Inventory Qty",
    "Variant Inventory Policy", "Variant Fulfillment Service",
    "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Status",
]


def _row(**kw):
    d = {h: "" for h in _HEADER}
    d.update(kw)
    return [d[h] for h in _HEADER]


def _price_row(sku, price, compare="", *, handle=None):
    return _row(Handle=(handle or sku.lower()),
                Title="Part", Type="ENGINE PARTS", Status="active",
                **{"Variant SKU": sku, "Variant Price": str(price),
                   "Variant Compare At Price": str(compare)})


def _csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(db):
    # Context-manager form triggers the app's startup event (seeds the
    # DEFAULT_USER_ID=1 admin the test-env auth bypass resolves to).
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _make_product(db, sku, *, price=10.00, locked=False, cost=4.50):
    p = Product(sku=sku, title=sku, description=sku,
                price_override=price, price_override_locked=locked,
                cost=cost, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _seed_pai_with_source(db, product_id, *, vendor_cost=4.50):
    v = db.query(Vendor).filter(Vendor.vendor_code == "PAI").first()
    if v is None:
        v = Vendor(name="PAI Industries", vendor_code="PAI",
                   vendor_number="9", is_active=True)
        db.add(v); db.commit(); db.refresh(v)
    src = ProductVendorSource(product_id=product_id, vendor_id=v.id,
                              vendor_cost=vendor_cost, is_active=True,
                              is_preferred=True)
    db.add(src); db.commit()
    return v, src


# ── The feed respects the lock ────────────────────────────────────────────────

def test_locked_price_survives_pricing_update_sell(db):
    """ACCEPTANCE: the operator-locked product keeps its hand-set price through
    an --apply run; the unlocked product in the same file updates normally."""
    locked = _make_product(db, "JAKS-PAI-100", price=25.00, locked=True)
    free = _make_product(db, "JAKS-PAI-101", price=10.00, locked=False)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-100", "12.50"),
              _price_row("JAKS-PAI-101", "12.50")]),
        dry_run=False)
    assert s["skipped_price_locked"] == 1
    assert s["prices_updated"] == 1
    db.refresh(locked); db.refresh(free)
    assert locked.price_override == 25.00, "operator-set price must never be clobbered"
    assert locked.price_override_locked is True, "the feed never unlocks"
    assert free.price_override == 12.50


def test_locked_skip_counted_in_dry_run_too(db):
    """The dry-run report must match what --apply would do (cross-repo
    contract), so skipped_price_locked increments on dry runs as well."""
    _make_product(db, "JAKS-PAI-110", price=25.00, locked=True)
    s = ProductImportService(db, None).pricing_update_sell(
        _csv([_price_row("JAKS-PAI-110", "12.50")]), dry_run=True)
    assert s["skipped_price_locked"] == 1
    assert s["prices_updated"] == 0


def test_locked_row_not_double_counted_as_unchanged(db):
    _make_product(db, "JAKS-PAI-115", price=25.00, locked=True)
    s = ProductImportService(db, None).pricing_update_sell(
        _csv([_price_row("JAKS-PAI-115", "12.50")]), dry_run=False)
    assert s["skipped_price_locked"] == 1
    assert s["unchanged"] == 0


def test_locked_product_still_gets_cost_update(db):
    """The lock protects ONLY the sell price — the same row's vendor cost
    still lands on the vendor source (cost is not an operator-owned field)."""
    p = _make_product(db, "JAKS-PAI-120", price=25.00, locked=True)
    _, src = _seed_pai_with_source(db, p.id, vendor_cost=2.50)
    svc = ProductImportService(db, None)

    header_with_cost = _HEADER + ["Our Cost"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_cost)
    w.writerow(_price_row("JAKS-PAI-120", "12.50") + ["3.75"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["skipped_price_locked"] == 1
    assert s["costs_updated"] == 1
    db.refresh(p); db.refresh(src)
    assert p.price_override == 25.00
    assert src.vendor_cost == 3.75


def test_same_feed_price_on_locked_product_counts_unchanged_not_skipped(db):
    """A locked product whose feed price EQUALS the stored price is a normal
    'unchanged' row — skipped_price_locked only counts real suppressed moves."""
    _make_product(db, "JAKS-PAI-130", price=25.00, locked=True)
    s = ProductImportService(db, None).pricing_update_sell(
        _csv([_price_row("JAKS-PAI-130", "25.00")]), dry_run=False)
    assert s["skipped_price_locked"] == 0
    assert s["unchanged"] == 1


# ── The operator edit path sets / clears the lock ─────────────────────────────

def test_operator_price_change_sets_lock(db):
    p = _make_product(db, "JAKS-PAI-200", price=10.00, locked=False)
    ProductService(db, current_user_id=1).update_product(
        p.id, {"price_override": 14.00})
    db.refresh(p)
    assert p.price_override == 14.00
    assert p.price_override_locked is True


def test_resubmitting_same_price_does_not_toggle_lock(db):
    """The autosave re-posts the whole form on every debounce — an UNCHANGED
    price_override must never set (or clear) the lock."""
    p = _make_product(db, "JAKS-PAI-210", price=10.00, locked=False)
    svc = ProductService(db, current_user_id=1)
    svc.update_product(p.id, {"price_override": 10.00, "title": "edited title"})
    db.refresh(p)
    assert p.price_override_locked is False, "same value must not lock"

    p.price_override_locked = True
    db.commit()
    svc.update_product(p.id, {"price_override": 10.00, "title": "edited again"})
    db.refresh(p)
    assert p.price_override_locked is True, "same value must not unlock either"


def test_clearing_override_clears_lock(db):
    p = _make_product(db, "JAKS-PAI-220", price=14.00, locked=True)
    ProductService(db, current_user_id=1).update_product(
        p.id, {"price_override": None})
    db.refresh(p)
    assert p.price_override is None
    assert p.price_override_locked is False


def test_first_price_on_unpriced_product_sets_lock(db):
    """price_override None → a value is a CHANGE (operator hand-priced it)."""
    p = _make_product(db, "JAKS-PAI-230", price=None, locked=False)
    ProductService(db, current_user_id=1).update_product(
        p.id, {"price_override": 19.99})
    db.refresh(p)
    assert p.price_override_locked is True


# ── The unlock affordance ─────────────────────────────────────────────────────

def test_unlock_route_clears_lock_and_keeps_price(db, client):
    p = _make_product(db, "JAKS-PAI-300", price=25.00, locked=True)
    r = client.post(f"/products/{p.id}/unlock-price", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/products/{p.id}")
    db.expire_all()
    p2 = db.get(Product, p.id)
    assert p2.price_override == 25.00, "unlock keeps the price"
    assert p2.price_override_locked is False

    # …and the feed may manage the price again after unlock.
    s = ProductImportService(db, None).pricing_update_sell(
        _csv([_price_row("JAKS-PAI-300", "12.50")]), dry_run=False)
    assert s["prices_updated"] == 1
    assert s["skipped_price_locked"] == 0
    db.expire_all()
    assert db.get(Product, p.id).price_override == 12.50


def test_unlock_service_is_idempotent(db):
    p = _make_product(db, "JAKS-PAI-310", price=25.00, locked=False)
    ProductService(db, current_user_id=1).unlock_price_override(p.id)
    db.refresh(p)
    assert p.price_override_locked is False
    assert p.price_override == 25.00
