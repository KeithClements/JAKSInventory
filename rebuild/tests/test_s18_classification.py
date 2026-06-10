"""
tests/test_s18_classification.py
================================
§18.6 — ClassificationService (engine-make normalization + keyword category
match + confidence gate) and its integration into the full importer.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401 — registers all models
from app.models.product import Product, ProductCategory
from app.services.classification_service import ClassificationService, normalize_make
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
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


_HEADER = ["Handle", "Title", "Body (HTML)", "Type", "Tags", "Variant SKU",
           "Variant Price", "Status"]
_BODY = "<p><strong>Applications:</strong></p><ul><li>CUMMINS 855 ENGINES</li></ul>"


def _csv_one(title="Cylinder Head Bolt", type_="ENGINE PARTS", body=_BODY):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    w.writerow(["h1", title, body, type_, "CUMMINS", "JAKS-1", "10.00", "active"])
    return buf.getvalue()


# ── normalize_make ──────────────────────────────────────────────────────────
def test_normalize_make():
    assert normalize_make("CUMMINS") == "Cummins"
    assert normalize_make("Cummins ISX") == "Cummins"
    assert normalize_make("CAT") == "CAT / Caterpillar"
    assert normalize_make("CATERPILLAR 3406") == "CAT / Caterpillar"   # caterpillar beats cat
    assert normalize_make("DETROIT DIESEL") == "Detroit"
    assert normalize_make("Kubota") == ""        # unknown → blank


# ── classify ────────────────────────────────────────────────────────────────
def test_classify_engine_make_single_vs_multi(db):
    svc = ClassificationService(db)
    r1 = svc.classify(app_makes=["CUMMINS", "CUMMINS"], app_models=["ISX", "ISX"])
    assert r1["engine_manufacturer"] == "Cummins"
    assert r1["engine_model"] == "ISX"
    # multi-fit part (two makes) → leave engine make blank
    r2 = svc.classify(app_makes=["CUMMINS", "CAT"])
    assert r2["engine_manufacturer"] == ""


def test_classify_category_keyword_match_and_needs_review(db):
    eng = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(eng)
    db.flush()
    fast = ProductCategory(name="Fasteners", parent_id=eng.id, level=2, is_active=True,
                           import_keywords="bolt, stud, nut")
    db.add(fast)
    db.commit()
    svc = ClassificationService(db)
    hit = svc.classify(title="Cylinder Head Bolt")
    assert hit["category_id"] == fast.id and hit["needs_review"] is False
    miss = svc.classify(title="Mystery Widget")
    assert miss["category_id"] is None and miss["needs_review"] is True


# ── importer integration ────────────────────────────────────────────────────
def test_full_import_applies_classification(db):
    eng = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(eng)
    db.flush()
    fast = ProductCategory(name="Fasteners", parent_id=eng.id, level=2, is_active=True,
                           import_keywords="bolt")
    db.add(fast)
    db.commit()
    fid = fast.id

    ProductImportService(db, None).full_import(_csv_one(), dry_run=False)
    p = db.query(Product).first()   # sku is now the regenerated JAKS scheme value (one product imported)
    assert p is not None
    assert p.engine_manufacturer == "Cummins"     # from the CUMMINS application
    assert p.category_id == fid                    # deeper match via "bolt"
    assert p.needs_review is False
    assert p.brand == "PAI" and p.manufacturer == ""   # still de-conflated


def test_full_import_flags_unclassifiable_as_needs_review(db):
    # No keyword rules exist → cannot confidently place deeper → needs_review.
    ProductImportService(db, None).full_import(_csv_one(), dry_run=False)
    p = db.query(Product).first()   # sku is now the regenerated JAKS scheme value (one product imported)
    assert p.needs_review is True
    assert p.engine_manufacturer == "Cummins"          # engine make still derived
    # Shopify Type still created/assigned the broad top-level category.
    assert p.category is not None and p.category.name == "ENGINE PARTS"
