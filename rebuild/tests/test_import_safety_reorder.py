"""Import-safety + reorder-tooling lane.

Three guarantees under test:

1. Direct importer (POST /products/import-run) — writes the LIVE catalog with
   zero staging, so it is (a) permission-gated (route require_admin AND the
   service-layer Permission.APPLY_IMPORT assert, so a wiring change can't
   silently un-gate it) and (b) a non-dry-run requires the exact typed phrase
   "APPLY TO LIVE CATALOG" or nothing is written. Dry-run stays frictionless.

2. Review-Queue bulk approval — the 'all' scope no longer sweeps flagged
   (needs_review / uncertain) candidates along with the confident ones; they
   stay PENDING unless include_flagged is explicitly posted. Enforced in
   ImportReviewService.bulk_set_status, not just the template.

3. Reorder tooling — /products/bulk-reorder-point sets a validated (int >= 0)
   reorder point on selected rows (negatives/garbage write nothing), and
   /products/reorder-backfill seeds a category subtree but ONLY rows whose
   reorder_point is still 0, so a deliberately-set value is never overwritten.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401 — registers all models on Base
from app.constants import ScrapedItemReviewStatus, UserRole
from app.deps import get_current_user_id, require_admin
from app.main import app
from app.models.import_review import ImportCandidate
from app.models.product import Product, ProductCategory
from app.models.user import User
from app.services.import_review_service import ImportReviewService
from tests.conftest import activate, fresh_engine

_CONFIRM = "APPLY TO LIVE CATALOG"

# ── Shopify-export CSV helpers (same shape as tests/test_import_review_service) ─

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


def _user(db, role, name="U"):
    n = db.query(User).count() + 1
    u = User(name=f"{name}{n}", username=f"{name.lower()}{n}",
             password_hash="x", role=role)
    db.add(u)
    db.commit()
    return u.id


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    # User id 1 == deps.DEFAULT_USER_ID (the identity TestClient requests resolve
    # to in the in-memory bypass) — seed it as a REAL ADMIN row so require_admin
    # and the service assert exercise actual role enforcement, not the
    # unknown-actor skip.
    admin_id = _user(s, UserRole.ADMIN, name="Admin")
    assert admin_id == 1
    # full_import (the NEW-product path) needs the PAI vendor with its owner-
    # assigned SKU digit, exactly as the live DB has it.
    from app.models.vendor import Vendor
    s.add(Vendor(name="PAI Industries", vendor_code="PAI",
                 vendor_number="9", is_active=True))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _post_import_run(client, *, dry_run: bool, confirm_text: str | None = None,
                     sku="DIRECT-IMP-001"):
    # No JAKS-… prefix on the feed SKU → full_import books it to the seeded PAI
    # vendor (a JAKS-XXX-… prefix would demand a vendor with code XXX).
    data = {"mode": "full", "dry_run": "true" if dry_run else "false",
            "import_images": "false"}
    if confirm_text is not None:
        data["confirm_text"] = confirm_text
    return client.post(
        "/products/import-run", data=data,
        files={"file": ("feed.csv", _csv([_prod_row(sku)]), "text/csv")},
    )


def _products(db) -> int:
    return db.query(Product).count()


# ═══ 1. Direct importer — permission gate ══════════════════════════════════════

def test_import_run_blocked_for_sales_user(db):
    """A real SALES user is 403'd from /products/import-run (route require_admin)
    and nothing is written."""
    sales_id = _user(db, UserRole.SALES)
    client = TestClient(app)
    app.dependency_overrides[get_current_user_id] = lambda: sales_id
    try:
        r = _post_import_run(client, dry_run=False, confirm_text=_CONFIRM)
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
    assert _products(db) == 0


def test_import_run_service_gate_holds_without_route_dep(db):
    """Even with the route-level require_admin dependency out of the picture,
    the route's own Permission.APPLY_IMPORT service assert still 403s a SALES
    user — the gate is not a single point of failure in the route wiring."""
    sales_id = _user(db, UserRole.SALES)
    client = TestClient(app)
    app.dependency_overrides[get_current_user_id] = lambda: sales_id
    app.dependency_overrides[require_admin] = lambda: None
    try:
        r = _post_import_run(client, dry_run=False, confirm_text=_CONFIRM)
        assert r.status_code == 403
        assert "error" in r.json()
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(require_admin, None)
    assert _products(db) == 0


# ═══ 1b. Direct importer — typed confirmation ══════════════════════════════════

def test_dry_run_needs_no_confirmation(db):
    """Dry-run stays frictionless: no confirm_text, HTTP 200, nothing written."""
    client = TestClient(app)
    r = _post_import_run(client, dry_run=True)
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["products_seen"] == 1
    assert _products(db) == 0


def test_live_run_without_confirm_writes_nothing(db):
    client = TestClient(app)
    r = _post_import_run(client, dry_run=False)          # no confirm_text at all
    assert r.status_code == 400
    assert _CONFIRM in r.json()["error"]
    assert _products(db) == 0


def test_live_run_with_wrong_confirm_writes_nothing(db):
    client = TestClient(app)
    for wrong in ("apply to live catalog", "APPLY", "APPLY TO LIVE CATALOG!"):
        r = _post_import_run(client, dry_run=False, confirm_text=wrong)
        assert r.status_code == 400, wrong
    assert _products(db) == 0


def test_live_run_with_exact_confirm_writes_for_admin(db):
    client = TestClient(app)
    r = _post_import_run(client, dry_run=False, confirm_text=_CONFIRM)
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is False
    assert body["created"] == 1
    assert db.query(Product).filter(Product.title == "Head Bolt").count() == 1


# ═══ 2. Bulk approval — flagged rows need an explicit opt-in ═══════════════════

def _staged_batch(db):
    """Stage 2 candidates and force a deterministic flag split: index 0 confident,
    index 1 flagged needs_review (independent of classifier heuristics)."""
    svc = ImportReviewService(db, None)
    batch = svc.analyze_feed(_csv([_prod_row("BULK-CONF-1"), _prod_row("BULK-FLAG-1")]))
    cands = (db.query(ImportCandidate)
             .filter(ImportCandidate.batch_id == batch.id)
             .order_by(ImportCandidate.id).all())
    cands[0].needs_review = False
    cands[1].needs_review = True
    db.commit()
    return batch, cands


def test_bulk_all_scope_leaves_flagged_pending(db):
    batch, cands = _staged_batch(db)
    n = ImportReviewService(db, None).bulk_set_status(
        batch.id, ScrapedItemReviewStatus.ACCEPTED, scope="all")
    assert n == 1
    db.refresh(cands[0]); db.refresh(cands[1])
    assert cands[0].review_status == ScrapedItemReviewStatus.ACCEPTED
    assert cands[1].review_status == ScrapedItemReviewStatus.PENDING


def test_bulk_all_scope_include_flagged_applies_them(db):
    batch, cands = _staged_batch(db)
    n = ImportReviewService(db, None).bulk_set_status(
        batch.id, ScrapedItemReviewStatus.ACCEPTED, scope="all",
        include_flagged=True)
    assert n == 2
    db.refresh(cands[1])
    assert cands[1].review_status == ScrapedItemReviewStatus.ACCEPTED


def test_flagged_scope_still_targets_flagged_rows(db):
    """The explicit 'flagged' scope (the secondary button / Reject flagged) keeps
    working — only the silent ride-along through 'all' was removed."""
    batch, cands = _staged_batch(db)
    n = ImportReviewService(db, None).bulk_set_status(
        batch.id, ScrapedItemReviewStatus.REJECTED, scope="flagged")
    assert n == 1
    db.refresh(cands[0]); db.refresh(cands[1])
    assert cands[0].review_status == ScrapedItemReviewStatus.PENDING
    assert cands[1].review_status == ScrapedItemReviewStatus.REJECTED


def _prod_row_with_oem(sku, oems, *, price="10.99"):
    """Feed row whose Body (HTML) carries an OEM References list — the shape
    parse_body_html() reads into the candidate's raw_json 'oem' payload."""
    body = ("<p>Part</p><p><strong>OEM References:</strong></p><ul>"
            + "".join(f"<li>{o}</li>" for o in oems) + "</ul>")
    return _row(Handle=sku.lower(), Title="Head Bolt", Type="ENGINE PARTS",
                Tags="CUMMINS",
                **{"Body (HTML)": body, "Variant SKU": sku,
                   "Variant Price": price}, Status="active")


