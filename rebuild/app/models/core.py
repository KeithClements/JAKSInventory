from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import (
    CoreDirection, CoreStatus, CoreVendorStatus,
    CoreDenialResolution, CoreCreditMethod,
    CoreInspectionOutcome, CoreSlipStatus, VCRStatus, VCRLineOutcome,
)


class CoreCharge(Base):
    """
    Tracks every open core charge in both directions:
      customer_owes_return  — customer bought a part with core; they owe JAKS the old core back
      vendor_owes_credit    — JAKS shipped cores back to vendor; waiting on vendor credit

    Partial returns supported: qty_charged - qty_returned = qty_outstanding
    """
    __tablename__ = "core_charges"
    __table_args__ = (
        # "open cores for customer" — dashboard and customer account view
        Index("ix_core_charges_customer_id_status", "customer_id", "status"),
        # overdue core report scans return_deadline across all open records
        Index("ix_core_charges_return_deadline", "return_deadline"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CoreDirection.CUSTOMER_OWES_RETURN
    )

    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendors.id"), nullable=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)

    # ── Source Links ──────────────────────────────────────────────────────────
    invoice_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_lines.id"), nullable=True
    )
    po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("po_lines.id"), nullable=True
    )

    # ── Quantities (partial return tracking) ──────────────────────────────────
    qty_charged: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    qty_returned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Pricing (separate vendor cost vs customer charge — cores are marked up) ──
    vendor_unit_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    customer_unit_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Status & Deadline ────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CoreStatus.OPEN
    )
    return_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Vendor Decision (when JAKS ships core back and vendor responds) ────────
    vendor_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CoreVendorStatus.PENDING
    )
    vendor_denial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_decision_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    denial_resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    denial_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Slip / VCR / Location links ───────────────────────────────────────────
    core_slip_id: Mapped[int | None] = mapped_column(
        ForeignKey("core_slips.id"), nullable=True
    )
    vcr_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_core_returns.id"), nullable=True
    )
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("core_locations.id"), nullable=True
    )

    # ── Inspection ────────────────────────────────────────────────────────────
    inspection_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inspected_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # ── Credit issued when core returned ──────────────────────────────────────
    credit_method: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Tracking ──────────────────────────────────────────────────────────────
    # Printed on the customer core return slip — future barcode/QR ready
    core_tracking_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credit_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    customer: Mapped[Customer | None] = relationship(
        "Customer", back_populates="core_charges"
    )
    vendor: Mapped[Vendor | None] = relationship("Vendor", back_populates="core_charges")
    product: Mapped[Product] = relationship("Product", back_populates="core_charges")
    return_events: Mapped[list[CoreReturnEvent]] = relationship(
        "CoreReturnEvent", back_populates="core_charge", cascade="all, delete-orphan"
    )
    core_slip: Mapped[CoreSlip | None] = relationship(
        "CoreSlip", foreign_keys=[core_slip_id], back_populates="core_charges"
    )
    vendor_core_return: Mapped[VendorCoreReturn | None] = relationship(
        "VendorCoreReturn", foreign_keys=[vcr_id], back_populates="core_charges"
    )
    location: Mapped[CoreLocation | None] = relationship("CoreLocation")

    # ── Computed ──────────────────────────────────────────────────────────────
    @property
    def qty_outstanding(self) -> int:
        return self.qty_charged - self.qty_returned

    @property
    def total_customer_charge(self) -> float:
        return round(self.customer_unit_charge * self.qty_charged, 2)

    @property
    def total_vendor_charge(self) -> float:
        return round(self.vendor_unit_charge * self.qty_charged, 2)

    @property
    def core_margin(self) -> float:
        return round(
            (self.customer_unit_charge - self.vendor_unit_charge) * self.qty_charged, 2
        )

    @property
    def is_overdue(self) -> bool:
        if self.return_deadline is None or self.status == CoreStatus.CLOSED:
            return False
        return datetime.utcnow() > self.return_deadline


class CoreReturnEvent(Base):
    """
    Records each partial or full core return from a customer.
    One CoreCharge may have multiple CoreReturnEvents (partial returns).
    """
    __tablename__ = "core_return_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    core_charge_id: Mapped[int] = mapped_column(
        ForeignKey("core_charges.id"), nullable=False
    )
    qty_returned: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    returned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    credit_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CoreCreditMethod.ACCOUNT_CREDIT
    )
    credit_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    processed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    core_charge: Mapped[CoreCharge] = relationship(
        "CoreCharge", back_populates="return_events"
    )


class CoreLocation(Base):
    """Named physical locations for received cores (e.g. 'Staging Shelf A', 'Quarantine Bin')."""
    __tablename__ = "core_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CoreSlip(Base):
    """
    Customer core return slip printed when a core is received from a customer.
    Number format: CORE-2026-XXXX (generated by bump_counter next_core_slip_number).
    """
    __tablename__ = "core_slips"
    __table_args__ = (
        Index("ix_core_slips_invoice_id", "invoice_id"),
        Index("ix_core_slips_customer_id", "customer_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slip_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CoreSlipStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    printed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    customer: Mapped[Customer | None] = relationship("Customer")
    core_charges: Mapped[list[CoreCharge]] = relationship(
        "CoreCharge", foreign_keys="CoreCharge.core_slip_id", back_populates="core_slip"
    )


class VendorCoreReturn(Base):
    """
    Batch shipment of cores back to a vendor for credit.
    Number format: VCR-2026-XXXX (generated by bump_counter next_vcr_number).
    """
    __tablename__ = "vendor_core_returns"
    __table_args__ = (
        Index("ix_vcr_vendor_id_status", "vendor_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vcr_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VCRStatus.DRAFT
    )

    tracking_number: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    rma_number: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── Credit reconciliation ──────────────────────────────────────────────────
    expected_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credit_difference: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    vendor_decision_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    lines: Mapped[list[VendorCoreReturnLine]] = relationship(
        "VendorCoreReturnLine", back_populates="vcr", cascade="all, delete-orphan"
    )
    core_charges: Mapped[list[CoreCharge]] = relationship(
        "CoreCharge", foreign_keys="CoreCharge.vcr_id", back_populates="vendor_core_return"
    )


class VendorCoreReturnLine(Base):
    """One core in a VCR batch."""
    __tablename__ = "vendor_core_return_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    vcr_id: Mapped[int] = mapped_column(
        ForeignKey("vendor_core_returns.id"), nullable=False
    )
    core_charge_id: Mapped[int | None] = mapped_column(
        ForeignKey("core_charges.id"), nullable=True
    )
    part_number: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expected_unit_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_unit_credit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vendor_outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VCRLineOutcome.PENDING
    )

    vcr: Mapped[VendorCoreReturn] = relationship(
        "VendorCoreReturn", back_populates="lines"
    )


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.customer import Customer    # noqa: E402
from app.models.vendor import Vendor        # noqa: E402
from app.models.product import Product      # noqa: E402
from app.models.invoice import Invoice      # noqa: E402
