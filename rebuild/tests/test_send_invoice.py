"""
tests/test_send_invoice.py
==========================
§22 Function A — the shared "Send" dialog wired onto the INVOICE workspace.

This slice is additive: it surfaces the reusable documents/_send_dialog.html on
the invoice workspace and adds POST /invoices/{id}/send-message, which delegates
to the already-built MessagingService via document_messaging.perform_document_send.

Log-only mode is the default kill-switch, so a real send NEVER hits SMTP/Twilio —
the NullMessagingProvider returns LOGGED_ONLY and a row lands in communication_log.
These tests assert on that row + its linkage, not on real delivery.

Locks:
  • GET  /invoices/{id}      → workspace renders the dialog (action targets send-message).
  • POST /invoices/{id}/send-message channels=email → 303 back to ?sent=1, and a
    Communication (LOGGED_ONLY, OUTBOUND, EMAIL) linked to the invoice is written.
  • The invoice's status is NOT changed by sending.
  • Posting no channels → 303 with sent=0 and no Communication row.
"""
from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    CommunicationChannel,
    CommunicationDirection,
    CommunicationStatus,
    InvoiceStatus,
    LineType,
)
from app.main import app
from app.models.communication import Communication
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.services.invoice_service import InvoiceService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # admin, seeded by the startup hook
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _customer(db) -> Customer:
    # Unique email per customer — customers.email has a UNIQUE index, so a
    # hardcoded address collides across tests (MEMORY gotcha).
    c = Customer(
        company_name=f"Send Test Co {next(_seq)}",
        contact_name="QA",
        email=f"buyer{next(_seq)}@test.local",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _open_invoice(db, customer_id: int, amount: float = 100.0) -> Invoice:
    """Create + finalise a non-taxable OPEN invoice (mirrors test_payments)."""
    inv = Invoice(
        invoice_number=f"INV-SEND-{next(_seq):05d}",
        customer_id=customer_id,
        status=InvoiceStatus.DRAFT,
        tax_rate=0.0,
        tax_rate_snapshot=0.0,
        tax_exempt_snapshot=True,
    )
    db.add(inv)
    db.flush()
    db.add(InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Test part",
        qty=1,
        unit_price=amount,
        unit_cost=amount * 0.6,
        is_taxable=False,
    ))
    db.commit()

    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _comms_for(db, inv: Invoice) -> list[Communication]:
    return (
        db.query(Communication)
        .filter(
            Communication.related_entity_type == "invoice",
            Communication.related_entity_id == inv.id,
        )
        .all()
    )


# ── 1. Workspace renders the Send dialog ──────────────────────────────────────

def test_workspace_renders_send_dialog(client, db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id)

    r = client.get(f"/invoices/{inv.id}")
    assert r.status_code == 200, r.text
    html = r.text
    # The dialog form posts to the new send-message endpoint for THIS invoice.
    assert f'action="/invoices/{inv.id}/send-message"' in html
    # The trigger button + Alpine state are present.
    assert "sendOpen" in html
    assert "Send…" in html
    # Channel checkboxes the shared dialog renders.
    assert 'name="channels" value="email"' in html
    assert 'name="channels" value="sms"' in html
    # Pre-filled recipient from the customer.
    assert cust.email in html


# ── 2. POST send-message (email) → 303 + a LOGGED_ONLY Communication ──────────

def test_send_message_email_logs_communication(client, db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id)

    assert _comms_for(db, inv) == []

    r = client.post(
        f"/invoices/{inv.id}/send-message",
        data={
            "channels": "email",
            "to_email": cust.email,
            "email_subject": "Your invoice",
            "email_body": "Please find attached.",
        },
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/invoices/{inv.id}?sent=1"

    db.expire_all()
    comms = _comms_for(db, inv)
    assert len(comms) == 1
    comm = comms[0]
    # Log-only mode default → LOGGED_ONLY (never really transmitted).
    assert comm.status == CommunicationStatus.LOGGED_ONLY
    assert comm.channel == CommunicationChannel.EMAIL
    assert comm.direction == CommunicationDirection.OUTBOUND
    assert comm.customer_id == cust.id
    assert comm.to_address == cust.email
    assert comm.subject == "Your invoice"


# ── 3. Sending does not change invoice status ─────────────────────────────────

def test_send_message_does_not_change_invoice_status(client, db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id)
    assert inv.status == InvoiceStatus.OPEN

    r = client.post(
        f"/invoices/{inv.id}/send-message",
        data={"channels": "email", "to_email": cust.email,
              "email_subject": "s", "email_body": "b"},
    )
    assert r.status_code == 303

    db.expire_all()
    again = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert again.status == InvoiceStatus.OPEN


# ── 4. No channels selected → 303 sent=0, nothing logged ──────────────────────

def test_send_message_no_channels_logs_nothing(client, db):
    cust = _customer(db)
    inv = _open_invoice(db, cust.id)

    r = client.post(f"/invoices/{inv.id}/send-message", data={})
    assert r.status_code == 303
    assert r.headers["location"] == f"/invoices/{inv.id}?sent=0"

    db.expire_all()
    assert _comms_for(db, inv) == []


# ── 4b. Blocked channel (do_not_contact) → sent=0&send_error=1, nothing logged ─

def test_send_message_blocked_flags_send_error(client, db):
    cust = _customer(db)
    cust.do_not_contact = True
    db.commit()
    inv = _open_invoice(db, cust.id)

    r = client.post(
        f"/invoices/{inv.id}/send-message",
        data={"channels": "email", "to_email": cust.email,
              "email_subject": "s", "email_body": "b"},
    )
    assert r.status_code == 303
    # The rep must get the amber "couldn't send" banner, not a silent no-op.
    assert r.headers["location"] == f"/invoices/{inv.id}?sent=0&send_error=1"
    db.expire_all()
    assert _comms_for(db, inv) == []   # blocked before any log row is written


# ── 5. Unknown invoice id → redirect to the list (no crash) ───────────────────

def test_send_message_unknown_invoice_redirects(client, db):
    r = client.post("/invoices/999999/send-message",
                    data={"channels": "email", "to_email": "x@y.z"})
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/invoices/"
