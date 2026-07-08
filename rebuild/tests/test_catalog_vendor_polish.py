"""
tests/test_catalog_vendor_polish.py
===================================
Lane H — catalog/vendor polish guards.

  1. Vendor list N+1 fix — the list template must render from the router's
     §2B aggregate maps (open_po_map / open_bill_map / credit_map /
     last_po_map / lead_time_map), never per-row vendor.<relationship>
     traversals. Guarded two ways: the rendered page shows the aggregate
     values, and the SELECT count for GET /vendors/ stays FLAT as vendors
     (each with POs + credits) are added.

  2. GET /vendors/export.csv — filtered "export what I see" CSV download
     (mirrors /customers/export.csv), including the aggregate columns.

  3. Dead-template deletion — the verified-unreferenced templates and the
     superseded test file stay deleted, and the app still boots/renders.
     search/_results_dropdown.html was deleted together with the legacy
     GET /search header-dropdown route; /search/overlay (Ctrl+K) is now the
     only global-search surface.

  4. Category Maintenance delete confirms — Category/Brand/Manufacturer
     delete forms carry an onsubmit confirm() naming the item.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_catalog_vendor_polish.py -q
"""
from __future__ import annotations

import csv
import io
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import app.database as _appdb
from app.constants import POStatus, VendorCreditStatus
from app.main import app
from app.models.product import Brand, Manufacturer, ProductCategory
from app.models.purchase_order import PurchaseOrder
from app.models.vendor import Vendor, VendorCredit
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)

_REPO = pathlib.Path(__file__).resolve().parents[1]


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


def _vendor(db, name: str) -> Vendor:
    v = Vendor(name=name, vendor_code=f"L{next(_seq):03d}"[:4])
    db.add(v); db.commit(); db.refresh(v)
    return v


def _po(db, vendor_id: int, status: str = POStatus.DRAFT) -> PurchaseOrder:
    po = PurchaseOrder(po_number=f"PO-LH-{next(_seq):05d}",
                       vendor_id=vendor_id, status=status)
    db.add(po); db.commit(); db.refresh(po)
    return po


def _credit(db, vendor_id: int, amount: float) -> VendorCredit:
    cr = VendorCredit(vendor_id=vendor_id, amount=amount,
                      status=VendorCreditStatus.OPEN)
    db.add(cr); db.commit(); db.refresh(cr)
    return cr


def _select_count(client, url: str) -> int:
    """SELECT statements issued while serving one GET (N+1 detector)."""
    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(_ENGINE, "before_cursor_execute", _count)
    try:
        resp = client.get(url)
        assert resp.status_code == 200
    finally:
        event.remove(_ENGINE, "before_cursor_execute", _count)
    return counter["n"]


# ── 1. Vendor list renders from the aggregate maps ───────────────────────────

class TestVendorListAggregates:

    def test_list_shows_map_values(self, client, db):
        busy = _vendor(db, "Lane H Busy Diesel")
        quiet = _vendor(db, "Lane H Quiet Supply")
        _po(db, busy.id, POStatus.DRAFT)
        _po(db, busy.id, POStatus.SENT)
        _po(db, busy.id, POStatus.RECEIVED)     # closed — must NOT count
        _credit(db, busy.id, 45.50)

        resp = client.get("/vendors/")
        assert resp.status_code == 200
        page = resp.text
        assert "Lane H Busy Diesel" in page
        assert "Lane H Quiet Supply" in page
        # open_po_map: 2 open POs (received one excluded)
        assert "2 POs" in page
        # credit_map: 1 open credit + its amount
        assert "1 credit" in page
        assert "$45.50" in page

    def test_query_count_flat_as_vendors_grow(self, client, db):
        """The N+1 guard: SELECTs for GET /vendors/ must not scale with rows."""
        baseline = _select_count(client, "/vendors/")
        for i in range(6):
            v = _vendor(db, f"Lane H Bulk Vendor {i}")
            _po(db, v.id, POStatus.SENT)
            _credit(db, v.id, 10.0 + i)
        grown = _select_count(client, "/vendors/")
        assert grown == baseline, (
            f"GET /vendors/ issued {grown - baseline} extra SELECT(s) after "
            "adding vendors — the template is traversing per-row relationships "
            "again instead of reading the aggregate maps."
        )


# ── 2. /vendors/export.csv ────────────────────────────────────────────────────

class TestVendorExportCsv:

    def test_export_streams_filtered_csv_with_aggregates(self, client, db):
        target = _vendor(db, "Lane H Export Target")
        _po(db, target.id, POStatus.DRAFT)
        _credit(db, target.id, 99.25)

        resp = client.get("/vendors/export.csv?q=Lane H Export Target")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers.get("content-disposition", "")

        rows = list(csv.reader(io.StringIO(resp.text)))
        header, data = rows[0], rows[1:]
        assert header[0] == "name"
        assert {"open_pos", "open_bills", "open_credits",
                "open_credit_amount"} <= set(header)
        # q filter — only the target vendor exported
        assert len(data) == 1
        row = dict(zip(header, data[0]))
        assert row["name"] == "Lane H Export Target"
        assert row["open_pos"] == "1"
        assert row["open_credits"] == "1"
        assert row["open_credit_amount"] == "99.25"

    def test_export_tab_filter(self, client, db):
        gone = _vendor(db, "Lane H Inactive Vendor")
        gone.is_active = False
        db.commit()
        active_csv = client.get("/vendors/export.csv").text
        assert "Lane H Inactive Vendor" not in active_csv     # default tab=active
        inactive_csv = client.get("/vendors/export.csv?tab=inactive").text
        assert "Lane H Inactive Vendor" in inactive_csv

    def test_export_toolbar_link_on_list(self, client):
        assert 'href="/vendors/export.csv' in client.get("/vendors/").text


# ── 3. Dead templates stay deleted; app still boots ──────────────────────────

_DELETED = [
    "app/templates/invoices/detail.html",
    "app/templates/invoices/new.html",
    "app/templates/reports/inventory.html",
    "app/templates/reports/sales.html",
    "tests/test_ar_buckets_and_last_contacted.py",
    # Deleted together with the legacy GET /search header-dropdown route (the
    # Ctrl+K overlay at /search/overlay is the only global-search surface).
    "app/templates/search/_results_dropdown.html",
]


class TestDeadFilesStayDeleted:

    @pytest.mark.parametrize("rel", _DELETED)
    def test_deleted(self, rel):
        assert not (_REPO / rel).exists(), f"{rel} was resurrected — it is dead code"

    def test_app_boots_and_core_pages_render(self, client):
        assert client.get("/").status_code < 500
        assert client.get("/invoices/").status_code == 200
        assert client.get("/reports/").status_code == 200


# ── 4. Category Maintenance delete confirms ──────────────────────────────────

class TestCategoryDeleteConfirms:

    def test_delete_buttons_confirm_with_item_name(self, client, db):
        db.add(ProductCategory(name="Lane H Cat"))
        db.add(Brand(name="Lane H Brand"))
        db.add(Manufacturer(name="Lane H Make"))
        db.commit()

        page = client.get("/categories/").text
        assert 'return confirm("Delete category " + "Lane H Cat"' in page
        assert 'return confirm("Delete brand " + "Lane H Brand"' in page
        assert 'return confirm("Delete manufacturer " + "Lane H Make"' in page
