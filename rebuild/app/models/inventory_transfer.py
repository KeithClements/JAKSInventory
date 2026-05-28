"""
app/models/inventory_transfer.py
=================================
R6 — Location-to-location inventory movements.

Distinct from inventory_transactions (which change qty_on_hand).
Transfers do NOT change total qty — they move stock between locations.

Used both for general inventory (Warehouse → Counter) and for cores
(Core Shelf → Ready for PAI). Core movements may alternately use
core_location_movements (more specific to core lifecycle).

Implementation lands in Phase B. Phase A only adds the schema.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class InventoryTransfer(Base):
    __tablename__ = "inventory_transfers"
    __table_args__ = (
        Index("ix_inv_transfers_product_id", "product_id"),
        Index("ix_inv_transfers_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=True
    )
    destination_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=True
    )

    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Optional polymorphic back-reference (e.g. a VCR triggered the transfer)
    reference_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    performed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
