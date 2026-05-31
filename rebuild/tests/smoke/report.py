"""
tests/smoke/report.py
======================
The results contract shared between the runner (writer) and the
``/admin/smoke-tests`` dashboard (reader), plus the canonical on-disk paths.

Every step records the fields the spec requires on failure:
    page URL · failed action · expected result · actual result ·
    screenshot path · trace path · video path

Results are written atomically to ``RESULTS_PATH`` (latest run) and a timestamped
copy under ``RUNS_DIR`` for history. The dashboard reads ``RESULTS_PATH``; a
sibling ``RUNNING_LOCK`` marks an in-flight run so the page can show "running…".
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import tempfile
from dataclasses import dataclass, field, asdict

# ── Canonical paths ───────────────────────────────────────────────────────────
# Kept under tests/smoke so artifacts live with the suite and are easy to gitignore.
SMOKE_DIR = pathlib.Path(__file__).resolve().parent
ARTIFACTS_DIR = SMOKE_DIR / "artifacts"
RUNS_DIR = ARTIFACTS_DIR / "runs"
RESULTS_PATH = ARTIFACTS_DIR / "latest.json"
RUNNING_LOCK = ARTIFACTS_DIR / "running.lock"

SCHEMA_VERSION = 1

# Status constants
PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
ERROR = "error"      # suite-level: never completed (e.g. server/browser failed to start)
RUNNING = "running"


@dataclass
class StepResult:
    """One workflow step's outcome. ``actual`` and the artifact paths are filled in
    as the step runs / fails."""

    key: str
    index: int
    title: str
    action: str = ""            # what the step tried to do
    expected: str = ""          # what success looks like
    status: str = SKIPPED
    actual: str = ""            # what actually happened (esp. on failure)
    page_url: str = ""          # URL at the moment of the action / failure
    error: str = ""             # exception type + message, if any
    duration_s: float = 0.0
    screenshot: str | None = None
    trace: str | None = None
    video: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SuiteResult:
    status: str = RUNNING
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    base_url: str = ""
    schema_version: int = SCHEMA_VERSION
    artifacts_dir: str = ""
    trace: str | None = None     # context-level trace zip (covers the whole run)
    video_dir: str | None = None
    environment: dict = field(default_factory=dict)
    error: str = ""              # suite-level fatal error (server/browser bootstrap)
    steps: list[StepResult] = field(default_factory=list)

    # ── derived ──
    @property
    def totals(self) -> dict:
        t = {"total": len(self.steps), PASSED: 0, FAILED: 0, SKIPPED: 0}
        for s in self.steps:
            t[s.status] = t.get(s.status, 0) + 1
        return t

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 2),
            "base_url": self.base_url,
            "artifacts_dir": self.artifacts_dir,
            "trace": self.trace,
            "video_dir": self.video_dir,
            "environment": self.environment,
            "error": self.error,
            "totals": self.totals,
            "steps": [s.to_dict() for s in self.steps],
        }
        return d


def write_results(result: SuiteResult, path: pathlib.Path = RESULTS_PATH) -> None:
    """Atomically write the results JSON (temp file + os.replace) so the dashboard
    never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_dict(), indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_results(path: pathlib.Path = RESULTS_PATH) -> dict | None:
    """Read the latest results JSON, or None if no run has ever completed."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# A run lock older than this is treated as stale (the runner crashed before
# clearing it) so the dashboard never gets stuck showing "running" forever.
STALE_LOCK_SECONDS = 600


def is_running(lock: pathlib.Path = RUNNING_LOCK) -> bool:
    if not lock.exists():
        return False
    try:
        import time
        age = time.time() - lock.stat().st_mtime
        return age < STALE_LOCK_SECONDS
    except OSError:
        return False


def set_running(value: bool, lock: pathlib.Path = RUNNING_LOCK) -> None:
    if value:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("running", encoding="utf-8")
    elif lock.exists():
        try:
            lock.unlink()
        except OSError:
            pass
