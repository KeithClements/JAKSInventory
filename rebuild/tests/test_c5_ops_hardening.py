"""
tests/test_c5_ops_hardening.py
==============================
C5 (FULL_ERP_REVIEW_2026-07-04) — operability: a monitor can see the app is up,
logs persist, the app auto-restarts, and the landmine launchers are neutralized.

Covers:
  * GET /health returns JSON with a DB check, and is reachable WITHOUT a session
    (so an external watchdog can poll it) — verified with the real auth layer on.
  * _configure_file_logging is safe/no-op on the in-memory test engine.
  * Launcher hardening (content assertions, mirroring test_s22_password_rotation):
      - START JAKS.bat no longer runs run.py; it redirects to the real launcher.
      - scripts/axle_service_run.bat is a supervised production runner (loop, no pause).
      - service_install.py uses Task Scheduler, not the broken pywin32 path.
      - run.py no longer force-enables --reload (the Windows stall).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_c5_ops_hardening.py -q
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import logging

from fastapi.testclient import TestClient

import app.main as main
from app.main import app, _configure_file_logging
from tests.conftest import activate, fresh_engine

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── /health ────────────────────────────────────────────────────────────────────

def test_health_ok_with_db(monkeypatch):
    engine = fresh_engine()
    activate(engine)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert "time" in body


def test_health_reachable_without_session(monkeypatch):
    """A watchdog has no login — /health must NOT redirect to /login even with the
    real auth layer on."""
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)
    engine = fresh_engine()
    activate(engine)
    c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    r = c.get("/health")   # no session cookie at all
    assert r.status_code == 200, (
        f"/health must be auth-exempt for monitors, got {r.status_code} "
        f"→ {r.headers.get('location')!r}")


def test_configure_file_logging_noop_on_memory_engine():
    """On the in-memory test engine, file logging must add no FileHandler (the
    suite never writes log files) and must not raise."""
    before = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
    _configure_file_logging()  # engine is in-memory during tests
    after = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
    assert after == before, "file logging should be skipped on the in-memory engine"


# ── launcher / service hardening ────────────────────────────────────────────────

def test_start_jaks_bat_is_a_safe_redirect():
    text = (_REPO_ROOT / "START JAKS.bat").read_text(encoding="utf-8", errors="ignore")
    low = text.lower()
    assert "run.py" not in low, "START JAKS.bat must no longer launch the reload-mode run.py"
    assert "start jaks erp.bat" in low, "START JAKS.bat should redirect to the real launcher"


def test_axle_service_runner_is_supervised_production():
    text = (_REPO_ROOT / "scripts" / "axle_service_run.bat").read_text(encoding="utf-8", errors="ignore")
    low = text.lower()
    assert "jaks_env=production" in low, "the service runner must set production posture"
    assert "goto loop" in low, "the service runner must auto-restart uvicorn (supervised loop)"
    assert "uvicorn app.main:app" in low
    assert "pause" not in low, "a headless/service runner must never pause"


def test_service_install_uses_task_scheduler_not_broken_pywin32():
    text = (_REPO_ROOT / "service_install.py").read_text(encoding="utf-8", errors="ignore")
    assert "schtasks" in text, "service_install.py should register a Scheduled Task"
    assert "_svc_reg_class_" not in text, "the broken pywin32 attribute must be gone"
    assert "win32serviceutil" not in text, "the non-working pywin32 service path must be gone"


def test_run_py_does_not_force_reload():
    text = (_REPO_ROOT / "run.py").read_text(encoding="utf-8", errors="ignore")
    assert "reload=True" not in text, (
        "run.py must not hard-enable --reload (documented Windows stall)")
    assert "JAKS_RELOAD" in text, "reload should be opt-in via env"
