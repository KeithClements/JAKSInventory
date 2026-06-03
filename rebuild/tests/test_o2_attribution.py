"""
tests/test_o2_attribution.py
============================
O2 follow-through tests for the *customers* router + the seeded second user.

Two gaps these close (see PHASE_1_TEST_PLAN.md §3.1 / MASTER_PLAN §16.4 P0):

  1. customers.py used a module-level ``CURRENT_USER_ID = 1`` so every CRM /
     call-log / messaging write was attributed to user #1 regardless of who was
     signed in. It now takes ``user_id = Depends(get_current_user_id)`` like the
     other routers. These tests prove a call logged while signed in is attributed
     to the signed-in user, and that the unauthenticated path still falls back to
     DEFAULT_USER_ID (so the attribution is genuinely session-driven).

  2. The wife/bookkeeping second user is now seeded at startup (non-admin), so the
     §11 "second user" line is satisfied out of the box.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.auth as auth
from app.auth import SESSION_COOKIE, hash_password
from app.deps import DEFAULT_USER_ID
from app.main import app
from app.constants import CallOutcome, CallType, UserRole
from app.models.customer import Customer, CustomerCallLog
from app.models.user import User
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def client_db():
    """In-memory app + TestClient. Startup seeds the session secret, the admin
    user (#1), and now the bookkeeping user."""
    engine = fresh_engine()
    SessionLocal = activate(engine)
    auth.reset_secret_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, SessionLocal


# ── 1. attribution flows to the signed-in user ───────────────────────────────

def test_signed_in_user_is_actor_on_call_log(client_db):
    """A call logged via the customers router while signed in is attributed to
    the signed-in user (not the old hardcoded user #1)."""
    client, SessionLocal = client_db

    db = SessionLocal()
    try:
        clerk = User(name="Clerk", username="clerk", is_active=True,
                     password_hash=hash_password("pw"), role=UserRole.SALES)
        db.add(clerk)
        cust = Customer(company_name="Attribution Call Co", contact_name="QA",
                        email="ac@test.local")
        db.add(cust)
        db.commit()
        clerk_id, customer_id = clerk.id, cust.id
    finally:
        db.close()
    assert clerk_id != DEFAULT_USER_ID, "test is meaningless if clerk == default actor"

    r = client.post("/login", data={"username": "clerk", "password": "pw"},
                    follow_redirects=False)
    assert r.status_code == 303 and SESSION_COOKIE in r.cookies

    r = client.post(f"/customers/{customer_id}/log-call", data={
        "call_type": CallType.INBOUND,
        "outcome": CallOutcome.OTHER,
        "notes": "Attribution check",
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text[:300]

    db = SessionLocal()
    try:
        row = (db.query(CustomerCallLog)
               .filter(CustomerCallLog.customer_id == customer_id)
               .order_by(CustomerCallLog.id.desc()).first())
        assert row is not None, "no call-log row was written"
        assert row.logged_by_id == clerk_id, (
            f"call log attributed to user {row.logged_by_id}; expected the "
            f"signed-in clerk {clerk_id} (DEFAULT_USER_ID is {DEFAULT_USER_ID})"
        )
    finally:
        db.close()


def test_unauthenticated_call_log_falls_back_to_default_actor(client_db):
    """Counter-case: with no session cookie the same write attributes to
    DEFAULT_USER_ID — proving the attribution above is session-driven, not
    incidental."""
    client, SessionLocal = client_db

    db = SessionLocal()
    try:
        cust = Customer(company_name="Anon Call Co", contact_name="QA",
                        email="anc@test.local")
        db.add(cust); db.commit()
        customer_id = cust.id
    finally:
        db.close()

    # No /login — the request carries no session cookie.
    r = client.post(f"/customers/{customer_id}/log-call", data={
        "call_type": CallType.INBOUND,
        "outcome": CallOutcome.OTHER,
        "notes": "Anonymous",
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text[:300]

    db = SessionLocal()
    try:
        row = (db.query(CustomerCallLog)
               .filter(CustomerCallLog.customer_id == customer_id)
               .order_by(CustomerCallLog.id.desc()).first())
        assert row is not None
        assert row.logged_by_id == DEFAULT_USER_ID, (
            f"unauthenticated write attributed to {row.logged_by_id}, expected "
            f"DEFAULT_USER_ID {DEFAULT_USER_ID}"
        )
    finally:
        db.close()


# ── 2. the bookkeeping (wife) user is seeded at startup ───────────────────────

def test_startup_seeds_non_admin_bookkeeping_user(client_db):
    """A second, non-admin BOOKKEEPING user exists after startup and can log in."""
    client, SessionLocal = client_db

    db = SessionLocal()
    try:
        bk = db.query(User).filter(User.role == UserRole.BOOKKEEPING).first()
        assert bk is not None, "startup did not seed a bookkeeping (second) user"
        assert bk.is_active is True
        assert bk.is_admin is False, "the bookkeeping user must NOT be an admin"
    finally:
        db.close()

    # The seeded credentials actually work.
    r = client.post("/login", data={"username": "bookkeeper", "password": "bookkeeper"},
                    follow_redirects=False)
    assert r.status_code == 303 and SESSION_COOKIE in r.cookies
