"""add vendors.private_label

Revision ID: 0002_vendor_private_label
Revises: 0001_baseline
Create Date: 2026-06-17

Private-label vendors (e.g. DFT, Migao): their products are sold as our house
brand and the customer-facing SKU hides the vendor code, using the house prefix
instead ({prefix}-{prefix}-{part#}). This adds the per-vendor flag that drives it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_vendor_private_label"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    # Idempotent: a fresh DB stamped at HEAD already has the column from
    # create_all(); only an existing pre-revision DB needs the ALTER.
    if not _has_column("vendors", "private_label"):
        op.add_column(
            "vendors",
            sa.Column("private_label", sa.Boolean(), nullable=False,
                      server_default=sa.text("0")),
        )


def downgrade() -> None:
    if _has_column("vendors", "private_label"):
        op.drop_column("vendors", "private_label")
