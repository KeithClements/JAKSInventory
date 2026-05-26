from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, Boolean, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models.mixins import QBOSyncMixin
from app.constants import POStatus, VendorBillStatus, QBOSyncStatus, Carrier


class PurchaseOrder(QBOSyncMixin, Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=POStatus.DRAFT
    )

    # ── Verbal / Phone Order Support ──────────────────────────────────────────
    vendor_confirmation_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # ── Drop-Ship ─────────────────────────────────────────────────────────────
    is_drop_ship: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    drop_ship_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    drop_ship_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_addresses.id"), nullable=True
    )

    # ── Costs ────────────────────────────────────────────────────────────────
    freight_in_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Dates ────────────────────────────────────────────────────────────────
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── QBO Sync ─────────────────────────────────────────────────────────────
    # qbo_sync_status, qbo_last_synced_at, qbo_sync_error, qbo_sync_retry_count
    # are inherited from QBOSyncMixin.
    qbo_bill_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    vendor: Mapped[Vendor] = relationship("Vendor", back_populates="purchase_orders")
    lines: Mapped[list[POLine]] = relationship(
        "POLine", back_populates="po", cascade="all, delete-orphan"
    )
    # Receipts are linked through POReceiptLine.po_id (a receipt can span multiple POs).
    # Access via: db.query(POReceipt).join(POReceiptLine).filter(POReceiptLine.po_id == id)
    receipts: Mapped[list[POReceipt]] = relationship(
        "POReceipt",
        secondary="po_receipt_lines",
        primaryjoin="PurchaseOrder.id == foreign(POReceiptLine.po_id)",
        secondaryjoin="foreign(POReceiptLine.receipt_id) == POReceipt.id",
        viewonly=True,
        overlaps="lines,po_line,receipt",
    )
    bills: Mapped[list[VendorBill]] = relationship("VendorBill", back_populates="po")

    # ── Computed ──────────────────────────────────────────────────────────────
    @property
    def subtotal(self) -> float:
        return round(sum(ln.line_total for ln in self.lines), 2)

    @property
    def core_total(self) -> float:
        return round(
            sum(ln.core_charge_per_unit * ln.qty_ordered for ln in self.lines), 2
        )

    @property
    def total(self) -> float:
        return round(self.subtotal + self.core_total + self.freight_in_cost, 2)

    @property
    def is_fully_received(self) -> bool:
        return all(ln.qty_outstanding == 0 for ln in self.lines)


class POLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    qty_ordered: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    qty_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_billed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    core_charge_per_unit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Allocated freight-in cost per unit (computed by POService after receipt)
    landed_cost_per_unit: Mapped[float | None] = mapped_column(Float, nullable=True)

    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    po: Mapped[PurchaseOrder] = relationship("PurchaseOrder", back_populates="lines")
    product: Mapped[Product | None] = relationship("Product", back_populates="po_lines")
    receipt_lines: Mapped[list[POReceiptLine]] = relationship(
        "POReceiptLine", back_populates="po_line"
    )
    bill_lines: Mapped[list[VendorBillLine]] = relationship(
        "VendorBillLine", back_populates="po_line"
    )

    @property
    def line_total(self) -> float:
        return round(self.unit_cost * self.qty_ordered, 2)

    @property
    def qty_outstanding(self) -> int:
        return self.qty_ordered - self.qty_received

    @property
    def has_billing_discrepancy(self) -> bool:
        return self.qty_billed > self.qty_received


class POReceipt(Base):
    """
    One receiving session — may fulfill lines from multiple POs from the same vendor.
    Inventory only increases via receipt records, never directly from PO creation.
    """
    __tablename__ = "po_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # vendor_id denormalised for "all receipts from vendor" queries
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    received_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    tracking_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Carrier uses the Carrier enum (same values as Shipment.carrier)
    carrier: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default=None
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # One receipt can span lines from multiple POs.
    # The POs involved are found via: [rl.po_line.po for rl in self.lines]
    lines: Mapped[list[POReceiptLine]] = relationship(
        "POReceiptLine", back_populates="receipt", cascade="all, delete-orphan"
    )


class POReceiptLine(Base):
    """
    Each line explicitly references which PO and PO line it fulfills.
    Allows one receipt session to span multiple open POs.
    """
    __tablename__ = "po_receipt_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("po_receipts.id"), nullable=False)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_lines.id"), nullable=False)
    qty_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    condition_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    receipt: Mapped[POReceipt] = relationship("POReceipt", back_populates="lines")
    po_line: Mapped[POLine] = relationship("POLine", back_populates="receipt_lines")


class VendorBill(QBOSyncMixin, Base):
    """
    Vendor's invoice to JAKS. Matched against receipts (3-way match).
    Status = discrepancy when qty_billed > qty_received on any line.
    """
    __tablename__ = "vendor_bills"
    __table_args__ = (
        Index("ix_vendor_bills_qbo_sync_status", "qbo_sync_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=True
    )
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    bill_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bill_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VendorBillStatus.PENDING
    )
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── QBO Sync ──────────────────────────────────────────────────────────────
    # qbo_sync_status, qbo_last_synced_at, qbo_sync_error, qbo_sync_retry_count
    # are inherited from QBOSyncMixin.
    qbo_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    po: Mapped[PurchaseOrder | None] = relationship(
        "PurchaseOrder", back_populates="bills"
    )
    lines: Mapped[list[VendorBillLine]] = relationship(
        "VendorBillLine", back_populates="bill", cascade="all, delete-orphan"
    )

    @property
    def has_discrepancy(self) -> bool:
        return any(ln.has_discrepancy for ln in self.lines)


class VendorBillLine(Base):
    __tablename__ = "vendor_bill_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("vendor_bills.id"), nullable=False)
    po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("po_lines.id"), nullable=True
    )
    qty_billed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    bill: Mapped[VendorBill] = relationship("VendorBill", back_populates="lines")
    po_line: Mapped[POLine | None] = relationship("POLine", back_populates="bill_lines")

    @property
    def line_total(self) -> float:
        return round(self.qty_billed * self.unit_cost, 2)

    @property
    def has_discrepancy(self) -> bool:
        if self.po_line is None:
            return False
        return self.qty_billed > self.po_line.qty_received


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.vendor import Vendor                         # noqa: E402
from app.models.product import Product                       # noqa: E402
from app.models.customer import CustomerAddress              # noqa: E402
