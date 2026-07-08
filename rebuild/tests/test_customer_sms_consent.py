"""
tests/test_customer_sms_consent.py
===================================
"OK to text" + opt-out controls on the customer profile (§22 Function B).

Locks the slice contract:
  • POST /{id}/sms-consent     → allow_sms True + sms_consent_at set + method "verbal".
  • POST /{id}/sms-optout      → allow_sms False (email + do_not_contact untouched).
  • POST /{id}/contact-optout  → do_not_contact True + allow_sms/allow_email False.
  • POST /{id}/contact-allow   → do_not_contact cleared.
  • Each route audits the change to communication_log / audit_log via the shared
    MessagingService and redirects 303 back to the customer page.
  • detail.html renders the three-state control: "OK to text" button when
    allow_sms is False, and the green "Texting OK" chip when True.

Fresh-engine + TestClient; never hits real SMTP/Twilio (the default log-only
mode means consent toggles are pure DB writes, no provider call at all).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.audit import AuditLog
from app.models.customer import Customer
from tests.conftest import activate, fresh_engine


# ── Fixtures (per-test isolated in-memory DB) ────────────────────────────────

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


def _seed_customer(db, **overrides) -> Customer:
    data = dict(
        company_name="Mike's Diesel",
        contact_name="Mike",
        phone="555-123-4567",
        email="mike@example.com",
    )
    data.update(overrides)
    c = Customer(**data)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── POST /{id}/sms-consent ───────────────────────────────────────────────────

def test_sms_consent_sets_allow_sms_and_consent_fields(client, db_session):
    c = _seed_customer(db_session)
    assert c.allow_sms is False  # default

    r = client.post(f"/customers/{c.id}/sms-consent")
    assert r.status_code == 303
    assert r.headers["location"] == f"/customers/{c.id}"

    db_session.expire_all()
    c = db_session.query(Customer).get(c.id)
    assert c.allow_sms is True
    assert c.sms_consent_at is not None
    assert c.sms_consent_method == "verbal"


def test_sms_consent_audits(client, db_session):
    c = _seed_customer(db_session)
    client.post(f"/customers/{c.id}/sms-consent")

    row = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "customer", AuditLog.entity_id == c.id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.action == "consent_recorded"


def test_sms_consent_redirects_back_to_referer(client, db_session):
    """When triggered from the communications page, the 303 returns there."""
    c = _seed_customer(db_session)
    r = client.post(
        f"/customers/{c.id}/sms-consent",
        headers={"referer": f"http://testserver/customers/{c.id}/communications"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/customers/{c.id}/communications"


def test_sms_consent_ignores_offsite_referer(client, db_session):
    """A foreign Referer can't bounce the redirect off-app — falls back to detail."""
    c = _seed_customer(db_session)
    r = client.post(
        f"/customers/{c.id}/sms-consent",
        headers={"referer": "http://evil.example.com/phish"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/customers/{c.id}"


# ── POST /{id}/sms-optout ────────────────────────────────────────────────────

def test_sms_optout_clears_allow_sms_only(client, db_session):
    c = _seed_customer(db_session, allow_sms=True, sms_consent_method="verbal")
    assert c.allow_email is True

    r = client.post(f"/customers/{c.id}/sms-optout")
    assert r.status_code == 303

    db_session.expire_all()
    c = db_session.query(Customer).get(c.id)
    assert c.allow_sms is False
    # Email + contactability untouched — "stop texting" is SMS-only.
    assert c.allow_email is True
    assert c.do_not_contact is False


# ── POST /{id}/contact-optout (Do Not Contact) ───────────────────────────────

def test_contact_optout_sets_do_not_contact_and_clears_channels(client, db_session):
    c = _seed_customer(db_session, allow_sms=True, allow_email=True)

    r = client.post(f"/customers/{c.id}/contact-optout")
    assert r.status_code == 303

    db_session.expire_all()
    c = db_session.query(Customer).get(c.id)
    assert c.do_not_contact is True
    assert c.allow_sms is False
    assert c.allow_email is False
    assert c.opt_out_at is not None


# ── POST /{id}/contact-allow ─────────────────────────────────────────────────

def test_contact_allow_clears_do_not_contact(client, db_session):
    c = _seed_customer(db_session, do_not_contact=True)

    r = client.post(f"/customers/{c.id}/contact-allow")
    assert r.status_code == 303

    db_session.expire_all()
    c = db_session.query(Customer).get(c.id)
    assert c.do_not_contact is False


# ── detail.html three-state rendering ────────────────────────────────────────

def test_detail_shows_ok_to_text_button_when_no_consent(client, db_session):
    c = _seed_customer(db_session, allow_sms=False)
    r = client.get(f"/customers/{c.id}")
    assert r.status_code == 200
    html = r.text
    # The "OK to text" affordance + its form action are present.
    assert "OK to text" in html
    assert f'action="/customers/{c.id}/sms-consent"' in html
    # No green "Texting OK" chip when there's no consent.
    assert "Texting OK" not in html


def test_detail_shows_green_chip_when_consented(client, db_session):
    from datetime import datetime
    c = _seed_customer(
        db_session, allow_sms=True, sms_consent_method="verbal",
        sms_consent_at=datetime(2026, 6, 17, 12, 0, 0),
    )
    r = client.get(f"/customers/{c.id}")
    assert r.status_code == 200
    html = r.text
    assert "Texting OK" in html
    assert "Jun 17, 2026" in html  # consent date rendered into the chip
    # Stop-texting affordance shown instead of "OK to text".
    assert f'action="/customers/{c.id}/sms-optout"' in html
    assert "Stop texting" in html


def test_detail_shows_do_not_contact_chip_and_allow_button(client, db_session):
    c = _seed_customer(db_session, do_not_contact=True)
    r = client.get(f"/customers/{c.id}")
    assert r.status_code == 200
    html = r.text
    assert "Do Not Contact" in html
    assert "Allow contact" in html
    assert f'action="/customers/{c.id}/contact-allow"' in html
