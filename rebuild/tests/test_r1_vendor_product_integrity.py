"""
tests/test_r1_vendor_product_integrity.py
=========================================
Fix bundle R1-7a + R1-8:
  - R1-7a: vendor quick-create must truncate vendor_code to 4 chars
    (matches full create/update; internal vendor_sku contract is
    JAKS-[VENDOR_CODE]-[PART#] and the column is String(4)).
  - R1-8: soft-deleting a preferred vendor source must clear is_preferred,
    and Product.preferred_vendor_source must never return an inactive row
    (ghost preferred vendor in search results / preview dock / CSV export).

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_r1_vendor_product_integrity.py -q
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401 — registers every model on Base

import pytest
from fastapi.testclient import TestClient

from app.constants import UserRole
from app.models.product import Product, ProductVendorSource
from app.models.user import User
from app.models.vendor import Vendor

from app.main import app as _fastapi_app

_client = TestClient(_fastapi_app, raise_server_exceptions=False)

_counter = itertools.count(1)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == 1).first():
        s.add(User(id=1, name="Test Admin", username="admin_r1",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


# ── Data builders ─────────────────────────────────────────────────────────────

def _make_vendor(db, code: str = "PAI") -> Vendor:
    n = next(_counter)
    v = Vendor(name=f"Vendor-R1-{n}", vendor_code=code)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _make_product(db) -> Product:
    n = next(_counter)
    p = Product(sku=f"R1-{n:04d}", title="R1 Part", description="R1 Part",
                cost=50.0, markup_pct=40.0, qty_on_hand=3, is_active=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_source(db, product: Product, vendor: Vendor, *,
                 preferred: bool = False, active: bool = True,
                 cost: float = 42.0) -> ProductVendorSource:
    s = ProductVendorSource(
        product_id=product.id,
        vendor_id=vendor.id,
        vendor_part_number=f"VP-{next(_counter)}",
        vendor_cost=cost,
        is_preferred=preferred,
        is_active=active,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ── R1-7a: quick-create vendor_code truncation ────────────────────────────────

def test_quick_create_truncates_vendor_code_to_4_chars(db):
    resp = _client.post(
        "/vendors/quick-create",
        data={"name": "Longcode Diesel Supply", "vendor_code": "longcode10"},
    )
    assert resp.status_code == 200, resp.text
    v = (
        db.query(Vendor)
        .filter(Vendor.name == "Longcode Diesel Supply")
        .first()
    )
    assert v is not None
    assert v.vendor_code == "LONG"  # uppercased + truncated, matches full create
    assert len(v.vendor_code) <= 4


def test_quick_create_short_code_unchanged(db):
    resp = _client.post(
        "/vendors/quick-create",
        data={"name": "Short Code Vendor", "vendor_code": "hhp"},
    )
    assert resp.status_code == 200, resp.text
    v = db.query(Vendor).filter(Vendor.name == "Short Code Vendor").first()
    assert v is not None
    assert v.vendor_code == "HHP"


# ── R1-8: ghost preferred vendor source ───────────────────────────────────────

def test_remove_preferred_source_clears_preferred_flag(db):
    product = _make_product(db)
    vendor = _make_vendor(db)
    source = _make_source(db, product, vendor, preferred=True)

    resp = _client.delete(f"/products/{product.id}/vendor-sources/{source.id}")
    assert resp.status_code == 200

    db.expire_all()
    row = db.query(ProductVendorSource).filter(ProductVendorSource.id == source.id).first()
    assert row.is_active is False
    assert row.is_preferred is False  # no ghost preferred on a soft-deleted row

    refreshed = db.query(Product).filter(Product.id == product.id).first()
    assert refreshed.preferred_vendor_source is None


def test_remove_preferred_with_other_active_source_returns_none(db):
    # Removing the preferred source does NOT auto-promote the other one;
    # the product simply has no preferred source until one is set.
    product = _make_product(db)
    v1 = _make_vendor(db, code="AAA")
    v2 = _make_vendor(db, code="BBB")
    preferred = _make_source(db, product, v1, preferred=True)
    other = _make_source(db, product, v2, preferred=False)

    resp = _client.delete(f"/products/{product.id}/vendor-sources/{preferred.id}")
    assert resp.status_code == 200

    db.expire_all()
    refreshed = db.query(Product).filter(Product.id == product.id).first()
    assert refreshed.preferred_vendor_source is None
    # The other source is untouched.
    row = db.query(ProductVendorSource).filter(ProductVendorSource.id == other.id).first()
    assert row.is_active is True
    assert row.is_preferred is False


def test_preferred_property_skips_inactive_rows(db):
    # Legacy data shape: a soft-deleted row that still carries is_preferred=True
    # (pre-fix rows) must never surface; an active preferred row wins.
    product = _make_product(db)
    v1 = _make_vendor(db, code="OLD")
    v2 = _make_vendor(db, code="NEW")
    _make_source(db, product, v1, preferred=True, active=False)
    active_pref = _make_source(db, product, v2, preferred=True, active=True)

    refreshed = db.query(Product).filter(Product.id == product.id).first()
    result = refreshed.preferred_vendor_source
    assert result is not None
    assert result.id == active_pref.id
    assert result.is_active is True


def test_preferred_property_returns_none_when_only_inactive_preferred(db):
    product = _make_product(db)
    vendor = _make_vendor(db)
    _make_source(db, product, vendor, preferred=True, active=False)

    refreshed = db.query(Product).filter(Product.id == product.id).first()
    assert refreshed.preferred_vendor_source is None
