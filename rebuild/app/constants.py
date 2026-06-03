"""
app/constants.py
================
Single source of truth for every status value, type enum, and typed constant
in the JAKS Inventory system.

Every model, service, and router imports from here.
No magic strings anywhere in the codebase.

Python 3.11+ StrEnum — values ARE the string, so comparisons with DB strings work directly.
"""

from enum import StrEnum


# ─── Products ────────────────────────────────────────────────────────────────

class ProductStatus(StrEnum):
    ACTIVE        = "active"
    INACTIVE      = "inactive"
    SUPERSEDED    = "superseded"
    DISCONTINUED  = "discontinued"
    SPECIAL_ORDER = "special_order"


class ProductReturnPolicy(StrEnum):
    STANDARD      = "standard"
    NON_RETURNABLE = "non_returnable"
    SPECIAL_ORDER = "special_order"
    WARRANTY_ONLY = "warranty_only"


class KitType(StrEnum):
    VENDOR_KIT = "vendor_kit"
    CUSTOM_KIT = "custom_kit"


class UnitOfMeasure(StrEnum):
    EA   = "EA"
    SET  = "SET"
    KIT  = "KIT"
    BOX  = "BOX"
    GAL  = "GAL"
    QT   = "QT"
    PAIR = "PAIR"
    FT   = "FT"


class CrossRefType(StrEnum):
    OEM        = "oem"
    COMPETITOR = "competitor"
    VENDOR_ALT = "vendor_alt"


class SerialNumberStatus(StrEnum):
    IN_STOCK = "in_stock"
    SOLD     = "sold"
    RETURNED = "returned"


# ─── Categories ───────────────────────────────────────────────────────────────
# Level values for product_categories self-referential tree (1 = major group,
# 2 = category, 3 = subcategory). Stored as integers — defined here for clarity.
CATEGORY_LEVEL_GROUP    = 1
CATEGORY_LEVEL_CATEGORY = 2
CATEGORY_LEVEL_SUB      = 3


# ─── Vendors ──────────────────────────────────────────────────────────────────

class VendorContactRole(StrEnum):
    SALES      = "sales"
    WARRANTY   = "warranty"
    RETURNS    = "returns"
    ACCOUNTING = "accounting"
    SHIPPING   = "shipping"
    GENERAL    = "general"


class VendorCreditType(StrEnum):
    REBATE             = "rebate"
    PRICE_CORRECTION   = "price_correction"
    DAMAGED_GOODS      = "damaged_goods"
    FREIGHT_ADJUSTMENT = "freight_adjustment"
    PROMOTIONAL        = "promotional"
    RETURN             = "return"
    WARRANTY           = "warranty"
    OTHER              = "other"


class VendorCreditStatus(StrEnum):
    OPEN    = "open"
    APPLIED = "applied"
    CLOSED  = "closed"


class VendorProgramType(StrEnum):
    VOLUME_REBATE = "volume_rebate"
    PROMOTIONAL   = "promotional"
    TIER_DISCOUNT = "tier_discount"
    OTHER         = "other"


# ─── Customers ────────────────────────────────────────────────────────────────

class PaymentTerms(StrEnum):
    COD    = "cod"
    NET_30 = "net_30"
    NET_60 = "net_60"


class PricingTier(StrEnum):
    STANDARD  = "standard"
    WHOLESALE = "wholesale"
    FLEET     = "fleet"
    DEALER    = "dealer"


class CustomerType(StrEnum):
    """P2-D6 — single customer type, fixed list. The key that maps to the
    type-driven default profiles (customer_type_defaults / P2-D1). OTHER is the
    global-fallback profile. Existing customers default to OTHER (unaffected)."""
    FLEET          = "fleet"
    OWNER_OPERATOR = "owner_operator"
    REPAIR_SHOP    = "repair_shop"
    DEALER         = "dealer"
    MUNICIPALITY   = "municipality"
    INTERNAL       = "internal"
    OTHER          = "other"


# Human labels for the Customer Type chips/select (UI reads these; enum stays the
# wire/DB value). Order matches the fixed list in P2-D6.
CUSTOMER_TYPE_LABELS: dict[str, str] = {
    CustomerType.FLEET:          "Fleet",
    CustomerType.OWNER_OPERATOR: "Owner-Operator",
    CustomerType.REPAIR_SHOP:    "Repair Shop",
    CustomerType.DEALER:         "Dealer",
    CustomerType.MUNICIPALITY:   "Municipality",
    CustomerType.INTERNAL:       "Internal",
    CustomerType.OTHER:          "Other",
}


