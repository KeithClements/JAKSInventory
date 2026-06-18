"""
tests/test_r3_import_mapping.py
===============================
R3 — multi-vendor CSV import via saved column mappings.

The import parsers used hard-coded header aliases, so a SAMPA/Interstate-McBee
file with 'Part Number' instead of 'Variant SKU' silently staged SKU-less
garbage through the Shopify fallback parser. Now:

  * Recognized headers (PAI Shopify export / JAKS native) stage EXACTLY as
    before — zero behavior change (regression-guarded here).
  * Unrecognized headers park the upload (ImportPendingUpload = the
    needs-mapping state) and route to a column-mapping screen; NOTHING stages.
  * Confirming a mapping parses through ProductImportService.parse_mapped_csv
    and feeds the SAME staging pipeline (all R1 guards intact).
  * Mappings can be saved as per-vendor templates (upsert by name) that
    pre-fill the screen on the next upload from that source.
  * A mapping without a SKU / Part Number column is rejected.

Fixture pattern mirrors tests/test_r3_statements.py: module-isolated
in-memory engine + TestClient. Never touches jaks.db.
"""
from __future__ import annotations

import itertools
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app
from app.constants import ImportDisposition, ScrapedItemReviewStatus
from app.models.import_review import (
    ImportBatch, ImportCandidate, ImportMappingTemplate, ImportPendingUpload,
)
from app.models.product import Product
from app.models.vendor import Vendor
from app.services.import_review_service import ImportReviewService
from app.services.product_import_service import ProductImportService

_ENGINE = fresh_engine()
_counter = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# CSV builders
# ---------------------------------------------------------------------------

_SHOPIFY_HEADER = (
    "Handle,Title,Body (HTML),Vendor,Type,Tags,Published,Variant SKU,"
    "Variant Grams,Variant Price,Variant Compare At Price,Variant Barcode,"
    "Image Src,Image Position,Image Alt Text,Status"
)


def _shopify_csv(sku: str, *, title="Head Bolt", price="10.99") -> str:
    return (
        f"{_SHOPIFY_HEADER}\n"
        f"{sku.lower()},{title},,PAI,ENGINE PARTS,CUMMINS,TRUE,{sku},"
        f"100,{price},,,,,,active\n"
    )


# SAMPA/IMB-style vendor price file — none of the recognized signatures.
_VENDOR_HEADER = "Part Number,Description,List Price,Dealer Cost,Core,Engine Model"


def _vendor_csv(*rows: tuple) -> str:
    out = [_VENDOR_HEADER]
    for sku, desc, price, cost, core, model in rows:
        out.append(f"{sku},{desc},{price},{cost},{core},{model}")
    return "\n".join(out) + "\n"


_VENDOR_MAPPING = {           # header -> canonical field, as the form posts it
    "Part Number": "sku",
    "Description": "title",
    "List Price": "price",
    "Dealer Cost": "cost",
    "Core": "core_charge",
    "Engine Model": "engine_models",
}


def _upload(client, text: str, *, source_app: str = "", filename: str = "feed.csv"):
    return client.post(
        "/import-review/upload",
        files={"file": (filename, text.encode(), "text/csv")},
        data={"source_app": source_app},
        follow_redirects=False,
    )


def _mapping_form(mapping: dict[str, str], **extra) -> dict:
    """Build the header_{i}/field_{i} pairs the mapping screen posts."""
    data: dict = {}
    for i, h in enumerate(_VENDOR_HEADER.split(",")):
        data[f"header_{i}"] = h
        data[f"field_{i}"] = mapping.get(h, "")
    data.update(extra)
    return data


def _seed_pai(db, *, vendor_number="9"):
    v = (db.query(Vendor)
         .filter(Vendor.name == "PAI Industries").first())
    if v is None:
        v = Vendor(name="PAI Industries", vendor_code="PAI",
                   vendor_number=vendor_number, is_active=True)
        db.add(v)
    else:
        v.vendor_number = vendor_number
    db.commit()
    return v


