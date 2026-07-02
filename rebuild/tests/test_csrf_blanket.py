"""
tests/test_csrf_blanket.py
==========================
Blanket CSRF regression net over EVERY state-changing route.

The double-submit CSRF guard (app/security.py CSRFMiddleware) must 403 any
POST/PUT/PATCH/DELETE from an authenticated session that carries no token.
That bug class — a state-changing call shipped without the token wiring and
silently 403ing — has now been found twice in the field, so this module pins
the property mechanically instead of per-route:

1. Enumerate ``app.routes`` at TEST time (not a hand-kept list), fill dummy
   path params, and fire each unsafe method with a VALID signed session
   cookie but NO CSRF cookie/header/field → every one must be exactly 403
   with the middleware's denial message. Any route added in the future is
   covered the day it is registered.
2. The middleware runs BEFORE routing, so even a nonsense path must 403 —
   asserted directly, which is what guarantees (1) generalizes to routes
   that do not exist yet.
3. The exempt surface must stay tight: the exact/prefix exempt lists are
   pinned, the concrete unsafe routes living under them are pinned, and
   POST /login is behaviorally verified to NOT 403 (login must stay
   reachable without a token or nobody could ever sign in).

The suite-wide CSRF bypass (JAKS_SKIP_AUTH + in-memory engine, see
security._test_bypass) is defeated per-test by deleting the env var — the
session cookie is a real signed token for a REAL seeded ADMIN row (pattern:
tests/test_money_route_rbac.py), so the auth/pw-rotation layers behind the
guard stay green and the only thing under test is the CSRF gate itself.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_csrf_blanket.py -q
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE, hash_password, make_session_token
from app.constants import UserRole
from app.main import app
from app.models.user import User
from app.security import CSRF_COOKIE, CSRF_HEADER
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Skipped in the blanket sweep: the middleware's own exempts (pinned separately
# below) plus /p/ public signed-document links (token-in-URL is the credential;
# they carry no session for CSRF to protect).
_SWEEP_SKIP_EXACT = frozenset({"/login", "/logout"})
_SWEEP_SKIP_PREFIXES = ("/static/", "/api/leadfinder", "/p/")
# The middleware's denial text — asserted alongside the status so a 403 raised
# by some OTHER layer (RBAC, require_admin) can never satisfy this test.
_CSRF_DENIAL = "CSRF token missing or invalid"
# Enumeration sanity floor: 236 unsafe route/method pairs at time of writing.
# Well below that means the sweep itself broke (and would false-pass green).
_MIN_EXPECTED_TARGETS = 150

_PARAM_RE = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _fill_path(route) -> str:
    """Concrete URL for a route template — 1 for int params, x otherwise.

    Values are deliberately nonsense: the middleware must 403 BEFORE routing,
    so no real row may be required (property pinned by the nonsense-path test)."""
    convertors = getattr(route, "param_convertors", None) or {}

    def dummy(match: re.Match) -> str:
        conv = type(convertors.get(match.group(1), None)).__name__
        return "1" if ("Integer" in conv or "Float" in conv) else "x"

    return _PARAM_RE.sub(dummy, route.path)


def _sweep_targets() -> list[tuple[str, str]]:
    """(method, concrete_path) for every registered non-exempt unsafe route."""
    targets = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = (getattr(route, "methods", None) or set()) & _UNSAFE_METHODS
        if not path or not methods:
            continue
        if path in _SWEEP_SKIP_EXACT or path.startswith(_SWEEP_SKIP_PREFIXES):
            continue
        for method in methods:
            targets.add((method, _fill_path(route)))
    return sorted(targets)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ctx():
    """Module engine + one REAL ADMIN row (username is NOT a shipped default,
    so the password-rotation gate behind the CSRF layer never intercepts)."""
    SessionLocal = activate(_ENGINE)
    db = SessionLocal()
    try:
        admin = User(
            name="csrf-blanket-admin",
            username="csrf-blanket-admin",
            password_hash=hash_password("csrf-blanket-pw-not-default"),
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        return {"admin_id": admin.id}
    finally:
        db.close()


@pytest.fixture()
def client(ctx):
    # raise_server_exceptions=False: a 500 must surface as a wrong status, not a
    # traceback; follow_redirects=False: a redirect instead of 403 IS the bug.
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture()
def live_csrf(monkeypatch):
    """Defeat security._test_bypass so the middleware actually validates."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)


def _sign_in(client: TestClient, user_id: int) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, make_session_token(user_id))


# ── 1. The blanket: every state-changing route, no token → exactly 403 ────────

