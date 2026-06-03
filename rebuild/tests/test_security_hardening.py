"""
tests/test_security_hardening.py
================================
X7-P1 hardening guards (see PHASE_1_TEST_PLAN.md §10 Suite E):

  E-6 CSRF — the session cookie must carry SameSite=Lax + HttpOnly. SameSite=Lax
       stops a cross-site page's POST from carrying the session cookie, which is
       the CSRF defense for cookie-based auth on this same-origin HTMX app. This
       test guards against a future change silently dropping those flags.

  E-5 strong admin password — the signed-in user can change their own password
       in-app (so they can get off the default 'admin' without an env reseed),
       with current-password verification, a min length, and a confirm match.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.auth as auth
from app.auth import SESSION_COOKIE, verify_password
from app.main import app
from app.models.user import User
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def client_db():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    auth.reset_secret_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, SessionLocal


def _login_admin(client):
    r = client.post("/login", data={"username": "admin", "password": "admin"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    return r


# ── E-6: CSRF mitigation via SameSite cookie ──────────────────────────────────

def test_session_cookie_is_httponly_and_samesite_lax(client_db):
    client, _ = client_db
    r = _login_admin(client)
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert SESSION_COOKIE in set_cookie, "login did not set the session cookie"
    assert "httponly" in set_cookie, "session cookie must be HttpOnly"
    assert "samesite=lax" in set_cookie, (
        "session cookie must be SameSite=Lax (the CSRF defense for cookie auth)"
    )


# ── E-5: self-service change password ─────────────────────────────────────────

def test_change_password_happy_path(client_db):
    client, SessionLocal = client_db
    _login_admin(client)

    r = client.post("/account/password", data={
        "current_password": "admin",
        "new_password": "a-strong-passphrase",
        "confirm_password": "a-strong-passphrase",
    }, follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"], r.text[:200]

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert verify_password("a-strong-passphrase", admin.password_hash), "new password not set"
        assert not verify_password("admin", admin.password_hash), "old password still works"
    finally:
        db.close()


def test_change_password_rejects_wrong_current(client_db):
    client, SessionLocal = client_db
    _login_admin(client)

    r = client.post("/account/password", data={
        "current_password": "WRONG",
        "new_password": "a-strong-passphrase",
        "confirm_password": "a-strong-passphrase",
    }, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert verify_password("admin", admin.password_hash), "password must be unchanged on a bad attempt"
    finally:
        db.close()


def test_change_password_rejects_too_short(client_db):
    client, SessionLocal = client_db
    _login_admin(client)

    r = client.post("/account/password", data={
        "current_password": "admin",
        "new_password": "short",
        "confirm_password": "short",
    }, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert verify_password("admin", admin.password_hash), "weak password must be rejected"
    finally:
        db.close()


def test_change_password_rejects_mismatch(client_db):
    client, SessionLocal = client_db
    _login_admin(client)

    r = client.post("/account/password", data={
        "current_password": "admin",
        "new_password": "a-strong-passphrase",
        "confirm_password": "different-passphrase",
    }, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert verify_password("admin", admin.password_hash), "mismatch must not change the password"
    finally:
        db.close()
