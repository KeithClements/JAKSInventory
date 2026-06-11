"""
tests/test_product_import.py
============================
ProductImportService — two modes, both dry-run-first.

Cardinal rules under test (owner-locked):
  • Variant Price → price_override (OUR sell). NEVER product.cost, NEVER vendor_cost.
  • Full Import is idempotent (existing SKUs skipped).
  • Pricing-Update NEVER creates products and NEVER touches non-pricing data.
  • PAI cost change → ProductVendorSource.vendor_cost + ProductCostHistory.
  • Competitor change → competitor_prices + competitor_price_history.
  • dry_run writes nothing.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401  (registers all models)
from app.models.product import (
    Product, ProductVendorSource, CrossReference, ProductApplication,
    ProductImage, ProductCategory, ProductCostHistory,
)
from app.models.competitor import CompetitorPrice, CompetitorPriceHistory
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


_HEADER = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Variant SKU", "Variant Grams",
    "Variant Inventory Tracker", "Variant Inventory Qty", "Variant Inventory Policy",
    "Variant Fulfillment Service", "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Status",
]
_BODY1 = ("<p>Head Bolt</p><p><strong>PAI Part #:</strong> 111</p>"
          "<p><strong>OEM References:</strong></p><ul><li>CUMMINS 209700</li>"
          "<li>CUMMINS 3068898</li></ul><p><strong>Applications:</strong></p>"
          "<ul><li>CUMMINS 855 ENGINES</li></ul><p><strong>Warranty:</strong> 2.0 year(s)</p>")
_BODY2 = ("<p>Gasket</p><p><strong>PAI Part #:</strong> 222</p>"
          "<p><strong>OEM References:</strong></p><ul><li>DETROIT 23522456</li></ul>"
          "<p><strong>Warranty:</strong> 1.0 year(s)</p>")


def _row(**kw) -> list:
    d = {h: "" for h in _HEADER}
    d.update(kw)
    return [d[h] for h in _HEADER]


def _shopify_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    w.writerow(_row(Handle="jaks-pai-1", Title="Head Bolt", **{"Body (HTML)": _BODY1},
                    Vendor="JAKS", Type="ENGINE PARTS", Tags="CUMMINS",
                    **{"Variant SKU": "JAKS-PAI-111", "Variant Grams": "2087",
                       "Variant Price": "10.99", "Variant Compare At Price": "19.68",
                       "Variant Barcode": "UPC111", "Image Src": "http://img/1a.jpg",
                       "Image Position": "1"}, Status="active"))
    w.writerow(_row(Handle="jaks-pai-1", **{"Image Src": "http://img/1b.jpg", "Image Position": "2"}))
    w.writerow(_row(Handle="jaks-pai-2", Title="Gasket", **{"Body (HTML)": _BODY2},
                    Vendor="JAKS", Type="GASKETS",
                    **{"Variant SKU": "JAKS-PAI-222", "Variant Grams": "500",
                       "Variant Price": "48.99", "Variant Compare At Price": "94.17",
                       "Variant Barcode": "UPC222", "Image Src": "http://img/2a.jpg",
                       "Image Position": "1"}, Status="active"))
    return buf.getvalue()


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _product_by_pai(db, pai_part):
    """Find an imported product by its PAI part number — product.sku is now the
    regenerated JAKS scheme SKU, not the old JAKS-PAI-… value."""
    src = (db.query(ProductVendorSource)
           .filter(ProductVendorSource.vendor_part_number == pai_part).first())
    return src.product if src else None


@pytest.fixture()
def db():
    # Per-test fresh engine via the shared isolation harness: StaticPool +
    # activate() repoints app.database.engine/SessionLocal/get_db at THIS engine,
    # so any global-session reach (routes, background tasks, future edits) hits
    # the same in-memory DB the test reads — never the prior module's engine.
    # See conftest docstring for the cross-module-leak this prevents.
    activate(fresh_engine())
    s = appdb.SessionLocal()
    # R1-16: full_import no longer auto-creates the vendor (vendor digit is
    # owner-assigned) — seed PAI the way the live DB has it.
    from app.models.vendor import Vendor
    s.add(Vendor(name="PAI Industries", vendor_code="PAI",
                 vendor_number="9", is_active=True))
    s.commit()
    try:
        yield s
    finally:
        s.close()


# ── Full import ────────────────────────────────────────────────────────────────

def test_full_import_dry_run_writes_nothing(db):
    summ = ProductImportService(db, None).full_import(_shopify_csv(), dry_run=True)
    assert summ["created"] == 2
    assert summ["cross_refs"] == 3 and summ["applications"] == 1
    assert db.query(Product).count() == 0  # dry run → nothing written


def test_full_import_creates_products_and_relations(db):
    ProductImportService(db, None).full_import(_shopify_csv(), dry_run=False)
    p = _product_by_pai(db, "111")
    assert p is not None
    # SKU minted on the JAKS scheme; the raw CSV number is parked on the vendor source
    assert p.sku.startswith("JAKS-") and p.sku != "JAKS-PAI-111"
    _s0 = db.query(ProductVendorSource).filter(ProductVendorSource.product_id == p.id).first()
    assert _s0.vendor_sku == "JAKS-PAI-111"
    # Pricing mapping — the cardinal rule
    assert p.price_override == 10.99          # Variant Price = OUR sell
    assert p.compare_at_price == 19.68        # Compare At = marketing
    assert p.cost == 0.0                       # NEVER set from price
    assert p.special_order_only is True
    assert p.supplier_warranty_months == 24
    assert p.shopify_product_id == "jaks-pai-1"
    assert p.search_keywords == "CUMMINS"
    # §18.2 — Brand / Vendor / Manufacturer kept separate (was conflated pre-§18).
    # 2026-06-11 owner rule: Manufacturer = the ENGINE MAKE caught from the
    # description (here the single CUMMINS signal) — never the vendor name.
    assert p.brand == "PAI"               # Brand = the parts brand
    assert p.manufacturer == "Cummins"    # detected — NOT "PAI Industries"
    # PAI vendor source created, vendor_cost BLANK
    src = db.query(ProductVendorSource).filter(ProductVendorSource.product_id == p.id).first()
    assert src is not None and src.is_preferred is True
    assert src.vendor_cost == 0.0 and src.vendor_part_number == "111"
    # cross-refs + applications + images parsed from HTML
    assert db.query(CrossReference).filter(CrossReference.product_id == p.id).count() == 2
    assert db.query(ProductApplication).filter(ProductApplication.product_id == p.id).count() == 1
    assert db.query(ProductImage).filter(ProductImage.product_id == p.id).count() == 2
    # category created from Type
    assert db.query(ProductCategory).filter(ProductCategory.name == "ENGINE PARTS").first() is not None


def test_full_import_is_idempotent(db):
    svc = ProductImportService(db, None)
    svc.full_import(_shopify_csv(), dry_run=False)
    summ2 = svc.full_import(_shopify_csv(), dry_run=False)
    assert summ2["created"] == 0 and summ2["skipped_existing"] == 2
    assert db.query(Product).count() == 2  # no duplicates


# ── Pricing update: PAI cost ─────────────────────────────────────────────────────

def test_pricing_update_pai_cost_updates_source_and_history(db):
    svc = ProductImportService(db, None)
    svc.full_import(_shopify_csv(), dry_run=False)  # gives products + PAI sources (cost 0)
    cost_csv = _csv(["sku", "pai_cost"], [["JAKS-PAI-111", "5.50"]])

    summ = svc.pricing_update_pai_cost(cost_csv, dry_run=False)
    assert summ["costs_updated"] == 1
    p = _product_by_pai(db, "111")
    src = db.query(ProductVendorSource).filter(ProductVendorSource.product_id == p.id).first()
    assert src.vendor_cost == 5.50
    assert p.cost == 0.0  # product.cost untouched (moving-avg only)
    hist = db.query(ProductCostHistory).filter(ProductCostHistory.product_id == p.id).all()
    assert len(hist) == 1 and hist[0].old_cost == 0.0 and hist[0].new_cost == 5.50
    # re-run same cost → unchanged, no new history
    summ2 = svc.pricing_update_pai_cost(cost_csv, dry_run=False)
    assert summ2["unchanged"] == 1 and summ2["costs_updated"] == 0
    assert db.query(ProductCostHistory).count() == 1


def test_pricing_update_never_creates_products(db):
    svc = ProductImportService(db, None)
    cost_csv = _csv(["sku", "pai_cost"], [["DOES-NOT-EXIST", "9.99"]])
    summ = svc.pricing_update_pai_cost(cost_csv, dry_run=False)
    assert summ["skipped_no_product"] == 1
    assert db.query(Product).count() == 0


# ── Pricing update: competitor ───────────────────────────────────────────────────

def test_pricing_update_competitor_create_then_history(db):
    svc = ProductImportService(db, None)
    svc.full_import(_shopify_csv(), dry_run=False)
    hdr = ["sku", "competitor_name", "competitor_part_number", "price", "shipping_price"]

    summ = svc.pricing_update_competitor(_csv(hdr, [["JAKS-PAI-111", "NAPA", "N-1", "88.00", "9.00"]]), dry_run=False)
    assert summ["created"] == 1
    cp = db.query(CompetitorPrice).first()
    assert cp.competitor_name == "NAPA" and cp.price == 88.0 and cp.shipping_price == 9.0

    # price moves → history appended, row updated
    summ2 = svc.pricing_update_competitor(_csv(hdr, [["JAKS-PAI-111", "NAPA", "N-1", "95.00", "9.00"]]), dry_run=False)
    assert summ2["updated"] == 1 and summ2["history_appended"] == 1
    db.refresh(cp)
    assert cp.price == 95.0
    assert db.query(CompetitorPriceHistory).count() == 1
    h = db.query(CompetitorPriceHistory).first()
    assert h.old_price == 88.0 and h.new_price == 95.0

    # same price again → unchanged, no new history
    summ3 = svc.pricing_update_competitor(_csv(hdr, [["JAKS-PAI-111", "NAPA", "N-1", "95.00", "9.00"]]), dry_run=False)
    assert summ3["unchanged"] == 1
    assert db.query(CompetitorPriceHistory).count() == 1


def test_pricing_update_competitor_never_touches_product(db):
    svc = ProductImportService(db, None)
    svc.full_import(_shopify_csv(), dry_run=False)
    p = _product_by_pai(db, "111")
    before = (p.title, p.category_id, p.price_override, p.compare_at_price,
              db.query(CrossReference).filter(CrossReference.product_id == p.id).count())
    hdr = ["sku", "competitor_name", "price"]
    svc.pricing_update_competitor(_csv(hdr, [["JAKS-PAI-111", "eBay", "120.00"]]), dry_run=False)
    db.refresh(p)
    after = (p.title, p.category_id, p.price_override, p.compare_at_price,
             db.query(CrossReference).filter(CrossReference.product_id == p.id).count())
    assert before == after  # identity / pricing-override / cross-refs all untouched


# ── §18 — Categorization & classification foundation ─────────────────────────

def test_full_import_does_not_conflate_brand_and_manufacturer(db):
    """§18.2 / A1 — the importer must never write the VENDOR name into
    product.manufacturer. Brand = 'PAI'; manufacturer is the ENGINE MAKE
    caught from the description (2026-06-11 owner rule) or blank for
    multi-make/no-signal parts. Regression guard."""
    ProductImportService(db, None).full_import(_shopify_csv(), dry_run=False)
    products = db.query(Product).all()
    assert products
    for p in products:
        assert p.brand == "PAI"
        assert p.manufacturer != "PAI"             # never the vendor
        assert p.manufacturer != "PAI Industries"  # never the vendor name


def test_category_has_maintenance_columns(db):
    """§18.3 — product_categories gains sort_order / default_markup_pct /
    import_keywords for the Category Maintenance screen."""
    c = ProductCategory(name="Engine", level=1, sort_order=5,
                        default_markup_pct=42.0, import_keywords="head, gasket")
    db.add(c)
    db.commit()
    db.refresh(c)
    assert c.sort_order == 5
    assert c.default_markup_pct == 42.0
    assert c.import_keywords == "head, gasket"


def test_seed_brands_and_manufacturers_idempotent(db):
    """§18.2 — Brand + Manufacturer maintained lists seed once, idempotently."""
    from app.seeds import seed_brands, seed_manufacturers
    from app.models.product import Brand, Manufacturer
    seed_brands(db); seed_brands(db)              # second call must be a no-op
    seed_manufacturers(db); seed_manufacturers(db)
    brand_names = {b.name for b in db.query(Brand).all()}
    assert {"PAI", "Interstate-McBee", "SAMPA", "JAK'S"} <= brand_names
    assert db.query(Brand).count() == 4           # no duplicates
    assert db.query(Brand).filter(Brand.is_house_brand == True).count() == 1   # JAK'S
    man_names = {m.name for m in db.query(Manufacturer).all()}
    assert "Cummins" in man_names and "CAT / Caterpillar" in man_names
    assert "Other" not in man_names               # UI escape hatch, not a real row
