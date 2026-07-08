"""
tests/visual/capture_local.py
=============================
Deterministic visual-baseline capture.

Boots the REAL app (real templates + the compiled static CSS) on an isolated,
demo-seeded **in-memory** engine with auth bypassed, runs a background uvicorn,
and drives capture_baselines.capture_all() against it.

Why not `python capture_baselines.py` against the dev server: that shoots a live
server backed by the mutable data/jaks.db, so baselines drift run-to-run (the
documented visual-suite instability). This harness pins the data to the fixed
demo seed, so the same baselines reproduce every run — the determinism the
pixel-diff needs. For a stable diff later, regenerate AND compare through this
same harness (same seed), not against the mutable file DB.

Run:
    .venv\\Scripts\\python.exe tests/visual/capture_local.py --force
    .venv\\Scripts\\python.exe tests/visual/capture_local.py --force --only products_list dashboard
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import threading
import time
import urllib.request

# 0. capture_baselines prints a "⚠ small" flag for <20KB shots; the Windows
#    cp1252 console can't encode it and the UnicodeEncodeError is otherwise
#    miscounted as a capture failure. Force UTF-8 stdout so the report is honest.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 1. Bypass the enforce_login middleware (main.py honours JAKS_SKIP_AUTH) BEFORE
#    the app module is imported.
os.environ["JAKS_SKIP_AUTH"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# 2. Point the app's DB globals at an isolated in-memory engine BEFORE startup.
#    ":memory:" in the URL also makes deps._is_test_env() True, so any route that
#    Depends(get_current_user_id) resolves to DEFAULT_USER_ID instead of redirecting.
from tests.conftest import activate, fresh_engine  # noqa: E402

_SessionLocal = activate(fresh_engine())

from app.main import app  # noqa: E402
from app.seeds_demo import reset_and_seed_demo  # noqa: E402

PORT = 8099


def _wait_until_up(url: str, tries: int = 150) -> bool:
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic visual baseline capture")
    ap.add_argument("--force", action="store_true", help="re-capture even if baseline exists")
    ap.add_argument("--only", nargs="+", metavar="LABEL", help="only these screen labels")
    ap.add_argument("--viewport", choices=["1280", "1920"], help="only this viewport")
    args = ap.parse_args()

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning",
                            lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{PORT}"
    if not _wait_until_up(f"{base}/login"):
        print("ERROR: server did not come up", file=sys.stderr)
        server.should_exit = True
        return 1

    # Startup has run (settings / users / type-defaults seeded). Layer the rich
    # demo data (products, customers, vendors, cores, ...) on top, deterministically.
    db = _SessionLocal()
    try:
        summary = reset_and_seed_demo(db)
        print(f"  demo seed: {summary}")
    finally:
        db.close()

    # capture_baselines reads BASE_URL at import time → set it first, import after.
    os.environ["JAKS_DEV_URL"] = base
    from tests.visual.capture_baselines import capture_all

    try:
        capture_all(force=args.force, only=args.only, viewport_filter=args.viewport)
        return 0
    except SystemExit as exc:           # capture_all sys.exit(1) on any failure
        return int(exc.code or 0)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
