"""
tests/test_s22_password_rotation.py
===================================
Lane G (§23 / Risk #8/#14) — default-password rotation gate + launcher hardening.

Two halves:

1. **The redirect gate** (``enforce_password_rotation`` in app/main.py): while a
   signed-in user's account STILL verifies its shipped default password
   (admin/admin or bookkeeper/bookkeeper), every authenticated navigation is
   forced to the change-password screen (/account) — non-dismissable. Once the
   password is rotated, normal navigation resumes.

   The whole suite runs with JAKS_SKIP_AUTH=1 (conftest sets it at import), which
   no-ops the gate. These tests DELETE that env var (mirroring
   tests/test_auth_enforcement.py) so the real middleware runs, then drive a real
   signed-in session via a valid jaks_session cookie.

2. **Launcher hardening**: the two real launchers export JAKS_ENV=production and
   no longer print the admin/admin credentials.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_s22_password_rotation.py -q
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import SESSION_COOKIE, make_session_token, hash_password, verify_password
from app.models.user import User
from app.constants import UserRole
from tests.conftest import activate, fresh_engine

# A normal authenticated page that does NOT require a special role (any signed-in
# user reaches the products list). Used as the "normal navigation" probe so the
# gate test does not couple to another lane's role gate on /dashboard.
_NORMAL_PAGE = "/products/"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── shared engine + seeding ───────────────────────────────────────────────────

def _seed_admin(SessionLocal, password: str) -> int:
    """Seed (or reset) an ADMIN 'admin' user with the given password; return id."""
    db = SessionLocal()
    try:
        db.query(User).delete()
        u = User(
            name="JAKS Admin",
            username="admin",
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _cookie_client(user_id: int) -> TestClient:
    """A TestClient carrying a valid signed session cookie for ``user_id``."""
    c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    c.cookies.set(SESSION_COOKIE, make_session_token(user_id))
    return c


# ── 1. the gate ───────────────────────────────────────────────────────────────

def test_default_password_redirects_to_change_password(monkeypatch):
    """A signed-in user whose account still verifies the default password is
    redirected (303 → /account) on a normal page — the gate is non-dismissable."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)   # real gate ON
    engine = fresh_engine()
    SessionLocal = activate(engine)
    uid = _seed_admin(SessionLocal, "admin")

    c = _cookie_client(uid)
    r = c.get(_NORMAL_PAGE)
    assert r.status_code in (302, 303), (
        f"expected the rotation gate to redirect, got {r.status_code}")
    assert r.headers.get("location") == "/account", (
        f"gate must send the user to the change-password flow, got "
        f"{r.headers.get('location')!r}")


def test_account_and_logout_remain_reachable_under_gate(monkeypatch):
    """The gate must NOT trap the user away from the very pages they need to
    rotate the password: /account (the form) and /logout stay reachable."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    SessionLocal = activate(engine)
    uid = _seed_admin(SessionLocal, "admin")

    c = _cookie_client(uid)
    r_account = c.get("/account")
    # /account is exempt — it must NOT bounce back to /account in a redirect loop.
    assert not (
        r_account.status_code in (302, 303)
        and r_account.headers.get("location") == "/account"
    ), "the change-password page must be reachable under the gate (no loop)"

    r_logout = c.post("/logout")
    assert r_logout.headers.get("location") == "/login", (
        "logout must remain reachable so a gated user can sign out")


def test_navigation_resumes_after_password_rotation(monkeypatch):
    """After the password is rotated off the default, normal navigation works —
    the gate no longer fires for that user."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    SessionLocal = activate(engine)
    uid = _seed_admin(SessionLocal, "admin")

    c = _cookie_client(uid)
    # Pre-condition: gate is active.
    pre = c.get(_NORMAL_PAGE)
    assert pre.headers.get("location") == "/account"

    # Rotate the password via the real change-password flow. CSRF is enforced now
    # (test bypass is off), so fetch /account first to receive the jaks_csrf
    # cookie, then echo it back via the X-CSRF-Token header.
    form = c.get("/account")
    csrf = c.cookies.get("jaks_csrf")
    assert csrf, "expected a jaks_csrf cookie to be issued for the form"
    posted = c.post(
        "/account/password",
        data={
            "current_password": "admin",
            "new_password": "a-strong-new-pw",
            "confirm_password": "a-strong-new-pw",
        },
        headers={"X-CSRF-Token": csrf},
    )
    # change_password redirects to /account?ok=1 on success.
    assert posted.status_code in (302, 303)
    assert "ok=1" in posted.headers.get("location", ""), (
        f"password change did not succeed: {posted.headers.get('location')!r}")

    # Confirm the hash really changed in the DB.
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        assert verify_password("a-strong-new-pw", u.password_hash)
        assert not verify_password("admin", u.password_hash)
    finally:
        db.close()

    # Now normal navigation resumes — no /account bounce.
    post = c.get(_NORMAL_PAGE)
    assert not (
        post.status_code in (302, 303)
        and post.headers.get("location") == "/account"
    ), "after rotation the gate must stop firing"


def test_bookkeeper_default_password_also_gated(monkeypatch):
    """The gate covers the bookkeeper default too, not just admin."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        db.query(User).delete()
        u = User(
            name="JAKS Bookkeeping",
            username="bookkeeper",
            password_hash=hash_password("bookkeeper"),
            role=UserRole.BOOKKEEPING,
            is_active=True,
        )
        db.add(u)
        db.commit()
        uid = u.id
    finally:
        db.close()

    c = _cookie_client(uid)
    r = c.get(_NORMAL_PAGE)
    assert r.headers.get("location") == "/account", (
        "bookkeeper on the default password must be gated too")


# ── 2. launcher hardening ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "launcher",
    ["Start JAKS ERP.bat", "Start JAKS ERP (LAN).bat"],
)
def test_real_launchers_set_production_env(launcher):
    text = (_REPO_ROOT / launcher).read_text(encoding="utf-8", errors="ignore")
    assert "JAKS_ENV=production" in text, (
        f"{launcher} must export JAKS_ENV=production")


@pytest.mark.parametrize(
    "launcher",
    ["Start JAKS ERP.bat", "Start JAKS ERP (LAN).bat", "START JAKS.bat"],
)
def test_launchers_do_not_print_default_credentials(launcher):
    text = (_REPO_ROOT / launcher).read_text(encoding="utf-8", errors="ignore").lower()
    assert "admin / admin" not in text, (
        f"{launcher} must not echo the admin/admin credentials")
    assert "admin/admin" not in text, (
        f"{launcher} must not echo the admin/admin credentials")
