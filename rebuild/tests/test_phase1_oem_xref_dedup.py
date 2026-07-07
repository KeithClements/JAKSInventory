"""
tests/test_phase1_oem_xref_dedup.py
====================================
MASTER_PLAN §23.3 Phase 1 #6 — cross-vendor OEM cross-reference dedup on import.

The (vendor_code, vendor_sku) dedup key in full_import is intentionally
vendor-namespaced ([[test_r4_multivendor_import]] pins this: two DIFFERENT
vendors selling a part with the same/different SKU are two distinct products,
by design for the twin-SKU scheme). But that means importing the SAME physical
part from a SECOND vendor (classic PAI → IMB overlap) used to always mint a
duplicate product. These pin the fix: when a new row's OEM number already
belongs to an existing product from ANOTHER vendor's feed, the importer adds
a vendor source to that product instead of creating a duplicate.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.constants import CrossRefType
from app.models.product import CrossReference, Product, ProductVendorSource
from app.models.vendor import Vendor
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


def _oem_body(*oem_numbers: str) -> str:
    lis = "".join(f"<li>{n}</li>" for n in oem_numbers)
    return f"<p>HEAD GASKET</p><p><strong>OEM References:</strong></p><ul>{lis}</ul>"


def _prod_row(sku, *, title="Head Gasket", typ="ENGINE PARTS", price="10.99", oem=()):
    return _row(Handle=sku.lower(), Title=title, Type=typ, Tags="",
                **{"Variant SKU": sku, "Variant Price": price,
                   "Body (HTML)": _oem_body(*oem) if oem else ""},
                Status="active")


@pytest.fixture()
def db():
    activate(fresh_engine())
    import app.database as appdb
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _seed_vendor(db, *, name, code, digit):
    v = Vendor(name=name, vendor_code=code, vendor_number=digit, is_active=True)
    db.add(v); db.commit(); db.refresh(v)
    return v


def test_second_vendor_same_oem_adds_source_not_duplicate(db):
    """PAI import creates the product with an OEM ref; a LATER IMB import of the
    SAME physical part (different SKU, same OEM #) must NOT mint a 2nd product —
    it should add IMB as a second vendor source on the existing one."""
    pai = _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    imb = _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)

    summ1 = svc.full_import(
        _csv([_prod_row("JAKS-PAI-040049", oem=["CUMMINS 3068898"])]), dry_run=False)
    assert summ1.get("error") is None and summ1["created"] == 1

    summ2 = svc.full_import(
        _csv([_prod_row("JAKS-IMB-1832665", title="Head Gasket (IMB)",
                        oem=["CUMMINS 3068898"])]), dry_run=False)
    assert summ2.get("error") is None
    assert summ2["created"] == 0
    assert summ2["matched_by_xref"] == 1

    assert db.query(Product).count() == 1
    product = db.query(Product).first()
    sources = (db.query(ProductVendorSource)
               .filter(ProductVendorSource.product_id == product.id).all())
    assert {s.vendor_id for s in sources} == {pai.id, imb.id}
    imb_src = next(s for s in sources if s.vendor_id == imb.id)
    assert imb_src.vendor_sku == "JAKS-IMB-1832665"
    assert imb_src.is_preferred is False   # the original PAI source stays preferred


def test_dry_run_reports_match_without_writing(db):
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)
    svc.full_import(
        _csv([_prod_row("JAKS-PAI-040049", oem=["CUMMINS 3068898"])]), dry_run=False)

    summ = svc.full_import(
        _csv([_prod_row("JAKS-IMB-1832665", oem=["CUMMINS 3068898"])]), dry_run=True)
    assert summ["matched_by_xref"] == 1
    assert summ["created"] == 0
    # Dry run — nothing actually written for the 2nd vendor.
    assert db.query(Product).count() == 1
    assert db.query(ProductVendorSource).count() == 1


def test_different_oem_numbers_still_create_separate_products(db):
    """No OEM overlap → normal behavior unchanged: two distinct products."""
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)
    summ = svc.full_import(_csv([
        _prod_row("JAKS-PAI-100", oem=["CUMMINS 1111"]),
        _prod_row("JAKS-IMB-200", oem=["CUMMINS 2222"]),
    ]), dry_run=False)
    assert summ.get("error") is None
    assert summ["created"] == 2
    assert summ["matched_by_xref"] == 0
    assert db.query(Product).count() == 2


def test_no_oem_data_unaffected_regression(db):
    """Rows with NO OEM references at all (most feeds) are completely unaffected
    — the existing per-vendor-namespace dedup behavior is untouched."""
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)
    summ = svc.full_import(_csv([
        _prod_row("JAKS-PAI-100", title="Head Bolt"),
        _prod_row("JAKS-IMB-200", title="Head Bolt"),
    ]), dry_run=False)
    assert summ.get("error") is None and summ["created"] == 2
    assert summ["matched_by_xref"] == 0
    assert db.query(Product).count() == 2


def test_ambiguous_oem_match_across_two_products_falls_through_to_create(db):
    """If a row's OEM numbers span TWO different existing products (a
    pre-existing catalog dupe from before this fix), don't guess — create the
    row normally rather than merge into an arbitrary one."""
    pai = _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    imb = _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    dft = _seed_vendor(db, name="Diesel Forward Tech", code="DFT", digit="5")
    svc = ProductImportService(db, None)

    svc.full_import(_csv([_prod_row("JAKS-PAI-100", oem=["CUMMINS 1111"])]), dry_run=False)
    svc.full_import(_csv([_prod_row("JAKS-IMB-200", oem=["CUMMINS 2222"])]), dry_run=False)
    assert db.query(Product).count() == 2

    # A 3rd-vendor row whose OEM list touches BOTH existing products' numbers.
    summ = svc.full_import(_csv([
        _prod_row("JAKS-DFT-300", oem=["CUMMINS 1111", "CUMMINS 2222"]),
    ]), dry_run=False)
    assert summ.get("error") is None
    assert summ["xref_match_ambiguous"] == 1
    assert summ["created"] == 1     # created normally, not merged
    assert db.query(Product).count() == 3


def test_vendor_renumbered_part_refreshes_existing_source_not_duplicate(db):
    """Edge case: the SAME vendor already supplies the matched product (under a
    different vendor_sku — e.g. renumbered), so adding a 2nd active source
    would violate the (product_id, vendor_id) unique index. Must refresh the
    existing source instead of crashing."""
    pai = _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    svc = ProductImportService(db, None)
    svc.full_import(
        _csv([_prod_row("JAKS-PAI-OLD100", oem=["CUMMINS 9999"], price="50.00")]),
        dry_run=False)
    product = db.query(Product).first()

    # Same vendor (PAI), a DIFFERENT feed SKU, same OEM number — not caught by
    # the (vendor_code, vendor_sku) key, but caught by the OEM xref check.
    summ = svc.full_import(
        _csv([_prod_row("JAKS-PAI-NEW100", oem=["CUMMINS 9999"], price="65.00")]),
        dry_run=False)
    assert summ.get("error") is None
    assert summ["matched_by_xref"] == 1
    assert summ["created"] == 0
    assert db.query(Product).count() == 1   # no duplicate product

    sources = (db.query(ProductVendorSource)
               .filter(ProductVendorSource.product_id == product.id).all())
    assert len(sources) == 1                # refreshed in place, not duplicated
    assert sources[0].vendor_sku == "JAKS-PAI-NEW100"


def test_in_batch_rows_matching_same_product_no_duplicate_crash(db):
    """Regression (turbo lane, 2026-07-06): several rows in ONE import that all
    cross-ref-match the SAME existing product used to crash the flush. Turbo
    interchange lists share OEM numbers heavily, so many rows match one existing
    PAI turbo AND carry the same extra shared numbers; the 2nd matching row
    re-added the shared OEM ref (and a 2nd active vendor source) because the
    'does it already have this?' checks were built from a DB snapshot blind to
    the 1st row's still-pending inserts → UNIQUE constraint failure."""
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    imb = _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)
    # Seed one existing product owning OEM 3068898.
    svc.full_import(
        _csv([_prod_row("JAKS-PAI-040049", oem=["CUMMINS 3068898"])]), dry_run=False)

    # TWO IMB rows in ONE import, BOTH matching that product via 3068898 and BOTH
    # carrying the same NEW shared interchange number 5288-01.
    summ = svc.full_import(_csv([
        _prod_row("JAKS-IMB-A", oem=["CUMMINS 3068898", "HOLSET 5288-01"]),
        _prod_row("JAKS-IMB-B", oem=["CUMMINS 3068898", "HOLSET 5288-01"]),
    ]), dry_run=False)
    assert summ.get("error") is None          # no flush crash
    assert summ["matched_by_xref"] == 2
    assert summ["created"] == 0

    assert db.query(Product).count() == 1
    product = db.query(Product).first()
    refs = {r.ref_number for r in db.query(CrossReference).filter(
        CrossReference.product_id == product.id,
        CrossReference.ref_type == CrossRefType.OEM).all()}
    assert refs == {"3068898", "5288-01"}     # shared new ref added exactly once
    # Both IMB rows collapse to ONE active IMB source (2nd skipped, not crashed,
    # not duplicated) — the (product_id, vendor_id) index allows only one.
    imb_sources = db.query(ProductVendorSource).filter(
        ProductVendorSource.product_id == product.id,
        ProductVendorSource.vendor_id == imb.id).all()
    assert len(imb_sources) == 1


def test_oem_numbers_merged_onto_matched_product(db):
    """A new OEM number on the 2nd-vendor row (not already on the matched
    product) is added as a CrossReference; a repeated one is not duplicated."""
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)
    svc.full_import(
        _csv([_prod_row("JAKS-PAI-040049", oem=["CUMMINS 3068898"])]), dry_run=False)
    svc.full_import(
        _csv([_prod_row("JAKS-IMB-1832665",
                        oem=["CUMMINS 3068898", "MACK 66-66666"])]), dry_run=False)

    product = db.query(Product).first()
    refs = {
        r.ref_number for r in
        db.query(CrossReference).filter(
            CrossReference.product_id == product.id,
            CrossReference.ref_type == CrossRefType.OEM,
        ).all()
    }
    assert refs == {"3068898", "66-66666"}   # merged, no duplicate of the shared one
