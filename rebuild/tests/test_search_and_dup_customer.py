"""
tests/test_search_and_dup_customer.py
=====================================
Two never-covered counter paths the dispatch called out:

  1. Product search by VENDOR PART NUMBER (the PAI catalog #). A part is found
     by the number printed in the PAI catalog even though product.sku is now the
     regenerated JAKS-scheme SKU — the match runs through
     ProductVendorSource.vendor_part_number (SearchService strategy #4), is
     separator/case-insensitive (normalize_part), and reports match_type
     'vendor_sku' while returning the JAKS sku as part_number.

  2. Duplicate-customer detection — _find_duplicate_customers (routers/customers)
     is the soft-warn guard that stops a counter person silently creating a
     second "Mike's Diesel". Unit-cover its contract (normalized exact-eq OR
     >=4-char substring either direction; active-only; self/inactive excluded)
     plus the end-to-end quick-create flow: a near-dupe WARNS without creating,
     and only creates once the user confirms.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_search_and_dup_customer.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import ProductStatus
from app.main import app
from app.models.customer import Customer
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.routers.customers import _find_duplicate_customers
from app.services.search_service import SearchService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── 1. Search by vendor part number (PAI catalog #) ──────────────────────────

def test_search_finds_product_by_vendor_part_number(db):
    n = next(_seq)
    jaks_sku = f"JAKS-ISX-INJ-{90000 + n}"          # JAKS-scheme sku, NOT the PAI #
    pai_part = f"20970{n}"                            # the number in the PAI catalog
    prod = Product(sku=jaks_sku, title="Injector", cost=120.0, markup_pct=40.0,
                   qty_on_hand=3, status=ProductStatus.ACTIVE, is_active=True)
    db.add(prod); db.commit(); db.refresh(prod)
    vendor = Vendor(name=f"PAI {n}")
    db.add(vendor); db.commit(); db.refresh(vendor)
    db.add(ProductVendorSource(
        product_id=prod.id, vendor_id=vendor.id,
        vendor_part_number=pai_part, vendor_sku=f"PAI-{pai_part}",
        vendor_cost=80.0, is_active=True, is_preferred=True))
    db.commit()

    hits = SearchService(db).search_products(pai_part)
    by_id = {h.product_id: h for h in hits}
    assert prod.id in by_id, f"PAI part {pai_part} did not surface the product"
    hit = by_id[prod.id]
    assert hit.match_type == "vendor_sku"            # matched via the vendor source
    assert hit.part_number == jaks_sku              # result reports the JAKS sku

    # Separator/case-insensitive: a dashed variant of the same catalog # still hits
    dashed = f"20-970{n}"
    assert prod.id in {h.product_id for h in SearchService(db).search_products(dashed)}

    # A part number nobody carries finds nothing.
    assert SearchService(db).search_products("ZZ-NO-SUCH-PART-999") == []


# ── 2a. Duplicate-customer detection — the contract ──────────────────────────

def _cust(db, name, *, active=True):
    c = Customer(company_name=name, contact_name="QA", is_active=active)
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_find_duplicate_matches_normalized_exact(db):
    stem = f"Alpha{next(_seq)}Diesel"
    _cust(db, f"{stem}")
    # punctuation/case/space differences normalize away -> still a duplicate
    matches = _find_duplicate_customers(db, stem.upper().replace("D", " D") + "!")
    assert any(m.company_name == stem for m in matches)


def test_find_duplicate_matches_substring_either_direction(db):
    n = next(_seq)
    short = f"Mike{n}sDiesel"
    long = f"Mike{n}s Diesel Repair LLC"
    _cust(db, long)
    # querying the SHORT name finds the longer existing record (norm short in long)
    assert any(m.company_name == long for m in _find_duplicate_customers(db, short))
    # and vice-versa: existing short, query the longer name
    _cust(db, short)
    names = {m.company_name for m in _find_duplicate_customers(db, long)}
    assert short in names and long in names


def test_find_duplicate_ignores_short_fragments(db):
    # A normalized name < 4 chars must NOT fuzzy-match on substring (only exact),
    # so a 2-letter query never floods warnings off unrelated companies.
    _cust(db, f"AB Holdings {next(_seq)}")
    assert _find_duplicate_customers(db, "AB") == []


def test_find_duplicate_excludes_self_and_inactive(db):
    n = next(_seq)
    me = _cust(db, f"Bravo{n}Diesel")
    _cust(db, f"Bravo{n}Diesel Archived", active=False)   # soft-deleted near-dupe
    # editing myself must not flag myself, and inactive rows are never warned on
    matches = _find_duplicate_customers(db, f"Bravo{n}Diesel", exclude_id=me.id)
    assert matches == []


def test_find_duplicate_no_false_positive_on_unrelated(db):
    n = next(_seq)
    _cust(db, f"Charlie{n} Engine Works")
    assert _find_duplicate_customers(db, f"Delta{n} Transmission Co") == []


# ── 2b. Duplicate-customer detection — the quick-create UX (warn, then create) ─

def test_quick_create_warns_on_duplicate_then_creates_on_confirm(client, db):
    n = next(_seq)
    existing = f"Echo{n}sDiesel"
    near_dupe = f"Echo{n}s Diesel"          # normalizes to the same -> duplicate
    _cust(db, existing)
    before = db.query(Customer).count()

    # POST a near-duplicate WITHOUT confirm -> warns (re-renders form), no create
    r = client.post("/customers/quick-create", data={"company_name": near_dupe})
    assert r.status_code == 200
    db.expire_all()
    assert db.query(Customer).count() == before, "near-duplicate was created without confirm"
    assert existing in r.text, "the warning did not surface the existing match"

    # POST again WITH confirm_duplicate=1 -> creates it
    r2 = client.post("/customers/quick-create",
                     data={"company_name": near_dupe, "confirm_duplicate": "1"})
    db.expire_all()
    assert db.query(Customer).count() == before + 1, "confirmed create did not persist"
