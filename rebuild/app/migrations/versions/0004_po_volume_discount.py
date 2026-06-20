"""add PO vendor volume discount columns

Revision ID: 0004_po_volume_discount
Revises: 0003_customer_price_rule
Create Date: 2026-06-19

Vendor volume discount (e.g. PAI: 5% off every part when a PO exceeds $5,000).
The rule itself reuses the existing ``vendor_programs`` table (threshold_amount +
discount_percent), so no new table/column there. Two columns record the discount
on the PO it was applied to:

  * purchase_orders.volume_discount_pct — the % actually applied (0 = none),
    snapshotted at apply time so a later rule change never rewrites old POs.
  * po_lines.list_unit_cost — each line's pre-discount price, so the discount is
    reversible and the struck list price can be shown. NULL = no discount.

A fresh/test DB builds these via create_all() and is stamped at HEAD, so this
revision is guarded (idempotent): it only adds a column on an existing
pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_po_volume_discount"
down_revision = "0003_customer_price_rule"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("purchase_orders", "volume_discount_pct"):
        op.add_column(
            "purchase_orders",
            sa.Column(
                "volume_discount_pct", sa.Float(),
                nullable=False, server_default="0",
            ),
        )
    if not _has_column("po_lines", "list_unit_cost"):
        op.add_column(
            "po_lines",
            sa.Column("list_unit_cost", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("po_lines", "list_unit_cost"):
        op.drop_column("po_lines", "list_unit_cost")
    if _has_column("purchase_orders", "volume_discount_pct"):
        op.drop_column("purchase_orders", "volume_discount_pct")
