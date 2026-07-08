"""
tests/test_warranty_labor.py
============================
Q6 — warranty repair labor (hours × rate) + serial/ESN capture, entered during
the warranty process and tracked separately from the parts credit.

Covers: capture at claim creation, the labor_amount/total_labor_amount math,
the update_service_info path (edit during the process), the CLOSED guard, the
POST /warranty/{id}/service-info route, and that print renders serial + labor.
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
    c = Customer(company_name=f"Lbr Cust {_uniq()}", contact_name="QA",
                 email=f"lbr{_uniq()}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"Lbr Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"LBR-{_uniq():04d}", title="Labor Part", description="Labor Part",
                cost=20.0, markup_pct=50.0, qty_on_hand=1,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _claim(db, *, lines):
    from app.services.warranty_service import WarrantyService
    cust = _customer(db); ven = _vendor(db)
    return WarrantyService(db, None).create_claim(
        customer_id=cust.id, invoice_id=None, vendor_id=ven.id,
        failure_description="unit failed", lines=lines,
    )


# ── capture at creation ────────────────────────────────────────────────────────

def test_labor_and_serial_captured_at_creation(db):
    prod = _product(db)
    claim = _claim(db, lines=[{
        "product_id": prod.id, "qty_claimed": 1, "credit_amount": 0.0,
        "serial_number": "ESN-12345", "labor_hours": 2.5, "labor_rate": 120.0,
    }])
    db.refresh(claim)
    line = claim.claim_lines[0]
    assert line.serial_number == "ESN-12345"
    assert line.labor_hours == 2.5
    assert line.labor_rate == 120.0
    # labor_amount = hours × rate; distinct from parts credit (0 here)
    assert line.labor_amount == 300.0
    assert claim.total_labor_amount == 300.0
    assert claim.total_credit_amount == 0.0  # labor is NOT folded into parts credit


def test_zero_labor_is_none_amount(db):
    prod = _product(db)
    claim = _claim(db, lines=[{"product_id": prod.id, "qty_claimed": 1}])
    db.refresh(claim)
    assert claim.claim_lines[0].labor_amount == 0.0
    assert claim.total_labor_amount == 0.0
    assert claim.claim_lines[0].serial_number is None


# ── edit during the process ────────────────────────────────────────────────────

def test_update_service_info_sets_labor_and_serial(db):
    from app.services.warranty_service import WarrantyService
    prod = _product(db)
    claim = _claim(db, lines=[{"product_id": prod.id, "qty_claimed": 1}])
    line_id = claim.claim_lines[0].id

    WarrantyService(db, None).update_service_info(claim.id, {
        line_id: {"serial_number": "SN-999", "labor_hours": 1.5, "labor_rate": 100.0},
    })
    db.refresh(claim)
    line = claim.claim_lines[0]
    assert line.serial_number == "SN-999"
    assert line.labor_amount == 150.0
    assert claim.total_labor_amount == 150.0


def test_update_service_info_blocked_when_closed(db):
    from app.constants import WarrantyStatus
    from app.services.warranty_service import WarrantyService
    prod = _product(db)
    claim = _claim(db, lines=[{"product_id": prod.id, "qty_claimed": 1}])
    line_id = claim.claim_lines[0].id
    # Force CLOSED directly (lifecycle transitions covered elsewhere)
    claim.status = WarrantyStatus.CLOSED
    db.commit()
    with pytest.raises(ValueError):
        WarrantyService(db, None).update_service_info(
            claim.id, {line_id: {"labor_hours": 5, "labor_rate": 50}}
        )


# ── route + print ──────────────────────────────────────────────────────────────

def test_service_info_route_updates_line(client, db):
    prod = _product(db)
    claim = _claim(db, lines=[{"product_id": prod.id, "qty_claimed": 1}])
    line_id = claim.claim_lines[0].id

    resp = client.post(
        f"/warranty/{claim.id}/service-info",
        data={
            "line_id[]": str(line_id),
            "serial_number[]": "ROUTE-SN-1",
            "labor_hours[]": "3",
            "labor_rate[]": "90",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.expire_all()
    from app.models.warranty import WarrantyClaim
    refetched = db.query(WarrantyClaim).filter(WarrantyClaim.id == claim.id).first()
    line = refetched.claim_lines[0]
    assert line.serial_number == "ROUTE-SN-1"
    assert line.labor_amount == 270.0


def test_print_renders_serial_and_labor(client, db):
    prod = _product(db)
    claim = _claim(db, lines=[{
        "product_id": prod.id, "qty_claimed": 1,
        "serial_number": "PRINT-SN-7", "labor_hours": 2, "labor_rate": 75.0,
    }])
    resp = client.get(f"/warranty/{claim.id}/print")
    assert resp.status_code == 200
    body = resp.text
    assert "PRINT-SN-7" in body            # serial column now real (was AttributeError)
    assert "Total Labor" in body           # labor surfaced on the printed doc
    assert "150.00" in body                # 2 × 75.00
