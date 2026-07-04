"""add shopify_processed_orders (order-sync idempotency ledger)

Revision ID: 0012_shopify_processed_orders
Revises: 0011_cm_allocation_link
Create Date: 2026-07-04

The Shopify order-sync feed decrements ERP stock for each web sale. This table
records every Shopify order that has been processed EXACTLY ONCE (keyed by the
Shopify order id) so a re-poll of the same window can never double-decrement
inventory.

A fresh/test DB builds this via create_all() and is stamped at HEAD, so this
revision is guarded (idempotent): it only creates the table on an existing
pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_shopify_processed_orders"
down_revision = "0011_cm_allocation_link"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return table in insp.get_table_names()


def upgrade() -> None:
    if _has_table("shopify_processed_orders"):
        return
    op.create_table(
        "shopify_processed_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shopify_order_id", sa.String(length=100), nullable=False, unique=True),
        sa.Column("order_name", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("processed_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("order_created_at", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("lines_matched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_unmatched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pieces_decremented", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reversed_at", sa.DateTime(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    if _has_table("shopify_processed_orders"):
        op.drop_table("shopify_processed_orders")
