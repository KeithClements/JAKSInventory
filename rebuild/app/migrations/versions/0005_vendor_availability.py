"""add vendor availability columns (scraper feed → auto hide/deactivate)

Revision ID: 0005_vendor_availability
Revises: 0004_po_volume_discount
Create Date: 2026-06-21

The scraper feed can now report each part's per-vendor supply status
(in_stock / out_of_stock / discontinued). Three columns hold it:

  * product_vendor_sources.availability_status     — the vendor's status (NULL = unknown)
  * product_vendor_sources.availability_updated_at — when the feed last set it
  * products.vendor_availability                   — denormalized worst-case roll-up
    across the product's active sources (cached, like qty_on_hand), so the list
    badge + Shopify bulk-push exclusion are one indexed read, not a join.

Owner-locked policy (full automation): out_of_stock hides the product from the
storefront push; discontinued deactivates it. See ProductImportService.

A fresh/test DB builds these via create_all() and is stamped at HEAD, so this
revision is guarded (idempotent): it only adds a column on an existing
pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_vendor_availability"
down_revision = "0004_po_volume_discount"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("product_vendor_sources", "availability_status"):
        op.add_column(
            "product_vendor_sources",
            sa.Column("availability_status", sa.String(length=20), nullable=True),
        )
    if not _has_column("product_vendor_sources", "availability_updated_at"):
        op.add_column(
            "product_vendor_sources",
            sa.Column("availability_updated_at", sa.DateTime(), nullable=True),
        )
    if not _has_column("products", "vendor_availability"):
        op.add_column(
            "products",
            sa.Column(
                "vendor_availability", sa.String(length=20),
                nullable=False, server_default="",
            ),
        )


def downgrade() -> None:
    if _has_column("products", "vendor_availability"):
        op.drop_column("products", "vendor_availability")
    if _has_column("product_vendor_sources", "availability_updated_at"):
        op.drop_column("product_vendor_sources", "availability_updated_at")
    if _has_column("product_vendor_sources", "availability_status"):
        op.drop_column("product_vendor_sources", "availability_status")
