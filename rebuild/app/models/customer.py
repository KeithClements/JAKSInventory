from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Date, DateTime, Float, Boolean, Integer, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import (
    PaymentTerms, DeliveryType, AddressType, CallType, CallOutcome,
    PreferredContactMethod, SMSConsentMethod,
)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── Primary contact (quick access; full list → customer_contacts) ─────────
    phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── Primary billing address (quick access; full list → customer_addresses) ─
    address_line1: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    address_line2: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    zip_code: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(50), nullable=False, default="US")

    # ── Tax ──────────────────────────────────────────────────────────────────
    is_tax_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tax_exempt_cert_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tax_exempt_cert_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # R10 — cert can expire; system warns but does not hard-block when expired
    tax_exempt_cert_expiry: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    tax_exempt_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Pricing & Terms ───────────────────────────────────────────────────────
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    payment_terms: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentTerms.COD
    )
    interest_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # R1 — monthly interest rate (as %), accrues simple interest on overdue balance
    # after due_date + interest_grace_days
    interest_grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    delivery_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DeliveryType.PICKUP
    )

    # ── Account Balance ───────────────────────────────────────────────────────
    # Running credit balance — increases from core returns, credit memos, overpayments.
    # Decreases when applied to an invoice.
    credit_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── QBO ───────────────────────────────────────────────────────────────────
    qbo_customer_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    internal_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # R10 — preference/quirk notes ("ask for Bob", "text preferred", "needs PO#")
    communication_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Communication Preferences (R12) ───────────────────────────────────────
    preferred_contact_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PreferredContactMethod.PHONE
    )
    allow_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_marketing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── SMS / Email Consent Tracking (R12 — Twilio/A2P compliance) ───────────
    sms_consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sms_consent_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email_consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opt_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    addresses: Mapped[list[CustomerAddress]] = relationship(
        "CustomerAddress", back_populates="customer", cascade="all, delete-orphan"
    )
    contacts: Mapped[list[CustomerContact]] = relationship(
        "CustomerContact", back_populates="customer", cascade="all, delete-orphan"
    )
    call_logs: Mapped[list[CustomerCallLog]] = relationship(
        "CustomerCallLog",
        back_populates="customer",
        order_by="CustomerCallLog.logged_at.desc()",
    )
    quotes: Mapped[list[Quote]] = relationship("Quote", back_populates="customer")
    sales_orders: Mapped[list[SalesOrder]] = relationship(
        "SalesOrder", back_populates="customer"
    )
    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="customer")
    payments: Mapped[list[Payment]] = relationship("Payment", back_populates="customer")
    core_charges: Mapped[list[CoreCharge]] = relationship(
        "CoreCharge", back_populates="customer"
    )
    return_authorizations: Mapped[list[ReturnAuthorization]] = relationship(
        "ReturnAuthorization", back_populates="customer"
    )
    warranty_claims: Mapped[list[WarrantyClaim]] = relationship(
        "WarrantyClaim", back_populates="customer"
    )

    # ── Convenience ───────────────────────────────────────────────────────────
    @property
    def primary_address(self) -> CustomerAddress | None:
        return next((a for a in self.addresses if a.is_primary), None)

    @property
    def primary_contact(self) -> CustomerContact | None:
        return next((c for c in self.contacts if c.is_primary), None)

    @property
    def is_taxable(self) -> bool:
        """Convenience inverse of is_tax_exempt; used by invoice/quote templates."""
        return not self.is_tax_exempt

    @property
    def display_name(self) -> str:
        return self.company_name or self.contact_name or f"Customer #{self.id}"


class CustomerAddress(Base):
    """R11 — multi ship-to addresses per customer."""
    __tablename__ = "customer_addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    address_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AddressType.SHIPPING
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # R11 — default-per-type flags
    is_default_shipping: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default_billing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Address lines — keep `street` for backward compat, add explicit line1/line2
    street: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    street_line2: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    zip_code: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(50), nullable=False, default="US")

    # R11 — per-address contact info (job sites often have a different contact)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    customer: Mapped[Customer] = relationship("Customer", back_populates="addresses")


class CustomerContact(Base):
    __tablename__ = "customer_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_billing_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    customer: Mapped[Customer] = relationship("Customer", back_populates="contacts")


class CustomerCallLog(Base):
    __tablename__ = "customer_call_logs"
    __table_args__ = (
        # compound: covers ORDER BY logged_at DESC on every call log fetch
        Index("ix_customer_call_logs_customer_id_logged_at", "customer_id", "logged_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    logged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    logged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ── CRM Classification ────────────────────────────────────────────────────
    call_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CallType.INBOUND
    )  # CallType
    outcome: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CallOutcome.OTHER
    )  # CallOutcome
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotes.id"), nullable=True
    )

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    customer: Mapped[Customer] = relationship("Customer", back_populates="call_logs")


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.quote import Quote, SalesOrder              # noqa: E402
from app.models.invoice import Invoice, Payment             # noqa: E402
from app.models.core import CoreCharge                      # noqa: E402
from app.models.returns import ReturnAuthorization          # noqa: E402
from app.models.warranty import WarrantyClaim               # noqa: E402