class CustomerFlag(StrEnum):
    """P2-D2 — structured customer flags shown as chips everywhere transactions
    happen (list, preview, detail, quote/SO/invoice workspaces). Flags can drive
    rules later (e.g. Requires-PO warns before finalize). Stored on Customer.flags
    as a CSV of these values; CustomerService.flags_for() is the merged read view
    that also surfaces TAX_EXEMPT / TEXT_PREFERRED derived from the customer's
    canonical columns so a chip can never contradict the underlying field."""
    REQUIRES_PO         = "requires_po"
    CREDIT_HOLD         = "credit_hold"
    TAX_EXEMPT          = "tax_exempt"
    CALL_FIRST          = "call_first"
    TEXT_PREFERRED      = "text_preferred"
    WARRANTY_ESCALATION = "warranty_escalation"


# Human labels for flag chips. Order matches the enum (= display order).
CUSTOMER_FLAG_LABELS: dict[str, str] = {
    CustomerFlag.REQUIRES_PO:         "Requires PO #",
    CustomerFlag.CREDIT_HOLD:         "Credit Hold",
    CustomerFlag.TAX_EXEMPT:          "Tax Exempt",
    CustomerFlag.CALL_FIRST:          "Call First",
    CustomerFlag.TEXT_PREFERRED:      "Text Preferred",
    CustomerFlag.WARRANTY_ESCALATION: "Warranty Escalation",
}


class DeliveryType(StrEnum):
    LOCAL_DELIVERY = "local_delivery"
    PICKUP         = "pickup"
    SHIP           = "ship"


class AddressType(StrEnum):
    BILLING  = "billing"
    SHIPPING = "shipping"
    JOB_SITE = "job_site"   # R11 — multi ship-to support
    OTHER    = "other"


class PreferredContactMethod(StrEnum):
    """R12 — customer communication preference."""
    PHONE = "phone"
    TEXT  = "text"
    EMAIL = "email"


class SMSConsentMethod(StrEnum):
    """R12 — how SMS consent was obtained (Twilio/A2P compliance)."""
    VERBAL       = "verbal"
    SIGNED_FORM  = "signed_form"
    ONLINE_FORM  = "online_form"
    IMPORTED     = "imported"   # legacy customers grandfathered
    OTHER        = "other"


class CallType(StrEnum):
    INBOUND   = "inbound"
    OUTBOUND  = "outbound"
    EMAIL     = "email"
    IN_PERSON = "in_person"


class ActivityType(StrEnum):
    """Kind of customer interaction — the primary field for the unified Activity Log.
    activity_type governs which outcome options are relevant; call_type (direction)
    is a secondary field for calls only. Contract: ACTIVITY_LOG_CONTRACT.md."""
    CALL          = "call"
    TEXT          = "text"
    COUNTER_VISIT = "counter_visit"
    EMAIL         = "email"
    NOTE          = "note"


class CallOutcome(StrEnum):
    QUOTED           = "quoted"
    ORDER_PLACED     = "order_placed"
    NO_ANSWER        = "no_answer"
    REACHED          = "reached"       # Activity Log: call connected, no specific outcome
    VOICEMAIL        = "voicemail"     # Activity Log: left a voicemail
    FOLLOW_UP_NEEDED = "follow_up_needed"
    RESOLVED         = "resolved"
    OTHER            = "other"


# ─── Quotes ───────────────────────────────────────────────────────────────────

class QuoteStatus(StrEnum):
    DRAFT     = "draft"
    SENT      = "sent"
    ACCEPTED  = "accepted"
    DECLINED  = "declined"
    EXPIRED   = "expired"
    CONVERTED = "converted"


class QuoteOutcome(StrEnum):
    PENDING     = "pending"
    WON         = "won"
    LOST        = "lost"
    NO_DECISION = "no_decision"


class LostReason(StrEnum):
    """P2-D7 — structured reason captured when a quote is marked lost. Drives the
    lost_sales_log → win-rate / lost-revenue reporting. COMPETITOR pairs with an
    optional competitor name + price on the log row."""
    PRICE            = "price"
    LEAD_TIME        = "lead_time"
    COMPETITOR       = "competitor"
    NO_LONGER_NEEDED = "no_longer_needed"
    NO_RESPONSE      = "no_response"
    OTHER            = "other"