# ---------------------------------------------------------------------------
# 0. Format detection (unit level)
# ---------------------------------------------------------------------------

def test_detect_known_format_signatures(client):
    assert ProductImportService.detect_known_format(_shopify_csv("DK-1")) == "shopify"
    jaks = "pai_part_no,title,category,sell_price\nJ-1,Bolt,ENGINE PARTS,9.99\n"
    assert ProductImportService.detect_known_format(jaks) == "jaks"
    assert ProductImportService.detect_known_format(
        _vendor_csv(("V-1", "Bolt", "10", "5", "0", "ISX"))) is None


# ---------------------------------------------------------------------------
# 1. Regression — recognized Shopify headers bypass mapping entirely
# ---------------------------------------------------------------------------

def test_shopify_upload_bypasses_mapping_and_stages(client, db):
    pend_before = db.query(ImportPendingUpload).count()
    r = _upload(client, _shopify_csv("R3MAP-SHOP-1"), source_app="pai-info")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/import-review/")
    assert "/mapping/" not in r.headers["location"]
    batch_id = int(r.headers["location"].rstrip("/").split("/")[-1])

    db.expire_all()
    assert db.query(ImportPendingUpload).count() == pend_before  # nothing parked
    cands = (db.query(ImportCandidate)
             .filter(ImportCandidate.batch_id == batch_id).all())
    assert len(cands) == 1
    assert cands[0].sku == "R3MAP-SHOP-1"          # staged exactly as before
    assert cands[0].disposition == ImportDisposition.NEW


# ---------------------------------------------------------------------------
# 2. Unknown headers — parked in needs-mapping, nothing staged
# ---------------------------------------------------------------------------

def test_unknown_headers_park_upload_and_stage_nothing(client, db):
    batches_before = db.query(ImportBatch).count()
    cands_before = db.query(ImportCandidate).count()

    r = _upload(client, _vendor_csv(("SAM-100", "Cam Follower", "55.00", "31.00", "0", "ISX")),
                source_app="sampa", filename="sampa_price.csv")
    assert r.status_code == 303
    assert "/import-review/mapping/" in r.headers["location"]
    upload_id = int(r.headers["location"].rstrip("/").split("/")[-1])

    db.expire_all()
    pend = db.get(ImportPendingUpload, upload_id)
    assert pend is not None and pend.source_app == "sampa"
    assert "SAM-100" in pend.text                    # raw text parked verbatim
    # NEEDS_MAPPING state stages NOTHING — no batch, no candidates.
    assert db.query(ImportBatch).count() == batches_before
    assert db.query(ImportCandidate).count() == cands_before

    # The mapping screen renders the detected headers with fuzzy guesses
    # ('Part Number' → sku) pre-selected.
    page = client.get(f"/import-review/mapping/{upload_id}")
    assert page.status_code == 200
    assert "Part Number" in page.text
    assert 'data-prefill="sku"' in page.text

    # The index page surfaces the parked upload with a Map-columns link.
    idx = client.get("/import-review/")
    assert f"/import-review/mapping/{upload_id}" in idx.text
    assert "Map columns" in idx.text

    # cleanup: discard so later tests start clean
    client.post(f"/import-review/mapping/{upload_id}/discard")
    db.expire_all()
    assert db.get(ImportPendingUpload, upload_id) is None


# ---------------------------------------------------------------------------
# 3. Mapping POST — rows flow through the EXISTING staging pipeline
# ---------------------------------------------------------------------------

