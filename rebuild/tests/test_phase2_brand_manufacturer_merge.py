"""
tests/test_phase2_brand_manufacturer_merge.py
================================================
MASTER_PLAN §23.3 Phase 2 — brand merge + constrain brand/manufacturer to
managed lists.

Part A: CategoryService.merge_brand / merge_manufacturer — mirror
merge_category's contract (reassign products, soft-deactivate src, never
hard-delete) but simpler (no hierarchy/children). merge_manufacturer
reassigns BOTH Product.manufacturer and Product.engine_manufacturer, since
the Manufacturer model docstring calls them "the SAME concept" and both
now read this one managed list.

Part B: the product-form Manufacturer/Brand fields used to read (a) a
hardcoded 8-entry MANUFACTURERS constant with no owner-editable backing at
all for Manufacturer, and (b) plain free text for Brand. Both are now
constrained <select> dropdowns sourced from the Category-Maintenance-
managed Brand/Manufacturer tables (manufacturer_names(db)/brand_names(db)),
with the SAME "legacy-preserving" fallback the engine picker already used —
a stored/submitted value outside the canonical list still renders selected,
never silently blanked.

NOTE: app startup seeds BOTH Brand (from constants.BRANDS) and Manufacturer
(from constants.ENGINE_MAKES) with real-world names (Cummins, Interstate-
McBee, ...) — every name this file creates is suffixed with the shared
counter so it can never collide with a seeded row.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401
from app.constants import ProductStatus
from app.main import app
from app.models.product import Brand, Manufacturer, Product
from app.services.category_service import (
    CategoryService, brand_names, manufacturer_names,
)
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(fresh_engine())
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _uniq(label: str) -> str:
    return f"{label} {next(_seq)}"


def _product(db, *, brand="", manufacturer="", engine_manufacturer="", **kw) -> Product:
    n = next(_seq)
    kw.setdefault("sku", f"MRG-{n}")
    kw.setdefault("title", f"Merge Part {n}")
    p = Product(brand=brand, manufacturer=manufacturer,
                engine_manufacturer=engine_manufacturer,
                status=ProductStatus.ACTIVE, is_active=True, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── Part A: merge_brand ────────────────────────────────────────────────────

def test_merge_brand_reassigns_products_and_deactivates_src(db):
    svc = CategoryService(db)
    src_name, dest_name = _uniq("PIA Industries"), _uniq("PAI Industries")
    src = svc.create_brand(src_name)
    dest = svc.create_brand(dest_name)
    p = _product(db, brand=src_name)

    result = svc.merge_brand(src.id, dest.id)
    assert result == {"products_moved": 1, "dest_name": dest_name}

    db.refresh(p)
    assert p.brand == dest_name
    db.refresh(src)
    assert src.is_active is False


def test_merge_brand_case_insensitive_match(db):
    """A product's brand string doesn't have to match the src Brand row's
    exact casing to be caught by the merge (e.g. a sloppy import stamped it
    in all-caps) — the reassignment matches case-insensitively, same rule
    update_brand's own rename cascade already uses."""
    svc = CategoryService(db)
    src_name, dest_name = _uniq("Interstate-McBee-Old"), _uniq("Interstate-McBee")
    src = svc.create_brand(src_name)
    dest = svc.create_brand(dest_name)
    p = _product(db, brand=src_name.upper())   # different casing than the Brand row

    svc.merge_brand(src.id, dest.id)
    db.refresh(p)
    assert p.brand == dest_name


def test_merge_brand_never_hard_deletes_source(db):
    """Unlike delete_brand (which hard-deletes when unused), merge ALWAYS
    soft-deactivates — a merged-away brand should stay resolvable in
    history/audit even if it had zero products."""
    svc = CategoryService(db)
    src = svc.create_brand(_uniq("Empty Src Brand"))
    dest = svc.create_brand(_uniq("Dest Brand"))

    svc.merge_brand(src.id, dest.id)
    still_there = db.get(Brand, src.id)
    assert still_there is not None
    assert still_there.is_active is False


def test_merge_brand_rejects_self_and_inactive_dest(db):
    svc = CategoryService(db)
    a = svc.create_brand(_uniq("Self Brand"))
    with pytest.raises(ValueError):
        svc.merge_brand(a.id, a.id)

    inactive_dest = svc.create_brand(_uniq("Inactive Dest"))
    svc.update_brand(inactive_dest.id, is_active=False)
    src = svc.create_brand(_uniq("Some Src"))
    with pytest.raises(ValueError):
        svc.merge_brand(src.id, inactive_dest.id)


def test_brand_merge_route(client, db):
    svc = CategoryService(db)
    src_name, dest_name = _uniq("Route Src Brand"), _uniq("Route Dest Brand")
    src = svc.create_brand(src_name)
    dest = svc.create_brand(dest_name)
    p = _product(db, brand=src_name)

    r = client.post(f"/categories/brand/{src.id}/merge-into/{dest.id}")
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    db.expire_all()
    assert p.brand == dest_name


