"""credit-memo reversal integrity — credit_memo_allocations.linked_payment_allocation_id

Revision ID: 0011_cm_allocation_link
Revises: 0010_qbo_surcharge_receipt_id
Create Date: 2026-07-02

Applying a credit memo to an invoice creates a synthetic ACCOUNT_CREDIT
PaymentAllocation; reverse_credit_memo previously re-found that allocation via
a LIKE-on-notes heuristic (fragile: a note edit or duplicated wording silently
orphans the allocation). This column links the CM allocation to its payment
allocation at apply time so the reversal joins on the id directly.

NULL for rows created before the link existed — the reversal falls back to the
legacy heuristic for those and raises loudly when no match is found.

Additive only; guarded (idempotent) like every revision in this chain.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_cm_allocation_link"
down_revision = "0010_qbo_surcharge_receipt_id"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("credit_memo_allocations", "linked_payment_allocation_id"):
        op.add_column(
            "credit_memo_allocations",
            sa.Column(
                "linked_payment_allocation_id", sa.Integer(),
                sa.ForeignKey("payment_allocations.id"), nullable=True,
            ),
        )


def downgrade() -> None:
    if _has_column("credit_memo_allocations", "linked_payment_allocation_id"):
        op.drop_column("credit_memo_allocations", "linked_payment_allocation_id")
