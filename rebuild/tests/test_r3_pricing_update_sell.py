"""
tests/test_r3_pricing_update_sell.py
====================================
ProductImportService.pricing_update_sell — re-run the scraper's standard
Shopify export (Variant SKU + Variant Price + Variant Compare At Price)
against existing products. Updates OUR sell + compare-at only; never cost,
never creates products, never touches vendor sources or competitor prices.

Shopify image-only rows (blank SKU + blank price columns) are silently
skipped as "image_rows_skipped", not counted against skipped_no_sku.

Optional max_change_pct rail skips any product whose new price would move
more than that % from its current price_override, surfacing the swing in
over_threshold_sample so an owner can catch a bad scraper run.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401 — register all models
from app.constants import ProductStatus
from app.models.product import Product
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


# The exact header order from the scraper's Shopify export
# (C:\Users\keith\PAI Info\exports\pai_shopify_*.csv) — pinning it so a
# scraper format change is caught here, not in production.
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


def _csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _price_row(sku, price, compare="", *, handle=None):
    return _row(Handle=(handle or sku.lower()),
                Title="Part", Type="ENGINE PARTS", Status="active",
                **{"Variant SKU": sku, "Variant Price": str(price),
                   "Variant Compare At Price": str(compare)})


def _image_row(handle, image_url, position):
    """Shopify image-only follow-up row: every product field blank
    EXCEPT Handle, Image Src, Image Position."""
    return _row(Handle=handle, **{"Image Src": image_url,
                                  "Image Position": str(position)})


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _make_product(db, sku, *, price=10.00, compare=None, cost=4.50):
    p = Product(sku=sku, title=sku, description=sku,
                price_override=price, compare_at_price=compare,
                cost=cost, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── Happy path ────────────────────────────────────────────────────────────────

def test_dry_run_writes_nothing(db):
    p = _make_product(db, "JAKS-PAI-100", price=10.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(_csv([_price_row("JAKS-PAI-100", "12.50")]),
                                dry_run=True)
    assert s["matched"] == 1
    assert s["prices_updated"] == 1
    db.refresh(p)
    assert p.price_override == 10.00, "dry-run must not write"


def test_commit_updates_price_and_compare(db):
    p = _make_product(db, "JAKS-PAI-101", price=10.00, compare=None)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-101", "12.50", "19.99")]),
        dry_run=False)
    assert s["matched"] == 1
    assert s["prices_updated"] == 1
    assert s["compare_updated"] == 1
    db.refresh(p)
    assert p.price_override == 12.50
    assert p.compare_at_price == 19.99


def test_only_compare_changes(db):
    """A row that re-states the same price but bumps compare-at only counts
    the compare update — not a price update — and the product is touched
    accordingly."""
    p = _make_product(db, "JAKS-PAI-102", price=10.00, compare=15.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-102", "10.00", "18.00")]),
        dry_run=False)
    assert s["prices_updated"] == 0
    assert s["compare_updated"] == 1
    db.refresh(p)
    assert p.price_override == 10.00
    assert p.compare_at_price == 18.00


def test_unchanged_skipped(db):
    p = _make_product(db, "JAKS-PAI-103", price=12.50, compare=19.99)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-103", "12.50", "19.99")]),
        dry_run=False)
    assert s["unchanged"] == 1
    assert s["prices_updated"] == 0
    db.refresh(p)
    assert p.price_override == 12.50


# ── Scraper format quirks ─────────────────────────────────────────────────────

def test_shopify_image_only_rows_silently_skipped(db):
    """Every product in the scraper export carries 2–6 follow-up rows that
    are blank except Handle + Image Src/Position. They must NOT count
    against skipped_no_sku — that statistic is reserved for genuine
    bad-data rows."""
    p = _make_product(db, "JAKS-PAI-200", price=10.00)
    svc = ProductImportService(db, None)

    rows = [
        _price_row("JAKS-PAI-200", "11.00"),
        _image_row("jaks-pai-200", "https://cdn/200_02.jpg", 2),
        _image_row("jaks-pai-200", "https://cdn/200_03.jpg", 3),
    ]
    s = svc.pricing_update_sell(_csv(rows), dry_run=False)
    assert s["matched"] == 1
    assert s["prices_updated"] == 1
    assert s["image_rows_skipped"] == 2
    assert s["skipped_no_sku"] == 0
    db.refresh(p)
    assert p.price_override == 11.00


def test_unknown_sku_skipped_no_product(db):
    _make_product(db, "JAKS-PAI-300", price=10.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-300", "11.00"),
              _price_row("JAKS-DOES-NOT-EXIST", "99.00")]),
        dry_run=False)
    assert s["skipped_no_product"] == 1
    assert s["prices_updated"] == 1


def test_row_with_no_price_columns_skipped_no_price(db):
    """SKU present but BOTH price columns blank — counter, not silent."""
    _make_product(db, "JAKS-PAI-400", price=10.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_row(Handle="jaks-pai-400",
                   **{"Variant SKU": "JAKS-PAI-400"})]),
        dry_run=False)
    assert s["skipped_no_price"] == 1
    assert s["prices_updated"] == 0


# ── Invariants: never touch cost / never create ───────────────────────────────

def test_cost_columns_in_row_are_ignored(db):
    """Even if a future scraper export carries a stray cost-shaped column,
    sell-mode must NOT touch product.cost or last_cost. Belt + suspenders."""
    p = _make_product(db, "JAKS-PAI-500", price=10.00, cost=4.50)
    svc = ProductImportService(db, None)

    # Append a phantom cost column — DictReader will ignore unknown headers
    # via _get; the test guards that we don't add cost-keyed paths later.
    header_with_cost = _HEADER + ["pai_cost"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_cost)
    w.writerow(_price_row("JAKS-PAI-500", "12.00") + ["999.00"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["prices_updated"] == 1
    db.refresh(p)
    assert p.price_override == 12.00
    assert p.cost == 4.50, "sell mode must never touch cost"


def test_never_creates_products(db):
    """100 unknown SKUs through sell mode = 0 products created, 100 skipped."""
    svc = ProductImportService(db, None)
    rows = [_price_row(f"JAKS-PAI-{i:04d}", "5.00") for i in range(900, 1000)]
    s = svc.pricing_update_sell(_csv(rows), dry_run=False)
    assert s["skipped_no_product"] == 100
    assert s["matched"] == 0
    assert db.query(Product).count() == 0


# ── Safety rail: max_change_pct ───────────────────────────────────────────────

def test_threshold_skips_runaway_change_and_keeps_safe_ones(db):
    """A scraper run that misparses one product to $100.00 from $10.00
    (10×) must NOT silently rewrite the catalog — the over-threshold row
    is skipped and surfaced; sane rows still commit."""
    big = _make_product(db, "JAKS-PAI-600", price=10.00)
    small = _make_product(db, "JAKS-PAI-601", price=10.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-600", "100.00"),  # 900% jump -> skipped
              _price_row("JAKS-PAI-601", "11.50")]), # 15% jump -> kept
        dry_run=False,
        max_change_pct=50.0,
    )
    assert s["over_threshold_skipped"] == 1
    assert s["prices_updated"] == 1
    assert len(s["over_threshold_sample"]) == 1
    assert s["over_threshold_sample"][0]["sku"] == "JAKS-PAI-600"

    db.refresh(big); db.refresh(small)
    assert big.price_override == 10.00, "over-threshold change must not commit"
    assert small.price_override == 11.50


def test_threshold_does_not_block_brand_new_prices(db):
    """price_override is None on a product that has never been priced —
    the threshold rail must not block establishing the first price (no
    baseline to measure against)."""
    p = Product(sku="JAKS-PAI-700", title="x", description="x",
                price_override=None, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-700", "99.00")]),
        dry_run=False, max_change_pct=50.0)
    assert s["over_threshold_skipped"] == 0
    assert s["prices_updated"] == 1
    db.refresh(p)
    assert p.price_override == 99.00


# ── End-to-end against the real scraper export shape ─────────────────────────

def test_real_scraper_export_shape(db):
    """Verbatim shape from C:\\Users\\keith\\PAI Info\\exports\\
    pai_shopify_caterpillar-engine.csv — 1 product row + 1 image-only
    follow-up — must update the sell price and count the image row."""
    p = _make_product(db, "JAKS-PAI-040049", price=2.50)
    csv_text = (
        ",".join(_HEADER) + "\n"
        "jaks-pai-040049,SCREW,<p>SCREW</p>,JAKS,ENGINE PARTS,CATERPILLAR,"
        "TRUE,Title,Default Title,JAKS-PAI-040049,150,,0,deny,manual,"
        "2.90,5.86,TRUE,TRUE,193807163280,"
        "https://cache.paiindustries.com/.../040049_01_XL.jpg,1,SCREW,active\n"
        "jaks-pai-040049,,,,,,,,,,,,,,,,,,,,"
        "https://cache.paiindustries.com/.../040049_02_XL.jpg,2,,\n"
    )

    s = ProductImportService(db, None).pricing_update_sell(
        csv_text, dry_run=False)
    assert s["matched"] == 1
    assert s["prices_updated"] == 1
    assert s["compare_updated"] == 1
    assert s["image_rows_skipped"] == 1
    assert s["skipped_no_sku"] == 0
    db.refresh(p)
    assert p.price_override == 2.90
    assert p.compare_at_price == 5.86
