from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Date, DateTime, Float, Boolean, Integer, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import (
    PaymentTerms, DeliveryType, AddressType, ActivityType, CallType, CallOutcome,
    PreferredContactMethod, SMSConsentMethod, CustomerType, CustomerStatus,
    CustomerFlag, CUSTOMER_STORED_FLAGS,
)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        # R3 — DB backstop: non-blank account numbers must be unique. Partial:
        # the '' default (most customers) repeats freely. Name must match
        # _PENDING_UNIQUE_INDEXES in app/database.py (defensive live-DB path).
        Index(
            "uq_customers_account_number", "account_number",
            unique=True,
            sqlite_where=text("account_number IS NOT NULL AND account_number != ''"),
        ),
        # Lead Finder dedup backstop: one customer per real FMCSA DOT. Partial so
        # the NULL default (every non-carrier customer) repeats freely. Name must
        # match _PENDING_UNIQUE_INDEXES in app/database.py (defensive live-DB path).
        Index(
            "uq_customers_usdot_number", "usdot_number",
            unique=True,
            sqlite_where=text("usdot_number IS NOT NULL"),
        ),
        # C10 — DB backstop against duplicate customers on email (case-insensitive).
        # Partial so the '' default (customers with no email on file) repeats
        # freely. Name must match _PENDING_UNIQUE_INDEXES in app/database.py
        # (the defensive live-DB path that probes for existing dupes first).
        Index(
            "uq_customers_email", text("email COLLATE NOCASE"),
            unique=True,
            sqlite_where=text("email IS NOT NULL AND email != ''"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── Lifecycle status (owner-locked 4-state) ──────────────────────────────
    # Separate from is_active for richer filtering. Backfill: is_active==False →
    # 'inactive'; existing active rows → 'active'. credit_hold mirrors the flag
    # but lives here for query-able status-row display.
    customer_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CustomerStatus.ACTIVE
    )

    # ── Classification (P2-D6) ────────────────────────────────────────────────
    # Single customer type from a fixed list; the key that maps to type-driven
    # default profiles (customer_type_defaults). Existing rows default to OTHER.
    customer_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CustomerType.OTHER
    )
    # Human/external account number (e.g. legacy AR code). Free text, not unique.
    account_number: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # FMCSA carrier identity (USDOT #). Ties a customer to its real carrier; the
    # JAK's Lead Finder dedup key (one customer per real DOT — see the partial
    # unique index in __table_args__). NULL for non-carrier customers.
    usdot_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

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
    # Maximum credit extended (0 = no limit enforced in Phase 1; checked in Phase 2).
    credit_limit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Pricing tier: standard | wholesale | fleet | dealer
    pricing_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")

    # O6 — per-customer CC surcharge override. NULL means "use the system
    # default cc_surcharge_pct setting"; a non-NULL value (including 0.0) overrides
    # the system default at invoice creation time, pre-filling the invoice's own
    # cc_surcharge_pct field. The invoice-level field then governs what's shown
    # on the workspace and used in payment calculations.
    card_surcharge_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── QBO ───────────────────────────────────────────────────────────────────
    qbo_customer_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    # ── Structured Flags (P2-D2) ──────────────────────────────────────────────
    # CSV of CustomerFlag values for the manually-managed flags (Requires-PO,
    # Credit-Hold, Call-first, Warranty-escalation). Read through
    # CustomerService.flags_for(), which merges in TAX_EXEMPT / TEXT_PREFERRED
    # derived from is_tax_exempt / preferred_contact_method so a chip can never
    # contradict the canonical column. Never edit this string directly — use
    # CustomerService.set_flag().
    flags: Mapped[str] = mapped_column(String(255), nullable=False, default="")

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

    @property
    def flag_keys(self) -> list[str]:
        """P2-D2 — merged flag key strings for the chips macro. This is the single
        derivation point the UI calls (``customer_flags(customer.flag_keys)``):
        the manually-managed CSV flags PLUS TAX_EXEMPT / TEXT_PREFERRED derived
        from the canonical columns, returned in CustomerFlag (display) order.
        CustomerService.flags_for() delegates here; set_flag / set_stored_flags
        are the writers."""
        active: set[str] = {
            tok.strip()
            for tok in (self.flags or "").split(",")
            if tok.strip() in CUSTOMER_STORED_FLAGS
        }
        if self.is_tax_exempt:
            active.add(CustomerFlag.TAX_EXEMPT)
        if self.preferred_contact_method == PreferredContactMethod.TEXT:
            active.add(CustomerFlag.TEXT_PREFERRED)
        return [f.value for f in CustomerFlag if f in active]


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
    """Unified Activity Log (contract: ACTIVITY_LOG_CONTRACT.md).

    Extends the original call-log table with five columns so that every kind
    of customer interaction (call, text, counter visit, email, note) lands in
    one place with optional follow-up tracking and a polymorphic document link.
    The table name and existing columns are unchanged for backward compatibility;
    new code should use ``log_activity()`` and read ``activity_type`` as the
    primary classification.

    Alias: ``Activity = CustomerCallLog`` is used in new code for readability.
    """
    __tablename__ = "customer_call_logs"
    __table_args__ = (
        # PRIMARY query: ORDER BY logged_at DESC per customer (timeline)
        Index("ix_customer_call_logs_customer_id_logged_at", "customer_id", "logged_at"),
        # Dashboard widget: open follow-ups due by date
        Index("ix_customer_call_logs_follow_up_date", "follow_up_date"),
        # Doc-side panel: all activities linked to one document
        Index("ix_customer_call_logs_entity", "related_entity_type", "related_entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    logged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    logged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ── Original CRM classification (kept for backward compat) ───────────────
    call_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CallType.INBOUND
    )  # direction — only meaningful when activity_type='call'
    outcome: Mapped[str | None] = mapped_column(
        String(30), nullable=True                   # nullable: a NOTE has no outcome
    )  # CallOutcome
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotes.id"), nullable=True       # back-compat alias for related_entity
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Activity Log extensions (+5 columns, ACTIVITY_LOG_CONTRACT.md §1) ─────
    # activity_type: kind of interaction; default 'call' preserves existing rows.
    activity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ActivityType.CALL
    )
    # follow_up_date: "follow up by this date" — drives the dashboard widget.
    follow_up_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    # follow_up_done_at: stamped when AP marks done; NULL = still open/pending.
    follow_up_done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Polymorphic link to a quote, invoice, or PO.
    related_entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    customer: Mapped[Customer] = relationship("Customer", back_populates="call_logs")


