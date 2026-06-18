"""
tests/test_r4_vendor_digit.py
=============================
R4 owner workflow — vendor setup converts the FEED SKU CODE (PAI / IMB) to the
1-digit SKU vendor number automatically:

  • blank SKU # on create/quick-create → auto-assigns the next free digit
  • explicit digit → must be a single 0-9, unique across vendors
  • blank on update → keeps the current digit (never silently clears)
  • FROZEN once any product has been minted under it (part_seq stamped on a
    product carrying this vendor's source) — the form goes read-only and the
    route ignores submitted changes.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as appdb
from app.main import app
from app.models.product import Product
from app.models.vendor import Vendor
from app.services.product_import_service import ProductImportService
from app.services.sku_service import next_free_vendor_digit, vendor_digit_locked
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


_SEQ = {"n": 0}


def _mk_vendor(db, *, name=None, code=None, digit=""):
    _SEQ["n"] += 1
    v = Vendor(name=(name or f"Vendor {_SEQ['n']}"),
               vendor_code=(code or f"V{_SEQ['n']:02d}"),
               vendor_number=digit, is_active=True)
    db.add(v); db.commit(); db.refresh(v)
    return v


# ── next_free_vendor_digit ────────────────────────────────────────────────────

def test_next_free_skips_taken_digits(db):
    start = next_free_vendor_digit(db)   # whatever the module DB has so far
    v = _mk_vendor(db, digit=start)
    after = next_free_vendor_digit(db)
    assert after != start and after != ""
    db.delete(v); db.commit()


# ── create: auto-assign + validation ─────────────────────────────────────────

def test_create_blank_digit_auto_assigns(client, db):
    expected = next_free_vendor_digit(db)
    r = client.post("/vendors/new", data={
        "name": "Auto Digit Co", "vendor_code": "ADC", "vendor_number": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    v = db.query(Vendor).filter(Vendor.vendor_code == "ADC").first()
    assert v is not None
    assert v.vendor_number == expected   # the feed code converted to a digit


def test_create_duplicate_digit_rejected(client, db):
    taken = _mk_vendor(db, name="Holder Co", code="HLD", digit="7")
    r = client.post("/vendors/new", data={
        "name": "Clasher Co", "vendor_code": "CLS", "vendor_number": "7",
    }, follow_redirects=False)
    assert r.status_code == 422
    assert "already assigned" in r.text and "Holder Co" in r.text
    assert db.query(Vendor).filter(Vendor.vendor_code == "CLS").first() is None
    # form repopulates so the owner doesn't retype everything
    assert "Clasher Co" in r.text
    db.delete(taken); db.commit()


def test_create_invalid_digit_rejected(client, db):
    r = client.post("/vendors/new", data={
        "name": "Bad Digit Co", "vendor_code": "BDC", "vendor_number": "12",
    }, follow_redirects=False)
    assert r.status_code == 422
    assert "single digit" in r.text
    assert db.query(Vendor).filter(Vendor.vendor_code == "BDC").first() is None


def test_quick_create_auto_assigns_digit(client, db):
    expected = next_free_vendor_digit(db)
    r = client.post("/vendors/quick-create", data={
        "name": "Quick Co", "vendor_code": "QCK",
    })
    assert r.status_code == 200
    v = db.query(Vendor).filter(Vendor.vendor_code == "QCK").first()
    assert v is not None and v.vendor_number == expected


# ── update: keep / change / clash / freeze ────────────────────────────────────

def _update(client, v, digit):
    return client.post(f"/vendors/{v.id}", data={
        "name": v.name, "vendor_code": v.vendor_code, "vendor_number": digit,
        "account_number": "", "contact_name": "", "phone": "", "email": "",
        "website": "", "payment_terms": "net_30", "notes": "", "internal_notes": "",
    }, follow_redirects=False)


def test_update_blank_keeps_current_digit(client, db):
    v = _mk_vendor(db, code="KPD", digit="6")
    r = _update(client, v, "")
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    db.expire_all()
    assert db.get(Vendor, v.id).vendor_number == "6", \
        "blank submit must never silently clear the digit"
    db.delete(db.get(Vendor, v.id)); db.commit()


def test_update_change_digit_while_unlocked(client, db):
    v = _mk_vendor(db, code="CHG", digit="6")
    r = _update(client, v, "8")
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    db.expire_all()
    assert db.get(Vendor, v.id).vendor_number == "8"
    db.delete(db.get(Vendor, v.id)); db.commit()


def test_update_clash_redirects_with_error_and_saves_nothing(client, db):
    holder = _mk_vendor(db, name="Digit Holder", code="DH8", digit="8")
    v = _mk_vendor(db, code="WNT", digit="6")
    r = _update(client, v, "8")
    assert r.status_code == 303 and "error=" in r.headers["location"]
    db.expire_all()
    assert db.get(Vendor, v.id).vendor_number == "6"   # unchanged
    db.delete(db.get(Vendor, v.id)); db.delete(db.get(Vendor, holder.id)); db.commit()


# ── freeze after first mint ───────────────────────────────────────────────────

_HEADER = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Variant SKU", "Variant Grams",
    "Variant Inventory Tracker", "Variant Inventory Qty", "Variant Inventory Policy",
    "Variant Fulfillment Service", "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Status",
]


def _feed_csv(sku):
    d = {h: "" for h in _HEADER}
    d.update({"Handle": sku.lower(), "Title": "Head Bolt", "Type": "ENGINE PARTS",
              "Tags": "CUMMINS", "Variant SKU": sku, "Variant Price": "10.99",
              "Status": "active"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    w.writerow([d[h] for h in _HEADER])
    return buf.getvalue()


def test_digit_frozen_after_first_mint(client, db):
    """The vendor digit FREEZES once a product is opaque-minted under it (part_seq
    stamped). NOTE: full_import no longer opaque-mints — since the §20 revert the
    customer SKU is the vendor's real part #, so import sets sku=part# and never
    stamps part_seq. The freeze guard (and its vendor-form behaviour) is still
    shipped for the dormant opaque-SKU scheme, so it is exercised here via the
    SkuService mint path that actually stamps part_seq."""
    from app.constants import ProductStatus
    from app.models.product import ProductVendorSource
    from app.services.sku_service import SkuService

    v = _mk_vendor(db, name="Frozen Co", code="FRZ", digit="5")
    p = Product(sku="FRZ-RAW-1", title="Head Bolt", description="x",
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.flush()
    db.add(ProductVendorSource(product_id=p.id, vendor_id=v.id,
                               vendor_part_number="FRZ1", is_preferred=True, is_active=True))
    db.commit()
    SkuService(db, None).assign_new_sku(p, v)   # stamps part_seq → freezes the digit
    db.commit()
    assert vendor_digit_locked(db, v.id) is True

    r = _update(client, v, "4")
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    db.expire_all()
    assert db.get(Vendor, v.id).vendor_number == "5", \
        "digit is FROZEN once a product is minted under it"

    # the detail page shows the frozen state instead of an editable field
    page = client.get(f"/vendors/{v.id}").text
    assert "Frozen" in page

    # cleanup so later tests' digit expectations stay deterministic
    p = db.query(Product).filter(Product.part_seq.isnot(None)).first()
    # (module-scoped engine: leave rows; no other test asserts global counts)


def test_unlocked_vendor_shows_editable_field(client, db):
    v = _mk_vendor(db, name="Editable Co", code="EDT", digit="2")
    page = client.get(f"/vendors/{v.id}").text
    assert "Frozen" not in page
    assert "auto-assign the next free digit" in page
    db.delete(db.get(Vendor, v.id)); db.commit()
