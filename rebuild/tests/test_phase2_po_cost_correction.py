"""
Phase 2 — PO line cost correction after the order is committed
==============================================================
`POService.correct_po_line_cost` lets AP fix a mis-keyed line cost on a
SENT/PARTIAL/RECEIVED PO without re-opening the PO for free editing. Covered:

  - Happy path on a SENT PO: cost updates, audit row captures old→new + reason.
  - Records-only: correcting a cost AFTER receipt does NOT re-cost on-hand
    inventory (product.cost stays as booked at receipt — R11 Option A), and the
    audit note says so.
  - Reason is required.
  - Routing guards: blocked on DRAFT (use the line editor), blocked once the
    line is on a vendor bill (must use Correct & Reconcile).
  - No-op when the cost is unchanged (no audit row written).
"""
from __future__ import annotations

import datetime as dt
import pytest
from fastapi.testclient import TestClient

from tests.conftest import fresh_engine, activate
from app.main import app
from app.models.vendor import Vendor
from app.models.product import Product
from app.models.purchase_order import POLine
from app.models.audit import AuditLog
from app.services.po_service import POService
from app.constants import POStatus, EntityType, AuditAction

_UID = 1


@pytest.fixture()
def Session():
    return activate(fresh_engine())


@pytest.fixture()
def client():
    return TestClient(app)


def _seed_sent_po(db, *, qty=3, cost=243.04):
    vendor = Vendor(name="PAI Industries", vendor_code="PAI", vendor_number="9")
    db.add(vendor)
    db.flush()
    product = Product(sku="JAKS-GAS-90020", title="UPPER GASKET SET", qty_on_order=0)
    db.add(product)
    db.flush()
    svc = POService(db, _UID)
    po = svc.create_po(vendor_id=vendor.id, data={})
    line = svc.add_line(po_id=po.id, product_id=product.id,
                        data={"description": "UPPER GASKET SET", "qty_ordered": qty, "unit_cost": cost})
    svc.send_to_vendor(po.id)
    return po, line, product


def _cost_audit_rows(db, po_id):
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == EntityType.PURCHASE_ORDER,
            AuditLog.entity_id == po_id,
            AuditLog.action == AuditAction.EDITED,
        )
        .all()
    )


def test_correct_cost_after_send_audited(Session):
    db = Session()
    try:
        po, line, _ = _seed_sent_po(db)
        POService(db, _UID).correct_po_line_cost(line.id, 250.00, "vendor quoted higher")

        db.expire_all()
        assert db.get(POLine, line.id).unit_cost == pytest.approx(250.00)
        rows = _cost_audit_rows(db, po.id)
        assert any(
            "unit_cost=243.04" in (r.old_value or "")
            and "unit_cost=250.00" in (r.new_value or "")
            and "vendor quoted higher" in (r.notes or "")
            for r in rows
        ), "expected an old→new cost audit row carrying the reason"
    finally:
        db.close()


def test_correct_cost_after_receipt_is_records_only(Session):
    db = Session()
    try:
        po, line, product = _seed_sent_po(db, qty=3, cost=243.04)
        # Receive all 3 → product.cost is booked at the original cost.
        POService(db, _UID).create_receipt(
            vendor_id=po.vendor_id,
            po_line_quantities={line.id: 3},
            data={},
        )
        db.expire_all()
        booked_cost = db.get(Product, product.id).cost
        assert booked_cost == pytest.approx(243.04, abs=0.01)

        # Correct the PO cost AFTER receipt.
        POService(db, _UID).correct_po_line_cost(line.id, 300.00, "wrong cost keyed")
        db.expire_all()

        # PO line reflects the correction...
        assert db.get(POLine, line.id).unit_cost == pytest.approx(300.00)
        # ...but inventory cost is untouched (records-only / R11 Option A).
        assert db.get(Product, product.id).cost == pytest.approx(booked_cost, abs=0.0001)
        rows = _cost_audit_rows(db, po.id)
        assert any("records-only" in (r.notes or "") for r in rows), \
            "post-receipt correction must note it is records-only"
    finally:
        db.close()


def test_correct_cost_requires_reason(Session):
    db = Session()
    try:
        _, line, _ = _seed_sent_po(db)
        with pytest.raises(ValueError, match="reason is required"):
            POService(db, _UID).correct_po_line_cost(line.id, 250.0, "   ")
    finally:
        db.close()


