"""QBO sync-status stamping (P8).

Single place that writes ``qbo_sync_status`` / ``qbo_sync_error`` columns
across the local entities that round-trip to QBO. Migration 105 adds these
columns to:

    invoices, customer_credits, core_returns, purchase_orders,
    vendor_core_returns

Plus the existing per-table id columns (already present pre-105):

    invoices.qbo_invoice_id            (P8: customer invoice)
    customer_credits.qbo_credit_id     (P8: customer core credit memo)
    customer_credits.qbo_refund_receipt_id (P8: cash refund receipt)
    core_returns.qbo_credit_id         (P8: tracking back-link)
    purchase_orders.qbo_bill_id        (P8: vendor bill)
    vendor_core_returns.qbo_vendor_credit_id (P8: vendor credit, mig 105)

This module deliberately stays *additive*. It is wired into the
``on_success`` callbacks of ``qbo.push.push_*_to_qbo`` and into the
exception paths in the UI export screen. It does **not** rewrite any of
the existing id-stamping logic — those continue to work as they did
before P8, and this helper adds the status/error book-keeping alongside.

Status values
~~~~~~~~~~~~~
    "synced"   — last push to QBO succeeded; ``qbo_sync_error`` cleared.
    "failed"   — last push raised or QBO returned an error.
    "pending"  — manual reset; row is queued for the next push.
    "skipped"  — write was suppressed (writes disabled, void, zero-amt).

Callers should treat the legacy ``qbo_exported`` / ``qbo_exported_at``
columns on ``invoices`` and ``purchase_orders`` as the canonical "has
this ever been pushed" flag. ``qbo_sync_status`` is the per-attempt
state — useful for UIs that need to surface retry candidates.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# Tables that have the qbo_sync_status / qbo_sync_error columns
# (added by migration 105). Order does not matter.
_ALLOWED_TABLES = frozenset({
    "invoices",
    "customer_credits",
    "core_returns",
    "purchase_orders",
    "vendor_core_returns",
})


def stamp(
    table: str,
    row_id: int | str | None,
    status: str,
    *,
    error: str | None = None,
    extra_columns: dict[str, Any] | None = None,
) -> bool:
    """Update ``{table}.qbo_sync_status`` (and ``qbo_sync_error``) for ``row_id``.

    Best-effort: never raises. Returns ``True`` on success, ``False`` if
    the table is not allow-listed or the write fails (logged at WARNING).

    ``extra_columns`` lets callers atomically set sibling columns —
    typically the qbo id column on the same row — without issuing a
    second UPDATE. Example::

        stamp("invoices", inv_id, "synced",
              extra_columns={"qbo_invoice_id": qbo_id})

    The function intentionally does *not* know about per-table id column
    names; callers pass them via ``extra_columns`` so this module stays
    free of table-specific knowledge.
    """
    if table not in _ALLOWED_TABLES:
        log.debug("sync_status.stamp: table %r not allow-listed", table)
        return False
    if row_id is None:
        return False

    try:
        from db import get_db
    except Exception as exc:
        log.warning("sync_status.stamp: cannot import db.get_db: %s", exc)
        return False

    cols = ["qbo_sync_status = %s", "qbo_sync_error = %s"]
    vals: list[Any] = [str(status)[:32], (error or None) and str(error)[:1000]]

    if extra_columns:
        for k, v in extra_columns.items():
            # Defensive: only allow simple identifier-shaped column names.
            if not (isinstance(k, str) and k.replace("_", "").isalnum()):
                continue
            cols.append(f"{k} = %s")
            vals.append(v)

    sql = f"UPDATE {table} SET {', '.join(cols)} WHERE id = %s"
    vals.append(int(row_id) if str(row_id).isdigit() else row_id)

    try:
        with get_db() as conn:
            conn.execute(sql, tuple(vals))
            conn.commit()
        return True
    except Exception as exc:
        log.warning("sync_status.stamp(%s id=%s) failed: %s", table, row_id, exc)
        return False


def mark_synced(table: str, row_id: int | str | None,
                *, qbo_id: str | None = None, id_column: str | None = None) -> bool:
    """Convenience: stamp ``synced`` + clear error + set ``{id_column}=qbo_id``."""
    extra: dict[str, Any] = {}
    if qbo_id and id_column:
        extra[id_column] = qbo_id
    return stamp(table, row_id, "synced", error=None, extra_columns=extra or None)


def mark_failed(table: str, row_id: int | str | None, error: str) -> bool:
    """Convenience: stamp ``failed`` + record the error message."""
    return stamp(table, row_id, "failed", error=error)
