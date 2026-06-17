"""
tests/test_leadfinder_api.py
============================
HTTP-surface tests for the token-gated Lead Finder integration API
(app/routers/leadfinder_api.py). The dedup / link-or-create business logic is
covered in tests/test_lead_conversion.py; these tests cover the API adapter:

  • LEADFINDER_API_TOKEN unset/empty  → 503 integration_disabled.
  • X-LeadFinder-Token wrong/missing  → 401 unauthorized.
  • correct token + CREATE packet     → 200 action=created with a customer_id.
  • re-POST the same packet           → 200 action=linked (idempotent, 1 customer).
  • POST /match for a phone-matching packet → candidates with a phone match.

The DB is a module-isolated in-memory engine (conftest.activate) so the route's
``get_db`` dependency override resolves to the same engine the test seeds.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app as fastapi_app
from app.models.customer import Customer
from tests.conftest import activate, fresh_engine

_TOKEN = "test-leadfinder-secret"
_HEADERS = {"X-LeadFinder-Token": _TOKEN}


@pytest.fixture()
def SessionLocal():
    """Module-isolated in-memory engine, pointed at the app globals + override."""
    engine = fresh_engine()
    return activate(engine)


@pytest.fixture()
def db(SessionLocal):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(SessionLocal):
    """TestClient bound to the app whose get_db override points at this engine."""
    return TestClient(fastapi_app)


@pytest.fixture()
def token_env(monkeypatch):
    """Set the ERP-side shared secret for the duration of a test."""
    monkeypatch.setenv("LEADFINDER_API_TOKEN", _TOKEN)


def _packet(**lead_overrides) -> dict:
    lead = {
        "lead_id": 7,
        "company_name": "Roadrunner Freight Co",
        "legal_name": "Roadrunner Freight Company LLC",
        "dba_name": "Roadrunner Freight",
        "usdot_number": 7654321,
        "phone": "(555) 222-3344",
        "email": "ops@roadrunner.example",
        "address": {"street": "5 Mesa Rd", "city": "Phoenix", "state": "AZ",
                    "zip": "85001"},
        "business_type": "fleet",
        "power_units": 18,
        "driver_count": 20,
        "engine_platforms": "Cummins X15",
        "known_engines": "X15",
        "lead_score": 77,
        "origin": "fmcsa",
        "contacts": [
            {"name": "Wile Coyote", "role": "Owner", "title": None,
             "phone": "555-222-3344", "mobile": None,
             "email": "wile@roadrunner.example", "is_primary": True},
        ],
        "notes": [
            {"type": "research", "body": "Runs the I-10 corridor.",
             "created_at": "2026-06-12"},
        ],
        "activities": [
            {"type": "call", "outcome": "reached", "subject": "Intro",
             "body": "Discussed turbo program.", "date": "2026-06-13"},
        ],
    }
    lead.update(lead_overrides)
    return lead


# ── AUTH gating ──────────────────────────────────────────────────────────────

def test_convert_503_when_token_env_unset(client, monkeypatch):
    """No LEADFINDER_API_TOKEN on the ERP → 503 integration_disabled."""
    monkeypatch.delenv("LEADFINDER_API_TOKEN", raising=False)
    resp = client.post(
        "/api/leadfinder/convert",
        json={"lead": _packet(), "mode": "auto"},
        headers=_HEADERS,
    )
    assert resp.status_code == 503
    assert resp.json() == {
        "error": "integration_disabled",
        "detail": "Set LEADFINDER_API_TOKEN on the ERP",
    }


def test_convert_401_on_wrong_token(client, token_env):
    """Wrong X-LeadFinder-Token → 401 unauthorized."""
    resp = client.post(
        "/api/leadfinder/convert",
        json={"lead": _packet(), "mode": "auto"},
        headers={"X-LeadFinder-Token": "not-the-secret"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_convert_401_on_missing_token(client, token_env):
    """No X-LeadFinder-Token header at all → 401 unauthorized."""
    resp = client.post(
        "/api/leadfinder/convert",
        json={"lead": _packet(), "mode": "auto"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


# ── CREATE + IDEMPOTENT ──────────────────────────────────────────────────────

def test_convert_creates_then_links_idempotent(client, db, token_env):
    """Correct token + CREATE packet → created; re-POST → linked, one customer."""
    body = {"lead": _packet(), "mode": "auto"}

    first = client.post("/api/leadfinder/convert", json=body, headers=_HEADERS)
    assert first.status_code == 200
    payload = first.json()
    assert payload["action"] == "created"
    cid = payload["customer_id"]
    assert isinstance(cid, int)
    assert payload["customer_url"] == f"/customers/{cid}"

    # The customer really exists in the DB this route wrote to.
    assert db.query(Customer).filter(Customer.id == cid).first() is not None

    # Re-POST the same packet → idempotent link, NOT a second customer.
    second = client.post("/api/leadfinder/convert", json=body, headers=_HEADERS)
    assert second.status_code == 200
    payload2 = second.json()
    assert payload2["action"] == "linked"
    assert payload2["customer_id"] == cid

    assert db.query(Customer).filter(Customer.usdot_number == 7654321).count() == 1


# ── /match preview ───────────────────────────────────────────────────────────

def test_match_returns_phone_candidate(client, db, token_env):
    """A packet whose phone matches an existing customer → a phone candidate."""
    existing = Customer(
        company_name="Some Other Carrier",
        contact_name="",
        phone="555-222-3344",   # same digits as the packet phone
        city="Phoenix", state="AZ",
    )
    db.add(existing)
    db.commit()
    eid = existing.id
    before = db.query(Customer).count()

    # Drop usdot so the candidate is found by phone, not exact-DOT.
    resp = client.post(
        "/api/leadfinder/match",
        json={"lead": _packet(usdot_number=None)},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(
        c["match_reason"] == "phone" and c["customer_id"] == eid
        for c in data["candidates"]
    )
    # Pure preview — no write.
    assert db.query(Customer).count() == before


def test_match_503_without_token_env(client, monkeypatch):
    """/match is gated by the same token rule as /convert."""
    monkeypatch.delenv("LEADFINDER_API_TOKEN", raising=False)
    resp = client.post(
        "/api/leadfinder/match", json={"lead": _packet()}, headers=_HEADERS
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "integration_disabled"


# ── invalid packet → 422 ─────────────────────────────────────────────────────

def test_convert_422_on_packet_without_company_name(client, token_env):
    """A lead with no company name is an invalid packet → 422."""
    resp = client.post(
        "/api/leadfinder/convert",
        json={"lead": {"lead_id": 1}, "mode": "auto"},
        headers=_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_packet"
