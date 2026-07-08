"""
tests/test_vendor_source_cost_sync.py
=====================================
§8N (Option A, owner-ruled 2026-06-01): ``product.cost`` is the moving-weighted-
average COGS, maintained ONLY by ``InventoryService._apply_moving_average_cost``
on PO receipt. The vendor QUOTE price lives on
``ProductVendorSource.vendor_cost``.

Adding or switching a preferred vendor source therefore:
  * updates ``ProductVendorSource.vendor_cost``,
  * records a ``ProductCostHistory`` row (so the Cost History tab shows vendor-
    quote changes alongside receipt-driven changes), and
  * still auto-prefers the first source / switches the preferred flag,
but it must NOT write ``product.cost`` — the vendor quote is not the product's
cost of record.

History / supersession
----------------------
This file originally guarded the OPPOSITE behavior (the 2026-06-01 owner bug that
mirrored the preferred source's cost up to ``product.cost``). §8N — committed in
``0fbf59a fix(D-10): product.cost = moving-avg COGS only`` — reversed that ruling.
The tests below now pin the §8N behavior. (See also
``tests/test_product_cost_semantic.py``, the §8N receipt-side guards.)

UI follow-up (NOT money-path, tracked separately)
--------------------------------------------------
The add/prefer routes in ``app/routers/products.py`` still emit an HX-Trigger
``product-cost-synced`` carrying the vendor quote, and ``products/detail.html``
still listens for it to live-update the Pricing card's "our cost". Under §8N that
value no longer equals ``product.cost`` (it self-corrects on reload). Removing /
repurposing that live-update is a UI-Builder cleanup; it is intentionally not
asserted here.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.product import Product, ProductCostHistory, ProductVendorSource
from app.models.vendor import Vendor
from app.services.product_service import ProductService
from tests.conftest import activate, fresh_engine


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


def _seed_product(db, *, cost=20.0):
    """A vendor + a product priced at ``cost`` with no vendor sources yet."""
    vendor = Vendor(name="Acme Diesel", vendor_code="ACME", is_active=True)
    product = Product(sku="COST-SYNC-1", title="Test Injector", cost=cost,
                      qty_on_hand=0, is_active=True)
    db.add_all([vendor, product])
    db.commit()
    return vendor.id, product.id


def _add_vendor(db, name, code):
    v = Vendor(name=name, vendor_code=code, is_active=True)
    db.add(v)
    db.commit()
    return v.id


# ── service layer ──────────────────────────────────────────────────────────────

def test_first_source_autoprefers_but_leaves_product_cost(db_session):
    """The first source still auto-prefers, but §8N forbids moving product.cost
    (the moving-avg COGS) from a vendor quote."""
    vendor_id, product_id = _seed_product(db_session, cost=20.0)
    svc = ProductService(db_session, current_user_id=1)

    # "is_preferred" intentionally NOT passed — the checkbox was left unchecked.
    source = svc.add_vendor_source(product_id, vendor_id, {"vendor_cost": 10.0})

    assert source.is_preferred is True, "the first source should still auto-prefer"
    product = db_session.query(Product).filter(Product.id == product_id).first()
    assert product.cost == 20.0, (
        "§8N: a vendor-source quote must NOT move product.cost (moving-avg COGS)"
    )
    assert product.cost_source == "manual", (
        "§8N: vendor-source sync must not stamp cost_source='vendor'"
    )


def test_vendor_source_change_records_history(db_session):
    """The Cost History tab still gets a row when the preferred vendor's quote
    differs from the stored product.cost — only the side effect on product.cost
    is removed."""
    vendor_id, product_id = _seed_product(db_session, cost=20.0)
    ProductService(db_session, current_user_id=1).add_vendor_source(
        product_id, vendor_id, {"vendor_cost": 10.0}
    )

    hist = (
        db_session.query(ProductCostHistory)
        .filter(ProductCostHistory.product_id == product_id)
        .all()
    )
    assert len(hist) == 1
    assert hist[0].old_cost == 20.0
    assert hist[0].new_cost == 10.0


def test_adding_sources_never_moves_product_cost(db_session):
    """Neither a preferred nor a non-preferred source moves product.cost under §8N."""
    vendor_id, product_id = _seed_product(db_session, cost=20.0)
    svc = ProductService(db_session, current_user_id=1)
    svc.add_vendor_source(product_id, vendor_id, {"vendor_cost": 10.0})  # auto-preferred

    v2 = _add_vendor(db_session, "Other Co", "OTHR")
    svc.add_vendor_source(product_id, v2, {"vendor_cost": 5.0})          # not preferred

    product = db_session.query(Product).filter(Product.id == product_id).first()
    assert product.cost == 20.0, "§8N: product.cost is moving-avg COGS, untouched by quotes"


def test_explicit_preferred_switches_flag_not_product_cost(db_session):
    """Marking a second source preferred switches the preferred flag but must not
    change product.cost (§8N)."""
    vendor_id, product_id = _seed_product(db_session, cost=20.0)
    svc = ProductService(db_session, current_user_id=1)
    svc.add_vendor_source(product_id, vendor_id, {"vendor_cost": 10.0})

    v2 = _add_vendor(db_session, "Other Co", "OTHR")
    s2 = svc.add_vendor_source(product_id, v2, {"vendor_cost": 7.5, "is_preferred": True})

    product = db_session.query(Product).filter(Product.id == product_id).first()
    assert product.cost == 20.0, "§8N: switching preferred vendor must not move product.cost"
    prefs = [s for s in product.vendor_sources if s.is_preferred]
    assert len(prefs) == 1 and prefs[0].id == s2.id, "only the new source stays preferred"


def test_set_preferred_vendor_switches_flag_not_product_cost(db_session):
    """set_preferred_vendor switches the flag; product.cost stays the moving avg (§8N)."""
    vendor_id, product_id = _seed_product(db_session, cost=20.0)
    svc = ProductService(db_session, current_user_id=1)
    svc.add_vendor_source(product_id, vendor_id, {"vendor_cost": 10.0})

    v2 = _add_vendor(db_session, "Other Co", "OTHR")
    s2 = svc.add_vendor_source(product_id, v2, {"vendor_cost": 5.0})  # not preferred

    svc.set_preferred_vendor(product_id, s2.id)
    product = db_session.query(Product).filter(Product.id == product_id).first()
    assert product.cost == 20.0, "§8N: set_preferred_vendor must not move product.cost"
    prefs = [s for s in product.vendor_sources if s.is_preferred]
    assert len(prefs) == 1 and prefs[0].id == s2.id


# ── route layer ─────────────────────────────────────────────────────────────────

def test_add_source_route_persists_quote_not_product_cost(client, db_session):
    """The add-source route succeeds and persists the vendor quote on the source,
    but §8N keeps product.cost (moving avg) untouched. (The route's
    'product-cost-synced' HX-Trigger is a UI-Builder cleanup — see module docstring
    — and is deliberately not asserted.)"""
    vendor_id, product_id = _seed_product(db_session, cost=20.0)
    resp = client.post(
        f"/products/{product_id}/vendor-sources",
        data={"vendor_id": str(vendor_id), "vendor_cost": "10"},
    )
    assert resp.status_code == 200

    db_session.expire_all()  # cross-session: see the route's committed write
    product = db_session.query(Product).filter(Product.id == product_id).first()
    assert product.cost == 20.0, "§8N: the route must not move product.cost"

    source = (
        db_session.query(ProductVendorSource)
        .filter(ProductVendorSource.product_id == product_id)
        .first()
    )
    assert source is not None and source.vendor_cost == 10.0, "the quote lands on the source"
