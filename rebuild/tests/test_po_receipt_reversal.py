"""
tests/test_po_receipt_reversal.py
==================================
POService.reverse_receipt — undo a mis-receive. Reverses qty_on_hand,
qty_on_order, and (when a prior moving-average snapshot exists) product.cost,
then marks the whole receipt reversed. Whole-receipt, atomic, idempotent.

Scoped to the safe/exact case — refuses (not guesses) when a line was already
billed, allocated stock to a linked SO, or something else touched the
product's on-hand qty since the receipt.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()
_counter = itertools.count(1)


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


# ── Helpers (pattern mirrors tests/test_r3_landed_cost.py) ──────────────────

def _vendor(db):
    from app.models.vendor import Vendor
    n = next(_counter)
    v = Vendor(name=f"RevVendor-{n}")
    db.add(v); db.flush()
    return v


def _product(db, *, initial_cost=0.0, qty_on_hand=0):
    from app.constants import ProductStatus
    from app.models.product import Product
    n = next(_counter)
    p = Product(
        sku=f"REV-{n:04d}", title=f"Rev Part {n}", description="test",
        cost=initial_cost, last_cost=initial_cost,
        markup_pct=50.0, qty_on_hand=qty_on_hand, qty_committed=0,
        status=ProductStatus.ACTIVE, is_active=True,
        cost_source="receipt" if initial_cost > 0 else "manual",
    )
    db.add(p); db.flush()
    return p


def _sent_po(db, vendor, *, lines=(), is_drop_ship=False):
    """lines = [(product, unit_cost, qty), ...] -> (po, [POLine, ...])"""
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder, POLine
    n = next(_counter)
    po = PurchaseOrder(
        vendor_id=vendor.id, po_number=f"REV-PO-{n:04d}",
        status=POStatus.SENT, is_drop_ship=is_drop_ship,
    )
    db.add(po); db.flush()
    out = []
    for product, unit_cost, qty in lines:
        line = POLine(
            po_id=po.id, product_id=product.id if product else None,
            description="test", qty_ordered=qty, unit_cost=unit_cost,
        )
        db.add(line); db.flush()
        out.append(line)
    return po, out


def _receive(db, vendor, quantities):
    from app.services.po_service import POService
    receipt = POService(db, current_user_id=1).create_receipt(
        vendor_id=vendor.id, po_line_quantities=quantities, data={},
    )
    db.commit()
    return receipt


def _reverse(db, receipt_id, reason="test reversal"):
    from app.services.po_service import POService
    result = POService(db, current_user_id=1).reverse_receipt(receipt_id, reason)
    db.commit()
    return result


def _fresh(db, model, obj_id):
    return db.query(model).filter(model.id == obj_id).first()


# ── Happy path ───────────────────────────────────────────────────────────────

def test_reverse_no_prior_stock_restores_zero(db):
    from app.constants import POStatus
    from app.models.product import Product
    from app.models.purchase_order import POLine, PurchaseOrder

    vendor = _vendor(db)
    product = _product(db, initial_cost=0.0, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 20.0, 10)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 10})

    p = _fresh(db, Product, product.id)
    assert p.qty_on_hand == 10 and p.cost == pytest.approx(20.0)

    result = _reverse(db, receipt.id)

    p = _fresh(db, Product, product.id)
    assert p.qty_on_hand == 0
    assert p.cost == pytest.approx(0.0)
    ln = _fresh(db, POLine, line.id)
    assert ln.qty_received == 0
    assert ln.over_received is False
    po_fresh = _fresh(db, PurchaseOrder, po.id)
    assert po_fresh.status == POStatus.SENT
    assert any("restored" in n for n in result["cost_notes"])


def test_reverse_blended_average_restores_exact_prior_cost(db):
    """5 on hand @ $10, receive 10 @ $20 -> avg $16.67. Reversing must restore
    exactly $10.00, not re-derive it approximately."""
    from app.models.product import Product

    vendor = _vendor(db)
    product = _product(db, initial_cost=10.0, qty_on_hand=5)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 20.0, 10)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 10})

    p = _fresh(db, Product, product.id)
    assert p.qty_on_hand == 15
    assert p.cost == pytest.approx((5 * 10.0 + 10 * 20.0) / 15, abs=0.005)

    _reverse(db, receipt.id)

    p = _fresh(db, Product, product.id)
    assert p.qty_on_hand == 5
    assert p.cost == pytest.approx(10.0, abs=0.0001)


def test_reverse_is_idempotent(db):
    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 15.0, 4)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 4})

    _reverse(db, receipt.id)
    with pytest.raises(ValueError, match="already reversed"):
        _reverse(db, receipt.id)


def test_reverse_requires_a_reason(db):
    from app.services.po_service import POService
    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 15.0, 4)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 4})
    with pytest.raises(ValueError, match="reason is required"):
        POService(db, current_user_id=1).reverse_receipt(receipt.id, "   ")


def test_reverse_drop_ship_line_only_touches_qty_received(db):
    """Drop-ship never touched inventory on receipt — reversal must not error
    even though there's no product-side state to undo."""
    from app.models.purchase_order import POLine

    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 15.0, 3)], is_drop_ship=True)
    db.commit()
    receipt = _receive(db, vendor, {line.id: 3})

    ln = _fresh(db, POLine, line.id)
    assert ln.qty_received == 3

    _reverse(db, receipt.id)

    ln = _fresh(db, POLine, line.id)
    assert ln.qty_received == 0


# ── PO status recomputation ──────────────────────────────────────────────────

