"""
tests/test_product_merge.py
============================
ProductService.merge_products — re-points every table that can reference a
product onto the keeper, folds the duplicate's on-hand qty through the normal
ledger path, and retires the duplicate via the existing supersede shape
(status=SUPERSEDED, superseded_by_id, is_active=False). Never a hard delete.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.constants import ProductStatus
from app.models.product import (
    CrossReference, Product, ProductApplication, ProductCategory,
    ProductVendorSource, SuggestedSell,
)
from app.models.vendor import Vendor
from app.services.product_service import ProductService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    SessionLocal = activate(fresh_engine())
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _vendor(db, code="PAI", digit="9"):
    v = Vendor(name=f"{code} Supply", vendor_code=code, vendor_number=digit)
    db.add(v); db.commit(); db.refresh(v)
    return v


def _category(db, name="Cylinder Heads", code="CYL"):
    c = ProductCategory(name=name, code=code, level=1, is_active=True)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, sku, *, qty=0, category=None, **kw) -> Product:
    p = Product(sku=sku, title=kw.pop("title", sku), category_id=category.id if category else None,
                qty_on_hand=qty, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── guards ───────────────────────────────────────────────────────────────────

def test_cannot_merge_product_into_itself(db):
    p = _product(db, "JAKS-1")
    with pytest.raises(ValueError, match="itself"):
        ProductService(db).merge_products(p.id, p.id)


def test_cannot_merge_into_an_already_superseded_keeper(db):
    keeper = _product(db, "JAKS-KEEP")
    dup = _product(db, "JAKS-DUP")
    keeper.status = ProductStatus.SUPERSEDED
    db.commit()
    with pytest.raises(ValueError, match="itself been merged"):
        ProductService(db).merge_products(keeper.id, dup.id)


def test_cannot_merge_an_already_merged_duplicate_again(db):
    keeper = _product(db, "JAKS-KEEP")
    dup = _product(db, "JAKS-DUP")
    other = _product(db, "JAKS-OTHER")
    ProductService(db).merge_products(keeper.id, dup.id)
    with pytest.raises(ValueError, match="already merged"):
        ProductService(db).merge_products(other.id, dup.id)


def test_new_sku_collision_with_unrelated_product_raises_before_any_mutation(db):
    keeper = _product(db, "JAKS-KEEP")
    dup = _product(db, "JAKS-DUP")
    _product(db, "JAKS-TAKEN")
    with pytest.raises(ValueError, match="already used by product"):
        ProductService(db).merge_products(keeper.id, dup.id, new_sku="JAKS-TAKEN")
    db.refresh(keeper); db.refresh(dup)
    assert keeper.sku == "JAKS-KEEP"
    assert dup.sku == "JAKS-DUP"
    assert dup.superseded_by_id is None


# ── happy path ───────────────────────────────────────────────────────────────

def test_basic_merge_retires_duplicate_and_sums_qty(db):
    keeper = _product(db, "JAKS-KEEP", qty=1)
    dup = _product(db, "JAKS-DUP", qty=4)

    result = ProductService(db).merge_products(keeper.id, dup.id)

    db.refresh(keeper); db.refresh(dup)
    assert keeper.qty_on_hand == 5
    assert dup.qty_on_hand == 0
    assert dup.status == ProductStatus.SUPERSEDED
    assert dup.superseded_by_id == keeper.id
    assert dup.is_active is False
    assert dup.sku == "JAKS-DUP-MERGED-" + str(keeper.id)
    assert result["qty_absorbed"] == 4
    assert result["keeper_sku"] == "JAKS-KEEP"


def test_merge_can_rename_keeper_to_the_duplicates_old_sku(db):
    """The real owner scenario: the duplicate holds the SKU the owner actually
    wants — the duplicate's SKU must be vacated before the keeper claims it."""
    keeper = _product(db, "JAKS-JAKS-M2239250CGI", qty=4)
    dup = _product(db, "JAKS-2239250S6", qty=1)

    result = ProductService(db).merge_products(keeper.id, dup.id, new_sku="JAKS-2239250S6")

    db.refresh(keeper); db.refresh(dup)
    assert keeper.sku == "JAKS-2239250S6"
    assert keeper.qty_on_hand == 5
    assert dup.sku == "JAKS-2239250S6-MERGED-" + str(keeper.id)
    assert dup.status == ProductStatus.SUPERSEDED
    assert result["duplicate_old_sku"] == "JAKS-2239250S6"


