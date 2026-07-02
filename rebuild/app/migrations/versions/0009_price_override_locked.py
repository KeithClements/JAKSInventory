"""operator price lock — products.price_override_locked (scraper-audit bug #11)

Revision ID: 0009_price_override_locked
Revises: 0008_vendor_uniqueness
Create Date: 2026-07-01

The scraper's nightly push (scripts/import_and_push.py → ProductImportService.
pricing_update_sell) writes product.price_override — the SAME field an ERP
operator edits by hand on the product screen, so any hand-set price was
silently clobbered on the next nightly run.

  * products.price_override_locked — set automatically when an operator CHANGES
    price_override via the UI to a non-empty value; cleared when they clear the
    override (or via the explicit "unlock" control on the product screen, which
    keeps the price but lets the feed manage it again). pricing_update_sell
    skips the sell-price write for locked products (cost / availability /
    manufacturer on the same row still apply) and counts them in
    skipped_price_locked.

Additive only. A fresh/test DB builds this via create_all() and is stamped at
HEAD, so this revision is guarded (idempotent): it only adds the column on an
existing pre-revision file DB that lacks it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_price_override_locked"
down_revision = "0008_vendor_uniqueness"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh DB — create_all() handled it; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("products", "price_override_locked"):
        op.add_column(
            "products",
            sa.Column(
                "price_override_locked", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    if _has_column("products", "price_override_locked"):
        op.drop_column("products", "price_override_locked")
