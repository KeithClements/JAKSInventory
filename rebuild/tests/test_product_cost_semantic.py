"""
tests/test_product_cost_semantic.py
====================================
Regression guard for D-10 / §8N: product.cost = moving-weighted-average COGS.

The bug: _apply_moving_average_cost (R11) wrote the moving avg to product.cost,
then compare_and_record_cost_change ran in the same receipt and overwrote
product.cost with the vendor quote price when PO cost ≠ stored vendor_cost.

Owner ruled Option A (2026-06-01): product.cost is COGS only.  Vendor quote
price lives on ProductVendorSource.vendor_cost.  _sync_cost_from_preferred and
compare_and_record_cost_change must NOT write product.cost.
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
# Helpers
# ---------------------------------------------------------------------------

def _vendor(db):
    from app.models.purchase_order import Vendor
    n = next(_counter)
    v = Vendor(name=f"D10Vendor-{n}")
    db.add(v); db.flush()
    return v


def _product(db, *, initial_cost=0.0, qty_on_hand=0):
    from app.constants import ProductStatus
    from app.models.product import Product
    n = next(_counter)
    p = Product(
        sku=f"D10-{n:04d}", title=f"D10 Part {n}", description="test",
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


def _sent_po(db, vendor, product, *, unit_cost, qty):
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder, POLine
    n = next(_counter)
    po = PurchaseOrder(
        vendor_id=vendor.id, po_number=f"D10-PO-{n:04d}", status=POStatus.SENT,
    )
    db.add(po); db.flush()
    line = POLine(
        po_id=po.id, product_id=product.id, description="test",
        qty_ordered=qty, unit_cost=unit_cost,
    )
    db.add(line); db.flush()
    return po, line


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_receipt_cost_is_moving_average_not_vendor_quote(db):
    """
    Core D-10 regression guard.

    Setup: preferred vendor source at $10.  We receive a PO at $12 (negotiated).
    Before the fix: _sync_cost_from_preferred overwrote product.cost to $10.
    After the fix: product.cost = $12 (receipt price = moving avg from zero stock).
    """
    vendor = _vendor(db)
    product = _product(db, initial_cost=0.0, qty_on_hand=0)
    _add_vendor_source(db, product, vendor, vendor_cost=10.0)
    _, line = _sent_po(db, vendor, product, unit_cost=12.0, qty=5)
    db.commit()

    from app.services.po_service import POService
    from app.models.product import Product
    po_svc = POService(db, current_user_id=1)
    po_svc.create_receipt(
        vendor_id=vendor.id,
        po_line_quantities={line.id: 5},
        data={},
    )
    db.commit()

    p = db.query(Product).filter(Product.id == product.id).first()
    assert p.cost == pytest.approx(12.0, abs=0.01), (
        f"product.cost should be receipt moving-avg (12.0), "
        f"not the vendor quote (10.0). Got {p.cost}"
    )
    assert p.last_cost == pytest.approx(12.0, abs=0.01)
    assert p.cost_source == "receipt"


def test_receipt_moving_average_weighted_correctly(db):
    """Weighted avg: 5 units @ $10 existing + 5 @ $14 received = $12."""
    vendor = _vendor(db)
    product = _product(db, initial_cost=10.0, qty_on_hand=5)
    _add_vendor_source(db, product, vendor, vendor_cost=10.0)
    _, line = _sent_po(db, vendor, product, unit_cost=14.0, qty=5)
    db.commit()

    from app.services.po_service import POService
    from app.models.product import Product
    po_svc = POService(db, current_user_id=1)
    po_svc.create_receipt(
        vendor_id=vendor.id,
        po_line_quantities={line.id: 5},
        data={},
    )
    db.commit()

    p = db.query(Product).filter(Product.id == product.id).first()
    assert p.cost == pytest.approx(12.0, abs=0.01), (
        f"Weighted avg of 5@10 + 5@14 should be 12.0, got {p.cost}"
    )


def test_receipt_does_not_overwrite_vendor_quote(db):
    """
    §8N: ProductVendorSource.vendor_cost is the vendor's quoted/catalog price.
    A PO receipt at a different (often one-off negotiated) cost must NOT clobber
    the stored quote — it only records the divergence in ProductCostHistory.

    Setup: preferred vendor quote $10. Receive a PO at $12.
    Expect: source.vendor_cost stays $10, and a ProductCostHistory row captures
    the 10 -> 12 divergence.
    """
    from app.models.product import ProductCostHistory, ProductVendorSource

    vendor = _vendor(db)
    product = _product(db, initial_cost=0.0, qty_on_hand=0)
    src = _add_vendor_source(db, product, vendor, vendor_cost=10.0)
    _, line = _sent_po(db, vendor, product, unit_cost=12.0, qty=5)
    db.commit()

    from app.services.po_service import POService
    POService(db, current_user_id=1).create_receipt(
        vendor_id=vendor.id,
        po_line_quantities={line.id: 5},
        data={},
    )
    db.commit()

    src = db.query(ProductVendorSource).filter(ProductVendorSource.id == src.id).first()
    assert src.vendor_cost == pytest.approx(10.0, abs=0.005), (
        f"§8N: the vendor quote must stay $10 (the receipt price $12 is not the "
        f"catalog quote). Got {src.vendor_cost}"
    )

    rows = (
        db.query(ProductCostHistory)
        .filter(ProductCostHistory.product_id == product.id)
        .all()
    )
    assert any(
        r.old_cost == pytest.approx(10.0, abs=0.005)
        and r.new_cost == pytest.approx(12.0, abs=0.005)
        for r in rows
    ), "expected a ProductCostHistory row recording the 10 -> 12 receipt divergence"


def test_sync_cost_from_preferred_does_not_overwrite_moving_average(db):
    """_sync_cost_from_preferred must leave product.cost (moving avg) untouched."""
    vendor = _vendor(db)
    product = _product(db, initial_cost=12.0, qty_on_hand=5)
    _add_vendor_source(db, product, vendor, vendor_cost=10.0)
    db.commit()

    from app.services.product_service import ProductService
    from app.models.product import Product
    svc = ProductService(db, current_user_id=1)
    svc._sync_cost_from_preferred(product, new_cost=8.0, vendor_id=vendor.id)
    db.flush()

    p = db.query(Product).filter(Product.id == product.id).first()
    assert p.cost == pytest.approx(12.0, abs=0.01), (
        f"_sync_cost_from_preferred must NOT write product.cost. "
        f"Expected 12.0 (moving avg), got {p.cost}"
    )
    assert p.cost_source != "vendor", (
        "cost_source should not be set to 'vendor' by _sync_cost_from_preferred"
    )
