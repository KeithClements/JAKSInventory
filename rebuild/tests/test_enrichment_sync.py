"""
tests/test_enrichment_sync.py
=============================
Phase 2 §7.2 — ProductEnrichmentService (external catalog CSV → enrich existing
products' cross_references + product_applications).

Guards the hard invariants: match jaks_sku→sku, NEVER create a product, NEVER
touch cost/sell; idempotent upsert; application dedup at make/model+CPL grain.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import CrossRefType
from app.models.product import Product, CrossReference, ProductApplication
from app.services.enrichment_service import ProductEnrichmentService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, sku, cost=100.0):
    p = Product(sku=sku, title="Widget", cost=cost)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _xrefs(db, pid):
    return db.query(CrossReference).filter(CrossReference.product_id == pid).all()


def _apps(db, pid):
    return db.query(ProductApplication).filter(ProductApplication.product_id == pid).all()


def test_enriches_matched_product(db):
    p = _product(db, f"SKU-{next(_seq)}")
    rows = [{
        "jaks_sku": p.sku, "oem_number": "OEM-1", "competitor_number": "COMP-9",
        "vendor_part": "V-700", "brand": "Cummins",
        "engine_make": "Cummins", "engine_model": "ISX15", "cpl": "8523",
        "esn_range": "123-456",
    }]
    summary = ProductEnrichmentService(db).enrich_from_rows(rows)
    assert summary["matched"] == 1
    assert summary["cross_refs_added"] == 3
    assert summary["applications_added"] == 1

    xrefs = {(x.ref_type, x.ref_number) for x in _xrefs(db, p.id)}
    assert (CrossRefType.OEM, "OEM-1") in xrefs
    assert (CrossRefType.COMPETITOR, "COMP-9") in xrefs
    assert (CrossRefType.VENDOR_ALT, "V-700") in xrefs
    app = _apps(db, p.id)[0]
    assert (app.engine_make, app.engine_model, app.cpl, app.esn_range) == \
        ("Cummins", "ISX15", "8523", "123-456")


def test_case_insensitive_sku_match(db):
    p = _product(db, f"MixedCase-{next(_seq)}")
    summary = ProductEnrichmentService(db).enrich_from_rows(
        [{"jaks_sku": p.sku.lower(), "oem_number": "X100"}])
    assert summary["matched"] == 1 and summary["cross_refs_added"] == 1


def test_skips_unmatched_sku_never_creates_product(db):
    before = db.query(Product).count()
    summary = ProductEnrichmentService(db).enrich_from_rows(
        [{"jaks_sku": "DOES-NOT-EXIST", "oem_number": "Z900", "engine_make": "CAT"}])
    assert summary["skipped_no_product"] == 1
    assert summary["matched"] == 0
    assert db.query(Product).count() == before          # NEVER creates a product


def test_never_touches_cost_or_sell(db):
    p = _product(db, f"COST-{next(_seq)}", cost=250.0)
    cost_before = p.cost
    sell_before = getattr(p, "selling_price", None)
    ProductEnrichmentService(db).enrich_from_rows(
        [{"jaks_sku": p.sku, "oem_number": "C1", "engine_make": "Detroit"}])
    db.refresh(p)
    assert p.cost == cost_before == 250.0
    if sell_before is not None:
        assert getattr(p, "selling_price", None) == sell_before


def test_idempotent_rerun(db):
    p = _product(db, f"IDEM-{next(_seq)}")
    rows = [{"jaks_sku": p.sku, "oem_number": "OEM-A",
             "engine_make": "Cummins", "engine_model": "X15", "cpl": "1"}]
    svc = ProductEnrichmentService(db)
    first = svc.enrich_from_rows(rows)
    assert first["cross_refs_added"] == 1 and first["applications_added"] == 1
    second = svc.enrich_from_rows(rows)
    assert second["cross_refs_added"] == 0 and second["cross_refs_existing"] == 1
    assert second["applications_added"] == 0 and second["applications_existing"] == 1
    assert len(_xrefs(db, p.id)) == 1 and len(_apps(db, p.id)) == 1


def test_application_dedup_grain(db):
    p = _product(db, f"APP-{next(_seq)}")
    # same make/model/cpl twice in one batch → one application; differing cpl → two
    rows = [
        {"jaks_sku": p.sku, "engine_make": "Cummins", "engine_model": "ISX", "cpl": "100"},
        {"jaks_sku": p.sku, "engine_make": "Cummins", "engine_model": "ISX", "cpl": "100"},
        {"jaks_sku": p.sku, "engine_make": "Cummins", "engine_model": "ISX", "cpl": "200"},
    ]
    summary = ProductEnrichmentService(db).enrich_from_rows(rows)
    assert summary["applications_added"] == 2
    assert summary["applications_existing"] == 1
    assert len(_apps(db, p.id)) == 2


def test_no_sku_row_skipped(db):
    summary = ProductEnrichmentService(db).enrich_from_rows([{"oem_number": "Q1"}])
    assert summary["skipped_no_sku"] == 1


def test_csv_text(db):
    p = _product(db, f"CSV-{next(_seq)}")
    text = f"jaks_sku,oem_number,engine_make,engine_model,cpl\n{p.sku},OEM-CSV,Cummins,ISX15,9999\n"
    summary = ProductEnrichmentService(db).enrich_from_csv_text(text)
    assert summary["matched"] == 1
    assert summary["cross_refs_added"] == 1
    assert summary["applications_added"] == 1


def test_enrich_sync_route(client, db):
    p = _product(db, f"ROUTE-{next(_seq)}")
    text = f"jaks_sku,oem_number,engine_make,engine_model\n{p.sku},OEM-RT,Cummins,L9\n"
    r = client.post("/products/enrich-sync",
                    files={"file": ("enrich.csv", text, "text/csv")})
    assert r.status_code == 200
    data = r.json()
    assert data["matched"] == 1 and data["cross_refs_added"] == 1
    assert data["applications_added"] == 1
