"""
app/routers/admin.py
====================
Admin utilities. Currently hosts the smoke-test dashboard at /admin/smoke-tests,
which renders the latest results written by ``tests/smoke/runner.py`` and lets the
operator kick off a fresh run.

The actual Playwright run is heavy (it spins up its own isolated server + a real
browser), so it is NEVER run inside this request. The "Run" button launches the
runner as a DETACHED subprocess; the runner writes results JSON which this page
reads. While a run is in flight the page auto-refreshes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# tests/ is importable from the repo root (the dev server runs from there).
from tests.smoke import report as smoke_report

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@router.get("/smoke-tests", response_class=HTMLResponse)
def smoke_tests_dashboard(request: Request):
    """Render the smoke-test results dashboard."""
    results = smoke_report.read_results()
    running = smoke_report.is_running()
    return templates.TemplateResponse(
        request,
        "admin/smoke_tests.html",
        {
            "results": results,
            "running": running,
        },
    )


@router.post("/smoke-tests/run", response_class=RedirectResponse)
def smoke_tests_run():
    """Launch the smoke runner as a detached background process, then redirect
    back to the dashboard (which will auto-refresh while it runs)."""
    if smoke_report.is_running():
        return RedirectResponse("/admin/smoke-tests?msg=already-running", status_code=303)

    smoke_report.set_running(True)

    log_path = smoke_report.ARTIFACTS_DIR / "last_run.log"
    smoke_report.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "w", encoding="utf-8")

    creationflags = 0
    if sys.platform == "win32":
        # Detach so the run outlives this request and doesn't die with the worker.
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    try:
        subprocess.Popen(
            [sys.executable, "-m", "tests.smoke.runner"],
            cwd=str(_REPO_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception:
        # If we couldn't even launch, clear the lock so the page isn't stuck.
        smoke_report.set_running(False)
        log_fh.close()
        return RedirectResponse("/admin/smoke-tests?msg=launch-failed", status_code=303)

    return RedirectResponse("/admin/smoke-tests?msg=started", status_code=303)


@router.get("/smoke-tests/artifact/{relpath:path}")
def smoke_tests_artifact(relpath: str):
    """Serve a run artifact (screenshot / trace.zip / video) from the smoke
    artifacts dir. Path-traversal guarded — only files inside ARTIFACTS_DIR."""
    base = smoke_report.ARTIFACTS_DIR.resolve()
    target = (base / relpath).resolve()
    if base not in target.parents or not target.is_file():
        return HTMLResponse("Artifact not found.", status_code=404)
    return FileResponse(str(target))
