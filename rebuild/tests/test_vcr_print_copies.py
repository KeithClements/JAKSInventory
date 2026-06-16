"""
tests/test_vcr_print_copies.py
==============================
Vendor Core Return print supports multiple copies in one job:
  /cores/vcr/{id}/print?copies=both → a Vendor Copy + an Office Copy (two pages,
  page-break between). The default print is unchanged (single vendor copy, no
  copy label). PO-independent — exercises only the cores print route.

Fixture pattern mirrors tests/test_r2_vcr_batch.py.
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


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _ready_vcr(db):
    """A vendor + one accepted/returned core batched into a DRAFT VCR."""
    from app.constants import (ProductStatus, CoreDirection, CoreStatus,
                               CoreInspectionOutcome)
    from app.models.customer import Customer
    from app.models.vendor import Vendor
    from app.models.product import Product
    from app.models.core import CoreCharge
    from app.services.core_service import CoreService

    cust = Customer(company_name=f"Copies Cust {_uniq()}", email="copies@test.local")
    ven = Vendor(name=f"Copies Vendor {_uniq()}", is_active=True, account_number="JAK-1")
    prod = Product(sku=f"VCRCOPY-{_uniq():04d}", title="Reman Turbo", description="x",
                   cost=300, markup_pct=40, qty_on_hand=0,
                   status=ProductStatus.ACTIVE, is_active=True)
    db.add_all([cust, ven, prod]); db.commit()
    core = CoreCharge(product_id=prod.id, customer_id=cust.id, vendor_id=ven.id,
                      qty_charged=1, qty_returned=0, customer_unit_charge=195,
                      vendor_unit_charge=150, direction=CoreDirection.CUSTOMER_OWES_RETURN,
                      status=CoreStatus.OPEN)
    db.add(core); db.commit(); db.refresh(core)
    CoreService(db, None).record_customer_return(
        core.id, qty_returned=1, inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    return CoreService(db, None).create_vcr(ven.id, [core.id])


def test_vcr_print_both_copies(client, db):
    vcr = _ready_vcr(db)
    both = client.get(f"/cores/vcr/{vcr.id}/print?copies=both")
    assert both.status_code == 200
    assert "Vendor Copy" in both.text
    assert "Office Copy" in both.text
    # Two document bodies → the VCR title/heading appears at least twice
    assert both.text.count("Vendor Core Return") >= 2


def test_vcr_print_default_is_single_vendor_copy(client, db):
    vcr = _ready_vcr(db)
    single = client.get(f"/cores/vcr/{vcr.id}/print")
    assert single.status_code == 200
    # Unchanged default: no copy labels rendered
    assert "Office Copy" not in single.text
    assert "Vendor Copy" not in single.text
