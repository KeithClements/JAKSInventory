"""
tests/test_r4_delta_refresh.py
==============================
R4 â€” Smart Import delta-refresh + review-queue hygiene:

1. SKIP-UNCHANGED STAGING: re-running the full scraper export against a 13k
   catalog stages ONLY the rows where something material changed (price,
   compare-at, PAI cost, manufacturer, new images, new OEM refs). Identical
   UPDATE rows are tallied on the batch (unchanged_count), never staged.
   Changed rows carry a field-level diff_json the preview dock renders as
   Current â†’ Incoming.

2. QUEUE HYGIENE: applying a candidate DELETES it (the catalog is the record;
   the batch header keeps the tallies); swept DUPLICATE rows leave too; a
   whole batch can be deleted (admin-gated) without touching the catalog.
"""
from __future__ import annotations

import csv
import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401
from app.constants import (
    ImportBatchStatus, ImportDisposition, ScrapedItemReviewStatus, UserRole,
)
from app.models.import_review import ImportBatch, ImportCandidate
from app.models.product import (
    Product, ProductCostHistory, ProductVendorSource,
)
from app.models.user import User
from app.models.vendor import Vendor
from app.services.base import PermissionDeniedError
from app.services.import_review_service import ImportReviewService
from tests.conftest import activate, fresh_engine

# Shopify header + the two optional scraper columns (SCRAPER_REQUIREMENTS.md)
_HEADER = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Variant SKU", "Variant Grams",
    "Variant Inventory Tracker", "Variant Inventory Qty", "Variant Inventory Policy",
    "Variant Fulfillment Service", "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Status",
    "Our Cost", "Manufacturer",
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


def _feed_row(sku, *, price="10.99", compare="", cost="", mfg="", oem_html="",
              image="", title="Head Bolt"):
    body = f"<p>{title}</p>"
    if oem_html:
        body += f"<p><strong>OEM References:</strong></p><ul>{oem_html}</ul>"
    return _row(Handle=sku.lower(), Title=title, **{"Body (HTML)": body},
                Type="ENGINE PARTS", Tags="CUMMINS", Status="active",
                **{"Variant SKU": sku, "Variant Price": price,
                   "Variant Compare At Price": compare,
                   "Our Cost": cost, "Manufacturer": mfg,
                   "Image Src": image})


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _seed_pai(db, *, vendor_number="9"):
    v = Vendor(name="PAI Industries", vendor_code="PAI",
               vendor_number=vendor_number, is_active=True)
    db.add(v); db.commit(); db.refresh(v)
    return v


