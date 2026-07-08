"""
tests/test_product_list_sort.py
===============================
Owner request (2026-06-01): the Products list must sort by Vendor and by Category
(hierarchical), in addition to the default SKU order.

These tests lock the ordering contract for ``GET /products/?sort=``:
  * ``sku``      → A→Z by SKU (default; unknown values normalize to this)
  * ``vendor``   → A→Z by preferred-vendor name, products with no vendor LAST
  * ``category`` → by full category path (Major Group → Category → Sub-category),
                   so sub-categories nest under their parent; no-category LAST
SKU is the tiebreaker in every mode.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.product import Product, ProductCategory, ProductVendorSource
from app.models.vendor import Vendor
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db_session():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    return TestClient(app, follow_redirects=False)


def _row_skus(html: str) -> list[str]:
    """SKUs in table order — the SKU cell anchor carries the font-mono class."""
    return re.findall(r'href="/products/\d+"\s+class="font-mono[^>]*>([^<]+)</a>', html)


def _seed(db):
    """
    Three products with deliberately crossed SKU / vendor / category orders so each
    sort produces a distinct, unambiguous sequence.

      P_z: SKU ZZZ-1 · vendor Alpha    · category Engine → Pistons (sub-category)
      P_a: SKU AAA-1 · vendor Zeta     · category Belts            (top-level)
      P_m: SKU MMM-1 · (no vendor)     · (no category)
    """
    v_alpha = Vendor(name="Alpha Supply", vendor_code="ALPH", is_active=True)
    v_zeta = Vendor(name="Zeta Parts", vendor_code="ZETA", is_active=True)
    db.add_all([v_alpha, v_zeta])

    engine = ProductCategory(name="Engine", level=1)
    belts = ProductCategory(name="Belts", level=1)
    db.add_all([engine, belts])
    db.commit()
    pistons = ProductCategory(name="Pistons", level=2, parent_id=engine.id)
    db.add(pistons)
    db.commit()

    p_z = Product(sku="ZZZ-1", title="Z part", is_active=True, category_id=pistons.id)
    p_a = Product(sku="AAA-1", title="A part", is_active=True, category_id=belts.id)
    p_m = Product(sku="MMM-1", title="M part", is_active=True)
    db.add_all([p_z, p_a, p_m])
    db.commit()

    db.add_all([
        ProductVendorSource(product_id=p_z.id, vendor_id=v_alpha.id,
                            is_preferred=True, is_active=True, vendor_cost=1.0),
        ProductVendorSource(product_id=p_a.id, vendor_id=v_zeta.id,
                            is_preferred=True, is_active=True, vendor_cost=1.0),
    ])
    db.commit()


def test_default_sort_is_sku(client, db_session):
    _seed(db_session)
    assert _row_skus(client.get("/products/").text) == ["AAA-1", "MMM-1", "ZZZ-1"]


def test_unknown_sort_falls_back_to_sku(client, db_session):
    _seed(db_session)
    assert _row_skus(client.get("/products/?sort=zzz").text) == ["AAA-1", "MMM-1", "ZZZ-1"]


def test_sort_by_vendor_nulls_last(client, db_session):
    _seed(db_session)
    # Alpha < Zeta by vendor name; the no-vendor product sorts last.
    assert _row_skus(client.get("/products/?sort=vendor").text) == ["ZZZ-1", "AAA-1", "MMM-1"]


def test_sort_by_category_hierarchical_nulls_last(client, db_session):
    _seed(db_session)
    # "Belts" < "Engine → Pistons" by full path; the no-category product sorts last.
    assert _row_skus(client.get("/products/?sort=category").text) == ["AAA-1", "ZZZ-1", "MMM-1"]
