"""
tests/test_phase2_markup_tier_admin.py
=========================================
MASTER_PLAN §23.3 Phase 2 #5 — markup-tier admin UI.

Closes the gap the pricing-preview route's own docstring/UI copy flagged:
"Editing the tiers and the Activate toggle are pending Backend save routes."
New CRUD routes under /settings/pricing/tiers/* (create/update/delete) +
/settings/pricing/toggle-active (the markup_tiers_active setting flip),
following the SAME inline-CRUD style /settings/locations already uses (no
dedicated service — MarkupTier is a tiny 4-field lookup row; PricingService
is documented "stateless math — does NOT write to the database", so CRUD
does not belong there).

NOTE: app startup seeds 4 default tiers (seed_markup_tiers, idempotent —
skips if the table is non-empty) — every test here accounts for that
baseline rather than assuming an empty table.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
from app.main import app
from app.models.pricing import MarkupTier
from app.models.setting import Setting
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Create ──────────────────────────────────────────────────────────────────

def test_create_tier(client, db):
    before = db.query(MarkupTier).count()
    r = client.post("/settings/pricing/tiers", data={
        "min_cost": "100", "max_cost": "200", "markup_pct": "25", "sort_order": "99",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "saved=1" in r.headers["location"]
    assert "tab=pricing" in r.headers["location"]

    db.expire_all()
    assert db.query(MarkupTier).count() == before + 1
    tier = db.query(MarkupTier).filter(MarkupTier.sort_order == 99).first()
    assert tier is not None
    assert tier.min_cost == 100.0 and tier.max_cost == 200.0 and tier.markup_pct == 25.0
    assert tier.is_active is True


def test_create_tier_with_no_max_cost_is_unbounded(client, db):
    r = client.post("/settings/pricing/tiers", data={
        "min_cost": "500", "max_cost": "", "markup_pct": "20", "sort_order": "98",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    tier = db.query(MarkupTier).filter(MarkupTier.sort_order == 98).first()
    assert tier.max_cost is None


def test_create_tier_missing_markup_pct_flashes_error(client, db):
    before = db.query(MarkupTier).count()
    r = client.post("/settings/pricing/tiers", data={
        "min_cost": "0", "max_cost": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    db.expire_all()
    assert db.query(MarkupTier).count() == before   # nothing created


def test_create_tier_max_not_greater_than_min_flashes_error(client, db):
    before = db.query(MarkupTier).count()
    r = client.post("/settings/pricing/tiers", data={
        "min_cost": "100", "max_cost": "50", "markup_pct": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    db.expire_all()
    assert db.query(MarkupTier).count() == before


# ── Update ──────────────────────────────────────────────────────────────────

def test_update_tier(client, db):
    tier = MarkupTier(min_cost=1000, max_cost=2000, markup_pct=15.0, sort_order=97)
    db.add(tier); db.commit(); db.refresh(tier)

    r = client.post(f"/settings/pricing/tiers/{tier.id}/update", data={
        "min_cost": "1000", "max_cost": "2500", "markup_pct": "18",
        "sort_order": "97", "is_active": "1",
    }, follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]

    db.expire_all()
    updated = db.get(MarkupTier, tier.id)
    assert updated.max_cost == 2500.0 and updated.markup_pct == 18.0


def test_update_tier_unchecking_active_deactivates(client, db):
    tier = MarkupTier(min_cost=3000, max_cost=None, markup_pct=12.0, sort_order=96, is_active=True)
    db.add(tier); db.commit(); db.refresh(tier)

    r = client.post(f"/settings/pricing/tiers/{tier.id}/update", data={
        "min_cost": "3000", "max_cost": "", "markup_pct": "12", "sort_order": "96",
        # is_active omitted — an unchecked checkbox never appears in form data
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(MarkupTier, tier.id).is_active is False


def test_update_nonexistent_tier_flashes_error(client, db):
    r = client.post("/settings/pricing/tiers/999999/update", data={
        "min_cost": "0", "markup_pct": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


# ── Delete ──────────────────────────────────────────────────────────────────

def test_delete_tier(client, db):
    tier = MarkupTier(min_cost=4000, max_cost=None, markup_pct=10.0, sort_order=95)
    db.add(tier); db.commit(); db.refresh(tier)
    tier_id = tier.id

    r = client.post(f"/settings/pricing/tiers/{tier_id}/delete", follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    db.expire_all()
    assert db.get(MarkupTier, tier_id) is None   # hard-deleted — no FK from Product


def test_delete_nonexistent_tier_is_a_no_op_not_an_error(client, db):
    r = client.post("/settings/pricing/tiers/999999/delete", follow_redirects=False)
    assert r.status_code == 303   # doesn't 500


# ── Activate-grid toggle ────────────────────────────────────────────────────

def test_toggle_active_on(client, db):
    r = client.post("/settings/pricing/toggle-active", data={"active": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    db.expire_all()
    row = db.query(Setting).filter(Setting.key == "markup_tiers_active").first()
    assert row.value == "true"


def test_toggle_active_off(client, db):
    client.post("/settings/pricing/toggle-active", data={"active": "1"})
    r = client.post("/settings/pricing/toggle-active", data={}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    row = db.query(Setting).filter(Setting.key == "markup_tiers_active").first()
    assert row.value == "false"


def test_toggle_active_actually_drives_pricing(client, db):
    """End-to-end: the toggle this route flips is the SAME setting
    PricingService.markup_tiers_active() reads."""
    from app.services.pricing_service import PricingService
    client.post("/settings/pricing/toggle-active", data={"active": "1"})
    db.expire_all()
    assert PricingService(db).markup_tiers_active() is True

    client.post("/settings/pricing/toggle-active", data={})
    db.expire_all()
    assert PricingService(db).markup_tiers_active() is False


# ── Settings page renders the admin table ───────────────────────────────────

def test_settings_pricing_tab_renders_tier_table(client, db):
    r = client.get("/settings/?tab=pricing")
    assert r.status_code == 200
    assert "Cost-bracket Markup Tiers" in r.text
    assert "Activate grid" in r.text
    assert 'action="/settings/pricing/tiers"' in r.text   # the Add form
