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


@router.get("/login")
def login_form(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "auth/login.html", {"error": bool(error)})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = (
        db.query(User)
        .filter(User.username == username.strip(), User.is_active == True)  # noqa: E712
        .first()
    )
    if user is None or not verify_password(password, user.password_hash):
        # Same response whether the user exists or not (no account enumeration).
        return RedirectResponse("/login?error=1", status_code=303)

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
