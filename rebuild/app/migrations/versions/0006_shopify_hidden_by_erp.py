"""add products.shopify_hidden_by_erp (availability → live storefront reconcile)

Revision ID: 0006_shopify_hidden_by_erp
Revises: 0005_vendor_availability
Create Date: 2026-06-24

The vendor-availability import flags a part out_of_stock/discontinued, but
flagging alone never pulled a listing that was ALREADY LIVE on Shopify down — it
only stopped the recurring sync from refreshing it. ShopifyService.reconcile_
availability now flips an already-ACTIVE listing for an unavailable part to DRAFT
(hidden), and re-lists it when the part is back in stock.

This boolean records that the ERP itself hid the listing, so the auto-re-list
direction is SAFE: only a listing we auto-hid is auto-re-listed — a listing a
human drafted for any other reason is never auto-resurrected.

A fresh/test DB builds this via create_all() and is stamped at HEAD, so this
revision is guarded (idempotent): it only adds the column on an existing
pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_shopify_hidden_by_erp"
down_revision = "0005_vendor_availability"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("products", "shopify_hidden_by_erp"):
        op.add_column(
            "products",
            sa.Column(
                "shopify_hidden_by_erp", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    if _has_column("products", "shopify_hidden_by_erp"):
        op.drop_column("products", "shopify_hidden_by_erp")
