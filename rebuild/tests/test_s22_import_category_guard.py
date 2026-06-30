"""
tests/test_s22_import_category_guard.py
=======================================
Lane F — Risk #7 (HIGH): the direct ``full_import`` path must NOT auto-create
arbitrary level-1 categories from the scraper "Type" column.

A scraper feed carries free-text Type values ("Misc", "Test", "Cat 3406 Head
Gasket", …). Previously ``_resolve_category`` minted one top-level
ProductCategory per distinct Type string, polluting the taxonomy. The guard:

  • A Type that does NOT match an existing category by name is REFUSED — no
    category row is created, the product is left uncategorized, and the row is
    flagged ``needs_review`` (mirrors Smart Import's ``category_issue``).
  • The import summary surfaces the count of skipped Types (``categories_skipped``
    + ``skipped_category_names``) and the rows flagged for category review
    (``category_needs_review``).
  • A Type that DOES match an existing top-level category still classifies
    normally (category assigned, no skip).
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
from app.models.product import Product, ProductCategory, ProductVendorSource
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    # full_import no longer auto-creates the vendor — seed PAI like the live DB.
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
# Deliberately generic title/body: no keyword rules exist, so the classifier
# cannot place these on its own — the ONLY category signal is the Type column,
# which is exactly the path under test.
_BODY = "<p>Generic widget, no classifiable keywords here.</p>"


def _csv(rows: list[tuple[str, str, str]]) -> str:
    """rows = list of (handle, type_name, sku)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    for handle, type_name, sku in rows:
        w.writerow([handle, f"Widget {sku}", _BODY, type_name, "", sku,
                    "10.00", "active"])
    return buf.getvalue()


def _top_level_names(db) -> set[str]:
    return {c.name for c in db.query(ProductCategory)
            .filter(ProductCategory.level == 1).all()}


def _product_by_part(db, sku: str) -> Product | None:
    # Look up by the parked raw feed SKU (vendor_sku) — product.sku is the
    # regenerated JAKS scheme value, and vendor_part_number comes from the body
    # "PAI Part #" which these generic feeds intentionally omit.
    src = (db.query(ProductVendorSource)
           .filter(ProductVendorSource.vendor_sku == sku).first())
    return src.product if src else None


# ── the guard: non-allowlisted Types are refused, not auto-created ───────────
def test_arbitrary_types_create_no_categories_and_flag_needs_review(db):
    # No categories seeded → allowlist is EMPTY → every Type below is refused.
    feed = _csv([
        ("h1", "Misc", "1001"),
        ("h2", "Test", "1002"),
        ("h3", "Cat 3406 Head Gasket", "1003"),
    ])
    summ = ProductImportService(db, None).full_import(feed, dry_run=False)

    # 3 products created, but ZERO categories minted from the junk Type strings.
    assert summ["created"] == 3
    assert db.query(ProductCategory).count() == 0
    assert _top_level_names(db) == set()

    # Each refused Type is counted + named in the summary (deduped distinct).
    assert summ["categories_skipped"] == 3
    assert set(summ["skipped_category_names"]) == {
        "Misc", "Test", "Cat 3406 Head Gasket"}
    # categories_created stays 0 (legacy key kept for back-compat).
    assert summ["categories_created"] == 0

    # Every affected row is uncategorized AND flagged for review.
    assert summ["category_needs_review"] == 3
    for part in ("1001", "1002", "1003"):
        p = _product_by_part(db, part)
        assert p is not None
        assert p.category_id is None
        assert p.needs_review is True


def test_repeated_type_counted_once(db):
    # Same junk Type on two rows → one distinct skipped name, both rows flagged.
    feed = _csv([("h1", "Misc", "2001"), ("h2", "Misc", "2002")])
    summ = ProductImportService(db, None).full_import(feed, dry_run=False)
    assert db.query(ProductCategory).count() == 0
    # Skip counter increments per distinct Type (cached after the first refusal).
    assert summ["categories_skipped"] == 1
    assert summ["skipped_category_names"] == ["Misc"]
    # Both products are still flagged needs_review.
    assert summ["category_needs_review"] == 2
    assert _product_by_part(db, "2001").needs_review is True
    assert _product_by_part(db, "2002").needs_review is True


def test_dry_run_creates_nothing_but_reports_skips(db):
    feed = _csv([("h1", "Misc", "3001")])
    summ = ProductImportService(db, None).full_import(feed, dry_run=True)
    assert db.query(ProductCategory).count() == 0
    assert db.query(Product).count() == 0          # dry run writes nothing
    assert summ["categories_skipped"] == 1
    assert summ["skipped_category_names"] == ["Misc"]


# ── the allowlist: a Type matching an existing category classifies normally ──
def test_type_matching_existing_category_classifies_normally(db):
    # Seed a real top-level category the feed Type can map onto.
    eng = ProductCategory(name="Engine Parts", level=1, is_active=True)
    db.add(eng)
    db.commit()
    eng_id = eng.id

    feed = _csv([
        ("h1", "Engine Parts", "4001"),   # matches existing category by name
        ("h2", "Bogus Type", "4002"),     # not allowlisted → refused
    ])
    summ = ProductImportService(db, None).full_import(feed, dry_run=False)

    # No NEW top-level categories minted (still just the one seeded).
    assert _top_level_names(db) == {"Engine Parts"}
    assert db.query(ProductCategory).count() == 1

    # The matching row gets the existing category and is NOT flagged for the
    # category reason; the bogus row is refused + flagged.
    matched = _product_by_part(db, "4001")
    assert matched.category_id == eng_id

    refused = _product_by_part(db, "4002")
    assert refused.category_id is None
    assert refused.needs_review is True

    assert summ["categories_skipped"] == 1
    assert summ["skipped_category_names"] == ["Bogus Type"]
    assert summ["category_needs_review"] == 1


def test_case_insensitive_match_to_existing_category(db):
    # Type allowlist match is case-insensitive (cache keys on name.lower()).
    db.add(ProductCategory(name="Gaskets", level=1, is_active=True))
    db.commit()
    gid = db.query(ProductCategory).filter(ProductCategory.name == "Gaskets").one().id

    feed = _csv([("h1", "GASKETS", "5001")])
    summ = ProductImportService(db, None).full_import(feed, dry_run=False)

    assert db.query(ProductCategory).count() == 1   # no new node
    assert _product_by_part(db, "5001").category_id == gid
    assert summ["categories_skipped"] == 0
