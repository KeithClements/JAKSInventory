"""
tests/test_phase1_engine_manufacturer_and_reclassify.py
=========================================================
MASTER_PLAN §23.3 Phase 1 #5 — engine picker sourced from the Manufacturer
table (not the hardcoded ENGINE_MAKES constant) + POST /products/reclassify-all.

Part A: the engine_picker macro's "Make" dropdown used to always render the
static constants.ENGINE_MAKES list. Product.manufacturer filters already read
the owner-maintained `manufacturers` table (Category Maintenance); the picker
used a SEPARATE list that could silently drift the moment an owner edited
manufacturers. engine_make_names(db) is the fix — DB-backed, "Other" appended
(seed_manufacturers() deliberately excludes it as a free-text escape hatch).

Part B: reclassify_all re-runs ClassificationService.classify() across every
product still missing category/engine-make/engine-model, filling ONLY blanks
(never overwriting an already-confirmed value) — mirrors backfill_manufacturers'
own conservative contract, extended to category + engine fields.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401
from app.models.product import Manufacturer, Product, ProductApplication, ProductCategory
from app.services.category_service import CategoryService, engine_make_names
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Part A: engine_make_names ──────────────────────────────────────────────────

def test_engine_make_names_reads_the_manufacturer_table(db):
    CategoryService(db).create_manufacturer("Cummins", sort_order=1)
    CategoryService(db).create_manufacturer("Detroit", sort_order=2)
    names = engine_make_names(db)
    assert names == ["Cummins", "Detroit", "Other"]


def test_engine_make_names_reflects_owner_edits_live(db):
    """The whole point of moving off the hardcoded constant: renaming a
    manufacturer via Category Maintenance is IMMEDIATELY reflected in the
    picker — no drift between the two lists."""
    m = CategoryService(db).create_manufacturer("Detriot")   # owner typo
    names_before = engine_make_names(db)
    assert "Detriot" in names_before

    CategoryService(db).update_manufacturer(m.id, name="Detroit")
    names_after = engine_make_names(db)
    assert "Detroit" in names_after
    assert "Detriot" not in names_after


def test_engine_make_names_excludes_inactive(db):
    m = CategoryService(db).create_manufacturer("Retired Make")
    CategoryService(db).update_manufacturer(m.id, is_active=False)
    assert "Retired Make" not in engine_make_names(db)


def test_engine_make_names_always_includes_other_even_if_no_manufacturers(db):
    assert engine_make_names(db) == ["Other"]


def test_engine_make_names_does_not_duplicate_other(db):
    # Defensive: if an owner ever names a real manufacturer "Other", don't
    # double-append it.
    CategoryService(db).create_manufacturer("Other")
    names = engine_make_names(db)
    assert names.count("Other") == 1


# ── Part B: reclassify_all ─────────────────────────────────────────────────────

def _category_with_keywords(db, name, keywords, level=2, parent_id=None):
    c = ProductCategory(name=name, level=level, is_active=True,
                        import_keywords=keywords, parent_id=parent_id)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, sku, title, **kw):
    kw.setdefault("is_active", True)
    p = Product(sku=sku, title=title, cost=10.0, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_reclassify_fills_blank_category_from_current_rules(db):
    root = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(root); db.commit()
    _category_with_keywords(db, "Fasteners", "head bolt", parent_id=root.id)

    p = _product(db, "JAKS-PAI-100", "Head Bolt Kit", category_id=None)
    svc = ProductImportService(db, None)

    preview = svc.reclassify_all(dry_run=True)
    assert preview["scanned"] == 1
    assert preview["reclassified"] == 1
    assert preview["category_filled"] == 1
    db.refresh(p)
    assert p.category_id is None    # dry run — nothing written

    result = svc.reclassify_all(dry_run=False)
    assert result["reclassified"] == 1
    db.refresh(p)
    assert p.category_id is not None


def test_reclassify_never_overwrites_a_set_category(db):
    """The core safety rule: a product that ALREADY has a category is left
    alone even though the classifier might now suggest a different one. The
    product is still SCANNED (its engine_manufacturer is separately blank —
    OR-candidacy), but category_id specifically must never move."""
    root = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(root); db.commit()
    cat_a = _category_with_keywords(db, "Fasteners", "head bolt", parent_id=root.id)
    _category_with_keywords(db, "Gaskets", "head bolt", parent_id=root.id)

    p = _product(db, "JAKS-PAI-200", "Head Bolt Kit", category_id=cat_a.id)
    result = ProductImportService(db, None).reclassify_all(dry_run=False)
    assert result["category_filled"] == 0   # category_id was already set — never touched
    db.refresh(p)
    assert p.category_id == cat_a.id        # untouched regardless of what the classifier suggests


def test_reclassify_fills_engine_manufacturer_from_applications(db):
    cat = ProductCategory(name="Fuel System", level=2, is_active=True)
    db.add(cat); db.commit()
    p = _product(db, "JAKS-PAI-300", "Injector", category_id=cat.id, engine_manufacturer="")
    # category_id already set, so this product's only open field is
    # engine_manufacturer — exercises that path in isolation.
    db.add(ProductApplication(product_id=p.id, engine_make="CUMMINS", engine_model="ISX"))
    db.commit()

    result = ProductImportService(db, None).reclassify_all(dry_run=False)
    assert result["engine_manufacturer_filled"] == 1
    db.refresh(p)
    assert p.engine_manufacturer == "Cummins"
    assert p.engine_model == "ISX"


def test_reclassify_never_overwrites_a_set_engine_manufacturer(db):
    cat = ProductCategory(name="Fuel System", level=2, is_active=True)
    db.add(cat); db.commit()
    p = _product(db, "JAKS-PAI-400", "Injector", category_id=cat.id,
                engine_manufacturer="Detroit")   # already set — must survive
    db.add(ProductApplication(product_id=p.id, engine_make="CUMMINS", engine_model="ISX"))
    db.commit()

    result = ProductImportService(db, None).reclassify_all(dry_run=False)
    assert result["scanned"] == 0   # category_id set AND engine_manufacturer set → not a candidate
    db.refresh(p)
    assert p.engine_manufacturer == "Detroit"


def test_reclassify_multi_make_applications_leaves_engine_manufacturer_blank(db):
    cat = ProductCategory(name="Fuel System", level=2, is_active=True)
    db.add(cat); db.commit()
    p = _product(db, "JAKS-PAI-500", "Universal Bracket", category_id=cat.id, engine_manufacturer="")
    db.add(ProductApplication(product_id=p.id, engine_make="CUMMINS", engine_model="ISX"))
    db.add(ProductApplication(product_id=p.id, engine_make="MACK", engine_model="MP8"))
    db.commit()

    result = ProductImportService(db, None).reclassify_all(dry_run=False)
    assert result["engine_manufacturer_filled"] == 0
    db.refresh(p)
    assert p.engine_manufacturer == ""


def test_reclassify_inactive_products_skipped(db):
    root = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(root); db.commit()
    _category_with_keywords(db, "Fasteners", "head bolt", parent_id=root.id)
    p = _product(db, "JAKS-PAI-600", "Head Bolt Kit", category_id=None, is_active=False)

    result = ProductImportService(db, None).reclassify_all(dry_run=False)
    assert result["scanned"] == 0
    db.refresh(p)
    assert p.category_id is None


# ── Part C: POST /products/reclassify-all route + the CSRF bug it surfaced ────

def test_reclassify_all_route_dry_run_then_apply(db):
    """The route mirrors backfill-manufacturers exactly: Form(dry_run), admin-
    gated, delegates straight to the service."""
    from fastapi.testclient import TestClient
    from app.main import app

    root = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(root); db.commit()
    _category_with_keywords(db, "Fasteners", "head bolt", parent_id=root.id)
    p = _product(db, "JAKS-PAI-700", "Head Bolt Kit", category_id=None)

    # The context-manager form triggers the app's startup event (seeds the
    # DEFAULT_USER_ID=1 admin the test-env auth bypass resolves to) — a bare
    # TestClient(app) skips it and every admin-gated route redirects to /login.
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/products/reclassify-all", data={"dry_run": "true"})
        assert r.status_code == 200
        body = r.json()
        assert body["dry_run"] is True and body["reclassified"] == 1
        db.expire_all()
        assert db.get(Product, p.id).category_id is None    # dry-run wrote nothing

        r2 = client.post("/products/reclassify-all", data={"dry_run": "false"})
        assert r2.status_code == 200 and r2.json()["dry_run"] is False
        db.expire_all()
        assert db.get(Product, p.id).category_id is not None


def test_import_template_stamps_csrf_header_on_both_utility_fetches():
    """Regression for a real bug this feature surfaced: a bare fetch() POST
    from a template never carries the CSRF cookie (base.html's csrfToken() is
    scoped inside its own IIFE; htmx:configRequest only stamps hx-*/htmx
    requests). Both mfgBackfill() and the new reclassifyAll() used to 403
    every single call (verified live: POST /products/reclassify-all → 403
    before the fix, → 200 after). Pins that both fetch() calls set the header
    via a local _csrfToken() helper so a future edit can't silently drop it."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "app/templates/products/import.html").read_text(encoding="utf-8")
    assert "function _csrfToken()" in src
    assert src.count("'X-CSRF-Token': _csrfToken()") == 2   # mfgBackfill + reclassifyAll
    assert "/products/backfill-manufacturers" in src
    assert "/products/reclassify-all" in src
