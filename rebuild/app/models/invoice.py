from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models.mixins import QBOSyncMixin
from app.utils import calc_line_total, calc_margin_pct
from app.invoice_totals import compute_invoice_totals
from app.constants import (
    InvoiceStatus, InvoiceLockReason, LineType,
    PaymentMethod, PaymentStatus, PaymentReversalReason, PaymentDirection,
    QBOSyncStatus,
)


class Invoice(QBOSyncMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        # open-invoice queries always filter by customer + status
        Index("ix_invoices_customer_id_status", "customer_id", "status"),
        # QBO sync job polls this on every run
        Index("ix_invoices_qbo_sync_status", "qbo_sync_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)

    # ── Source Documents ──────────────────────────────────────────────────────
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotes.id"), nullable=True
    )
    sales_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InvoiceStatus.DRAFT
    )

    # ── Lock Control (D5 — locks at EOD or QBO sync or paid) ─────────────────
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lock_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Customer-Facing Reference Fields ──────────────────────────────────────
    customer_po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_job_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    esn: Mapped[str | None] = mapped_column(String(100), nullable=True)
    engine_manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    engine_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # ── Pricing ───────────────────────────────────────────────────────────────
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    apply_cc_surcharge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cc_surcharge_pct: Mapped[float] = mapped_column(Float, nullable=False, default=3.0)
    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tax_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── R1 Tax Snapshots — captured at invoice creation, frozen at finalize ────
    tax_rate_snapshot: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_exempt_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # R10 — sales tax filing requires per-invoice jurisdiction
    tax_jurisdiction: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── R11 Ship-to ────────────────────────────────────────────────────────────
    ship_to_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_addresses.id"), nullable=True
    )
    # Snapshot of address text frozen at finalization (in case address record edits later)
    ship_to_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Dates ────────────────────────────────────────────────────────────────
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── QBO Sync ─────────────────────────────────────────────────────────────
    # qbo_sync_status, qbo_last_synced_at, qbo_sync_error, qbo_sync_retry_count
    # are inherited from QBOSyncMixin.
    qbo_invoice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    customer: Mapped[Customer] = relationship("Customer", back_populates="invoices")
    sales_order: Mapped[SalesOrder | None] = relationship(
        "SalesOrder",
        back_populates="invoices",
        foreign_keys=[sales_order_id],
    )
    lines: Mapped[list[InvoiceLine]] = relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.sort_order",
    )
    allocations: Mapped[list[PaymentAllocation]] = relationship(
        "PaymentAllocation", back_populates="invoice"
    )

    # ── Computed ──────────────────────────────────────────────────────────────
    # These money properties — read by the List "Total" column, the row Preview
    # panel, and the print/PDF — delegate to the ONE totals engine
    # (app.invoice_totals.compute_invoice_totals), the very same function
    # InvoiceService.calculate_totals feeds the workspace totals panel. Routing
    # both through one pure function is the structural guarantee that the
    # customer-facing surfaces and the workspace can never disagree. See that
    # module for the full policy (cores never taxed; every line — fees & credits
    # included — counts toward total; invoice-level discount hits parts only).
    #   • subtotal        — pre-discount sum of every line_total
    #   • discount_amount — invoice.discount_pct applied ONCE to the PARTS subtotal
    #   • tax_amount      — cores/freight/fees excluded from the taxable base
    #   • total           — subtotal − discount_amount + tax_amount
    @property
    def subtotal(self) -> float:
        return compute_invoice_totals(self)["subtotal"]

    @property
    def discount_amount(self) -> float:
        return compute_invoice_totals(self)["discount_amount"]

    @property
    def tax_amount(self) -> float:
        return compute_invoice_totals(self)["tax_amount"]

    @property
    def surcharge_amount(self) -> float:
        # R1 — surcharge is now applied AT PAYMENT TIME on the card portion only.
        # This property is preserved as an INFORMATIONAL ESTIMATE — "what surcharge
        # would be if the whole balance is paid by card" — and is NOT added to total.
        if not self.apply_cc_surcharge:
            return 0.0
        return round(self.total * (self.cc_surcharge_pct / 100), 2)

    @property
    def total(self) -> float:
        # R1 — surcharge NOT included in total. See surcharge_amount note above.
        return compute_invoice_totals(self)["total"]

    @property
    def amount_paid(self) -> float:
        # Exclude reversed allocations — NSF / stop-payment reversals must not
        # count as collected money.  Omitting this filter corrupts A/R balances.
        return round(sum(a.amount_applied for a in self.allocations if not a.is_reversed), 2)

    @property
    def balance_due(self) -> float:
        return compute_invoice_totals(self)["balance_due"]

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None

    @property
    def is_overdue(self) -> bool:
        if self.due_date is None or self.status == InvoiceStatus.PAID:
            return False
        return datetime.utcnow() > self.due_date


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )

    # Back-link to the SO line this invoice line fulfills (for partial invoicing)
    so_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("so_lines.id"), nullable=True
    )

    line_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=LineType.PRODUCT
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unit_cost: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # snapshot at time of sale — never changes after lock
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # R5 — true when line discount_pct differs from customer.discount_pct
    discount_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # R1 — per-line tax fields (some lines like freight may be non-taxable)
    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tax_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    is_core_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parent_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_lines.id"), nullable=True
    )
    # ── Auto-generation flags (drives parent↔core cascade behavior) ──────────
    # is_auto_generated: True when the system created this line (e.g. auto-added
    #   core charge when its parent product was added). Drives the cascade-delete
    #   when the parent line is removed.
    # is_locked_to_parent: When True, this line's qty syncs from its parent.
    #   User can manually unlink (set False) with explicit warning, after which
    #   parent edits no longer cascade to this line.
    is_auto_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_locked_to_parent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="lines")
    product: Mapped[Product | None] = relationship(
        "Product", back_populates="invoice_lines"
    )

    # ── Self-referential: core charge lines attach under their parent product line ──
    parent: Mapped[InvoiceLine | None] = relationship(
        "InvoiceLine", back_populates="children", remote_side="InvoiceLine.id",
        foreign_keys="InvoiceLine.parent_line_id",
    )
    children: Mapped[list[InvoiceLine]] = relationship(
        "InvoiceLine", back_populates="parent",
        foreign_keys="InvoiceLine.parent_line_id",
    )

    @property
    def line_total(self) -> float:
        return calc_line_total(self.unit_price, self.qty, self.discount_pct)

    @property
    def margin_pct(self) -> float:
        return calc_margin_pct(self.unit_price, self.unit_cost)


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT  (restructured: no longer tied directly to one invoice)
# ─────────────────────────────────────────────────────────────────────────────

