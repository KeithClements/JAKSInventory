"""
tests/test_login_throttle.py
============================
FULL_ERP_REVIEW_2026-07-04 HIGH — the LAN login had no lockout, so default-cred
guessing was free. After LOGIN_FAIL_MAX failures for the same username+IP the login
is refused (without even checking the password) for LOGIN_LOCK_SECONDS.

Mirrors tests/test_s22_password_rotation: delete JAKS_SKIP_AUTH so the real path
runs (the throttle is bypassed under the suite's auth bypass).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_login_throttle.py -q
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app
from app.auth import SESSION_COOKIE, hash_password
from app.constants import UserRole
from app.models.user import User
from app.routers.auth import LOGIN_FAIL_MAX, reset_login_throttle
from tests.conftest import activate, fresh_engine


def _seed_user(SessionLocal, username: str, password: str) -> None:
    db = SessionLocal()
    try:
        db.query(User).delete()
        db.add(User(
            name=username.title(), username=username,
            password_hash=hash_password(password),
            role=UserRole.ADMIN, is_active=True,
        ))
        db.commit()
    finally:
        db.close()


def test_lockout_after_max_failures_then_reset(monkeypatch):
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)   # real throttle ON
    SessionLocal = activate(fresh_engine())
    _seed_user(SessionLocal, "admin", "correct-horse")
    reset_login_throttle()

    c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)

    # Burn the allowance with wrong passwords.
    for _ in range(LOGIN_FAIL_MAX):
        r = c.post("/login", data={"username": "admin", "password": "wrong"})
        assert r.headers.get("location") == "/login?error=1"

    # Now even the CORRECT password is refused — locked, no session issued.
    blocked = c.post("/login", data={"username": "admin", "password": "correct-horse"})
    assert blocked.headers.get("location") == "/login?error=locked", (
        "login should be locked after too many failures")
    assert SESSION_COOKIE not in blocked.cookies

    # Manual unlock (a restart clears the in-process state) lets the right password in.
    reset_login_throttle()
    ok = c.post("/login", data={"username": "admin", "password": "correct-horse"})
    assert ok.headers.get("location") == "/", "correct password must work after reset"
    assert SESSION_COOKIE in ok.cookies


def test_throttle_is_bypassed_under_test_env():
    """With the suite's auth bypass active (JAKS_SKIP_AUTH set by conftest), the
    throttle must be inert so ordinary tests can hammer /login freely."""
    from app.routers.auth import _throttle_active
    assert _throttle_active() is False
