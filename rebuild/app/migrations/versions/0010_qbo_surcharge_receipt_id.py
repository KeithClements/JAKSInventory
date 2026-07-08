"""surcharge-receipt idempotency — payments.qbo_surcharge_receipt_id

Revision ID: 0010_qbo_surcharge_receipt_id
Revises: 0009_price_override_locked
Create Date: 2026-07-02

A surcharged card payment pushed to QBO also books a one-line SalesReceipt
(DocNumber JAKS-SC-{payment_id}) so the QBO deposit matches the bank. The
receipt id was previously recovered only by querying QBO for the DocNumber —
a query-then-create window where two near-simultaneous pushes could both see
"no receipt" and double-book the surcharge income. Persisting the id locally
closes the window: push checks this column before ever hitting the API.

Additive only. A fresh/test DB builds this via create_all() and is stamped at
HEAD, so this revision is guarded (idempotent): it only adds the column on an
existing pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_qbo_surcharge_receipt_id"
down_revision = "0009_price_override_locked"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("payments", "qbo_surcharge_receipt_id"):
        op.add_column(
            "payments",
            sa.Column("qbo_surcharge_receipt_id", sa.String(100), nullable=True),
        )


def downgrade() -> None:
    if _has_column("payments", "qbo_surcharge_receipt_id"):
        op.drop_column("payments", "qbo_surcharge_receipt_id")
