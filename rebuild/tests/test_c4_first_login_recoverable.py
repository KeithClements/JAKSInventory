"""
tests/test_c4_first_login_recoverable.py
========================================
C4 (FULL_ERP_REVIEW_2026-07-04) — first login must be recoverable without a dev.

Two failures made a fresh install un-onboardable:

1. **The forced-rotation form always 403'd.** ``auth/account.html`` is a standalone
   page that does NOT extend base.html, so the global CSRF stamper never ran and the
   form carried no ``_csrf`` field. A real browser submits the token via the FORM
   FIELD (not the ``X-CSRF-Token`` header), so the double-submit CSRFMiddleware
   rejected every submit with a 403 — and the rotation gate blocks every other page,
   trapping the user. NOTE: the pre-existing rotation test posted with the *header*,
   which is why the trap went unnoticed. These tests drive the FORM-FIELD path.

2. **A role-denied landing was raw JSON.** A SALES hire logs in → ``/`` is gated to
   admin/bookkeeper → the browser showed ``{"detail": "..."}`` with no nav. Now 403s
   for a browser render a branded page that extends base.html (nav = a way forward).

Harness mirrors tests/test_s22_password_rotation.py: delete JAKS_SKIP_AUTH so the
real CSRF + gate run, and drive a real signed session cookie for a seeded user.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_c4_first_login_recoverable.py -q
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app
from app.auth import SESSION_COOKIE, make_session_token, hash_password, verify_password
from app.models.user import User
from app.constants import UserRole
from tests.conftest import activate, fresh_engine


def _seed_user(SessionLocal, username: str, password: str, role: UserRole) -> int:
    db = SessionLocal()
    try:
        db.query(User).delete()
        u = User(
            name=username.title(),
            username=username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _cookie_client(user_id: int) -> TestClient:
    c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    c.cookies.set(SESSION_COOKIE, make_session_token(user_id))
    return c


# ── 1. the rotation form is submittable (the trap is gone) ─────────────────────

def test_account_page_carries_csrf_field_and_stamper(monkeypatch):
    """The rendered /account page must contain a _csrf field AND the cookie-reading
    stamper script, so a browser can produce a valid token on its own."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    SessionLocal = activate(engine)
    uid = _seed_user(SessionLocal, "admin", "admin", UserRole.ADMIN)

    c = _cookie_client(uid)
    r = c.get("/account")
    assert r.status_code == 200
    body = r.text
    assert 'name="_csrf"' in body, "rotation form is missing the _csrf hidden field"
    assert "jaks_csrf" in body, "rotation page is missing the cookie-reading CSRF stamper"


def test_rotation_form_field_csrf_is_accepted(monkeypatch):
    """The real-browser path: POST with the _csrf FORM FIELD (no header) must be
    accepted — this is the exact submit that 403'd before the fix, trapping the user
    on the rotation gate. Then the gate must release."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    SessionLocal = activate(engine)
    uid = _seed_user(SessionLocal, "admin", "admin", UserRole.ADMIN)

    c = _cookie_client(uid)
    # Under the gate, a normal page bounces to /account.
    assert c.get("/products/").headers.get("location") == "/account"

    # Fetch the form to receive the jaks_csrf cookie the browser would carry.
    c.get("/account")
    csrf = c.cookies.get("jaks_csrf")
    assert csrf, "expected a jaks_csrf cookie to be issued for the form"

    # Submit exactly as the stamped native form would: token in the FORM FIELD,
    # NOT the X-CSRF-Token header.
    posted = c.post(
        "/account/password",
        data={
            "current_password": "admin",
            "new_password": "a-strong-new-pw",
            "confirm_password": "a-strong-new-pw",
            "_csrf": csrf,
        },
    )
    assert posted.status_code in (302, 303), (
        f"form-field CSRF submit was rejected ({posted.status_code}) — the "
        f"first-login trap is back: {posted.text[:120]!r}")
    assert "ok=1" in posted.headers.get("location", "")

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        assert verify_password("a-strong-new-pw", u.password_hash)
    finally:
        db.close()

    # Gate releases now that the default password is gone.
    assert c.get("/products/").status_code == 200


def test_rotation_form_without_token_still_403s(monkeypatch):
    """Guard against over-correcting: a submit with NO token at all must still be
    refused (we fixed the stamping, not disabled CSRF on the auth route)."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    SessionLocal = activate(engine)
    uid = _seed_user(SessionLocal, "admin", "admin", UserRole.ADMIN)

    c = _cookie_client(uid)
    c.get("/account")  # receive the cookie, but do NOT echo it back
    r = c.post(
        "/account/password",
        data={
            "current_password": "admin",
            "new_password": "a-strong-new-pw",
            "confirm_password": "a-strong-new-pw",
        },
    )
    assert r.status_code == 403, "CSRF must still reject a tokenless auth POST"


# ── 2. role-denied landing renders HTML, not raw JSON ──────────────────────────

def test_role_denied_dashboard_renders_html_not_json(monkeypatch):
    """A SALES user (non-default password → past the rotation gate) hitting the
    admin-only dashboard must get a branded HTML 403 with nav, not a JSON blob."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    SessionLocal = activate(engine)
    uid = _seed_user(SessionLocal, "clerk", "a-strong-clerk-pw", UserRole.SALES)

    c = _cookie_client(uid)
    r = c.get("/", headers={"accept": "text/html"})
    assert r.status_code == 403
    assert "text/html" in r.headers.get("content-type", ""), (
        "role-denied browser navigation still returns non-HTML")
    # It is the branded page (extends base.html), not the raw {"detail": ...} JSON.
    assert '{"detail"' not in r.text
    assert "Access restricted" in r.text