def test_mapping_post_stages_through_existing_pipeline(client, db):
    text = _vendor_csv(
        ("IMB-200", "Injector Cup", "120.00", "70.00", "25.00", "ISX"),
        ("IMB-201", "Cam Bushing", "18.50", "9.25", "0", "N14"),
        ("IMB-200", "Injector Cup dup", "120.00", "70.00", "25.00", "ISX"),  # dup-in-feed
    )
    r = _upload(client, text, source_app="imb", filename="imb_feed.csv")
    upload_id = int(r.headers["location"].rstrip("/").split("/")[-1])

    r2 = client.post(f"/import-review/mapping/{upload_id}",
                     data=_mapping_form(_VENDOR_MAPPING, source_label="imb"),
                     follow_redirects=False)
    assert r2.status_code == 303
    batch_id = int(r2.headers["location"].rstrip("/").split("/")[-1])

    db.expire_all()
    assert db.get(ImportPendingUpload, upload_id) is None    # parked file consumed
    batch = db.get(ImportBatch, batch_id)
    assert batch is not None and batch.total == 3
    cands = (db.query(ImportCandidate)
             .filter(ImportCandidate.batch_id == batch_id)
             .order_by(ImportCandidate.id).all())
    assert [c.sku for c in cands] == ["IMB-200", "IMB-201", "IMB-200"]
    assert cands[0].title == "Injector Cup"
    assert cands[0].new_price == 120.00
    assert cands[0].disposition == ImportDisposition.NEW
    # R1 dup-in-feed guard applies unchanged to the mapped path
    assert cands[2].disposition == ImportDisposition.DUPLICATE
    # raw_json carries the full canonical row for apply (cost / core / apps)
    raw = json.loads(cands[0].raw_json)
    assert raw["cost"] == "70.00"
    assert raw["core_charge"] == 25.00
    assert raw["apps"] == ["ISX"]


# ---------------------------------------------------------------------------
# 4. Saved template pre-fills the next identical-source upload
# ---------------------------------------------------------------------------

def test_saved_template_prefills_next_upload(client, db):
    # Save a template during the first mapped upload...
    r = _upload(client, _vendor_csv(("SAM-300", "Gasket", "12.00", "6.00", "0", "ISX")),
                source_app="sampa")
    upload_id = int(r.headers["location"].rstrip("/").split("/")[-1])
    r2 = client.post(f"/import-review/mapping/{upload_id}",
                     data=_mapping_form(_VENDOR_MAPPING, source_label="sampa",
                                        save_template="1",
                                        template_name="SAMPA price file"),
                     follow_redirects=False)
    assert r2.status_code == 303

    db.expire_all()
    tpl = (db.query(ImportMappingTemplate)
           .filter(ImportMappingTemplate.name == "SAMPA price file").first())
    assert tpl is not None and tpl.source_label == "sampa"
    assert tpl.mapping["Part Number"] == "sku"

    # ...then the NEXT upload from the same source pre-fills from it.
    r3 = _upload(client, _vendor_csv(("SAM-301", "Seal", "8.00", "4.00", "0", "N14")),
                 source_app="sampa")
    upload2 = int(r3.headers["location"].rstrip("/").split("/")[-1])
    page = client.get(f"/import-review/mapping/{upload2}")
    assert "Pre-filled from saved template" in page.text
    assert "SAMPA price file" in page.text
    assert 'data-prefill="core_charge"' in page.text   # template, not fuzzy guess
    client.post(f"/import-review/mapping/{upload2}/discard")


# ---------------------------------------------------------------------------
# 5. A mapping without SKU is rejected — nothing staged
# ---------------------------------------------------------------------------

def test_missing_sku_mapping_rejected(client, db):
    r = _upload(client, _vendor_csv(("SAM-400", "Bolt", "5.00", "2.00", "0", "ISX")),
                source_app="sampa")
    upload_id = int(r.headers["location"].rstrip("/").split("/")[-1])
    no_sku = {h: f for h, f in _VENDOR_MAPPING.items() if f != "sku"}

    batches_before = db.query(ImportBatch).count()
    r2 = client.post(f"/import-review/mapping/{upload_id}",
                     data=_mapping_form(no_sku, source_label="sampa"),
                     follow_redirects=False)
    assert r2.status_code == 400
    assert "SKU / Part Number" in r2.text

    db.expire_all()
    assert db.query(ImportBatch).count() == batches_before   # nothing staged
    assert db.get(ImportPendingUpload, upload_id) is not None  # still parked, retryable
    client.post(f"/import-review/mapping/{upload_id}/discard")

    # service-level guard too (route-independent)
    with pytest.raises(ValueError):
        ProductImportService(db, None).parse_mapped_csv(
            _vendor_csv(("X", "y", "1", "1", "0", "")), {"Description": "title"})


