"""
tests/test_no_scheduler_threads_in_tests.py
============================================
Regression pin for the cross-test flakiness root cause (2026-07-05).

The bug
-------
app.main's on_startup starts six background DAEMON schedulers (shopify sync /
order-poll / weekly-audit, overdue-core scan, qbo-retry, inventory-resync). Each
self-skips an in-memory engine via a ``":memory:" in engine.url`` guard. But a
FILE-DB test (test_backup_acceptance / test_backup_restore / test_alembic_adoption)
legitimately points ``app.database.engine`` at a temp FILE engine before booting a
TestClient — so that guard does NOT fire and all six daemon threads spawn. Being
daemons they persist for the WHOLE session and keep ticking (shopify-order-poll
every 60s) on the *shared global* ``app.database.SessionLocal`` — which by then
points at whatever in-memory engine a LATER test activated. Each tick opens a
session on that later test's single StaticPool connection concurrently with the
running test, corrupting its in-flight transaction. The visible symptom was a
DIFFERENT unrelated test failing per run (``Could not refresh instance``, wrong
totals, …), which looked like PYTHONHASHSEED hash-order flakiness but was really
daemon-thread timing.

The fix
-------
conftest sets ``JAKS_DISABLE_SCHEDULERS=1`` and on_startup honours it, so NO test —
file-DB ones included — ever spawns the schedulers. This test reproduces the exact
polluter condition (file engine + TestClient startup) and fails loudly if a future
change lets the daemon threads back in.
"""
from __future__ import annotations

import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as _appdb
from app.database import Base
from app.deps import get_db
from app.main import app
from app.models import __all_models__  # noqa: F401 — register every model on Base

# The daemon threads on_startup would spawn (names set in app/main.py).
_SCHEDULER_THREAD_NAMES = {
    "shopify-nightly-sync",
    "shopify-order-poll",
    "shopify-weekly-audit",
    "overdue-core-daily-scan",
    "qbo-retry-worker",
    "inventory-resync-daily",
}


def _live_scheduler_threads() -> set[str]:
    return {t.name for t in threading.enumerate()} & _SCHEDULER_THREAD_NAMES


def test_file_engine_startup_spawns_no_scheduler_threads(tmp_path, monkeypatch):
    """Booting a TestClient against a FILE engine (the backup-test condition) must
    NOT start the background daemon schedulers under the suite."""
    # No scheduler thread should already be alive — if one is, a prior test leaked it.
    assert not _live_scheduler_threads(), (
        "a background scheduler daemon leaked from an earlier test: "
        f"{_live_scheduler_threads()}"
    )

    db_file = tmp_path / "data" / "jaks.db"
    db_file.parent.mkdir(parents=True)
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Mirror test_backup_acceptance: repoint the app globals at the temp FILE engine
    # so the schedulers' ":memory:" self-skip does NOT fire. Only JAKS_DISABLE_SCHEDULERS
    # (set by conftest) can keep them from spawning.
    monkeypatch.setattr(_appdb, "engine", engine)
    monkeypatch.setattr(_appdb, "SessionLocal", SessionLocal)

    # Quiet the startup backup (its default source path doesn't exist here) so the
    # test asserts on threads, not on incidental backup noise. Mirrors test_backup_acceptance.
    from app.settings_utils import set_setting_value_db
    _s = SessionLocal()
    try:
        set_setting_value_db(_s, "backup_on_startup", "false")
        _s.commit()
    finally:
        _s.close()

    def _override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        assert ":memory:" not in str(_appdb.engine.url), "precondition: file engine"
        with TestClient(app):  # triggers on_startup (init_db + seeds + scheduler block)
            pass
        leaked = _live_scheduler_threads()
        assert not leaked, (
            f"on_startup spawned background scheduler daemon thread(s) {leaked} under "
            "the test suite. These tick on the shared global SessionLocal and corrupt "
            "other tests' transactions (cross-test flakiness). Gate the scheduler "
            "startup on JAKS_DISABLE_SCHEDULERS (see app/main.py on_startup)."
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
