"""
tests/test_r4_multivendor_import.py
===================================
full_import with a TWO-VENDOR scraper feed (SCRAPER_REQUIREMENTS.md): the
feed-SKU prefix names each row's vendor (JAKS-PAI-… / JAKS-IMB-…; no prefix =
legacy PAI). MASTER_PLAN §20: each NEW product keeps the vendor's real part #
as its customer SKU (no opaque digit/sequence minting), and gets THAT vendor's
source + brand.

The R1 guard generalizes per-vendor: EVERY vendor a feed references must exist
with its digit — now a vendor-EXISTENCE guard, not an in-SKU digit — before
anything is written (atomic).
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
from app.constants import ScrapedItemReviewStatus
from app.models.product import Product, ProductVendorSource
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


def _prod_row(sku, *, title="Head Bolt", typ="ENGINE PARTS", price="10.99"):
    return _row(Handle=sku.lower(), Title=title, Type=typ, Tags="CUMMINS",
                **{"Variant SKU": sku, "Variant Price": price}, Status="active")


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _seed_vendor(db, *, name, code, digit, private_label=False):
    v = Vendor(name=name, vendor_code=code, vendor_number=digit, is_active=True,
               private_label=private_label)
    db.add(v); db.commit(); db.refresh(v)
    return v


def _by_feed_sku(db, feed_sku):
    src = (db.query(ProductVendorSource)
           .filter(ProductVendorSource.vendor_sku == feed_sku).first())
    assert src is not None, f"no vendor source parked for {feed_sku}"
    return db.get(Product, src.product_id), src


def test_mixed_feed_mints_each_vendors_digit(db):
    """One PAI row + one IMB row: each product gets ITS vendor's digit in the
    minted SKU, ITS vendor's source, and ITS brand."""
    pai = _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    imb = _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)

    summ = svc.full_import(_csv([
        _prod_row("JAKS-PAI-040049"),
        _prod_row("JAKS-IMB-1832665", title="Injector Sleeve"),
    ]), dry_run=False)
    assert summ.get("error") is None
    assert summ["created"] == 2

    p_pai, src_pai = _by_feed_sku(db, "JAKS-PAI-040049")
    p_imb, src_imb = _by_feed_sku(db, "JAKS-IMB-1832665")

    # MASTER_PLAN §20: SKU = the vendor's real part # (here the feed SKU; no masking).
    assert p_pai.sku == "JAKS-PAI-040049"
    assert p_imb.sku == "JAKS-IMB-1832665"

    assert src_pai.vendor_id == pai.id
    assert src_imb.vendor_id == imb.id
    assert p_pai.brand == "PAI"
    assert p_imb.brand == "Interstate-McBee"


def test_private_label_vendor_import_masks_sku(db):
    """A private-label vendor (e.g. DFT) imported via full_import → the customer
    SKU hides the vendor code (house prefix instead) and the product is flagged
    house brand. The raw feed SKU still parks on the source (searchable)."""
    _seed_vendor(db, name="Diesel Forward Tech", code="DFT", digit="5",
                 private_label=True)
    svc = ProductImportService(db, None)
    summ = svc.full_import(_csv([_prod_row("JAKS-DFT-10R1273")]), dry_run=False)
    assert summ.get("error") is None and summ["created"] == 1, summ
    p, src = _by_feed_sku(db, "JAKS-DFT-10R1273")
    assert p.sku == "JAKS-JAKS-10R1273"     # vendor code DFT hidden
    assert p.is_house_brand is True
    assert src.vendor_sku == "JAKS-DFT-10R1273"   # raw feed sku still searchable


def test_two_private_label_vendors_same_part_collide(db):
    """Masking collapses the vendor code, so two DIFFERENT private-label vendors
    with the same bare part# both want JAKS-JAKS-<part#>. The first lands; the
    second is counted as a collision AND recorded in sku_collisions (not silently
    dropped without a trace)."""
    _seed_vendor(db, name="Diesel Forward Tech", code="DFT", digit="5",
                 private_label=True)
    _seed_vendor(db, name="Migao", code="MIG", digit="6", private_label=True)
    svc = ProductImportService(db, None)
    summ = svc.full_import(
        _csv([_prod_row("JAKS-DFT-7777"), _prod_row("JAKS-MIG-7777")]), dry_run=False)
    assert summ.get("error") is None
    assert summ["created"] == 1, summ
    assert summ["skipped_sku_collision"] == 1, summ
    assert len(summ["sku_collisions"]) == 1
    assert summ["sku_collisions"][0]["prod_sku"] == "JAKS-JAKS-7777"


