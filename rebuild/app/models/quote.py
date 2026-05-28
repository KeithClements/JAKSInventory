from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, Date, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import QuoteStatus, QuoteOutcome, QuoteFollowupStatus, SOStatus, SOPaymentMode, SOLineSource, LineType, LineRole
from app.utils import calc_line_total, calc_margin_pct


# ─────────────────────────────────────────────────────────────────────────────
# QUOTE
# ─────────────────────────────────────────────────────────────────────────────

class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QuoteStatus.DRAFT
    )
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Expiry & Follow-up ────────────────────────────────────────────────────
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    validity_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    follow_up_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=QuoteFollowupStatus.PENDING
    )
    follow_up_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Outcome Tracking ──────────────────────────────────────────────────────
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QuoteOutcome.PENDING
    )
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Reactivation ──────────────────────────────────────────────────────────
    reactivated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reactivated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    original_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Conversion Tracking ───────────────────────────────────────────────────
    converted_to_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True
    )
    converted_to_so_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=True
    )

    # ── Option Groups (System 2 — customer picks one repair strategy) ────────────
    # Nullable — most quotes don't use groups. When set, lines tagged to the
    # selected group convert to SO; other-group lines are dropped.
    # Group names are configurable in settings (package_name_economy, etc.).
    selected_option_group: Mapped[str | None] = mapped_column(String(50), nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    customer: Mapped[Customer] = relationship("Customer", back_populates="quotes")
    lines: Mapped[list[QuoteLine]] = relationship(
        "QuoteLine", back_populates="quote", cascade="all, delete-orphan",
        order_by="QuoteLine.sort_order"
    )

    # ── Computed ──────────────────────────────────────────────────────────────
    @property
    def subtotal(self) -> float:
        """Sum of line totals for included lines only (is_included=True)."""
        return round(sum(ln.line_total for ln in self.lines if ln.is_included), 2)

    @property
    def is_expired(self) -> bool:
        if self.valid_until is None:
            return False
        return datetime.utcnow() > self.valid_until

    @property
    def needs_follow_up(self) -> bool:
        if self.follow_up_date is None:
            return False
        return (
            self.outcome == QuoteOutcome.PENDING
            and datetime.utcnow().date() >= self.follow_up_date.date()
        )


class QuoteLine(Base):
    __tablename__ = "quote_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    line_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=LineType.PRODUCT
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_core_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Parent-child line relationships ──────────────────────────────────────
    # line_role   — relationship role within the parent-child tree (see LineRole enum).
    # is_included — controls whether this line contributes to the quote total.
    #               False for upgrade_option lines until customer selects one.
    # option_label— human label for the option, e.g. "Stage 2 Upgrade", "OEM Alternative".
    # parent_line_id — self-referential FK; top-level lines have NULL.
    #
    # is_optional / option_group are the older System 1 / System 2 fields; kept for
    # backward compatibility — line_role supersedes is_optional for new code.
    line_role: Mapped[str] = mapped_column(
        String(30), nullable=False, default=LineRole.PRIMARY
    )
    is_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    option_label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    is_optional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    option_group: Mapped[str | None] = mapped_column(String(50), nullable=True)

    parent_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_lines.id"), nullable=True
    )
    # See InvoiceLine for documentation on these flags.
    is_auto_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_locked_to_parent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    research_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    research_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_items.id"), nullable=True
    )

    quote: Mapped[Quote] = relationship("Quote", back_populates="lines")
    product: Mapped[Product | None] = relationship(
        "Product", back_populates="quote_lines"
    )

    # ── Self-referential: core charge lines attach under their parent product line ──
    parent: Mapped[QuoteLine | None] = relationship(
        "QuoteLine", back_populates="children", remote_side="QuoteLine.id",
        foreign_keys="QuoteLine.parent_line_id",
    )
    children: Mapped[list[QuoteLine]] = relationship(
        "QuoteLine", back_populates="parent",
        foreign_keys="QuoteLine.parent_line_id",
    )

    @property
    def line_total(self) -> float:
        return calc_line_total(self.unit_price, self.qty, self.discount_pct)

    @property
    def margin_pct(self) -> float:
        return calc_margin_pct(self.unit_price, self.unit_cost)


