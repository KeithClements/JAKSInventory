"""
tests/test_product_keyword_search.py
====================================
The Products LIST screen now searches like the quote line adder: multi-word
queries NARROW (AND across words, OR across fields), engine FITMENT is searched,
results come back best-match-first, and a "why it matched" hint explains a row
that matched on something other than its visible SKU/title.

Before this, ``GET /products/?q=cat+spacer`` treated the whole string as one
literal phrase and ignored ``product_applications`` entirely → 0 results for a
part titled "SPACER PLATE" whose manufacturer is Caterpillar and which fits a
C15.  This is the regression the owner hit on the catalog screen.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine
from app.main import app
from app.models.product import Product, ProductApplication
from app.routers.products import _compute_match_hints

_ENGINE = fresh_engine()


@pytest.fixture(autouse=True)
def _clean():
    SL = activate(_ENGINE)
    db = SL()
    db.query(ProductApplication).delete()
    db.query(Product).delete()
    db.commit()
    db.close()
    yield


def _session():
    return activate(_ENGINE)()


def _seed():
    """A Caterpillar SPACER PLATE that fits a C15 (the C15 lives ONLY in the
    applications table, not in any product field), plus a Cummins HEAD GASKET
    control that shares none of the search words."""
    db = _session()
    spacer = Product(
        sku="KW-SPACER-1", title="SPACER PLATE",
        manufacturer="Caterpillar", engine_manufacturer="CAT / Caterpillar",
        is_active=True,
    )
    gasket = Product(
        sku="KW-GASKET-2", title="HEAD GASKET SET",
        manufacturer="Cummins", engine_manufacturer="Cummins",
        is_active=True,
    )
    db.add_all([spacer, gasket])
    db.flush()
    db.add(ProductApplication(
        product_id=spacer.id, engine_make="CATERPILLAR", engine_model="C15 ENGINE",
    ))
    db.commit()
    ids = {"spacer": spacer.id, "gasket": gasket.id}
    db.close()
    return ids


def _tab_count(html: str, label: str) -> int:
    m = re.search(rf"{re.escape(label)}\s*<span[^>]*>\s*(\d+)\s*</span>", html)
    assert m, f"tab {label!r} count pill not found in page"
    return int(m.group(1))


# ── 1. The headline bug: two plain words, neither a part number, find the part ──

def test_cat_spacer_finds_caterpillar_spacer():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=cat+spacer")
    assert r.status_code == 200
    assert "KW-SPACER-1" in r.text        # "cat"→manufacturer, "spacer"→title
    assert "KW-GASKET-2" not in r.text     # the gasket has neither word
    assert _tab_count(r.text, "All") == 1  # tab counts honor the multi-word search


# ── 2. Engine fitment — "c15 spacer" matches via product_applications only ─────

def test_c15_spacer_matches_via_engine_fitment():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=c15+spacer")
    assert "KW-SPACER-1" in r.text          # C15 is only in the fitment table
    assert "KW-GASKET-2" not in r.text


# ── 3. Each added word NARROWS (AND) — an unmatched word zeroes the result ─────

def test_extra_word_narrows_to_zero():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=cat+spacer+turbocharger")
    assert "KW-SPACER-1" not in r.text      # no field carries "turbocharger"
    assert _tab_count(r.text, "All") == 0


# ── 4. The "why it matched" hint explains a match the row doesn't show ─────────

def test_match_hint_explains_manufacturer_and_fitment():
    ids = _seed()
    db = _session()
    rows = db.query(Product).filter(Product.id == ids["spacer"]).all()
    # "cat" isn't in the title/SKU → hint surfaces the manufacturer.
    h_cat = _compute_match_hints(db, rows, "cat spacer")
    assert h_cat[ids["spacer"]] == "Caterpillar"
    # "c15" lives only in fitment → hint says it fits that engine.
    h_c15 = _compute_match_hints(db, rows, "c15 spacer")
    hint = h_c15[ids["spacer"]]
    assert "C15" in hint and hint.lower().startswith("fits")
    db.close()


# ── 5. Best-match first — a contiguous title hit ranks above a softer match ────

def test_best_match_first_ordering():
    db = _session()
    # KW-ORD-TITLE has the phrase in its TITLE; KW-ORD-OTHER only matches both
    # words via its description. Plain SKU sort would put OTHER first (O < T);
    # relevance must override that and float the title match to the top.
    db.add_all([
        Product(sku="KW-ORD-TITLE", title="SPACER PLATE", is_active=True),
        Product(sku="KW-ORD-OTHER", title="BRACKET",
                description="spacer plate retainer", is_active=True),
    ])
    db.commit()
    db.close()
    client = TestClient(app)
    r = client.get("/products/?q=spacer+plate")
    assert "KW-ORD-TITLE" in r.text and "KW-ORD-OTHER" in r.text
    assert r.text.index("KW-ORD-TITLE") < r.text.index("KW-ORD-OTHER")


# ── 6. The hint chip actually renders in the list HTML ────────────────────────

def test_hint_chip_renders_in_list():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=cat+spacer")
    assert "Matched your search on: Caterpillar" in r.text


# ── 7. Regression — a single plain word and a part-number search still work ────

def test_single_word_and_partnumber_search_unaffected():
    _seed()
    client = TestClient(app)
    # single word
    assert "KW-GASKET-2" in client.get("/products/?q=gasket").text
    # de-dashed SKU still resolves (sku_norm branch)
    assert "KW-SPACER-1" in client.get("/products/?q=kwspacer1").text
