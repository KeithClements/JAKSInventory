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
from app.models.product import Product, ProductCostHistory, ProductVendorSource
from app.models.vendor import Vendor
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


def _make_product(db, sku, *, price=10.00, compare=None, cost=4.50,
                  manufacturer=""):
    p = Product(sku=sku, title=sku, description=sku,
                price_override=price, compare_at_price=compare,
                cost=cost, manufacturer=manufacturer,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _seed_vendor_with_source(db, product_id, *, code, name, vendor_number,
                             vendor_cost=4.50):
    v = db.query(Vendor).filter(Vendor.vendor_code == code).first()
    if v is None:
        v = Vendor(name=name, vendor_code=code,
                   vendor_number=vendor_number, is_active=True)
        db.add(v); db.commit(); db.refresh(v)
    src = ProductVendorSource(product_id=product_id, vendor_id=v.id,
                              vendor_cost=vendor_cost, is_active=True,
                              is_preferred=True)
    db.add(src); db.commit()
    return v, src


def _seed_pai_with_source(db, product_id, *, vendor_cost=4.50):
    return _seed_vendor_with_source(db, product_id, code="PAI",
                                    name="PAI Industries", vendor_number="9",
                                    vendor_cost=vendor_cost)


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

def test_cost_column_with_pai_source_updates_vendor_cost(db):
    """When a `pai_cost` (or `Our Cost`) column is present AND the product
    has a PAI vendor source, sell-mode updates ProductVendorSource.vendor_cost
    and writes a ProductCostHistory row. product.cost (moving-avg COGS) is
    NOT touched — that's owned by receipts only."""
    p = _make_product(db, "JAKS-PAI-500", price=10.00, cost=4.50)
    _, src = _seed_pai_with_source(db, p.id, vendor_cost=2.50)
    svc = ProductImportService(db, None)

    header_with_cost = _HEADER + ["pai_cost"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_cost)
    w.writerow(_price_row("JAKS-PAI-500", "12.00") + ["3.75"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["prices_updated"] == 1
    assert s["costs_updated"] == 1
    assert s["skipped_no_vendor_source"] == 0
    db.refresh(p); db.refresh(src)
    assert p.price_override == 12.00
    assert p.cost == 4.50, "product.cost (COGS) only changes on receipt"
    assert src.vendor_cost == 3.75
    assert db.query(ProductCostHistory).filter_by(product_id=p.id).count() == 1


def test_our_cost_header_label_recognized(db):
    """Owner-facing form: the scraper-emitted column may be literally 'Our Cost'."""
    p = _make_product(db, "JAKS-PAI-510", price=10.00)
    _, src = _seed_pai_with_source(db, p.id, vendor_cost=0.00)
    svc = ProductImportService(db, None)

    header_with_cost = _HEADER + ["Our Cost"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_cost)
    w.writerow(_price_row("JAKS-PAI-510", "10.00") + ["4.20"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["costs_updated"] == 1
    db.refresh(src)
    assert src.vendor_cost == 4.20


def test_cost_without_pai_source_skipped_and_counted(db):
    """A `pai_cost` value with NO PAI source on the product can't land
    anywhere safely — counted, never silently dropped."""
    p = _make_product(db, "JAKS-PAI-520", price=10.00)
    # Note: no _seed_pai_with_source — product has no PAI source yet.
    svc = ProductImportService(db, None)

    header_with_cost = _HEADER + ["pai_cost"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_cost)
    w.writerow(_price_row("JAKS-PAI-520", "10.00") + ["3.50"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["skipped_no_vendor_source"] == 1
    assert s["skipped_no_pai_source"] == 1   # per-vendor labeled count
    assert s["costs_updated"] == 0


def test_manufacturer_canonicalized_case_insensitive(db):
    """Scraper emits 'CUMMINS' / 'cummins' — both land as 'Cummins'."""
    p1 = _make_product(db, "JAKS-PAI-600", price=10.00, manufacturer="")
    p2 = _make_product(db, "JAKS-PAI-601", price=10.00, manufacturer="")
    svc = ProductImportService(db, None)

    header_with_mfg = _HEADER + ["Manufacturer"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_mfg)
    w.writerow(_price_row("JAKS-PAI-600", "10.00") + ["CUMMINS"])
    w.writerow(_price_row("JAKS-PAI-601", "10.00") + ["caterpillar"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["manufacturer_updated"] == 2
    assert s["manufacturer_unmapped_sample"] == []
    db.refresh(p1); db.refresh(p2)
    assert p1.manufacturer == "Cummins"
    assert p2.manufacturer == "Caterpillar"


def test_manufacturer_unmapped_passes_through_and_is_surfaced(db):
    """An unrecognized make ('John Deere' — not in the dropdown list) is
    preserved verbatim AND surfaced in manufacturer_unmapped_sample so the
    owner can decide whether to add it to the list."""
    p = _make_product(db, "JAKS-PAI-610", price=10.00, manufacturer="")
    svc = ProductImportService(db, None)

    header_with_mfg = _HEADER + ["Manufacturer"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_mfg)
    w.writerow(_price_row("JAKS-PAI-610", "10.00") + ["John Deere"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["manufacturer_updated"] == 1
    assert len(s["manufacturer_unmapped_sample"]) == 1
    assert s["manufacturer_unmapped_sample"][0]["sku"] == "JAKS-PAI-610"
    db.refresh(p)
    assert p.manufacturer == "John Deere"


def test_manufacturer_unchanged_is_idempotent(db):
    """Already-correct manufacturer — second pass shows no update."""
    p = _make_product(db, "JAKS-PAI-620", price=10.00, manufacturer="Cummins")
    svc = ProductImportService(db, None)

    header_with_mfg = _HEADER + ["Manufacturer"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_with_mfg)
    w.writerow(_price_row("JAKS-PAI-620", "10.00") + ["Cummins"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False)
    assert s["manufacturer_updated"] == 0
    assert s["unchanged"] == 1


def test_threshold_skip_does_not_block_independent_cost_or_mfg_writes(db):
    """A runaway sell price gets skipped, but the cost and manufacturer
    columns on the same row still land (they're independent fields and
    a bad scrape of one shouldn't poison the others)."""
    p = _make_product(db, "JAKS-PAI-630", price=10.00, manufacturer="")
    _, src = _seed_pai_with_source(db, p.id, vendor_cost=1.00)
    svc = ProductImportService(db, None)

    header_ext = _HEADER + ["pai_cost", "Manufacturer"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header_ext)
    w.writerow(_price_row("JAKS-PAI-630", "100.00") + ["4.50", "Cummins"])

    s = svc.pricing_update_sell(buf.getvalue(), dry_run=False,
                                max_change_pct=50.0)
    assert s["over_threshold_skipped"] == 1
    assert s["prices_updated"] == 0
    assert s["costs_updated"] == 1
    assert s["manufacturer_updated"] == 1
    db.refresh(p); db.refresh(src)
    assert p.price_override == 10.00, "runaway price never committed"
    assert src.vendor_cost == 4.50
    assert p.manufacturer == "Cummins"


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


# ── Multi-vendor feeds: SKU prefix names the vendor (SCRAPER_REQUIREMENTS.md) ──

def _csv_with_cost(rows_with_cost):
    """rows_with_cost: list of (sku, price, cost). Builds the Shopify header
    + Our Cost column, like the two-vendor scraper export."""
    header = _HEADER + ["Our Cost"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for sku, price, cost in rows_with_cost:
        w.writerow(_price_row(sku, price) + [cost])
    return buf.getvalue()


def test_mixed_vendor_feed_writes_each_vendors_source(db):
    """ACCEPTANCE: one JAKS-PAI row + one JAKS-IMB row, each product seeded
    with its own vendor source — dry-run counts costs_updated=2 with zero
    skipped; commit writes each cost to ITS vendor's source + history row."""
    p1 = _make_product(db, "JAKS-PAI-800", price=10.00)
    p2 = _make_product(db, "JAKS-IMB-801", price=10.00)
    pai_v, s1 = _seed_pai_with_source(db, p1.id, vendor_cost=4.00)
    imb_v, s2 = _seed_vendor_with_source(db, p2.id, code="IMB",
                                         name="Interstate-McBee",
                                         vendor_number="3", vendor_cost=7.00)
    svc = ProductImportService(db, None)
    text = _csv_with_cost([("JAKS-PAI-800", "10.00", "4.50"),
                           ("JAKS-IMB-801", "10.00", "7.77")])

    s = svc.pricing_update_sell(text, dry_run=True)
    assert s["costs_updated"] == 2
    assert s["skipped_no_vendor_source"] == 0

    s = svc.pricing_update_sell(text, dry_run=False)
    assert s["costs_updated"] == 2
    db.refresh(s1); db.refresh(s2)
    assert s1.vendor_cost == 4.50      # PAI row -> PAI source
    assert s2.vendor_cost == 7.77      # IMB row -> IMB source
    h1 = db.query(ProductCostHistory).filter_by(product_id=p1.id).one()
    h2 = db.query(ProductCostHistory).filter_by(product_id=p2.id).one()
    assert h1.vendor_id == pai_v.id and "PAI" in h1.notes
    assert h2.vendor_id == imb_v.id and "IMB" in h2.notes


def test_imb_row_without_imb_source_counted_per_vendor(db):
    """ACCEPTANCE: an IMB-prefixed row whose product has only a PAI source is
    counted per-vendor (skipped_no_imb_source) and NEVER writes the IMB
    dealer price onto the PAI source."""
    p = _make_product(db, "JAKS-IMB-810", price=10.00)
    _, pai_src = _seed_pai_with_source(db, p.id, vendor_cost=4.00)
    # IMB vendor record exists, but this product has no IMB source.
    db.add(Vendor(name="Interstate-McBee", vendor_code="IMB",
                  vendor_number="3", is_active=True))
    db.commit()
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv_with_cost([("JAKS-IMB-810", "10.00", "7.77")]), dry_run=False)
    assert s["costs_updated"] == 0
    assert s["skipped_no_vendor_source"] == 1
    assert s["skipped_no_imb_source"] == 1
    db.refresh(pai_src)
    assert pai_src.vendor_cost == 4.00, "IMB cost must never land on the PAI source"


def test_unknown_prefix_never_reroutes_to_another_vendor(db):
    """A JAKS-XYZ- prefixed row claims vendor XYZ; if no such vendor exists
    the cost is skipped (per-vendor count) rather than written to PAI."""
    p = _make_product(db, "JAKS-XYZ-820", price=10.00)
    _, pai_src = _seed_pai_with_source(db, p.id, vendor_cost=4.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv_with_cost([("JAKS-XYZ-820", "10.00", "9.99")]), dry_run=False)
    assert s["costs_updated"] == 0
    assert s["skipped_no_vendor_source"] == 1
    assert s["skipped_no_xyz_source"] == 1
    db.refresh(pai_src)
    assert pai_src.vendor_cost == 4.00
