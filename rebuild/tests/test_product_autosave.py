"""
tests/test_product_autosave.py
==============================
Owner feedback (2026-06-06): the product detail page *said* "All changes saved"
but edits were silently lost — the indicator was cosmetic and there was no real
auto-save. The fix wires the Info-tab form to POST the whole form (debounced) to
``/products/{id}/autosave``, which persists via the same ``update_product`` path
as the manual Save button and drives an honest save-state pill off the HTTP status.

These tests lock the contract:
  * a successful autosave actually COMMITS (the reported bug);
  * the whole-form payload doesn't blank untouched fields;
  * an invalid payload returns non-200 AND persists nothing (so the pill can
    honestly show "Save failed" instead of a false "saved");
  * SKU is never mutated by autosave (update_product ignores it), so a debounced
    save mid-edit can't corrupt the identity key.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.product import Product
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


def _seed(db) -> int:
    p = Product(
        sku="JAKS-AUTOSAVE-1",
        title="Autosave Test Part",
        is_active=True,
        cost=10.0,
        reorder_point=5,
        internal_notes="original notes",
        has_core=False,
    )
    db.add(p)
    db.commit()
    return p.id


def _form(**overrides) -> dict:
    """The full form the browser sends (FormData of every named field), so the
    autosave round-trip mirrors reality and doesn't blank untouched columns."""
    base = {
        "sku": "JAKS-AUTOSAVE-1",
        "title": "Autosave Test Part",
        "description": "",
        "brand": "",
        "manufacturer": "",
        "cost": "10.00",
        "markup_pct": "",
        "price_override": "",
        "category_id": "",
        "reorder_point": "5",
        "notes": "",
        "internal_notes": "original notes",
        "supplier_warranty_type": "parts_only",
        "manufacturer_warranty_months": "0",
        "supplier_warranty_months": "0",
        "jaks_warranty_months": "0",
        "warranty_percentage": "10.0",
    }
    base.update(overrides)
    return base


def test_autosave_persists_edits(client, db_session):
    pid = _seed(db_session)
    resp = client.post(
        f"/products/{pid}/autosave",
        data=_form(reorder_point="12", internal_notes="changed via autosave"),
    )
    assert resp.status_code == 200, resp.text

    # Re-read from a fresh session: the change must be on disk, not just in the UI.
    db_session.expire_all()
    p = db_session.query(Product).get(pid)
    assert p.reorder_point == 12
    assert p.internal_notes == "changed via autosave"
    # Untouched fields survive the whole-form save.
    assert p.title == "Autosave Test Part"


def test_autosave_invalid_payload_saves_nothing(client, db_session):
    """A bad core-charge combo (customer < vendor) must be rejected — and because
    update_product validates before mutating, NOTHING in the same payload persists.
    This is what lets the pill honestly show "Save failed" instead of a false save."""
    pid = _seed(db_session)
    resp = client.post(
        f"/products/{pid}/autosave",
        data=_form(
            has_core="on",
            vendor_core_charge="50.00",
            customer_core_charge="10.00",
            reorder_point="99",
        ),
    )
    assert resp.status_code != 200  # 422 — surfaced as the red "Save failed" pill

    db_session.expire_all()
    p = db_session.query(Product).get(pid)
    assert p.reorder_point == 5      # the co-submitted change did NOT sneak through
    assert p.has_core is False


def test_autosave_never_mutates_sku(client, db_session):
    """SKU is the identity key; update_product intentionally ignores it. A debounced
    autosave firing while the SKU field is mid-edit must not rewrite it."""
    pid = _seed(db_session)
    resp = client.post(
        f"/products/{pid}/autosave",
        data=_form(sku="JAKS-DIFFERENT", title="Renamed"),
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    p = db_session.query(Product).get(pid)
    assert p.sku == "JAKS-AUTOSAVE-1"   # unchanged
    assert p.title == "Renamed"          # other editable fields still save


def test_detail_page_wires_autosave_and_single_adjust_card(client, db_session):
    """The detail template must render with the real autosave wiring, and the
    Adjust Inventory card must appear exactly once and only inside the Info tab —
    not as the old global header action that showed on every tab."""
    pid = _seed(db_session)
    r = client.get(f"/products/{pid}")
    assert r.status_code == 200
    html = r.text
    # Real auto-save is wired to the honest endpoint.
    assert 'id="product-info-form"' in html
    assert f"/products/{pid}/autosave" in html
    # Exactly one Adjust Inventory card, and the old global header trigger is gone.
    assert html.count('id="adjust-inventory"') == 1
    assert "open-adjust-inventory" not in html
    # Each "Add …" form carries the unsaved-entry guard for its key field.
    assert 'data-guard-field="vendor_id"' in html
    assert 'data-guard-field="ref_number"' in html
    assert 'data-guard-field="sku"' in html