# ─────────────────────────────────────────────────────────────────────────────
# SALES ORDER  (confirmed missing workflow step between Quote and Invoice)
# ─────────────────────────────────────────────────────────────────────────────

class SalesOrder(Base):
    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    so_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotes.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SOStatus.OPEN
    )

    # ── Payment at SO stage (D4 — 3 modes confirmed) ──────────────────────────
    payment_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SOPaymentMode.NONE
    )
    deposit_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Customer-Facing Reference Fields ──────────────────────────────────────
    customer_po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_job_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    esn: Mapped[str | None] = mapped_column(String(100), nullable=True)
    engine_manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    engine_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # ── Shipping ──────────────────────────────────────────────────────────────
    ship_to_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_addresses.id"), nullable=True
    )

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    customer: Mapped[Customer] = relationship("Customer", back_populates="sales_orders")
    lines: Mapped[list[SOLine]] = relationship(
        "SOLine", back_populates="so", cascade="all, delete-orphan",
        order_by="SOLine.sort_order"
    )
    invoices: Mapped[list[Invoice]] = relationship(
        "Invoice", back_populates="sales_order",
        foreign_keys="Invoice.sales_order_id"
    )

    # ── Computed ──────────────────────────────────────────────────────────────
    @property
    def subtotal(self) -> float:
        return round(sum(ln.line_total for ln in self.lines), 2)

    @property
    def is_fully_invoiced(self) -> bool:
        return all(ln.qty_invoiced >= ln.qty_ordered for ln in self.lines)

    @property
    def has_backorder(self) -> bool:
        return any(ln.source == SOLineSource.BACKORDER for ln in self.lines)


class SOLine(Base):
    __tablename__ = "so_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    line_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=LineType.PRODUCT
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty_ordered: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    qty_committed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_fulfilled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_invoiced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    core_charge: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SOLineSource.STOCK
    )
    # ── Parent/child linkage (consistent with InvoiceLine / QuoteLine) ───────
    # The existing `core_charge` float field is the legacy per-line core amount;
    # parent_line_id + is_core_line enable the future pattern where cores are
    # discrete child lines (matching how invoice cores work).
    parent_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("so_lines.id"), nullable=True
    )
    is_core_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_auto_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_locked_to_parent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    so: Mapped[SalesOrder] = relationship("SalesOrder", back_populates="lines")
    product: Mapped[Product | None] = relationship("Product", back_populates="so_lines")

    # Self-referential parent/children for core-line linkage.
    parent: Mapped[SOLine | None] = relationship(
        "SOLine", back_populates="children", remote_side="SOLine.id",
        foreign_keys="SOLine.parent_line_id",
    )
    children: Mapped[list[SOLine]] = relationship(
        "SOLine", back_populates="parent",
        foreign_keys="SOLine.parent_line_id",
    )

    @property
    def line_total(self) -> float:
        discounted = self.unit_price * (1 - self.discount_pct / 100)
        return round(discounted * self.qty_ordered, 2)

    @property
    def qty_remaining(self) -> int:
        return self.qty_ordered - self.qty_invoiced

    @property
    def is_backordered(self) -> bool:
        return self.source == SOLineSource.BACKORDER


# ─────────────────────────────────────────────────────────────────────────────
# Scaffold tables (architecture placeholder)
# ─────────────────────────────────────────────────────────────────────────────

class QuoteFollowup(Base):
    __tablename__ = "quote_followups"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    scheduled_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)


class LostSaleLog(Base):
    __tablename__ = "lost_sales_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotes.id"), nullable=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    competitor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    competitor_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    logged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.customer import Customer                     # noqa: E402
from app.models.product import Product                       # noqa: E402
from app.models.invoice import Invoice                       # noqa: E402
from app.models.research import ResearchItem                 # noqa: E402
