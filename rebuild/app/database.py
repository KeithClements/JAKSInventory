from __future__ import annotations

import logging
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jaks.db"
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── Lightweight inline migrations for SQLite ──────────────────────────────────
# We don't use Alembic — too heavy for a single-user local app. Each entry is
# `(table_name, column_name, column_sql_definition)`. On startup we add any
# missing columns via ALTER TABLE; idempotent (skips columns that already exist).
# When adding a new column, append a new tuple here; never delete entries.
_PENDING_COLUMN_ADDITIONS: list[tuple[str, str, str]] = [
    # Phase A — Transaction Workspace: parent/core cascade flags
    ("invoice_lines", "is_auto_generated",   "BOOLEAN NOT NULL DEFAULT 0"),
    ("invoice_lines", "is_locked_to_parent", "BOOLEAN NOT NULL DEFAULT 0"),
    ("quote_lines",   "is_auto_generated",   "BOOLEAN NOT NULL DEFAULT 0"),
    ("quote_lines",   "is_locked_to_parent", "BOOLEAN NOT NULL DEFAULT 0"),
    ("so_lines",      "parent_line_id",      "INTEGER NULL REFERENCES so_lines(id)"),
    ("so_lines",      "is_core_line",        "BOOLEAN NOT NULL DEFAULT 0"),
    ("so_lines",      "is_auto_generated",   "BOOLEAN NOT NULL DEFAULT 0"),
    ("so_lines",      "is_locked_to_parent", "BOOLEAN NOT NULL DEFAULT 0"),
]


def _apply_inline_migrations() -> None:
    """Run idempotent ALTER TABLE ADD COLUMN for any columns missing from
    existing databases. New databases pick everything up from create_all()."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, sql_def in _PENDING_COLUMN_ADDITIONS:
            if table not in existing_tables:
                continue  # fresh DB — create_all() handled it
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column in cols:
                continue
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sql_def}'))
            log.info("Added column %s.%s", table, column)


def init_db() -> None:
    # Importing __all_models__ is not dead code — the import side-effect registers
    # every model class with Base.metadata so create_all() can see all tables.
    from app.models import __all_models__  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _apply_inline_migrations()
