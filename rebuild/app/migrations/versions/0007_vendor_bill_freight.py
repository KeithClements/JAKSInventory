"""add vendor_bills.freight_amount (vendor-billed freight on the bill)

Revision ID: 0007_vendor_bill_freight
Revises: 0006_shopify_hidden_by_erp
Create Date: 2026-06-28

The vendor bill total was summed purely from the parts lines, so a vendor (e.g.
PAI) that charges freight on the SAME invoice as the parts left the recorded
payable understating what we actually owe by the freight amount. freight_amount
holds that vendor-billed freight; it is PART of total_amount (so the bill matches
the vendor's invoice) and posts to QBO as its own freight-in expense line. It
defaults from the PO's freight_in_cost at bill creation (net of freight already
billed on prior bills of that PO) and is editable on correction.

A fresh/test DB builds this via create_all() and is stamped at HEAD, so this
revision is guarded (idempotent): it only adds the column on an existing
pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_vendor_bill_freight"
down_revision = "0006_shopify_hidden_by_erp"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("vendor_bills", "freight_amount"):
        op.add_column(
            "vendor_bills",
            sa.Column(
                "freight_amount", sa.Float(),
                nullable=False, server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    if _has_column("vendor_bills", "freight_amount"):
        op.drop_column("vendor_bills", "freight_amount")
