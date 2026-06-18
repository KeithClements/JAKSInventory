"""
tests/test_settings_messaging_test.py
======================================
Settings → Messaging "Send test" (§22.5).

The shared MessagingService core is already built/tested elsewhere; this file
locks the THIN UI WIRING for the Send-test control:

  • POST /settings/messaging/test with test_channel=email + an address → 303,
    and a Communication audit row (provider="test") is written. Under the default
    messaging_log_only_mode kill-switch the recorded status is LOGGED_ONLY and
    nothing is actually transmitted.
  • Same for test_channel=sms.
  • The redirect carries the result status back as ?msg_test=… so the page can flash it.
  • GET /settings/ renders the new Send-test control (channel select + address input
    posting to /settings/messaging/test).

Fresh in-memory DB + TestClient (mirrors tests/test_settings_page.py). The
TestClient context manager runs startup, which seeds the admin user #1 — the
route is admin-gated and the test bypass user (DEFAULT_USER_ID) is that admin,
so no extra auth wiring is needed. Log-only mode is the default, so no real
SMTP/Twilio is ever touched.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_settings_messaging_test.py -q
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import CommunicationChannel, CommunicationStatus
from app.main import app
from app.models.communication import Communication
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c  # startup seeds the admin user (route is admin-gated)


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── POST happy paths — logs a test Communication under the safe-mode default ──

def test_post_email_test_logs_communication(client, db):
    before = db.query(Communication).filter(Communication.provider == "test").count()

    r = client.post(
        "/settings/messaging/test",
        data={"test_channel": "email", "test_to": "owner@jaksdiesel.com"},
    )
    assert r.status_code == 303, r.text
    # Result status round-trips in the redirect so the page can flash it.
    assert "msg_test=" in r.headers["location"]
    assert "tab=messaging" in r.headers["location"]

    db.expire_all()
    rows = (
        db.query(Communication)
        .filter(Communication.provider == "test",
                Communication.channel == CommunicationChannel.EMAIL)
        .all()
    )
    assert len(rows) == before + 1
    row = rows[-1]
    # Default kill-switch (messaging_log_only_mode=true) → logged, never sent.
    assert row.status == CommunicationStatus.LOGGED_ONLY
    assert row.provider == "test"
    # Test sends are NOT tied to a customer / consent.
    assert row.customer_id is None
    assert row.to_address == "owner@jaksdiesel.com"
    # logged_only echoed back in the flash query string.
    assert "msg_test=logged_only" in r.headers["location"]


def test_post_sms_test_logs_communication(client, db):
    r = client.post(
        "/settings/messaging/test",
        data={"test_channel": "sms", "test_to": "+13035551234"},
    )
    assert r.status_code == 303, r.text

    db.expire_all()
    row = (
        db.query(Communication)
        .filter(Communication.provider == "test",
                Communication.channel == CommunicationChannel.SMS)
        .order_by(Communication.id.desc())
        .first()
    )
    assert row is not None
    assert row.status == CommunicationStatus.LOGGED_ONLY
    assert row.provider == "test"
    assert row.customer_id is None
    # SMS number normalized to E.164 by the service.
    assert row.to_address == "+13035551234"


# ── GET /settings/ renders the Send-test control ─────────────────────────────

def test_settings_page_renders_send_test_control(client):
    html = client.get("/settings/").text
    # The Send-test form posts to the dedicated route.
    assert 'action="/settings/messaging/test"' in html
    # Channel select + address input present.
    assert 'name="test_channel"' in html
    assert 'name="test_to"' in html
    # It lives on the Messaging tab.
    assert "tab === 'messaging'" in html
