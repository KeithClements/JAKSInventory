from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, DateTime, Float, Boolean, Integer, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models.mixins import QBOSyncMixin
from app.constants import PaymentTerms, VendorContactRole, VendorCreditType, VendorCreditStatus, VendorProgramType


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # ── Identification ────────────────────────────────────────────────────────
    # vendor_code drives SKU generation: JAKS-[VENDOR_CODE]-[PART#]
    vendor_code: Mapped[str] = mapped_column(String(4), nullable=False, default="")
    account_number: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    website: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    # ── Single primary contact (kept for quick access; full list → vendor_contacts) ──
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── Terms & Policy ────────────────────────────────────────────────────────
    payment_terms: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentTerms.NET_30
    )
    return_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restock_fee_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    special_order_returnable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    contacts: Mapped[list[VendorContact]] = relationship(
        "VendorContact", back_populates="vendor", cascade="all, delete-orphan"
    )
    credits: Mapped[list[VendorCredit]] = relationship(
        "VendorCredit", back_populates="vendor"
    )
    programs: Mapped[list[VendorProgram]] = relationship(
        "VendorProgram", back_populates="vendor", cascade="all, delete-orphan"
    )
    purchase_orders: Mapped[list[PurchaseOrder]] = relationship(
        "PurchaseOrder", back_populates="vendor"
    )
    vendor_sources: Mapped[list[ProductVendorSource]] = relationship(
        "ProductVendorSource", back_populates="vendor"
    )
    core_charges: Mapped[list[CoreCharge]] = relationship(
        "CoreCharge", back_populates="vendor"
    )
    warranty_claims: Mapped[list[WarrantyClaim]] = relationship(
        "WarrantyClaim", back_populates="vendor"
    )

    # ── Convenience ───────────────────────────────────────────────────────────
    @property
    def primary_contact(self) -> VendorContact | None:
        return next((c for c in self.contacts if c.is_primary), None)


class VendorContact(Base):
    __tablename__ = "vendor_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VendorContactRole.GENERAL
    )
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sales_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_warranty_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_returns_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_accounting_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    vendor: Mapped[Vendor] = relationship("Vendor", back_populates="contacts")


class VendorCredit(QBOSyncMixin, Base):
    """Non-core vendor credits: rebates, pricing corrections, freight adjustments, etc."""
    __tablename__ = "vendor_credits"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    credit_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credit_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    credit_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=VendorCreditType.OTHER
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VendorCreditStatus.OPEN
    )

    # ── QBO sync ─────────────────────────────────────────────────────────────
    # qbo_sync_status, qbo_last_synced_at, qbo_sync_error, qbo_sync_retry_count
    # are inherited from QBOSyncMixin.
    qbo_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    vendor: Mapped[Vendor] = relationship("Vendor", back_populates="credits")


class VendorProgram(Base):
    """Volume rebate / promotional / tier discount agreements with a vendor."""
    __tablename__ = "vendor_programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    program_name: Mapped[str] = mapped_column(String(200), nullable=False)
    program_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=VendorProgramType.OTHER
    )
    threshold_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    rebate_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    vendor: Mapped[Vendor] = relationship("Vendor", back_populates="programs")


# ── Late imports (avoid circular) ─────────────────────────────────────────────
from app.models.purchase_order import PurchaseOrder          # noqa: E402
from app.models.product import ProductVendorSource           # noqa: E402
from app.models.core import CoreCharge                       # noqa: E402
from app.models.warranty import WarrantyClaim                # noqa: E402