# Human labels for the Mark-Lost reason picker. Order matches the enum.
LOST_REASON_LABELS: dict[str, str] = {
    LostReason.PRICE:            "Price",
    LostReason.LEAD_TIME:        "Lead time",
    LostReason.COMPETITOR:       "Competitor",
    LostReason.NO_LONGER_NEEDED: "No longer needed",
    LostReason.NO_RESPONSE:      "No response",
    LostReason.OTHER:            "Other",
}


# ─── Sales Orders ─────────────────────────────────────────────────────────────

class SOStatus(StrEnum):
    OPEN      = "open"
    PARTIAL   = "partial"
    HOLD      = "hold"
    FULFILLED = "fulfilled"
    INVOICED  = "invoiced"
    CANCELLED = "cancelled"


class SOPaymentMode(StrEnum):
    FULL    = "full"
    DEPOSIT = "deposit"
    NONE    = "none"


class SOLineSource(StrEnum):
    """Legacy 2-value source — kept for backward compat. New code uses FulfillmentSource."""
    STOCK    = "stock"
    BACKORDER = "backorder"


class FulfillmentSource(StrEnum):
    """R7 — full SO line fulfillment source taxonomy."""
    STOCK         = "stock"
    BACKORDER     = "backorder"
    LINKED_PO     = "linked_po"
    SPECIAL_ORDER = "special_order"
    DROPSHIP      = "dropship"


class SOLineStatus(StrEnum):
    """R7 — SO line state machine, varies by fulfillment_source."""
    # initial states (mirror fulfillment_source)
    STOCK                       = "stock"
    BACKORDER                   = "backorder"
    LINKED_PO                   = "linked_po"
    SPECIAL_ORDER               = "special_order"
    DROPSHIP                    = "dropship"
    # awaiting states
    AWAITING_STOCK              = "awaiting_stock"
    AWAITING_PO_RECEIPT         = "awaiting_po_receipt"
    AWAITING_SPECIAL_ORDER_PO   = "awaiting_special_order_po"
    # dropship flow
    VENDOR_CONFIRMED            = "vendor_confirmed"
    SHIPPED_DIRECT              = "shipped_direct"
    # reservation + terminal
    RESERVED_STOCK              = "reserved_stock"
    FULFILLED                   = "fulfilled"
    INVOICED                    = "invoiced"
    CLOSED                      = "closed"
    CANCELLED                   = "cancelled"


# ─── Invoices ─────────────────────────────────────────────────────────────────

class InvoiceStatus(StrEnum):
    DRAFT   = "draft"
    OPEN    = "open"     # posted / finalised — awaiting payment
    PARTIAL = "partial"  # partially paid
    PAID    = "paid"
    VOID    = "void"


class InvoiceLockReason(StrEnum):
    END_OF_DAY = "end_of_day"
    QBO_SYNC   = "qbo_sync"
    QBO_PUSHED = "qbo_pushed"  # R8 — alias for QBO_SYNC, clearer naming
    PAID       = "paid"
    MANUAL     = "manual"
    FINALIZED  = "finalized"   # set immediately when invoice is finalised to OPEN
    VOIDED     = "voided"      # set when invoice is voided


class LineType(StrEnum):
    PRODUCT             = "product"
    CORE_CHARGE         = "core_charge"
    WARRANTY            = "warranty"        # Extended warranty upsell child line
    SHIPPING            = "shipping"
    FREIGHT             = "freight"
    LOCAL_DELIVERY      = "local_delivery"
    FUEL_SERVICE_CHARGE = "fuel_service_charge"
    DISCOUNT            = "discount"
    RESTOCKING_FEE      = "restocking_fee"
    WARRANTY_CREDIT     = "warranty_credit"  # Warranty claim credit back to customer
    NSF_FEE             = "nsf_fee"
    CC_SURCHARGE        = "cc_surcharge"     # R1 — credit card convenience fee
    TAX                 = "tax"              # R5 — sales tax line
    MISC_FEE            = "misc_fee"         # R5 — generic misc fee
    MISC                = "misc"


# Line types that make up the discountable "parts" subtotal. The invoice-level
# discount (invoice.discount_pct) applies to THESE ONLY — cores are pass-through
# liability and freight is billed at cost. Single source of truth shared by the
# Invoice model's totals and InvoiceService.calculate_totals so the two never drift.
PARTS_LINE_TYPES = frozenset({
    LineType.PRODUCT,
    LineType.MISC,
    LineType.WARRANTY,
})

