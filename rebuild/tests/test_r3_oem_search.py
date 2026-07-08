"""
tests/test_r3_oem_search.py
===========================
R3 — the products LIST screen finds parts by OEM / vendor / competitor number.

Before R3 ``GET /products/?q=`` only matched sku/title/manufacturer/brand, so a
number that worked in the quote line adder (SearchService) returned nothing on
the catalog list.  The list filter now adds EXISTS subqueries against:

  • cross_references.ref_number            (OEM / competitor / vendor-alt)
  • product_vendor_sources.vendor_part_number + vendor_sku
  • competitor_prices.competitor_part_number

…all normalized with the SAME helpers the line adder uses
(search_service._norm_col + utils.normalize_part), so separators/parens match.
Tab counts honor the search predicate too (counts match the visible list).

Also R3: the line-adder serializer emits a ``match_label`` and the
match_type='competitor' hit (R2) gets a proper COMP badge instead of falling
back to the default PART chip.
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
from app.models.competitor import CompetitorPrice
from app.models.product import CrossReference, Product, ProductVendorSource
from app.models.vendor import Vendor

_ENGINE = fresh_engine()


@pytest.fixture(autouse=True)
def _clean():
    SL = activate(_ENGINE)
    db = SL()
    db.query(CompetitorPrice).delete()
    db.query(CrossReference).delete()
    db.query(ProductVendorSource).delete()
    db.query(Product).delete()
    db.query(Vendor).delete()
    db.commit()
    db.close()
    yield


def _session():
    return activate(_ENGINE)()


def _seed():
    """Two products: R3WP carries an OEM cross-ref, a vendor source, and a
    competitor price; R3GK carries none (the negative control)."""
    db = _session()
    vendor = Vendor(name="PAI Industries", is_active=True)
    db.add(vendor)
    db.flush()

    wp = Product(sku="R3-WP-100", title="Water Pump Assembly", is_active=True)
    gk = Product(sku="R3-GK-200", title="Head Gasket Set", is_active=True)
    db.add_all([wp, gk])
    db.flush()

    db.add(CrossReference(product_id=wp.id, ref_type="oem",
                          ref_number="3683512(C)"))
    db.add(ProductVendorSource(product_id=wp.id, vendor_id=vendor.id,
                               vendor_part_number="805023",
                               vendor_sku="JAKS-PAI-805023",
                               is_active=True))
    db.add(CompetitorPrice(product_id=wp.id, competitor_name="HHP",
                           competitor_part_number="HHP-181904",
                           price=199.99, is_active=True))
    db.commit()
    ids = {"wp": wp.id, "gk": gk.id}
    db.close()
    return ids


def _tab_count(html: str, label: str) -> int:
    """Extract the rendered count for a filter tab (label followed by the
    count pill span — see macros/filter_tabs.html)."""
    m = re.search(rf"{re.escape(label)}\s*<span[^>]*>\s*(\d+)\s*</span>", html)
    assert m, f"tab {label!r} (with count pill) not found in page"
    return int(m.group(1))


# ── 1. OEM number finds the product on the list, and tab counts reflect it ──

def test_products_list_finds_by_oem_number_and_counts_reflect_search():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=3683512")
    assert r.status_code == 200
    assert "R3-WP-100" in r.text          # the OEM-matched product
    assert "R3-GK-200" not in r.text      # the control stays out
    # Tab counts honor the search predicate — only 1 active product matches.
    assert _tab_count(r.text, "All") == 1


# ── 2. Vendor part number / vendor SKU hit ───────────────────────────────────

def test_products_list_finds_by_vendor_part_number_and_vendor_sku():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=805023")          # vendor_part_number
    assert "R3-WP-100" in r.text and "R3-GK-200" not in r.text
    r2 = client.get("/products/?q=JAKS-PAI-805023")  # vendor_sku (with dashes)
    assert "R3-WP-100" in r2.text and "R3-GK-200" not in r2.text


# ── 3. Competitor part number hit ────────────────────────────────────────────

def test_products_list_finds_by_competitor_number():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=HHP-181904")
    assert r.status_code == 200
    assert "R3-WP-100" in r.text and "R3-GK-200" not in r.text


# ── 4. Normalized / parenthetical forms match (shared _norm_col behaviour) ──

def test_products_list_matches_normalized_and_parenthetical_forms():
    _seed()
    client = TestClient(app)
    # Stored "3683512(C)" must match the paren-less query — exactly like the
    # line adder's SearchService normalization.
    r = client.get("/products/?q=3683512C")
    assert "R3-WP-100" in r.text
    # Separator-insensitive competitor number ("HHP181904" vs "HHP-181904").
    r2 = client.get("/products/?q=hhp181904")
    assert "R3-WP-100" in r2.text


# ── 5. Plain title search unchanged (and the de-dashed SKU branch survives) ──

def test_products_list_plain_title_search_unchanged():
    _seed()
    client = TestClient(app)
    r = client.get("/products/?q=Head Gasket")
    assert "R3-GK-200" in r.text and "R3-WP-100" not in r.text
    # De-dash behaviour kept: "r3gk200" still finds SKU "R3-GK-200".
    r2 = client.get("/products/?q=r3gk200")
    assert "R3-GK-200" in r2.text


# ── 6. Inactive vendor source / competitor price rows do NOT match ───────────

def test_products_list_ignores_inactive_number_sources():
    ids = _seed()
    db = _session()
    db.query(ProductVendorSource).filter(
        ProductVendorSource.product_id == ids["wp"]
    ).update({"is_active": False})
    db.query(CompetitorPrice).filter(
        CompetitorPrice.product_id == ids["wp"]
    ).update({"is_active": False})
    db.commit()
    db.close()
    client = TestClient(app)
    assert "R3-WP-100" not in client.get("/products/?q=805023").text
    assert "R3-WP-100" not in client.get("/products/?q=HHP-181904").text
    # The (still-active) cross-ref keeps matching.
    assert "R3-WP-100" in client.get("/products/?q=3683512").text


# ── 7. Line-adder serializer emits the COMP badge for a competitor match ─────

def test_line_adder_serializer_emits_competitor_badge():
    _seed()
    client = TestClient(app)
    r = client.get("/line-items/product-search?q=HHP-181904")
    assert r.status_code == 200
    hits = r.json()
    assert hits, "competitor number should return the product"
    hit = next(h for h in hits if h["sku"] == "R3-WP-100")
    assert hit["match_type"] == "competitor"
    assert hit["match_label"] == "COMP"
    # The matched competitor number is surfaced like cross-ref matches are.
    assert hit["cross_ref_number"] == "HHP-181904"


# ── 8. Serializer labels for the other match types stay stable ───────────────

def test_line_adder_serializer_match_labels_stable():
    _seed()
    client = TestClient(app)
    by_q = {
        "R3-WP-100": "PART",       # sku match
        "3683512C": "OEM",         # cross-ref match (normalized parens)
        "805023": "VEND",          # vendor part number match
    }
    for q, label in by_q.items():
        hits = client.get(f"/line-items/product-search?q={q}").json()
        hit = next(h for h in hits if h["sku"] == "R3-WP-100")
        assert hit["match_label"] == label, f"q={q!r}"
