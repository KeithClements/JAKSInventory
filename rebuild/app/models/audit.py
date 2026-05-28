from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import AuditAction, EntityType


class AuditLog(Base):
    """
    Financial and operational change log.
    Written by AuditService — never written directly by routers or templates.

    Scope: major transaction changes, not every field in the app.
    Priority records: invoices, payments, credits, returns, POs, inventory, QBO sync, deletes/voids.

    old_value / new_value: JSON-serialized snapshots of changed fields.
    """
    __tablename__ = "audit_log"
    __table_args__ = (
        # "show history for this record" — the primary query pattern
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        # date-range queries for the audit report
        Index("ix_audit_log_changed_at", "changed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )  # null = system event

    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)  # EntityType
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)        # AuditAction

    # R6 — per-field change tracking. When NULL, old/new are whole-record JSON snapshots.
    # When set, old_value/new_value hold the specific field's values.
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON or scalar
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON or scalar
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