# Line types billed as freight / delivery — surfaced in their own totals bucket
# (billed at cost, never discounted). Single source of truth shared by the
# Invoice model's totals and InvoiceService.calculate_totals via
# app.invoice_totals.compute_invoice_totals so the two engines never drift.
FREIGHT_LINE_TYPES = frozenset({
    LineType.SHIPPING,
    LineType.FREIGHT,
    LineType.LOCAL_DELIVERY,
    LineType.FUEL_SERVICE_CHARGE,
})

# R5 — line types that NEVER take a discount, even from customer.discount_pct
NON_DISCOUNTABLE_LINE_TYPES = frozenset({
    LineType.CORE_CHARGE,
    LineType.FREIGHT,
    LineType.SHIPPING,
    LineType.LOCAL_DELIVERY,
    LineType.CC_SURCHARGE,
    LineType.TAX,
    LineType.WARRANTY,
    LineType.MISC_FEE,
    LineType.NSF_FEE,
    LineType.RESTOCKING_FEE,
})

# R1 — line types that NEVER take sales tax (Phase 1 default, may vary by
# jurisdiction in Phase 2 when TaxJar is wired). PRODUCT is taxable by default;
# explicitly listed types below are not.
NON_TAXABLE_LINE_TYPES = frozenset({
    LineType.CORE_CHARGE,           # refundable deposit — not a sale
    LineType.TAX,                   # tax doesn't tax itself
    LineType.CC_SURCHARGE,          # itself a fee, not a taxable product
    LineType.NSF_FEE,               # bank fee, not a sale
    LineType.DISCOUNT,              # negative adjustment, not sellable
    LineType.WARRANTY_CREDIT,       # credit, not sellable
    LineType.RESTOCKING_FEE,        # service fee — typically non-taxable
    # Freight/delivery — non-taxable in most B2B parts jurisdictions including CO.
    # Override per-line if a state taxes shipping (e.g. NY, TX inbound).
    LineType.FREIGHT,
    LineType.SHIPPING,
    LineType.LOCAL_DELIVERY,
    LineType.FUEL_SERVICE_CHARGE,
})


class LineRole(StrEnum):
    """
    Describes the relationship role of a quote line within the parent-child tree.
    Distinct from LineType (which describes content).  Multiple lines share a
    parent_line_id; line_role governs which one is included in the quote total
    and how the UI presents alternates.

      primary        — The default top-level product line; is_included=True.
      core           — Core charge child; always included when parent is.
      warranty       — JAKS extended-warranty child; included when selected.
      upgrade_option — Alternate version (Stage 2, OEM replacement, etc.);
                       excluded from total until customer selects it.
      optional       — Add-on (bolts, gaskets, install kit) under a parent;
                       included by default, customer may decline.
      suggested      — Free-add suggestion chip added directly to the quote;
                       included, treated like a primary line.
    """
    PRIMARY        = "primary"
    CORE           = "core"
    WARRANTY       = "warranty"
    UPGRADE_OPTION = "upgrade_option"
    OPTIONAL       = "optional"
    SUGGESTED      = "suggested"


class SuggestedSellType(StrEnum):
    RECOMMENDED = "recommended"   # Default — show as chip, pre-selected in slide-over
    REQUIRED    = "required"      # Always add (install kits, head bolts, etc.)
    OPTIONAL    = "optional"      # Low-confidence / customer preference
    WARRANTY    = "warranty"      # Triggers warranty tier picker when clicked


# ─── Purchase Orders ──────────────────────────────────────────────────────────

class POStatus(StrEnum):
    VERBAL_ORDER = "verbal_order"
    DRAFT        = "draft"
    SENT         = "sent"
    PARTIAL      = "partial"
    RECEIVED     = "received"
    BILLED       = "billed"
    CANCELLED    = "cancelled"


class VendorBillStatus(StrEnum):
    PENDING     = "pending"
    APPROVED    = "approved"
    DISCREPANCY = "discrepancy"
    PAID        = "paid"


class MatchResolution(StrEnum):
    """
    AP decision recorded on a POLine after 3-way match surfaces a variance.
    UNRESOLVED is the default; terminal values clear the is_flag on the line.
    """
    UNRESOLVED = "unresolved"  # default; variance still live
    ACCEPTED   = "accepted"   # variance acknowledged; will pay as billed
    REJECTED   = "rejected"   # disputed; waiting for corrected bill / credit
    ON_HOLD    = "on_hold"    # parked pending more info
    CREDITED   = "credited"   # offset by a linked vendor credit memo
    CLEARED    = "cleared"    # manually dismissed (corrected outside system)


