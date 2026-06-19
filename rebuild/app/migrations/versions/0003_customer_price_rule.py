"""add customer_price_rule

Revision ID: 0003_customer_price_rule
Revises: 0002_vendor_private_label
Create Date: 2026-06-18

Customer-Specific Product Pricing (CUSTOMER_PRICING_DESIGN.md). One new table
holds per-customer cost-plus pricing rules (scope = SKU / category / brand /
whole-customer). Resolved as "Step 0" of the line-pricing waterfall; when no
rule matches the existing behaviour is unchanged.

A fresh/test DB builds this table via create_all() and is stamped at HEAD, so
this revision is guarded (idempotent): it only creates the table on an existing
pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_customer_price_rule"
down_revision = "0002_vendor_private_label"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    # Idempotent: a fresh DB stamped at HEAD already has the table from
    # create_all(); only an existing pre-revision DB needs the CREATE.
    if _has_table("customer_price_rule"):
        return

    op.create_table(
        "customer_price_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id", sa.Integer(),
            sa.ForeignKey("customers.id"), nullable=False,
        ),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_ref", sa.String(length=200), nullable=True),
        sa.Column("price_method", sa.String(length=20), nullable=False),
        sa.Column("price_value", sa.Float(), nullable=False),
        sa.Column("qty_min", sa.Float(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_customer_price_rule_customer_id",
        "customer_price_rule", ["customer_id"],
    )
    op.create_index(
        "ix_customer_price_rule_customer_scope",
        "customer_price_rule", ["customer_id", "scope_type", "scope_ref"],
    )


def downgrade() -> None:
    if _has_table("customer_price_rule"):
        op.drop_index(
            "ix_customer_price_rule_customer_scope",
            table_name="customer_price_rule",
        )
        op.drop_index(
            "ix_customer_price_rule_customer_id",
            table_name="customer_price_rule",
        )
        op.drop_table("customer_price_rule")