def test_apply_update_silently_skips_garbage_oem_refs(db):
    """_apply_update: feed rows routinely carry placeholder refs ('N/A',
    'NUMBER OEM NUMBER'); add_cross_reference RAISES on those (manual-entry
    guard), which used to wedge the whole apply. The fix silent-skips garbage
    while still adding the legit refs — a revert makes this batch error out."""
    import json as _json

    from app.models.product import CrossReference

    existing = Product(sku="UPD-GARBAGE-1", title="Original Title",
                       price_override=25.00, cost=0.0)
    db.add(existing)
    db.commit()

    svc = ImportReviewService(db, None)
    batch = svc.analyze_feed(_csv([
        _prod_row_with_oem("UPD-GARBAGE-1",
                           ["CUMMINS 3901706", "CUMMINS N/A", "N/A"])
    ]))
    cands = (db.query(ImportCandidate)
             .filter(ImportCandidate.batch_id == batch.id).all())
    assert len(cands) == 1
    c = cands[0]
    # Sanity: the raw payload really carries the garbage entries alongside the
    # legit one — the skip must happen at APPLY time, not by losing feed data.
    raw_oem = _json.loads(c.raw_json)["oem"]
    assert "CUMMINS 3901706" in raw_oem
    assert "CUMMINS N/A" in raw_oem
    assert "N/A" in raw_oem

    svc.set_review_status(c.id, ScrapedItemReviewStatus.ACCEPTED)
    result = svc.apply_approved(batch.id)

    assert result["errors"] == []            # garbage never wedges the apply
    assert result["updated"] == 1
    assert result["cross_refs_added"] == 1   # only the legit ref was added

    refs = {
        x.ref_number
        for x in db.query(CrossReference)
        .filter(CrossReference.product_id == existing.id)
    }
    assert refs == {"3901706"}
    assert db.query(CrossReference).filter(
        CrossReference.ref_number == "N/A").count() == 0


