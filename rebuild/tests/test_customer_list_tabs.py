"""
tests/test_customer_list_tabs.py
================================
Regression guard for the Customer List filter-tab contract and the customer
activate / deactivate lifecycle UI.

Background (owner-reported gap, fixed 2026-05-31): ``app/routers/customers.py``
hard-filtered ``Customer.is_active == True`` on every list tab (all /
open_invoices / open_quotes / terms) and shipped no ``/reactivate`` route — so a
deactivated customer was unreachable from the UI and could never be turned back
on. This mirrors the Vendors fix from earlier the same day
(``tests/test_vendor_list_tabs.py``).

Design note locked here: unlike Vendors (whose ``all`` tab is a true
active+inactive union), the Customer List is the busy counter screen, so its four
operational tabs — including ``all`` — stay scoped to ACTIVE customers. A
dedicated ``inactive`` lifecycle tab surfaces deactivated accounts, and the
detail page can both deactivate an active customer and reactivate an inactive one.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.customer import Customer
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
    """One active + one inactive customer. Returns (active_id, inactive_id)."""
    active = Customer(company_name="Active Diesel Co", is_active=True)
    inactive = Customer(company_name="Retired Diesel Co", is_active=False)
    db.add_all([active, inactive])
    db.commit()
    return active.id, inactive.id


# ── tab → router contract ─────────────────────────────────────────────────────

def test_default_list_shows_only_active(client, db_session):
    _seed(db_session)
    html = client.get("/customers/").text
    assert "Active Diesel Co" in html
    assert "Retired Diesel Co" not in html


def test_all_tab_is_active_scoped(client, db_session):
    """Customers' `all` deliberately means all-ACTIVE (unlike vendors' union)."""
    _seed(db_session)
    html = client.get("/customers/?tab=all").text
    assert "Active Diesel Co" in html
    assert "Retired Diesel Co" not in html


def test_inactive_tab_surfaces_inactive_customer(client, db_session):
    """The whole point of the fix: deactivated customers are reachable again."""
    _seed(db_session)
    html = client.get("/customers/?tab=inactive").text
    assert "Retired Diesel Co" in html
    assert "Active Diesel Co" not in html


def test_operational_tabs_stay_active_scoped(client, db_session):
    """open_invoices / open_quotes / terms must never leak inactive customers."""
    _seed(db_session)
    for tab in ("open_invoices", "open_quotes", "terms"):
        html = client.get(f"/customers/?tab={tab}").text
        assert "Retired Diesel Co" not in html, f"inactive customer leaked into '{tab}'"


def test_unknown_tab_falls_back_to_all(client, db_session):
    """A bogus slug normalizes to `all` (active-only) — no crash, no inactive leak."""
    _seed(db_session)
    html = client.get("/customers/?tab=bogus").text
    assert "Active Diesel Co" in html
    assert "Retired Diesel Co" not in html


def test_inactive_tab_link_and_label_present(client, db_session):
    _seed(db_session)
    html = client.get("/customers/").text
    # the tab slugs the router actually honors
    assert "?tab=all" in html
    assert "?tab=open_invoices" in html
    assert "?tab=open_quotes" in html
    assert "?tab=terms" in html
    assert "?tab=inactive" in html
    assert "Inactive" in html  # the new tab label


def test_inactive_tab_count_reflects_full_dataset(client, db_session):
    """The Inactive count badge comes from the unfiltered dataset — it must read
    1 even on the default view, which renders no inactive rows."""
    _seed(db_session)  # exactly one inactive customer
    html = client.get("/customers/").text
    m = re.search(r'\?tab=inactive.*?tabular-nums">\s*(\d+)\s*<', html, re.S)
    assert m is not None, "Inactive tab + count badge not found in rendered list"
    assert m.group(1) == "1"


# ── activate / deactivate lifecycle ───────────────────────────────────────────

def test_inactive_detail_offers_reactivate(client, db_session):
    _, inactive_id = _seed(db_session)
    html = client.get(f"/customers/{inactive_id}").text
    assert f"/customers/{inactive_id}/reactivate" in html
    assert "Reactivate" in html
    # no deactivate path on an already-inactive customer
    assert f"/customers/{inactive_id}/deactivate" not in html


def test_active_detail_offers_deactivate(client, db_session):
    active_id, _ = _seed(db_session)
    html = client.get(f"/customers/{active_id}").text
    assert f"/customers/{active_id}/deactivate" in html
    # no reactivate path on an active customer
    assert f"/customers/{active_id}/reactivate" not in html


def test_reactivate_round_trip(client, db_session):
    _, inactive_id = _seed(db_session)
    resp = client.post(f"/customers/{inactive_id}/reactivate")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/customers/{inactive_id}"
    db_session.expire_all()  # route committed on its own session — re-read
    c = db_session.query(Customer).filter(Customer.id == inactive_id).first()
    assert c.is_active is True


def test_deactivate_round_trip(client, db_session):
    active_id, _ = _seed(db_session)
    resp = client.post(f"/customers/{active_id}/deactivate")
    assert resp.status_code == 303
    db_session.expire_all()
    c = db_session.query(Customer).filter(Customer.id == active_id).first()
    assert c.is_active is False


def test_reactivated_customer_returns_to_active_list(client, db_session):
    """End-to-end: reactivate → the customer is back in the default (active) view
    and gone from the Inactive tab."""
    _, inactive_id = _seed(db_session)
    client.post(f"/customers/{inactive_id}/reactivate")
    default_html = client.get("/customers/").text
    inactive_html = client.get("/customers/?tab=inactive").text
    assert "Retired Diesel Co" in default_html
    assert "Retired Diesel Co" not in inactive_html
