"""
tests/test_vendor_list_tabs.py
==============================
Regression guard for the Vendor List filter-tab contract and the vendor
activate / deactivate lifecycle UI.

Background (owner-reported gap, fixed 2026-05-31): the template once shipped tabs
slugged ``open_pos`` / ``credits`` (labels "Active POs" / "Open Credits") that the
router never honored — the ``counts`` dict had no such keys and the router mapped
those slugs to the default ``active`` filter. The net effect: deactivated vendors
were unreachable from the UI, and two tabs always read 0.

These tests lock the template tabs to what ``app/routers/vendors.py`` actually
computes (``active`` / ``inactive`` / ``all``) and verify the detail page can both
deactivate an active vendor and reactivate an inactive one (the ``/reactivate``
route existed but had no button).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.vendor import Vendor
from tests.conftest import fresh_engine, activate


@pytest.fixture()
def db_session():
    """Module-isolated in-memory DB, re-pointed via conftest.activate()."""
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    # follow_redirects=False so the 303 lifecycle contract is assertable directly.
    return TestClient(app, follow_redirects=False)


def _seed(db):
    """One active + one inactive vendor. Returns (active_id, inactive_id)."""
    active = Vendor(name="Active Diesel", vendor_code="ACTV", is_active=True)
    inactive = Vendor(name="Retired Diesel", vendor_code="RETD", is_active=False)
    db.add_all([active, inactive])
    db.commit()
    return active.id, inactive.id


# ── tab → router contract ─────────────────────────────────────────────────────

def test_default_list_shows_only_active(client, db_session):
    _seed(db_session)
    html = client.get("/vendors/").text
    assert "Active Diesel" in html
    assert "Retired Diesel" not in html


def test_inactive_tab_surfaces_inactive_vendor(client, db_session):
    """The whole point of the fix: deactivated vendors are reachable again."""
    _seed(db_session)
    html = client.get("/vendors/?tab=inactive").text
    assert "Retired Diesel" in html
    assert "Active Diesel" not in html


def test_all_tab_shows_both(client, db_session):
    _seed(db_session)
    html = client.get("/vendors/?tab=all").text
    assert "Active Diesel" in html
    assert "Retired Diesel" in html


def test_tabs_match_router_slugs_not_stale_ones(client, db_session):
    _seed(db_session)
    html = client.get("/vendors/").text
    # the three tabs the router actually honors
    assert "?tab=active" in html
    assert "?tab=inactive" in html
    assert "?tab=all" in html
    # stale tabs that silently fell through to the active filter must be gone
    assert "?tab=open_pos" not in html
    assert "?tab=credits" not in html
    assert "Active POs" not in html
    assert "Open Credits" not in html


# ── activate / deactivate lifecycle ───────────────────────────────────────────

def test_inactive_detail_offers_reactivate(client, db_session):
    _, inactive_id = _seed(db_session)
    html = client.get(f"/vendors/{inactive_id}").text
    assert f"/vendors/{inactive_id}/reactivate" in html
    assert "Reactivate vendor" in html
    assert "Deactivate vendor" not in html  # no deactivate path on an inactive vendor


def test_active_detail_offers_deactivate(client, db_session):
    active_id, _ = _seed(db_session)
    html = client.get(f"/vendors/{active_id}").text
    assert f"/vendors/{active_id}/deactivate" in html
    assert "Deactivate vendor" in html
    assert "Reactivate vendor" not in html


def test_reactivate_round_trip(client, db_session):
    _, inactive_id = _seed(db_session)
    resp = client.post(f"/vendors/{inactive_id}/reactivate")
    assert resp.status_code == 303
    db_session.expire_all()  # route committed on its own session — re-read
    v = db_session.query(Vendor).filter(Vendor.id == inactive_id).first()
    assert v.is_active is True