def test_correct_cost_blocked_on_draft(Session):
    db = Session()
    try:
        vendor = Vendor(name="PAI", vendor_code="PAI", vendor_number="9")
        db.add(vendor); db.flush()
        product = Product(sku="JAKS-X", title="X")
        db.add(product); db.flush()
        svc = POService(db, _UID)
        po = svc.create_po(vendor_id=vendor.id, data={})
        line = svc.add_line(po_id=po.id, product_id=product.id,
                            data={"qty_ordered": 1, "unit_cost": 10.0})
        with pytest.raises(ValueError, match="draft/verbal"):
            svc.correct_po_line_cost(line.id, 12.0, "nope")
    finally:
        db.close()


def test_correct_cost_blocked_once_line_is_billed(Session):
    # A line carrying bill lines must route through Correct & Reconcile even while
    # the PO is still PARTIAL (some units billed, some outstanding).
    db = Session()
    try:
        po, line, _ = _seed_sent_po(db, qty=4, cost=100.0)
        svc = POService(db, _UID)
        svc.create_receipt(vendor_id=po.vendor_id, po_line_quantities={line.id: 2}, data={})
        svc.create_vendor_bill(
            po_id=po.id, vendor_id=po.vendor_id, bill_number="INV-1",
            bill_date=dt.datetime(2026, 6, 27), due_date=None,
            lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
        )
        db.expire_all()
        with pytest.raises(ValueError, match="already on a vendor bill"):
            svc.correct_po_line_cost(line.id, 110.0, "too late")
    finally:
        db.close()


def test_correct_cost_noop_writes_no_audit(Session):
    db = Session()
    try:
        po, line, _ = _seed_sent_po(db, cost=243.04)
        POService(db, _UID).correct_po_line_cost(line.id, 243.04, "same value")
        db.expire_all()
        assert _cost_audit_rows(db, po.id) == []
    finally:
        db.close()


# ── UI / endpoint integration ───────────────────────────────────────────────

def test_correction_form_renders_on_sent_workspace(Session, client):
    db = Session()
    try:
        po, line, _ = _seed_sent_po(db)
        po_id, line_id = po.id, line.id
    finally:
        db.close()

    html = client.get(f"/purchase-orders/{po_id}").text
    assert f"/purchase-orders/{po_id}/lines/{line_id}/correct-cost" in html, \
        "SENT PO workspace should expose the inline cost-correction form"


def test_correction_form_absent_on_draft_workspace(Session, client):
    db = Session()
    try:
        vendor = Vendor(name="PAI", vendor_code="PAI", vendor_number="9")
        db.add(vendor); db.flush()
        product = Product(sku="JAKS-Y", title="Y")
        db.add(product); db.flush()
        svc = POService(db, _UID)
        po = svc.create_po(vendor_id=vendor.id, data={})
        svc.add_line(po_id=po.id, product_id=product.id, data={"qty_ordered": 1, "unit_cost": 10.0})
        po_id = po.id
    finally:
        db.close()

    html = client.get(f"/purchase-orders/{po_id}").text
    assert "/correct-cost" not in html, "DRAFT PO uses free line editing, not the correction form"


def test_correct_cost_endpoint_updates_and_returns_section(Session, client):
    db = Session()
    try:
        po, line, _ = _seed_sent_po(db)
        po_id, line_id = po.id, line.id
    finally:
        db.close()

    r = client.post(
        f"/purchase-orders/{po_id}/lines/{line_id}/correct-cost",
        data={"unit_cost": "259.99", "reason": "vendor re-quote"},
    )
    assert r.status_code == 200
    assert "259.99" in r.text  # re-rendered lines section reflects the new cost

    db = Session()
    try:
        assert db.get(POLine, line_id).unit_cost == pytest.approx(259.99)
    finally:
        db.close()


def test_correct_cost_endpoint_rejects_missing_reason(Session, client):
    db = Session()
    try:
        po, line, _ = _seed_sent_po(db)
        po_id, line_id = po.id, line.id
    finally:
        db.close()

    # No reason → service raises, router fails soft (200, unchanged), cost stays put.
    r = client.post(
        f"/purchase-orders/{po_id}/lines/{line_id}/correct-cost",
        data={"unit_cost": "259.99", "reason": ""},
    )
    assert r.status_code == 200
    db = Session()
    try:
        assert db.get(POLine, line_id).unit_cost == pytest.approx(243.04)
    finally:
        db.close()
