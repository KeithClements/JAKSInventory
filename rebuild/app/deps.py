from __future__ import annotations

from typing import Generator
from fastapi import Request
from sqlalchemy.orm import Session
from app.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Fallback actor when no valid session cookie is present. O2 makes login OPT-IN:
# attribution uses the signed-in user when available, else this admin id, so
# unauthenticated requests / TestClient calls keep working in single-user mode.
DEFAULT_USER_ID = 1


def get_current_user_id(request: Request) -> int:
    """Resolve the acting user id from the signed session cookie (O2).

    Returns the signed-in user's id when a valid session cookie is present,
    otherwise DEFAULT_USER_ID. Used as a FastAPI dependency throughout the
    routers, so every service audit() row is stamped with the real actor once
    the user has logged in.
    """
    from app.auth import read_session_token, SESSION_COOKIE

    uid = read_session_token(request.cookies.get(SESSION_COOKIE))
    return uid if uid is not None else DEFAULT_USER_ID
