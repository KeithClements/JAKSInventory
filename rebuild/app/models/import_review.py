"""Smart Import / Review Queue staging models.

The scraper (or any CSV/Excel feed) NEVER writes the catalog directly. Each feed
row is analyzed and staged here as an ImportCandidate, tagged against the seven
review questions (new? duplicate? cross-ref match? new image? price change? bad
category? needs review?). A human approves/rejects in the Review Queue; only then
does the ERP create/update the product — and, separately, publish to Shopify/eBay.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (String, Text, Integer, Boolean, Float, ForeignKey, DateTime,
                        Index, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.constants import ImportBatchStatus, ImportDisposition, ScrapedItemReviewStatus


class ImportBatch(Base):
    """One ingested feed (a scraper export / uploaded file). Groups its candidates."""
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_app: Mapped[str] = mapped_column(String(50), nullable=False, default="")   # e.g. "pai-info"
    filename: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ImportBatchStatus.STAGED
    )

    # ── Tallies (filled by the analyzer) ──────────────────────────────────────
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    update_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cross_ref_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    candidates: Mapped[list["ImportCandidate"]] = relationship(
        "ImportCandidate", back_populates="batch", cascade="all, delete-orphan"
    )


class ImportCandidate(Base):
    """One staged product row awaiting human review (raw_json is authoritative for apply)."""
    __tablename__ = "import_candidates"
    __table_args__ = (
        Index("ix_import_candidates_batch_id", "batch_id"),
        Index("ix_import_candidates_review_status", "review_status"),
        Index("ix_import_candidates_sku", "sku"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)

    # ── Source row ────────────────────────────────────────────────────────────
    sku: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    category_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Analysis — the seven questions ────────────────────────────────────────
    disposition: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ImportDisposition.NEW
    )                                                          # new / update / cross_ref / duplicate
    matched_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )                                                          # the product it updates/matches
    incoming_image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_new_images: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    old_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_categories.id"), nullable=True
    )
    category_issue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    engine_manufacturer: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    engine_model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flags: Mapped[str] = mapped_column(String(300), nullable=False, default="")  # human-readable, comma-sep

    # ── Review ────────────────────────────────────────────────────────────────
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScrapedItemReviewStatus.PENDING
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Apply outcome ─────────────────────────────────────────────────────────
    applied_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    batch: Mapped[ImportBatch] = relationship("ImportBatch", back_populates="candidates")
