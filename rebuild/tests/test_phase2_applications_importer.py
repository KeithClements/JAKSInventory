"""
tests/test_phase2_applications_importer.py
=============================================
MASTER_PLAN §23.3 Phase 2 #1 — standalone ProductApplication batch importer.

ProductImportService.import_applications(text, dry_run=True) — a CSV of
sku/make/model/cpl/esn_range applied onto EXISTING products only (same
never-create contract as every other pricing_update_* mode). Idempotent on
the model-docstring-locked dedup grain (product_id, engine_make,
engine_model, cpl) — a repeat row refreshes esn_range instead of
duplicating.

Also pins a real bug found while wiring this: the import screen's
productImport() fetch() (Full Import + Pricing Update + this new
Applications mode) never stamped the CSRF header — the ENTIRE product
importer had 403'd since CSRF was added, silently, exactly like the two
Catalog Utilities buttons fixed earlier the same session.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.constants import ProductStatus
from app.main import app
from app.models.product import Product, ProductApplication
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    Session = activate(fresh_engine())
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _product(db, sku, **kw) -> Product:
    p = Product(sku=sku, title=f"Part {sku}", status=ProductStatus.ACTIVE,
               is_active=True, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


_HEADER = ["sku", "make", "model", "cpl", "esn_range"]


# ── Service: import_applications ───────────────────────────────────────────

def test_creates_application_for_existing_product(db):
    p = _product(db, "JAKS-PAI-100")
    svc = ProductImportService(db, None)

    result = svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-100", "Cummins", "ISX", "2250", "12345-67890"]]),
        dry_run=False)

    assert result["created"] == 1 and result["matched"] == 1
    apps = db.query(ProductApplication).filter(ProductApplication.product_id == p.id).all()
    assert len(apps) == 1
    assert apps[0].engine_make == "Cummins" and apps[0].engine_model == "ISX"
    assert apps[0].cpl == "2250" and apps[0].esn_range == "12345-67890"
    assert apps[0].source == "applications_import"


def test_dry_run_writes_nothing(db):
    p = _product(db, "JAKS-PAI-101")
    svc = ProductImportService(db, None)

    result = svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-101", "Cummins", "ISX", "", ""]]), dry_run=True)

    assert result["created"] == 1   # counted for preview
    assert db.query(ProductApplication).filter(
        ProductApplication.product_id == p.id).count() == 0   # nothing written


def test_never_creates_products(db):
    """The core contract every pricing_update_* mode shares — a sku with no
    match is skipped and counted, never silently minted as a new product."""
    svc = ProductImportService(db, None)
    result = svc.import_applications(
        _csv(_HEADER, [["NO-SUCH-SKU", "Cummins", "ISX", "", ""]]), dry_run=False)

    assert result["skipped_no_product"] == 1
    assert result["created"] == 0
    assert db.query(Product).count() == 0


def test_blank_sku_and_blank_make_are_skipped_and_counted(db):
    p = _product(db, "JAKS-PAI-102")
    svc = ProductImportService(db, None)
    result = svc.import_applications(_csv(_HEADER, [
        ["", "Cummins", "ISX", "", ""],               # no sku
        ["JAKS-PAI-102", "", "ISX", "", ""],           # no make
    ]), dry_run=False)

    assert result["skipped_no_sku"] == 1
    assert result["skipped_no_make"] == 1
    assert result["created"] == 0


def test_repeat_row_updates_instead_of_duplicating(db):
    """Idempotent on (product, make, model, cpl) — a re-run with a new
    esn_range refreshes the existing row, never duplicates it."""
    p = _product(db, "JAKS-PAI-103")
    svc = ProductImportService(db, None)

    svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-103", "Cummins", "ISX", "2250", "OLD-RANGE"]]),
        dry_run=False)
    result2 = svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-103", "Cummins", "ISX", "2250", "NEW-RANGE"]]),
        dry_run=False)

    assert result2["updated"] == 1 and result2["created"] == 0
    apps = db.query(ProductApplication).filter(ProductApplication.product_id == p.id).all()
    assert len(apps) == 1   # still just one row
    assert apps[0].esn_range == "NEW-RANGE"


def test_matches_via_parked_vendor_sku_same_as_other_import_modes(db):
    """sku_to_id resolves BOTH the product's own SKU and the parked
    ProductVendorSource.vendor_sku, matching every other importer mode."""
    from app.models.product import ProductVendorSource
    from app.models.vendor import Vendor
    v = Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9")
    db.add(v); db.flush()
    p = _product(db, "JAKS-PAI-104")
    db.add(ProductVendorSource(product_id=p.id, vendor_id=v.id,
                               vendor_sku="OLD-FEED-SKU-104", vendor_cost=10.0))
    db.commit()

    svc = ProductImportService(db, None)
    result = svc.import_applications(
        _csv(_HEADER, [["OLD-FEED-SKU-104", "Cummins", "ISX", "", ""]]), dry_run=False)

    assert result["matched"] == 1
    assert db.query(ProductApplication).filter(
        ProductApplication.product_id == p.id).count() == 1


def test_different_cpl_creates_a_separate_application(db):
    """CPL is part of the dedup grain — same make/model, different CPL is a
    genuinely distinct application row, not an update."""
    p = _product(db, "JAKS-PAI-105")
    svc = ProductImportService(db, None)

    svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-105", "Cummins", "ISX", "2250", ""]]), dry_run=False)
    svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-105", "Cummins", "ISX", "2350", ""]]), dry_run=False)

    apps = db.query(ProductApplication).filter(ProductApplication.product_id == p.id).all()
    assert len(apps) == 2
    assert {a.cpl for a in apps} == {"2250", "2350"}


def test_case_insensitive_dedup_matches_add_applications_own_rule(db):
    p = _product(db, "JAKS-PAI-106")
    svc = ProductImportService(db, None)

    svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-106", "Cummins", "ISX", "", ""]]), dry_run=False)
    result2 = svc.import_applications(
        _csv(_HEADER, [["JAKS-PAI-106", "CUMMINS", "isx", "", "SOME-RANGE"]]), dry_run=False)

    assert result2["updated"] == 1
    assert db.query(ProductApplication).filter(
        ProductApplication.product_id == p.id).count() == 1


def test_empty_file_returns_zero_rows(db):
    svc = ProductImportService(db, None)
    result = svc.import_applications(_csv(_HEADER, []), dry_run=False)
    assert result["rows"] == 0


# ── Route: POST /products/import-run mode=applications ─────────────────────

def test_route_mode_applications(db, client):
    p = _product(db, "JAKS-PAI-200")
    csv_text = _csv(_HEADER, [["JAKS-PAI-200", "Cummins", "ISX", "", ""]])

    r = client.post("/products/import-run",
                    data={"mode": "applications", "dry_run": "false"},
                    files={"file": ("apps.csv", csv_text, "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 1
    assert db.query(ProductApplication).filter(
        ProductApplication.product_id == p.id).count() == 1


# ── Regression: productImport()'s fetch() carries the CSRF header ──────────

def test_import_template_stamps_csrf_on_import_run_fetch():
    """Real bug found while wiring the Applications mode: productImport()'s
    fetch('/products/import-run', ...) never sent X-CSRF-Token — the ENTIRE
    product importer (Full Import + Pricing Update + this new mode) 403'd
    silently since CSRF was added. Pins the fix (reuses the same local
    _csrfToken() helper the two Catalog Utilities buttons use)."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "app/templates/products/import.html").read_text(encoding="utf-8")
    assert "function _csrfToken()" in src
    assert src.count("'X-CSRF-Token': _csrfToken()") == 3   # mfgBackfill + reclassifyAll + productImport
    assert "mode==='applications'" in src or 'mode===\'applications\'' in src
