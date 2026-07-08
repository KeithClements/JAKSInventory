# QA Run Guide

## Stable server (REQUIRED for manual and visual tests)

Always run the development server with `--no-reload` during QA passes.
With `--reload` active, any file save in another lane's worktree can
hot-reload the server mid-request, producing spurious 500s or silently
corrupting in-flight database sessions.

```bat
cd C:\Users\keith\JAKSInventory\rebuild

REM Stable server -- no reload, dedicated QA port
.venv\Scripts\python.exe -m uvicorn app.main:app ^
    --host 127.0.0.1 ^
    --port 8001 ^
    --no-reload
```

Then in a second terminal:

```bat
cd C:\Users\keith\JAKSInventory\rebuild

REM Run the full test suite (TestClient -- in-process, no network needed)
.venv\Scripts\pytest tests/ -v

REM Run visual regression tests against the stable server
set JAKS_DEV_URL=http://127.0.0.1:8001
.venv\Scripts\pytest tests/test_visual_regression.py -m visual -v

REM Capture new baselines after intentional changes
set JAKS_DEV_URL=http://127.0.0.1:8001
.venv\Scripts\python.exe tests/visual/capture_baselines.py --force --only <label>
```

## Test suite overview

| File | What it covers | Fast? |
|---|---|---|
| `tests/test_smoke.py` | Top-level list routes (200 check) | ✅ ~2s |
| `tests/test_smoke_subendpoints.py` | HTMX sub-endpoints, workspace GETs, preview panels, add-line POSTs | ✅ ~2s |
| `tests/test_ui_lint.py` | Design-system rules §1-§10 + compiled CSS guard | ✅ ~1s |
| `tests/test_html_snapshots.py` | HTML structure regression (tab labels, markers, size) | ✅ ~2s |
| `tests/test_visual_regression.py` | Pixel-accurate Playwright screenshots at 1280+1920px | ⚠ ~2min |
| `tests/test_workflow_*.py` | Business workflow scenarios | ✅ ~5s |

Run the first four on every PR.  Run the visual tests before merges
that touch templates or CSS.

## Re-baseline procedure

### After a template governance pass

```bat
REM Add new HTML snapshot baselines
.venv\Scripts\pytest tests/test_html_snapshots.py -v
REM (first run for any new screen: SKIP + saves baseline; second run: PASS)
```

### After an intentional visual change

```bat
REM Re-capture pixel baseline for a specific screen
set JAKS_DEV_URL=http://127.0.0.1:8001
.venv\Scripts\python.exe tests/visual/capture_baselines.py --force --only <label>
git add tests/visual/baselines/pixels/<label>@*.png
git commit -m "QA: re-baseline <label> after intentional change"
```

### PENDING: payments_list pixel baseline

The `payments_list` pixel baseline was captured with `preview_dock_shell`
commented out (dock deferred pending backend route).  Once backend lands
`GET /payments/preview/{id}` (before `/{id}` in payments.py), re-capture:

```bat
set JAKS_DEV_URL=http://127.0.0.1:8001
.venv\Scripts\python.exe tests/visual/capture_baselines.py --force --only payments_list
git add tests/visual/baselines/pixels/payments_list@*.png
git commit -m "QA: re-baseline payments_list after dock fix"
```

## Known broken endpoints (track in QA gate)

| Endpoint | Bug | File | Status |
|---|---|---|---|
| `GET /sales-orders/_/product-search?q=<2+chars>` | `Product.part_number` — no such column | `sales_orders.py:382` | ❌ OPEN |
| `GET /quotes/` (live server) | Schema/migration gap on live `jaks.db` | backend | ❌ OPEN |
| `GET /returns/` (live server) | Schema/migration gap on live `jaks.db` | backend | ❌ OPEN |

These failures are tracked by `test_smoke_subendpoints.py` and will surface
automatically when a fix is merged.
