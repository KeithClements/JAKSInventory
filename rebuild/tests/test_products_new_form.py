"""
tests/test_products_new_form.py
================================
Vendor-SKU + fast-product-entry on the new-product form (MASTER_PLAN §20).

The owner replaced the typed-SKU UX with structured input — pick a Vendor, type
the Vendor Part #, optionally pick Engine + Category. The customer-facing SKU IS
the vendor's real part number (private-label parts instead carry the owner-typed
JAKS Product #; see test_product_private_label.py). Cost goes to the chosen
vendor's ProductVendorSource (NOT product.cost — that's moving-avg COGS) and the
typed part# is also stamped as a VENDOR_ALT CrossReference so search finds it.

This file locks the contract:
  • GET /products/new renders the new-shape form (vendor + engine, no SKU input).
  • POST /products/new persists the SKU (= vendor part #), source, and cross-ref.
  • Required-field validations re-render the form with the owner's exact wording.
  • Same vendor+part# on an ACTIVE source → blocked (duplicate source).
  • GET /products/classify-part returns category/engine/cost suggestions or nulls.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.constants import CrossRefType
from app.main import app
from app.models.product import (
    CrossReference, Product, ProductCategory, ProductVendorSource,
)
from app.models.vendor import Vendor
from tests.conftest import activate, fresh_engine


# ── Fixtures (mirror test_product_new_po_seed.py — per-test isolated DB) ──────

@pytest.fixture()
def db_session():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    return TestClient(app, follow_redirects=False)


def _seed_vendor(db, *, name="PAI Industries", code="PAI",
                 vendor_number="9", is_active=True) -> Vendor:
    v = Vendor(name=name, vendor_code=code,
               vendor_number=vendor_number, is_active=is_active)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _seed_category(db, name="Injectors", code="INJ", level=2, parent_id=None
                   ) -> ProductCategory:
    c = ProductCategory(name=name, code=code, level=level,
                        parent_id=parent_id, is_active=True)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _seed_product_with_source(db, vendor: Vendor, *, sku, part_number,
                              title="Seeded", category_id=None,
                              engine_model="", vendor_cost=10.0,
                              part_seq=None, engine_code="", category_code=""):
    p = Product(sku=sku, title=title, is_active=True,
                category_id=category_id, engine_model=engine_model,
                engine_code=engine_code, category_code=category_code,
                part_seq=part_seq)
    db.add(p)
    db.commit()
    db.refresh(p)
    s = ProductVendorSource(
        product_id=p.id, vendor_id=vendor.id,
        vendor_part_number=part_number, vendor_sku=part_number,
        vendor_cost=vendor_cost, is_preferred=True, is_active=True,
    )
    db.add(s)
    db.commit()
    return p


# ── GET /products/new — renders structured-input shape (no SKU field) ────────

def test_get_new_renders_vendor_and_engine_picker_not_sku_input(client, db_session):
    _seed_vendor(db_session)
    r = client.get("/products/new")
    assert r.status_code == 200
    html = r.text
    # New shape — Vendor select + Vendor Part #.
    assert 'name="vendor_id"' in html
    assert 'name="vendor_part_number"' in html
    # Engine picker macro is in (resolves engine_make/engine_model hidden fields).
    assert 'name="engine_make"' in html
    assert 'name="engine_model"' in html
    # No more manual SKU input on this form.
    assert 'name="sku"' not in html


# ── POST happy path ──────────────────────────────────────────────────────────

def test_post_creates_product_vendor_source_and_cross_ref(client, db_session):
    vendor = _seed_vendor(db_session)
    cat = _seed_category(db_session, name="Injectors", code="INJ", level=2)

    r = client.post("/products/new", data={
        "vendor_id": str(vendor.id),
        "vendor_part_number": "311148",
        "vendor_cost": "42.50",
        "title": "ISX Injector",
        "engine_make": "Cummins",
        "engine_model": "ISX",
        "category_id": str(cat.id),
        "cost": "0",  # legacy field — ignored on auto-SKU path for product.cost
        "reorder_point": "0",
        "vendor_core_charge": "0",
        "customer_core_charge": "0",
    })
    assert r.status_code == 303, r.text
    pid = int(r.headers["location"].rsplit("/", 1)[-1])

    db_session.expire_all()
    p = db_session.query(Product).get(pid)
    assert p is not None
    # MASTER_PLAN §20: the customer-facing SKU IS the vendor's real part number.
    assert p.sku == "311148"
    assert p.is_house_brand is False

    # Vendor source created, preferred, holds the typed cost.
    src = db_session.query(ProductVendorSource).filter(
        ProductVendorSource.product_id == p.id
    ).first()
    assert src is not None
    assert src.is_preferred is True
    assert src.is_active is True
    assert src.vendor_id == vendor.id
    assert src.vendor_part_number == "311148"
    assert src.vendor_cost == 42.50

    # VENDOR_ALT cross-reference created (uppercased).
    xref = db_session.query(CrossReference).filter(
        CrossReference.product_id == p.id,
        CrossReference.ref_type == CrossRefType.VENDOR_ALT,
    ).first()
    assert xref is not None
    assert xref.ref_number == "311148"


def test_post_sku_is_vendor_part_number(client, db_session):
    """The customer-facing SKU is exactly the typed vendor part # — no masking,
    no engine/category encoding (MASTER_PLAN §20)."""
    vendor = _seed_vendor(db_session)
    cat = _seed_category(db_session, name="Gaskets", code="GSK", level=2)
    r = client.post("/products/new", data={
        "vendor_id": str(vendor.id),
        "vendor_part_number": "MULTIFIT-1",
        "vendor_cost": "5.00",
        "title": "Universal seal",
        "engine_make": "", "engine_model": "",  # "No specific engine"
        "category_id": str(cat.id),
        "reorder_point": "0",
        "vendor_core_charge": "0", "customer_core_charge": "0",
    })
    assert r.status_code == 303, r.text
    pid = int(r.headers["location"].rsplit("/", 1)[-1])

    p = db_session.query(Product).get(pid)
    assert p.sku == "MULTIFIT-1"


# ── Required-field validations ───────────────────────────────────────────────

def test_post_without_vendor_id_returns_422(client, db_session):
    _seed_vendor(db_session)
    r = client.post("/products/new", data={
        "vendor_part_number": "X-1", "title": "no vendor",
    })
    assert r.status_code == 422
    assert "vendor is required" in r.text.lower()
    # Nothing persisted.
    assert db_session.query(Product).count() == 0


def test_post_without_vendor_part_number_returns_422(client, db_session):
    vendor = _seed_vendor(db_session)
    r = client.post("/products/new", data={
        "vendor_id": str(vendor.id), "title": "no part #",
    })
    assert r.status_code == 422
    assert "vendor part number is required" in r.text.lower()
    assert db_session.query(Product).count() == 0


def test_post_vendor_without_sku_digit_still_creates(client, db_session):
    """MASTER_PLAN §20 dropped the opaque vendor digit — a vendor needs no
    SKU # to create products; the SKU is just the vendor part #."""
    vendor = _seed_vendor(db_session, vendor_number="")
    r = client.post("/products/new", data={
        "vendor_id": str(vendor.id),
        "vendor_part_number": "X-2",
        "title": "Vendor without digit",
        "reorder_point": "0",
        "vendor_core_charge": "0", "customer_core_charge": "0",
    })
    assert r.status_code == 303, r.text
    pid = int(r.headers["location"].rsplit("/", 1)[-1])
    p = db_session.query(Product).get(pid)
    assert p.sku == "X-2"