def test_approve_and_apply_route_default_excludes_flagged(db):
    """POST /approve-and-apply scope=all (the old one-click primary) applies the
    confident row but leaves the flagged row PENDING and un-applied."""
    batch, cands = _staged_batch(db)
    conf_id, flagged_id = cands[0].id, cands[1].id
    client = TestClient(app)
    r = client.post(f"/import-review/{batch.id}/approve-and-apply",
                    data={"scope": "all"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(ImportCandidate, conf_id) is None           # applied → removed
    flagged = db.get(ImportCandidate, flagged_id)
    assert flagged is not None
    assert flagged.review_status == ScrapedItemReviewStatus.PENDING
    assert flagged.applied_product_id is None
    assert _products(db) == 1

    # The explicit flagged action (include_flagged posted) then pulls it in.
    r = client.post(f"/import-review/{batch.id}/approve-and-apply",
                    data={"scope": "flagged", "include_flagged": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(ImportCandidate, flagged_id) is None        # applied → removed
    assert _products(db) == 2


# ═══ 3. Reorder tooling ════════════════════════════════════════════════════════

def test_bulk_reorder_point_blocked_for_non_admin(db):
    """/products/bulk-reorder-point is a bulk write across the catalog — it
    carries require_admin. A real SALES user is 403'd and nothing is written."""
    p = Product(sku="RP-DENY", reorder_point=2)
    db.add(p)
    db.commit()
    sales_id = _user(db, UserRole.SALES)
    client = TestClient(app)
    app.dependency_overrides[get_current_user_id] = lambda: sales_id
    try:
        r = client.post("/products/bulk-reorder-point",
                        data={"product_ids": [str(p.id)],
                              "reorder_point": "9"},
                        follow_redirects=False)
        assert r.status_code == 403, f"expected 403 for SALES, got {r.status_code}"
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
    db.refresh(p)
    assert p.reorder_point == 2, "denied bulk-reorder must write nothing"


def test_bulk_reorder_point_sets_and_rejects_negatives(db):
    p1 = Product(sku="RP-A")
    p2 = Product(sku="RP-B")
    db.add_all([p1, p2])
    db.commit()
    client = TestClient(app)

    r = client.post("/products/bulk-reorder-point",
                    data={"product_ids": [str(p1.id), str(p2.id)],
                          "reorder_point": "5"},
                    follow_redirects=False)
    assert r.status_code == 303
    db.refresh(p1); db.refresh(p2)
    assert p1.reorder_point == 5 and p2.reorder_point == 5

    # Zero is a legitimate value (turns the reorder flag off).
    r = client.post("/products/bulk-reorder-point",
                    data={"product_ids": [str(p1.id)], "reorder_point": "0"},
                    follow_redirects=False)
    assert r.status_code == 303
    db.refresh(p1)
    assert p1.reorder_point == 0

    # Negative / garbage values write nothing.
    for bad in ("-3", "abc", ""):
        r = client.post("/products/bulk-reorder-point",
                        data={"product_ids": [str(p2.id)], "reorder_point": bad},
                        follow_redirects=False)
        assert r.status_code == 303
        db.refresh(p2)
        assert p2.reorder_point == 5, f"reorder_point={bad!r} should write nothing"


def test_reorder_backfill_touches_only_zero_rows(db):
    parent = ProductCategory(name="Engine")
    db.add(parent); db.flush()
    child = ProductCategory(name="Bearings", parent_id=parent.id, level=2)
    other = ProductCategory(name="Electrical")
    db.add_all([child, other]); db.flush()

    a = Product(sku="BF-A", category_id=parent.id)                     # 0 → filled
    b = Product(sku="BF-B", category_id=child.id)                      # subtree → filled
    c = Product(sku="BF-C", category_id=parent.id, reorder_point=7)    # deliberate → kept
    d = Product(sku="BF-D", category_id=other.id)                      # other cat → untouched
    e = Product(sku="BF-E", category_id=parent.id, is_active=False)    # inactive → untouched
    db.add_all([a, b, c, d, e])
    db.commit()
    client = TestClient(app)

    # Dry-run: reports the match count, writes nothing.
    r = client.post("/products/reorder-backfill",
                    data={"category_id": str(parent.id), "reorder_point": "4",
                          "dry_run": "true"})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    db.refresh(a)
    assert a.reorder_point == 0

    # Apply: only the reorder_point=0 rows under the subtree change.
    r = client.post("/products/reorder-backfill",
                    data={"category_id": str(parent.id), "reorder_point": "4",
                          "dry_run": "false"})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    for p in (a, b, c, d, e):
        db.refresh(p)
    assert a.reorder_point == 4 and b.reorder_point == 4
    assert c.reorder_point == 7          # never overwritten
    assert d.reorder_point == 0 and e.reorder_point == 0


def test_reorder_backfill_rejects_nonpositive_value(db):
    cat = ProductCategory(name="Gaskets")
    db.add(cat); db.flush()
    p = Product(sku="BF-NEG", category_id=cat.id)
    db.add(p)
    db.commit()
    client = TestClient(app)
    for bad in ("0", "-2"):
        r = client.post("/products/reorder-backfill",
                        data={"category_id": str(cat.id), "reorder_point": bad,
                              "dry_run": "false"})
        assert r.status_code == 200
        assert "error" in r.json()
        db.refresh(p)
        assert p.reorder_point == 0
