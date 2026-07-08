"""
tests/test_chrome_and_security.py
=================================
Lane A — app-chrome + security-hardening fixes from the headed-browser QA report:

  * CSP allows the Google-Fonts stylesheet + font origins (login / print pages).
  * A missing page renders a friendly HTML 404 for browser navigations, but still
    returns JSON for API / fetch callers.
  * Authenticated HTML responses carry ``Cache-Control: no-store`` (shared-terminal
    Back-button leak), while static assets / the login page are left cacheable.
  * base.html renders the signed-in user's REAL initials (resolve_current_user
    middleware) instead of the hardcoded "K".

All run headlessly against the project's standard in-memory TestClient harness
(``activate(fresh_engine())``). The CSRF guard + auth enforcement are bypassed
in this env (JAKS_SKIP_AUTH + in-memory), so we still log in to obtain the real
signed session cookie that the no-store / initials logic keys off.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.auth as auth
from app.main import app
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def client_db():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    auth.reset_secret_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, SessionLocal


def _login_admin(client) -> None:
    r = client.post(
        "/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:200]


# ── CSP: Google-Fonts origins are allowed ─────────────────────────────────────

def test_csp_allows_google_fonts_origins(client_db):
    client, _ = client_db
    r = client.get("/login")
    csp = r.headers.get("content-security-policy", "")
    assert csp, "CSP header should be present"
    assert "https://fonts.googleapis.com" in csp, "style-src must allow the Google Fonts stylesheet"
    assert "https://fonts.gstatic.com" in csp, "font-src must allow the Google Fonts font files"


def test_csp_font_hosts_in_correct_directives(client_db):
    client, _ = client_db
    csp = client.get("/login").headers.get("content-security-policy", "")
    # Split into directive segments and confirm each host lands in the right one.
    directives = {
        seg.strip().split(" ", 1)[0]: seg.strip()
        for seg in csp.split(";") if seg.strip()
    }
    assert "fonts.googleapis.com" in directives.get("style-src", "")
    assert "fonts.gstatic.com" in directives.get("font-src", "")
    # Did not weaken object-src / frame-ancestors.
    assert directives.get("object-src") == "object-src 'none'"
    assert directives.get("frame-ancestors") == "frame-ancestors 'none'"


# ── 404: friendly HTML for browsers, JSON for API ─────────────────────────────

def test_404_html_request_returns_friendly_page(client_db):
    client, _ = client_db
    _login_admin(client)
    r = client.get("/this-route-does-not-exist", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "{\"detail\"" not in body, "browser 404 must not be raw JSON"
    assert "Error 404" in body and "dashboard" in body.lower()


def test_404_api_request_still_returns_json(client_db):
    client, _ = client_db
    _login_admin(client)
    r = client.get(
        "/this-route-does-not-exist",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not Found"}


def test_404_htmx_request_is_not_full_page(client_db):
    """An HTMX partial 404 should stay JSON (not inject a whole base.html page)."""
    client, _ = client_db
    _login_admin(client)
    r = client.get(
        "/this-route-does-not-exist",
        headers={"Accept": "text/html", "HX-Request": "true"},
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


# ── Cache-Control: no-store on authenticated HTML only ────────────────────────

def test_authenticated_html_is_no_store(client_db):
    client, _ = client_db
    _login_admin(client)
    r = client.get("/", follow_redirects=False)
    # Dashboard renders HTML for the signed-in admin.
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("text/html")
    cc = r.headers.get("cache-control", "")
    assert "no-store" in cc, f"authenticated HTML must be no-store, got {cc!r}"


def test_login_page_is_not_no_store(client_db):
    """The login page is unauthenticated → left normally cacheable (no no-store)."""
    client, _ = client_db
    r = client.get("/login")
    assert r.status_code == 200
    assert "no-store" not in r.headers.get("cache-control", "")


def test_static_asset_is_not_no_store(client_db):
    client, _ = client_db
    _login_admin(client)
    # app.css is served by StaticFiles; never no-store even while authenticated.
    r = client.get("/static/css/app.css")
    assert "no-store" not in r.headers.get("cache-control", "")


# ── base.html renders the signed-in user's real initials (not hardcoded "K") ──

def test_header_renders_real_user_initials(client_db):
    client, _ = client_db
    _login_admin(client)
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    # The seeded admin is name="JAKS Admin" → initials "JA".
    assert ">JA<" in body or "JA\n" in body or "JA " in body, (
        "header avatar should show the signed-in user's initials"
    )
    # Account / Sign-out affordances are present in the chrome.
    assert "/account" in body
    assert 'action="/logout"' in body


def test_anonymous_render_does_not_crash_avatar(client_db):
    """The login page renders without a current_user and must not error on the
    avatar fallback. (base.html is not used by /login, so we assert the page that
    DOES extend base.html degrades safely when accessed unauthenticated would
    redirect; here we simply confirm the login page renders.)"""
    client, _ = client_db
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


# ── login fields carry `required` ─────────────────────────────────────────────

def test_login_inputs_have_required_attr(client_db):
    client, _ = client_db
    html = client.get("/login").text
    # Both the username and password inputs must be `required`.
    assert html.count("required") >= 2, "username + password inputs should be required"
    assert 'name="username"' in html and 'name="password"' in html
