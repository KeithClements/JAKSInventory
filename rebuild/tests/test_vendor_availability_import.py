"""
tests/test_vendor_availability_import.py
========================================
Vendor availability sync from the scraper feed (owner-locked "full automation"):

  • feed `availability` column → ProductVendorSource.availability_status +
    denormalized Product.vendor_availability (worst-case roll-up).
  • out_of_stock  → product hidden from the storefront push (vendor_availability
    flag the Shopify bulk sync filters on); product stays active.
  • discontinued  → product deactivated (is_active=False, status=discontinued).
  • back in_stock after deactivation → NOT auto-reactivated; needs_review flagged.
  • blank / unknown cell = don't touch.

Covers full_import (create + refresh existing), pricing_update_sell, and the
Shopify bulk-push exclusion.
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
from app.constants import ProductStatus, VendorAvailability
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.services.product_import_service import (
    ProductImportService, normalize_availability, _effective_availability,
)
from tests.conftest import activate, fresh_engine


# ── harness ───────────────────────────────────────────────────────────────────
@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _pai_vendor(db):
    v = db.query(Vendor).filter(Vendor.vendor_code == "PAI").first()
    if v is None:
        v = Vendor(name="PAI Industries", vendor_code="PAI",
                   vendor_number="9", is_active=True)
        db.add(v); db.commit(); db.refresh(v)
    return v


def _make_product(db, sku, *, price=10.00, status=ProductStatus.ACTIVE,
                  is_active=True, vendor_availability=""):
    p = Product(sku=sku, title=sku, description=sku, price_override=price,
                cost=4.50, status=status, is_active=is_active,
                vendor_availability=vendor_availability)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _seed_source(db, product_id, vendor, *, sku, availability_status=None):
    src = ProductVendorSource(
        product_id=product_id, vendor_id=vendor.id, vendor_sku=sku,
        vendor_part_number=sku, vendor_cost=4.50, is_active=True,
        is_preferred=True, availability_status=availability_status)
    db.add(src); db.commit(); db.refresh(src)
    return src


# ── JAKS-native CSV (one row per product — used for full_import) ───────────────
_JAKS_HEADER = [
    "pai_part_no", "vendor", "title", "category", "engine_make", "engine_models",
    "sell_price", "cost", "status", "availability", "warranty_years",
    "weight_grams", "core_charge", "is_reman", "unit_of_measure", "pack_qty",
    "oem_refs", "image_urls",
]


def _jaks_row(part, **kw):
    d = {h: "" for h in _JAKS_HEADER}
    d.update({"pai_part_no": part, "vendor": "PAI", "title": f"Part {part}",
              "category": "ENGINE PARTS", "sell_price": "12.50",
              "status": "active"})
    d.update(kw)
    return [d[h] for h in _JAKS_HEADER]


def _jaks_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_JAKS_HEADER)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


# ── Shopify pricing-update CSV (existing products) ────────────────────────────
_SHOP_HEADER = ["Handle", "Title", "Type", "Variant SKU", "Variant Price",
                "Variant Compare At Price", "availability", "Status"]


def _shop_row(sku, *, price="", compare="", availability="", handle=None):
    d = {h: "" for h in _SHOP_HEADER}
    d.update({"Handle": handle or sku.lower(), "Title": "Part",
              "Type": "ENGINE PARTS", "Status": "active", "Variant SKU": sku,
              "Variant Price": str(price), "Variant Compare At Price": str(compare),
              "availability": availability})
    return [d[h] for h in _SHOP_HEADER]


def _shop_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_SHOP_HEADER)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


# ══ Unit: normalization ═══════════════════════════════════════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("in stock", VendorAvailability.IN_STOCK),
    ("In_Stock", VendorAvailability.IN_STOCK),
    ("available", VendorAvailability.IN_STOCK),
    ("Out of Stock", VendorAvailability.OUT_OF_STOCK),
    ("out-of-stock", VendorAvailability.OUT_OF_STOCK),
    ("OOS", VendorAvailability.OUT_OF_STOCK),
    ("backordered", VendorAvailability.OUT_OF_STOCK),
    ("Discontinued", VendorAvailability.DISCONTINUED),
    ("NLA", VendorAvailability.DISCONTINUED),
    ("obsolete", VendorAvailability.DISCONTINUED),
    ("", ""),
    ("   ", ""),
    ("who knows", ""),     # unrecognized → unknown (don't touch)
])
def test_normalize_availability(raw, expected):
    assert normalize_availability(raw) == expected


def test_effective_availability_rollup():
    # any in-stock source wins (sellable)
    assert _effective_availability(["in_stock", "out_of_stock"]) == VendorAvailability.IN_STOCK
    # no in-stock, an out → out (hide, may return)
    assert _effective_availability(["out_of_stock", "discontinued"]) == VendorAvailability.OUT_OF_STOCK
    # every known source discontinued → discontinued (deactivate)
    assert _effective_availability(["discontinued"]) == VendorAvailability.DISCONTINUED
    # unknowns ignored
    assert _effective_availability(["", ""]) == ""


# ══ full_import — create new products ══════════════════════════════════════════
def test_full_import_create_out_of_stock(db):
    _pai_vendor(db)
    svc = ProductImportService(db, None)
    s = svc.full_import(_jaks_csv([_jaks_row("340097", availability="out_of_stock")]),
                        dry_run=False)
    assert s["created"] == 1
    assert s["availability_updated"] == 1
    assert s["out_of_stock_flagged"] == 1
    assert s["discontinued_deactivated"] == 0

    prod = db.query(Product).first()
    assert prod.is_active is True, "out-of-stock stays active, just hidden from push"
    assert prod.vendor_availability == VendorAvailability.OUT_OF_STOCK
    src = db.query(ProductVendorSource).first()
    assert src.availability_status == VendorAvailability.OUT_OF_STOCK
    assert src.availability_updated_at is not None


def test_full_import_create_discontinued_is_deactivated(db):
    _pai_vendor(db)
    svc = ProductImportService(db, None)
    s = svc.full_import(_jaks_csv([_jaks_row("340098", availability="discontinued")]),
                        dry_run=False)
    assert s["created"] == 1
    assert s["discontinued_deactivated"] == 1

    prod = db.query(Product).first()
    assert prod.is_active is False
    assert prod.status == ProductStatus.DISCONTINUED
    assert prod.vendor_availability == VendorAvailability.DISCONTINUED


def test_full_import_blank_availability_untouched(db):
    _pai_vendor(db)
    svc = ProductImportService(db, None)
    s = svc.full_import(_jaks_csv([_jaks_row("340099", availability="")]),
                        dry_run=False)
    assert s["created"] == 1
    assert s["availability_updated"] == 0
    prod = db.query(Product).first()
    assert prod.is_active is True
    assert prod.vendor_availability == ""


def test_full_import_dry_run_counts_but_writes_nothing(db):
    _pai_vendor(db)
    svc = ProductImportService(db, None)
    s = svc.full_import(_jaks_csv([_jaks_row("340100", availability="discontinued")]),
                        dry_run=True)
    assert s["created"] == 1
    assert s["discontinued_deactivated"] == 1
    assert db.query(Product).count() == 0, "dry-run writes nothing"


# ══ full_import — refresh availability on an EXISTING product ══════════════════
def test_full_import_existing_refreshes_availability(db):
    v = _pai_vendor(db)
    p = _make_product(db, "JAKS-PAI-340097")
    _seed_source(db, p.id, v, sku="340097")
    svc = ProductImportService(db, None)

    s = svc.full_import(_jaks_csv([_jaks_row("340097", availability="out_of_stock")]),
                        dry_run=False)
    assert s["created"] == 0
    assert s["availability_updated"] == 1
    assert s["out_of_stock_flagged"] == 1
    db.refresh(p)
    assert p.vendor_availability == VendorAvailability.OUT_OF_STOCK
    src = db.query(ProductVendorSource).filter_by(product_id=p.id).first()
    assert src.availability_status == VendorAvailability.OUT_OF_STOCK


# ══ pricing_update_sell ═══════════════════════════════════════════════════════
def test_pricing_update_discontinued_deactivates(db):
    v = _pai_vendor(db)
    p = _make_product(db, "JAKS-PAI-500")
    _seed_source(db, p.id, v, sku="JAKS-PAI-500")
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _shop_csv([_shop_row("JAKS-PAI-500", availability="discontinued")]),
        dry_run=False)
    assert s["availability_updated"] == 1
    assert s["discontinued_deactivated"] == 1
    db.refresh(p)
    assert p.is_active is False
    assert p.status == ProductStatus.DISCONTINUED


def test_pricing_update_availability_only_row_not_skipped(db):
    """A row carrying ONLY availability (no price/cost) is still matched and
    applied — not counted as skipped_no_price or unchanged."""
    v = _pai_vendor(db)
    p = _make_product(db, "JAKS-PAI-501")
    _seed_source(db, p.id, v, sku="JAKS-PAI-501")
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _shop_csv([_shop_row("JAKS-PAI-501", availability="out_of_stock")]),
        dry_run=False)
    assert s["skipped_no_price"] == 0
    assert s["matched"] == 1
    assert s["unchanged"] == 0
    assert s["out_of_stock_flagged"] == 1
    db.refresh(p)
    assert p.vendor_availability == VendorAvailability.OUT_OF_STOCK


def test_pricing_update_back_in_stock_does_not_auto_reactivate(db):
    """Owner-locked safety: a part that comes back in stock while deactivated is
    flagged for review, never silently re-activated."""
    v = _pai_vendor(db)
    p = _make_product(db, "JAKS-PAI-502", status=ProductStatus.DISCONTINUED,
                      is_active=False, vendor_availability="discontinued")
    _seed_source(db, p.id, v, sku="JAKS-PAI-502",
                 availability_status="discontinued")
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _shop_csv([_shop_row("JAKS-PAI-502", availability="in_stock")]),
        dry_run=False)
    assert s["reactivation_suggested"] == 1
    db.refresh(p)
    assert p.is_active is False, "must NOT auto-reactivate"
    assert p.needs_review is True
    assert p.vendor_availability == VendorAvailability.IN_STOCK


def test_pricing_update_dry_run_writes_nothing(db):
    v = _pai_vendor(db)
    p = _make_product(db, "JAKS-PAI-503")
    _seed_source(db, p.id, v, sku="JAKS-PAI-503")
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _shop_csv([_shop_row("JAKS-PAI-503", availability="discontinued")]),
        dry_run=True)
    assert s["discontinued_deactivated"] == 1
    db.refresh(p)
    assert p.is_active is True, "dry-run must not deactivate"
    assert p.vendor_availability == ""


# ══ Shopify bulk-push exclusion ═══════════════════════════════════════════════
def test_storefront_push_excludes_out_of_stock(db, monkeypatch):
    """sync_inventory_all_linked must not include out_of_stock / discontinued
    parts in the storefront stock feed."""
    from app.services.shopify_service import ShopifyService

    good = _make_product(db, "JAKS-PAI-700", vendor_availability="in_stock")
    oos = _make_product(db, "JAKS-PAI-701", vendor_availability="out_of_stock")
    disc = _make_product(db, "JAKS-PAI-702", vendor_availability="discontinued",
                         status=ProductStatus.DISCONTINUED, is_active=False)
    for p in (good, oos, disc):
        p.shopify_inventory_item_id = f"gid://shopify/InventoryItem/{p.id}"
    db.commit()

    svc = ShopifyService(db, None)
    captured = {}
    monkeypatch.setattr(svc, "sync_inventory",
                        lambda ids: captured.setdefault("ids", list(ids)) or {})
    svc.sync_inventory_all_linked()

    assert good.id in captured["ids"]
    assert oos.id not in captured["ids"]
    assert disc.id not in captured["ids"]