# ─── Payments ─────────────────────────────────────────────────────────────────

class PaymentMethod(StrEnum):
    CASH           = "cash"
    CHECK          = "check"
    CREDIT_CARD    = "credit_card"
    ACH            = "ach"
    WIRE           = "wire"
    ACCOUNT_CREDIT = "account_credit"  # applied from customer.credit_balance
    OTHER          = "other"


class PaymentStatus(StrEnum):
    APPLIED  = "applied"
    REVERSED = "reversed"
    NSF      = "nsf"


class PaymentReversalReason(StrEnum):
    NSF          = "nsf"
    STOP_PAYMENT = "stop_payment"
    ERROR        = "error"


# ─── Research Items ───────────────────────────────────────────────────────────

class ResearchStatus(StrEnum):
    OPEN     = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class ResearchUrgency(StrEnum):
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"


class ResearchActivityType(StrEnum):
    NOTE         = "note"
    CALLED_VENDOR = "called_vendor"
    CALLED_DEALER = "called_dealer"
    FOUND_PART   = "found_part"
    CROSS_ADDED  = "cross_added"
    STATUS_CHANGE = "status_change"


class CrossRefStatus(StrEnum):
    RESEARCHING       = "researching"
    FOUND             = "found"
    PROVEN            = "proven"
    DEALER_CONFIRMED  = "dealer_confirmed"
    VENDOR_CONFIRMED  = "vendor_confirmed"
    BAD_CROSS         = "bad_cross"
    OBSOLETE          = "obsolete"


# ─── Quote Follow-Up ──────────────────────────────────────────────────────────

class QuoteFollowupStatus(StrEnum):
    PENDING    = "pending"
    CALLED     = "called"
    LEFT_VM    = "left_vm"
    EMAILED    = "emailed"
    INTERESTED = "interested"
    DECLINED   = "declined"
    WON        = "won"


# ─── Core Charges ─────────────────────────────────────────────────────────────

class CoreDirection(StrEnum):
    CUSTOMER_OWES_RETURN = "customer_owes_return"
    VENDOR_OWES_CREDIT   = "vendor_owes_credit"


class CoreStatus(StrEnum):
    OPEN             = "open"
    PARTIAL          = "partial"
    RETURNED         = "returned"
    CREDITED         = "credited"
    SHIPPED_TO_VENDOR = "shipped_to_vendor"
    VENDOR_ACCEPTED  = "vendor_accepted"
    VENDOR_REJECTED  = "vendor_rejected"
    CLOSED           = "closed"


class CoreVendorStatus(StrEnum):
    PENDING  = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CoreDenialResolution(StrEnum):
    ABSORBED_BY_JAKS     = "absorbed_by_jaks"
    CHARGED_TO_CUSTOMER  = "charged_to_customer"
    DISPUTED             = "disputed"


class CoreCreditMethod(StrEnum):
    ACCOUNT_CREDIT = "account_credit"
    CHECK          = "check"
    HOLD           = "hold"     # record intent; no financial action yet


class CoreInspectionOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL  = "partial"
    HOLD     = "hold"      # received, not yet accepted/rejected — credit deferred


class CoreSlipStatus(StrEnum):
    OPEN    = "open"
    PRINTED = "printed"
    CLOSED  = "closed"


class VCRStatus(StrEnum):
    DRAFT        = "draft"
    SHIPPED      = "shipped"
    VENDOR_REVIEW = "vendor_review"
    CREDITED     = "credited"
    DISPUTED     = "disputed"
    CLOSED       = "closed"


class VCRLineOutcome(StrEnum):
    PENDING  = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL  = "partial"


# ─── Returns ──────────────────────────────────────────────────────────────────

class RAStatus(StrEnum):
    DRAFT    = "draft"
    OPEN     = "open"
    RECEIVED = "received"
    CLOSED   = "closed"


class ReturnDisposition(StrEnum):
    RETURN_TO_STOCK  = "return_to_stock"
    QUARANTINE       = "quarantine"
    VENDOR_RETURN    = "vendor_return"
    DAMAGED          = "damaged"
    WARRANTY_REVIEW  = "warranty_review"
    SCRAP            = "scrap"


# ─── Warranty Claims ──────────────────────────────────────────────────────────

