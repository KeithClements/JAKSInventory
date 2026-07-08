"""
tests/test_auth.py
==================
Tests for O2 minimal login + audit attribution, including the O2-ENFORCE update
(redirect to /login when no session in production; test bypass via in-memory DB).

Covers:
  - pbkdf2 hash/verify + signed session token primitives
  - HTTP login flow
  - Attribution seam (get_current_user_id → signed-in user or DEFAULT_USER_ID)
  - Enforcement behaviour: unauthenticated → 302 /login in production, or
    DEFAULT_USER_ID fallback in tests (in-memory DB bypass)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi import Request
from fastapi.testclient import TestClient

import app.auth as auth
from app.auth import (
    SESSION_COOKIE,
    hash_password,
    make_session_token,
    read_session_token,
    verify_password,
)
import app.deps as _deps
from app.deps import DEFAULT_USER_ID, get_current_user_id
from app.main import app
from app.models.user import User
from app.constants import UserRole
from tests.conftest import activate, fresh_engine


# ── password hashing ──────────────────────────────────────────────────────────

def test_hash_is_not_plaintext_and_is_pbkdf2():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert h.startswith("pbkdf2_")
    # salted: two hashes of the same password differ
    assert hash_password("hunter2") != h


def test_verify_password_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) is True
    assert verify_password("wrong horse", h) is False


def test_verify_rejects_legacy_placeholder():
    # The pre-O2 placeholder must never authenticate.
    assert verify_password("anything", "[single-user-mode-no-auth]") is False
    assert verify_password("", "") is False
    assert verify_password("x", None) is False


# ── session token ─────────────────────────────────────────────────────────────

def test_session_token_roundtrip():
    activate(fresh_engine())          # ensures a settings table / secret path
    auth.reset_secret_cache()
    token = make_session_token(42)
    assert read_session_token(token) == 42


def test_session_token_tamper_rejected():
    activate(fresh_engine())
    auth.reset_secret_cache()
    token = make_session_token(7)
    assert read_session_token(token + "x") is None
    assert read_session_token("garbage") is None
    assert read_session_token(None) is None


def test_session_token_expired_rejected():
    activate(fresh_engine())
    auth.reset_secret_cache()
    token = make_session_token(5)
    # max_age=-1 → any token is older than the allowed age (itsdangerous uses
    # a strict age>max_age check, so max_age=0 would NOT reject a 0s-old token).
    assert read_session_token(token, max_age=-1) is None


# ── HTTP login flow ───────────────────────────────────────────────────────────

@pytest.fixture()
def client_db():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    auth.reset_secret_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        # startup seeded session secret + admin user (real hash, pw "admin")
        yield c, SessionLocal


def test_login_page_renders(client_db):
    client, _ = client_db
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_login_bad_credentials_no_cookie(client_db):
    client, _ = client_db
    r = client.post("/login", data={"username": "admin", "password": "wrong"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/login?error=1" in r.headers["location"]
    assert SESSION_COOKIE not in r.cookies


def test_login_good_credentials_sets_cookie(client_db):
    client, _ = client_db
    r = client.post("/login", data={"username": "admin", "password": "admin"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert SESSION_COOKIE in r.cookies


def test_logout_clears_cookie(client_db):
    client, _ = client_db
    client.post("/login", data={"username": "admin", "password": "admin"},
                follow_redirects=False)
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ── attribution seam (get_current_user_id) ────────────────────────────────────

def _fake_request(cookies: dict) -> Request:
    scope = {
        "type": "http",
        "headers": [
            (b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode())
        ] if cookies else [],
    }
    return Request(scope)


def test_get_current_user_id_falls_back_without_cookie():
    activate(fresh_engine())
    auth.reset_secret_cache()
    assert get_current_user_id(_fake_request({})) == DEFAULT_USER_ID


def test_get_current_user_id_reads_session_cookie(client_db):
    client, SessionLocal = client_db
    # Seed a distinct user and forge their valid session cookie.
    db = SessionLocal()
    try:
        u = User(name="Pat", username="pat",
                 password_hash=hash_password("pw"), role=UserRole.SALES)
        db.add(u); db.commit(); db.refresh(u)
        uid = u.id
    finally:
        db.close()

    token = make_session_token(uid)
    assert get_current_user_id(_fake_request({SESSION_COOKIE: token})) == uid


# ── end-to-end: the signed-in user is the actor on a FINANCIAL write ───────────
#    THE last-1A-gate proof: login → get_current_user_id(cookie) → the payments
#    route's PaymentService.record_payment → audit() stamps the real actor. The
#    seam tests above prove the dependency reads the cookie; these prove it flows
#    all the way onto a money write's audit row.

def test_signed_in_user_is_actor_on_payment(client_db):
    from app.constants import AuditAction, EntityType, PaymentMethod, UserRole
    from app.models.audit import AuditLog
    from app.models.customer import Customer

    client, SessionLocal = client_db

    # A distinct, fully-permissioned user (id != seeded admin #1) + a customer.
    db = SessionLocal()
    try:
        cashier = User(name="Cashier", username="cashier", is_active=True,
                       password_hash=hash_password("pw"), role=UserRole.ADMIN)
        db.add(cashier)
        cust = Customer(company_name="Attribution Co", contact_name="QA",
                        email="attr@test.local")
        db.add(cust)
        db.commit()
        cashier_id, customer_id = cashier.id, cust.id
    finally:
        db.close()
    assert cashier_id != DEFAULT_USER_ID, "test is meaningless if cashier == default actor"

    # Log in — the session cookie now rides on every subsequent client request.
    r = client.post("/login", data={"username": "cashier", "password": "pw"},
                    follow_redirects=False)
    assert r.status_code == 303 and SESSION_COOKIE in r.cookies

    # Record a payment (a financial write) through the route while signed in.
    r = client.post("/payments/new", data={
        "customer_id": str(customer_id),
        "amount": "50.00",
        "method": PaymentMethod.CASH.value,
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text[:300]

    # The payment's audit row must name the signed-in cashier as the actor.
    db = SessionLocal()
    try:
        row = (db.query(AuditLog)
               .filter(AuditLog.entity_type == EntityType.PAYMENT,
                       AuditLog.action == AuditAction.PAYMENT_APPLIED)
               .order_by(AuditLog.id.desc()).first())
        assert row is not None, "no PAYMENT_APPLIED audit row was written"
        assert row.user_id == cashier_id, (
            f"financial write attributed to user {row.user_id}; expected the "
            f"signed-in cashier {cashier_id} (DEFAULT_USER_ID is {DEFAULT_USER_ID})"
        )
    finally:
        db.close()


def test_unauthenticated_financial_write_falls_back_to_default_actor(client_db):
    """Counter-case: with NO session cookie the same write attributes to
    DEFAULT_USER_ID — proving the cashier attribution above is session-driven,
    not incidental."""
    from app.constants import AuditAction, EntityType, PaymentMethod
    from app.models.audit import AuditLog
    from app.models.customer import Customer

    client, SessionLocal = client_db
    db = SessionLocal()
    try:
        cust = Customer(company_name="Anon Co", contact_name="QA", email="anon@test.local")
        db.add(cust); db.commit()
        customer_id = cust.id
    finally:
        db.close()

    # No /login: the request carries no session cookie.
    r = client.post("/payments/new", data={
        "customer_id": str(customer_id),
        "amount": "25.00",
        "method": PaymentMethod.CASH.value,
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text[:300]

    db = SessionLocal()
    try:
        row = (db.query(AuditLog)
               .filter(AuditLog.entity_type == EntityType.PAYMENT,
                       AuditLog.action == AuditAction.PAYMENT_APPLIED)
               .order_by(AuditLog.id.desc()).first())
        assert row is not None
        assert row.user_id == DEFAULT_USER_ID, (
            f"unauthenticated write attributed to {row.user_id}, expected "
            f"DEFAULT_USER_ID {DEFAULT_USER_ID}"
        )
    finally:
        db.close()


# ── O2-ENFORCE: redirect when no session in production ───────────────────────

def _htmx_request(cookies: dict) -> Request:
    scope = {
        "type": "http",
        "headers": [
            (b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()),
            (b"hx-request", b"true"),
        ],
    }
    return Request(scope)


def test_enforcement_raises_302_when_no_session_in_production(monkeypatch):
    """In production (non-test env) a missing session must redirect to /login."""
    from fastapi import HTTPException
    monkeypatch.setattr(_deps, "_is_test_env", lambda: False)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(_fake_request({}))
    assert exc_info.value.status_code == 302
    assert exc_info.value.headers["Location"] == "/login"


def test_enforcement_sends_hx_redirect_for_htmx_requests(monkeypatch):
    """HTMX requests get HX-Redirect (200 + header) not a bare 302 redirect.
    This prevents the login-page HTML from being injected into a partial slot."""
    from fastapi import HTTPException
    monkeypatch.setattr(_deps, "_is_test_env", lambda: False)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(_htmx_request({}))
    assert exc_info.value.status_code == 200
    assert exc_info.value.headers.get("HX-Redirect") == "/login"


def test_test_env_bypass_returns_default_user_id():
    """In test mode (in-memory DB) a missing cookie falls back to DEFAULT_USER_ID."""
    activate(fresh_engine())
    auth.reset_secret_cache()
    # _is_test_env() returns True naturally because activate() set :memory: engine
    result = get_current_user_id(_fake_request({}))
    assert result == DEFAULT_USER_ID


def test_valid_cookie_always_wins_over_enforcement(monkeypatch):
    """A valid session cookie returns the correct user id regardless of env."""
    activate(fresh_engine())
    auth.reset_secret_cache()
    # Even with prod-mode enforcement active, a real cookie should pass
    monkeypatch.setattr(_deps, "_is_test_env", lambda: False)
    token = make_session_token(99)
    assert get_current_user_id(_fake_request({SESSION_COOKIE: token})) == 99
