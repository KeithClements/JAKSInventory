"""
app/routers/demo.py
====================
Beta sandbox administration — reset and reseed demo data.

Kept separate from admin.py (QA-owned smoke-test dashboard) to avoid
collision. Both mount under /admin; paths don't conflict.

Routes:
  GET  /admin/demo         — Status / confirmation page
  POST /admin/demo/reset   — Wipe + reseed (destructive; requires confirmation)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/demo", tags=["admin", "demo"])

_CONFIRM_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Demo Reset — JAKS Admin</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center">
  <div class="bg-white shadow-md rounded-lg px-8 py-8 w-full max-w-md">
    <h1 class="text-xl font-bold text-gray-800 mb-2">Reset &amp; Reseed Demo Data</h1>
    <p class="text-sm text-gray-500 mb-6">
      This will <strong class="text-red-600">permanently delete</strong> all customers,
      vendors, products, quotes, invoices, POs, and payments, then repopulate the
      database with realistic diesel-parts demo data.
      Settings, users, and the admin password are preserved.
    </p>
    {msg_block}
    <form method="post" action="/admin/demo/reset">
      <div class="flex items-center gap-3 mb-4">
        <input type="checkbox" id="confirm" name="confirm" value="1" class="w-4 h-4">
        <label for="confirm" class="text-sm text-gray-700">
          I understand this is irreversible and will delete all current data.
        </label>
      </div>
      <button type="submit"
              class="w-full bg-red-600 hover:bg-red-700 text-white font-medium
                     rounded-md px-4 py-2">
        Reset &amp; Reseed Demo Data
      </button>
    </form>
    <div class="mt-4 text-center">
      <a href="/" class="text-sm text-blue-600 hover:underline">← Back to dashboard</a>
    </div>
  </div>
</body>
</html>"""


@router.get("", response_class=HTMLResponse)
def demo_reset_form(request: Request, done: str = "", error: str = "", _admin=Depends(require_admin)):
    msg = ""
    if done:
        msg = f'<div class="bg-green-50 border border-green-200 text-green-700 text-sm rounded-md px-3 py-2 mb-4">Demo data reseeded successfully. <a href="/" class="underline">Go to dashboard →</a></div>'
    elif error:
        msg = f'<div class="bg-red-50 border border-red-200 text-red-700 text-sm rounded-md px-3 py-2 mb-4">{error}</div>'
    return HTMLResponse(_CONFIRM_PAGE.format(msg_block=msg))


@router.post("/reset")
async def demo_reset(request: Request, _admin=Depends(require_admin)):
    """Wipe and reseed demo data. Requires the confirm checkbox. ADMIN-only —
    this destroys all business data, so a non-admin (e.g. bookkeeping) or a
    forged request must never reach it."""
    from app.database import SessionLocal
    from app.seeds_demo import reset_and_seed_demo

    form = await request.form()
    if str(form.get("confirm", "")) != "1":
        return RedirectResponse(
            "/admin/demo?error=Please+check+the+confirmation+box",
            status_code=303,
        )

    db = SessionLocal()
    try:
        summary = reset_and_seed_demo(db)
        log.info("Demo reset complete: %s", summary)
    except Exception as exc:
        db.rollback()
        log.exception("Demo reset failed")
        return RedirectResponse(
            f"/admin/demo?error=Reset+failed:+{str(exc)[:80]}",
            status_code=303,
        )
    finally:
        db.close()

    parts = ", ".join(f"{v} {k}" for k, v in summary.items())
    return RedirectResponse(f"/admin/demo?done=1&summary={parts}", status_code=303)
