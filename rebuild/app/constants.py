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


class DeliveryType(StrEnum):
    LOCAL_DELIVERY = "local_delivery"
    PICKUP         = "pickup"
    SHIP           = "ship"


class AddressType(StrEnum):
    BILLING  = "billing"
    SHIPPING = "shipping"


class CallType(StrEnum):
    INBOUND   = "inbound"
    OUTBOUND  = "outbound"
    EMAIL     = "email"
    IN_PERSON = "in_person"


class CallOutcome(StrEnum):
    QUOTED           = "quoted"
    ORDER_PLACED     = "order_placed"
    NO_ANSWER        = "no_answer"
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
    STOCK    = "stock"
    BACKORDER = "backorder"


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
    PAID       = "paid"


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
    MISC                = "misc"


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


class CoreInspectionOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL  = "partial"


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
    DAMAGED        = "damaged"
    LOST           = "lost"
    CYCLE_COUNT    = "cycle_count"
    VENDOR_SHORTAGE = "vendor_shortage"
    WRITE_OFF      = "write_off"
    OPENING_COUNT  = "opening_count"
    CORRECTION     = "correction"


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
    PAYMENT              = "payment"
    SHIPMENT             = "shipment"