# ── Duplicate (vendor + part#) on a different product → blocked ──────────────

def test_post_duplicate_vendor_part_blocks(client, db_session):
    vendor = _seed_vendor(db_session)
    cat = _seed_category(db_session, name="Injectors", code="INJ")
    _seed_product_with_source(
        db_session, vendor, sku="311148",
        part_number="311148", category_id=cat.id,
    )
    r = client.post("/products/new", data={
        "vendor_id": str(vendor.id),
        "vendor_part_number": "311148",  # same vendor + same part#
        "title": "Duplicate attempt",
        "reorder_point": "0",
        "vendor_core_charge": "0", "customer_core_charge": "0",
    })
    assert r.status_code == 422
    body = r.text.lower()
    assert "already sources" in body
    # No second product was created.
    assert db_session.query(Product).count() == 1


# ── Post-create search hook: cross-ref carries the typed part# ───────────────

def test_post_creates_findable_cross_reference(client, db_session):
    """A typed part # must be searchable post-create — that's the whole point of
    stamping it as a VENDOR_ALT CrossReference alongside the vendor source."""
    vendor = _seed_vendor(db_session)
    cat = _seed_category(db_session, name="Injectors", code="INJ")
    r = client.post("/products/new", data={
        "vendor_id": str(vendor.id),
        "vendor_part_number": "ABC-123",
        "title": "Findable",
        "category_id": str(cat.id),
        "reorder_point": "0",
        "vendor_core_charge": "0", "customer_core_charge": "0",
    })
    assert r.status_code == 303
    pid = int(r.headers["location"].rsplit("/", 1)[-1])

    found = (db_session.query(CrossReference)
             .filter(CrossReference.ref_number == "ABC-123")
             .first())
    assert found is not None
    assert found.product_id == pid
    assert found.ref_type == CrossRefType.VENDOR_ALT


