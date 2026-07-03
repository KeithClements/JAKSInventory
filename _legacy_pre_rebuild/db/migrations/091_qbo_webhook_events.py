"""Migration 091: ``qbo_webhook_events`` table for inbound QBO notifications.

QBO calls our webhook endpoint when entities change in the realm
(Customer, Invoice, Item, Payment, etc.). We persist every payload
verbatim so workers can replay / reconcile asynchronously without
losing notifications if processing fails.

Schema mirrors the QBO event-notification envelope:
- ``realm_id``        — QBO company id
- ``entity_type``     — Customer / Invoice / Item / ...
- ``entity_id``       — QBO Id of the changed object
- ``operation``       — Create / Update / Delete / Merge / Void / Emailed
- ``last_updated``    — QBO event timestamp (ISO 8601)
- ``payload_json``    — full notification body (one event slice)
- ``signature``       — raw ``intuit-signature`` header for audit
- ``received_at``     — when we accepted the POST
- ``processed_at``    — when a worker successfully handled it (NULL = pending)
- ``error``           — last processing error (NULL on success)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def migrate(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS qbo_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            realm_id TEXT,
            entity_type TEXT,
            entity_id TEXT,
            operation TEXT,
            last_updated TEXT,
            payload_json TEXT,
            signature TEXT,
            received_at TEXT DEFAULT (datetime('now')),
            processed_at TEXT,
            error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_qbo_webhook_events_pending "
        "ON qbo_webhook_events(processed_at) WHERE processed_at IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_qbo_webhook_events_entity "
        "ON qbo_webhook_events(entity_type, entity_id)"
    )
    conn.commit()
    logger.info("Migration 091 complete: qbo_webhook_events")
