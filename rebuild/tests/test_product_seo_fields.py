"""
tests/test_product_seo_fields.py
================================
SEO / marketplace fields (seo_title, seo_description, search_keywords) — the
spine of the future Shopify / eBay export-sync (owner request 2026-06-11).

Locks three contracts:
  1. /products/export.csv carries the three SEO columns + per-product values.
  2. The detail Info-tab form persists SEO edits through product_update
     (fields whitelisted in _parse_product_form AND ProductService.update_product).
  3. A form that does NOT carry the SEO inputs (quick-create, new) can never
     blank stored SEO values on a later update — the parser only includes the
     keys when the posting form has them.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine
from app.main import app


@pytest.fixture()
def db():
    Session = activate(fresh_engine())
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(db):
    return TestClient(app, follow_redirects=False)


def _product(db, **kw):
    from app.constants import ProductStatus
    from app.models.product import Product
    defaults = dict(
        sku="SEO-1", title="ISX15 Turbo Seal Kit", description="Seal kit",
        cost=10.0, status=ProductStatus.ACTIVE, is_active=True,
        seo_title="ISX15 Turbo Seal Kit | Cummins",
        seo_description="OEM-quality ISX15 turbo inlet/outlet seal kit.",
        search_keywords="cummins, isx15, turbo, seal",
    )
    defaults.update(kw)
    p = Product(**defaults)
    db.add(p)
    db.commit()
    return p


# ---------------------------------------------------------------------------
# 1 — export carries the SEO columns
# ---------------------------------------------------------------------------

def test_export_csv_includes_seo_columns(client, db):
    _product(db)
    r = client.get("/products/export.csv")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    header, data = rows[0], rows[1]
    for col in ("seo_title", "seo_description", "search_keywords"):
        assert col in header, f"export header missing {col}"
    row = dict(zip(header, data))
    assert row["seo_title"] == "ISX15 Turbo Seal Kit | Cummins"
    assert row["seo_description"] == "OEM-quality ISX15 turbo inlet/outlet seal kit."
    assert row["search_keywords"] == "cummins, isx15, turbo, seal"


# ---------------------------------------------------------------------------
# 2 — detail form persists SEO edits
# ---------------------------------------------------------------------------

def _full_form(p, **overrides):
    """Minimal valid product_update payload mirroring the detail Info form."""
    form = {
        "sku": p.sku, "title": p.title, "description": p.description,
        "cost": str(p.cost), "reorder_point": "0",
        "vendor_core_charge": "0", "customer_core_charge": "0",
        "manufacturer_warranty_months": "0", "supplier_warranty_months": "0",
        "jaks_warranty_months": "0", "warranty_percentage": "10",
        "seo_title": p.seo_title, "seo_description": p.seo_description,
        "search_keywords": p.search_keywords,
    }
    form.update(overrides)
    return form


def test_update_persists_seo_fields(client, db):
    p = _product(db)
    r = client.post(f"/products/{p.id}", data=_full_form(
        p,
        seo_title="New SEO Title",
        seo_description="New meta description for marketplaces.",
        search_keywords="new, tags",
    ))
    assert r.status_code == 303
    db.expire_all()
    from app.models.product import Product
    fresh = db.query(Product).get(p.id)
    assert fresh.seo_title == "New SEO Title"
    assert fresh.seo_description == "New meta description for marketplaces."
    assert fresh.search_keywords == "new, tags"


# ---------------------------------------------------------------------------
# 3 — forms WITHOUT the SEO inputs never blank stored values
# ---------------------------------------------------------------------------

def test_form_without_seo_fields_does_not_blank_values(client, db):
    p = _product(db)
    form = _full_form(p)
    del form["seo_title"], form["seo_description"], form["search_keywords"]
    r = client.post(f"/products/{p.id}", data=form)
    assert r.status_code == 303
    db.expire_all()
    from app.models.product import Product
    fresh = db.query(Product).get(p.id)
    assert fresh.seo_title == "ISX15 Turbo Seal Kit | Cummins"
    assert fresh.seo_description == "OEM-quality ISX15 turbo inlet/outlet seal kit."
    assert fresh.search_keywords == "cummins, isx15, turbo, seal"


# ---------------------------------------------------------------------------
# 4 — full import fills SEO fields from the feed when the columns are present
#     (Shopify exports carry "SEO Title"/"SEO Description" natively; the JAKS
#     native scraper format added optional seo_title/seo_description 2026-06-12)
# ---------------------------------------------------------------------------

def test_full_import_fills_seo_from_feed(db):
    from app.models.vendor import Vendor
    from app.models.product import ProductVendorSource
    from app.services.product_import_service import ProductImportService

    db.add(Vendor(name="PAI Industries", vendor_code="PAI",
                  vendor_number="9", is_active=True))
    db.commit()

    header = ["Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags",
              "Variant SKU", "Variant Price", "Status",
              "SEO Title", "SEO Description"]
    row = ["jaks-pai-9", "Turbo Seal Kit",
           "<p><strong>PAI Part #:</strong> 999</p>", "JAKS", "GASKETS",
           "CAT", "JAKS-PAI-999", "25.00", "active",
           "CAT C15 Turbo Seal Kit | 2-Yr Warranty",
           "OEM-quality turbo seal kit for CAT C15 engines."]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerow(row)

    ProductImportService(db, None).full_import(buf.getvalue(), dry_run=False)

    src = (db.query(ProductVendorSource)
           .filter(ProductVendorSource.vendor_part_number == "999").first())
    assert src is not None, "import should create the product + vendor source"
    p = src.product
    assert p.seo_title == "CAT C15 Turbo Seal Kit | 2-Yr Warranty"
    assert p.seo_description == "OEM-quality turbo seal kit for CAT C15 engines."