# ── /products/classify-part ──────────────────────────────────────────────────

def test_classify_part_returns_nulls_when_no_match(client, db_session):
    vendor = _seed_vendor(db_session)
    r = client.get(
        f"/products/classify-part?vendor_id={vendor.id}&part=UNKNOWN-XYZ"
    )
    assert r.status_code == 200
    j = r.json()
    # Every field shaped — nulls on a miss, never KeyError.
    for key in ("category_id", "category_path",
                "engine_make", "engine_model", "suggested_cost"):
        assert key in j
    assert j["suggested_cost"] is None
    # No imported part → no category/engine to suggest.
    assert j["category_id"] is None
    assert j["engine_make"] is None


def test_classify_part_returns_suggested_cost_from_existing_source(client, db_session):
    """When THIS vendor already has the typed part# on an active source, return
    its vendor_cost so the form pre-fills the Vendor Cost input."""
    vendor = _seed_vendor(db_session)
    cat = _seed_category(db_session, name="Filters", code="FIL")
    _seed_product_with_source(
        db_session, vendor, sku="JAKS-FIL-90001",
        part_number="LF3000", category_id=cat.id, vendor_cost=18.75,
    )
    r = client.get(
        f"/products/classify-part?vendor_id={vendor.id}&part=LF3000"
    )
    assert r.status_code == 200
    j = r.json()
    assert j["suggested_cost"] == 18.75


# ── /products/twin-check ─────────────────────────────────────────────────────

def test_twin_check_returns_null_when_no_match(client, db_session):
    vendor = _seed_vendor(db_session)
    r = client.get(
        f"/products/twin-check?vendor_id={vendor.id}&part=NOMATCH-1"
    )
    assert r.status_code == 200
    assert r.json() == {"twin": None}


def test_twin_check_finds_match_on_other_vendor(client, db_session):
    """The typed (vendor, part#) is brand-new but ANOTHER vendor sources the
    same physical part# — that's a twin candidate."""
    pai = _seed_vendor(db_session, name="PAI", code="PAI", vendor_number="9")
    hhp = _seed_vendor(db_session, name="HHP", code="HHP", vendor_number="3")
    cat = _seed_category(db_session, name="Injectors", code="INJ")
    base = _seed_product_with_source(
        db_session, pai, sku="JAKS-ISX-INJ-90001",
        title="ISX Injector", part_number="311148",
        category_id=cat.id, part_seq=1,
    )
    # HHP types the SAME part# — they should be prompted to twin off `base`.
    r = client.get(
        f"/products/twin-check?vendor_id={hhp.id}&part=311148"
    )
    assert r.status_code == 200
    j = r.json()
    assert j["twin"] is not None
    assert j["twin"]["product_id"] == base.id
    assert j["twin"]["sku"] == "JAKS-ISX-INJ-90001"
    assert j["twin"]["vendor_name"] == "PAI"
    assert j["twin"]["title"] == "ISX Injector"


def test_twin_check_ignores_same_vendor_match(client, db_session):
    """A match on the SAME vendor is a duplicate, not a twin — the duplicate
    block on POST handles that path; twin-check must stay silent here."""
    vendor = _seed_vendor(db_session)
    cat = _seed_category(db_session, name="Injectors", code="INJ")
    _seed_product_with_source(
        db_session, vendor, sku="JAKS-INJ-90001",
        part_number="311148", category_id=cat.id,
    )
    r = client.get(
        f"/products/twin-check?vendor_id={vendor.id}&part=311148"
    )
    assert r.status_code == 200
    assert r.json() == {"twin": None}