class WarrantyStatus(StrEnum):
    DRAFT               = "draft"
    SUBMITTED_TO_VENDOR = "submitted_to_vendor"
    VENDOR_APPROVED     = "vendor_approved"
    VENDOR_DENIED       = "vendor_denied"
    CUSTOMER_CREDITED   = "customer_credited"
    CUSTOMER_NOTIFIED   = "customer_notified"
    CLOSED              = "closed"


class WarrantyDecision(StrEnum):
    PENDING  = "pending"
    APPROVED = "approved"
    PARTIAL  = "partial"   # vendor approved some lines, denied others
    DENIED   = "denied"


class WarrantyResolution(StrEnum):
    CREDIT         = "credit"
    REPLACEMENT    = "replacement"
    PARTIAL_CREDIT = "partial_credit"
    DENIED         = "denied"


# ─── Inventory ────────────────────────────────────────────────────────────────

class InventoryTxnType(StrEnum):
    PO_RECEIPT         = "po_receipt"
    INVOICE_SALE       = "invoice_sale"
    RETURN_TO_STOCK    = "return_to_stock"
    SO_COMMITTED       = "so_committed"
    SO_RELEASED        = "so_released"
    MANUAL_ADJUSTMENT  = "manual_adjustment"
    TRANSFER           = "transfer"
    WRITE_OFF          = "write_off"
    INITIAL_COUNT      = "initial_count"
    DROP_SHIP_SALE     = "drop_ship_sale"
    CORRECTION         = "correction"


class AdjustmentReason(StrEnum):
    """R6 — full inventory adjustment reason taxonomy."""
    CYCLE_COUNT_OVER       = "cycle_count_over"
    CYCLE_COUNT_SHORT      = "cycle_count_short"
    CYCLE_COUNT            = "cycle_count"       # legacy, kept for backward compat
    DAMAGED                = "damaged"
    LOST                   = "lost"
    FOUND                  = "found"
    VENDOR_RETURN          = "vendor_return"
    VENDOR_SHORTAGE        = "vendor_shortage"   # legacy
    WARRANTY_SCRAP         = "warranty_scrap"
    CORE_SCRAP             = "core_scrap"
    INTERNAL_USE           = "internal_use"
    INITIAL_INVENTORY_LOAD = "initial_inventory_load"
    OPENING_COUNT          = "opening_count"     # legacy
    WRITE_OFF              = "write_off"
    CORRECTION             = "correction"
    OTHER                  = "other"


# R6 — reasons that REQUIRE a free-text note before save.
ADJUSTMENT_REASONS_REQUIRING_NOTE = frozenset({
    AdjustmentReason.OTHER,
    AdjustmentReason.CORRECTION,
})


class InventoryLocationType(StrEnum):
    SHOP              = "shop"
    WAREHOUSE         = "warehouse"
    VENDOR_DROP_SHIP  = "vendor_drop_ship"
    QUARANTINE        = "quarantine"
    CORE_STAGING      = "core_staging"


# ─── Shipping ─────────────────────────────────────────────────────────────────

class ShipmentStatus(StrEnum):
    DRAFT     = "draft"
    PACKED    = "packed"
    SHIPPED   = "shipped"
    DELIVERED = "delivered"
    LOST      = "lost"
    CANCELLED = "cancelled"


class Carrier(StrEnum):
    UPS              = "ups"
    FEDEX            = "fedex"
    LTL              = "ltl"
    LOCAL_DELIVERY   = "local_delivery"
    VENDOR_DROP_SHIP = "vendor_drop_ship"
    CUSTOMER_PICKUP  = "customer_pickup"


# ─── Users & Audit ────────────────────────────────────────────────────────────

class UserRole(StrEnum):
    ADMIN       = "admin"
    BOOKKEEPING = "bookkeeping"
    SALES       = "sales"
    READ_ONLY   = "read_only"


class AuditAction(StrEnum):
    CREATED            = "created"
    EDITED             = "edited"
    LOCKED             = "locked"
    VOIDED             = "voided"
    DELETED            = "deleted"
    PAYMENT_APPLIED    = "payment_applied"
    PAYMENT_REVERSED   = "payment_reversed"
    QBO_SYNCED         = "qbo_synced"
    INVENTORY_ADJUSTED = "inventory_adjusted"
    NSF                = "nsf"
    CORE_RECEIVED      = "core_received"
    SO_CONVERTED       = "so_converted"
    INVOICE_CONVERTED  = "invoice_converted"
    STATUS_CHANGED     = "status_changed"
    MATCH_RESOLVED     = "match_resolved"


