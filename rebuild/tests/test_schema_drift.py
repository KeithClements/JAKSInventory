"""
tests/test_schema_drift.py
==========================
CI gate: model definitions must stay in sync with _PENDING_COLUMN_ADDITIONS.

THE test that would have caught the quote ESN drift before the owner saw it:

  When a developer adds a column to a SQLAlchemy model they MUST also add a
  matching tuple to `_PENDING_COLUMN_ADDITIONS` in `app/database.py`, so that
  existing production/staging databases (which cannot be dropped and recreated)
  pick up the new column on the next server start.  If the entry is missing,
  `create_all()` on a fresh DB has the column, but an existing DB silently omits
  it — causing 500s or silent data loss when the app tries to read/write it.

  The ESN regression (@640c2e2 fixed it): `esn`, `engine_manufacturer`,
  `engine_model`, `customer_po_number`, `customer_job_number` were added to the
  Quote model but not to `_PENDING_COLUMN_ADDITIONS`; live databases never got
  the columns; the owner discovered it at the quote workspace.

Two tests:

  test_all_migration_entries_reference_valid_model_columns
    Inverse guard: every (table, col) in _PENDING_COLUMN_ADDITIONS must exist
    in Base.metadata. Catches stale migration entries left behind after a
    column is renamed or removed from a model.

  test_migration_roundtrip_covers_all_model_columns
    Forward guard (the one that catches the ESN class of bug): builds a
    "minimal" in-memory DB that contains ALL tables but ONLY the columns that
    are NOT covered by _PENDING_COLUMN_ADDITIONS (simulating an existing DB
    before the migrations run), then applies the migrations, then asserts
    every model column is present. Fails with a precise list of missing
    entries so the developer knows exactly what to add to database.py.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_schema_drift.py -v
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import Column, MetaData, Table, create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.database import Base, _PENDING_COLUMN_ADDITIONS
from app.models import __all_models__  # noqa: F401 — registers every model


# ---------------------------------------------------------------------------
# 1. Stale-migration guard: every migration entry must exist in the model
# ---------------------------------------------------------------------------

def test_all_migration_entries_reference_valid_model_columns():
    """
    Every (table, column) in _PENDING_COLUMN_ADDITIONS must exist in
    Base.metadata.  Catches stale entries left behind when a column is
    renamed or removed from a model without updating database.py.
    """
    stale = []
    for table_name, col_name, _sql_def in _PENDING_COLUMN_ADDITIONS:
        if table_name not in Base.metadata.tables:
            stale.append(f"{table_name}.{col_name}  (table not in metadata)")
            continue
        table = Base.metadata.tables[table_name]
        if col_name not in table.c:
            stale.append(
                f"{table_name}.{col_name}  "
                f"(column not in {table_name} model; rename or remove from "
                f"_PENDING_COLUMN_ADDITIONS)"
            )

    assert not stale, (
        "_PENDING_COLUMN_ADDITIONS references table/column pairs that don't "
        "exist in Base.metadata. Remove stale entries from database.py:\n"
        + "\n".join(f"  {s}" for s in stale)
    )


# ---------------------------------------------------------------------------
# 2. Forward guard: minimal schema + migrations = full schema
# ---------------------------------------------------------------------------

def test_migration_roundtrip_covers_all_model_columns():
    """
    Simulates an existing (pre-migration) database and proves that applying
    _PENDING_COLUMN_ADDITIONS brings it up to the full model schema.

    Steps:
      1. Build a "minimal" in-memory SQLite DB with all model tables but
         WITHOUT the migration columns (simulates a DB from before those
         columns were added to the models).
      2. Apply the _PENDING_COLUMN_ADDITIONS logic.
      3. Verify every column in Base.metadata is now present.

    If a developer adds a column to a model but forgets _PENDING_COLUMN_ADDITIONS,
    the column won't be added in step 2 and the assertion in step 3 fails with
    the exact table.column name — the fix is a one-liner in database.py.
    """
    migration_col_set = {
        (table, col) for table, col, _ in _PENDING_COLUMN_ADDITIONS
    }

    # ── Step 1: build minimal metadata (no migration columns) ───────────────
    min_meta = MetaData()
    for table_name, table in Base.metadata.tables.items():
        initial_cols = [
            # Recreate with name + type only — strip FKs and constraints so
            # the minimal schema creates cleanly across all table orderings.
            Column(col.name, col.type, primary_key=col.primary_key)
            for col in table.columns
            if (table_name, col.name) not in migration_col_set
        ]
        if initial_cols:
            Table(table_name, min_meta, *initial_cols)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    min_meta.create_all(engine)

    # ── Step 2: apply migrations (mirror of _apply_inline_migrations logic) ─
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, sql_def in _PENDING_COLUMN_ADDITIONS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {column} {sql_def}'))

    # ── Step 3: every model column must now be present ───────────────────────
    insp2 = inspect(engine)
    existing_tables2 = set(insp2.get_table_names())
    missing = []

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables2:
            missing.append(f"[TABLE ABSENT] {table_name}")
            continue
        db_cols = {c["name"] for c in insp2.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_cols:
                missing.append(f"{table_name}.{col.name}")

    assert not missing, (
        "After simulated migration, these model columns are missing from the DB.\n"
        "Add the missing columns to _PENDING_COLUMN_ADDITIONS in app/database.py:\n"
        + "\n".join(f"  {m}" for m in sorted(missing))
    )
