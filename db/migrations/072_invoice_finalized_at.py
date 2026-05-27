"""Migration 072: explicit ``finalized_at`` timestamp on invoices.

Background
----------
Until now the only way to tell whether an invoice has been "finalized"
(inventory deducted, cores created, serials marked sold) was to inspect
``invoices.status`` — but ``status`` also changes when a payment lands
("paid" / "partial"). That coupling has two consequences:

1. ``record_payment`` immediately flips ``status`` away from ``draft``,
   so the user can never finalize the invoice afterwards (the
   ``status == 'draft'`` guard in :func:`finalize_invoice` rejects them).
   Cores never get created, inventory is never deducted.
2. The Save & Send / Finalize button hides itself the moment status
   ≠ ``draft`` — which is exactly when the user discovers the omission.

Adding a dedicated ``finalized_at`` column lets us separate the two
concerns cleanly: payment status lives on ``status``, finalization lives
on ``finalized_at``.

Backfill rule
-------------
Any invoice whose status is *not* ``draft`` is assumed to have been
finalized previously, so we stamp ``finalized_at`` from the row's
``updated_at`` (falling back to ``created_at``).
"""
from __future__ import annotations


def _has_column(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            name = r[1] if not hasattr(r, "keys") else r["name"]
            if str(name) == column:
                return True
        return False
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def migrate(conn) -> None:
    if not _has_column(conn, "invoices", "finalized_at"):
        try:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN finalized_at TIMESTAMP"
            )
        except Exception:
            # PostgreSQL may need a slightly different syntax; try plain.
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMP"
            )

    # Backfill: anything that's no longer a draft must have been finalized
    # at some point — best-effort timestamp from updated_at / created_at.
    try:
        conn.execute(
            """
            UPDATE invoices
               SET finalized_at = COALESCE(updated_at, created_at)
             WHERE finalized_at IS NULL
               AND status IS NOT NULL
               AND LOWER(status) <> 'draft'
            """
        )
    except Exception:
        pass
    conn.commit()
