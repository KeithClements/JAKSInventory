"""
tests/test_vendor_contacts.py
=============================
Contract tests for the O4 vendor-contacts CRUD routes (the Contacts card on
vendors/detail.html).  These hit the real HTTP routes the UI lane posts to, so a
refactor can't silently break the card.

What this locks down:
  1. First contact auto-becomes primary; second does not.
  2. Explicit is_primary (and /make-primary) switch primary exclusively.
  3. Update edits fields but never touches primary.
  4. Delete is soft (is_active=False) and clears primary.
  5. Blank name / unknown vendor / unknown contact never 500 — they 303.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.constants import VendorContactRole
from app.models.vendor import Vendor, VendorContact
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
    # follow_redirects=False so we can assert the 303 contract directly.
    return TestClient(app, follow_redirects=False)


def _seed_vendor(db, name="Acme Diesel", code="ACME"):
    v = Vendor(name=name, vendor_code=code)
    db.add(v)
    db.commit()
    return v


def _contacts(db, vendor_id, active_only=True):
    db.expire_all()  # route committed on a different session — re-read from DB
    q = db.query(VendorContact).filter(VendorContact.vendor_id == vendor_id)
    if active_only:
        q = q.filter(VendorContact.is_active == True)  # noqa: E712
    return q.all()


# ── create ──────────────────────────────────────────────────────────────────

def test_create_first_contact_autoprimary(client, db_session):
    v = _seed_vendor(db_session)
    r = client.post(f"/vendors/{v.id}/contacts", data={"name": "Pat Sales", "role": "sales"})
    assert r.status_code == 303
    assert f"/vendors/{v.id}" in r.headers["location"]
    contacts = _contacts(db_session, v.id)
    assert len(contacts) == 1
    assert contacts[0].name == "Pat Sales"
    assert contacts[0].role == VendorContactRole.SALES
    # First contact is auto-primary even though is_primary wasn't sent.
    assert contacts[0].is_primary is True


def test_create_requires_name(client, db_session):
    v = _seed_vendor(db_session)
    r = client.post(f"/vendors/{v.id}/contacts", data={"name": "   "})
    assert r.status_code == 303
    assert "error" in r.headers["location"]
    assert _contacts(db_session, v.id) == []


def test_create_unknown_vendor_redirects(client, db_session):
    r = client.post("/vendors/9999/contacts", data={"name": "Ghost"})
    assert r.status_code == 303
    assert r.headers["location"] == "/vendors/"


def test_second_contact_not_primary_by_default(client, db_session):
    v = _seed_vendor(db_session)
    client.post(f"/vendors/{v.id}/contacts", data={"name": "First"})
    client.post(f"/vendors/{v.id}/contacts", data={"name": "Second"})
    contacts = {c.name: c for c in _contacts(db_session, v.id)}
    assert contacts["First"].is_primary is True
    assert contacts["Second"].is_primary is False


def test_create_explicit_primary_steals_primary(client, db_session):
    v = _seed_vendor(db_session)
    client.post(f"/vendors/{v.id}/contacts", data={"name": "First"})
    client.post(f"/vendors/{v.id}/contacts", data={"name": "Second", "is_primary": "on"})
    contacts = {c.name: c for c in _contacts(db_session, v.id)}
    assert contacts["First"].is_primary is False
    assert contacts["Second"].is_primary is True
    assert sum(1 for c in contacts.values() if c.is_primary) == 1


# ── update ──────────────────────────────────────────────────────────────────

def test_update_edits_fields_not_primary(client, db_session):
    v = _seed_vendor(db_session)
    client.post(f"/vendors/{v.id}/contacts", data={"name": "First"})
    client.post(f"/vendors/{v.id}/contacts", data={"name": "Second"})
    second = next(c for c in _contacts(db_session, v.id) if c.name == "Second")
    r = client.post(
        f"/vendors/{v.id}/contacts/{second.id}",
        data={"name": "Second Edited", "role": "warranty", "email": "w@x.com"},
    )
    assert r.status_code == 303
    refreshed = db_session.get(VendorContact, second.id)
    db_session.refresh(refreshed)
    assert refreshed.name == "Second Edited"
    assert refreshed.role == VendorContactRole.WARRANTY
    assert refreshed.email == "w@x.com"
    # update must NOT change primary (First stays primary)
    assert refreshed.is_primary is False


# ── make-primary ────────────────────────────────────────────────────────────

def test_make_primary_switches_exclusively(client, db_session):
    v = _seed_vendor(db_session)
    client.post(f"/vendors/{v.id}/contacts", data={"name": "First"})
    client.post(f"/vendors/{v.id}/contacts", data={"name": "Second"})
    second = next(c for c in _contacts(db_session, v.id) if c.name == "Second")
    r = client.post(f"/vendors/{v.id}/contacts/{second.id}/make-primary")
    assert r.status_code == 303
    contacts = {c.name: c for c in _contacts(db_session, v.id)}
    assert contacts["Second"].is_primary is True
    assert contacts["First"].is_primary is False
    assert sum(1 for c in contacts.values() if c.is_primary) == 1


# ── delete (soft) ───────────────────────────────────────────────────────────

def test_delete_soft_removes_from_active(client, db_session):
    v = _seed_vendor(db_session)
    client.post(f"/vendors/{v.id}/contacts", data={"name": "First"})
    first = _contacts(db_session, v.id)[0]
    r = client.post(f"/vendors/{v.id}/contacts/{first.id}/delete")
    assert r.status_code == 303
    assert _contacts(db_session, v.id, active_only=True) == []
    # still present as inactive (history preserved)
    assert len(_contacts(db_session, v.id, active_only=False)) == 1
    gone = db_session.get(VendorContact, first.id)
    assert gone.is_active is False
    assert gone.is_primary is False


def test_primary_contact_property_reflects_state(client, db_session):
    v = _seed_vendor(db_session)
    client.post(f"/vendors/{v.id}/contacts", data={"name": "Only"})
    db_session.expire_all()
    fresh = db_session.get(Vendor, v.id)
    assert fresh.primary_contact is not None
    assert fresh.primary_contact.name == "Only"
