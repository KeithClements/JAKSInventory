"""
app/models/competitor.py
========================
Competitor market-intelligence pricing — Phase 2.

HARD RULE (owner-locked): competitor pricing is MARKET INTELLIGENCE and must
never be stored as cost. PAI purchasing cost lives on
``ProductVendorSource.vendor_cost`` (+ ``ProductCostHistory``). These two tables
are the symmetric market-side ledger:

  • CompetitorPrice         — the latest/current price per (product, competitor,
                              competitor part #). Many competitors per product
                              (HHP, ATL Diesel, FinditParts, eBay, Amazon, NAPA,
                              local jobbers …).
  • CompetitorPriceHistory  — one row appended every time a CompetitorPrice
                              changes (price / shipping / core), mirroring how
                              ProductCostHistory tracks PAI cost over time.

Written by the importer's Pricing-Update mode; never created in Full-import mode
without an explicit competitor source.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    String, Text, Float, Boolean, ForeignKey, DateTime, Index, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CompetitorPrice(Base):
    """Latest/current competitor price for a product. Unique per
    (product, competitor_name, competitor_part_number) so Pricing-Update targets
    exactly one row and appends history on change."""
    __tablename__ = "competitor_prices"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "competitor_name", "competitor_part_number",
            name="uq_competitor_price_grain",
        ),
        Index("ix_competitor_prices_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)

    competitor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    competitor_part_number: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    competitor_brand: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    shipping_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    core_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    availability_status: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # high / med / low (kept as text so the scraper can set whatever it computes)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    product: Mapped["Product"] = relationship(
        "Product", back_populates="competitor_prices"
    )
    history: Mapped[list["CompetitorPriceHistory"]] = relationship(
        "CompetitorPriceHistory",
        back_populates="competitor_price",
        cascade="all, delete-orphan",
        order_by="CompetitorPriceHistory.seen_at",
    )


class CompetitorPriceHistory(Base):
    """Append-only price-change log for a CompetitorPrice (mirrors
    ProductCostHistory on the PAI-cost side)."""
    __tablename__ = "competitor_price_history"
    __table_args__ = (
        Index("ix_competitor_price_history_cp_id", "competitor_price_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_price_id: Mapped[int] = mapped_column(
        ForeignKey("competitor_prices.id"), nullable=False
    )

    old_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    new_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    old_shipping_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    new_shipping_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    old_core_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    new_core_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    percent_change: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    source: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    competitor_price: Mapped["CompetitorPrice"] = relationship(
        "CompetitorPrice", back_populates="history"
    )


# Late import for type-checker/relationship resolution (string-based at runtime).
from app.models.product import Product  # noqa: E402,F401
