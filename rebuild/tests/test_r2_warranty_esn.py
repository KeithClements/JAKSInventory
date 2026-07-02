"""
tests/test_r2_warranty_esn.py
=============================
R2 — warranty claims carry the data PAI / Interstate-McBee require.

  (1) WarrantyClaim.esn — engine serial number persists create → detail and is
      covered by an append-only inline migration (legacy DBs pick it up).
  (2) warranty_type select in the new-claim picker binds through POST
      /warranty/new (JAKS_EXTENDED no longer silently impossible).
  (3) POST /warranty/{id}/vendor-credit records what the vendor actually paid
      via WarrantyService.record_vendor_credit (VendorCredit, type=WARRANTY).
  (4) submit_to_vendor guard: a vendor-type claim with no vendor assigned is
      rejected with a friendly error redirect (JAKS_EXTENDED claims, which have
      no external vendor by definition, still flow).

Fixture pattern mirrors tests/test_warranty.py: module-isolated in-memory
engine (conftest fresh_engine/activate) + TestClient startup. The migration
test mirrors tests/test_schema_drift.py's minimal-legacy-schema roundtrip.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


_SEQ = {"n": 0}


def _uniq():
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"R2 Cust {_uniq()}", contact_name="QA",
                 email=f"r2wty{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"R2 Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"R2W-{_uniq():04d}", title="R2 Part", description="R2 Part",
                cost=20.0, markup_pct=50.0, qty_on_hand=1,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _post_new(client, *, customer, product, vendor=None, **extra):
    """POST the new-claim form the way _new_picker.html submits it."""
    data = {
        "customer_id": str(customer.id),
        "failure_description": "injector failed under load",
        "product_id[]": [str(product.id)],
        "qty_claimed[]": ["1"],
        "credit_amount[]": ["50.00"],
    }
    if vendor is not None:
        data["vendor_id"] = str(vendor.id)
    data.update(extra)
    return client.post("/warranty/new", data=data, follow_redirects=False)


def _claim_id_from(resp):
    """Create redirects to /warranty/{id} on success."""
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "/warranty/new" not in loc, f"create bounced back to picker: {loc}"
    return int(loc.rstrip("/").split("/")[-1])


def _service_claim(db, *, customer, product, vendor=None, **kwargs):
    from app.services.warranty_service import WarrantyService
    # §23.3 Phase 3 — warranty_require_esn defaults ON; give vendor claims an
    # ESN unless a test explicitly overrides it.
    kwargs.setdefault("esn", "79R2-ESN-TEST")
    return WarrantyService(db, None).create_claim(
        customer_id=customer.id, invoice_id=None,
        vendor_id=vendor.id if vendor else None,
        failure_description="unit failed",
        lines=[{"product_id": product.id, "qty_claimed": 1, "credit_amount": 0.0}],
        **kwargs,
    )


def _approve(db, claim, credit=100.0):
    from app.constants import WarrantyDecision, WarrantyResolution
    from app.services.warranty_service import WarrantyService
    svc = WarrantyService(db, None)
    svc.submit_to_vendor(claim.id)
    svc.record_vendor_decision(
        claim.id, decision=WarrantyDecision.APPROVED,
        line_resolutions=[{"claim_line_id": claim.claim_lines[0].id,
                           "approved_qty": 1, "credit_amount": credit,
                           "resolution": WarrantyResolution.CREDIT}])


# ===========================================================================
# (1) ESN — persists create → detail
# ===========================================================================

def test_esn_persists_create_to_detail(client, db):
    from app.models.warranty import WarrantyClaim

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    resp = _post_new(client, customer=cust, product=prod, vendor=ven,
                     esn="79R2-431882")
    claim_id = _claim_id_from(resp)

    claim = db.get(WarrantyClaim, claim_id)
    assert claim is not None
    assert claim.esn == "79R2-431882"

    detail = client.get(f"/warranty/{claim_id}")
    assert detail.status_code == 200
    assert "79R2-431882" in detail.text  # workspace header shows the ESN


def test_esn_defaults_empty_when_omitted(client, db):
    from app.models.warranty import WarrantyClaim

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    resp = _post_new(client, customer=cust, product=prod, vendor=ven)
    claim = db.get(WarrantyClaim, _claim_id_from(resp))
    assert claim.esn == ""


def test_new_picker_renders_esn_and_type_inputs(client, db):
    """The slide-over picker (HTMX partial) offers the ESN input and the
    warranty-type select with real enum values (not silent Undefined-blanks)."""
    resp = client.get("/warranty/new", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert 'name="esn"' in resp.text
    assert 'name="warranty_type"' in resp.text
    assert 'value="vendor"' in resp.text
    assert 'value="jaks_extended"' in resp.text


# ===========================================================================
# (2) warranty_type select binds through the create route
# ===========================================================================

def test_warranty_type_select_persists_jaks_extended(client, db):
    from app.constants import WarrantyType
    from app.models.warranty import WarrantyClaim

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    resp = _post_new(client, customer=cust, product=prod, vendor=ven,
                     warranty_type="jaks_extended")
    claim = db.get(WarrantyClaim, _claim_id_from(resp))
    assert claim.warranty_type == WarrantyType.JAKS_EXTENDED


def test_warranty_type_defaults_vendor_when_absent(client, db):
    from app.constants import WarrantyType
    from app.models.warranty import WarrantyClaim

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    resp = _post_new(client, customer=cust, product=prod, vendor=ven)
    claim = db.get(WarrantyClaim, _claim_id_from(resp))
    assert claim.warranty_type == WarrantyType.VENDOR

    # Garbage value falls back to VENDOR too (service re-validates)
    resp2 = _post_new(client, customer=cust, product=prod, vendor=ven,
                      warranty_type="bogus_type")
    claim2 = db.get(WarrantyClaim, _claim_id_from(resp2))
    assert claim2.warranty_type == WarrantyType.VENDOR


# ===========================================================================
# (3) Vendor credit — POST /warranty/{id}/vendor-credit
# ===========================================================================

def test_vendor_credit_route_records_vendor_credit(client, db):
    from app.constants import VendorCreditStatus, VendorCreditType
    from app.models.vendor import VendorCredit

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    claim = _service_claim(db, customer=cust, product=prod, vendor=ven)
    _approve(db, claim, credit=100.0)

    resp = client.post(
        f"/warranty/{claim.id}/vendor-credit",
        data={"credit_amount": "85.50", "reference": "PAI-CM-1234"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]

    vc = db.query(VendorCredit).filter(VendorCredit.vendor_id == ven.id).first()
    assert vc is not None
    assert vc.credit_type == VendorCreditType.WARRANTY
    assert vc.amount == 85.50
    assert vc.status == VendorCreditStatus.OPEN
    assert claim.claim_number in vc.notes
    assert "PAI-CM-1234" in vc.notes


def test_vendor_credit_requires_vendor_on_claim(client, db):
    from app.models.vendor import VendorCredit

    cust = _customer(db); prod = _product(db)
    claim = _service_claim(db, customer=cust, product=prod, vendor=None)

    before = db.query(VendorCredit).count()
    resp = client.post(
        f"/warranty/{claim.id}/vendor-credit",
        data={"credit_amount": "50.00"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert db.query(VendorCredit).count() == before


def test_vendor_credit_rejects_nonpositive_amount(client, db):
    from app.models.vendor import VendorCredit

    cust = _customer(db); ven = _vendor(db); prod = _product(db)
    claim = _service_claim(db, customer=cust, product=prod, vendor=ven)

    before = db.query(VendorCredit).count()
    resp = client.post(
        f"/warranty/{claim.id}/vendor-credit",
        data={"credit_amount": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert db.query(VendorCredit).count() == before


# ===========================================================================
# (4) Submit guard — no vendor assigned
# ===========================================================================

def test_submit_without_vendor_rejected_via_route(client, db):
    from app.constants import WarrantyStatus

    cust = _customer(db); prod = _product(db)
    claim = _service_claim(db, customer=cust, product=prod, vendor=None)

    resp = client.post(f"/warranty/{claim.id}/submit", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    db.refresh(claim)
    assert claim.status == WarrantyStatus.DRAFT  # transition blocked


def test_jaks_extended_claim_submits_without_vendor(client, db):
    """JAKS-extended claims have no external vendor by definition — the
    no-vendor guard must not strand them in DRAFT."""
    from app.constants import WarrantyStatus, WarrantyType
    from app.services.warranty_service import WarrantyService

    cust = _customer(db); prod = _product(db)
    claim = _service_claim(db, customer=cust, product=prod, vendor=None,
                           warranty_type=WarrantyType.JAKS_EXTENDED)
    WarrantyService(db, None).submit_to_vendor(claim.id)
    db.refresh(claim)
    assert claim.status == WarrantyStatus.SUBMITTED_TO_VENDOR


# ===========================================================================
# (1b) Inline migration — legacy DB gains the esn column
# ===========================================================================

def test_migration_adds_esn_on_legacy_engine():
    """Mimics tests/test_schema_drift.py: a pre-R2 warranty_claims table
    (no esn column) gains it when _PENDING_COLUMN_ADDITIONS is applied."""
    from sqlalchemy import Column, Integer, MetaData, String, Table, inspect, text

    from app.database import _PENDING_COLUMN_ADDITIONS
    from tests.conftest import bare_engine

    legacy_meta = MetaData()
    Table(
        "warranty_claims", legacy_meta,
        Column("id", Integer, primary_key=True),
        Column("claim_number", String(50)),
    )
    engine = bare_engine()
    legacy_meta.create_all(engine)

    assert ("warranty_claims", "esn") in {
        (t, c) for t, c, _ in _PENDING_COLUMN_ADDITIONS
    }, "esn migration entry missing from _PENDING_COLUMN_ADDITIONS"

    # Apply migrations (mirror of _apply_inline_migrations logic)
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, sql_def in _PENDING_COLUMN_ADDITIONS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                conn.execute(
                    text(f'ALTER TABLE "{table}" ADD COLUMN {column} {sql_def}')
                )

    cols_after = {c["name"] for c in inspect(engine).get_columns("warranty_claims")}
    assert "esn" in cols_after
    assert "warranty_type" in cols_after  # earlier entry still applies on the same pass
