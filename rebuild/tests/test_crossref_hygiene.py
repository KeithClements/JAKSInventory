"""
tests/test_crossref_hygiene.py
==============================
Cross-reference hygiene + line-adder exact-match ranking.

Confirmed defect: cross_references carried placeholder garbage ('NUMBER OEM
NUMBER' → 5,339 products, 'N/A' → 3,440, 'G' → 1,783) with no validation at
import, and the line-adder search returned a LIMIT-8 slice where the exact
match could be crowded out entirely — direct wrong-part-sold risk.

Covers:
  • is_garbage_ref truth table (denylist + <3-alphanumerics rule)
  • full_import skips garbage refs, keeps legit ones, tallies skips
  • purge_garbage_crossrefs: dry-run counts without deleting; apply deletes
    ONLY garbage rows
  • POST /catalog-hygiene/crossrefs/apply: ADMIN purges + audits; SALES → 403
  • exact normalized match ranks FIRST (exact SKU and exact cross-ref beat
    contains-matches) and the endpoint returns the total via X-Total-Matches
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine  # noqa: E402

import app.database as _appdb  # noqa: E402
from app.constants import CrossRefType, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.models.product import CrossReference, Product  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.vendor import Vendor  # noqa: E402
from app.services.crossref_hygiene import (  # noqa: E402
    is_garbage_ref,
    purge_garbage_crossrefs,
)
from app.services.product_import_service import ProductImportService  # noqa: E402
from app.services.search_service import SearchService  # noqa: E402

_ENGINE = fresh_engine()
_seq = itertools.count(1)
_CLERK_PW = "clerk-pw-not-default"


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        # Session-cookie signing secret now lives in THIS module's engine.
        import app.auth as auth
        auth.reset_secret_cache()
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── builders ──────────────────────────────────────────────────────────────────

def _product(db, sku: str, **kw) -> Product:
    p = Product(sku=sku, title=kw.pop("title", f"Part {sku}"), **kw)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _xref(db, product_id: int, ref: str, ref_type: str = CrossRefType.OEM) -> CrossReference:
    x = CrossReference(product_id=product_id, ref_type=ref_type, ref_number=ref)
    db.add(x)
    db.commit()
    db.refresh(x)
    return x


def _pai_vendor(db) -> Vendor:
    v = db.query(Vendor).filter(Vendor.vendor_code == "PAI").first()
    if v is None:
        v = Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9")
        db.add(v)
        db.commit()
        db.refresh(v)
    return v


def _import_row(sku: str, oem: list[str]) -> dict:
    """Pre-parsed row dict in the parse_mapped_csv/parse_jaks shape that
    full_import(rows=...) consumes."""
    return {
        "sku": sku, "title": f"TEST PART {sku}", "type": "",
        "tags": "", "price": "100.00", "compare_at": "",
        "cost": "50.00", "barcode": "", "grams": "",
        "status": "active", "availability": "",
        "pai_part": sku, "oem": oem, "apps": [], "images": [],
        "warranty_months": 0, "handle": sku.lower(),
        "core_charge": None, "source_format": "jaks",
        "core_unknown_reman": False, "is_reman": False,
        "unit_of_measure": "EA", "pack_qty": 1,
    }


# ── is_garbage_ref truth table ─────────────────────────────────────────────────

@pytest.mark.parametrize("ref", [
    None, "", "   ",
    "N/A", "n/a", "NA", "N / A",
    "G", "-", "--", "1", "AB",          # < 3 alphanumerics
    "NONE", "null", "NIL",
    "NUMBER OEM NUMBER", "number oem number",
    "OEM", "OEM NUMBER", "O.E.M.", "oem-number",
    "TBD", "UNKNOWN", "MISC",
    "SEE NOTES", "see-notes", "CALL",
])
def test_is_garbage_ref_true(ref):
    assert is_garbage_ref(ref) is True


@pytest.mark.parametrize("ref", [
    "3901706",          # real Cummins OEM number
    "OK-1",             # 3 alphanumerics after separator strip
    "3683512(C)",
    "A1B",
    "R23525566",
    "PART-88",          # contains a denylist WORD but is a real number
    "23532333",
])
def test_is_garbage_ref_false(ref):
    assert is_garbage_ref(ref) is False


# ── FIX 2: import skips garbage, keeps legit ──────────────────────────────────

def test_full_import_skips_garbage_refs_keeps_legit(db):
    _pai_vendor(db)
    row = _import_row("TST1001", [
        "CUMMINS 3901706",       # legit
        "MACK 25171799",         # legit
        "CUMMINS N/A",           # garbage (denylist)
        "NUMBER OEM NUMBER",     # garbage (the 5,339-product offender)
        "G",                     # garbage (< 3 alphanumerics)
    ])
    summary = ProductImportService(db).full_import(rows=[row], dry_run=False)

    assert summary["created"] == 1
    assert summary["cross_refs"] == 2
    assert summary["cross_refs_skipped_garbage"] == 3

    prod = db.query(Product).filter(Product.sku == "JAKS-PAI-TST1001").first()
    assert prod is not None
    refs = {
        x.ref_number
        for x in db.query(CrossReference).filter(CrossReference.product_id == prod.id)
    }
    assert refs == {"3901706", "25171799"}


def test_full_import_dry_run_tallies_garbage_without_writing(db):
    _pai_vendor(db)
    before = db.query(CrossReference).count()
    row = _import_row("TST2002", ["CUMMINS 3068898", "VOLVO N/A", "OEM NUMBER"])
    summary = ProductImportService(db).full_import(rows=[row], dry_run=True)

    assert summary["cross_refs"] == 1
    assert summary["cross_refs_skipped_garbage"] == 2
    assert db.query(CrossReference).count() == before      # nothing written


# ── FIX 1: purge dry-run counts, apply deletes only garbage ───────────────────

def test_purge_dry_run_counts_without_deleting(db):
    p = _product(db, f"PURGE-{next(_seq)}")
    garbage = ["N/A", "NUMBER OEM NUMBER", "G", "--"]
    legit = ["3901706", "OK-1"]
    for r in garbage + legit:
        _xref(db, p.id, r)

    before = db.query(CrossReference).count()
    report = purge_garbage_crossrefs(db, dry_run=True)

    assert report["dry_run"] is True
    assert report["deleted"] == 0
    assert report["garbage_rows"] >= len(garbage)
    assert report["total_rows"] == before
    offenders = dict(report["by_ref_number"])
    assert offenders.get("N/A", 0) >= 1
    assert offenders.get("NUMBER OEM NUMBER", 0) >= 1
    assert db.query(CrossReference).count() == before      # nothing deleted


def test_purge_apply_deletes_only_garbage(db):
    p = _product(db, f"PURGE-{next(_seq)}")
    for r in ["N/A", "G", "SEE NOTES"]:
        _xref(db, p.id, r)
    keep = _xref(db, p.id, "4089649")

    dry = purge_garbage_crossrefs(db, dry_run=True)
    report = purge_garbage_crossrefs(db, dry_run=False)
    db.commit()

    assert report["deleted"] == dry["garbage_rows"] == report["garbage_rows"]
    left = [
        x.ref_number
        for x in db.query(CrossReference).filter(CrossReference.product_id == p.id)
    ]
    assert left == ["4089649"]
    assert db.get(CrossReference, keep.id) is not None
    # Table-wide: no garbage row survives anywhere.
    assert purge_garbage_crossrefs(db, dry_run=True)["garbage_rows"] == 0


# ── FIX 3: hygiene surface — preview + RBAC-gated apply ───────────────────────

def test_preview_page_renders(client, db):
    p = _product(db, f"PURGE-{next(_seq)}")
    _xref(db, p.id, "N/A")
    r = client.get("/catalog-hygiene/crossrefs")
    assert r.status_code == 200
    assert "Cross-Reference Hygiene" in r.text
    # Preview is a dry run — the seeded garbage row must SURVIVE the page load.
    # (Pin the exact row: a regression that makes the preview purge would pass a
    # looser any-row-survives check.)
    db.expire_all()
    assert db.query(CrossReference).filter(
        CrossReference.product_id == p.id,
        CrossReference.ref_number == "N/A",
    ).count() == 1


def test_admin_apply_purges_and_audits(client, db):
    p = _product(db, f"PURGE-{next(_seq)}")
    _xref(db, p.id, "N/A")
    keep = _xref(db, p.id, "3411767")

    # No session cookie + in-memory engine → DEFAULT_USER_ID #1 (seeded ADMIN).
    r = client.post("/catalog-hygiene/crossrefs/apply", follow_redirects=False)
    assert r.status_code == 303

    db.expire_all()
    assert (
        db.query(CrossReference)
        .filter(CrossReference.product_id == p.id, CrossReference.ref_number == "N/A")
        .count() == 0
    )
    assert db.get(CrossReference, keep.id) is not None
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "cross_reference", AuditLog.action == "deleted")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert "hygiene purge" in (audit.notes or "")


def test_sales_user_blocked_from_apply(client, db):
    from app.auth import hash_password

    n = next(_seq)
    db.add(User(name=f"Clerk {n}", username=f"clerk_{n}",
                password_hash=hash_password(_CLERK_PW), role=UserRole.SALES))
    db.commit()

    p = _product(db, f"PURGE-{next(_seq)}")
    _xref(db, p.id, "N/A")

    login = client.post("/login", data={"username": f"clerk_{n}", "password": _CLERK_PW},
                        follow_redirects=False)
    assert login.status_code == 303
    try:
        r = client.post("/catalog-hygiene/crossrefs/apply", follow_redirects=False)
        assert r.status_code == 403
        db.expire_all()
        assert (
            db.query(CrossReference)
            .filter(CrossReference.product_id == p.id, CrossReference.ref_number == "N/A")
            .count() == 1
        )  # nothing was deleted
    finally:
        client.cookies.clear()   # drop the clerk session for later tests


# ── FIX 4: exact-match-first ranking + total match count ──────────────────────

def test_exact_sku_ranks_first_and_endpoint_returns_total(client, db):
    # 9 contains-matches + 1 exact — more than the LIMIT-8 slice.
    for i in range(1, 10):
        _product(db, f"Q77001-{i}")
    exact = _product(db, "Q77001")

    results = SearchService(db).search_products("Q77001", limit=8)
    assert results[0].product_id == exact.id
    assert results[0].match_type == "part_number"
    assert len(results) == 8

    total = SearchService(db).count_product_matches("Q77001")
    assert total == 10

    r = client.get("/line-items/product-search?q=Q77001")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 8
    assert body[0]["sku"] == "Q77001"
    assert int(r.headers["X-Total-Matches"]) == 10


def test_exact_crossref_beats_sku_contains_matches(db):
    # THE reported failure: 8 SKU-contains matches fill the LIMIT-8 slice and
    # the product whose OEM number IS the query never appears.
    for i in range(1, 9):
        _product(db, f"PX888777-{i}")
    target = _product(db, f"ZZTOP-{next(_seq)}")
    _xref(db, target.id, "888-777X")   # normalizes to 888777x

    results = SearchService(db).search_products("888777X", limit=8)
    assert results[0].product_id == target.id
    assert results[0].match_type == "cross_ref"
    assert results[0].cross_ref_number == "888-777X"
