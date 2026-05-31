"""
tests/test_smoke_workflow.py
============================
Pytest entry point for the core-workflow Playwright smoke suite.

This is HEAVY — it boots an isolated app instance and a real headless browser and
drives the 12 business-critical workflow steps end to end. It is therefore opt-in:
it only runs when ``JAKS_RUN_SMOKE=1`` is set, so the normal unit/route suite stays
fast.

Run it explicitly:
    # PowerShell
    $env:JAKS_RUN_SMOKE = "1"; pytest tests/test_smoke_workflow.py -m smoke -v

    # or the standalone runner (writes tests/smoke/artifacts/latest.json)
    python -m tests.smoke.runner

The test fails if any workflow step FAILED, printing each failure's expected vs.
actual so the breakage is readable in CI output. A bootstrap error (server/browser
could not start) is reported separately.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.smoke


@pytest.mark.skipif(
    not os.environ.get("JAKS_RUN_SMOKE"),
    reason="heavy browser suite — set JAKS_RUN_SMOKE=1 to run (or use: python -m tests.smoke.runner)",
)
def test_core_workflow_smoke():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed — pip install playwright && playwright install chromium")

    from tests.smoke.runner import run_suite

    suite = run_suite()

    if suite.status == "error":
        pytest.fail(f"Smoke suite bootstrap error (server/browser failed to start):\n{suite.error}")

    failed = [s for s in suite.steps if s.status == "failed"]
    if failed:
        lines = [
            f"  {s.index}. {s.title}\n     expected: {s.expected}\n     actual:   {s.actual}"
            for s in failed
        ]
        pytest.fail(
            f"{len(failed)} workflow step(s) failed (results: tests/smoke/artifacts/latest.json):\n"
            + "\n".join(lines)
        )
