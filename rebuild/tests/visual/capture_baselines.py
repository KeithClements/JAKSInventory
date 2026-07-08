"""
tests/visual/capture_baselines.py
==================================
Capture (or re-capture) PNG baselines for every registered screen.

Usage:
    # Capture all screens (skip already-captured unless --force):
    python tests/visual/capture_baselines.py

    # Force re-capture of everything:
    python tests/visual/capture_baselines.py --force

    # Re-capture specific screens only:
    python tests/visual/capture_baselines.py --only products_list invoices_list

    # Re-capture a single viewport:
    python tests/visual/capture_baselines.py --only products_list --viewport 1280

Requires:
    pip install playwright
    playwright install chromium

The server must be running at BASE_URL (default http://localhost:8000).
Set JAKS_DEV_URL env var to override.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

# Ensure repo root is importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tests.visual.screens import SCREENS, VIEWPORTS

BASE_URL = os.environ.get("JAKS_DEV_URL", "http://localhost:8000")
BASELINE_DIR = pathlib.Path(__file__).parent / "baselines" / "pixels"
BASELINE_DIR.mkdir(parents=True, exist_ok=True)


def _baseline_path(label: str, vp_label: str) -> pathlib.Path:
    return BASELINE_DIR / f"{label}@{vp_label}px.png"


def capture_all(
    force: bool = False,
    only: list[str] | None = None,
    viewport_filter: str | None = None,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    # Filter screen list
    screens = SCREENS
    if only:
        screens = [s for s in SCREENS if s.label in only]
        missing = set(only) - {s.label for s in screens}
        if missing:
            print(f"WARNING: unknown screen labels: {missing}")

    viewports = VIEWPORTS
    if viewport_filter:
        viewports = [v for v in VIEWPORTS if v["label"] == viewport_filter]

    total = len(screens) * len(viewports)
    captured = 0
    skipped = 0
    failed = 0

    print(f"\nCapturing {total} screenshots "
          f"({len(screens)} screens × {len(viewports)} viewports)")
    print(f"  Server: {BASE_URL}")
    print(f"  Output: {BASELINE_DIR}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for vp in viewports:
                context = browser.new_context(
                    viewport={"width": vp["width"], "height": vp["height"]},
                    # Disable animations so screenshots are deterministic
                    reduced_motion="reduce",
                )
                page = context.new_page()

                for screen in screens:
                    out_path = _baseline_path(screen.label, vp["label"])

                    if out_path.exists() and not force:
                        print(f"  SKIP  {screen.label}@{vp['label']}px  (already exists, use --force to overwrite)")
                        skipped += 1
                        continue

                    try:
                        url = f"{BASE_URL}{screen.url}"

                        # Pre-flight HTTP check -- don't baseline a 5xx error page
                        response = page.goto(url, wait_until=screen.wait_for, timeout=20_000)
                        status = response.status if response else 0
                        if status >= 500:
                            print(f"  WARN  {screen.label}@{vp['label']}px  HTTP {status} -- skipping (live-DB issue?)")
                            failed += 1
                            continue
                        if status >= 400:
                            print(f"  WARN  {screen.label}@{vp['label']}px  HTTP {status} -- capturing error state")

                        if screen.settle_ms:
                            page.wait_for_timeout(screen.settle_ms)

                        # Dismiss any x-cloak elements (Alpine has hydrated)
                        page.evaluate(
                            "() => document.querySelectorAll('[x-cloak]')"
                            ".forEach(el => el.style.display = 'none')"
                        )

                        page.screenshot(path=str(out_path), full_page=True)
                        size_kb = out_path.stat().st_size // 1024

                        # Warn if unrealistically small (likely unstyled partial or error text)
                        flag = "  ⚠ small" if size_kb < 20 else ""
                        print(f"  OK    {screen.label}@{vp['label']}px  ({size_kb}KB){flag}")
                        captured += 1

                    except Exception as exc:
                        print(f"  FAIL  {screen.label}@{vp['label']}px  {exc}")
                        failed += 1

                context.close()
        finally:
            browser.close()

    print(f"\nDone: {captured} captured, {skipped} skipped, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture visual regression baselines")
    parser.add_argument("--force", action="store_true",
                        help="Re-capture even if baseline already exists")
    parser.add_argument("--only", nargs="+", metavar="LABEL",
                        help="Only capture these screen labels")
    parser.add_argument("--viewport", choices=["1280", "1920"],
                        help="Only capture this viewport width")
    args = parser.parse_args()

    capture_all(force=args.force, only=args.only, viewport_filter=args.viewport)
