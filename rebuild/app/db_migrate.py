"""Alembic adoption runner — called once from app.database.init_db() after
create_all + the legacy inline migrations.

Strategy ("adopt by stamp", same pattern as the sibling AxleShop app):

  * In-memory / test DBs are SKIPPED entirely — their schema comes from
    Base.metadata.create_all() and they never touch Alembic (zero test risk).
  * A live FILE DB with no alembic_version table is adopted:
      - was_fresh  → stamp HEAD   (create_all already built the full schema)
      - existing   → stamp BASELINE, then upgrade HEAD (run any later revisions)
  * A live FILE DB already under Alembic → upgrade HEAD (apply new revisions).

Never raises into startup: the legacy inline-migration list still keeps the
schema current, so an Alembic hiccup must not block the app from booting.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect

import app.database as _appdb

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BASELINE = "0001_baseline"


def _is_memory_db() -> bool:
    """True for the in-memory SQLite DBs the test suite uses (no Alembic there)."""
    db = (_appdb.engine.url.database or "").strip()
    return db in ("", ":memory:")


def _config():
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    # env.py runs against app.database.engine, so this URL is only a placeholder.
    cfg.set_main_option("sqlalchemy.url", str(_appdb.engine.url))
    return cfg


def adopt(was_fresh: bool) -> None:
    """Bring the live file DB under Alembic / apply pending revisions. No-op for
    in-memory test DBs. Best-effort: logs and continues on any failure."""
    if _is_memory_db():
        return
    try:
        from alembic import command
        cfg = _config()
        has_version = inspect(_appdb.engine).has_table("alembic_version")
        if has_version:
            command.upgrade(cfg, "head")
            return
        if was_fresh:
            # Fresh install: create_all already built the current (head) schema.
            command.stamp(cfg, "head")
        else:
            # Existing pre-Alembic DB: mark it at baseline, then run any revisions
            # added since baseline.
            command.stamp(cfg, BASELINE)
            command.upgrade(cfg, "head")
        log.info("Alembic adopted (was_fresh=%s)", was_fresh)
    except Exception:
        log.exception(
            "Alembic adoption failed — continuing startup (the inline-migration "
            "list keeps the schema current)."
        )
