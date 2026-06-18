"""
tests/test_messaging_providers.py
=================================
Email (SMTP) + SMS (Twilio) provider wiring behind MessagingService's existing
swap point. Verifies the safe-by-default log-only kill switch, provider selection
from Settings, credential fallback, secret decryption, channel guards, and E.164
normalization. No network calls.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.constants import CommunicationChannel
from app.services.messaging_service import (
    MessagingService, NullMessagingProvider, SmtpProvider, TwilioProvider, _to_e164,
)
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


@pytest.fixture()
def db():
    SessionLocal = activate(_ENGINE)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _set(db, **kw):
    for k, v in kw.items():
        set_setting_value_db(db, k, v)
    db.commit()


# ── E.164 ────────────────────────────────────────────────────────────────────

def test_e164_normalization():
    assert _to_e164("(303) 555-1234") == "+13035551234"
    assert _to_e164("303-555-1234") == "+13035551234"
    assert _to_e164("13035551234") == "+13035551234"
    assert _to_e164("+44 20 7946 0958") == "+442079460958"
    assert _to_e164("") == ""
    assert _to_e164("   ") == ""


# ── Safe default ─────────────────────────────────────────────────────────────

def test_log_only_default_returns_null(db):
    # No settings touched → messaging_log_only_mode defaults "true" → never sends.
    svc = MessagingService(db)
    assert isinstance(svc._provider_for(CommunicationChannel.EMAIL), NullMessagingProvider)
    assert isinstance(svc._provider_for(CommunicationChannel.SMS), NullMessagingProvider)


def test_log_only_overrides_configured_providers(db):
    # Even fully configured, log-only mode forces Null (the kill switch).
    _set(db, messaging_log_only_mode="true",
         messaging_email_provider="smtp", smtp_host="smtp.x", smtp_from_address="a@b.c",
         messaging_sms_provider="twilio", twilio_account_sid="AC1",
         twilio_auth_token_encrypted="tok", twilio_from_number="+13035550000")
    svc = MessagingService(db)
    assert isinstance(svc._provider_for(CommunicationChannel.EMAIL), NullMessagingProvider)
    assert isinstance(svc._provider_for(CommunicationChannel.SMS), NullMessagingProvider)


# ── Email selection ──────────────────────────────────────────────────────────

def test_smtp_selected_when_configured(db):
    _set(db, messaging_log_only_mode="false", messaging_email_provider="smtp",
         smtp_host="smtp.gmail.com", smtp_from_address="parts@jaksdiesel.com")
    assert isinstance(MessagingService(db)._provider_for(CommunicationChannel.EMAIL), SmtpProvider)


def test_email_falls_back_to_null_when_creds_missing(db):
    _set(db, messaging_log_only_mode="false", messaging_email_provider="smtp",
         smtp_host="", smtp_from_address="")  # provider chosen but not configured
    assert isinstance(MessagingService(db)._provider_for(CommunicationChannel.EMAIL), NullMessagingProvider)


# ── SMS selection ────────────────────────────────────────────────────────────

def test_twilio_selected_and_token_decrypted(db):
    from app.services.qbo_client import _encrypt
    _set(db, messaging_log_only_mode="false", messaging_sms_provider="twilio",
         twilio_account_sid="AC123", twilio_auth_token_encrypted=_encrypt("supersecret"),
         twilio_from_number="+13035551234")
    p = MessagingService(db)._provider_for(CommunicationChannel.SMS)
    assert isinstance(p, TwilioProvider)
    assert p.auth_token == "supersecret"   # decrypted at selection time


def test_sms_falls_back_to_null_when_creds_missing(db):
    _set(db, messaging_log_only_mode="false", messaging_sms_provider="twilio",
         twilio_account_sid="", twilio_auth_token_encrypted="", twilio_from_number="")
    assert isinstance(MessagingService(db)._provider_for(CommunicationChannel.SMS), NullMessagingProvider)


# ── Channel guards (no network) ──────────────────────────────────────────────

def test_provider_channel_guards():
    smtp = SmtpProvider(host="h", port=587, username="", password="", from_address="a@b.c")
    assert smtp.send_sms(to="+13035551234", body="x").status == "failed"
    tw = TwilioProvider(account_sid="AC1", auth_token="t", from_number="+1303")
    assert tw.send_email(to="a@b.c", subject="s", body="b").status == "failed"


def test_smtp_send_no_recipient_is_failed_not_raise():
    smtp = SmtpProvider(host="h", port=587, username="", password="", from_address="a@b.c")
    assert smtp.send_email(to="", subject="s", body="b").status == "failed"
