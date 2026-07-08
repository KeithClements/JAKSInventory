"""po_receipts reversal tracking — reverse a mis-receive

Revision ID: 0013_po_receipt_reversal
Revises: 0012_shopify_processed_orders
Create Date: 2026-07-07

Receipts were previously immutable — no way to correct a mis-receive short of
a manual inventory adjustment plus a manual cost correction outside the app.
POService.reverse_receipt() undoes qty_on_hand, qty_on_order, and (when a
prior moving-average snapshot exists on ProductCostHistory) product.cost, then
marks the whole receipt reversed via these three columns. Whole-receipt,
atomic, idempotent — a receipt can only be reversed once.

Additive only. A fresh/test DB builds this via create_all() and is stamped at
HEAD, so this revision is guarded (idempotent): it only adds the columns on an
existing pre-revision file DB that lacks them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_po_receipt_reversal"
down_revision = "0012_shopify_processed_orders"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("po_receipts", "reversed_at"):
        op.add_column("po_receipts", sa.Column("reversed_at", sa.DateTime(), nullable=True))
    if not _has_column("po_receipts", "reversed_by_id"):
        op.add_column("po_receipts", sa.Column("reversed_by_id", sa.Integer(), nullable=True))
    if not _has_column("po_receipts", "reversal_reason"):
        op.add_column("po_receipts", sa.Column("reversal_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("po_receipts", "reversal_reason"):
        op.drop_column("po_receipts", "reversal_reason")
    if _has_column("po_receipts", "reversed_by_id"):
        op.drop_column("po_receipts", "reversed_by_id")
    if _has_column("po_receipts", "reversed_at"):
        op.drop_column("po_receipts", "reversed_at")
