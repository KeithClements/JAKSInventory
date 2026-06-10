"""
tests/test_r1_security_startup.py
=================================
R1-15 (partial) + R1-9 (startup half) guards.

1. JAKS_SKIP_AUTH gate — the env-var auth bypass is honored ONLY when the app
   runs on an in-memory test engine (deps._is_test_env). A stray
   JAKS_SKIP_AUTH=1 in a production environment (file DB) must NOT disable the
   enforce_login middleware.
2. Session-secret resolution order — JAKS_SESSION_SECRET env var wins; unset it
   and the DB-stored ``session_secret_key`` setting takes over (backward
   compatible); with neither, the insecure module fallback is returned.
3. Startup overdue-core scan — on_startup calls CoreService.mark_overdue_cores
   (R1-9: it previously had no caller) and swallows scan exceptions so startup
   never crashes because of it.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r1_security_startup.py -q
"""
from __future__ import annotations

import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.auth as auth
import app.database as _appdb
import app.deps as deps
from app.main import app
from app.services.core_service import CoreService
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


@pytest.fixture(autouse=True)
def _clean_secret_state(monkeypatch):
    """Keep the process-global secret cache + env var from leaking across tests
    (and into the rest of the suite — the cache is module-global in app.auth)."""
    monkeypatch.delenv("JAKS_SESSION_SECRET", raising=False)
    auth.reset_secret_cache()
    yield
    auth.reset_secret_cache()


def _is_login_redirect(r) -> bool:
    return r.status_code in (302, 303) and "/login" in r.headers.get("location", "")


# ── 1. JAKS_SKIP_AUTH gate ────────────────────────────────────────────────────

def test_skip_auth_honored_on_in_memory_engine(monkeypatch):
    """Control: with JAKS_SKIP_AUTH=1 AND an in-memory engine (the normal suite
    environment) a protected route is NOT auth-redirected — the bypass works."""
    monkeypatch.setenv("JAKS_SKIP_AUTH", "1")
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        r = c.post("/invoices/1/payment", data={})
        assert not _is_login_redirect(r), (
            f"in-memory bypass broken: {r.status_code} -> "
            f"{r.headers.get('location')!r} (the whole suite depends on this)")


def test_skip_auth_refused_when_not_test_env(monkeypatch):
    """JAKS_SKIP_AUTH=1 in a NON-test context (deps._is_test_env → False, the
    documented monkeypatch seam) must NOT bypass auth — the middleware still
    303s unauthenticated requests to /login."""
    monkeypatch.setenv("JAKS_SKIP_AUTH", "1")
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        monkeypatch.setattr(deps, "_is_test_env", lambda: False)
        r = c.post("/invoices/1/payment", data={})
        assert _is_login_redirect(r), (
            f"JAKS_SKIP_AUTH bypassed auth outside a test env: {r.status_code} "
            f"-> {r.headers.get('location')!r}")


def test_skip_auth_refused_on_file_engine_url(monkeypatch):
    """End-to-end version of the gate: with the active engine pointing at a FILE
    URL (production shape), JAKS_SKIP_AUTH=1 must not disable enforcement. The
    middleware redirects before any handler runs, so a stub engine is enough."""
    monkeypatch.setenv("JAKS_SKIP_AUTH", "1")
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        # Swap in a stub whose URL looks like the production file DB. Only
        # _is_test_env reads it (the request never reaches a DB-using handler).
        monkeypatch.setattr(
            _appdb, "engine", types.SimpleNamespace(url="sqlite:///data/jaks.db")
        )
        r = c.post("/invoices/1/payment", data={})
        assert _is_login_redirect(r), (
            f"JAKS_SKIP_AUTH bypassed auth on a file-DB engine: {r.status_code} "
            f"-> {r.headers.get('location')!r}")


# ── 2. Session-secret resolution order ───────────────────────────────────────

def test_env_secret_takes_precedence_over_db(monkeypatch):
    SessionLocal = activate(_ENGINE)
    db = SessionLocal()
    try:
        set_setting_value_db(db, "session_secret_key", "db-secret-aaa")
        db.commit()
    finally:
        db.close()

    monkeypatch.setenv("JAKS_SESSION_SECRET", "env-secret-bbb")
    auth.reset_secret_cache()
    assert auth._get_secret() == "env-secret-bbb"

    # Round trip: tokens minted under the env secret verify under it.
    token = auth.make_session_token(42)
    assert auth.read_session_token(token) == 42


def test_db_secret_fallback_when_env_unset(monkeypatch):
    SessionLocal = activate(_ENGINE)
    db = SessionLocal()
    try:
        set_setting_value_db(db, "session_secret_key", "db-secret-ccc")
        db.commit()
    finally:
        db.close()

    monkeypatch.delenv("JAKS_SESSION_SECRET", raising=False)
    auth.reset_secret_cache()
    assert auth._get_secret() == "db-secret-ccc", (
        "env var unset must fall back to the DB-stored secret "
        "(existing sessions keep working)")


def test_env_secret_invalidates_db_signed_token_and_back(monkeypatch):
    """Precedence is live, not just at read time: a token signed under the DB
    secret stops validating while the env secret is set, and validates again
    once it's unset (DB fallback restored)."""
    SessionLocal = activate(_ENGINE)
    db = SessionLocal()
    try:
        set_setting_value_db(db, "session_secret_key", "db-secret-ddd")
        db.commit()
    finally:
        db.close()

    auth.reset_secret_cache()
    db_token = auth.make_session_token(7)
    assert auth.read_session_token(db_token) == 7

    monkeypatch.setenv("JAKS_SESSION_SECRET", "env-secret-eee")
    assert auth.read_session_token(db_token) is None  # signed under the other key

    monkeypatch.delenv("JAKS_SESSION_SECRET", raising=False)
    assert auth.read_session_token(db_token) == 7


def test_insecure_fallback_when_no_env_and_no_db_row(monkeypatch):
    """Empty settings table + no env var → the (never-cached) module fallback."""
    activate(fresh_engine())  # fresh tables, never started → no seeded secret
    monkeypatch.delenv("JAKS_SESSION_SECRET", raising=False)
    auth.reset_secret_cache()
    assert auth._get_secret() == auth._FALLBACK_SECRET


# ── 3. Startup overdue-core scan (R1-9) ──────────────────────────────────────

def test_startup_calls_mark_overdue_cores(monkeypatch):
    activate(_ENGINE)
    calls: list[int] = []

    def _fake(self):
        calls.append(1)
        return {"overdue": 0, "approaching": 0, "scanned": 0}

    monkeypatch.setattr(CoreService, "mark_overdue_cores", _fake)
    with TestClient(app):  # entering the context runs the startup event
        pass
    assert calls, "on_startup did not invoke CoreService.mark_overdue_cores"


def test_startup_survives_scan_failure(monkeypatch):
    """The scan is fail-soft: an exception inside mark_overdue_cores must not
    crash startup, and the app still serves requests afterwards."""
    activate(_ENGINE)

    def _boom(self):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(CoreService, "mark_overdue_cores", _boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/login")  # auth-exempt route — proves the app is up
        assert r.status_code == 200