def _make_product(db, sku, *, price=10.99, compare=None, manufacturer="",
                  pai=None, vendor_cost=None):
    p = Product(sku=sku, title="Head Bolt", description="",
                price_override=price, compare_at_price=compare,
                manufacturer=manufacturer, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    if pai is not None:
        db.add(ProductVendorSource(product_id=p.id, vendor_id=pai.id,
                                   vendor_sku=sku, vendor_cost=(vendor_cost or 0.0),
                                   is_active=True, is_preferred=True))
        db.commit()
    return p


def _cands(db, batch_id):
    return (db.query(ImportCandidate)
            .filter(ImportCandidate.batch_id == batch_id)
            .order_by(ImportCandidate.id).all())


# â•â•â• 1 â€” skip-unchanged staging â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def test_identical_update_row_not_staged(db):
    """Same price, nothing new â†’ no candidate; tallied as unchanged."""
    _make_product(db, "JAKS-R4-100", price=10.99)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-100", price="10.99")]))

    assert _cands(db, batch.id) == []
    db.refresh(batch)
    assert batch.unchanged_count == 1
    assert batch.update_count == 0


def test_mixed_feed_stages_only_the_changed_fraction(db):
    """The user's exact scenario: most of the catalog unchanged, a fraction
    changed â€” only the changed rows land in the queue."""
    for i in range(5):
        _make_product(db, f"JAKS-R4-2{i:02d}", price=10.99)
    svc = ImportReviewService(db, None)

    rows = [_feed_row(f"JAKS-R4-2{i:02d}", price="10.99") for i in range(4)]
    rows.append(_feed_row("JAKS-R4-204", price="12.49"))     # the one change

    batch = svc.analyze_feed(_csv(rows))
    cands = _cands(db, batch.id)
    assert len(cands) == 1
    assert cands[0].sku == "JAKS-R4-204"
    db.refresh(batch)
    assert batch.unchanged_count == 4
    assert batch.update_count == 1


def test_price_change_staged_with_diff(db):
    _make_product(db, "JAKS-R4-300", price=10.99)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-300", price="14.99")]))
    (c,) = _cands(db, batch.id)
    diff = json.loads(c.diff_json)
    assert any(d["field"] == "sell price" and d["new"] == 14.99 for d in diff)


def test_cost_change_staged_with_diff(db):
    pai = _seed_pai(db)
    _make_product(db, "JAKS-PAI-310", price=10.99, pai=pai, vendor_cost=4.00)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-PAI-310", cost="4.85")]))
    (c,) = _cands(db, batch.id)
    diff = json.loads(c.diff_json)
    assert any(d["field"] == "our cost (PAI)"
               and d["old"] == 4.00 and d["new"] == 4.85 for d in diff)


def test_unchanged_cost_does_not_stage(db):
    pai = _seed_pai(db)
    _make_product(db, "JAKS-PAI-311", price=10.99, pai=pai, vendor_cost=4.85)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-PAI-311", cost="4.85")]))
    assert _cands(db, batch.id) == []
    db.refresh(batch)
    assert batch.unchanged_count == 1


def test_manufacturer_change_staged_with_diff(db):
    _make_product(db, "JAKS-R4-320", price=10.99, manufacturer="")
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-320", mfg="Cummins")]))
    (c,) = _cands(db, batch.id)
    diff = json.loads(c.diff_json)
    assert any(d["field"] == "manufacturer" and d["new"] == "Cummins" for d in diff)


def test_new_oem_ref_staged_with_diff(db):
    _make_product(db, "JAKS-R4-330", price=10.99)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([
        _feed_row("JAKS-R4-330", oem_html="<li>CUMMINS 3900677</li>")]))
    (c,) = _cands(db, batch.id)
    diff = json.loads(c.diff_json)
    assert any(d["field"] == "OEM refs" for d in diff)


def test_skip_unchanged_off_stages_everything(db):
    """Legacy behavior available behind the toggle."""
    _make_product(db, "JAKS-R4-340", price=10.99)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-340", price="10.99")]),
                             skip_unchanged=False)
    assert len(_cands(db, batch.id)) == 1
    db.refresh(batch)
    assert batch.unchanged_count == 0


def test_new_rows_never_skipped(db):
    """NEW products always stage â€” skip-unchanged only prunes UPDATE rows."""
    svc = ImportReviewService(db, None)
    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-NEW-1")]))
    (c,) = _cands(db, batch.id)
    assert c.disposition == ImportDisposition.NEW


# â•â•â• 2 â€” apply writes the refreshed fields â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def test_apply_update_writes_cost_compare_and_manufacturer(db):
    pai = _seed_pai(db)
    p = _make_product(db, "JAKS-PAI-400", price=10.99, manufacturer="",
                      pai=pai, vendor_cost=4.00)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([
        _feed_row("JAKS-PAI-400", price="12.49", compare="19.99",
                  cost="4.85", mfg="CUMMINS")]))
    (c,) = _cands(db, batch.id)
    svc.set_review_status(c.id, ScrapedItemReviewStatus.ACCEPTED)
    result = svc.apply_approved(batch.id)
    assert result["errors"] == []

    db.expire_all()
    p = db.get(Product, p.id)
    assert p.price_override == 12.49
    assert p.compare_at_price == 19.99
    assert p.manufacturer == "Cummins"          # canonicalized from CUMMINS
    assert (p.cost or 0.0) == 0.0               # COGS untouched â€” receipts own it
    src = db.query(ProductVendorSource).filter_by(product_id=p.id).first()
    assert src.vendor_cost == 4.85
    assert db.query(ProductCostHistory).filter_by(product_id=p.id).count() == 1


# â•â•â• 3 â€” queue hygiene â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def test_applied_candidate_leaves_the_queue(db):
    _make_product(db, "JAKS-R4-500", price=10.99)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-500", price="12.00")]))
    (c,) = _cands(db, batch.id)
    svc.set_review_status(c.id, ScrapedItemReviewStatus.ACCEPTED)
    result = svc.apply_approved(batch.id)
    assert result["applied"] == 1

    db.expire_all()
    assert db.get(ImportCandidate, c.id) is None
    batch = db.get(ImportBatch, batch.id)
    assert batch.applied_count == 1
    assert batch.status == ImportBatchStatus.APPLIED


