"""
tests/test_visual_regression.py
================================
Pixel-accurate visual regression against compiled-CSS baselines.

Requires:
    pip install playwright pillow
    playwright install chromium

Run with a live server:
    # Windows CMD:
    set JAKS_DEV_URL=http://localhost:8000
    pytest tests/test_visual_regression.py -m visual -v

    # PowerShell:
    $env:JAKS_DEV_URL = "http://localhost:8000"
    pytest tests/test_visual_regression.py -m visual -v

CI (GitHub Actions): see .github/workflows/visual-regression.yml

Behaviour:
  - No baseline PNG  -> SKIP (run capture_baselines.py first)
  - Pixel diff <= THRESHOLD -> PASS
  - Pixel diff >  THRESHOLD -> FAIL (saves .current.png + .diff.png in tests/visual/diffs/)

To update a baseline after an intentional change:
    python tests/visual/capture_baselines.py --force --only <label>

Environment variables:
    JAKS_DEV_URL            Server base URL  (default: http://localhost:8000)
    VISUAL_DIFF_THRESHOLD   Max changed-pixel fraction (default: 0.005 = 0.5%)
"""
from __future__ import annotations

import io
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Playwright availability guard
# ---------------------------------------------------------------------------

try:
    from playwright.sync_api import sync_playwright, Browser
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

pytestmark = pytest.mark.visual

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("JAKS_DEV_URL", "http://localhost:8000")
THRESHOLD = float(os.environ.get("VISUAL_DIFF_THRESHOLD", "0.005"))

BASELINE_DIR = pathlib.Path(__file__).parent / "visual" / "baselines" / "pixels"
DIFF_DIR     = pathlib.Path(__file__).parent / "visual" / "diffs"
DIFF_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Screen list
# ---------------------------------------------------------------------------

from tests.visual.screens import SCREENS, VIEWPORTS

# ---------------------------------------------------------------------------
# Pixel-diff using Pillow
# ---------------------------------------------------------------------------

def _pixel_diff(
    baseline_path: pathlib.Path, current_bytes: bytes
) -> tuple[float, bytes | None]:
    """Return (changed_ratio, diff_png_bytes | None).

    changed_ratio = pixels_changed / total_pixels
    diff_png paints changed pixels red over the current screenshot.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        pytest.skip("Pillow not installed -- pip install pillow")

    baseline = Image.open(str(baseline_path)).convert("RGB")
    current  = Image.open(io.BytesIO(current_bytes)).convert("RGB")

    # Size mismatch = treat as 100% diff (e.g. page height changed massively)
    if baseline.size != current.size:
        return 1.0, None

    diff = ImageChops.difference(baseline, current)
    w, h = diff.size
    total = w * h
    dp = diff.load()
    changed = sum(
        1 for y in range(h) for x in range(w)
        if sum(dp[x, y]) > 15  # 15/765 per-channel threshold
    )
    ratio = changed / total

    if ratio > 0:
        highlight = current.copy()
        px = highlight.load()
        dp = diff.load()
        w, h = highlight.size
        for y in range(h):
            for x in range(w):
                r, g, b = dp[x, y]
                if r + g + b > 15:
                    px[x, y] = (220, 40, 40)   # red overlay on changed pixels
        buf = io.BytesIO()
        highlight.save(buf, format="PNG")
        return ratio, buf.getvalue()

    return 0.0, None

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser():
    if not _HAS_PLAYWRIGHT:
        pytest.skip("playwright not installed -- pip install playwright && playwright install chromium")

    # Verify server is reachable before spawning a browser
    import urllib.request
    try:
        urllib.request.urlopen(BASE_URL, timeout=4)
    except Exception as exc:
        pytest.skip(f"Server not reachable at {BASE_URL}: {exc}\nStart the server or set JAKS_DEV_URL.")

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        yield br
        br.close()

# ---------------------------------------------------------------------------
# Parametric tests: one per (screen, viewport)
# ---------------------------------------------------------------------------

_PARAMS = [(s, vp) for s in SCREENS for vp in VIEWPORTS]
_IDS   = [f"{s.label}@{vp['label']}px" for s, vp in _PARAMS]


@pytest.mark.parametrize("screen,viewport", _PARAMS, ids=_IDS)
def test_visual(browser: "Browser", screen, viewport):
    """Render screen at viewport, pixel-diff against stored baseline.

    SKIP  -- no baseline PNG yet (run capture_baselines.py)
    PASS  -- pixel diff within threshold
    FAIL  -- pixel diff exceeds threshold (diff images saved to tests/visual/diffs/)
    """
    label = f"{screen.label}@{viewport['label']}px"
    baseline_path = BASELINE_DIR / f"{label}.png"

    if not baseline_path.exists():
        pytest.skip(f"No baseline for {label} -- run: python tests/visual/capture_baselines.py --only {screen.label}")

    # Render
    context = browser.new_context(
        viewport={"width": viewport["width"], "height": viewport["height"]},
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        response = page.goto(
            f"{BASE_URL}{screen.url}",
            wait_until=screen.wait_for,
            timeout=20_000,
        )
        status = response.status if response else 0
        if status >= 500:
            pytest.skip(f"{label}: server returned HTTP {status} -- live-DB issue, skip diff")

        page.wait_for_timeout(screen.settle_ms)
        page.evaluate(
            "() => document.querySelectorAll('[x-cloak]').forEach(el => el.style.display = 'none')"
        )
        current_bytes = page.screenshot(full_page=True)
    finally:
        page.close()
        context.close()

    # Diff
    ratio, diff_png = _pixel_diff(baseline_path, current_bytes)

    if ratio > THRESHOLD:
        current_path = DIFF_DIR / f"{label}.current.png"
        diff_path    = DIFF_DIR / f"{label}.diff.png"
        current_path.write_bytes(current_bytes)
        if diff_png:
            diff_path.write_bytes(diff_png)

        pytest.fail(
            f"\n[VISUAL REGRESSION] {label}\n"
            f"  Changed pixels : {ratio:.2%}  (threshold {THRESHOLD:.2%})\n"
            f"  Current        : {current_path}\n"
            f"  Diff highlight : {diff_path}\n"
            f"\nTo update baseline if change is intentional:\n"
            f"  python tests/visual/capture_baselines.py --force --only {screen.label}\n"
        )
