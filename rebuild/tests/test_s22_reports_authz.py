"""
tests/test_s22_reports_authz.py
===============================
Phase 0 / Risk #1 (HIGH) — Reports + Dashboard role gate.

Every report (and its CSV export) and the dashboard expose sensitive financials:
cost basis, margins, AR/AP aging, sales tax collected, top customers, and the
captured competitor pricing in the lost-sales log. A counter clerk (SALES) or a
READ_ONLY user must NOT be able to read or export any of it.

`app/deps.require_reports_access` gates the whole `/reports` router and the
`/dashboard` route to ADMIN or BOOKKEEPING. This test proves real role
enforcement, so — like tests/test_backup_acceptance.py — it runs against an
ISOLATED, FILE-BASED app DB. That matters because `deps._is_test_env()` is True
only for an in-memory engine; with a real file DB the in-memory auth/RBAC bypass
is OFF, so the gate actually evaluates the signed-in user's role.

Roles exercised (real seeded/created users, authenticated via /login):
  • admin / admin          → ADMIN        → 200 on every gated route
  • bookkeeper / bookkeeper → BOOKKEEPING  → 200 on every gated route
  • clerk / clerk           → SALES        → 403 on every gated route
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.database import Base
from app.deps import get_db
from app.models import __all_models__  # noqa: F401 — registers every model on Base
from app.main import app
from app.settings_utils import set_setting_value_db

# Non-default passwords for the seeded admin/bookkeeper accounts. The default
# passwords ("admin"/"bookkeeper") would trip the enforce_password_rotation gate
# (app/main.py) which 303-redirects to /account before any route runs — that
# would mask the role gate under test. Seeding non-default passwords via the
# JAKS_*_PASSWORD env vars lets the role gate be exercised in isolation.
_ADMIN_PW = "admin-pw-not-default"
_BOOKKEEPER_PW = "bk-pw-not-default"
_CLERK_PW = "clerk-pw-not-default"

# The gated routes a SALES user must be 403'd from and ADMIN/BOOKKEEPING 200'd on.
# Covers an HTML report, an inventory report, the competitor-data report, a CSV
# export, and the dashboard — the acceptance set in HANDOFF §Lane A. The dashboard
# route is registered at "/" (dashboard.py:27), so that is the path we probe.
_GATED_ROUTES = [
    "/reports/ar-aging",
    "/reports/inventory-valuation",
    "/reports/lost-sales",
    "/reports/ar-aging/export.csv",   # a CSV export route
    "/",                              # the dashboard
]


def _seed_sales_user(SessionLocal) -> None:
    """Create a SALES (counter-clerk) user that is neither ADMIN nor BOOKKEEPING."""
    from app.auth import hash_password
    from app.constants import UserRole
    from app.models.user import User

    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == "clerk").first() is None:
            db.add(User(
                name="Counter Clerk",
                username="clerk",
                password_hash=hash_password(_CLERK_PW),
                role=UserRole.SALES,
            ))
            db.commit()
    finally:
        db.close()


@pytest.fixture()
def isolated_app(tmp_path, monkeypatch):
    """Point the app at an isolated FILE-based SQLite DB so real auth/RBAC runs.

    A file DB (not the in-memory conftest engine) is required because the auth +
    CSRF bypass and `require_reports_access`'s test fallback all key off
    `deps._is_test_env()` == in-memory engine. With a real file the gate evaluates
    the actual signed-in user's role.

    This fixture explicitly SAVES and RESTORES the app.database globals
    (engine / SessionLocal) and app.dependency_overrides around its file-DB use —
    mirroring tests/test_alembic_adoption.py / test_backup_acceptance.py — so it
    does NOT leave the live/in-memory engine swapped out for a later test module
    (which previously caused an order-dependent flake in
    test_dryrun_contract_metrics).
    """
    # Seed admin/bookkeeper with NON-default passwords so the password-rotation
    # gate (app/main.py) doesn't redirect them to /account, which would mask the
    # role gate under test. Set before the TestClient triggers startup seeding.
    monkeypatch.setenv("JAKS_ADMIN_PASSWORD", _ADMIN_PW)
    monkeypatch.setenv("JAKS_BOOKKEEPER_PASSWORD", _BOOKKEEPER_PW)
    # Real auth/RBAC must run against the file DB — make sure no skip-auth leaks in.
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)

    db_file = tmp_path / "data" / "jaks.db"
    db_file.parent.mkdir(parents=True)

    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Snapshot the globals + the whole override map so we can restore them exactly,
    # regardless of what the conftest autouse harness left in place. monkeypatch
    # would restore _appdb attrs too, but the override map needs explicit handling
    # (we must put back the PREVIOUS get_db override, not just delete ours).
    saved_engine, saved_session = _appdb.engine, _appdb.SessionLocal
    saved_overrides = dict(app.dependency_overrides)

    try:
        _appdb.engine = engine
        _appdb.SessionLocal = SessionLocal

        def _override_get_db():
            s = SessionLocal()
            try:
                yield s
            finally:
                s.close()

        app.dependency_overrides[get_db] = _override_get_db

        # No startup-backup noise.
        db = SessionLocal()
        try:
            set_setting_value_db(db, "backup_on_startup", "false")
            db.commit()
        finally:
            db.close()

        with TestClient(app) as client:
            # Startup (via TestClient context) seeds admin (ADMIN) + bookkeeper
            # (BOOKKEEPING). Add the SALES clerk ourselves.
            _seed_sales_user(SessionLocal)
            import app.auth as auth
            auth.reset_secret_cache()  # secret now lives in THIS file DB
            yield client
    finally:
        # Restore app.database globals + the dependency_overrides map exactly as
        # they were, so the next test module's engine/override isn't clobbered.
        _appdb.engine = saved_engine
        _appdb.SessionLocal = saved_session
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)
        import app.auth as auth
        auth.reset_secret_cache()  # drop the file-DB secret cache for later modules


def _login(client, username: str, password: str) -> None:
    """Authenticate `client` as the given user (replaces any prior session)."""
    r = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"login as {username} failed: {r.status_code} {r.text[:200]}"


# ── SALES → 403 on every gated route ──────────────────────────────────────────

@pytest.mark.parametrize("route", _GATED_ROUTES)
def test_sales_user_forbidden(isolated_app, route):
    """A SALES counter clerk is rejected with HTTP 403 from every report, CSV
    export, and the dashboard (cost basis / AR / competitor data must not leak)."""
    client = isolated_app
    _login(client, "clerk", _CLERK_PW)
    r = client.get(route, follow_redirects=False)
    assert r.status_code == 403, (
        f"SALES user reached {route} (got {r.status_code}, expected 403) — "
        f"the reports/dashboard role gate is not enforced"
    )


# ── ADMIN → 200 on every gated route ──────────────────────────────────────────

@pytest.mark.parametrize("route", _GATED_ROUTES)
def test_admin_user_allowed(isolated_app, route):
    """The ADMIN owner can reach every gated route."""
    client = isolated_app
    _login(client, "admin", _ADMIN_PW)
    r = client.get(route, follow_redirects=False)
    assert r.status_code == 200, (
        f"ADMIN blocked from {route} (got {r.status_code}, expected 200): {r.text[:200]}"
    )


# ── BOOKKEEPING → 200 on every gated route ────────────────────────────────────

@pytest.mark.parametrize("route", _GATED_ROUTES)
def test_bookkeeping_user_allowed(isolated_app, route):
    """The BOOKKEEPING user (wife) can reach every gated route — these are the
    financials she needs; the gate must allow her role, not just ADMIN."""
    client = isolated_app
    _login(client, "bookkeeper", _BOOKKEEPER_PW)
    r = client.get(route, follow_redirects=False)
    assert r.status_code == 200, (
        f"BOOKKEEPING blocked from {route} (got {r.status_code}, expected 200): {r.text[:200]}"
    )
