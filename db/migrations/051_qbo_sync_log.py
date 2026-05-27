"""Migration 051: qbo_sync_log table for idempotency + retry tracking (QBO.11).

Every QBO write (item/bill/invoice/customer/payment) is keyed by a
stable idempotency key so that retries after a network failure cannot
create duplicate QBO records.

Columns:
    entity_type   -- 'item' | 'bill' | 'invoice' | 'customer' | 'payment'
    local_ref     -- our primary key or natural key (e.g. 'INV-00123')
    idempotency_key -- sha256 of (entity_type, local_ref, version_tag)
    qbo_id        -- returned from QBO on success; NULL if pending/failed
    status        -- 'pending' | 'success' | 'failed' | 'skipped'
    attempts      -- count of write attempts
    last_error    -- last exception message (truncated)
    created_at    -- first attempt
    updated_at    -- latest attempt / completion time
"""


def migrate(conn):
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    if "qbo_sync_log" not in tables:
        conn.execute(
            """
            CREATE TABLE qbo_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                local_ref TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                qbo_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qbo_sync_log_entity "
            "ON qbo_sync_log(entity_type, local_ref)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qbo_sync_log_status "
            "ON qbo_sync_log(status)"
        )
