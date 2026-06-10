"""
tests/test_r3_category_tools.py
===============================
R3 — Category maintenance tooling for the 13k-part post-import cleanup:

  R3-1  REPARENT — POST /categories/category/{id}/reparent moves a node (and
        its whole subtree) under a new parent or to the top level; validates
        target exists/active, no self/cycle, and the recomputed level of the
        node AND every descendant stays within MAX_LEVEL=3.

  R3-2  MERGE — POST /categories/category/{src}/merge-into/{dest} reassigns
        ALL of src's products to dest, adopts src's children (blocked with a
        clear error when adoption would exceed the 3-level cap), unions the
        import_keywords lists onto dest, then deactivates src.

  R3-3  Classifier keyword matching is word-boundary anchored — keyword
        'head' must no longer match 'overhead'; multi-word keywords like
        'gasket set' still match.

  R3-4  category_tree() rows now carry total_count (descendant-inclusive
        product count) alongside the direct product_count.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r3_category_tools.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
import app.models  # noqa: F401 — registers all models
from app.main import app
from app.models.product import Product, ProductCategory
from app.services.category_service import CategoryService
from app.services.classification_service import ClassificationService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, **kw) -> Product:
    n = next(_seq)
    p = Product(sku=f"R3-SKU-{n}", title=f"R3 Part {n}", **kw)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ══ R3-1 — reparent ══════════════════════════════════════════════════════════
def test_reparent_moves_subtree_and_recomputes_levels(db):
    svc = CategoryService(db)
    a = svc.create_category(name="R3 Engine A")
    b = svc.create_category(name="R3 Drivetrain B")
    c = svc.create_category(name="R3 Gaskets C", parent_id=a.id)
    d = svc.create_category(name="R3 Upper D", parent_id=c.id)
    assert (c.level, d.level) == (2, 3)

    # To top level: whole subtree shifts up one level.
    svc.reparent_category(c.id, None)
    db.expire_all()
    assert c.parent_id is None and c.level == 1
    assert d.parent_id == c.id and d.level == 2

    # Back under a different parent: subtree shifts down again.
    svc.reparent_category(c.id, b.id)
    db.expire_all()
    assert c.parent_id == b.id and c.level == 2
    assert d.level == 3


def test_reparent_rejects_self_and_cycle(db):
    svc = CategoryService(db)
    a = svc.create_category(name="R3 Cycle A")
    b = svc.create_category(name="R3 Cycle B", parent_id=a.id)
    c = svc.create_category(name="R3 Cycle C", parent_id=b.id)

    with pytest.raises(ValueError, match="under itself"):
        svc.reparent_category(a.id, a.id)
    with pytest.raises(ValueError, match="descendants"):
        svc.reparent_category(a.id, c.id)  # grandchild → cycle
    db.expire_all()
    assert a.parent_id is None and a.level == 1  # untouched


def test_reparent_rejects_depth_overflow(db):
    svc = CategoryService(db)
    a = svc.create_category(name="R3 Depth A")
    b = svc.create_category(name="R3 Depth B", parent_id=a.id)  # level 2
    src = svc.create_category(name="R3 Depth Src")
    child = svc.create_category(name="R3 Depth Child", parent_id=src.id)

    # src (height 2) under b (level 2) → child would land at level 4.
    with pytest.raises(ValueError, match="3 levels"):
        svc.reparent_category(src.id, b.id)
    db.expire_all()
    assert src.parent_id is None and child.level == 2  # untouched


def test_reparent_rejects_missing_or_inactive_target(db):
    svc = CategoryService(db)
    a = svc.create_category(name="R3 Target A")
    dead = svc.create_category(name="R3 Target Dead")
    svc.set_category_active(dead.id, False)

    with pytest.raises(ValueError, match="not found"):
        svc.reparent_category(a.id, 999_999)
    with pytest.raises(ValueError, match="inactive"):
        svc.reparent_category(a.id, dead.id)


# ══ R3-2 — merge ═════════════════════════════════════════════════════════════
def test_merge_reassigns_products_merges_keywords_deactivates_src(db):
    svc = CategoryService(db)
    src = svc.create_category(name="R3 Src Cams", import_keywords="camshaft, lifter")
    dest = svc.create_category(name="R3 Dest Valvetrain", import_keywords="lifter, pushrod")
    p1 = _product(db, category_id=src.id)
    p2 = _product(db, category_id=src.id)
    p3 = _product(db, category_id=dest.id)

    res = svc.merge_category(src.id, dest.id)

    db.expire_all()
    assert res["products_moved"] == 2
    assert p1.category_id == dest.id and p2.category_id == dest.id
    assert p3.category_id == dest.id  # untouched (already there)
    # Union, dest's first, case-insensitive dedupe ('lifter' appears once).
    assert dest.import_keywords == "lifter, pushrod, camshaft"
    assert src.is_active is False


def test_merge_adopts_children_when_depth_allows(db):
    svc = CategoryService(db)
    src = svc.create_category(name="R3 Adopt Src")
    child = svc.create_category(name="R3 Adopt Child", parent_id=src.id)
    grand = svc.create_category(name="R3 Adopt Grand", parent_id=child.id)
    dest = svc.create_category(name="R3 Adopt Dest")

    res = svc.merge_category(src.id, dest.id)

    db.expire_all()
    assert res["children_adopted"] == 1
    assert child.parent_id == dest.id and child.level == 2
    assert grand.parent_id == child.id and grand.level == 3
    assert src.is_active is False


def test_merge_blocked_when_children_would_overflow(db):
    svc = CategoryService(db)
    src = svc.create_category(name="R3 Block Src")
    child = svc.create_category(name="R3 Block Child", parent_id=src.id)
    top = svc.create_category(name="R3 Block Top")
    dest = svc.create_category(name="R3 Block Dest L2", parent_id=top.id)  # level 2
    p = _product(db, category_id=src.id)

    # child would land at level 3 BUT give src a grandchild → level 4 → block.
    grand = svc.create_category(name="R3 Block Grand", parent_id=child.id)
    with pytest.raises(ValueError, match="reparent the children"):
        svc.merge_category(src.id, dest.id)

    db.expire_all()
    assert src.is_active is True            # nothing happened
    assert p.category_id == src.id          # products untouched
    assert child.parent_id == src.id and grand.level == 3


def test_merge_rejects_self_and_descendant(db):
    svc = CategoryService(db)
    src = svc.create_category(name="R3 SelfMerge Src")
    child = svc.create_category(name="R3 SelfMerge Child", parent_id=src.id)

    with pytest.raises(ValueError, match="into itself"):
        svc.merge_category(src.id, src.id)
    with pytest.raises(ValueError, match="descendants"):
        svc.merge_category(src.id, child.id)


# ══ R3-3 — word-boundary classifier ══════════════════════════════════════════
# NOTE: the test app seeds the default category tree (app/seeds.py) with its own
# level-2 keyword rules ('gasket', 'cylinder head', …). Titles below are chosen
# so ONLY the categories created here can match — and the multi-word family sits
# at level 3 so deepest-first beats the seeded level-2 'gasket' rule.
def test_classifier_word_boundary_head_not_overhead(db):
    svc = CategoryService(db)
    parent = svc.create_category(name="R3 Cyl Components")
    fam = svc.create_category(name="R3 Heads", parent_id=parent.id,
                              import_keywords="head")
    cls = ClassificationService(db)
    miss = cls.classify(title="Overhead Console Bracket")
    assert miss["category_id"] is None          # 'head' must NOT match 'overhead'
    assert miss["needs_review"] is True

    hit = cls.classify(title="Replacement Head Casting")
    assert hit["category_id"] == fam.id         # standalone 'head' still matches
    assert hit["needs_review"] is False


def test_classifier_multiword_keyword_still_matches(db):
    svc = CategoryService(db)
    parent = svc.create_category(name="R3 Seal Components")
    sub = svc.create_category(name="R3 Seal Kits", parent_id=parent.id)
    fam = svc.create_category(name="R3 Gasket Sets", parent_id=sub.id,
                              import_keywords="gasket set")  # level 3
    cls = ClassificationService(db)
    hit = cls.classify(title="Upper Gasket Set")
    assert hit["category_id"] == fam.id and hit["needs_review"] is False
    # Boundary holds inside compounds too — neither 'gasket set' here nor the
    # seeded 'gasket' rule may fire on 'megagasket setup'.
    assert cls.classify(title="Megagasket Setup Tool")["category_id"] is None


# ══ R3-4 — descendant-inclusive tree counts ══════════════════════════════════
def test_tree_counts_include_descendants(db):
    svc = CategoryService(db)
    top = svc.create_category(name="R3 Count Top")
    mid = svc.create_category(name="R3 Count Mid", parent_id=top.id)
    leaf = svc.create_category(name="R3 Count Leaf", parent_id=mid.id)
    _product(db, category_id=top.id)
    _product(db, category_id=mid.id)
    _product(db, category_id=leaf.id)
    _product(db, category_id=leaf.id)

    rows = {r["cat"].id: r for r in svc.category_tree()}
    assert rows[top.id]["product_count"] == 1
    assert rows[top.id]["total_count"] == 4     # 1 direct + 3 in descendants
    assert rows[mid.id]["product_count"] == 1
    assert rows[mid.id]["total_count"] == 3
    assert rows[leaf.id]["product_count"] == 2
    assert rows[leaf.id]["total_count"] == 2    # leaf: direct only


def test_tree_targets_respect_depth_cap(db):
    svc = CategoryService(db)
    top = svc.create_category(name="R3 Elig Top")
    mid = svc.create_category(name="R3 Elig Mid", parent_id=top.id)
    svc.create_category(name="R3 Elig Leaf", parent_id=mid.id)

    rows = {r["cat"].id: r for r in svc.category_tree()}
    # top has a 3-deep subtree → NO parent can adopt it (would exceed level 3).
    assert rows[top.id]["reparent_targets"] == []
    # mid (height 2) can only move under top-level categories — never under
    # itself, its own subtree, or its current parent.
    mid_targets = rows[mid.id]["reparent_targets"]
    assert mid_targets and all(t.level == 1 for t in mid_targets)
    assert all(t.id not in (top.id, mid.id) for t in mid_targets)
    # merge destinations for top must be able to adopt its children:
    # dest.level + 2 <= 3 → only level-1 destinations.
    assert all(t.level == 1 for t in rows[top.id]["merge_targets"])


# ══ R3 — route wiring ════════════════════════════════════════════════════════
def test_reparent_and_merge_routes(client, db):
    svc = CategoryService(db)
    a = svc.create_category(name="R3 Route A")
    b = svc.create_category(name="R3 Route B", import_keywords="flywheel")
    c = svc.create_category(name="R3 Route C", parent_id=a.id)
    p = _product(db, category_id=b.id)

    # Reparent c under b via the route.
    r = client.post(f"/categories/category/{c.id}/reparent", data={"parent_id": str(b.id)})
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    db.expire_all()
    assert c.parent_id == b.id and c.level == 2

    # Invalid reparent (self) → error flash, not a 500.
    r = client.post(f"/categories/category/{a.id}/reparent", data={"parent_id": str(a.id)})
    assert r.status_code == 303 and "error=" in r.headers["location"]

    # Merge b (with product + adopted child c) into a via the route.
    r = client.post(f"/categories/category/{b.id}/merge-into/{a.id}")
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    db.expire_all()
    assert p.category_id == a.id
    assert b.is_active is False
    assert c.parent_id == a.id and c.level == 2   # child adopted by dest
    assert a.import_keywords == "flywheel"        # keywords carried over
