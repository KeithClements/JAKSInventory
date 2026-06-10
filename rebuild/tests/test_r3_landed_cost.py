"""
tests/test_r3_landed_cost.py
============================
R3 — freight-in is landed into COGS at receipt time.

PurchaseOrder.freight_in_cost (the PO-level freight field — the vendor bill has
no freight column) is allocated across the PO's ORDERED units, weighted by line
value (qty_ordered x unit_cost; all-zero-cost POs fall back to qty). Each
received unit then enters the moving-average update at its LANDED unit cost
(unit_cost + freight adder), POLine.landed_cost_per_unit records the landed
unit cost, and a ProductCostHistory "landed cost" row is written.

Invariants pinned here:
  * zero/absent freight  -> bit-for-bit pre-R3 behavior (mirrors the numbers in
    tests/test_product_cost_semantic.py::test_receipt_moving_average_weighted_correctly)
  * partial receipts land only their fraction of freight — two half receipts
    land half the freight each, never more (no over-allocation)
  * freight never leaks into ProductVendorSource.vendor_cost (the vendor quote)

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r3_landed_cost.py -q
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


# ---------------------------------------------------------------------------
# Helpers (pattern mirrors tests/test_product_cost_semantic.py)
# ---------------------------------------------------------------------------

def _vendor(db):
    from app.models.purchase_order import Vendor
    n = next(_counter)
    v = Vendor(name=f"R3Vendor-{n}")
    db.add(v); db.flush()
    return v


def _product(db, *, initial_cost=0.0, qty_on_hand=0):
    from app.constants import ProductStatus
    from app.models.product import Product
    n = next(_counter)
    p = Product(
        sku=f"R3-{n:04d}", title=f"R3 Part {n}", description="test",
        cost=initial_cost, last_cost=initial_cost,
        markup_pct=50.0, qty_on_hand=qty_on_hand, qty_committed=0,
        status=ProductStatus.ACTIVE, is_active=True,
        cost_source="receipt" if initial_cost > 0 else "manual",
    )
    db.add(p); db.flush()
    return p


def _add_vendor_source(db, product, vendor, *, vendor_cost):
    from app.models.product import ProductVendorSource
    src = ProductVendorSource(
        product_id=product.id, vendor_id=vendor.id,
        vendor_cost=vendor_cost, is_preferred=True, is_active=True,
    )
    db.add(src); db.flush()
    return src


def _sent_po(db, vendor, *, freight=0.0, lines=()):
    """lines = [(product, unit_cost, qty), ...] -> (po, [POLine, ...])"""
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder, POLine
    n = next(_counter)
    po = PurchaseOrder(
        vendor_id=vendor.id, po_number=f"R3-PO-{n:04d}",
        status=POStatus.SENT, freight_in_cost=freight,
    )
    db.add(po); db.flush()
    out = []
    for product, unit_cost, qty in lines:
        line = POLine(
            po_id=po.id, product_id=product.id, description="test",
            qty_ordered=qty, unit_cost=unit_cost,
        )
        db.add(line); db.flush()
        out.append(line)
    return po, out


def _receive(db, vendor, quantities):
    from app.services.po_service import POService
    POService(db, current_user_id=1).create_receipt(
        vendor_id=vendor.id, po_line_quantities=quantities, data={},
    )
    db.commit()


def _fresh(db, model, obj_id):
    return db.query(model).filter(model.id == obj_id).first()


def _freight_history_rows(db, product_id):
    from app.models.product import ProductCostHistory
    return [
        h for h in db.query(ProductCostHistory)
        .filter(ProductCostHistory.product_id == product_id).all()
        if "freight" in (h.notes or "").lower()
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_single_line_full_receipt_lands_freight(db):
    """
    5 on hand @ $10. PO: 10 @ $20 + $50 freight -> adder $5/unit, landed $25.
    Moving avg = (5x10 + 10x25) / 15 = $20.00 exactly.
    Freight must NOT leak into the vendor source quote.
    """
    from app.models.product import Product
    from app.models.purchase_order import POLine

    vendor = _vendor(db)
    product = _product(db, initial_cost=10.0, qty_on_hand=5)
    src = _add_vendor_source(db, product, vendor, vendor_cost=20.0)
    _, (line,) = _sent_po(db, vendor, freight=50.0, lines=[(product, 20.0, 10)])
    db.commit()

    _receive(db, vendor, {line.id: 10})

    p = _fresh(db, Product, product.id)
    assert p.cost == pytest.approx(20.0, abs=0.005), (
        f"(5x10 + 10x(20+5))/15 should be 20.00, got {p.cost}"
    )
    assert p.last_cost == pytest.approx(25.0, abs=0.005)
    assert p.cost_source == "receipt"
    assert p.qty_on_hand == 15

    ln = _fresh(db, POLine, line.id)
    assert ln.landed_cost_per_unit == pytest.approx(25.0, abs=0.005)

    # Vendor quote untouched by freight
    db.refresh(src)
    assert src.vendor_cost == pytest.approx(20.0, abs=0.005)

    # Cost history reflects the landed unit cost with a freight note
    rows = _freight_history_rows(db, product.id)
    assert rows, "expected a ProductCostHistory row noting freight"
    assert rows[-1].new_cost == pytest.approx(25.0, abs=0.005)
    assert rows[-1].old_cost == pytest.approx(20.0, abs=0.005)


def test_multi_line_freight_allocates_proportionally_by_value(db):
    """
    $100 freight. Line A: 10 @ $30 (value 300), line B: 5 @ $20 (value 100).
    adder_A = 100 x 30/400 = $7.50 ; adder_B = 100 x 20/400 = $5.00.
    Landed: A $37.50, B $25.00. Total landed freight = 10x7.5 + 5x5 = $100.
    """
    from app.models.product import Product
    from app.models.purchase_order import POLine

    vendor = _vendor(db)
    prod_a = _product(db)
    prod_b = _product(db)
    _, (line_a, line_b) = _sent_po(
        db, vendor, freight=100.0,
        lines=[(prod_a, 30.0, 10), (prod_b, 20.0, 5)],
    )
    db.commit()

    _receive(db, vendor, {line_a.id: 10, line_b.id: 5})

    pa = _fresh(db, Product, prod_a.id)
    pb = _fresh(db, Product, prod_b.id)
    assert pa.cost == pytest.approx(37.50, abs=0.005)
    assert pb.cost == pytest.approx(25.00, abs=0.005)

    la = _fresh(db, POLine, line_a.id)
    lb = _fresh(db, POLine, line_b.id)
    assert la.landed_cost_per_unit == pytest.approx(37.50, abs=0.005)
    assert lb.landed_cost_per_unit == pytest.approx(25.00, abs=0.005)

    # Conservation: freight landed across all received units == PO freight
    landed_freight = (
        (la.landed_cost_per_unit - la.unit_cost) * 10
        + (lb.landed_cost_per_unit - lb.unit_cost) * 5
    )
    assert landed_freight == pytest.approx(100.0, abs=0.01)


def test_partial_receipts_land_freight_fraction_then_remainder(db):
    """
    10 on hand @ $10. PO: 10 @ $20 + $50 freight (adder $5, landed $25).
    Receipt 1 (4 units): avg = (10x10 + 4x25)/14 = 200/14 = 14.2857
      -> only 4/10 of the freight ($20) has landed.
    Receipt 2 (6 units): avg = (14x14.2857 + 6x25)/20 = 350/20 = $17.50
      -> total inventory value 20 x 17.50 = $350
       = $100 opening + $200 goods + $50 freight (landed exactly once).
    """
    from app.models.product import Product

    vendor = _vendor(db)
    product = _product(db, initial_cost=10.0, qty_on_hand=10)
    _, (line,) = _sent_po(db, vendor, freight=50.0, lines=[(product, 20.0, 10)])
    db.commit()

    _receive(db, vendor, {line.id: 4})
    p = _fresh(db, Product, product.id)
    assert p.cost == pytest.approx(200.0 / 14.0, abs=0.005), (
        f"first half-receipt should land 4/10 of freight: got {p.cost}"
    )

    _receive(db, vendor, {line.id: 6})
    p = _fresh(db, Product, product.id)
    assert p.cost == pytest.approx(17.50, abs=0.005), (
        f"after both receipts the freight must land exactly once: got {p.cost}"
    )
    assert p.qty_on_hand == 20
    # No over-allocation: total value = opening 100 + goods 200 + freight 50
    assert p.cost * p.qty_on_hand == pytest.approx(350.0, abs=0.10)


def test_zero_freight_receipt_is_bit_for_bit_unchanged(db):
    """
    Regression vs pre-R3 behavior, mirroring the numbers of
    test_product_cost_semantic.test_receipt_moving_average_weighted_correctly:
    5 @ $10 on hand + receive 5 @ $14, NO freight -> $12.00, last_cost $14,
    landed_cost_per_unit stays NULL, no freight history row.
    """
    from app.models.product import Product
    from app.models.purchase_order import POLine

    vendor = _vendor(db)
    product = _product(db, initial_cost=10.0, qty_on_hand=5)
    _add_vendor_source(db, product, vendor, vendor_cost=10.0)
    _, (line,) = _sent_po(db, vendor, freight=0.0, lines=[(product, 14.0, 5)])
    db.commit()

    _receive(db, vendor, {line.id: 5})

    p = _fresh(db, Product, product.id)
    assert p.cost == pytest.approx(12.0, abs=0.005), (
        f"zero freight must reproduce the existing moving-avg result 12.0, got {p.cost}"
    )
    assert p.last_cost == pytest.approx(14.0, abs=0.005)

    ln = _fresh(db, POLine, line.id)
    assert ln.landed_cost_per_unit is None, (
        "landed_cost_per_unit must stay NULL on zero-freight receipts"
    )
    assert _freight_history_rows(db, product.id) == []


def test_zero_cost_lines_share_freight_by_qty(db):
    """
    All-zero-cost PO (no line value to weight by): $60 freight over
    10 + 20 = 30 ordered units -> $2.00/unit on every line.
    """
    from app.models.product import Product
    from app.models.purchase_order import POLine

    vendor = _vendor(db)
    prod_a = _product(db)
    prod_b = _product(db)
    _, (line_a, line_b) = _sent_po(
        db, vendor, freight=60.0,
        lines=[(prod_a, 0.0, 10), (prod_b, 0.0, 20)],
    )
    db.commit()

    _receive(db, vendor, {line_a.id: 10, line_b.id: 20})

    pa = _fresh(db, Product, prod_a.id)
    pb = _fresh(db, Product, prod_b.id)
    assert pa.cost == pytest.approx(2.0, abs=0.005)
    assert pb.cost == pytest.approx(2.0, abs=0.005)

    la = _fresh(db, POLine, line_a.id)
    lb = _fresh(db, POLine, line_b.id)
    assert la.landed_cost_per_unit == pytest.approx(2.0, abs=0.005)
    assert lb.landed_cost_per_unit == pytest.approx(2.0, abs=0.005)


def test_mixed_po_zero_cost_line_carries_no_value_weight(db):
    """
    Mixed PO ($40 freight): A 10 @ $20 (value 200), B 5 @ $0 (value 0).
    Value-weighted: all freight lands on A (adder $4, landed $24); the
    zero-cost line gets no adder and stays bit-for-bit legacy (cost untouched,
    landed_cost_per_unit NULL). Total landed = 10 x 4 = $40, conserved.
    """
    from app.models.product import Product
    from app.models.purchase_order import POLine

    vendor = _vendor(db)
    prod_a = _product(db)
    prod_b = _product(db)
    _, (line_a, line_b) = _sent_po(
        db, vendor, freight=40.0,
        lines=[(prod_a, 20.0, 10), (prod_b, 0.0, 5)],
    )
    db.commit()

    _receive(db, vendor, {line_a.id: 10, line_b.id: 5})

    pa = _fresh(db, Product, prod_a.id)
    pb = _fresh(db, Product, prod_b.id)
    assert pa.cost == pytest.approx(24.0, abs=0.005)
    assert pb.cost == pytest.approx(0.0, abs=0.005)

    la = _fresh(db, POLine, line_a.id)
    lb = _fresh(db, POLine, line_b.id)
    assert la.landed_cost_per_unit == pytest.approx(24.0, abs=0.005)
    assert lb.landed_cost_per_unit is None


def test_landed_cost_per_unit_written_and_stable_across_receipts(db):
    """
    PO: 8 @ $12.50 + $20 freight -> adder $2.50, landed $15.00. The adder is
    per ORDERED unit, so the landed value is identical on every partial
    receipt (the cumulative weighted value IS $15.00 throughout).
    """
    from app.models.product import Product
    from app.models.purchase_order import POLine

    vendor = _vendor(db)
    product = _product(db)
    _, (line,) = _sent_po(db, vendor, freight=20.0, lines=[(product, 12.50, 8)])
    db.commit()

    _receive(db, vendor, {line.id: 3})
    ln = _fresh(db, POLine, line.id)
    assert ln.landed_cost_per_unit == pytest.approx(15.0, abs=0.005)

    _receive(db, vendor, {line.id: 5})
    ln = _fresh(db, POLine, line.id)
    assert ln.landed_cost_per_unit == pytest.approx(15.0, abs=0.005)

    p = _fresh(db, Product, product.id)
    assert p.cost == pytest.approx(15.0, abs=0.005)
    assert p.qty_on_hand == 8
