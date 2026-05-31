"""
tests/test_auth.py
==================
Tests for O2 minimal login + audit attribution.

Covers the auth primitives (pbkdf2 hash/verify, signed session token) and the
HTTP login flow, plus the attribution seam: get_current_user_id returns the
signed-in user when a valid cookie is present and falls back to DEFAULT_USER_ID
otherwise (so unauthenticated / TestClient calls keep working in single-user mode).
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
