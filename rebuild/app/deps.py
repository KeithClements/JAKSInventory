from __future__ import annotations

from typing import Generator
from fastapi import HTTPException, Request
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