# ─── Scraper / Enrichment ─────────────────────────────────────────────────────

class ScraperSourceType(StrEnum):
    VENDOR      = "vendor"       # PAI, HHP, ATL — Phase 1
    COMPETITOR  = "competitor"   # FleetPride, etc. — Phase 2/3
    OEM         = "oem"          # OEM reference sites
    MARKETPLACE = "marketplace"  # eBay, Amazon — Phase 3


class ScrapeSearchType(StrEnum):
    VENDOR_PART = "vendor_part"
    OEM         = "oem"
    KEYWORD     = "keyword"
    ESN         = "esn"
    VIN         = "vin"


class ScrapeRunStatus(StrEnum):
    RUNNING  = "running"
    SUCCESS  = "success"
    PARTIAL  = "partial"   # some results, but incomplete
    FAILED   = "failed"


class ScrapedItemReviewStatus(StrEnum):
    PENDING  = "pending"   # awaiting Keith's review
    ACCEPTED = "accepted"  # applied to product record
    REJECTED = "rejected"  # wrong part / bad data
    IGNORED  = "ignored"   # deliberately skipped


# ─── QBO / Integrations ───────────────────────────────────────────────────────

class QBOSyncStatus(StrEnum):
    PENDING = "pending"
    SYNCED  = "synced"
    ERROR   = "error"
    SKIPPED = "skipped"


# ─── Documents ────────────────────────────────────────────────────────────────

class DocumentType(StrEnum):
    INVOICE                   = "invoice"
    QUOTE                     = "quote"
    SALES_ORDER               = "sales_order"
    PURCHASE_ORDER            = "purchase_order"
    RETURN_AUTHORIZATION      = "return_authorization"
    WARRANTY_CLAIM            = "warranty_claim"
    CUSTOMER_CORE_RETURN_SLIP = "customer_core_return_slip"
    VENDOR_CORE_RETURN_SHEET  = "vendor_core_return_sheet"


# ─── Entity Types (polymorphic FK — used by audit_log, document_attachments) ──

class EntityType(StrEnum):
    PRODUCT              = "product"
    CUSTOMER             = "customer"
    VENDOR               = "vendor"
    QUOTE                = "quote"
    SALES_ORDER          = "sales_order"
    INVOICE              = "invoice"
    PURCHASE_ORDER       = "purchase_order"
    PO_RECEIPT           = "po_receipt"
    RETURN_AUTHORIZATION = "return_authorization"
    WARRANTY_CLAIM       = "warranty_claim"
    CORE_CHARGE          = "core_charge"
    CORE_SLIP            = "core_slip"
    PAYMENT              = "payment"
    SHIPMENT             = "shipment"
    CREDIT_MEMO          = "credit_memo"
    VENDOR_CREDIT_MEMO   = "vendor_credit_memo"
    VENDOR_RETURN        = "vendor_return"
    CUSTOMER_STATEMENT   = "customer_statement"
    RESEARCH_ITEM        = "research_item"
    INVENTORY_ADJUSTMENT = "inventory_adjustment"
    INVENTORY_TRANSFER   = "inventory_transfer"
    COMMUNICATION        = "communication"


# ─── Warranty Types (R4) ─────────────────────────────────────────────────────

class WarrantyType(StrEnum):
    """R4 — credit source for warranty claims."""
    VENDOR        = "vendor"          # vendor reimburses JAKS
    JAKS_EXTENDED = "jaks_extended"   # JAKS absorbs / warranty reserve


# ─── Payments (R11) ──────────────────────────────────────────────────────────

class PaymentDirection(StrEnum):
    """R11 — direction of money movement. Most payments are from customers."""
    INCOMING_FROM_CUSTOMER = "incoming_from_customer"
    REFUND_TO_CUSTOMER     = "refund_to_customer"
    INCOMING_FROM_VENDOR   = "incoming_from_vendor"
    REFUND_TO_VENDOR       = "refund_to_vendor"


# ─── Credit Memos (R8) ───────────────────────────────────────────────────────

class CreditMemoTrigger(StrEnum):
    """R8 — what caused the credit memo to be issued."""
    MANUAL                    = "manual"
    ACCEPTED_RA               = "accepted_ra"
    APPROVED_WARRANTY         = "approved_warranty"
    LOCKED_INVOICE_CORRECTION = "locked_invoice_correction"
    OVERCHARGE                = "overcharge"
    PRICING                   = "pricing"


