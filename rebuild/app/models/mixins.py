"""
app/models/mixins.py
====================
SQLAlchemy 2.0 declarative mixins for shared column sets.

Mixins do NOT include the entity-specific QBO ID field (that name varies
per table: qbo_invoice_id, qbo_payment_id, qbo_id, etc.).  Each model
declares its own ID field and inherits the four consistent tracking fields
from QBOSyncMixin.

Usage:
    class Invoice(QBOSyncMixin, Base):
        __tablename__ = "invoices"
        qbo_invoice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
        # qbo_sync_status, qbo_last_synced_at, qbo_sync_error,
        # qbo_sync_retry_count come from QBOSyncMixin
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import QBOSyncStatus


class QBOSyncMixin:
    """
    Four-field QBO sync tracking block — included on every entity
    that is pushed to QuickBooks Online.

    Sync lifecycle:
        pending  — created, not yet pushed
        synced   — successfully pushed; qbo_last_synced_at is set
        error    — last push failed; qbo_sync_error contains the message
        skipped  — intentionally excluded from sync (e.g. void before first sync)

    qbo_sync_retry_count: incremented on each failed attempt; reset on success.
    """

    qbo_sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QBOSyncStatus.PENDING
    )
    qbo_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    qbo_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    qbo_sync_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
