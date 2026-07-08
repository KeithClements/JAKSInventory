"""
tests/test_optimistic_locking_wired.py
======================================
Route-level guards for optimistic locking (audit risk #12) on the Invoice, PO,
and Quote header-save paths, plus the new PO list CSV export.

BaseService.check_version() was built but only Sales Orders passed a version.
These tests pin the wiring for the other three documents:

  • a STALE _updated_at/updated_at on a header save → rejected with a friendly
    "changed by another user" message (409 for HTMX/fetch paths; redirect with
    ?error= for the quote's full-form POST) and NO write.
  • a FRESH timestamp → save succeeds, and the response carries X-Updated-At so
    the client can adopt the new version (autosave must never self-conflict).
  • a MISSING timestamp → legacy save path still works (no version to check).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_optimistic_locking_wired.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.constants import InvoiceStatus, POStatus, QuoteStatus
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.purchase_order import PurchaseOrder
from app.models.quote import Quote
from app.models.vendor import Vendor
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    SessionLocal = activate(fresh_engine())
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(db):
    return TestClient(app, raise_server_exceptions=False)


# ── seeding ──────────────────────────────────────────────────────────────────

def _customer(db) -> Customer:
    c = Customer(company_name=f"Lock Co {next(_seq)}", contact_name="QA")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _draft_invoice(db) -> Invoice:
    cust = _customer(db)
    inv = Invoice(invoice_number=f"INV-LK-{next(_seq):05d}", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _vendor(db, name: str | None = None) -> Vendor:
    v = Vendor(name=name or f"Lock Vendor {next(_seq)}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _draft_po(db, vendor: Vendor | None = None, status: str = POStatus.DRAFT) -> PurchaseOrder:
    vendor = vendor or _vendor(db)
    po = PurchaseOrder(po_number=f"PO-LK-{next(_seq):05d}", vendor_id=vendor.id,
                       status=status)
    db.add(po); db.commit(); db.refresh(po)
    return po


def _draft_quote(db) -> Quote:
    cust = _customer(db)
    q = Quote(quote_number=f"Q-LK-{next(_seq):05d}", customer_id=cust.id,
              status=QuoteStatus.DRAFT)
    db.add(q); db.commit(); db.refresh(q)
    return q


def _stale(record) -> str:
    """An updated_at from 'an earlier page load' — beyond the 1s tolerance."""
    return (record.updated_at - timedelta(minutes=5)).isoformat()


# ════════════════════════════════════════════════════════════════════════════
# Invoice header — POST /invoices/{id}/header  (HTMX, _updated_at field)
# ════════════════════════════════════════════════════════════════════════════

def test_invoice_header_stale_version_rejected(client, db):
    inv = _draft_invoice(db)
    r = client.post(f"/invoices/{inv.id}/header",
                    data={"notes": "SHOULD-NOT-LAND", "_updated_at": _stale(inv)})
    assert r.status_code == 409
    assert "changed by another user" in r.text.lower()
    db.expire_all()
    assert inv.notes != "SHOULD-NOT-LAND"          # write rejected, not applied


def test_invoice_header_fresh_version_succeeds(client, db):
    inv = _draft_invoice(db)
    r = client.post(f"/invoices/{inv.id}/header",
                    data={"notes": "fresh save", "_updated_at": inv.updated_at.isoformat()})
    assert r.status_code == 200
    # X-Updated-At feeds the client's next save so autosave can't self-conflict.
    assert r.headers.get("X-Updated-At")
    db.expire_all()
    assert inv.notes == "fresh save"


def test_invoice_header_missing_version_still_saves(client, db):
    # Legacy save path (no hidden field posted) must keep working.
    inv = _draft_invoice(db)
    r = client.post(f"/invoices/{inv.id}/header", data={"notes": "legacy save"})
    assert r.status_code == 200
    db.expire_all()
    assert inv.notes == "legacy save"


# ════════════════════════════════════════════════════════════════════════════
# PO header — POST /purchase-orders/{id}/header  (HTMX autosave, hx-swap none)
# ════════════════════════════════════════════════════════════════════════════

def test_po_header_stale_version_rejected(client, db):
    po = _draft_po(db)
    r = client.post(f"/purchase-orders/{po.id}/header",
                    data={"notes": "SHOULD-NOT-LAND", "_updated_at": _stale(po)})
    assert r.status_code == 409
    assert "changed by another user" in r.text.lower()
    db.expire_all()
    assert po.notes != "SHOULD-NOT-LAND"


def test_po_header_fresh_version_succeeds(client, db):
    po = _draft_po(db)
    r = client.post(f"/purchase-orders/{po.id}/header",
                    data={"notes": "fresh po save", "_updated_at": po.updated_at.isoformat()})
    assert r.status_code == 204
    assert r.headers.get("X-Updated-At")
    db.expire_all()
    assert po.notes == "fresh po save"


# ════════════════════════════════════════════════════════════════════════════
# Quote header — autosave (fetch, 409) + full-form POST (303 ?error= redirect)
# ════════════════════════════════════════════════════════════════════════════

def test_quote_autosave_stale_version_rejected(client, db):
    q = _draft_quote(db)
    r = client.post(f"/quotes/{q.id}/autosave",
                    data={"notes": "SHOULD-NOT-LAND", "updated_at": _stale(q)})
    assert r.status_code == 409
    assert "changed by another user" in r.text.lower()
    db.expire_all()
    assert q.notes != "SHOULD-NOT-LAND"


def test_quote_autosave_fresh_version_succeeds(client, db):
    q = _draft_quote(db)
    r = client.post(f"/quotes/{q.id}/autosave",
                    data={"notes": "fresh quote save", "updated_at": q.updated_at.isoformat()})
    assert r.status_code == 200
    assert r.headers.get("X-Updated-At")   # client adopts this for the next save
    db.expire_all()
    assert q.notes == "fresh quote save"


def test_quote_full_form_stale_version_redirects_with_error(client, db):
    q = _draft_quote(db)
    r = client.post(f"/quotes/{q.id}",
                    data={"notes": "SHOULD-NOT-LAND", "updated_at": _stale(q)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    db.expire_all()
    assert q.notes != "SHOULD-NOT-LAND"


# ════════════════════════════════════════════════════════════════════════════
# PO list CSV export — GET /purchase-orders/export.csv (tab/q filters)
# ════════════════════════════════════════════════════════════════════════════

def test_po_export_csv_returns_all_rows(client, db):
    po_a = _draft_po(db, _vendor(db, "Alpha Diesel Export"))
    po_b = _draft_po(db, _vendor(db, "Bravo Turbo Export"), status=POStatus.RECEIVED)
    r = client.get("/purchase-orders/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert po_a.po_number in r.text and po_b.po_number in r.text
    assert "Alpha Diesel Export" in r.text


def test_po_export_csv_respects_q_and_tab_filters(client, db):
    po_a = _draft_po(db, _vendor(db, "Alpha Diesel Export"))                       # DRAFT → open
    po_b = _draft_po(db, _vendor(db, "Bravo Turbo Export"), status=POStatus.RECEIVED)

    # q filter — vendor-name search, same semantics as the list view
    r = client.get("/purchase-orders/export.csv", params={"q": "Alpha Diesel"})
    assert po_a.po_number in r.text and po_b.po_number not in r.text

    # tab filter — "open" covers draft/verbal/sent; the RECEIVED PO drops out
    r = client.get("/purchase-orders/export.csv", params={"tab": "open"})
    assert po_a.po_number in r.text and po_b.po_number not in r.text

    # unknown tab slug falls back to "all" rather than erroring
    r = client.get("/purchase-orders/export.csv", params={"tab": "bogus"})
    assert r.status_code == 200
    assert po_a.po_number in r.text and po_b.po_number in r.text
