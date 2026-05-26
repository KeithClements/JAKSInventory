from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import (
    ScraperSourceType, ScrapeSearchType, ScrapeRunStatus,
    ScrapedItemReviewStatus, CrossRefType,
)


class ScraperSource(Base):
    """
    Registry of every data source the scraper can query.
    Seeded on startup with PAI, HHP, ATL (is_active=False until scrapers are implemented).

    Vendor sites:     PAI, HHP, ATL  — Phase 1 (cost + availability + cross-refs)
    Competitor sites: FleetPride, etc. — Phase 2/3
    Marketplace:      eBay, Amazon    — Phase 3

    When is_active=False, VendorAvailabilityService returns "check manually" for that source.
    When is_active=True, real scraper code is dispatched.
    """
    __tablename__ = "scraper_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScraperSourceType.VENDOR
    )
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_login: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    runs: Mapped[list[ScrapeRun]] = relationship("ScrapeRun", back_populates="source")
    field_mappings: Mapped[list[ScraperFieldMapping]] = relationship(
        "ScraperFieldMapping", back_populates="source", cascade="all, delete-orphan"
    )


class ScrapeRun(Base):
    """
    One row per enrichment or availability lookup attempt.
    Provides an audit trail: what was searched, when, what came back.
    Useful for debugging when scraper results look wrong.
    """
    __tablename__ = "scrape_runs"
    __table_args__ = (
        Index("ix_scrape_runs_source_id_status", "source_id", "status"),
        Index("ix_scrape_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("scraper_sources.id"), nullable=False)
    search_term: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    search_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScrapeSearchType.VENDOR_PART
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScrapeRunStatus.RUNNING
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped[ScraperSource] = relationship("ScraperSource", back_populates="runs")
    items: Mapped[list[ScrapedItem]] = relationship(
        "ScrapedItem", back_populates="run", cascade="all, delete-orphan"
    )


class ScrapedItem(Base):
    """
    Raw result from a scrape run — one row per part found.
    Sits in review_status='pending' until Keith accepts or rejects it.

    On accept:
      - If matched_product_id set: updates ProductVendorSource.vendor_cost,
        ProductImage, and triggers cross-ref prompt via ProductService.
      - If no match: user can create a new product from this data.

    confidence_score (0.0–1.0): how closely this result matches the search term.
    Phase 2: auto-accept when confidence_score >= threshold for trusted sources.
    Phase 1: everything goes through manual review.
    """
    __tablename__ = "scraped_items"
    __table_args__ = (
        Index("ix_scraped_items_run_id", "scrape_run_id"),
        Index("ix_scraped_items_review_status", "review_status"),
        Index("ix_scraped_items_matched_product_id", "matched_product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scrape_run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("scraper_sources.id"), nullable=False)

    # Nullable — scraper may find data for parts JAKS doesn't stock yet.
    # The review workflow handles creating new products from unmatched scraped items.
    matched_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )

    # ── Raw scraped fields ────────────────────────────────────────────────────
    vendor_part_number: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    oem_number: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # Nullable — vendor may not return pricing
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    retail_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    availability_text: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty_available: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    product_url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")

    # Full raw response stored for debugging / re-parsing without re-scraping
    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # ── Review workflow ───────────────────────────────────────────────────────
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScrapedItemReviewStatus.PENDING
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped[ScrapeRun] = relationship("ScrapeRun", back_populates="items")
    cross_refs: Mapped[list[ScrapedCrossRef]] = relationship(
        "ScrapedCrossRef", back_populates="item", cascade="all, delete-orphan"
    )


class ScrapedCrossRef(Base):
    """
    Cross-references found during a scrape run — e.g. OEM numbers listed on a vendor page.
    Sits pending review. On accept: creates CrossReference on the matched product with
    status from CrossRefStatus (found → proven through sales history).

    ref_number indexed for fast lookup: user may search by OEM# on quote screen and
    find it in pending cross-refs before it has been accepted to the product record.
    """
    __tablename__ = "scraped_cross_refs"
    __table_args__ = (
        Index("ix_scraped_cross_refs_item_id", "scraped_item_id"),
        Index("ix_scraped_cross_refs_ref_number", "ref_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scraped_item_id: Mapped[int] = mapped_column(
        ForeignKey("scraped_items.id"), nullable=False
    )
    ref_number: Mapped[str] = mapped_column(String(200), nullable=False)
    ref_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CrossRefType.OEM
    )
    brand: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScrapedItemReviewStatus.PENDING
    )

    item: Mapped[ScrapedItem] = relationship("ScrapedItem", back_populates="cross_refs")


class ScraperFieldMapping(Base):
    """
    Maps vendor-specific field names / CSS selectors to canonical field names.
    Stored in DB so that when a vendor changes their website, a data update
    fixes the scraper — no code deploy required.

    field_name examples: cost, title, description, image, availability, qty,
                         vendor_part_number, oem_number, brand
    selector_or_rule:    CSS selector, XPath, JSON key path, or regex
    """
    __tablename__ = "scraper_field_mappings"
    __table_args__ = (
        Index("ix_scraper_field_mappings_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("scraper_sources.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    selector_or_rule: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped[ScraperSource] = relationship("ScraperSource", back_populates="field_mappings")
