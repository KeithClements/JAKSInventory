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
    def goods_received(self) -> bool:
        """True once RAService.receive_goods has recorded a receipt on any line.

        receive_goods stamps ``inspected_at`` on every line it processes in the
        same transaction that flips status to RECEIVED, so this is the reliable
        "goods actually came back" signal — unlike ``status``, it stays False
        for an RA closed straight from OPEN (authorized but never received).
        """
        return any(ln.inspected_at is not None for ln in self.lines)

    @property
    def total_credit(self) -> float:
        """Credit due to the customer for this return — EXPECTED vs ACTUAL.

        • Before goods come back (DRAFT/OPEN — no receive recorded on any
          line): the EXPECTED credit, full authorized qty per line:
              sum(unit_price * qty - restocking_fee)
        • Once goods have been received (``goods_received`` — the RA reached
          RECEIVED/CLOSED via receive_goods): the ACTUAL credit, counting only
          what physically came back per line:
              sum(max(0, unit_price * max(qty_returned_to_stock, 0)
                         - restocking_fee))
          A customer who returns 2 of 5 authorized items is credited for 2,
          not 5. A line with nothing returned contributes $0 (its restocking
          fee is not charged as negative credit), and a restocking fee can
          never push a line negative.

        An RA closed directly from OPEN (no receive step) keeps the expected
        total — that path intentionally credits the full authorization.
        RAService.close_ra applies the same rule when building the credit memo.
        """
        if self.goods_received:
            total = 0.0
            for ln in self.lines:
                qty = max(ln.qty_returned_to_stock or 0, 0)
                if qty <= 0:
                    continue
                total += max(0.0, (ln.unit_price * qty) - ln.restocking_fee)
            return round(total, 2)
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
        """Per-line credit — EXPECTED vs ACTUAL (mirrors RA.total_credit).

        Once this line has a receive recorded (``inspected_at`` set by
        RAService.receive_goods), only ``qty_returned_to_stock`` earns credit;
        before that, the full authorized qty is the expected credit.
        """
        if self.inspected_at is not None:
            qty = max(self.qty_returned_to_stock or 0, 0)
            if qty <= 0:
                return 0.0
            return round(max(0.0, (self.unit_price * qty) - self.restocking_fee), 2)
        return round((self.unit_price * self.qty) - self.restocking_fee, 2)


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.customer import Customer    # noqa: E402
from app.models.product import Product      # noqa: E402
