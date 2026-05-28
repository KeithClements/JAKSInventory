"""
app/models/communication.py
============================
R12 — Communication architecture (immutable audit log).

Every outbound/inbound message — email, SMS, phone-call note, or manually
typed copy/paste — is recorded here. In Phase 1, NullMessagingProvider only
logs (status=logged_only); Phase 2 swaps in real SMTP/Twilio providers and
the same records carry delivery callbacks.

This table is APPEND-ONLY. No updated_at, no delete. Edits never happen.

Implementation lands in Phase L. Phase A only adds the schema.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import CommunicationChannel, CommunicationDirection, CommunicationStatus


class Communication(Base):
    __tablename__ = "communication_log"
    __table_args__ = (
        Index("ix_comm_customer_id", "customer_id"),
        Index("ix_comm_related_entity", "related_entity_type", "related_entity_id"),
        Index("ix_comm_sent_at", "sent_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # ── Parties ───────────────────────────────────────────────────────────────
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendors.id"), nullable=True
    )

    # ── Channel + Direction ───────────────────────────────────────────────────
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CommunicationDirection.OUTBOUND
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CommunicationStatus.LOGGED_ONLY
    )
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Provider's message ID for delivery callbacks (Twilio SID, SMTP id, etc.)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # ── Content ───────────────────────────────────────────────────────────────
    to_address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    from_address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    template_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Linking (polymorphic to any entity the message was about) ─────────────
    related_entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Audit ─────────────────────────────────────────────────────────────────
    sent_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Compliance (R12) ──────────────────────────────────────────────────────
    consent_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opt_out_check_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    attachments: Mapped[list[CommunicationAttachment]] = relationship(
        "CommunicationAttachment",
        back_populates="communication",
        cascade="all, delete-orphan",
    )


class CommunicationAttachment(Base):
    """R12 — file attachments. Phase 2 use, schema ready now."""
    __tablename__ = "communication_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    communication_id: Mapped[int] = mapped_column(
        ForeignKey("communication_log.id"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    communication: Mapped[Communication] = relationship(
        "Communication", back_populates="attachments"
    )
