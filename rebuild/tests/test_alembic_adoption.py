"""
tests/test_alembic_adoption.py
==============================
Locks the Alembic "adopt by stamp" wiring (app/db_migrate.py + app/migrations/):
  * head resolves to the baseline revision
  * in-memory (test) DBs are SKIPPED — schema comes from create_all, no Alembic
  * a real FILE DB gets an alembic_version table stamped at baseline
These manage their own engines and restore the app globals in finally.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import app.database as appdb
import app.db_migrate as dm
from app.database import Base
from app.models import __all_models__  # noqa: F401 — register models

_MIGRATIONS_DIR = pathlib.Path(appdb.__file__).resolve().parent / "migrations"


def test_head_is_baseline():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_current_head() == "0001_baseline"


def test_adopt_skips_in_memory_db():
    saved_engine, saved_session = appdb.engine, appdb.SessionLocal
    try:
        eng = create_engine("sqlite:///:memory:")
        appdb.engine = eng
        appdb.SessionLocal = sessionmaker(bind=eng)
        Base.metadata.create_all(bind=eng)
        dm.adopt(was_fresh=True)
        # In-memory DBs must NOT be touched by Alembic.
        assert inspect(eng).has_table("alembic_version") is False
    finally:
        appdb.engine, appdb.SessionLocal = saved_engine, saved_session


def test_adopt_fresh_file_db_stamps_baseline(tmp_path):
    saved_engine, saved_session = appdb.engine, appdb.SessionLocal
    dbfile = tmp_path / "fresh.db"
    try:
        eng = create_engine(f"sqlite:///{dbfile}")
        appdb.engine = eng
        appdb.SessionLocal = sessionmaker(bind=eng)
        Base.metadata.create_all(bind=eng)   # fresh build == head schema
        dm.adopt(was_fresh=True)
        assert inspect(eng).has_table("alembic_version")
        eng.dispose()
        con = sqlite3.connect(str(dbfile))
        ver = con.execute("select version_num from alembic_version").fetchone()[0]
        con.close()
        assert ver == "0001_baseline"
    finally:
        appdb.engine, appdb.SessionLocal = saved_engine, saved_session


def test_adopt_existing_file_db_then_upgrade_is_idempotent(tmp_path):
    """Existing (not-fresh) file DB → stamp baseline + upgrade head; re-running
    adopt() afterwards just upgrades head again (no error, stays at baseline)."""
    saved_engine, saved_session = appdb.engine, appdb.SessionLocal
    dbfile = tmp_path / "existing.db"
    try:
        eng = create_engine(f"sqlite:///{dbfile}")
        appdb.engine = eng
        appdb.SessionLocal = sessionmaker(bind=eng)
        Base.metadata.create_all(bind=eng)
        dm.adopt(was_fresh=False)   # stamp baseline + upgrade head
        dm.adopt(was_fresh=False)   # second run: has version table → upgrade head only
        eng.dispose()
        con = sqlite3.connect(str(dbfile))
        ver = con.execute("select version_num from alembic_version").fetchone()[0]
        con.close()
        assert ver == "0001_baseline"
    finally:
        appdb.engine, appdb.SessionLocal = saved_engine, saved_session
