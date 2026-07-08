"""
tests/test_document_messaging.py
================================
§22 Function A — service-level invariants of the shared send pipeline
(app/services/document_messaging.perform_document_send + render_pdf_or_none),
independent of any one document type. Log-only mode is the default kill-switch,
so sends never hit SMTP/Twilio — they return LOGGED_ONLY and write to
communication_log; consent failures write NOTHING.
"""
from __future__ import annotations

import itertools
from datetime import datetime

import pytest

import app.database as _appdb
from app.constants import CommunicationChannel
from app.models.communication import Communication
from app.models.customer import Customer
from app.services.document_messaging import perform_document_send, render_pdf_or_none
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module", autouse=True)
def _activate():
    activate(_ENGINE)
    yield


@pytest.fixture()
def db():
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db, **kw) -> Customer:
    n = next(_seq)
    c = Customer(
        company_name=f"DM Co {n}", contact_name="QA",
        email=f"dm{n}@test.local", phone=f"806-555-{n % 10000:04d}", **kw,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _comms(db, customer_id: int) -> list[Communication]:
    return db.query(Communication).filter(Communication.customer_id == customer_id).all()


# ── do_not_contact blocks EVERY channel (the strongest consent guarantee) ─────

def test_do_not_contact_blocks_email(db):
    c = _customer(db, do_not_contact=True)
    res = perform_document_send(
        db, 1, customer_id=c.id, channels=["email"],
        to_email=c.email, to_phone="", email_subject="s", email_body="b", sms_body="",
        related_entity_type="quote", related_entity_id=1,
    )
    assert res["sent"] == []
    assert any(b["channel"] == "email" for b in res["blocked"])
    assert _comms(db, c.id) == []   # blocked BEFORE any log row is written


# ── both channels in one send, with SMS blocked for lack of consent ───────────

def test_email_ok_but_sms_blocked_both_channels(db):
    c = _customer(db, allow_email=True, allow_sms=False)
    res = perform_document_send(
        db, 1, customer_id=c.id, channels=["email", "sms"],
        to_email=c.email, to_phone=c.phone, email_subject="s", email_body="b", sms_body="hi",
        related_entity_type="quote", related_entity_id=2,
    )
    assert any(s["channel"] == "email" for s in res["sent"])
    assert any(b["channel"] == "sms" for b in res["blocked"])
    rows = _comms(db, c.id)
    assert len(rows) == 1 and rows[0].channel == CommunicationChannel.EMAIL


# ── both channels, fully consented → two rows ─────────────────────────────────

def test_both_channels_consented_logs_two(db):
    c = _customer(db, allow_email=True, allow_sms=True, sms_consent_at=datetime.utcnow())
    res = perform_document_send(
        db, 1, customer_id=c.id, channels=["email", "sms"],
        to_email=c.email, to_phone=c.phone, email_subject="s", email_body="b", sms_body="hi",
        related_entity_type="quote", related_entity_id=3,
    )
    assert len(res["sent"]) == 2
    chans = {r.channel for r in _comms(db, c.id)}
    assert chans == {CommunicationChannel.EMAIL, CommunicationChannel.SMS}


# ── Itemization in the message bodies ────────────────────────────────────────

def test_itemized_bodies_include_lines(db):
    from app.services.document_messaging import (
        default_email_body, default_sms_body, itemize_lines,
    )

    class _L:
        def __init__(self, qty, desc, unit):
            self.qty, self.description, self.unit_price, self.is_included = qty, desc, unit, True

    lines = itemize_lines([_L(2, "Radiator", 450.0), _L(1, "Thermostat", 45.0), _L(3, "", 9.0)])
    # blank-description line is skipped; amounts = qty * unit_price
    assert lines == [
        {"qty": 2, "desc": "Radiator", "amount": 900.0},
        {"qty": 1, "desc": "Thermostat", "amount": 45.0},
    ]
    eb = default_email_body(db, doc_label="Quote", number="Q-1",
                            customer_name="Acme", total=945.0, lines=lines)
    assert "2 x Radiator" in eb and "$900.00" in eb and "Total" in eb
    sb = default_sms_body(db, doc_label="Quote", number="Q-1", total=945.0, lines=lines)
    assert "2x Radiator" in sb and "Total $945.00" in sb


def test_itemize_lines_include_predicate(db):
    from app.services.document_messaging import itemize_lines

    class _L:
        def __init__(self, qty, desc, unit, inc):
            self.qty, self.description, self.unit_price, self.is_included = qty, desc, unit, inc

    rows = itemize_lines([_L(1, "Keep", 10.0, True), _L(1, "Drop", 10.0, False)],
                         include=lambda ln: ln.is_included)
    assert [r["desc"] for r in rows] == ["Keep"]


# ── PDF best-effort: a bad template returns None, never raises ────────────────

def test_render_pdf_or_none_returns_none_on_bad_template():
    from app.routers.quotes import templates  # any Jinja2Templates env
    assert render_pdf_or_none(templates.env, "does/not/exist.html", "http://x/") is None
