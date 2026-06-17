"""
app/models/credit_memo.py
=========================
R8 — Customer-side credit memos.

A credit memo is an independent financial document (NOT a negative invoice).
Created from accepted RAs, approved warranties, locked-invoice corrections,
or manual issuance. Unapplied balance can sit as customer credit or be
allocated against specific invoices via credit_memo_allocations.

Number format: CM-YYYY-NNNN (via bump_counter next_cm_number).

Implementation lands in Phase F. Phase A only adds the schema.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models.mixins import QBOSyncMixin
from app.constants import CreditMemoTrigger, CreditMemoStatus


class CreditMemo(QBOSyncMixin, Base):
    __tablename__ = "credit_memos"
    __table_args__ = (
        Index("ix_credit_memos_customer_id_status", "customer_id", "status"),
        Index("ix_credit_memos_qbo_sync_status", "qbo_sync_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cm_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)

    # ── Source / Trigger ──────────────────────────────────────────────────────
    original_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )
    trigger_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default=CreditMemoTrigger.MANUAL
    )
    # Reverse-link from accepted RA / approved warranty (Phase F wires these)
    ra_id: Mapped[int | None] = mapped_column(
        ForeignKey("return_authorizations.id"), nullable=True
    )
    warranty_claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("warranty_claims.id"), nullable=True
    )

    # ── Amounts ───────────────────────────────────────────────────────────────
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # §21 — applied_amount is now COMPUTED from the allocations (see the property
    # below), so it can never drift from the actual CreditMemoAllocation rows.
    # The legacy DB column is left in place (orphaned, harmless) — not mapped.
    # unapplied_amount STAYS stored: close_credit_memo zeroes it when the residual
    # is pushed to the customer's credit_balance, a state not derivable from
    # allocations alone, and reverse_credit_memo reads it to detect that case.
    unapplied_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CreditMemoStatus.OPEN
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Audit / Lock ──────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # ── QBO ───────────────────────────────────────────────────────────────────
    qbo_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    customer: Mapped[Customer] = relationship("Customer")
    original_invoice: Mapped[Invoice | None] = relationship(
        "Invoice", foreign_keys=[original_invoice_id]
    )
    lines: Mapped[list[CreditMemoLine]] = relationship(
        "CreditMemoLine", back_populates="credit_memo", cascade="all, delete-orphan"
    )
    allocations: Mapped[list[CreditMemoAllocation]] = relationship(
        "CreditMemoAllocation", back_populates="credit_memo", cascade="all, delete-orphan"
    )

    @property
    def applied_amount(self) -> float:
        """§21 — sum of non-reversed allocations to invoices. Computed (not stored)
        so it can never drift from the CreditMemoAllocation ledger."""
        return round(
            sum(a.amount_applied for a in self.allocations if not a.is_reversed), 2
        )


class CreditMemoLine(Base):
    __tablename__ = "credit_memo_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    credit_memo_id: Mapped[int] = mapped_column(
        ForeignKey("credit_memos.id"), nullable=False
    )
    # Optional back-link to the original invoice line being credited
    original_invoice_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_lines.id"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tax_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    credit_memo: Mapped[CreditMemo] = relationship("CreditMemo", back_populates="lines")


class CreditMemoAllocation(Base):
    """Allocates a credit memo amount against a specific invoice."""
    __tablename__ = "credit_memo_allocations"
    __table_args__ = (
        Index("ix_cm_alloc_invoice_id", "invoice_id"),
        Index("ix_cm_alloc_credit_memo_id", "credit_memo_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    credit_memo_id: Mapped[int] = mapped_column(
        ForeignKey("credit_memos.id"), nullable=False
    )
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    amount_applied: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    applied_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    applied_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    is_reversed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    credit_memo: Mapped[CreditMemo] = relationship(
        "CreditMemo", back_populates="allocations"
    )
    invoice: Mapped[Invoice] = relationship("Invoice")


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.customer import Customer    # noqa: E402
from app.models.invoice import Invoice      # noqa: E402
