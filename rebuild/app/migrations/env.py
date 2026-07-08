"""Alembic environment for the Axle / JAKS ERP.

Key design choice: migrations run against the app's CURRENT engine
(``app.database.engine``) rather than an engine built from the .ini URL. This is
what lets the in-app adopter (app/db_migrate.py) and the test harness (which
re-points app.database.engine at an isolated DB) share one code path. SQLite gets
``render_as_batch=True`` so future ALTER/DROP migrations work despite SQLite's
limited ALTER TABLE.
"""
from __future__ import annotations

from alembic import context

import app.database as _appdb
from app.database import Base
from app.models import __all_models__  # noqa: F401 — register all models on Base.metadata

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(_appdb.engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = _appdb.engine
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,   # SQLite-safe ALTER/DROP
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
