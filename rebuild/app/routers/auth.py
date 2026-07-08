"""
app/routers/auth.py
===================
Minimal login / logout for the O2 go-live gate.

The login page is served as self-contained inline HTML (Backend owns router view
logic; this avoids creating a UI-lane-owned template for a one-field form). The
UI lane can promote it to a styled, base.html-extending screen later if desired.

Flow:
  GET  /login   → render the form (optional ?error=1)
  POST /login   → verify password, set the signed session cookie, redirect to /
  POST /logout  → clear the cookie, redirect to /login

Attribution: once logged in, the session cookie drives app.deps.get_current_user_id,
so every service's audit() row is stamped with the real signed-in user.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    hash_password,
    make_session_token,
    verify_password,
)
from app.deps import get_db, get_current_user
from app.models.user import User

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


# ── Login throttle (brute-force lockout) ──────────────────────────────────────
# The app binds 0.0.0.0 on the shop LAN with no lockout, so admin/admin-class
# guesses were free. After LOGIN_FAIL_MAX failures for the same username+IP within
# the window, further attempts are refused for LOGIN_LOCK_SECONDS — long enough to
# make online guessing impractical, short enough that a fat-fingered owner just
# waits a minute. In-process only (single-box deployment); a restart clears it.
# Skipped under the test bypass so the suite is unaffected — a dedicated test
# (test_login_throttle) unsets it to exercise the real path. (C-review: no lockout.)
LOGIN_FAIL_MAX = 5
LOGIN_LOCK_SECONDS = 60
_login_fails: dict[str, tuple[int, float]] = {}   # key → (fail_count, last_fail_ts)
_login_lock = threading.Lock()


def _throttle_key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "?"
    return f"{username.strip().lower()}|{ip}"


def _login_locked_for(key: str) -> float:
    """Seconds remaining on the lockout for ``key``, or 0 if not locked."""
    with _login_lock:
        rec = _login_fails.get(key)
        if not rec:
            return 0.0
        count, last = rec
        if count < LOGIN_FAIL_MAX:
            return 0.0
        remaining = LOGIN_LOCK_SECONDS - (time.time() - last)
        if remaining <= 0:
            _login_fails.pop(key, None)
            return 0.0
        return remaining


def _record_login_fail(key: str) -> None:
    with _login_lock:
        count, last = _login_fails.get(key, (0, 0.0))
        # A fresh window if the previous lockout fully elapsed.
        if count >= LOGIN_FAIL_MAX and (time.time() - last) >= LOGIN_LOCK_SECONDS:
            count = 0
        _login_fails[key] = (count + 1, time.time())


def _clear_login_fails(key: str) -> None:
    with _login_lock:
        _login_fails.pop(key, None)


def reset_login_throttle() -> None:
    """Clear all throttle state (test helper / manual unlock)."""
    with _login_lock:
        _login_fails.clear()


def _throttle_active() -> bool:
    """Throttle everywhere except under the suite's auth bypass."""
    from app.security import _test_bypass
    return not _test_bypass()


@router.get("/login")
def login_form(request: Request, error: str = ""):
    return templates.TemplateResponse(
        request, "auth/login.html",
        {"error": bool(error), "locked": error == "locked"},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    throttle = _throttle_active()
    key = _throttle_key(request, username)
    if throttle and _login_locked_for(key) > 0:
        # Too many recent failures for this username/IP — refuse without even
        # checking the password (which is what makes online guessing impractical).
        return RedirectResponse("/login?error=locked", status_code=303)

    user = (
        db.query(User)
        .filter(User.username == username.strip(), User.is_active == True)  # noqa: E712
        .first()
    )
    if user is None or not verify_password(password, user.password_hash):
        # Same response whether the user exists or not (no account enumeration).
        if throttle:
            _record_login_fail(key)
        return RedirectResponse("/login?error=1", status_code=303)

    if throttle:
        _clear_login_fails(key)
    user.last_login_at = datetime.utcnow()
    db.commit()

    resp = RedirectResponse("/", status_code=303)
    # §21.3 — Secure flag set for HTTPS (directly or via X-Forwarded-Proto) or when
    # JAKS_SECURE_COOKIES=1. Defaults OFF so a plain-HTTP LAN run still logs in.
    from app.security import secure_cookies
    resp.set_cookie(
        SESSION_COOKIE,
        make_session_token(user.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure_cookies(request),
    )
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ── Self-service account / change password ────────────────────────────────────
# The in-app way to get OFF the default 'admin' password without an env reseed.

@router.get("/account")
def account_page(request: Request, user=Depends(get_current_user), ok: str = "", error: str = ""):
    # must_rotate → the default-password gate (main.enforce_password_rotation) is
    # what forced this user here; the template shows a "why am I here" banner and
    # hides the dead-loop "Back to dashboard" link while the gate is active. Lazy
    # import avoids a circular import at module load (main imports this router).
    try:
        from app.main import account_uses_default_password
        must_rotate = account_uses_default_password(user)
    except Exception:  # noqa: BLE001 — banner is cosmetic; never break the page
        must_rotate = False
    return templates.TemplateResponse(
        request,
        "auth/account.html",
        {"user": user, "ok": bool(ok), "error": error, "must_rotate": must_rotate},
    )


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Let the signed-in user change THEIR OWN password (verify current → set new).

    ``user`` is bound to the same request-scoped session as ``db`` (FastAPI caches
    the get_db dependency), so committing here persists the new hash. Min length 8;
    new must match the confirmation.
    """
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse(
            "/account?error=" + url_quote("Current password is incorrect."),
            status_code=303,
        )
    if len(new_password) < 8:
        return RedirectResponse(
            "/account?error=" + url_quote("New password must be at least 8 characters."),
            status_code=303,
        )
    if new_password != confirm_password:
        return RedirectResponse(
            "/account?error=" + url_quote("New passwords do not match."),
            status_code=303,
        )

    user.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse("/account?ok=1", status_code=303)
