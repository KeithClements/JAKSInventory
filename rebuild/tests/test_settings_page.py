"""
tests/test_settings_page.py
===========================
The tabbed Settings page (@845d92f) + the pricing-grid PREVIEW.

  • GET /settings/ renders all six tabs (Company/Pricing/QuickBooks/Shopify/Tax/
    System) with their panels, and the in-flight "Documents & PDFs" card survives
    the tab reorg.
  • GET /settings/pricing/preview is a DRY RUN — it reports what the markup grid
    would do but must NEVER mutate any product's cost / markup / price override,
    regardless of the markup_tiers_active flag.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_settings_page.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app
from app.models.product import Product
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c   # startup seeds the admin user (preview is admin-gated) + the tier grid


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Tabbed settings page renders ─────────────────────────────────────────────

def test_settings_tabs_and_documents_card_render(client):
    html = client.get("/settings/").text
    # all six tab buttons + their panels render
    for tab in ("company", "pricing", "quickbooks", "shopify", "tax", "system"):
        assert f"tab === '{tab}'" in html, f"missing the {tab} tab"
    # the in-flight Documents & PDFs card survived the reorg, with its fields
    assert "Documents" in html
    assert 'name="document_show_logo"' in html
    # a QuickBooks-tab field renders (proves tab panels carry their fields)
    assert 'name="qbo_environment"' in html


# ── Pricing-grid preview is read-only ────────────────────────────────────────

def test_pricing_preview_does_not_mutate_products(client, db):
    # Products with no per-product markup → exactly the rows the grid would touch.
    p1 = Product(sku=f"PREV-{next(_seq)}", title="a", cost=60.0, markup_pct=None)
    p2 = Product(sku=f"PREV-{next(_seq)}", title="b", cost=10.0, markup_pct=None)
    db.add_all([p1, p2]); db.commit(); db.refresh(p1); db.refresh(p2)
    # Turn the grid ON so the preview WOULD compute different prices — the point is
    # it still changes nothing on disk.
    set_setting_value_db(db, "markup_tiers_active", "true"); db.commit()

    before = {p.id: (p.cost, p.markup_pct, p.price_override) for p in (p1, p2)}

    r = client.get("/settings/pricing/preview")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert "tiers" in body and "affected_count" in body and "sample" in body
    assert body["affected_count"] >= 2          # both unmarked products are candidates

    # Read-only: nothing on the products moved.
    db.expire_all()
    for pid, (cost, markup, override) in before.items():
        p = db.query(Product).filter(Product.id == pid).first()
        assert p.cost == cost
        assert p.markup_pct == markup
        assert p.price_override == override
