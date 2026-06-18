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


def _head_revision() -> str:
    """The current Alembic head — computed, not hard-coded, so this suite needs no
    edit when a new incremental revision is stacked on the baseline."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_baseline_is_graph_root():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(cfg)
    # The migration chain still roots at the adoption baseline, and a head exists.
    assert "0001_baseline" in script.get_bases()
    assert script.get_current_head() is not None


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
        # Fresh build == head schema → stamped at HEAD (db_migrate.adopt).
        assert ver == _head_revision()
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
        # Stamped baseline, then upgraded through any later revisions → at head.
        assert ver == _head_revision()
    finally:
        appdb.engine, appdb.SessionLocal = saved_engine, saved_session


def test_adopt_runs_real_add_column_on_pre0002_db(tmp_path):
    """A genuine pre-0002 DB (column absent) → adopt() must actually run
    0002_vendor_private_label.upgrade()'s op.add_column, not just stamp. Guards the
    one piece of net-new migration logic that the create_all-seeded test skips."""
    saved_engine, saved_session = appdb.engine, appdb.SessionLocal
    dbfile = tmp_path / "pre0002.db"
    try:
        eng = create_engine(f"sqlite:///{dbfile}")
        appdb.engine = eng
        appdb.SessionLocal = sessionmaker(bind=eng)
        Base.metadata.create_all(bind=eng)        # builds head schema (incl. the column)
        eng.dispose()
        # Simulate a DB that predates 0002: drop the column (SQLite >= 3.35).
        con = sqlite3.connect(str(dbfile))
        con.execute("ALTER TABLE vendors DROP COLUMN private_label")
        con.commit()
        cols_before = [r[1] for r in con.execute("PRAGMA table_info(vendors)").fetchall()]
        con.close()
        assert "private_label" not in cols_before

        eng = create_engine(f"sqlite:///{dbfile}")
        appdb.engine = eng
        appdb.SessionLocal = sessionmaker(bind=eng)
        dm.adopt(was_fresh=False)                 # stamp baseline + upgrade → runs add_column
        eng.dispose()
        con = sqlite3.connect(str(dbfile))
        cols_after = [r[1] for r in con.execute("PRAGMA table_info(vendors)").fetchall()]
        ver = con.execute("select version_num from alembic_version").fetchone()[0]
        con.close()
        assert "private_label" in cols_after      # the ALTER actually ran
        assert ver == _head_revision()
    finally:
        appdb.engine, appdb.SessionLocal = saved_engine, saved_session
