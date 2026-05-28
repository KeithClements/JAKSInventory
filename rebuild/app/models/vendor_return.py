"""
app/models/vendor_return.py
============================
R11 — Vendor returns for non-core merchandise.

Distinct from VendorCoreReturn (which handles core returns to vendor for credit).
This handles general merchandise: wrong part ordered, defective, etc.

Number format: VR-YYYY-NNNN

Implementation lands in Phase G. Phase A only adds the schema.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import VendorReturnStatus, VendorReturnLineOutcome


class VendorReturn(Base):
    __tablename__ = "vendor_returns"
    __table_args__ = (
        Index("ix_vr_vendor_id_status", "vendor_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vr_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)

    # ── Source Links ──────────────────────────────────────────────────────────
    original_po_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=True
    )
    original_vendor_bill_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_bills.id"), nullable=True
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VendorReturnStatus.DRAFT
    )

    # ── Credit Reconciliation ─────────────────────────────────────────────────
    expected_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credit_difference: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    restocking_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Shipping ──────────────────────────────────────────────────────────────
    tracking_number: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    rma_number: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    vendor_decision_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    lines: Mapped[list[VendorReturnLine]] = relationship(
        "VendorReturnLine", back_populates="vendor_return", cascade="all, delete-orphan"
    )


class VendorReturnLine(Base):
    __tablename__ = "vendor_return_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_return_id: Mapped[int] = mapped_column(
        ForeignKey("vendor_returns.id"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expected_unit_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_unit_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vendor_outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VendorReturnLineOutcome.PENDING
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    vendor_return: Mapped[VendorReturn] = relationship(
        "VendorReturn", back_populates="lines"
    )
