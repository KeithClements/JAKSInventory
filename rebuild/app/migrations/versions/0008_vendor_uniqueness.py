"""vendor name + code uniqueness backstops (Risk #2)

Revision ID: 0008_vendor_uniqueness
Revises: 0007_vendor_bill_freight
Create Date: 2026-06-29

Until now nothing stopped two vendors from being created with the same name or
the same vendor_code. A duplicate vendor_code silently collides the internal
vendor_sku scheme (JAKS-[CODE]-[PART#]); a duplicate active name fragments PO /
bill / source history across two records that should be one. The router now
probes for duplicates on both create paths, and these unique indexes are the
DB-level backstop behind that check.

  * uq_vendors_name_active   — partial unique on name WHERE is_active = 1
                               (deactivated vendors' names may be reused)
  * uq_vendors_vendor_code   — partial unique on vendor_code WHERE vendor_code != ''
                               (the '' default repeats freely)

A live DB may already contain duplicates. Creating a unique index on dup data
raises, so each index is created defensively: probe for duplicate key-groups
first and SKIP (don't brick the upgrade) if any exist — the owner dedups, then a
later run creates it. A fresh/test DB builds these via create_all() (mirrored in
Vendor.__table_args__) and is stamped at HEAD, so this revision is idempotent
(CREATE ... IF NOT EXISTS).

Mirrored in app/database.py _PENDING_UNIQUE_INDEXES (revision 0008) so a swallowed
Alembic run still applies them to a pre-existing live DB.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0008_vendor_uniqueness"
down_revision = "0007_vendor_bill_freight"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# (index_name, duplicate_probe_sql, create_sql, drop_name)
_INDEXES = [
    (
        "uq_vendors_name_active",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM vendors WHERE is_active = 1 "
        "GROUP BY name HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendors_name_active "
        "ON vendors (name) WHERE is_active = 1",
    ),
    (
        "uq_vendors_vendor_code",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM vendors WHERE vendor_code != '' "
        "GROUP BY vendor_code HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendors_vendor_code "
        "ON vendors (vendor_code) WHERE vendor_code != ''",
    ),
]


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "vendors"):
        return  # fresh DB — create_all() handled it
    for idx_name, probe_sql, create_sql in _INDEXES:
        try:
            dup_groups = bind.execute(sa.text(probe_sql)).scalar() or 0
            if dup_groups:
                log.warning(
                    "Unique index %s SKIPPED — vendors has %d duplicate "
                    "key-group(s); dedup needed before the backstop can be created",
                    idx_name, dup_groups,
                )
                continue
            bind.execute(sa.text(create_sql))
        except Exception as exc:  # noqa: BLE001 — a backstop must never brick the upgrade
            log.warning("Unique index %s NOT created (%s)", idx_name, exc)


def downgrade() -> None:
    for idx_name, _probe_sql, _create_sql in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {idx_name}")