# Readable alias for new code (table + schema unchanged).
Activity = CustomerCallLog


class CustomerTypeDefault(Base):
    """P2-D1 — type-driven default profile. One row per CustomerType holds the
    field defaults the new-customer form pre-fills when that type is picked
    (every field still overridable). OTHER is the global fallback. Owner-editable
    later via Settings → Customer Defaults. CustomerService.resolve_defaults()
    reads these (falling back to in-code defaults when a row is absent), so the
    service works with or without the table being seeded.

    NULL semantics: card_surcharge_pct NULL → "use the system cc_surcharge_pct
    setting" (same convention as Customer.card_surcharge_pct)."""
    __tablename__ = "customer_type_defaults"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_type: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)

    payment_terms: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentTerms.COD
    )
    pricing_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credit_limit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_tax_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL = use the system cc_surcharge_pct setting (Customer.card_surcharge_pct convention)
    card_surcharge_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    apply_card_surcharge_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # "status" default — maps to Customer.is_active on create
    status_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # "same as billing" default for the new-customer shipping block
    same_as_billing_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ── Late imports ───────────────────────────────────────────────────────────────
from app.models.quote import Quote, SalesOrder              # noqa: E402
from app.models.invoice import Invoice, Payment             # noqa: E402
from app.models.core import CoreCharge                      # noqa: E402
from app.models.returns import ReturnAuthorization          # noqa: E402
from app.models.warranty import WarrantyClaim               # noqa: E402
