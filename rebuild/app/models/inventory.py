from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, Float, ForeignKey, DateTime, Index, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import InventoryLocationType, InventoryTxnType, AdjustmentReason


class InventoryLocation(Base):
    """
    Physical or logical storage location.
    V1: one default location "Main Shop" created on seed.
    Multi-location UI added when needed.
    """
    __tablename__ = "inventory_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=InventoryLocationType.SHOP
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    transactions: Mapped[list[InventoryTransaction]] = relationship(
        "InventoryTransaction", back_populates="location"
    )


class InventoryTransaction(Base):
    """
    Full inventory ledger — every qty change creates a row here.
    qty_on_hand on the product is a cached value; the ledger is the source of truth.

    Rules:
      - positive qty_change = inventory in  (receipt, return to stock)
      - negative qty_change = inventory out (sale, write-off, committed)
      - qty_after is a snapshot at time of transaction (for reconciliation)
    """
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        # product_id: scanned on every on-hand calculation
        Index("ix_inventory_txn_product_id", "product_id"),
        # performed_at: date-range queries for reconciliation reports
        Index("ix_inventory_txn_performed_at", "performed_at"),
        # Polymorphic ref must be fully present or fully absent (never half-set)
        CheckConstraint(
            "(reference_type IS NULL) = (reference_id IS NULL)",
            name="ck_inventory_txn_ref_complete",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=True
    )

    transaction_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # InventoryTxnType
    qty_change: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_after: Mapped[int] = mapped_column(Integer, nullable=False)

    # Polymorphic back-reference — which event caused this
    reference_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # For manual_adjustment transactions only
    reason: Mapped[str | None] = mapped_column(String(30), nullable=True)

    performed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    performed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    product: Mapped[Product] = relationship(
        "Product", back_populates="inventory_transactions"
    )
    location: Mapped[InventoryLocation | None] = relationship(
        "InventoryLocation", back_populates="transactions"
    )


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.product import Product  # noqa: E402
