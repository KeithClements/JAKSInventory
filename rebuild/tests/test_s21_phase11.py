"""
tests/test_s21_phase11.py
=========================
§21 Phase-1.1 batch — locks in the follow-up fixes after the immediate sprint:

  - CoreCharge.vendor_id stamped from the product's preferred vendor at creation
  - resync_qty_committed / resync_qty_on_order recovery paths + admin routes
  - the 6 previously-disabled report CSV exports now stream text/csv
  - dashboard QBO chip reflects real connection state (not a hardcoded "ready")

(The case-insensitive Brand/Manufacturer rename cascade is covered in
tests/test_r1_category_maintenance.py.)
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.constants import InvoiceStatus, LineType, ProductStatus
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product, ProductVendorSource
from app.models.core import CoreCharge
from app.models.vendor import Vendor
from app.services.core_service import CoreService
from app.services.invoice_service import InvoiceService
from app.services.product_service import ProductService

_ENGINE = fresh_engine()
_UID = 1
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


def _product(db, **kw) -> Product:
    p = Product(sku=f"P11-{next(_seq):04d}", title="P11 Part", description="P11",
                cost=20.0, markup_pct=50.0, status=ProductStatus.ACTIVE, is_active=True, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── CoreCharge.vendor_id stamped at creation ──────────────────────────────────

def test_core_charge_vendor_id_stamped_from_preferred_source(db):
    vendor = Vendor(name=f"CoreVend-{next(_seq)}")
    db.add(vendor); db.commit(); db.refresh(vendor)
    prod = _product(db)
    db.add(ProductVendorSource(product_id=prod.id, vendor_id=vendor.id,
                               vendor_part_number="VP1", vendor_cost=10.0,
                               is_preferred=True, is_active=True))
    cust = Customer(company_name=f"CoreCust-{next(_seq)}")
    db.add(cust); db.commit(); db.refresh(cust)
    inv = Invoice(invoice_number=f"INV-P11-{next(_seq):05d}", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT)
    db.add(inv); db.commit()

    core = CoreService(db, _UID).create_core_charge(
        invoice_id=inv.id, invoice_line_id=None, product_id=prod.id,
        qty=1, customer_unit_charge=50.0, vendor_unit_charge=30.0)
    db.commit()
    assert core.vendor_id == vendor.id   # was NULL until VCR batch time before §21


# ── qty_committed / qty_on_order resync ───────────────────────────────────────

def test_resync_qty_committed_corrects_drift(db):
    prod = _product(db)
    prod.qty_committed = 7          # cache drift; no commitment ledger rows exist
    db.commit()
    old, new = ProductService(db, _UID).resync_qty_committed(prod.id)
    db.commit()
    assert old == 7 and new == 0    # ledger truth is 0
    assert db.query(Product).get(prod.id).qty_committed == 0


def test_resync_qty_on_order_corrects_drift(db):
    prod = _product(db)
    prod.qty_on_order = 9           # cache drift; no open POs exist
    db.commit()
    old, new = ProductService(db, _UID).resync_qty_on_order(prod.id)
    db.commit()
    assert old == 9 and new == 0
    assert db.query(Product).get(prod.id).qty_on_order == 0


def test_resync_committed_admin_route(client, db):
    prod = _product(db)
    prod.qty_committed = 4
    db.commit()
    r = client.post(f"/admin/inventory/resync-committed/{prod.id}")
    assert r.status_code == 200
    assert r.json()["new_qty_committed"] == 0


# ── The 6 report CSV exports stream text/csv ──────────────────────────────────

@pytest.mark.parametrize("path", [
    "/reports/sales-by-customer/export.csv",
    "/reports/sales-by-product/export.csv",
    "/reports/inventory-valuation/export.csv",
    "/reports/open-pos/export.csv",
    "/reports/outstanding-cores/export.csv",
    "/reports/lost-sales/export.csv",
])
def test_report_csv_exports(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text[:200]
    assert "text/csv" in r.headers.get("content-type", "")
    assert "attachment" in r.headers.get("content-disposition", "")


# ── Dashboard QBO chip reflects real state ────────────────────────────────────

def test_dashboard_qbo_chip_not_hardcoded_ready(client):
    r = client.get("/")
    assert r.status_code == 200
    # With no QBO connection seeded, the chip says "not connected", never "ready".
    assert "not connected" in r.text
    assert "<b>QuickBooks</b> ready" not in r.text
