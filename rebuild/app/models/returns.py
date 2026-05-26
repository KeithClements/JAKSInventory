from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import RAStatus, ReturnDisposition


class ReturnAuthorization(Base):
    """
    Standard customer return — wrong part, unused, customer-error, etc.
    Both require a signed document from the customer.
    Warranty-related returns use WarrantyClaim instead.
    """
    __tablename__ = "return_authorizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    ra_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RAStatus.DRAFT
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Approval ──────────────────────────────────────────────────────────────
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Policy Snapshot ───────────────────────────────────────────────────────
    # JSON snapshot of vendor/product return policy at time of return (for audit)
    vendor_policy_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    customer: Mapped[Customer] = relationship(
        "Customer", back_populates="return_authorizations"
    )
    lines: Mapped[list[ReturnLine]] = relationship(
        "ReturnLine", back_populates="ra", cascade="all, delete-orphan"
    )

    # ── Computed ──────────────────────────────────────────────────────────────
    @property
    def total_credit(self) -> float:
        return round(
            sum((ln.unit_price * ln.qty) - ln.restocking_fee for ln in self.lines), 2
        )


class ReturnLine(Base):
    __tablename__ = "return_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    ra_id: Mapped[int] = mapped_column(
        ForeignKey("return_authorizations.id"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    restocking_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Inventory Disposition ─────────────────────────────────────────────────
    disposition: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ReturnDisposition.QUARANTINE
    )
    qty_returned_to_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Inspection ────────────────────────────────────────────────────────────
    inspected_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    condition_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    ra: Mapped[ReturnAuthorization] = relationship("ReturnAuthorization", back_populates="lines")
    product: Mapped[Product | None] = relationship("Product")

    @property
    def line_credit(self) -> float:
        return round((self.unit_price * self.qty) - self.restocking_fee, 2)


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.customer import Customer    # noqa: E402
from app.models.product import Product      # noqa: E402