# ── Part A: merge_manufacturer ─────────────────────────────────────────────

def test_merge_manufacturer_reassigns_both_fields(db):
    """The Manufacturer table governs BOTH Product.manufacturer (product-form
    dropdown) AND Product.engine_manufacturer (engine picker) — merge must
    fix both, unlike update_manufacturer's rename cascade which only
    touches engine_manufacturer."""
    svc = CategoryService(db)
    src_name, dest_name = _uniq("Cummuns"), _uniq("Cummins")
    src = svc.create_manufacturer(src_name)
    dest = svc.create_manufacturer(dest_name)
    p1 = _product(db, manufacturer=src_name)                       # only Product.manufacturer set
    p2 = _product(db, engine_manufacturer=src_name)                # only engine_manufacturer set
    p3 = _product(db, manufacturer=src_name, engine_manufacturer=src_name)  # both

    result = svc.merge_manufacturer(src.id, dest.id)
    assert result["dest_name"] == dest_name
    assert result["products_moved"] == 3   # 3 DISTINCT products touched, not 4 field-writes

    db.refresh(p1); db.refresh(p2); db.refresh(p3)
    assert p1.manufacturer == dest_name
    assert p2.engine_manufacturer == dest_name
    assert p3.manufacturer == dest_name and p3.engine_manufacturer == dest_name


def test_merge_manufacturer_deactivates_src_never_hard_deletes(db):
    svc = CategoryService(db)
    src = svc.create_manufacturer(_uniq("Empty Src Mfg"))
    dest = svc.create_manufacturer(_uniq("Dest Mfg"))

    svc.merge_manufacturer(src.id, dest.id)
    still_there = db.get(Manufacturer, src.id)
    assert still_there is not None and still_there.is_active is False


def test_manufacturer_merge_route(client, db):
    svc = CategoryService(db)
    src_name, dest_name = _uniq("Route Src Mfg"), _uniq("Route Dest Mfg")
    src = svc.create_manufacturer(src_name)
    dest = svc.create_manufacturer(dest_name)
    p = _product(db, manufacturer=src_name, engine_manufacturer=src_name)

    r = client.post(f"/categories/manufacturer/{src.id}/merge-into/{dest.id}")
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    db.expire_all()
    assert p.manufacturer == dest_name
    assert p.engine_manufacturer == dest_name


# ── Part B: constrained product-form dropdowns ─────────────────────────────

def test_manufacturer_names_reads_the_managed_table_not_the_hardcoded_constant(db):
    svc = CategoryService(db)
    name = _uniq("Totally Custom Make")
    svc.create_manufacturer(name)
    names = manufacturer_names(db)
    assert name in names
    # The old hardcoded list never had this value — proves the source moved.
    from app.routers.products import MANUFACTURERS
    assert name not in MANUFACTURERS


def test_brand_names_reads_the_managed_brand_table(db):
    svc = CategoryService(db)
    name = _uniq("Totally Custom Brand")
    svc.create_brand(name)
    assert name in brand_names(db)


def test_product_new_page_renders_manufacturer_and_brand_as_selects(client, db):
    svc = CategoryService(db)
    mfg_name, brand_name = _uniq("Render Test Mfg"), _uniq("Render Test Brand")
    svc.create_manufacturer(mfg_name)
    svc.create_brand(brand_name)

    r = client.get("/products/new")
    assert r.status_code == 200
    assert 'name="manufacturer"' in r.text
    assert '<select name="manufacturer"' in r.text   # constrained, not free-text input
    assert '<select name="brand"' in r.text
    assert mfg_name in r.text
    assert brand_name in r.text


def test_product_detail_page_renders_brand_as_select_with_legacy_fallback(client, db):
    """A product carrying a brand value OUTSIDE the managed list (imported
    before the list existed, or since deactivated) must still show that
    value selected — never silently blanked."""
    unlisted = _uniq("Some Legacy Unlisted Brand")
    p = _product(db, brand=unlisted)
    r = client.get(f"/products/{p.id}")
    assert r.status_code == 200
    assert '<select name="brand"' in r.text
    assert unlisted in r.text


def test_product_update_422_rerender_includes_engine_makes(client, db):
    """Regression: the validation-error re-render path was missing
    engine_makes/engine_models_by_make/category_tree entirely (found while
    fixing this same route's manufacturers/brands context) — the engine
    picker macro would render broken on any 422. Force a validation error
    (negative cost) and confirm the re-render doesn't 500."""
    p = _product(db)
    r = client.post(f"/products/{p.id}", data={
        "title": p.title, "sku": p.sku, "cost": "-50",
    })
    assert r.status_code == 422
    assert "select" in r.text.lower()   # rendered fully, not a partial/broken page
