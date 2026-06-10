"""
tests/conftest.py
=================
Shared test-isolation harness.

Why this exists
---------------
Every workflow test module used to create its own in-memory engine and patch
the *global* ``app.database.engine`` / ``SessionLocal`` (and the ``get_db``
dependency override) at **import time**. Because those globals are shared and
all test modules are imported during collection, the *last* module imported
won — so when the full suite ran, earlier modules' route handlers and session
fixtures resolved to a different module's engine. Run alone each file passed;
run together the suite cascaded into failures/errors. ``test_smoke`` and
``test_quote_to_invoice_route`` additionally relied on a ``DATABASE_URL`` env
var that ``app.database`` never read, so they silently hit the live DB.

The fix
-------
Each module still owns its own isolated in-memory engine (so there is no
cross-module data sharing), but it re-points the globals + override at that
engine at **run time** via an autouse fixture that calls ``activate()`` below.
Because pytest runs one module's tests to completion before the next, the
active engine during any test is always that test's own module engine,
regardless of import order.
"""
from __future__ import annotations

import os
import pathlib
import sys

# Ensure the project root is importable as `app`.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Disable auth enforcement for the test suite — tests verify business logic,
# not the auth middleware (which has its own test when needed).
os.environ.setdefault("JAKS_SKIP_AUTH", "1")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as _appdb
from app.database import Base
from app.deps import get_db
from app.models import __all_models__  # noqa: F401 — registers every model on Base
from app.main import app as fastapi_app


def fresh_engine():
    """Create a brand-new, isolated in-memory SQLite engine with all tables.

    StaticPool keeps a single underlying connection so create_all() and every
    session/route see the same in-memory database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def bare_engine():
    """Create a raw in-memory engine with NO tables.

    For tests that control their own schema (e.g. schema-drift tests that
    need a minimal pre-migration database). Does NOT call create_all().
    """
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def activate(engine):
    """Point the app's DB globals + the ``get_db`` override at ``engine``.

    Call this at run time (from a module-scoped autouse fixture) so the active
    engine is correct for the currently-running module, immune to import order.
    Returns the module's ``SessionLocal`` so callers can seed data.
    """
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _appdb.engine = engine
    _appdb.SessionLocal = SessionLocal

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    return SessionLocal
