"""Migration 105: Explicit QBO sync-status fields (P8).

Surfaces the per-row sync state for every document type that round-trips
to QuickBooks Online so the UI and reconciliation jobs can distinguish
"never synced", "synced", "failed", and "retry" without inspecting the
qbo_sync_log audit trail.

Adds:
    invoices.qbo_sync_status            TEXT  ('pending'|'synced'|'failed'|'skipped'|NULL)
    invoices.qbo_sync_error             TEXT
    customer_credits.qbo_sync_status    TEXT
    customer_credits.qbo_sync_error     TEXT
    core_returns.qbo_sync_status        TEXT
    core_returns.qbo_sync_error         TEXT
    purchase_orders.qbo_sync_status     TEXT
    purchase_orders.qbo_sync_error      TEXT
    vendor_core_returns.qbo_vendor_credit_id     TEXT
    vendor_core_returns.qbo_last_synced_at       TEXT
    vendor_core_returns.qbo_sync_status          TEXT
    vendor_core_returns.qbo_sync_error           TEXT

Existing id columns are preserved (qbo_invoice_id, qbo_bill_id,
qbo_refund_receipt_id, qbo_credit_id) so this migration is purely
additive. All columns are nullable and default NULL/empty so the
migration is safe on populated databases.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


_TARGETS = [
    ("invoices",            "qbo_sync_status",       "TEXT"),
    ("invoices",            "qbo_sync_error",        "TEXT"),
    ("customer_credits",    "qbo_sync_status",       "TEXT"),
    ("customer_credits",    "qbo_sync_error",        "TEXT"),
    ("core_returns",        "qbo_sync_status",       "TEXT"),
    ("core_returns",        "qbo_sync_error",        "TEXT"),
    ("purchase_orders",     "qbo_sync_status",       "TEXT"),
    ("purchase_orders",     "qbo_sync_error",        "TEXT"),
    ("vendor_core_returns", "qbo_vendor_credit_id",  "TEXT"),
    ("vendor_core_returns", "qbo_last_synced_at",    "TEXT"),
    ("vendor_core_returns", "qbo_sync_status",       "TEXT"),
    ("vendor_core_returns", "qbo_sync_error",        "TEXT"),
]


def _columns(conn, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def migrate(conn) -> None:
    added = 0
    for table, col, decl in _TARGETS:
        existing = _columns(conn, table)
        if not existing:
            log.info("105: table %s does not exist, skipping %s", table, col)
            continue
        if col in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            added += 1
            log.info("105: added %s.%s", table, col)
        except Exception as e:
            log.warning("105: failed to add %s.%s: %s", table, col, e)
    conn.commit()
    log.info("Migration 105 complete: %d QBO sync-status columns added", added)