class CreditMemoStatus(StrEnum):
    OPEN     = "open"
    APPLIED  = "applied"   # fully allocated
    PARTIAL  = "partial"   # some applied, some remains unapplied
    REVERSED = "reversed"


class VendorCreditMemoTrigger(StrEnum):
    """R11 — what caused vendor credit memo."""
    OVERCHARGE              = "overcharge"
    VENDOR_RETURN_ACCEPTED  = "vendor_return_accepted"
    DEFECTIVE               = "defective"
    CORE_DISPUTE            = "core_dispute"
    FREIGHT_ADJUSTMENT      = "freight_adjustment"
    WARRANTY_CREDIT         = "warranty_credit"


class VendorReturnStatus(StrEnum):
    """R11 — vendor return (non-core merchandise return to vendor)."""
    DRAFT     = "draft"
    SHIPPED   = "shipped"
    ACCEPTED  = "accepted"
    REJECTED  = "rejected"
    PARTIAL   = "partial"
    CLOSED    = "closed"


class VendorReturnLineOutcome(StrEnum):
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    REJECTED  = "rejected"
    PARTIAL   = "partial"


# ─── Notifications (R8) ──────────────────────────────────────────────────────

class NotificationSeverity(StrEnum):
    """R8 — severity tier for in-app + email notifications."""
    INFO     = "info"
    WARNING  = "warning"
    ERROR    = "error"
    CRITICAL = "critical"


class NotificationType(StrEnum):
    """R8 — notification kind. Drives icon/routing/template."""
    VENDOR_BILL_DISCREPANCY    = "vendor_bill_discrepancy"
    PO_OVER_RECEIPT            = "po_over_receipt"
    INVENTORY_ADJUSTMENT       = "inventory_adjustment"
    CORE_OVERDUE               = "core_overdue"
    WARRANTY_VENDOR_DECISION   = "warranty_vendor_decision"
    QBO_PUSH_FAILURE           = "qbo_push_failure"
    QBO_TOKEN_EXPIRED          = "qbo_token_expired"
    LOW_STOCK                  = "low_stock"
    INVOICE_OVER_THRESHOLD     = "invoice_over_threshold"
    PAYMENT_OVER_THRESHOLD     = "payment_over_threshold"
    SCRAPER_FAILURE            = "scraper_failure"
    NEGATIVE_INVENTORY_OVERRIDE = "negative_inventory_override"
    QUOTE_FOLLOWUP_DUE         = "quote_followup_due"
    INVOICE_OVERDUE            = "invoice_overdue"
    SPECIAL_ORDER_ARRIVED      = "special_order_arrived"


# ─── Communications (R12) ────────────────────────────────────────────────────

class CommunicationChannel(StrEnum):
    """R12 — outbound channel for customer communication."""
    EMAIL        = "email"
    SMS          = "sms"
    PHONE_CALL   = "phone_call"
    MANUAL_NOTE  = "manual_note"   # logged but not transmitted


class CommunicationDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND  = "inbound"


class CommunicationStatus(StrEnum):
    """R12 — delivery state of a communication. Phase 1 default is LOGGED_ONLY."""
    QUEUED       = "queued"
    SENT         = "sent"
    DELIVERED    = "delivered"
    FAILED       = "failed"
    BOUNCED      = "bounced"
    LOGGED_ONLY  = "logged_only"   # Phase 1 — NullProvider just records the attempt


# ─── Permissions (R11) ───────────────────────────────────────────────────────

class Permission(StrEnum):
    """R11 — discrete permission flags used by services to gate actions."""
    INVENTORY_ADJUST            = "inventory_adjust"
    NEGATIVE_INVENTORY_OVERRIDE = "negative_inventory_override"
    VOID_LOCKED_INVOICE         = "void_locked_invoice"
    REPUSH_QBO                  = "repush_qbo"
    MERGE_CUSTOMERS             = "merge_customers"
    CHANGE_SETTINGS             = "change_settings"
    VIEW_AUDIT_LOG              = "view_audit_log"
    DEACTIVATE_MASTER           = "deactivate_master"
    REVERSE_PAYMENT             = "reverse_payment"
    ISSUE_CREDIT_MEMO           = "issue_credit_memo"
    SEND_SMS                    = "send_sms"
    SEND_EMAIL                  = "send_email"
    RECEIVE_WITHOUT_PO          = "receive_without_po"
    INVENTORY_TRANSFER          = "inventory_transfer"
    APPROVE_VENDOR_BILL         = "approve_vendor_bill"
