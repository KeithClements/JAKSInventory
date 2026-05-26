from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.constants import EntityType, DocumentType


class DocumentAttachment(Base):
    """
    Optional file attachments for any entity in the system.
    Used for: vendor packing slips, signed RAs, warranty photos, tax certs, etc.

    entity_type + entity_id form a polymorphic reference (no FK constraint —
    the entity_type string identifies which table to look in).

    IMPORTANT: Core workflow documents (Customer Core Return Slip,
    Vendor Core Return Sheet) are GENERATED documents with their own
    print routes — they are NOT stored here as file attachments.
    """
    __tablename__ = "document_attachments"
    __table_args__ = (
        # "load all attachments for this record" — the only query pattern used
        Index("ix_doc_attachments_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # ── Polymorphic reference to any entity ───────────────────────────────────
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)  # EntityType
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── File Info ─────────────────────────────────────────────────────────────
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)   # bytes
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Classification ────────────────────────────────────────────────────────
    # Freeform label: 'packing_slip' | 'signed_ra' | 'photo' | 'tax_cert' | 'other'
    document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Provenance ────────────────────────────────────────────────────────────
    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
