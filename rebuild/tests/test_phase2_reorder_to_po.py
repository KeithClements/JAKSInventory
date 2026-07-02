"""
tests/test_phase2_reorder_to_po.py
====================================
MASTER_PLAN §23.3 Phase 2 — reorder-to-PO from the Low Stock report.

POService.create_pos_from_reorder({product_id: qty}) groups by each
product's PREFERRED ACTIVE vendor source (same resolution
SalesOrderService.create_po_for_line uses) into ONE draft PO per vendor —
a bulk reorder spanning several SKUs from the same vendor doesn't mint a
PO per line. A product with no preferred vendor is skipped, never silently
dropped (tracked in the result). The qty comes from the CALLER (the report
route re-derives it from ReportService.get_low_stock(), never trusting a
submitted qty), so this service never recomputes the suggested-qty formula.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.constants import POStatus
from app.main import app
from app.models.purchase_order import PurchaseOrder
from app.services.po_service import POService
from tests.conftest import activate, fresh_engine

_UID = 1


@pytest.fixture()
def db():
    Session = activate(fresh_engine())
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _vendor(db, name, code):
    from app.models.vendor import Vendor
    v = Vendor(name=name, vendor_code=code, vendor_number="9", is_active=True)
    db.add(v); db.flush()
    return v


def _product(db, *, sku, reorder_point=5, qty_on_hand=0, max_stock_level=None):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=sku, title=f"Part {sku}", cost=10.0, markup_pct=30.0,
                qty_on_hand=qty_on_hand, reorder_point=reorder_point,
                max_stock_level=max_stock_level,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.flush()
    return p


def _source(db, product_id, vendor_id, *, vendor_cost=50.0, is_preferred=True):
    from app.models.product import ProductVendorSource
    s = ProductVendorSource(product_id=product_id, vendor_id=vendor_id,
                            vendor_cost=vendor_cost, is_preferred=is_preferred,
                            is_active=True, vendor_part_number="X1")
    db.add(s); db.flush()
    return s


# ── Service: create_pos_from_reorder ───────────────────────────────────────

def test_groups_multiple_products_by_vendor_into_one_po(db):
    v = _vendor(db, "PAI Industries", "PAI")
    p1 = _product(db, sku="A-1")
    p2 = _product(db, sku="A-2")
    _source(db, p1.id, v.id)
    _source(db, p2.id, v.id)
    db.commit()

    result = POService(db, current_user_id=_UID).create_pos_from_reorder(
        {p1.id: 10, p2.id: 5})

    assert len(result["created"]) == 1        # one PO for the shared vendor
    assert result["created"][0]["line_count"] == 2
    assert result["skipped_no_vendor"] == []

    po = db.get(PurchaseOrder, result["created"][0]["po_id"])
    assert po.status == POStatus.DRAFT
    assert po.vendor_id == v.id
    lines_by_product = {ln.product_id: ln for ln in po.lines}
    assert lines_by_product[p1.id].qty_ordered == 10
    assert lines_by_product[p2.id].qty_ordered == 5


def test_different_vendors_create_separate_pos(db):
    v1 = _vendor(db, "PAI Industries", "PAI")
    v2 = _vendor(db, "Interstate-McBee", "IMB")
    p1 = _product(db, sku="B-1")
    p2 = _product(db, sku="B-2")
    _source(db, p1.id, v1.id)
    _source(db, p2.id, v2.id)
    db.commit()

    result = POService(db, current_user_id=_UID).create_pos_from_reorder(
        {p1.id: 3, p2.id: 4})

    assert len(result["created"]) == 2
    vendor_ids = {c["vendor_name"] for c in result["created"]}
    assert vendor_ids == {"PAI Industries", "Interstate-McBee"}


def test_product_with_no_preferred_vendor_is_skipped_not_dropped_silently(db):
    p_ok = _product(db, sku="C-1")
    v = _vendor(db, "PAI Industries", "PAI")
    _source(db, p_ok.id, v.id)
    p_no_vendor = _product(db, sku="C-2")   # no ProductVendorSource at all
    db.commit()

    result = POService(db, current_user_id=_UID).create_pos_from_reorder(
        {p_ok.id: 5, p_no_vendor.id: 5})

    assert len(result["created"]) == 1
    assert result["skipped_no_vendor"] == [{"product_id": p_no_vendor.id, "sku": "C-2"}]


def test_uses_preferred_source_not_just_any_active_source(db):
    """A product with TWO active sources must use the preferred one, matching
    Product.preferred_vendor_source's own contract."""
    v_preferred = _vendor(db, "PAI Industries", "PAI")
    v_other = _vendor(db, "Interstate-McBee", "IMB")
    p = _product(db, sku="D-1")
    _source(db, p.id, v_other.id, vendor_cost=99.0, is_preferred=False)
    _source(db, p.id, v_preferred.id, vendor_cost=50.0, is_preferred=True)
    db.commit()

    result = POService(db, current_user_id=_UID).create_pos_from_reorder({p.id: 6})
    assert result["created"][0]["vendor_name"] == "PAI Industries"


