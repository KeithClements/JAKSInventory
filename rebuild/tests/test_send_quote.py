"""
tests/test_send_quote.py
========================
§22 — "Send" dialog wiring on the QUOTE workspace.

This slice adds an email/SMS Send dialog to the quote workspace, backed by the
shared messaging core (MessagingService / document_messaging). Nothing here
touches real SMTP/Twilio: the global "messaging_log_only_mode" kill-switch
defaults to "true", so every send is recorded in communication_log with
status=LOGGED_ONLY and NOTHING is transmitted.

Contract locked here:
  • GET /quotes/{id} renders the shared Send dialog wired to the send route
    (action="/quotes/{id}/send-message").
  • POST /quotes/{id}/send-message with channels=email on a DRAFT quote → 303,
    a LOGGED_ONLY Communication row exists, and the quote is now SENT.
  • POST with channels=sms when the customer has allow_sms=False → the SMS is
    BLOCKED (no SMS Communication row), but the route still returns 303 (no 500),
    and a DRAFT quote stays DRAFT (nothing was actually sent).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_send_quote.py -q
"""
from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from app.constants import (
    CommunicationChannel,
    CommunicationStatus,
    QuoteOutcome,
    QuoteStatus,
)
from app.constants import LineType, UserRole
from app.deps import DEFAULT_USER_ID
from app.main import app
from app.models.communication import Communication
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import Quote, QuoteLine
from app.models.user import User
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


# ── Fixtures (per-test isolated in-memory DB; log-only mode is the default) ───

@pytest.fixture()
def db_session():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    # The fresh engine has no startup-seeded user; the PDF/print context resolves
    # the acting user (DEFAULT_USER_ID under JAKS_SKIP_AUTH) for the 'Prepared by'
    # header, so seed it the same way startup does.
    db.add(User(id=DEFAULT_USER_ID, name="QA Admin", username="qa-admin",
                password_hash="x", role=UserRole.ADMIN, is_active=True))
    db.commit()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    return TestClient(app, follow_redirects=False, raise_server_exceptions=False)


def _customer(db, *, allow_sms=False, email="buyer@example.com", phone="(806) 555-0100"):
    c = Customer(
        company_name=f"Send Co {next(_seq)}",
        contact_name="QA Buyer",
        email=email,
        phone=phone,
        allow_sms=allow_sms,
        do_not_contact=False,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _product(db):
    p = Product(sku=f"SENDP{next(_seq)}", title="Turbo", cost=50.0)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _quote(db, cust, *, status=QuoteStatus.DRAFT):
    q = Quote(
        quote_number=f"Q-SEND-{next(_seq):05d}",
        customer_id=cust.id,
        status=status,
        outcome=QuoteOutcome.PENDING,
    )
    prod = _product(db)
    q.lines.append(
        QuoteLine(
            line_type=LineType.PRODUCT,
            product_id=prod.id,
            qty=1,
            unit_price=199.99,
            unit_cost=120.0,
            description="send-test line",
            sort_order=0,
        )
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


def _comms(db, quote, *, channel=None):
    qy = db.query(Communication).filter(
        Communication.related_entity_type == "quote",
        Communication.related_entity_id == quote.id,
    )
    if channel is not None:
        qy = qy.filter(Communication.channel == channel)
    return qy.all()


# ── 1) Workspace renders the shared Send dialog wired to the send route ───────

def test_workspace_renders_send_dialog(client, db_session):
    quote = _quote(db_session, _customer(db_session))
    r = client.get(f"/quotes/{quote.id}")
    assert r.status_code == 200, r.text
    html = r.text
    # Dialog posts to the new per-document send route.
    assert f'action="/quotes/{quote.id}/send-message"' in html
    # The trigger button + the Alpine flag that drives the overlay.
    assert "sendOpen = true" in html
    assert "sendOpen: false" in html
    # Channel checkboxes from the shared partial are present.
    assert 'name="channels" value="email"' in html
    assert 'name="channels" value="sms"' in html


# ── 2) Email send on a DRAFT quote → 303, LOGGED_ONLY row, quote → SENT ───────

def test_send_email_logs_and_promotes_draft_to_sent(client, db_session):
    cust = _customer(db_session)
    quote = _quote(db_session, cust, status=QuoteStatus.DRAFT)

    r = client.post(
        f"/quotes/{quote.id}/send-message",
        data={
            "channels": "email",
            "to_email": cust.email,
            "email_subject": "Your quote",
            "email_body": "Here is your quote, thanks.",
        },
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith(f"/quotes/{quote.id}?sent=1")

    # A Communication row was logged (LOGGED_ONLY since the kill-switch is on).
    emails = _comms(db_session, quote, channel=CommunicationChannel.EMAIL)
    assert len(emails) == 1
    assert emails[0].status == CommunicationStatus.LOGGED_ONLY
    assert emails[0].customer_id == cust.id

    # DRAFT was promoted to SENT by the first successful send.
    db_session.expire_all()
    refreshed = db_session.query(Quote).get(quote.id)
    assert refreshed.status == QuoteStatus.SENT


# ── 3) SMS without consent → blocked, but route still 303 (no 500) ────────────

def test_send_sms_without_consent_is_blocked_but_route_succeeds(client, db_session):
    cust = _customer(db_session, allow_sms=False)
    quote = _quote(db_session, cust, status=QuoteStatus.DRAFT)

    r = client.post(
        f"/quotes/{quote.id}/send-message",
        data={
            "channels": "sms",
            "to_phone": cust.phone,
            "sms_body": "Your quote is ready.",
        },
    )
    # The blocked channel never raises into the route.
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith(f"/quotes/{quote.id}?sent=0")
    assert "send_error=1" in r.headers["location"]

    # No SMS Communication row was created (consent check blocked it pre-log).
    assert _comms(db_session, quote, channel=CommunicationChannel.SMS) == []

    # Nothing went out, so a DRAFT quote stays DRAFT.
    db_session.expire_all()
    refreshed = db_session.query(Quote).get(quote.id)
    assert refreshed.status == QuoteStatus.DRAFT