# ---------------------------------------------------------------------------
# 6. Template upsert by label
# ---------------------------------------------------------------------------

def test_template_upsert_by_label(client, db):
    svc = ImportReviewService(db, None)
    svc.save_mapping_template("IMB mapping", "imb", {"Part Number": "sku"})
    before = db.query(ImportMappingTemplate).filter(
        ImportMappingTemplate.name == "IMB mapping").count()
    assert before == 1

    # Same name (case-insensitive) → UPDATE in place, not a second row.
    svc.save_mapping_template("imb MAPPING", "imb-v2",
                              {"Part Number": "sku", "List Price": "price"})
    db.expire_all()
    rows = db.query(ImportMappingTemplate).filter(
        ImportMappingTemplate.name.ilike("imb mapping")).all()
    assert len(rows) == 1
    assert rows[0].source_label == "imb-v2"
    assert rows[0].mapping == {"Part Number": "sku", "List Price": "price"}

    # find_template_for_source matches the new label case-insensitively
    assert svc.find_template_for_source("IMB-V2").id == rows[0].id

    # delete works (template management on the index page)
    assert svc.delete_mapping_template(rows[0].id) is True
    assert db.query(ImportMappingTemplate).filter(
        ImportMappingTemplate.name.ilike("imb mapping")).count() == 0


# ---------------------------------------------------------------------------
# 7. R1 vendor-digit guard still fires through the mapped path
# ---------------------------------------------------------------------------

def test_vendor_digit_guard_fires_for_mapped_batch(client, db):
    """Applying a mapped NEW candidate runs the locked full_import path, which
    refuses to mint SKUs when the import vendor is missing its owner-set digit
    (R1-16) — the mapped path must not bypass that."""
    # Ensure the vendor has NO digit (it may have been seeded by another test).
    v = db.query(Vendor).filter(Vendor.name == "PAI Industries").first()
    if v is not None:
        v.vendor_number = ""
        db.commit()

    r = _upload(client, _vendor_csv(("SAM-700", "Rocker Arm", "99.00", "55.00", "0", "ISX")),
                source_app="sampa")
    upload_id = int(r.headers["location"].rstrip("/").split("/")[-1])
    r2 = client.post(f"/import-review/mapping/{upload_id}",
                     data=_mapping_form(_VENDOR_MAPPING, source_label="sampa"),
                     follow_redirects=False)
    batch_id = int(r2.headers["location"].rstrip("/").split("/")[-1])

    db.expire_all()
    cand = (db.query(ImportCandidate)
            .filter(ImportCandidate.batch_id == batch_id,
                    ImportCandidate.sku == "SAM-700").first())
    assert cand is not None

    svc = ImportReviewService(db, None)
    svc.set_review_status(cand.id, ScrapedItemReviewStatus.ACCEPTED)
    result = svc.apply_approved(batch_id)

    assert result["created"] == 0 and result["applied"] == 0
    assert len(result["errors"]) == 1
    assert "vendor digit" in result["errors"][0] or "does not exist" in result["errors"][0]
    assert db.query(Product).filter(Product.title == "Rocker Arm").count() == 0

    # With the vendor digit set, the SAME approved candidate applies cleanly —
    # the mapped row carries everything full_import needs.
    _seed_pai(db, vendor_number="9")
    result2 = svc.apply_approved(batch_id)
    assert result2["created"] == 1 and result2["errors"] == []
    db.expire_all()
    p = db.query(Product).filter(Product.title == "Rocker Arm").first()
    # Default vendor scheme: SKU = {prefix}-{vendor_code}-{part#} (PAI-routed).
    assert p is not None and p.sku == "JAKS-PAI-SAM-700"
