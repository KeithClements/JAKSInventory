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

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    make_session_token,
    verify_password,
)
from app.deps import get_db
from app.models.user import User

router = APIRouter(tags=["auth"])


_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in — JAKS Inventory</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center">
  <form method="post" action="/login"
        class="bg-white shadow-md rounded-lg px-8 py-8 w-full max-w-sm">
    <h1 class="text-xl font-bold text-gray-800 mb-1">JAKS Inventory</h1>
    <p class="text-sm text-gray-500 mb-6">Sign in to continue</p>
    {error_block}
    <label class="block text-sm font-medium text-gray-700 mb-1">Username</label>
    <input name="username" autofocus autocomplete="username"
           class="w-full border border-gray-300 rounded-md px-3 py-2 mb-4
                  focus:outline-none focus:ring-2 focus:ring-blue-500" />
    <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
    <input name="password" type="password" autocomplete="current-password"
           class="w-full border border-gray-300 rounded-md px-3 py-2 mb-6
                  focus:outline-none focus:ring-2 focus:ring-blue-500" />
    <button type="submit"
            class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium
                   rounded-md px-4 py-2">Sign in</button>
  </form>
</body>
</html>"""

_ERROR_BLOCK = (
    '<div class="bg-red-50 border border-red-200 text-red-700 text-sm '
    'rounded-md px-3 py-2 mb-4">Invalid username or password.</div>'
)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str = "") -> HTMLResponse:
    return HTMLResponse(
        _LOGIN_PAGE.format(error_block=_ERROR_BLOCK if error else "")
    )


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
    resp.set_cookie(
        SESSION_COOKIE,
        make_session_token(user.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
