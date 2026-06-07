"""
tests/test_auth_enforcement.py
==============================
NO-SKIP-AUTH perimeter guard for the money / bulk-data routes.

The whole suite runs with JAKS_SKIP_AUTH=1 (conftest sets it at import), which
makes the enforce_login middleware (app/main.py:64-74) a no-op — so the suite
otherwise gives the HTTP auth layer ZERO coverage. This module DELETES that env
var so the real middleware runs, then proves an UNAUTHENTICATED caller (no
jaks_session cookie) cannot reach finalize / payment / credit-memo / customer-
import / product-import: the middleware short-circuits to a 303 redirect to
/login before the handler ever runs.

(The app blocks via redirect-to-/login, not a 401/403 status — a deliberate UX
choice for a browser app; that redirect IS the "blocked" signal we assert.)

Two extra halves keep it honest:
  • test_protected_money_paths_are_real_routes — the paths we probe are genuine
    registered routes, so a /login redirect means "blocked", NOT "no such route"
    (the middleware would 303 a typo'd path too).
  • test_enforcement_is_auth_specific_control — with enforcement OFF the SAME
    path is NOT redirected to /login, proving the block is the auth layer.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_auth_enforcement.py -v
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()

# (method, route-template, concrete-path-to-hit). Every one mutates money or bulk
# data and MUST sit behind auth. Templates are verified against app.routes so a
# rename/move trips test_protected_money_paths_are_real_routes, not a silent pass.
_PROTECTED = [
    ("POST", "/invoices/{invoice_id}/finalise",          "/invoices/1/finalise"),
    ("POST", "/invoices/{invoice_id}/payment",           "/invoices/1/payment"),
    ("POST", "/invoices/{invoice_id}/issue-credit-memo", "/invoices/1/issue-credit-memo"),
    ("POST", "/customers/import/confirm",                "/customers/import/confirm"),
    ("POST", "/products/import-run",                     "/products/import-run"),
    ("POST", "/payments/{payment_id}/reverse",           "/payments/1/reverse"),
]


def _registered_routes() -> set[tuple[str, str]]:
    reg: set[tuple[str, str]] = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        for m in (getattr(r, "methods", None) or set()):
            reg.add((m, path))
    return reg


def test_protected_money_paths_are_real_routes():
    """Sanity: every path probed below is a genuine registered route, so the
    /login redirect in the enforcement test means BLOCKED, not 'route not found'
    (the blanket middleware would 303 a non-existent path just the same)."""
    reg = _registered_routes()
    missing = [(m, t) for m, t, _ in _PROTECTED if (m, t) not in reg]
    assert missing == [], f"money routes not registered (moved/renamed?): {missing}"


def test_money_routes_block_unauthenticated_when_skip_auth_unset(monkeypatch):
    """With JAKS_SKIP_AUTH unset and no session cookie, every money route is
    intercepted by enforce_login and 303-redirected to /login — the handler is
    never reached."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)   # real enforcement ON
    activate(_ENGINE)
    # raise_server_exceptions=False: a handler error can't be mistaken for a block.
    # follow_redirects=False: we observe the 303 itself, not the login page.
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        for method, _tmpl, path in _PROTECTED:
            r = c.request(method, path, data={})
            assert r.status_code in (302, 303), (
                f"{method} {path}: expected an auth redirect, got {r.status_code} "
                f"(route executed unauthenticated?)")
            assert "/login" in r.headers.get("location", ""), (
                f"{method} {path}: blocked but not to /login "
                f"({r.headers.get('location')!r})")


def test_enforcement_is_auth_specific_control(monkeypatch):
    """Control: with JAKS_SKIP_AUTH=1 (the normal suite env) the SAME money route
    is NOT redirected to /login — the handler runs (and may 3xx/4xx/5xx for
    unrelated reasons). Proves the block above is the AUTH layer, not some other
    middleware blanket-redirecting everything."""
    monkeypatch.setenv("JAKS_SKIP_AUTH", "1")
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        r = c.post("/invoices/1/payment", data={})
        is_login_redirect = (
            r.status_code in (302, 303)
            and "/login" in r.headers.get("location", "")
        )
        assert not is_login_redirect, (
            f"with JAKS_SKIP_AUTH=1 the route still auth-redirected: "
            f"{r.status_code} -> {r.headers.get('location')!r}")
