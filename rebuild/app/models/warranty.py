from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import WarrantyStatus, WarrantyDecision, WarrantyResolution


class WarrantyClaim(Base):
    """
    Warranty claim lifecycle:
      draft → submitted_to_vendor → vendor_approved → customer_credited → closed
                                 ↘ vendor_denied → customer_notified → closed

    One claim can cover multiple invoice lines via warranty_claim_lines.
    Modeled on HHP / ATL Diesel dealer warranty process.
    """
    __tablename__ = "warranty_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )
    # Optional link to a related RA if the part was also returned
    ra_id: Mapped[int | None] = mapped_column(
        ForeignKey("return_authorizations.id"), nullable=True
    )
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendors.id"), nullable=True
    )

    claim_date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=WarrantyStatus.DRAFT
    )
    failure_description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Vendor Submission & Decision ──────────────────────────────────────────
    submitted_to_vendor_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    vendor_decision: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WarrantyDecision.PENDING
    )
    vendor_decision_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    vendor_decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Resolution ────────────────────────────────────────────────────────────
    resolution_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    total_credit_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    customer: Mapped[Customer] = relationship(
        "Customer", back_populates="warranty_claims"
    )
    vendor: Mapped[Vendor | None] = relationship(
        "Vendor", back_populates="warranty_claims"
    )
    claim_lines: Mapped[list[WarrantyClaimLine]] = relationship(
        "WarrantyClaimLine", back_populates="claim", cascade="all, delete-orphan"
    )

    @property
    def is_pending_vendor(self) -> bool:
        return self.status == WarrantyStatus.SUBMITTED_TO_VENDOR


class WarrantyClaimLine(Base):
    """One affected part within a warranty claim."""
    __tablename__ = "warranty_claim_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    warranty_claim_id: Mapped[int] = mapped_column(
        ForeignKey("warranty_claims.id"), nullable=False
    )
    invoice_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_lines.id"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    qty_claimed: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    credit_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # If resolution = replacement, this links the replacement invoice line
    replacement_invoice_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_lines.id"), nullable=True
    )

    claim: Mapped[WarrantyClaim] = relationship(
        "WarrantyClaim", back_populates="claim_lines"
    )
    product: Mapped[Product | None] = relationship("Product")


# ── Scaffold table ────────────────────────────────────────────────────────────

class ESNLookup(Base):
    """Architecture placeholder — ESN lookup scraper (Phase 3)."""
    __tablename__ = "esn_lookups"

    id: Mapped[int] = mapped_column(primary_key=True)
    esn: Mapped[str] = mapped_column(String(100), nullable=False)
    engine_manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    engine_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    looked_up_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EngineConfig(Base):
    """Architecture placeholder — engine configuration profiles (Phase 3)."""
    __tablename__ = "engine_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(200), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    displacement: Mapped[str | None] = mapped_column(String(50), nullable=True)
    years_produced: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.customer import Customer            # noqa: E402
from app.models.vendor import Vendor                # noqa: E402
from app.models.product import Product              # noqa: E402
from app.models.returns import ReturnAuthorization  # noqa: E402
