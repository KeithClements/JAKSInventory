"""
tests/test_reports_audit_followup.py
====================================
C (next-7-days) — the two reports the audit flagged as missing:
  * Inventory Movement History — the InventoryTransaction ledger (trace QOH changes)
  * QBO Unsynced Transactions  — invoices stuck PENDING / ERROR (dashboard chip drill-down)
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, timedelta

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


def _product(db, sku):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title=f"Part {sku}", description="x", cost=1.0,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _txn(db, product, qty_change, qty_after, *, ttype=None):
    from app.constants import InventoryTxnType
    from app.models.inventory import InventoryTransaction
    t = InventoryTransaction(
        product_id=product.id,
        transaction_type=ttype or InventoryTxnType.MANUAL_ADJUSTMENT,
        qty_change=qty_change,
        qty_after=qty_after,
        performed_by_id=None,
        notes="test movement",
    )
    db.add(t); db.commit(); db.refresh(t)
    return t


def _customer(db):
    from app.models.customer import Customer
    n = _uniq()
    c = Customer(company_name=f"Rpt Co {n}", contact_name="QA", email=f"rpt{n}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _invoice(db, customer, *, qbo_status, status="open", total=100.0, error=""):
    from app.constants import InvoiceStatus, LineType, QBOSyncStatus
    from app.models.invoice import Invoice, InvoiceLine
    inv = Invoice(
        invoice_number=f"INV-RPT-{_uniq():04d}",
        customer_id=customer.id,
        status=status,
        is_taxable=False,
        qbo_sync_status=qbo_status,
        qbo_sync_error=error,
    )
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT,
                       description="part", qty=1, unit_price=total, unit_cost=0.0))
    db.commit(); db.refresh(inv)
    return inv


# ── Inventory Movement ──────────────────────────────────────────────────────────

def test_inventory_movement_lists_ledger_and_filters_by_sku(db):
    from app.services.report_service import ReportService
    pa = _product(db, f"MOVE-A-{_uniq()}")
    pb = _product(db, f"MOVE-B-{_uniq()}")
    _txn(db, pa, +5, 5)
    _txn(db, pa, -2, 3)
    _txn(db, pb, +10, 10)

    data = ReportService(db).get_inventory_movement(sku_query=pa.sku)
    skus = {r["sku"] for r in data["rows"]}
    assert skus == {pa.sku}                      # SKU filter scopes to one product
    assert data["totals"]["total_matched"] == 2  # both A movements
    # newest first
    assert data["rows"][0]["qty_after"] == 3


def test_inventory_movement_truncates_with_flag(db):
    from app.services.report_service import ReportService
    p = _product(db, f"MOVE-TRUNC-{_uniq()}")
    for i in range(5):
        _txn(db, p, +1, i + 1)
    data = ReportService(db).get_inventory_movement(sku_query=p.sku, limit=3)
    assert data["totals"]["row_count"] == 3
    assert data["totals"]["total_matched"] == 5
    assert data["totals"]["truncated"] is True


def test_inventory_movement_route_200(client):
    assert client.get("/reports/inventory-movement").status_code == 200
    assert client.get("/reports/inventory-movement?q=MOVE&start=2020-01-01&end=2030-01-01").status_code == 200


# ── QBO Unsynced ────────────────────────────────────────────────────────────────

def test_qbo_unsynced_lists_pending_and_error_not_synced_or_draft(db):
    from app.constants import QBOSyncStatus
    from app.services.report_service import ReportService
    cust = _customer(db)
    pend = _invoice(db, cust, qbo_status=QBOSyncStatus.PENDING, total=200.0)
    err = _invoice(db, cust, qbo_status=QBOSyncStatus.ERROR, total=300.0, error="401 token expired")
    _invoice(db, cust, qbo_status=QBOSyncStatus.SYNCED, total=999.0)            # excluded
    _invoice(db, cust, qbo_status=QBOSyncStatus.PENDING, status="draft", total=50.0)  # excluded (draft)

    data = ReportService(db).get_qbo_unsynced()
    nums = [r["invoice_number"] for r in data["rows"]]
    assert pend.invoice_number in nums
    assert err.invoice_number in nums
    assert data["totals"]["count"] == 2
    assert data["totals"]["error_count"] == 1
    assert data["totals"]["pending_count"] == 1
    # ERROR sorts first (needs action)
    assert data["rows"][0]["status"] == QBOSyncStatus.ERROR
    assert data["rows"][0]["error"] == "401 token expired"


def test_qbo_unsynced_route_200(client):
    assert client.get("/reports/qbo-unsynced").status_code == 200
