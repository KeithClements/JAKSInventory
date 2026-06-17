"""
tests/test_backup_acceptance.py
===============================
QA acceptance test for O3 — closes the §11 go-live gate.

Backend's tests/test_backup_restore.py exercises the backup *service*
(app/services/backup_service.py) against throwaway temp databases. This test
goes one level higher: it drives the real /admin/backup HTTP router
(app/routers/backup.py) end to end against an ISOLATED, FILE-BASED app database
and proves a backup -> mutate -> restore round-trip actually brings the app's
data back THROUGH the SQLAlchemy engine.

It specifically exercises the Windows-critical path the unit tests cannot reach:
the restore route disposes app.database.engine BEFORE the backup file is copied
over the live DB (an open SQLite file cannot be overwritten on Windows), then
the app must read the restored bytes on a fresh connection.

Isolation
---------
A file-based SQLite DB under tmp_path (backup/restore need a REAL file, so the
in-memory conftest engine won't do). backup_service.DB_PATH and the app's
engine / SessionLocal / get_db override are monkeypatched at the temp file; the
backup dir auto-resolves to <tmp>/backups via the patched DB_PATH (and
get_setting_value re-imports SessionLocal at call time, so it reads the isolated
DB too). The live data/jaks.db is never touched.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.database import Base
from app.deps import get_db
from app.models import __all_models__  # noqa: F401 — registers every model on Base
from app.main import app
from app.services import backup_service
from app.settings_utils import set_setting_value_db


def _count_customers(SessionLocal, name: str) -> int:
    from app.models.customer import Customer
    db = SessionLocal()
    try:
        return db.query(Customer).filter(Customer.company_name == name).count()
    finally:
        db.close()


@pytest.fixture()
def isolated_app(tmp_path, monkeypatch):
    """Point the app + the backup service at an isolated FILE-based SQLite DB."""
    db_file = tmp_path / "data" / "jaks.db"
    db_file.parent.mkdir(parents=True)

    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Re-point the app globals + the backup service at the temp file. The restore
    # route disposes _appdb.engine, so it must be THIS engine for the dispose to
    # release the handle we then overwrite.
    monkeypatch.setattr(_appdb, "engine", engine)
    monkeypatch.setattr(_appdb, "SessionLocal", SessionLocal)
    monkeypatch.setattr(backup_service, "DB_PATH", db_file)

    def _override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db

    # Deterministic: no startup-backup noise. Backups resolve to <tmp>/backups
    # (resolve_backup_dir falls back to DB_PATH.parent.parent/backups).
    db = SessionLocal()
    try:
        set_setting_value_db(db, "backup_on_startup", "false")
        db.commit()
    finally:
        db.close()

    with TestClient(app) as client:
        # Restore is now ADMIN-gated (app/deps.require_admin). This DB is file-
        # based, so the in-memory auth bypass is OFF — authenticate as the seeded
        # admin so the backup/restore round-trip below reaches the route.
        import app.auth as auth
        auth.reset_secret_cache()
        login = client.post("/login", data={"username": "admin", "password": "admin"},
                            follow_redirects=False)
        assert login.status_code == 303, login.text[:200]
        yield client, SessionLocal, db_file

    app.dependency_overrides.pop(get_db, None)


def _csrf(client):
    """§21.3 — authenticated POSTs now need the double-submit CSRF token; the
    TestClient holds the jaks_csrf cookie issued on the login response."""
    return {"X-CSRF-Token": client.cookies.get("jaks_csrf", "")}


def test_backup_restore_roundtrip_via_router(isolated_app):
    """The §11 gate: a row backed up, then deleted, comes back after a restore
    driven entirely through the /admin/backup router (engine.dispose() included)."""
    client, SessionLocal, db_file = isolated_app
    from app.models.customer import Customer

    NAME = "Backup Acceptance Co"

    # 1. Seed a row that must survive the restore.
    db = SessionLocal()
    try:
        db.add(Customer(company_name=NAME, contact_name="QA", email="ba@test.local"))
        db.commit()
    finally:
        db.close()
    assert _count_customers(SessionLocal, NAME) == 1

    # 2. Create a backup through the router.
    r = client.post("/admin/backup/run", headers=_csrf(client))
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    assert created.startswith("jaks-") and created.endswith(".db")

    # 3. Mutate the live DB AFTER the snapshot (simulate loss / corruption).
    db = SessionLocal()
    try:
        db.query(Customer).filter(Customer.company_name == NAME).delete()
        db.commit()
    finally:
        db.close()
    assert _count_customers(SessionLocal, NAME) == 0

    # 4. Restore through the router. This disposes app.database.engine first
    #    (the Windows-critical step) and copies the snapshot over the live file.
    r = client.post("/admin/backup/restore", data={"filename": created}, headers=_csrf(client))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # 5. The app reads the restored row on a fresh connection -> round-trip proven.
    assert _count_customers(SessionLocal, NAME) == 1, (
        "restore did not bring the seeded customer back through the engine "
        "(the engine.dispose() -> overwrite -> reopen path failed)"
    )

    # 6. A pre-restore safety copy of the mutated state was written next to the DB.
    safety = list(db_file.parent.glob(f"{db_file.stem}-prerestore-*.db"))
    assert safety, "expected a pre-restore safety copy next to the live DB"


def test_restore_rejects_path_traversal(isolated_app):
    """The restore route accepts only a bare filename inside the backup dir."""
    client, _SessionLocal, _db_file = isolated_app
    r = client.post("/admin/backup/restore", data={"filename": "../jaks.db"}, headers=_csrf(client))
    assert r.status_code == 404, r.text
    assert r.json()["ok"] is False


def test_restore_requires_admin_role(isolated_app):
    """A non-admin (the seeded bookkeeping user) must NOT be able to restore the
    database — restore overwrites live data, so it is gated to ADMIN only."""
    client, _SessionLocal, _db_file = isolated_app
    # The fixture signed in as admin; switch to the seeded bookkeeper (non-admin).
    r = client.post("/login", data={"username": "bookkeeper", "password": "bookkeeper"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:200]

    r = client.post("/admin/backup/restore", data={"filename": "anything.db"}, headers=_csrf(client))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Administrator role required."
