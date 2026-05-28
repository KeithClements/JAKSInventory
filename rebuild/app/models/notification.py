"""
app/models/notification.py
===========================
R8 — In-app + future email notifications.

NotificationService.notify() creates rows here. The dashboard surfaces
unacknowledged warnings/errors; critical events also fire email (Phase 2).

Implementation lands in Phase K. Phase A only adds the schema.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.constants import NotificationSeverity, NotificationType


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Unacknowledged-by-user query is the dashboard hot path
        Index("ix_notifications_user_ack", "user_id", "acknowledged_at"),
        Index("ix_notifications_severity_ack", "severity", "acknowledged_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL user_id = system-wide notification (visible to all admins)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NotificationSeverity.INFO
    )
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Polymorphic link to the entity that triggered the notification
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional URL/route the user can click to investigate
    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