def test_reverse_rolls_back_po_status_partial_to_sent(db):
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder

    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 10.0, 10)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 4})   # partial

    assert _fresh(db, PurchaseOrder, po.id).status == POStatus.PARTIAL

    _reverse(db, receipt.id)

    assert _fresh(db, PurchaseOrder, po.id).status == POStatus.SENT


def test_reverse_leaves_other_lines_of_same_po_intact(db):
    """Two lines on one PO, received in SEPARATE receipts. Reversing the first
    receipt must not touch the second line's qty or cost."""
    from app.constants import POStatus
    from app.models.product import Product
    from app.models.purchase_order import POLine, PurchaseOrder

    vendor = _vendor(db)
    p1 = _product(db, qty_on_hand=0)
    p2 = _product(db, qty_on_hand=0)
    po, (line1, line2) = _sent_po(db, vendor, lines=[(p1, 10.0, 5), (p2, 30.0, 2)])
    db.commit()
    r1 = _receive(db, vendor, {line1.id: 5})
    r2 = _receive(db, vendor, {line2.id: 2})
    assert _fresh(db, PurchaseOrder, po.id).status == POStatus.RECEIVED

    _reverse(db, r1.id)

    assert _fresh(db, POLine, line1.id).qty_received == 0
    assert _fresh(db, POLine, line2.id).qty_received == 2   # untouched
    assert _fresh(db, Product, p2.id).qty_on_hand == 2       # untouched
    assert _fresh(db, PurchaseOrder, po.id).status == POStatus.PARTIAL


def test_reverse_does_not_uncancel_a_cancelled_po(db):
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder
    from app.services.po_service import POService

    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 10.0, 10)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 4})   # partial receipt, 6 outstanding
    po_svc = POService(db, current_user_id=1)
    po_svc.cancel(po.id)
    db.commit()
    assert _fresh(db, PurchaseOrder, po.id).status == POStatus.CANCELLED

    _reverse(db, receipt.id)

    assert _fresh(db, PurchaseOrder, po.id).status == POStatus.CANCELLED


# ── qty_on_order symmetry (real send_to_vendor flow) ────────────────────────

def test_reverse_restores_qty_on_order(db):
    from app.constants import POStatus
    from app.models.product import Product
    from app.models.purchase_order import PurchaseOrder, POLine
    from app.services.po_service import POService

    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po = PurchaseOrder(vendor_id=vendor.id, po_number=f"REV-PO-{next(_counter):04d}",
                        status="draft")
    db.add(po); db.flush()
    line = POLine(po_id=po.id, product_id=product.id, description="test",
                  qty_ordered=6, unit_cost=12.0)
    db.add(line); db.flush()
    db.commit()

    POService(db, current_user_id=1).send_to_vendor(po.id)
    db.commit()
    assert _fresh(db, Product, product.id).qty_on_order == 6

    receipt = _receive(db, vendor, {line.id: 6})
    assert _fresh(db, Product, product.id).qty_on_order == 0

    _reverse(db, receipt.id)
    assert _fresh(db, Product, product.id).qty_on_order == 6


# ── Guard rails — refuse rather than guess ──────────────────────────────────

def test_reverse_blocked_when_already_billed(db):
    from app.models.purchase_order import POLine
    from app.services.po_service import POService

    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 10.0, 5)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 5})

    fresh_line = _fresh(db, POLine, line.id)
    fresh_line.qty_billed = 5
    db.commit()

    with pytest.raises(ValueError, match="already billed"):
        POService(db, current_user_id=1).reverse_receipt(receipt.id, "oops")

    # Nothing changed
    assert _fresh(db, POLine, line.id).qty_received == 5


def test_reverse_blocked_when_allocated_to_linked_so(db):
    from app.models.product import Product
    from app.models.quote import SOLine, SalesOrder
    from app.models.customer import Customer
    from app.services.po_service import POService

    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 10.0, 5)])
    cust = Customer(company_name="Rev Test Customer")
    db.add(cust); db.flush()
    so = SalesOrder(so_number=f"REV-SO-{next(_counter):04d}", customer_id=cust.id, status="open")
    db.add(so); db.flush()
    so_line = SOLine(so_id=so.id, product_id=product.id, description="test",
                      qty_ordered=5, unit_price=25.0, linked_po_line_id=line.id)
    db.add(so_line); db.flush()
    db.commit()

    receipt = _receive(db, vendor, {line.id: 5})
    assert _fresh(db, Product, product.id).qty_committed == 5

    with pytest.raises(ValueError, match="linked sales order"):
        POService(db, current_user_id=1).reverse_receipt(receipt.id, "oops")

    assert _fresh(db, Product, product.id).qty_on_hand == 5   # nothing changed


def test_reverse_blocked_when_later_activity_touched_product(db):
    from app.models.product import Product
    from app.services.inventory_service import InventoryService
    from app.services.po_service import POService

    vendor = _vendor(db)
    product = _product(db, qty_on_hand=0)
    po, (line,) = _sent_po(db, vendor, lines=[(product, 10.0, 5)])
    db.commit()
    receipt = _receive(db, vendor, {line.id: 5})

    InventoryService(db, current_user_id=1).adjust_inventory(
        product.id, qty_delta=-1, reason="damaged", note="broke one"
    )
    db.commit()

    with pytest.raises(ValueError, match="other inventory activity"):
        POService(db, current_user_id=1).reverse_receipt(receipt.id, "oops")

    assert _fresh(db, Product, product.id).qty_on_hand == 4   # nothing further changed
