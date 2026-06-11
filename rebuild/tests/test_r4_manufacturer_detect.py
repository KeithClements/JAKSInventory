"""
tests/test_r4_manufacturer_detect.py
====================================
Owner rule (2026-06-11): if the manufacturer's name appears in the imported
description (OEM reference brands, applications, title, tags), the import
catches it and fills the product-form Manufacturer field.

  • derive_manufacturer(): explicit column wins; classifier engine make next;
    then a scan of all signals — exactly ONE distinct make fills, multiple
    distinct makes = multi-fit part, stays blank
  • word-boundary text scan: 'CAT C15' matches, 'LOCATING PIN' does NOT
  • full_import writes the detected value to Product.manufacturer
  • backfill_manufacturers(): same rule retroactively over the existing
    catalog, fill-blank-only, dry-run first
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
from app.models.product import (
    CrossReference, Product, ProductApplication,
)
from app.models.vendor import Vendor
from app.services.classification_service import detect_makes_in_text
from app.services.product_import_service import (
    ProductImportService, derive_manufacturer,
)
from tests.conftest import activate, fresh_engine

_HEADER = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Variant SKU", "Variant Grams",
    "Variant Inventory Tracker", "Variant Inventory Qty", "Variant Inventory Policy",
    "Variant Fulfillment Service", "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Status",
]


def _csv_row(sku, *, title="Head Bolt", tags="", body=""):
    d = {h: "" for h in _HEADER}
    d.update({"Handle": sku.lower(), "Title": title, "Type": "ENGINE PARTS",
              "Tags": tags, "Body (HTML)": body, "Variant SKU": sku,
              "Variant Price": "10.99", "Status": "active"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    w.writerow([d[h] for h in _HEADER])
    return buf.getvalue()


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _seed_pai(db):
    v = Vendor(name="PAI Industries", vendor_code="PAI",
               vendor_number="9", is_active=True)
    db.add(v); db.commit()
    return v


# ── word-boundary text scan ───────────────────────────────────────────────────

def test_text_scan_word_boundaries():
    assert detect_makes_in_text("CAT C15 HEAD BOLT KIT") == {"CAT / Caterpillar"}
    assert detect_makes_in_text("LOCATING PIN") == set()          # 'cat' inside a word
    assert detect_makes_in_text("CUMMINS ISX EGR DELETE") == {"Cummins"}
    assert detect_makes_in_text("") == set()


# ── derive_manufacturer priority + single-make rule ──────────────────────────

def test_explicit_column_wins():
    p = {"manufacturer": "CUMMINS", "title": "CAT thing", "oem": [], "apps": []}
    assert derive_manufacturer(p) == "Cummins"


def test_classifier_engine_make_used():
    p = {"title": "Head Bolt", "oem": [], "apps": []}
    cls = {"engine_manufacturer": "CAT / Caterpillar"}
    assert derive_manufacturer(p, cls) == "Caterpillar"


def test_single_make_from_oem_brands():
    p = {"title": "Head Bolt", "tags": "",
         "oem": ["CUMMINS 3900677", "CUMMINS 3970502"], "apps": []}
    assert derive_manufacturer(p) == "Cummins"


def test_single_make_from_title():
    p = {"title": "CATERPILLAR 3406E PISTON", "tags": "", "oem": [], "apps": []}
    assert derive_manufacturer(p) == "Caterpillar"


def test_multi_make_part_stays_blank():
    """The SCREW from the real export: CAT + CUMMINS + MACK refs — picking one
    would be wrong, so it stays blank on purpose."""
    p = {"title": "SCREW", "tags": "",
         "oem": ["CATERPILLAR 6V2317", "CUMMINS 3900677", "MACK 1AM14"],
         "apps": []}
    assert derive_manufacturer(p) == ""


def test_no_signal_stays_blank():
    p = {"title": "WASHER", "tags": "", "oem": [], "apps": []}
    assert derive_manufacturer(p) == ""


# ── full_import fills the field ───────────────────────────────────────────────

def test_full_import_catches_make_from_description(db):
    _seed_pai(db)
    body = ("<p>HEADBOLT KIT</p><p><strong>OEM References:</strong></p>"
            "<ul><li>CATERPILLAR 204-0712</li><li>CATERPILLAR 6V2317</li></ul>"
            "<p><strong>Applications:</strong></p><ul><li>CATERPILLAR C15</li></ul>")
    summ = ProductImportService(db, None).full_import(
        _csv_row("JAKS-PAI-MFG1", title="HEADBOLT KIT", body=body), dry_run=False)
    assert summ.get("error") is None and summ["created"] == 1
    p = db.query(Product).first()
    assert p.manufacturer == "Caterpillar", \
        "make named in the description must fill the Manufacturer field"


def test_full_import_multi_make_left_blank(db):
    _seed_pai(db)
    body = ("<p>SCREW</p><p><strong>OEM References:</strong></p>"
            "<ul><li>CATERPILLAR 6V2317</li><li>CUMMINS 3900677</li>"
            "<li>MACK 1AM14</li></ul>")
    summ = ProductImportService(db, None).full_import(
        _csv_row("JAKS-PAI-MFG2", title="SCREW", body=body), dry_run=False)
    assert summ.get("error") is None
    p = db.query(Product).first()
    assert (p.manufacturer or "") == ""


# ── retroactive backfill ──────────────────────────────────────────────────────

def _bare_product(db, sku, *, title="Part", manufacturer="", engine_mfr="",
                  keywords=""):
    p = Product(sku=sku, title=title, description="", manufacturer=manufacturer,
                engine_manufacturer=engine_mfr, search_keywords=keywords,
                is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_backfill_fills_from_stored_signals(db):
    p1 = _bare_product(db, "BF-1", title="Part")
    db.add(CrossReference(product_id=p1.id, ref_type="oem",
                          ref_number="3900677", brand="CUMMINS"))
    p2 = _bare_product(db, "BF-2", title="Part")
    db.add(ProductApplication(product_id=p2.id, engine_make="CATERPILLAR",
                              engine_model="C15", source="PAI"))
    p3 = _bare_product(db, "BF-3", title="DETROIT DD15 GASKET")
    p4 = _bare_product(db, "BF-4", title="Part")                  # no signal
    p5 = _bare_product(db, "BF-5", manufacturer="Mack")           # already set
    db.commit()
    svc = ProductImportService(db, None)

    preview = svc.backfill_manufacturers(dry_run=True)
    assert preview["filled"] == 3 and preview["no_signal"] == 1
    db.expire_all()
    assert (db.get(Product, p1.id).manufacturer or "") == ""      # dry-run wrote nothing

    result = svc.backfill_manufacturers(dry_run=False)
    assert result["filled"] == 3
    db.expire_all()
    assert db.get(Product, p1.id).manufacturer == "Cummins"
    assert db.get(Product, p2.id).manufacturer == "Caterpillar"
    assert db.get(Product, p3.id).manufacturer == "Detroit Diesel"
    assert (db.get(Product, p4.id).manufacturer or "") == ""
    assert db.get(Product, p5.id).manufacturer == "Mack"          # untouched


def test_backfill_multi_make_skipped(db):
    p = _bare_product(db, "BF-6", title="Part")
    db.add(CrossReference(product_id=p.id, ref_type="oem",
                          ref_number="1", brand="CUMMINS"))
    db.add(CrossReference(product_id=p.id, ref_type="oem",
                          ref_number="2", brand="MACK"))
    db.commit()
    result = ProductImportService(db, None).backfill_manufacturers(dry_run=False)
    assert result["multi_make_skipped"] == 1
    db.expire_all()
    assert (db.get(Product, p.id).manufacturer or "") == ""
