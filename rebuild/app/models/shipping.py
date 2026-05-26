from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import ShipmentStatus, Carrier


class Shipment(Base):
    """
    Outbound shipment record — tracks both what the customer was charged (customer_shipping_charge)
    and what JAKS actually paid the carrier (actual_shipping_cost).

    actual_shipping_cost is INTERNAL only — never printed on customer documents.
    The difference (customer_shipping_charge - actual_shipping_cost) is the freight margin.

    Barcode/QR support: tracking_number field is designed to be scannable in the future.
    """
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ── Source Document ────────────────────────────────────────────────────────
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )
    sales_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=True
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ShipmentStatus.DRAFT
    )

    # ── Carrier & Tracking ────────────────────────────────────────────────────
    carrier: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
    tracking_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    shipping_method: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Cost (internal vs customer-facing) ────────────────────────────────────
    actual_shipping_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    customer_shipping_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Dates ────────────────────────────────────────────────────────────────
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ── Computed ──────────────────────────────────────────────────────────────
    @property
    def freight_margin(self) -> float:
        return round(self.customer_shipping_charge - self.actual_shipping_cost, 2)