def test_no_prefix_row_defaults_to_pai(db):
    """Legacy feeds with non-prefixed SKUs keep minting in PAI's namespace."""
    pai = _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    svc = ProductImportService(db, None)

    summ = svc.full_import(_csv([_prod_row("040049X")]), dry_run=False)
    assert summ.get("error") is None and summ["created"] == 1
    p, src = _by_feed_sku(db, "040049X")
    assert p.sku == "JAKS-PAI-040049X"   # default vendor scheme, PAI-routed
    assert src.vendor_id == pai.id


def test_missing_imb_digit_aborts_whole_feed(db):
    """A mixed feed where IMB exists but has NO digit writes NOTHING — the
    error names the offending vendor so the owner knows exactly what to fix."""
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="")
    svc = ProductImportService(db, None)

    summ = svc.full_import(_csv([
        _prod_row("JAKS-PAI-040049"),
        _prod_row("JAKS-IMB-1832665"),
    ]), dry_run=False)
    assert "error" in summ
    assert "Interstate-McBee" in summ["error"]
    assert "vendor digit" in summ["error"]
    assert db.query(Product).count() == 0   # atomic — PAI rows not half-imported


def test_missing_imb_vendor_record_aborts(db):
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    svc = ProductImportService(db, None)

    summ = svc.full_import(_csv([_prod_row("JAKS-IMB-1832665")]), dry_run=False)
    assert "error" in summ
    assert "does not exist" in summ["error"]
    assert db.query(Product).count() == 0


def test_each_vendor_row_creates_its_own_product(db):
    """MASTER_PLAN §20: the opaque per-(engine,category) sequence counter is gone —
    each feed row keeps the vendor's real part # as its SKU and creates its own
    product under its own vendor + brand."""
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    svc = ProductImportService(db, None)

    summ = svc.full_import(_csv([
        _prod_row("JAKS-PAI-100", title="Head Bolt"),
        _prod_row("JAKS-IMB-200", title="Head Bolt"),
    ]), dry_run=False)
    assert summ.get("error") is None and summ["created"] == 2
    p1, s1 = _by_feed_sku(db, "JAKS-PAI-100")
    p2, s2 = _by_feed_sku(db, "JAKS-IMB-200")
    assert p1.sku == "JAKS-PAI-100"
    assert p2.sku == "JAKS-IMB-200"
    assert s1.vendor_id != s2.vendor_id     # each under its own vendor
    assert p1.brand != p2.brand


def test_smart_import_new_imb_row_creates_under_imb(db):
    """The review-queue path (_apply_new → full_import rows=[p]) inherits the
    per-row vendor resolution: an approved NEW IMB candidate creates the
    product under IMB's digit + source."""
    _seed_vendor(db, name="PAI Industries", code="PAI", digit="9")
    imb = _seed_vendor(db, name="Interstate-McBee", code="IMB", digit="3")
    rsvc = ImportReviewService(db, None)

    batch = rsvc.analyze_feed(_csv([_prod_row("JAKS-IMB-555", title="Cam Follower")]))
    (c,) = (db.query(__import__("app.models.import_review", fromlist=["ImportCandidate"])
            .ImportCandidate).filter_by(batch_id=batch.id).all())
    rsvc.set_review_status(c.id, ScrapedItemReviewStatus.ACCEPTED)
    result = rsvc.apply_approved(batch.id)
    assert result["errors"] == []
    assert result["created"] == 1

    p, src = _by_feed_sku(db, "JAKS-IMB-555")
    assert p.sku == "JAKS-IMB-555"     # §20: SKU = the IMB feed/vendor part #
    assert src.vendor_id == imb.id
    assert p.brand == "Interstate-McBee"
