"""
tests/test_r2_competitor_search.py
==================================
R2 — competitor part numbers must not be a dead end.

A customer calling with an HHP/ATL/IMB number has to find our part:

  1. SearchService.search_products grows a competitor-number strategy
     (JOIN competitor_prices, normalized LIKE, match_type='competitor',
     matched number surfaced via cross_ref_number) — placed after the
     vendor-part-number strategy and before description.
  2. _norm_col normalization widened to also strip ( ) + # % so a stored
     "3683512(C)" matches the fully-normalized query "3683512C"
     (utils.normalize_part strips ALL non-alphanumerics).
  3. The competitor pricing import upserts a competitor CrossReference per
     part # with a GLOBAL collision guard: a normalized ref_number already
     on a DIFFERENT product is skipped + counted (cross_ref_collisions),
     never silently duplicated. Idempotent on re-run.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r2_competitor_search.py -q
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401  (registers all models)
from app.constants import CrossRefType, ProductStatus
from app.models.competitor import CompetitorPrice
from app.models.product import CrossReference, Product
from app.services.product_import_service import ProductImportService
from app.services.search_service import SearchService
from app.utils import normalize_part
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    # Per-test fresh engine via the shared isolation harness (see conftest):
    # activate() repoints app.database globals at THIS in-memory engine.
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _product(db, sku: str, title: str = "Test Part") -> Product:
    p = Product(sku=sku, title=title, cost=100.0, markup_pct=40.0,
                qty_on_hand=2, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _comp_price(db, product_id: int, part: str, name: str = "HHP",
                price: float = 88.0) -> CompetitorPrice:
    cp = CompetitorPrice(product_id=product_id, competitor_name=name,
                         competitor_part_number=part, price=price)
    db.add(cp)
    db.commit()
    db.refresh(cp)
    return cp


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


_COMP_HDR = ["sku", "competitor_name", "competitor_part_number", "price"]


def _xrefs_for(db, norm_ref: str) -> list[CrossReference]:
    return [x for x in db.query(CrossReference).all()
            if normalize_part(x.ref_number) == norm_ref]


# ── 1. Search strategy: competitor part number ────────────────────────────────

def test_search_finds_product_by_competitor_number(db):
    """Line-adder path: SearchService surfaces a product by the competitor's
    number even when NO CrossReference row exists — match_type='competitor',
    our JAKS sku as part_number, their number surfaced like a cross-ref."""
    prod = _product(db, "JAKS-ISX-INJ-90001", "Injector")
    _comp_price(db, prod.id, "HHP-2026A", name="HHP")

    hits = SearchService(db).search_products("HHP-2026A")
    by_id = {h.product_id: h for h in hits}
    assert prod.id in by_id, "competitor number did not surface the product"
    hit = by_id[prod.id]
    assert hit.match_type == "competitor"
    assert hit.part_number == "JAKS-ISX-INJ-90001"   # OUR sku in the result
    assert hit.cross_ref_number == "HHP-2026A"       # THEIR number surfaced

    # Separator/case-insensitive, same as every other strategy.
    assert prod.id in {h.product_id for h in SearchService(db).search_products("hhp 2026a")}

    # A number nobody carries still finds nothing.
    assert SearchService(db).search_products("ZZ-NO-SUCH-COMP-999") == []


def test_parenthetical_stored_number_matches_normalized_query(db):
    """Normalization asymmetry fix: stored '3683512(C)' must match the query
    '3683512C' (normalize_part strips ALL non-alphanumerics; _norm_col now
    strips ( ) + # % too)."""
    prod = _product(db, "JAKS-855-GSK-90002", "Gasket Kit")
    _comp_price(db, prod.id, "3683512(C)", name="ATL Diesel")

    hits = SearchService(db).search_products("3683512C")
    by_id = {h.product_id: h for h in hits}
    assert prod.id in by_id, "parenthetical stored number did not match"
    assert by_id[prod.id].match_type == "competitor"
    assert by_id[prod.id].cross_ref_number == "3683512(C)"


def test_widened_norm_col_applies_to_cross_refs_too(db):
    """The same REPLACE-chain widening fixes the existing cross-ref strategy:
    a CrossReference stored with +/#/() separators matches the clean query."""
    prod = _product(db, "JAKS-N14-TRB-90003", "Turbo")
    db.add(CrossReference(product_id=prod.id, ref_type=CrossRefType.OEM,
                          ref_number="3804502+RX", brand="CUMMINS"))
    db.commit()

    hits = SearchService(db).search_products("3804502RX")
    by_id = {h.product_id: h for h in hits}
    assert prod.id in by_id
    assert by_id[prod.id].match_type == "cross_ref"


def test_exact_sku_still_wins_ordering(db):
    """Existing strategies unaffected: an exact SKU match still ranks first;
    the competitor hit rides behind it."""
    winner = _product(db, "R2WIN1", "Our Part")           # exact SKU match
    rival = _product(db, "JAKS-OTHER-90004", "Other Part")
    _comp_price(db, rival.id, "R2WIN1", name="FinditParts")  # competitor # collides with the sku

    hits = SearchService(db).search_products("R2WIN1")
    assert hits, "no results"
    assert hits[0].product_id == winner.id
    assert hits[0].match_type == "part_number"
    rival_hit = next((h for h in hits if h.product_id == rival.id), None)
    assert rival_hit is not None and rival_hit.match_type == "competitor"


def test_competitor_strategy_dedupes_against_seen(db):
    """A product already matched by an earlier strategy is NOT re-emitted by
    the competitor strategy (seen-id set dedupe)."""
    prod = _product(db, "R2DUP77", "Dedupe Part")
    _comp_price(db, prod.id, "R2DUP77", name="NAPA")  # same number as our sku

    hits = SearchService(db).search_products("R2DUP77")
    mine = [h for h in hits if h.product_id == prod.id]
    assert len(mine) == 1                       # exactly one row for the product
    assert mine[0].match_type == "part_number"  # the higher-priority match won


# ── 2. Import: competitor cross-ref upsert ────────────────────────────────────

def test_import_writes_cross_ref_once_idempotent(db):
    """The competitor pricing import upserts ONE competitor CrossReference per
    part # — re-running the same file adds nothing."""
    prod = _product(db, "R2-IMP-1", "Imported Part")
    svc = ProductImportService(db, None)
    text = _csv(_COMP_HDR, [["R2-IMP-1", "HHP", "HHP-9000", "100.00"]])

    summ1 = svc.pricing_update_competitor(text, dry_run=False)
    assert summ1["cross_refs_created"] == 1
    assert summ1["cross_ref_collisions"] == 0
    xrefs = _xrefs_for(db, normalize_part("HHP-9000"))
    assert len(xrefs) == 1
    assert xrefs[0].product_id == prod.id
    assert xrefs[0].ref_type == CrossRefType.COMPETITOR

    # Idempotent re-run: nothing new, no collision against ourselves.
    summ2 = svc.pricing_update_competitor(text, dry_run=False)
    assert summ2["cross_refs_created"] == 0
    assert summ2["cross_ref_collisions"] == 0
    assert len(_xrefs_for(db, normalize_part("HHP-9000"))) == 1

    # The customer-call scenario end-to-end: the number now finds the product.
    assert prod.id in {h.product_id for h in SearchService(db).search_products("HHP9000")}


def test_import_cross_product_collision_skipped_and_counted(db):
    """GLOBAL collision guard: the same normalized ref_number already on a
    DIFFERENT product → cross-ref skipped + counted; the CompetitorPrice row
    (market intelligence) is still written."""
    prod_a = _product(db, "R2-COLL-A", "Part A")
    prod_b = _product(db, "R2-COLL-B", "Part B")
    db.add(CrossReference(product_id=prod_b.id, ref_type=CrossRefType.OEM,
                          ref_number="HHP-555", brand="CUMMINS"))
    db.commit()

    svc = ProductImportService(db, None)
    summ = svc.pricing_update_competitor(
        _csv(_COMP_HDR, [["R2-COLL-A", "HHP", "HHP555", "75.00"]]), dry_run=False)

    assert summ["cross_ref_collisions"] == 1
    assert summ["cross_refs_created"] == 0
    assert summ["cross_ref_collision_sample"], "collision must not be silent"
    assert summ["cross_ref_collision_sample"][0]["sku"] == "R2-COLL-A"
    # No duplicate ref landed on product A — B's row is still the only owner.
    owners = {x.product_id for x in _xrefs_for(db, normalize_part("HHP555"))}
    assert owners == {prod_b.id}
    # Price intelligence unaffected by the cross-ref skip.
    cp = db.query(CompetitorPrice).filter(CompetitorPrice.product_id == prod_a.id).first()
    assert cp is not None and cp.price == 75.0


def test_import_dry_run_counts_but_writes_no_cross_ref(db):
    """dry_run computes the full summary (cross_refs_created counted) but
    writes neither the CompetitorPrice nor the CrossReference."""
    _product(db, "R2-DRY-1", "Dry Run Part")
    svc = ProductImportService(db, None)
    summ = svc.pricing_update_competitor(
        _csv(_COMP_HDR, [["R2-DRY-1", "IMB", "IMB-444", "50.00"]]), dry_run=True)
    assert summ["cross_refs_created"] == 1
    assert db.query(CrossReference).count() == 0
    assert db.query(CompetitorPrice).count() == 0


def test_import_without_part_number_writes_no_cross_ref(db):
    """Rows with no competitor_part_number never mint a CrossReference —
    preserves the existing 'never touches cross-refs' contract for them."""
    _product(db, "R2-NOPART-1", "No Part # Part")
    svc = ProductImportService(db, None)
    summ = svc.pricing_update_competitor(
        _csv(["sku", "competitor_name", "price"], [["R2-NOPART-1", "eBay", "120.00"]]),
        dry_run=False)
    assert summ["cross_refs_created"] == 0
    assert summ["cross_ref_collisions"] == 0
    assert db.query(CrossReference).count() == 0
    # The price row itself still lands.
    assert db.query(CompetitorPrice).count() == 1