class Payment(QBOSyncMixin, Base):
    """
    Represents a single customer payment event.
    One payment may be applied to multiple invoices via payment_allocations.
    A payment with no allocations is an unapplied credit on the customer account.
    """
    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_customer_id", "customer_id"),
        Index("ix_payments_qbo_sync_status", "qbo_sync_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    # R11 — vendor refunds use vendor_id (Phase G will refactor customer_id → nullable)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    # R3 — SO deposits stamp the Payment with their SO so fulfill_and_invoice can
    # auto-allocate the deposit to the resulting invoice. Nullable for non-SO payments.
    sales_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=True
    )
    payment_date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    payment_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentMethod.CASH
    )
    # R11 — direction governs all downstream behavior. Most payments are INCOMING_FROM_CUSTOMER.
    direction: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PaymentDirection.INCOMING_FROM_CUSTOMER
    )
    check_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    amount_received: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # R1 — surcharge captured at payment time on card portion only (not pre-baked on invoice)
    surcharge_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Status & Reversal ─────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentStatus.APPLIED
    )
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    nsf_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── QBO Sync ─────────────────────────────────────────────────────────────
    # qbo_sync_status, qbo_last_synced_at, qbo_sync_error, qbo_sync_retry_count
    # are inherited from QBOSyncMixin.
    qbo_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Surcharge SalesReceipt id (DocNumber JAKS-SC-{id}) — persisted so a repush
    # checks locally before the query-then-create window (double-click guard).
    qbo_surcharge_receipt_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    customer: Mapped[Customer] = relationship("Customer", back_populates="payments")
    allocations: Mapped[list[PaymentAllocation]] = relationship(
        "PaymentAllocation", back_populates="payment", cascade="all, delete-orphan"
    )

    @property
    def amount_allocated(self) -> float:
        # Mirror Invoice.amount_paid — exclude reversed allocations so an NSF /
        # stop-payment reversal releases the funds back to amount_unallocated
        # instead of stranding them (allocated-but-not-counted-as-paid).
        return round(sum(a.amount_applied for a in self.allocations if not a.is_reversed), 2)

    @property
    def amount_unallocated(self) -> float:
        return round(self.amount_received - self.amount_allocated, 2)


class PaymentAllocation(Base):
    """
    Many-to-many bridge: one Payment applied across one or more Invoices.
    """
    __tablename__ = "payment_allocations"
    __table_args__ = (
        # invoice.amount_paid sums through this table — scanned on every display
        Index("ix_payment_alloc_invoice_id", "invoice_id"),
        # payment.amount_allocated sums through this table
        Index("ix_payment_alloc_payment_id", "payment_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), nullable=False)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    amount_applied: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    allocated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_reversed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    payment: Mapped[Payment] = relationship("Payment", back_populates="allocations")
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="allocations")


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.customer import Customer                     # noqa: E402
from app.models.product import Product                       # noqa: E402
from app.models.quote import SalesOrder, SOLine              # noqa: E402
