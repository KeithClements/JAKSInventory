"""baseline — Alembic adoption marker

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-17

This is the version marker for the schema as it stood when Alembic was adopted.
It is intentionally a NO-OP:

  * Fresh databases get the full current schema from
    ``Base.metadata.create_all()`` and are stamped at HEAD (this revision when
    adopted), so this upgrade never runs on them.
  * Existing pre-Alembic databases were brought up to this same schema by the
    legacy inline-migration list in app/database.py; they are stamped at this
    baseline so any FUTURE incremental revisions run against them.

All schema changes from here on are real incremental revisions stacked on top of
this baseline (e.g. ``alembic revision -m "add X" --autogenerate``).
"""
from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: schema at adoption is built by create_all (fresh) or the legacy
    # inline-migration list (existing). This revision is only a version marker.
    pass


def downgrade() -> None:
    # The baseline cannot be meaningfully un-applied (it represents the whole
    # adopted schema). Downgrading below baseline is not supported.
    pass
