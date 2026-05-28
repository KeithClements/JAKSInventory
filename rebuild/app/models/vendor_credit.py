"""
app/models/vendor_credit.py
============================
R11 — Vendor-side credit memos (vendor owes JAKS money).

Mirror of customer credit_memos for the AP side. Tracks vendor overcharges,
defective merchandise credits, returns accepted by vendor, etc.

Number format: VCM-YYYY-NNNN

Implementation lands in Phase G. Phase A only adds the schema.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models.mixins import QBOSyncMixin
from app.constants import VendorCreditMemoTrigger, CreditMemoStatus


class VendorCreditMemo(QBOSyncMixin, Base):
    __tablename__ = "vendor_credit_memos"
    __table_args__ = (
        Index("ix_vcm_vendor_id_status", "vendor_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vcm_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)

    # ── Source / Trigger ──────────────────────────────────────────────────────
    original_vendor_bill_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_bills.id"), nullable=True
    )
    trigger_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default=VendorCreditMemoTrigger.OVERCHARGE
    )
    vendor_return_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_returns.id"), nullable=True
    )

    # ── Amounts ───────────────────────────────────────────────────────────────
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    applied_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unapplied_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CreditMemoStatus.OPEN
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # ── QBO ───────────────────────────────────────────────────────────────────
    qbo_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    allocations: Mapped[list[VendorCreditMemoAllocation]] = relationship(
        "VendorCreditMemoAllocation",
        back_populates="vendor_credit_memo",
        cascade="all, delete-orphan",
    )


class VendorCreditMemoAllocation(Base):
    """Allocates a vendor credit against a vendor bill (AP side)."""
    __tablename__ = "vendor_credit_memo_allocations"
    __table_args__ = (
        Index("ix_vcm_alloc_bill_id", "vendor_bill_id"),
        Index("ix_vcm_alloc_vcm_id", "vendor_credit_memo_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_credit_memo_id: Mapped[int] = mapped_column(
        ForeignKey("vendor_credit_memos.id"), nullable=False
    )
    vendor_bill_id: Mapped[int] = mapped_column(
        ForeignKey("vendor_bills.id"), nullable=False
    )
    amount_applied: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    applied_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    applied_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    is_reversed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    vendor_credit_memo: Mapped[VendorCreditMemo] = relationship(
        "VendorCreditMemo", back_populates="allocations"
    )
