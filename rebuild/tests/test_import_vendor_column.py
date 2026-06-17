"""
tests/test_import_vendor_column.py
==================================
The JAKS-native scraper export (SCRAPER_EXPORT_SPEC) carries an explicit
``vendor`` column (PAI / IMB / …) and a BARE part number with no SKU prefix.
The importer must honor that column to attribute each row to the right vendor
(and therefore the right SKU digit) — otherwise a mixed PAI+IMB feed mints every
IMB part in PAI's namespace. Regression guard for the 2026-06-13 fix.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
from app.models.product import ProductVendorSource, Product
from app.models.vendor import Vendor
from app.services.product_import_service import (
    ProductImportService, _row_vendor_code,
)
from tests.conftest import activate, fresh_engine


_JAKS_HEADER = ("pai_part_no,vendor,title,category,engine_make,engine_models,"
                "oem_refs,cost,sell_price,core_charge,is_reman,unit_of_measure,"
                "pack_qty,warranty_years,weight_grams,image_urls,image_files,"
                "superseded_by,in_stock,status,last_updated")


def _jaks_csv(*rows: str) -> str:
    return _JAKS_HEADER + "\n" + "\n".join(rows) + "\n"


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def test_row_vendor_code_prefers_explicit_column():
    assert _row_vendor_code({"vendor_code": "IMB", "sku": "040000"}) == "IMB"
    # explicit wins even over a (contradictory) prefix
    assert _row_vendor_code({"vendor_code": "IMB", "sku": "JAKS-PAI-1"}) == "IMB"
    # no column → fall back to prefix
    assert _row_vendor_code({"sku": "JAKS-PAI-1"}) == "PAI"
    # nothing → PAI
    assert _row_vendor_code({"sku": "040000"}) == "PAI"


def test_parse_jaks_export_captures_vendor_column(db):
    text = _jaks_csv(
        "040000,PAI,HEAD BOLT,Fasteners,CUMMINS,855,,7.23,10.51,,0,EA,6,2.0,144,,,,1,active,2026-06-10T00:00:00+00:00",
        "1832665,IMB,GASKET,Gaskets,CATERPILLAR,C15,,3.10,5.99,,0,EA,1,1.0,50,,,,1,active,2026-06-10T00:00:00+00:00",
    )
    rows = ProductImportService(db, None).parse_jaks_export_csv(text)
    by_part = {r["sku"]: r for r in rows}
    assert by_part["040000"]["vendor_code"] == "PAI"
    assert by_part["1832665"]["vendor_code"] == "IMB"


def test_full_import_attributes_imb_to_its_own_digit(db):
    # Both vendors must exist with their digit (importer is atomic otherwise).
    db.add(Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9", is_active=True))
    db.add(Vendor(name="Interstate-McBee", vendor_code="IMB", vendor_number="3", is_active=True))
    db.commit()

    text = _jaks_csv(
        "040000,PAI,HEAD BOLT,Fasteners,CUMMINS,855,,7.23,10.51,,0,EA,6,2.0,144,,,,1,active,2026-06-10T00:00:00+00:00",
        "1832665,IMB,GASKET,Gaskets,CATERPILLAR,C15,,3.10,5.99,,0,EA,1,1.0,50,,,,1,active,2026-06-10T00:00:00+00:00",
    )
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert summary["created"] == 2, summary
    assert "error" not in summary, summary

    pai_src = db.query(ProductVendorSource).filter_by(vendor_part_number="040000").one()
    imb_src = db.query(ProductVendorSource).filter_by(vendor_part_number="1832665").one()
    pai_prod = db.get(Product, pai_src.product_id)
    imb_prod = db.get(Product, imb_src.product_id)
    # MASTER_PLAN §20: the SKU IS the vendor's real part number (no opaque digit).
    assert pai_prod.sku == "040000"
    assert imb_prod.sku == "1832665", imb_prod.sku
    # Brand reflects the vendor, not a single hard-coded one.
    assert imb_prod.brand == "Interstate-McBee"


def test_full_import_aborts_when_imb_vendor_missing(db):
    # Only PAI exists; an IMB row must abort the whole import atomically.
    db.add(Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9", is_active=True))
    db.commit()
    text = _jaks_csv(
        "1832665,IMB,GASKET,Gaskets,CATERPILLAR,C15,,3.10,5.99,,0,EA,1,1.0,50,,,,1,active,2026-06-10T00:00:00+00:00",
    )
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert summary["created"] == 0
    assert "error" in summary and "IMB" in summary["error"].upper() or "interstate" in summary["error"].lower()
    assert db.query(Product).count() == 0     # nothing written


def test_twin_sku_same_part_two_vendors_collide_second_skipped(db):
    """MASTER_PLAN §20: the customer SKU IS the vendor part number, and Product.sku
    is globally unique — so the SAME part number from two vendors collides. The
    first row wins; the second is skipped as a collision (counted in
    skipped_sku_collision, never silently dropped).

    NOTE for owner: under §20 a second vendor offering the identical part number
    does NOT attach as a second source — it is skipped. If both vendors should be
    sellable on one product, that's a §20 enhancement (attach-source-on-collision),
    tracked separately — not an importer bug."""
    db.add(Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9", is_active=True))
    db.add(Vendor(name="Interstate-McBee", vendor_code="IMB", vendor_number="3", is_active=True))
    db.commit()
    text = _jaks_csv(
        "5555555,PAI,GASKET,Gaskets,CUMMINS,N14,,2.00,4.00,,0,EA,1,1.0,10,,,,1,active,2026-06-10T00:00:00+00:00",
        "5555555,IMB,GASKET,Gaskets,CUMMINS,N14,,2.10,4.20,,0,EA,1,1.0,10,,,,1,active,2026-06-10T00:00:00+00:00",
    )
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert summary["created"] == 1, summary
    assert summary.get("skipped_sku_collision") == 1, summary
    srcs = db.query(ProductVendorSource).filter_by(vendor_part_number="5555555").all()
    assert len(srcs) == 1                 # only the first vendor's source landed
    assert db.get(Product, srcs[0].product_id).sku == "5555555"   # §20: SKU = bare part #


def test_pricing_update_honors_vendor_column_for_cost(db):
    """A JAKS-native refresh row (bare part #, explicit vendor column) must write
    its cost to THAT vendor's source — not silently default to PAI."""
    import csv as _csv, io as _io
    db.add(Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9", is_active=True))
    db.add(Vendor(name="Interstate-McBee", vendor_code="IMB", vendor_number="3", is_active=True))
    db.commit()
    # create an IMB product (bare part 1832665, parked on the IMB source)
    ProductImportService(db, None).full_import(_jaks_csv(
        "1832665,IMB,GASKET,Gaskets,CATERPILLAR,C15,,3.10,5.99,,0,EA,1,1.0,50,,,,1,active,2026-06-10T00:00:00+00:00",
    ), dry_run=False)
    imb = db.query(Vendor).filter_by(vendor_code="IMB").one()
    src_before = (db.query(ProductVendorSource)
                  .filter_by(vendor_part_number="1832665", vendor_id=imb.id).one())
    assert float(src_before.vendor_cost) == 3.10

    # refresh feed: bare sku + explicit vendor=IMB + a new 'our cost'
    buf = _io.StringIO(); w = _csv.writer(buf)
    w.writerow(["sku", "vendor", "our cost", "price"])
    w.writerow(["1832665", "IMB", "9.99", "5.99"])
    summary = ProductImportService(db, None).pricing_update_sell(buf.getvalue(), dry_run=False)
    assert summary["costs_updated"] == 1, summary
    assert summary["skipped_no_vendor_source"] == 0, summary
    db.refresh(src_before)
    assert float(src_before.vendor_cost) == 9.99
