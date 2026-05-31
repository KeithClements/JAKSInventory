"""
tests/smoke/server.py
=====================
Boots the real FastAPI app in-process, on an ephemeral port, against an ISOLATED
freshly-created SQLite database — so the smoke suite never touches the dev
``data/jaks.db`` and always starts from known, seeded data.

Why in-process (not a subprocess + env var)
--------------------------------------------
``app/database.py`` hard-codes its DB path and is owned by the Backend lane, so we
do NOT modify it. Instead we re-point the app's DB globals at runtime — exactly the
pattern the QA-owned ``tests/conftest.py:activate()`` already uses for the
TestClient suite. ``app.main.on_startup`` is intentionally late-bound
(see its comment) so re-pointing ``app.database.SessionLocal`` BEFORE the server
starts makes startup seed OUR engine. Route handlers resolve the DB via the
``get_db`` FastAPI dependency, which we override the same way conftest does.

The server runs in a daemon thread; ``base_url`` is returned once it accepts
connections. ``SessionLocal`` is exposed so the data factory can seed and the
runner can read inventory back from the very same database the browser is hitting.
"""
from __future__ import annotations

import pathlib
import socket
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass

# Ensure the repo root (parent of tests/) is importable as ``app``.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _free_port() -> int:
    """Grab an OS-assigned free TCP port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _wait_until_serving(host: str, port: int, timeout_s: float = 30.0) -> None:
    """Block until the server accepts a TCP connection, or raise on timeout."""
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with closing(socket.create_connection((host, port), timeout=1.0)):
                return
        except OSError as exc:  # not up yet
            last_err = exc
            time.sleep(0.1)
    raise RuntimeError(
        f"Smoke server did not start listening on {host}:{port} within {timeout_s}s"
        + (f" (last error: {last_err})" if last_err else "")
    )


@dataclass
class SmokeServer:
    """A running in-process app instance backed by an isolated SQLite file."""

    base_url: str
    db_path: pathlib.Path
    SessionLocal: object  # sqlalchemy sessionmaker; typed loosely to avoid import cost here

    _server: object = None       # uvicorn.Server
    _thread: threading.Thread = None

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


def start_smoke_server(db_path: pathlib.Path) -> SmokeServer:
    """
    Re-point the app at ``db_path``, start uvicorn in a daemon thread, and return
    a :class:`SmokeServer` once it is accepting connections.

    ``db_path`` must NOT already exist (or must be empty) — startup creates all
    tables and runs the standard startup seeds (settings, counters, default user,
    core locations) against it.
    """
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 1. Build an isolated engine + SessionLocal and re-point the app globals ──
    # Must happen BEFORE the server starts so startup (init_db + seeds) targets it.
    import app.database as _appdb

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=False,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _appdb.engine = engine
    _appdb.SessionLocal = SessionLocal

    # ── 2. Import the app and override get_db (deps.py captured the old
    #       SessionLocal at import time, so the dependency override is required —
    #       same reason conftest.activate() does it). ──
    from app.deps import get_db
    from app.main import app as fastapi_app

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db

    # ── 3. Run uvicorn in a background daemon thread ──
    port = _free_port()
    host = "127.0.0.1"
    config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level="warning",
        # No reload, single worker — we want one deterministic process.
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="smoke-uvicorn", daemon=True)
    thread.start()

    _wait_until_serving(host, port)

    return SmokeServer(
        base_url=f"http://{host}:{port}",
        db_path=db_path,
        SessionLocal=SessionLocal,
        _server=server,
        _thread=thread,
    )
