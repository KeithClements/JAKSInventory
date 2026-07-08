"""
tests/test_send_so.py
======================
§22 Function A — the shared "Send" dialog wired onto the SALES ORDER workspace.

Covers this slice only (the messaging core itself is tested elsewhere):
  • GET /sales-orders/{id} renders documents/_send_dialog.html, posting to
    /sales-orders/{id}/send-message (note the HYPHEN url prefix).
  • POST /sales-orders/{id}/send-message with channels=email returns 303 back to
    the workspace (?sent=N) and logs a Communication row linked to the SO.

The global messaging_log_only_mode setting defaults to "true", so the send never
touches real SMTP/Twilio — the logged Communication lands as LOGGED_ONLY. We
assert on that status + the communication_log row, not on real delivery.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.constants import (
    CommunicationChannel,
    CommunicationStatus,
    SOPaymentMode,
)
from app.main import app
from app.models.communication import Communication
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import SalesOrder
from app.services.sales_order_service import SalesOrderService
from tests.conftest import activate, fresh_engine


# ── Per-test isolated in-memory DB (mirror test_products_new_form.py) ─────────

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


def _seed_customer(db, *, email="buyer@example.com") -> Customer:
    c = Customer(company_name="Big Rig Diesel", contact_name="Pat Buyer",
                 email=email, phone="806-555-0100")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _seed_product(db) -> Product:
    p = Product(sku="SO-SEND-0001", title="Reman Turbo", is_active=True,
                cost=100.0, markup_pct=50.0, qty_on_hand=10)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _seed_so(db, customer: Customer, product: Product) -> SalesOrder:
    svc = SalesOrderService(db, 1)
    so = svc.create_sales_order(
        customer_id=customer.id,
        payment_mode=SOPaymentMode.NONE,
        data={"lines": []},
    )
    svc.add_line(so.id, product.id, {"qty_ordered": 2, "unit_price": 250.0})
    db.refresh(so)
    return so


# ── Workspace renders the Send dialog ────────────────────────────────────────

def test_workspace_renders_send_dialog(client, db_session):
    cust = _seed_customer(db_session)
    prod = _seed_product(db_session)
    so = _seed_so(db_session, cust, prod)

    r = client.get(f"/sales-orders/{so.id}")
    assert r.status_code == 200, r.text
    html = r.text

    # Trigger button + the shared dialog's form action (hyphen prefix).
    assert "sendOpen = true" in html
    assert f'action="/sales-orders/{so.id}/send-message"' in html
    # The dialog pre-fills the customer's email and the channel checkboxes.
    assert cust.email in html
    assert 'name="channels"' in html
    assert 'value="email"' in html
    # Dialog header reflects the doc label + number.
    assert "Send Sales Order" in html
    assert so.so_number in html


# ── POST send-message logs a Communication (log-only) ────────────────────────

def test_send_message_email_logs_communication(client, db_session):
    cust = _seed_customer(db_session)
    prod = _seed_product(db_session)
    so = _seed_so(db_session, cust, prod)

    r = client.post(
        f"/sales-orders/{so.id}/send-message",
        data={
            "channels": "email",
            "to_email": cust.email,
            "email_subject": "Your sales order",
            "email_body": "Please find your sales order attached.",
            "to_phone": "",
            "sms_body": "",
        },
    )
    # 303 back to the workspace with the sent count.
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/sales-orders/{so.id}?sent=1"

    # One Communication row, log-only, linked to the SO, on the email channel.
    db_session.expire_all()
    comms = (
        db_session.query(Communication)
        .filter(
            Communication.related_entity_type == "sales_order",
            Communication.related_entity_id == so.id,
        )
        .all()
    )
    assert len(comms) == 1
    comm = comms[0]
    assert comm.channel == CommunicationChannel.EMAIL
    assert comm.status == CommunicationStatus.LOGGED_ONLY
    assert comm.customer_id == cust.id
    assert comm.to_address == cust.email


def test_send_message_no_channel_logs_nothing(client, db_session):
    """No channel checked → nothing transmitted/logged, still a clean 303 back."""
    cust = _seed_customer(db_session)
    prod = _seed_product(db_session)
    so = _seed_so(db_session, cust, prod)

    r = client.post(
        f"/sales-orders/{so.id}/send-message",
        data={"to_email": cust.email, "email_subject": "x", "email_body": "y"},
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/sales-orders/{so.id}?sent=0"
    assert db_session.query(Communication).count() == 0
