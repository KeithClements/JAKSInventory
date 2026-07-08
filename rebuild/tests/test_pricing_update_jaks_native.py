"""
tests/test_pricing_update_jaks_native.py
=========================================
Lock in two behaviors added when wiring the JAKS-native scraper export
(C:\\Users\\keith\\PAI Info\\exports\\jaks_import_all.csv) into the
Pricing-Update path so a refresh of cost+manufacturer works without
column renaming:

1. ``pai_part_no`` is a recognised SKU column in pricing_update_sell /
   pricing_update_pai_cost. The bare PAI part number lands on
   ProductVendorSource.vendor_sku at full-import time, and
   _sku_to_id_map already routes that → product id, so this is purely a
   header-recognition fix.

2. Manufacturer values like NAVISTAR / CAT / DETROIT canonicalize via
   the same derive_manufacturer mapping the full importer uses, instead
   of being passed through verbatim. This stops a scraper refresh from
   silently overwriting a correctly-canonical Product.manufacturer with
   the raw upstream string.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401
from app.constants import ProductStatus
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


_JAKS_HEADER = [
    "pai_part_no", "vendor", "title", "category", "engine_make",
    "engine_models", "oem_refs", "cost", "core_charge", "is_reman",
    "unit_of_measure", "pack_qty", "warranty_years", "weight_grams",
    "image_urls", "image_files", "superseded_by", "in_stock", "status",
    "last_updated",
]


def _jaks_row(**kw):
    d = {h: "" for h in _JAKS_HEADER}
    d.update(kw)
    return [d[h] for h in _JAKS_HEADER]


def _jaks_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_JAKS_HEADER)
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


def _seed_product_with_pai_source(db, *, jaks_sku, pai_part_no,
                                  vendor_cost=0.0, manufacturer=""):
    """A product as the full importer would have created it: customer-facing
    JAKS-… SKU on Product.sku, raw PAI part number parked on
    ProductVendorSource.vendor_sku."""
    p = Product(sku=jaks_sku, title=pai_part_no, description="",
                price_override=10.00, cost=0.0, manufacturer=manufacturer,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    v = db.query(Vendor).filter(Vendor.vendor_code == "PAI").first()
    if v is None:
        v = Vendor(name="PAI Industries", vendor_code="PAI",
                   vendor_number="9", is_active=True)
        db.add(v); db.commit(); db.refresh(v)
    src = ProductVendorSource(product_id=p.id, vendor_id=v.id,
                              vendor_sku=pai_part_no,
                              vendor_cost=vendor_cost, is_active=True,
                              is_preferred=True)
    db.add(src); db.commit(); db.refresh(src)
    return p, v, src


# ── (1) pai_part_no is a valid SKU column ────────────────────────────────────

def test_pai_part_no_resolves_via_vendor_sku_map(db):
    """A JAKS-native row keyed by pai_part_no must match a product whose
    Product.sku is the customer-facing JAKS SKU, because vendor_sku links
    them inside _sku_to_id_map."""
    p, _, src = _seed_product_with_pai_source(
        db, jaks_sku="JAKS-CUM-INJ-90001", pai_part_no="040065",
        vendor_cost=207.92)

    csv_text = _jaks_csv([
        _jaks_row(pai_part_no="040065", vendor="PAI",
                  title="Whatever", engine_make="CUMMINS",
                  cost="314.36", status="active"),
    ])

    s = ProductImportService(db, None).pricing_update_sell(
        csv_text, dry_run=False)
    assert s["matched"] == 1
    assert s["skipped_no_sku"] == 0
    assert s["skipped_no_product"] == 0
    assert s["costs_updated"] == 1
    db.refresh(src)
    assert src.vendor_cost == 314.36


def test_pai_part_no_fills_manufacturer_from_engine_make(db):
    """JAKS-native rows carry the make on engine_make, not manufacturer.
    pricing_update_sell already lists engine_make as a manufacturer key —
    the test makes sure the canonical form lands on Product.manufacturer."""
    p, *_ = _seed_product_with_pai_source(
        db, jaks_sku="JAKS-CUM-GEN-90111", pai_part_no="040013",
        vendor_cost=0.58, manufacturer="")

    csv_text = _jaks_csv([
        _jaks_row(pai_part_no="040013", vendor="PAI",
                  title="Part", engine_make="CUMMINS", cost="0.58"),
    ])

    s = ProductImportService(db, None).pricing_update_sell(
        csv_text, dry_run=False)
    assert s["manufacturer_updated"] == 1
    db.refresh(p)
    assert p.manufacturer == "Cummins"


# ── (2) Manufacturer values canonicalize via derive_manufacturer ─────────────

@pytest.mark.parametrize("raw,canonical", [
    ("NAVISTAR", "International"),
    ("navistar", "International"),
    ("CAT", "Caterpillar"),
    ("CATERPILLAR", "Caterpillar"),
    ("DETROIT", "Detroit Diesel"),
    ("PACCAR", "Paccar"),
    ("CUMMINS", "Cummins"),
])
def test_known_aliases_land_on_canonical_manufacturer(db, raw, canonical):
    p, *_ = _seed_product_with_pai_source(
        db, jaks_sku=f"JAKS-X-{abs(hash(raw)) % 10000:04d}",
        pai_part_no=f"PN{abs(hash(raw)) % 10000:04d}", manufacturer="")

    csv_text = _jaks_csv([
        _jaks_row(pai_part_no=f"PN{abs(hash(raw)) % 10000:04d}",
                  vendor="PAI", title="t", engine_make=raw),
    ])

    s = ProductImportService(db, None).pricing_update_sell(
        csv_text, dry_run=False)
    assert s["manufacturer_updated"] == 1
    db.refresh(p)
    assert p.manufacturer == canonical


def test_canonical_value_does_not_re_overwrite_with_raw_alias(db):
    """Critical regression: a product already canonicalized to "International"
    must NOT be re-written to "NAVISTAR" by a scraper refresh — that was
    the old behavior (verbatim pass-through) the canonical mapping fixes."""
    p, *_ = _seed_product_with_pai_source(
        db, jaks_sku="JAKS-CUM-NAV-90099", pai_part_no="121324",
        manufacturer="International")

    csv_text = _jaks_csv([
        _jaks_row(pai_part_no="121324", vendor="PAI",
                  title="t", engine_make="NAVISTAR"),
    ])

    s = ProductImportService(db, None).pricing_update_sell(
        csv_text, dry_run=False)
    assert s["manufacturer_updated"] == 0
    assert s["unchanged"] == 1
    db.refresh(p)
    assert p.manufacturer == "International"


def test_truly_unknown_make_falls_through_verbatim_and_is_surfaced(db):
    """An engine_make value the canonical mapper can't recognize — e.g.
    "FORD ENGINES", "UNIVERSAL USE" — should still land on the field
    (we never want to drop owner-facing data on the floor) but should
    be reported in manufacturer_unmapped_sample so the owner can review."""
    p, *_ = _seed_product_with_pai_source(
        db, jaks_sku="JAKS-FRD-90001", pai_part_no="431244", manufacturer="")

    csv_text = _jaks_csv([
        _jaks_row(pai_part_no="431244", vendor="PAI",
                  title="t", engine_make="FORD ENGINES"),
    ])

    s = ProductImportService(db, None).pricing_update_sell(
        csv_text, dry_run=False)
    assert s["manufacturer_updated"] == 1
    assert any(row.get("manufacturer") == "FORD ENGINES"
               for row in s["manufacturer_unmapped_sample"])
    db.refresh(p)
    assert p.manufacturer == "FORD ENGINES"
