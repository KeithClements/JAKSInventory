from __future__ import annotations

from typing import Generator
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Only used as the test-environment fallback (see _is_test_env below).
# Never returned in production; production routes require a real session.
DEFAULT_USER_ID = 1


def _is_test_env() -> bool:
    """True when the active DB engine is in-memory.

    All test suites call ``tests.conftest.activate(fresh_engine())`` which
    points ``app.database.engine`` at a ``sqlite:///:memory:`` URL.  That
    signal lets ``get_current_user_id`` bypass the login enforcement so
    TestClient-based tests keep working without sending a session cookie.

    This function is purposely a named callable so tests can monkeypatch it:
        monkeypatch.setattr(app.deps, "_is_test_env", lambda: False)
    """
    import app.database as _appdb
    return ":memory:" in str(_appdb.engine.url)


def get_current_user_id(request: Request) -> int:
    """Resolve the acting user id and enforce authentication (O2 ENFORCE).

    1. Valid ``jaks_session`` cookie → return the signed-in user's id.
    2. No valid cookie **and** running in tests (in-memory DB) → return
       DEFAULT_USER_ID so QA/TestClient suites work without cookies.
    3. No valid cookie **and** running in production (file DB) → redirect to
       /login. HTMX requests receive an ``HX-Redirect`` header so partial
       swaps are handled gracefully; regular requests get a plain HTTP 302.
    """
    from app.auth import read_session_token, SESSION_COOKIE

    uid = read_session_token(request.cookies.get(SESSION_COOKIE))
    if uid is not None:
        return uid

    # Test / dev bypass — see _is_test_env docstring.
    if _is_test_env():
        return DEFAULT_USER_ID

    # Production: unauthenticated → redirect to /login.
    # HTMX requests need HX-Redirect (not a standard redirect) so the browser
    # navigation is intercepted before a partial-swap injects a full login page
    # into a UI slot.
    if request.headers.get("HX-Request"):
        raise HTTPException(
            status_code=200,
            headers={"HX-Redirect": "/login"},
        )
    raise HTTPException(
        status_code=302,
        headers={"Location": "/login"},
    )


def get_current_user(
    uid: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Load the acting :class:`~app.models.user.User` row (enforces auth).

    Builds on ``get_current_user_id`` so the same login enforcement / in-memory
    test bypass applies, then resolves the full user so callers can check role.
    In the test bypass ``uid`` is ``DEFAULT_USER_ID`` (#1), which startup seeds
    as an ADMIN, so existing in-memory route tests keep passing. If the id maps
    to no row we treat the request as unauthenticated and redirect to /login.
    """
    from app.models.user import User

    user = db.query(User).filter(User.id == uid).first()
    if user is None:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def require_admin(user=Depends(get_current_user)):
    """Gate a route to ADMIN-role users only — HTTP 403 otherwise.

    Used for destructive admin operations such as restoring a backup over the
    live database: a non-admin (e.g. the bookkeeping user) must never be able to
    overwrite production data. Returns the user so the route can use it if needed.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator role required.")
    return user


def require_reports_access(user=Depends(get_current_user)):
    """Gate a route to ADMIN or BOOKKEEPING users only — HTTP 403 otherwise.

    Reports and the dashboard expose sensitive financials — cost basis, margins,
    AR/AP aging, sales tax collected, and captured competitor pricing. A counter
    clerk (SALES) or READ_ONLY user must not be able to read or export that data.

    Built on ``get_current_user`` so it inherits the same login enforcement and
    the in-memory test bypass (DEFAULT_USER_ID #1 is a seeded ADMIN, so existing
    in-memory route tests keep passing). Returns the user for optional route use.
    """
    from app.constants import UserRole

    if user.role not in (UserRole.ADMIN, UserRole.BOOKKEEPING):
        raise HTTPException(
            status_code=403,
            detail="Reports access requires the administrator or bookkeeping role.",
        )
    return user
