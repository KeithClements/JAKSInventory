"""
tests/test_r1_import_guards.py
==============================
R1-16 â€” import guardrails:

1. full_import NEVER auto-creates the import vendor. The vendor digit
   (Vendor.vendor_number) is owner-assigned â€” one digit per vendor (SKU scheme
   owner-locked 2026-06-06) â€” so a missing vendor (or a vendor with no digit)
   fails the import cleanly with an actionable summary["error"], writes nothing,
   and creates no vendor row. With the vendor seeded (as the live DB has PAI),
   the import works and mints SKUs on that vendor's digit.

2. apply_approved skips DUPLICATE-disposition candidates (dup-in-feed rows have
   matched_product_id=None and nothing to write) instead of letting them no-op
   in the update branch and wedge the batch in STAGED forever. The skipped count
   is reported and the batch still reaches APPLIED.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401 â€” registers all models (incl. ImportBatch/ImportCandidate)
from app.constants import ImportBatchStatus, ImportDisposition, ScrapedItemReviewStatus
from app.models.import_review import ImportCandidate
from app.models.product import Product
from app.models.vendor import Vendor
from app.services.import_review_service import ImportReviewService
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


def _prod_row(sku, *, handle=None, title="Head Bolt", typ="ENGINE PARTS", price="10.99"):
    return _row(Handle=(handle or sku.lower()), Title=title, Type=typ, Tags="CUMMINS",
                **{"Variant SKU": sku, "Variant Price": price}, Status="active")


def _seed_pai(db, *, vendor_number="9"):
    v = Vendor(name="PAI Industries", vendor_code="PAI",
               vendor_number=vendor_number, is_active=True)
    db.add(v)
    db.commit()
    return v


@pytest.fixture()
def db():
    # Per-test fresh engine via the shared isolation harness (see conftest):
    # activate() repoints app.database.engine/SessionLocal/get_db at THIS engine.
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _cands(db, batch):
    return db.query(ImportCandidate).filter(
        ImportCandidate.batch_id == batch.id).order_by(ImportCandidate.id).all()


# â”€â”€ Guard 1: vendor digit â€” never auto-create â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_full_import_without_vendor_fails_cleanly(db):
    summ = ProductImportService(db, None).full_import(
        _csv([_prod_row("JAKS-G-1")]), dry_run=False)
    assert "error" in summ
    assert "PAI Industries" in summ["error"]
    assert "vendor digit" in summ["error"]          # actionable: create vendor + digit first
    assert summ["created"] == 0
    assert db.query(Product).count() == 0           # nothing written
    assert db.query(Vendor).count() == 0            # NO vendor auto-created (no digit '9')


def test_full_import_dry_run_without_vendor_reports_error(db):
    # The dry-run preview surfaces the same actionable failure (instead of
    # previewing an import that would fail or corrupt the SKU namespace on commit).
    summ = ProductImportService(db, None).full_import(
        _csv([_prod_row("JAKS-G-2")]), dry_run=True)
    assert "error" in summ and "PAI Industries" in summ["error"]
    assert db.query(Vendor).count() == 0
    assert db.query(Product).count() == 0


def test_full_import_vendor_without_digit_fails(db):
    _seed_pai(db, vendor_number="")                 # exists, but no owner-set digit
    summ = ProductImportService(db, None).full_import(
        _csv([_prod_row("JAKS-G-3")]), dry_run=False)
    assert "error" in summ
    assert "no vendor digit" in summ["error"]
    assert db.query(Product).count() == 0
    pai = db.query(Vendor).first()
    assert (pai.vendor_number or "") == ""          # digit never defaulted/overwritten


def test_full_import_with_seeded_vendor_works(db):
    _seed_pai(db, vendor_number="9")
    summ = ProductImportService(db, None).full_import(
        _csv([_prod_row("JAKS-G-4")]), dry_run=False)
    assert "error" not in summ
    assert summ["created"] == 1
    p = db.query(Product).first()
    # Default vendor scheme: SKU = {prefix}-{vendor_code}-{bare part#} (the feed's
    # JAKS- prefix is stripped before re-assembly so it is never doubled).
    assert p is not None and p.sku == "JAKS-PAI-G-4"
    assert db.query(Vendor).count() == 1            # still exactly the seeded vendor


def test_apply_approved_surfaces_missing_vendor_error(db):
    # No vendor seeded: the NEW path's full_import fails â†’ the batch apply must
    # report the actionable error (not silently no-op) and create nothing.
    svc = ImportReviewService(db, None)
    batch = svc.analyze_feed(_csv([_prod_row("APPLY-NOVEND-1")]))
    c = _cands(db, batch)[0]
    svc.set_review_status(c.id, ScrapedItemReviewStatus.ACCEPTED)

    result = svc.apply_approved(batch.id)

    assert result["created"] == 0 and result["applied"] == 0
    assert len(result["errors"]) == 1
    assert "PAI Industries" in result["errors"][0]
    assert db.query(Product).count() == 0
    db.refresh(batch)
    assert batch.status == ImportBatchStatus.STAGED  # retryable once the vendor exists


# â”€â”€ Guard 2: DUPLICATE rows must not wedge a batch in STAGED â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_apply_with_approved_duplicates_reaches_applied(db):
    _seed_pai(db)
    svc = ImportReviewService(db, None)
    r1 = _prod_row("JAKS-PAI-DUPAPL1")
    r2 = _prod_row("JAKS-PAI-DUPAPL1", handle="JAKS-PAI-DUPAPL1-alt")  # same SKU â†’ dup-in-feed
    batch = svc.analyze_feed(_csv([r1, r2]))
    first, dup = _cands(db, batch)
    assert first.disposition == ImportDisposition.NEW
    assert dup.disposition == ImportDisposition.DUPLICATE
    assert dup.matched_product_id is None
    svc.set_review_status(first.id, ScrapedItemReviewStatus.ACCEPTED)
    svc.set_review_status(dup.id, ScrapedItemReviewStatus.ACCEPTED)

    result = svc.apply_approved(batch.id)

    assert result["created"] == 1 and result["applied"] == 1
    assert result["skipped_duplicates"] == 1        # reported, not silently dropped
    assert result["errors"] == []
    # R4 queue hygiene: the applied row AND the swept duplicate both leave the
    # queue; the batch header carries the tallies.
    db.expire_all()
    assert db.get(ImportCandidate, first.id) is None
    assert db.get(ImportCandidate, dup.id) is None
    batch = db.get(type(batch), batch.id)
    assert batch.applied_count == 1
    assert batch.status == ImportBatchStatus.APPLIED  # the wedge: used to stay STAGED forever
    # exactly one product â€” the dup row created nothing
    assert db.query(Product).count() == 1


def test_apply_run_that_only_skips_duplicates_still_finalizes(db):
    """Per-row apply flow: the NEW row is applied first; a later run that finds
    only the approved DUPLICATE left must still finalize the batch (the Apply
    button used to stay lit forever)."""
    _seed_pai(db)
    svc = ImportReviewService(db, None)
    r1 = _prod_row("JAKS-PAI-DUPONLY1")
    r2 = _prod_row("JAKS-PAI-DUPONLY1", handle="JAKS-PAI-DUPONLY1-alt")
    batch = svc.analyze_feed(_csv([r1, r2]))
    first, dup = _cands(db, batch)
    svc.set_review_status(first.id, ScrapedItemReviewStatus.ACCEPTED)
    svc.apply_approved(batch.id, only_ids=[first.id])   # NEW row applied alone

    svc.set_review_status(dup.id, ScrapedItemReviewStatus.ACCEPTED)
    result = svc.apply_approved(batch.id)               # only the dup remains

    assert result["applied"] == 0
    assert result["skipped_duplicates"] == 1
    db.refresh(batch)
    assert batch.status == ImportBatchStatus.APPLIED

