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
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
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
