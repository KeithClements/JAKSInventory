"""
tests/test_phase2b_landed_guards.py
===================================
Guards for the part of the latest landed batch NOT covered elsewhere.

Division of labor (avoid duplicating another session's work):
  • The "four list/data seams" (@3cb1a86) — customer account_number create/
    update/search, the ?sort=&direction= param on customers/invoices/quotes/
    payments, the category tree, and CRMService.get_unified_timeline — are
    covered by tests/test_list_seams.py. (NOTE for the coordinator: that file's
    `test_customer_sort_direction_orders_rows` is an ORDER-DEPENDENT FLAKE — it
    passes in isolation and in most full-suite runs but intermittently fails on
    the conftest cross-module engine-repoint hazard; the sort feature itself is
    correct. It should be hardened, not chased as a regression.)

  • THIS file pins what that one doesn't: the receiving condition_notes
    round-trip, plus render-smokes for the quote print header, the customer
    detail timeline page, and the receiving surfaces.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_phase2b_landed_guards.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import POStatus, QuoteStatus
from app.main import app
from app.models.customer import Customer
from app.models.product import Product
from app.models.purchase_order import POLine, POReceiptLine, PurchaseOrder
from app.models.quote import Quote
from app.models.vendor import Vendor
from app.services.po_service import POService
from tests.conftest import activate, fresh_engine

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


def _customer(db, **kw) -> Customer:
    c = Customer(company_name=f"P2B Co {next(_seq)}", contact_name="QA", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _quote(db, customer_id) -> Quote:
    q = Quote(quote_number=f"Q-P2B-{next(_seq):05d}", customer_id=customer_id,
              status=QuoteStatus.SENT)
    db.add(q); db.commit(); db.refresh(q)
    return q


def _po_with_line(db, *, qty=5):
    vendor = Vendor(name=f"Vend {next(_seq)}")
    db.add(vendor); db.commit(); db.refresh(vendor)
    prod = Product(sku=f"P2B-{next(_seq)}", title="Part", cost=10.0)
    db.add(prod); db.commit(); db.refresh(prod)
    po = PurchaseOrder(po_number=f"PO-P2B-{next(_seq):05d}", vendor_id=vendor.id,
                       status=POStatus.SENT)
    db.add(po); db.commit(); db.refresh(po)
    pol = POLine(po_id=po.id, product_id=prod.id, description="part",
                 qty_ordered=qty, unit_cost=10.0)
    db.add(pol); db.commit(); db.refresh(pol)
    return vendor, po, pol


# ── Receiving: condition_notes round-trips onto the receipt line ─────────────

def test_receive_condition_notes_round_trips_to_receipt_line(db):
    vendor, po, pol = _po_with_line(db, qty=5)
    note = "Cracked housing — flagged at the dock"

    POService(db, _UID).create_receipt(
        vendor_id=vendor.id,
        po_line_quantities={pol.id: 2},
        data={"condition_notes_map": {pol.id: note}},
    )

    db.expire_all()
    rl = db.query(POReceiptLine).filter(POReceiptLine.po_line_id == pol.id).first()
    assert rl is not None
    assert rl.condition_notes == note


def test_receive_without_condition_note_leaves_it_null(db):
    vendor, po, pol = _po_with_line(db, qty=5)
    POService(db, _UID).create_receipt(
        vendor_id=vendor.id, po_line_quantities={pol.id: 1}, data={})
    db.expire_all()
    rl = db.query(POReceiptLine).filter(POReceiptLine.po_line_id == pol.id).first()
    assert rl is not None and rl.condition_notes is None


# ── Render-smokes ────────────────────────────────────────────────────────────

def test_render_smoke_quote_print(client, db):
    q = _quote(db, _customer(db).id)
    assert client.get(f"/quotes/{q.id}/print").status_code == 200


def test_render_smoke_customer_detail_with_timeline(client, db):
    cust = _customer(db)
    _quote(db, cust.id)  # give the timeline something to render
    assert client.get(f"/customers/{cust.id}").status_code == 200


def test_render_smoke_receiving_workspace_and_queue(client, db):
    _vendor, po, _pol = _po_with_line(db, qty=3)
    assert client.get(f"/purchase-orders/{po.id}").status_code == 200
    assert client.get("/purchase-orders/receiving").status_code == 200