def test_every_state_changing_route_403s_without_csrf(ctx, client, live_csrf):
    targets = _sweep_targets()
    assert len(targets) >= _MIN_EXPECTED_TARGETS, (
        f"route enumeration collapsed to {len(targets)} targets — the sweep "
        "itself is broken and the blanket would false-pass")

    failures = []
    for method, path in targets:
        # Fresh jar per request: the middleware ISSUES a jaks_csrf cookie on
        # every response, and letting the client echo it back would drift the
        # probe away from "no token anywhere".
        _sign_in(client, ctx["admin_id"])
        r = client.request(method, path)
        if r.status_code != 403:
            failures.append(f"{method} {path} -> {r.status_code}")
        elif _CSRF_DENIAL not in r.text:
            failures.append(f"{method} {path} -> 403 but not the CSRF denial")

    assert failures == [], (
        f"{len(failures)}/{len(targets)} state-changing routes accepted (or "
        "mishandled) an authenticated request WITHOUT a CSRF token — the "
        "double-submit guard is not covering them:\n  " + "\n  ".join(failures))


# ── 2. Pre-routing property: nonsense paths 403 too (future routes covered) ───

def test_csrf_rejects_before_routing_even_on_nonsense_paths(ctx, client, live_csrf):
    _sign_in(client, ctx["admin_id"])
    r = client.post("/route-that-will-never-exist/999999/frobnicate")
    assert r.status_code == 403 and _CSRF_DENIAL in r.text, (
        f"expected the CSRF 403 BEFORE routing, got {r.status_code} — the "
        "middleware no longer guards unrouted paths, so newly added routes "
        "are NOT automatically covered")


# ── 3. Control: a matching token passes the CSRF layer (403s above are real) ──

def test_valid_double_submit_token_passes_csrf_layer(ctx, client, live_csrf):
    token = "csrf-blanket-control-token"
    _sign_in(client, ctx["admin_id"])
    client.cookies.set(CSRF_COOKIE, token)
    r = client.post(
        "/route-that-will-never-exist/999999/frobnicate",
        headers={CSRF_HEADER: token},
    )
    # Past CSRF + pw-gate + login enforcement, the router finally sees the
    # nonsense path: 404. A 403 here would mean the sweep's denials prove
    # nothing (the layer would be rejecting valid tokens across the board).
    assert r.status_code == 404, (
        f"cookie+header double-submit token was rejected ({r.status_code}) — "
        "the blanket 403s above are not evidence of token validation")


# ── 4. Exempt surface stays tight ─────────────────────────────────────────────

def test_exempt_lists_are_pinned():
    from app import security
    assert security._EXEMPT_EXACT == frozenset({"/login", "/logout"}), (
        f"CSRF exact-exempt list changed: {sorted(security._EXEMPT_EXACT)} — "
        "every addition is a route an attacker can call cross-site; review it")
    assert tuple(security._EXEMPT_PREFIXES) == ("/static/", "/api/leadfinder"), (
        f"CSRF prefix-exempt list changed: {security._EXEMPT_PREFIXES} — a new "
        "prefix silently un-protects everything registered under it; review it")


def test_no_new_unsafe_routes_hide_under_exempt_paths():
    """Concrete unsafe routes living inside the exempt surface, pinned by name.

    The prefix exemption is a standing hole: any FUTURE state-changing route
    registered under /api/leadfinder (or an exempt exact path) ships with no
    CSRF protection and the blanket sweep skips it. Force that to be a
    conscious, reviewed decision."""
    exempt_unsafe = sorted({
        (m, r.path)
        for r in app.routes
        for m in ((getattr(r, "methods", None) or set()) & _UNSAFE_METHODS)
        if getattr(r, "path", "") in _SWEEP_SKIP_EXACT
        or getattr(r, "path", "").startswith(("/static/", "/api/leadfinder"))
    })
    assert exempt_unsafe == [
        ("POST", "/api/leadfinder/convert"),
        ("POST", "/api/leadfinder/match"),
        ("POST", "/login"),
        ("POST", "/logout"),
    ], (f"the set of CSRF-EXEMPT state-changing routes changed: {exempt_unsafe} "
        "— new entries ship with NO cross-site protection; review, then re-pin")


def test_post_login_stays_reachable_without_csrf(client, live_csrf):
    # Behavioral half of the exemption pin: a browser with no cookies at all
    # must be able to POST the login form, or nobody can ever sign in.
    client.cookies.clear()
    r = client.post("/login", data={"username": "nobody", "password": "wrong"})
    assert r.status_code != 403 and _CSRF_DENIAL not in r.text, (
        f"POST /login was CSRF-blocked ({r.status_code}) — the exemption that "
        "keeps the sign-in form usable is gone")
    assert r.status_code < 500, f"POST /login 500'd: {r.status_code}"