def test_merge_moves_vendor_source_cross_ref_and_application(db):
    v = _vendor(db)
    cat = _category(db)
    keeper = _product(db, "JAKS-KEEP", category=cat)
    dup = _product(db, "JAKS-DUP", category=cat)
    db.add(ProductVendorSource(product_id=dup.id, vendor_id=v.id, vendor_part_number="X1",
                                is_preferred=True, is_active=True))
    db.add(CrossReference(product_id=dup.id, ref_number="OEM-1"))
    db.add(ProductApplication(product_id=dup.id, engine_make="CAT", engine_model="C15", cpl="1234"))
    db.commit()

    ProductService(db).merge_products(keeper.id, dup.id)

    db.refresh(keeper)
    assert len(keeper.vendor_sources) == 1
    assert keeper.vendor_sources[0].is_preferred is True
    assert {x.ref_number for x in keeper.cross_references} == {"OEM-1"}
    assert len(keeper.applications) == 1
    assert keeper.applications[0].engine_make == "CAT"


# ── uniqueness / self-reference edge cases ──────────────────────────────────

def test_merge_deactivates_conflicting_vendor_source_instead_of_colliding(db):
    """Both products actively source from the SAME vendor — moving the
    duplicate's source onto the keeper as-is would violate the partial unique
    index on (product_id, vendor_id) WHERE is_active. It must be deactivated,
    not dropped, and the keeper's own preferred source must survive."""
    v = _vendor(db)
    keeper = _product(db, "JAKS-KEEP")
    dup = _product(db, "JAKS-DUP")
    db.add(ProductVendorSource(product_id=keeper.id, vendor_id=v.id, vendor_part_number="KEEP-1",
                                is_preferred=True, is_active=True))
    db.add(ProductVendorSource(product_id=dup.id, vendor_id=v.id, vendor_part_number="DUP-1",
                                is_preferred=True, is_active=True))
    db.commit()

    ProductService(db).merge_products(keeper.id, dup.id)

    db.refresh(keeper)
    sources = {s.vendor_part_number: s for s in keeper.vendor_sources}
    assert set(sources) == {"KEEP-1", "DUP-1"}
    assert sources["KEEP-1"].is_active is True and sources["KEEP-1"].is_preferred is True
    assert sources["DUP-1"].is_active is False and sources["DUP-1"].is_preferred is False


def test_merge_drops_duplicate_cross_ref_that_keeper_already_has(db):
    keeper = _product(db, "JAKS-KEEP")
    dup = _product(db, "JAKS-DUP")
    db.add(CrossReference(product_id=keeper.id, ref_number="SHARED-1"))
    db.add(CrossReference(product_id=dup.id, ref_number="SHARED-1"))
    db.commit()

    ProductService(db).merge_products(keeper.id, dup.id)

    db.refresh(keeper)
    assert [x.ref_number for x in keeper.cross_references] == ["SHARED-1"]


def test_merge_drops_suggested_sell_that_would_self_reference(db):
    """Duplicate suggests the keeper as an add-on — after remap that would be
    the keeper suggesting itself, which must be dropped, not created."""
    keeper = _product(db, "JAKS-KEEP")
    dup = _product(db, "JAKS-DUP")
    other = _product(db, "JAKS-OTHER")
    db.add(SuggestedSell(product_id=dup.id, suggested_product_id=keeper.id))
    db.add(SuggestedSell(product_id=dup.id, suggested_product_id=other.id))
    db.commit()

    ProductService(db).merge_products(keeper.id, dup.id)

    rows = db.query(SuggestedSell).filter(SuggestedSell.product_id == keeper.id).all()
    assert [r.suggested_product_id for r in rows] == [other.id]


def test_merge_repoints_po_line_and_invoice_line(db):
    from app.models.purchase_order import POLine, PurchaseOrder
    from app.models.invoice import Invoice, InvoiceLine
    from app.models.vendor import Vendor as V
    from app.models.customer import Customer

    v = _vendor(db, code="IMB", digit="3")
    cust = Customer(company_name="Test Customer")
    db.add(cust); db.commit(); db.refresh(cust)

    keeper = _product(db, "JAKS-KEEP")
    dup = _product(db, "JAKS-DUP")

    po = PurchaseOrder(po_number="PO-TEST-0001", vendor_id=v.id, status="draft")
    db.add(po); db.commit(); db.refresh(po)
    db.add(POLine(po_id=po.id, product_id=dup.id, description="x", qty_ordered=2, unit_cost=10.0))

    inv = Invoice(invoice_number="INV-TEST-0001", customer_id=cust.id, status="draft")
    db.add(inv); db.commit(); db.refresh(inv)
    db.add(InvoiceLine(invoice_id=inv.id, product_id=dup.id, description="x", qty=1, unit_price=20.0))
    db.commit()

    ProductService(db).merge_products(keeper.id, dup.id)

    assert db.query(POLine).filter(POLine.product_id == keeper.id).count() == 1
    assert db.query(InvoiceLine).filter(InvoiceLine.product_id == keeper.id).count() == 1
    assert db.query(POLine).filter(POLine.product_id == dup.id).count() == 0
    assert db.query(InvoiceLine).filter(InvoiceLine.product_id == dup.id).count() == 0