def test_zero_or_missing_qty_is_ignored(db):
    v = _vendor(db, "PAI Industries", "PAI")
    p = _product(db, sku="E-1")
    _source(db, p.id, v.id)
    db.commit()

    result = POService(db, current_user_id=_UID).create_pos_from_reorder({p.id: 0})
    assert result["created"] == []
    assert result["skipped_no_vendor"] == []   # not a vendor problem — qty was 0


def test_empty_items_returns_empty_result(db):
    result = POService(db, current_user_id=_UID).create_pos_from_reorder({})
    assert result == {"created": [], "skipped_no_vendor": []}


# ── Route: POST /reports/low-stock/create-pos ──────────────────────────────

def test_route_creates_pos_from_checked_rows(db, client):
    v = _vendor(db, "PAI Industries", "PAI")
    p = _product(db, sku="ROUTE-1", qty_on_hand=1, reorder_point=10, max_stock_level=20)
    _source(db, p.id, v.id)
    db.commit()

    r = client.post("/reports/low-stock/create-pos",
                    data={"product_ids": [str(p.id)]}, follow_redirects=False)
    assert r.status_code == 303
    assert "ok=" in r.headers["location"]

    po = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == v.id).first()
    assert po is not None
    assert po.lines[0].product_id == p.id
    assert po.lines[0].qty_ordered == 19   # target 20 - on_hand 1 - on_order 0


def test_route_ignores_a_submitted_qty_and_uses_the_reports_own_number(db, client):
    """The report's own suggested_qty is the source of truth — a submitted
    qty field (there isn't one in the real form, but a tampered POST) can
    never override it, since the route only sends product_ids."""
    v = _vendor(db, "PAI Industries", "PAI")
    p = _product(db, sku="ROUTE-2", qty_on_hand=2, reorder_point=10, max_stock_level=15)
    _source(db, p.id, v.id)
    db.commit()

    # Attempt to smuggle a qty override — the route never reads this field.
    r = client.post("/reports/low-stock/create-pos",
                    data={"product_ids": [str(p.id)], "qty_override": "99999"},
                    follow_redirects=False)
    assert r.status_code == 303

    po = db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == v.id).first()
    assert po.lines[0].qty_ordered == 13   # target 15 - on_hand 2, NOT 99999


def test_route_no_selection_flashes_error_and_creates_nothing(db, client):
    v = _vendor(db, "PAI Industries", "PAI")
    p = _product(db, sku="ROUTE-3", qty_on_hand=1, reorder_point=10)
    _source(db, p.id, v.id)
    db.commit()

    r = client.post("/reports/low-stock/create-pos", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert db.query(PurchaseOrder).count() == 0


def test_route_no_checkboxes_never_defaults_to_every_row(db, client):
    """An empty submission is ALWAYS "nothing selected" — never silently
    expanded to "every row". The Create POs button is disabled client-side
    until something's checked, so a stray/duplicate POST reaching the server
    with no product_ids must never reorder the whole low-stock list."""
    v = _vendor(db, "PAI Industries", "PAI")
    p1 = _product(db, sku="ROUTE-4", qty_on_hand=1, reorder_point=10)
    p2 = _product(db, sku="ROUTE-5", qty_on_hand=1, reorder_point=10)
    _source(db, p1.id, v.id)
    _source(db, p2.id, v.id)
    db.commit()

    r = client.post("/reports/low-stock/create-pos", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert db.query(PurchaseOrder).count() == 0
