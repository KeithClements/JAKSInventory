"""
tests/test_s21_csrf_security.py
===============================
§21.3 internet-exposed hardening — CSRF double-submit guard + security headers.

CSRF is bypassed for the main suite (JAKS_SKIP_AUTH + in-memory engine), so these
tests build a tiny isolated app, force _test_bypass() OFF, and exercise the
middleware directly — including that the body-buffering REPLAY lets the downstream
route still read the posted form (the BaseHTTPMiddleware trap this design avoids).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import security
from app.security import CSRFMiddleware, security_headers_middleware, CSRF_COOKIE


@pytest.fixture()
def app_client(monkeypatch):
    # Force production behavior (no test bypass) so the guard actually runs.
    monkeypatch.setattr(security, "_test_bypass", lambda: False)
    # CSRF only validates AUTHENTICATED requests (it skips when there's no
    # session, letting the auth layer redirect). This bare app has no auth, so
    # simulate a logged-in session to exercise the validation path.
    import app.auth as auth
    monkeypatch.setattr(auth, "read_session_token", lambda *a, **k: 1)

    app = FastAPI()
    app.add_middleware(CSRFMiddleware)
    app.middleware("http")(security_headers_middleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.post("/echo")
    async def echo(request: Request):
        form = await request.form()           # proves the body survived the middleware
        return JSONResponse({"got": form.get("field"), "csrf_seen": form.get("_csrf")})

    return TestClient(app)


def _token(client) -> str:
    client.get("/ping")                        # issues the cookie
    return client.cookies.get(CSRF_COOKIE)


def test_get_issues_csrf_cookie(app_client):
    r = app_client.get("/ping")
    assert r.status_code == 200
    assert app_client.cookies.get(CSRF_COOKIE)


def test_security_headers_present(app_client):
    r = app_client.get("/ping")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_post_without_token_is_forbidden(app_client):
    _token(app_client)                         # have a cookie, but send no token
    r = app_client.post("/echo", data={"field": "x"})
    assert r.status_code == 403


def test_post_with_header_token_passes(app_client):
    tok = _token(app_client)
    r = app_client.post("/echo", data={"field": "x"},
                        headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert r.json()["got"] == "x"              # downstream still read the body


def test_post_with_form_field_token_passes_and_body_survives(app_client):
    tok = _token(app_client)
    # Native-form style: token in the _csrf field, no header. The replay must let
    # the route read BOTH fields.
    r = app_client.post("/echo", data={"field": "y", "_csrf": tok})
    assert r.status_code == 200
    body = r.json()
    assert body["got"] == "y" and body["csrf_seen"] == tok


def test_post_with_wrong_token_is_forbidden(app_client):
    _token(app_client)
    r = app_client.post("/echo", data={"field": "x", "_csrf": "not-the-token"})
    assert r.status_code == 403


def test_multipart_form_field_token_passes(app_client):
    tok = _token(app_client)
    # multipart/form-data (file-upload shape) — token carried in the _csrf part.
    r = app_client.post(
        "/echo",
        data={"field": "z", "_csrf": tok},
        files={"upload": ("a.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["got"] == "z"
