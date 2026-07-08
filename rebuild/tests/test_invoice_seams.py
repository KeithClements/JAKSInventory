"""
tests/test_invoice_seams.py
===========================
Phase-1B/2 invoice seams:
  1. QBO bulk push  (POST /qbo/invoices/push-batch + unsynced_invoice_ids)
  2. QBO filter tabs (not_synced / sync_failed / modified_since_sync)
  3. Search expansion (product SKU / cross-ref / customer phone / serial)
  4. Invoice intelligence metrics (open_ar, last_purchase, outstanding_cores,
     open_warranty_claims) + the workspace descriptor list.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    InvoiceStatus, LineType, QBOSyncStatus, CrossRefType,
    CoreDirection, CoreStatus, WarrantyStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product, CrossReference, ProductSerialNumber
from app.models.core import CoreCharge
from app.models.warranty import WarrantyClaim
from app.services.qbo_service import QBOSyncService
from app.services.invoice_metrics_service import InvoiceMetricsService, INVOICE_METRIC_KEYS
from app.routers.invoices import _build_invoice_panel_metrics
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)
_YR = datetime.utcnow().year


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _cust(db, **kw):
    c = Customer(company_name=f"Seam Co {next(_seq)}", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _prod(db, sku):
    p = Product(sku=sku, title="part", cost=10.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _inv(db, cust, *, status, amount=100.0, created_at=None, **qbo):
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=cust.id,
                  status=status, is_taxable=False,
                  created_at=created_at or datetime(_YR, 6, 1))
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 is_taxable=False))
    for k, v in qbo.items():
        setattr(inv, k, v)
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


# ── Seam 1: QBO bulk push ──────────────────────────────────────────────────────

def test_unsynced_invoice_ids(db):
    c = _cust(db)
    open_unsynced = _inv(db, c, status=InvoiceStatus.OPEN)            # finalized, pending
    _inv(db, c, status=InvoiceStatus.DRAFT)                           # draft → excluded
    synced = _inv(db, c, status=InvoiceStatus.PAID,
                  qbo_sync_status=QBOSyncStatus.SYNCED, qbo_invoice_id="QB1")  # excluded
    ids = QBOSyncService(db).unsynced_invoice_ids()
    assert open_unsynced.id in ids
    assert synced.id not in ids


def test_push_batch_skips_already_synced(client, db):
    c = _cust(db)
    a = _inv(db, c, status=InvoiceStatus.OPEN, qbo_invoice_id="QBA")
    b = _inv(db, c, status=InvoiceStatus.PAID, qbo_invoice_id="QBB")
    r = client.post("/qbo/invoices/push-batch", json={"invoice_ids": [a.id, b.id]})
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    by_id = {e["id"]: e for e in data["results"]}
    # already-synced → ok True with skipped marker (push_invoice short-circuits)
    assert by_id[a.id]["ok"] is True and by_id[a.id].get("skipped")
    assert by_id[b.id]["ok"] is True


def test_push_batch_all_unsynced_mode(client, db):
    c = _cust(db)
    u1 = _inv(db, c, status=InvoiceStatus.OPEN)
    u2 = _inv(db, c, status=InvoiceStatus.PARTIAL)
    r = client.post("/qbo/invoices/push-batch", json={"mode": "all_unsynced"})
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["results"]}
    assert {u1.id, u2.id} <= ids       # both unsynced finalized invoices attempted


def test_push_batch_accepts_form(client, db):
    c = _cust(db)
    a = _inv(db, c, status=InvoiceStatus.OPEN, qbo_invoice_id="QBF")
    r = client.post("/qbo/invoices/push-batch", data={"invoice_ids": str(a.id)})
    assert r.status_code == 200
    assert r.json()["results"][0]["id"] == a.id


# ── Seam 2: QBO filter tabs ────────────────────────────────────────────────────

def test_qbo_tab_not_synced(client, db):
    c = _cust(db)
    unsynced = _inv(db, c, status=InvoiceStatus.OPEN)                 # finalized, no qbo id
    synced = _inv(db, c, status=InvoiceStatus.OPEN, qbo_invoice_id="QBX")
    html = client.get("/invoices/?tab=not_synced").text
    assert unsynced.invoice_number in html
    assert synced.invoice_number not in html


def test_qbo_tab_sync_failed(client, db):
    c = _cust(db)
    failed = _inv(db, c, status=InvoiceStatus.OPEN, qbo_sync_status=QBOSyncStatus.ERROR)
    ok = _inv(db, c, status=InvoiceStatus.OPEN, qbo_sync_status=QBOSyncStatus.PENDING)
    html = client.get("/invoices/?tab=sync_failed").text
    assert failed.invoice_number in html
    assert ok.invoice_number not in html


def test_qbo_tab_modified_since_sync(client, db):
    c = _cust(db)
    synced_at = datetime(_YR, 6, 1)
    modified = _inv(db, c, status=InvoiceStatus.OPEN,
                    qbo_last_synced_at=synced_at)
    modified.updated_at = synced_at + timedelta(days=1)   # edited after sync
    db.commit()
    clean = _inv(db, c, status=InvoiceStatus.OPEN, qbo_last_synced_at=datetime(_YR, 6, 2))
    clean.updated_at = datetime(_YR, 6, 1)                # synced after last edit
    db.commit()
    html = client.get("/invoices/?tab=modified_since_sync").text
    assert modified.invoice_number in html
    assert clean.invoice_number not in html


# ── Seam 3: search expansion ───────────────────────────────────────────────────

@pytest.fixture()
def searchable(db):
    c = _cust(db, phone="555-867-5309")
    p = _prod(db, sku=f"WIDGET-{next(_seq)}")
    db.add(CrossReference(product_id=p.id, ref_type=CrossRefType.OEM,
                          ref_number=f"OEM-XREF-{next(_seq)}"))
    db.commit()
    xref = db.query(CrossReference).filter(CrossReference.product_id == p.id).first()
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=c.id,
                  status=InvoiceStatus.OPEN, is_taxable=False)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, product_id=p.id, qty=1,
                                 unit_price=50.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    serial = f"SN-{next(_seq)}"
    db.add(ProductSerialNumber(product_id=p.id, serial_number=serial,
                               invoice_line_id=inv.lines[0].id))
    db.commit()
    return {"inv": inv, "sku": p.sku, "xref": xref.ref_number,
            "phone": "5309", "serial": serial}


@pytest.mark.parametrize("field", ["sku", "xref", "phone", "serial"])
def test_search_matches_new_fields(client, searchable, field):
    q = searchable[field]
    html = client.get(f"/invoices/?q={q}").text
    assert searchable["inv"].invoice_number in html, f"search by {field} ({q}) should match"


# ── Seam 4: intelligence metrics ───────────────────────────────────────────────

def test_intelligence_has_new_keys(db):
    c = _cust(db, credit_limit=1000.0)
    target = _inv(db, c, status=InvoiceStatus.DRAFT, amount=100.0)
    m = InvoiceMetricsService(db).intelligence_for(target)
    assert set(m.keys()) == set(INVOICE_METRIC_KEYS)
    for k in ("open_ar", "last_purchase", "outstanding_cores", "open_warranty_claims"):
        assert k in m


def test_intelligence_customer_relationship_values(db):
    c = _cust(db)
    prod = _prod(db, sku=f"P-{next(_seq)}")
    # finalized history → open AR + last purchase
    _inv(db, c, status=InvoiceStatus.OPEN, amount=300.0, created_at=datetime(_YR, 5, 1))
    _inv(db, c, status=InvoiceStatus.OPEN, amount=200.0, created_at=datetime(_YR, 6, 15))
    target = _inv(db, c, status=InvoiceStatus.DRAFT, amount=99.0,
                  created_at=datetime(_YR, 7, 1))          # draft → not AR/last-purchase
    # 1 open core
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=c.id,
                      product_id=prod.id, customer_unit_charge=40.0, qty_charged=1,
                      qty_returned=0, status=CoreStatus.OPEN))
    # 1 open warranty claim + 1 terminal (excluded)
    db.add(WarrantyClaim(claim_number=f"W-{next(_seq)}", customer_id=c.id,
                         status=WarrantyStatus.SUBMITTED_TO_VENDOR, total_credit_amount=10.0))
    db.add(WarrantyClaim(claim_number=f"W-{next(_seq)}", customer_id=c.id,
                         status=WarrantyStatus.CLOSED, total_credit_amount=99.0))
    db.commit()

    m = InvoiceMetricsService(db).intelligence_for(target)
    assert m["open_ar"] == 500.0                            # 300 + 200 open (draft excluded)
    assert m["last_purchase"] == datetime(_YR, 6, 15)       # latest finalized
    assert m["outstanding_cores"] == 1
    assert m["open_warranty_claims"] == 1                   # terminal one excluded


def test_panel_descriptor_order_and_margin_gating():
    m = {"customer_lifetime_sales": 1000, "open_ar": 500, "last_purchase": None,
         "outstanding_cores": 2, "open_warranty_claims": 1, "profit": 300, "margin_pct": 25.0}
    desc = _build_invoice_panel_metrics(m)
    assert [d["label"] for d in desc] == [
        "Lifetime Sales", "Open AR", "Last Purchase", "Outstanding Cores",
        "Open Warranty Claims", "Profit", "Margin",
    ]
    # only Profit + Margin are gated behind the showMargin toggle
    assert [d["label"] for d in desc if d.get("margin")] == ["Profit", "Margin"]
    assert desc[1]["value"] == "$500.00"


def test_workspace_renders_with_panel_metrics(client, db):
    c = _cust(db)
    inv = _inv(db, c, status=InvoiceStatus.DRAFT)
    assert client.get(f"/invoices/{inv.id}").status_code == 200
