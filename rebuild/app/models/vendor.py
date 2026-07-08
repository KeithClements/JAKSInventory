from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, DateTime, Float, Boolean, Integer, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models.mixins import QBOSyncMixin
from app.constants import PaymentTerms, VendorContactRole, VendorCreditType, VendorCreditStatus, VendorProgramType


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (
        # Risk #2 — DB backstop: no two ACTIVE vendors may share a name (case is
        # preserved in the column; the router probe is case-insensitive). Partial
        # so a deactivated vendor's name can be reused / a re-created vendor can
        # take the same name as an inactive one. Name must match
        # _PENDING_UNIQUE_INDEXES in app/database.py (the defensive live-DB path).
        Index(
            "uq_vendors_name_active", "name",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        # Risk #2 — DB backstop: a non-blank vendor_code must be globally unique
        # (it drives the internal vendor_sku JAKS-[CODE]-[PART#]). Partial so the
        # '' default (an unconfigured vendor) repeats freely. Name must match
        # _PENDING_UNIQUE_INDEXES in app/database.py.
        Index(
            "uq_vendors_vendor_code", "vendor_code",
            unique=True,
            sqlite_where=text("vendor_code != ''"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # ── Identification ────────────────────────────────────────────────────────
    # vendor_code drives the INTERNAL vendor_sku: JAKS-[VENDOR_CODE]-[PART#]
    vendor_code: Mapped[str] = mapped_column(String(4), nullable=False, default="")
    # JAKS SKU scheme (owner-locked 2026-06-06): 1-digit OPAQUE vendor number baked
    # into the customer-facing SKU JAKS-[ENGINE]-[CAT]-[V][NNNN] (e.g. PAI=9). Owner-
    # set on the vendor record; distinct from the readable vendor_code (PAI/HHP),
    # which stays on the internal vendor_sku only. '' until assigned.
    vendor_number: Mapped[str] = mapped_column(String(2), nullable=False, default="")
    # Private label: when True, this vendor's products are sold as our house brand —
    # the customer-facing SKU HIDES the vendor code by omitting that segment
    # ({prefix}-{part#}, e.g. JAKS-10R1273 — never doubled). Products created/imported
    # under this vendor are flagged is_house_brand. Per-product override = the
    # is_house_brand checkbox on the product form. See [[jaks-sku-scheme]].
    private_label: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"))
    account_number: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    website: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # §21 — persisted QBO Vendor binding. qbo_service._resolve_vendor already
    # hasattr-gates this and writes it on first push; before this column it
    # re-resolved by name on every vendor-bill push (dup-vendor risk on rename).
    qbo_vendor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

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
