"""
tests/test_vendor_bill_freight.py
=================================
Vendor-billed freight on the bill.

A vendor (e.g. PAI) can put the freight charge on the SAME invoice as the parts.
The bill total was previously summed purely from the parts lines, so the recorded
AP payable understated what we actually owed by the freight amount. VendorBill now
carries freight_amount:

  * create_vendor_bill defaults freight from the PO's freight_in_cost (net of
    freight already billed on prior bills of that PO), and total_amount =
    parts_subtotal + freight_amount;
  * an explicit freight_amount (incl. 0.0) overrides the default — 0.0 is the
    "separate carrier bills freight" case;
  * freight is NOT a 3-way-match variance — a clean bill with freight still
    auto-APPROVES;
  * edit_vendor_bill can correct freight, and the QBO Bill payload posts freight
    as its own freight-in expense line so the QBO total matches total_amount.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401 — registers every model on Base

from app.constants import POStatus, UserRole, VendorBillStatus
from app.models.product import Product
from app.models.purchase_order import POLine, PurchaseOrder, VendorBill
from app.models.user import User
from app.models.vendor import Vendor
from app.services.po_service import POService
from app.services.qbo_service import QBOSyncService

_counter = itertools.count(1)
_UID = 1


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == 1).first():
        s.add(User(id=1, name="Test Admin", username="admin",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


def _make_vendor(db) -> Vendor:
    n = next(_counter)
    v = Vendor(name=f"Vendor-FRT-{n}", vendor_code=f"F{n:03d}"[:4])
    db.add(v)
    db.flush()
    return v


def _make_product(db) -> Product:
    n = next(_counter)
    p = Product(sku=f"SKU-FRT-{n:06d}", title=f"Widget-FRT {n}",
                qty_on_hand=0, qty_committed=0, cost=100.0)
    db.add(p)
    db.flush()
    return p


def _make_po(db, vendor, product, *, qty=2, unit_cost=100.0, freight=160.0):
    n = next(_counter)
    po = PurchaseOrder(
        po_number=f"PO-FRT-{n:04d}", vendor_id=vendor.id,
        status=POStatus.RECEIVED, freight_in_cost=freight,
        notes="", internal_notes="",
    )
    db.add(po)
    db.flush()
    line = POLine(
        po_id=po.id, product_id=product.id, description=product.title,
        qty_ordered=qty, qty_received=qty, qty_billed=0,
        unit_cost=unit_cost, core_charge_per_unit=0.0, notes="",
    )
    db.add(line)
    db.commit()
    db.refresh(po); db.refresh(line)
    return po, line


def _svc(db) -> POService:
    return POService(db, current_user_id=_UID)


# ══════════════════════════════════════════════════════════════════════════════
# Default freight from the PO
# ══════════════════════════════════════════════════════════════════════════════

def test_freight_defaults_from_po_and_lands_in_total(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=2, unit_cost=100.0, freight=160.0)

    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-FRT-1",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )

    assert bill.parts_subtotal == 200.0
    assert bill.freight_amount == 160.0
    assert bill.total_amount == 360.0
    # Clean parts match → freight does NOT flip it to discrepancy. Risk #5: a
    # clean bill lands PENDING (explicit AP Approve), never DISCREPANCY here.
    assert bill.status == VendorBillStatus.PENDING


def test_zero_freight_override_is_carrier_billed_case(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=2, unit_cost=100.0, freight=160.0)

    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-FRT-2",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
        freight_amount=0.0,
    )
    assert bill.freight_amount == 0.0
    assert bill.total_amount == 200.0


def test_explicit_freight_overrides_po_default(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=2, unit_cost=100.0, freight=160.0)

    # Vendor's actual freight differed from the PO estimate.
    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-FRT-3",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
        freight_amount=175.50,
    )
    assert bill.freight_amount == 175.50
    assert bill.total_amount == 375.50


def test_split_bills_do_not_double_count_freight(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=4, unit_cost=100.0, freight=160.0)

    first = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-SPLIT-A",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )
    assert first.freight_amount == 160.0

    # Second bill on the same PO defaults to the REMAINING freight (0) — the
    # full $160 already rode on the first bill.
    second = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-SPLIT-B",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )
    assert second.freight_amount == 0.0
    assert second.total_amount == 200.0


# ══════════════════════════════════════════════════════════════════════════════
# Editing freight
# ══════════════════════════════════════════════════════════════════════════════

def test_edit_bill_freight_recomputes_total(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=2, unit_cost=100.0, freight=160.0)

    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-EDIT-1",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )
    assert bill.total_amount == 360.0

    _svc(db).edit_vendor_bill(
        bill.id, reason="Vendor re-billed freight at $140",
        header={"freight_amount": 140.0},
    )
    db.refresh(bill)
    assert bill.freight_amount == 140.0
    assert bill.total_amount == 340.0  # parts 200 + freight 140


def test_edit_bill_line_keeps_freight(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=2, unit_cost=100.0, freight=160.0)

    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-EDIT-2",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )
    bl_id = bill.lines[0].id

    # Correct the part cost only — freight must survive the line edit.
    _svc(db).edit_vendor_bill(
        bill.id, reason="Part cost corrected to $110",
        line_edits=[{"bill_line_id": bl_id, "unit_cost": 110.0}],
    )
    db.refresh(bill)
    assert bill.freight_amount == 160.0
    assert bill.parts_subtotal == 220.0
    assert bill.total_amount == 380.0  # parts 220 + freight 160


def test_negative_freight_rejected_on_edit(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product)
    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-NEG-1",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )
    with pytest.raises(ValueError, match="Freight cannot be negative"):
        _svc(db).edit_vendor_bill(
            bill.id, reason="bad", header={"freight_amount": -5.0},
        )


# ══════════════════════════════════════════════════════════════════════════════
# QBO Bill payload — freight posts as its own line
# ══════════════════════════════════════════════════════════════════════════════

def test_qbo_bill_payload_includes_freight_line(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=2, unit_cost=100.0, freight=160.0)

    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-QBO-1",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )

    payload = QBOSyncService(db, current_user_id=_UID)._build_bill_payload(
        bill, vendor_ref={"value": "9"}, account_id="50", freight_account_id="60",
    )
    amounts = [ln["Amount"] for ln in payload["Line"]]
    # Parts line + freight line; the freight line posts to the freight account.
    assert 200.0 in amounts
    assert 160.0 in amounts
    assert round(sum(amounts), 2) == bill.total_amount
    freight_line = next(ln for ln in payload["Line"] if ln["Amount"] == 160.0)
    assert freight_line["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] == "60"


def test_qbo_bill_payload_no_freight_line_when_zero(db):
    vendor = _make_vendor(db)
    product = _make_product(db)
    po, line = _make_po(db, vendor, product, qty=2, unit_cost=100.0, freight=0.0)

    bill = _svc(db).create_vendor_bill(
        po_id=po.id, vendor_id=vendor.id, bill_number="PAI-QBO-2",
        bill_date=None, due_date=None,
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 100.0}],
    )
    payload = QBOSyncService(db, current_user_id=_UID)._build_bill_payload(
        bill, vendor_ref={"value": "9"}, account_id="50", freight_account_id="60",
    )
    assert len(payload["Line"]) == 1
    assert payload["Line"][0]["Amount"] == 200.0