def test_rejected_candidate_stays_until_batch_delete(db):
    _make_product(db, "JAKS-R4-510", price=10.99)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-510", price="12.00")]))
    (c,) = _cands(db, batch.id)
    svc.set_review_status(c.id, ScrapedItemReviewStatus.REJECTED)
    svc.apply_approved(batch.id)

    db.expire_all()
    assert db.get(ImportCandidate, c.id) is not None   # rejected rows persist


def test_delete_batch_sweeps_candidates_not_catalog(db):
    p = _make_product(db, "JAKS-R4-520", price=10.99)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-R4-520", price="12.00")]))
    assert len(_cands(db, batch.id)) == 1
    bid = batch.id

    assert svc.delete_batch(bid) is True
    db.expire_all()
    assert db.get(ImportBatch, bid) is None
    assert db.query(ImportCandidate).filter_by(batch_id=bid).count() == 0
    assert db.get(Product, p.id) is not None           # catalog untouched


def test_delete_batch_denied_for_non_admin(db):
    _make_product(db, "JAKS-R4-530", price=10.99)
    setup = ImportReviewService(db, None)
    batch = setup.analyze_feed(_csv([_feed_row("JAKS-R4-530", price="12.00")]))

    u = User(name="S", username="sales1", password_hash="x", role=UserRole.SALES)
    db.add(u); db.commit()
    with pytest.raises(PermissionDeniedError):
        ImportReviewService(db, u.id).delete_batch(batch.id)
    db.expire_all()
    assert db.get(ImportBatch, batch.id) is not None



# ═══ 4 — multi-vendor feeds (JAKS-IMB-… rows in the same file) ═════════════════

def _seed_imb(db):
    v = Vendor(name="Interstate-McBee", vendor_code="IMB",
               vendor_number="3", is_active=True)
    db.add(v); db.commit(); db.refresh(v)
    return v


def test_imb_cost_diff_compares_against_imb_source(db):
    """An IMB-prefixed row's "Our Cost" diffs against the IMB source — and
    applying writes it there, never to PAI."""
    pai = _seed_pai(db)
    imb = _seed_imb(db)
    p = _make_product(db, "JAKS-IMB-600", price=10.99, pai=pai, vendor_cost=4.00)
    db.add(ProductVendorSource(product_id=p.id, vendor_id=imb.id,
                               vendor_sku="JAKS-IMB-600", vendor_cost=7.00,
                               is_active=True, is_preferred=False))
    db.commit()
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-IMB-600", cost="7.77")]))
    (c,) = _cands(db, batch.id)
    diff = json.loads(c.diff_json)
    assert any(d["field"] == "our cost (IMB)"
               and d["old"] == 7.00 and d["new"] == 7.77 for d in diff)

    svc.set_review_status(c.id, ScrapedItemReviewStatus.ACCEPTED)
    result = svc.apply_approved(batch.id)
    assert result["errors"] == []
    db.expire_all()
    imb_src = (db.query(ProductVendorSource)
               .filter_by(product_id=p.id, vendor_id=imb.id).one())
    pai_src = (db.query(ProductVendorSource)
               .filter_by(product_id=p.id, vendor_id=pai.id).one())
    assert imb_src.vendor_cost == 7.77      # IMB row -> IMB source
    assert pai_src.vendor_cost == 4.00      # PAI source untouched
    hist = db.query(ProductCostHistory).filter_by(product_id=p.id).one()
    assert hist.vendor_id == imb.id and "IMB" in hist.notes


def test_imb_cost_without_imb_source_stages_no_phantom_change(db):
    """An IMB row whose product only has a PAI source must not stage a cost
    diff (apply couldn't write it) — same IMB cost vs PAI cost would otherwise
    look like a permanent phantom change on every re-run."""
    pai = _seed_pai(db)
    _seed_imb(db)
    _make_product(db, "JAKS-IMB-610", price=10.99, pai=pai, vendor_cost=4.00)
    svc = ImportReviewService(db, None)

    batch = svc.analyze_feed(_csv([_feed_row("JAKS-IMB-610", cost="7.77")]))
    assert _cands(db, batch.id) == []       # nothing actionable -> unchanged
    db.refresh(batch)
    assert batch.unchanged_count == 1
