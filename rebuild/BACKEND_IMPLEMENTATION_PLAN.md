# JAKS Inventory — Backend Implementation Plan
*Compiled: 2026-05-27 from 11-round backend interview with Keith*
*Status: ACTIVE — governs all backend work while UI is built in parallel*
*Source of truth for all backend rules. Supersedes prior interview notes.*

---

## Purpose

Keith is building the UI frontend. This document captures every backend rule,
schema change, and service method that can be implemented without touching UI
files. Every section here is "safe to work on" — none of it modifies templates.

---

## Table of Contents

1. [Schema Changes by Table](#schema-changes-by-table)
2. [New Tables](#new-tables)
3. [New / Updated Constants & Enums](#new--updated-constants--enums)
4. [Settings Keys (new defaults)](#settings-keys-new-defaults)
5. [Service-Layer Behavior Changes](#service-layer-behavior-changes)
6. [New Service Methods](#new-service-methods)
7. [Implementation Priority Order](#implementation-priority-order)
8. [Open Assumptions to Confirm](#open-assumptions-to-confirm)

---

## Schema Changes by Table

### `customers`
| Field | Type | Default | Notes |
|---|---|---|---|
| `interest_grace_days` | INT | 10 | Days past due_date before interest starts (R1) |
| `tax_exempt_cert_expiry` | DATE | NULL | Cert can expire — warn on use, no hard block (R10) |
| `tax_exempt_notes` | TEXT | NULL | Optional cert notes/file path |
| `communication_notes` | TEXT | NULL | "Ask for Bob", "Text preferred", "Do not email invoices" (R10) |
| `version` / `updated_at` | DATETIME | now() | Used for optimistic locking on financial records |
| `preferred_contact_method` | TEXT | 'phone' | phone \| text \| email (R12) |
| `allow_sms` | BOOL | false | Default off until consent obtained (R12) |
| `allow_email` | BOOL | true | |
| `allow_marketing` | BOOL | false | Future marketing communications |
| `do_not_contact` | BOOL | false | Master kill switch — blocks all outbound (R12) |
| `sms_consent_at` | DATETIME | NULL | Twilio/A2P compliance (R12) |
| `sms_consent_method` | TEXT | NULL | how_obtained: verbal, signed_form, online_form, etc. |
| `email_consent_at` | DATETIME | NULL | |
| `opt_out_at` | DATETIME | NULL | If customer opts out, set this + clear allow_* flags |

### `products`
| Field | Type | Default | Notes |
|---|---|---|---|
| `qty_committed` | INT | 0 | Reserved by SOs (R6) |
| `qty_on_po` | INT | 0 | Incoming from open POs |
| `qty_backordered` | INT | 0 | Customer demand exceeding on-hand |
| `qty_available` | computed | — | `qty_on_hand - qty_committed` (property, not column) |
| `last_cost` | REAL | 0 | Most recent receipt unit cost (separate from avg) (R11) |
| `successful_sale_count` | INT | 0 | For cross-ref auto-promote suggestion (R5) — NOTE: belongs on `cross_references` not products |

### `cross_references`
| Field | Type | Default | Notes |
|---|---|---|---|
| `successful_sale_count` | INT | 0 | Bump on each invoice finalize that used this cross-ref. After 3, system suggests "promote to Proven" (R5) |
| `replacement_product_id` | INT FK | NULL | For `obsolete` cross-refs — shows "Use replacement [part #]" (R5) |

### `invoices`
| Field | Type | Default | Notes |
|---|---|---|---|
| `tax_rate_snapshot` | REAL | 0 | Snapshot from customer at creation (R1) |
| `tax_exempt_snapshot` | BOOL | false | Snapshot from customer at creation |
| `tax_amount` | REAL | 0 | Total tax for invoice |
| `tax_jurisdiction` | TEXT | NULL | State/jurisdiction (Phase 1: copy from customer/company) (R10) |
| `qbo_id` | TEXT | NULL | QBO record ID after push |
| `qbo_sync_status` | TEXT | 'pending' | pending \| pushed \| failed |
| `qbo_pushed_at` | DATETIME | NULL | |
| `qbo_last_error` | TEXT | NULL | |
| `qbo_retry_count` | INT | 0 | |
| `ship_to_address_id` | INT FK | NULL | To `customer_addresses` (R11) |
| `ship_to_snapshot` | JSON | NULL | Address frozen at finalization |
| `version` / `updated_at` | DATETIME | now() | Optimistic lock (R9) |

### `invoice_lines`
| Field | Type | Default | Notes |
|---|---|---|---|
| `is_taxable` | BOOL | true | Per-line tax flag (R1) |
| `tax_amount` | REAL | 0 | Per-line tax computed from tax_rate_snapshot |
| `discount_overridden` | BOOL | false | Set true when user changes from customer default (R5) |

### `quotes`
| Field | Type | Default | Notes |
|---|---|---|---|
| `is_duplicate_of_quote_id` | INT FK | NULL | For "Duplicate Quote" action (R5) |
| `version` / `updated_at` | DATETIME | now() | Optimistic lock for any quote-to-doc conversion |

### `sales_orders`
| Field | Type | Default | Notes |
|---|---|---|---|
| `version` / `updated_at` | DATETIME | now() | Optimistic lock (R9) |
| `qbo_id`, `qbo_sync_status`, etc. | — | — | Same suite as invoices |

### `so_lines`
| Field | Type | Default | Notes |
|---|---|---|---|
| `fulfillment_source` | TEXT | 'stock' | stock \| backorder \| linked_po \| special_order \| dropship (R7) |
| `status` | TEXT | 'stock' | Per state-flow diagram (R7) |
| `linked_po_line_id` | INT FK | NULL | Set when fulfillment_source=linked_po |
| `qty_committed` | INT | 0 | |
| `qty_fulfilled` | INT | 0 | |
| `qty_invoiced` | INT | 0 | |
| `qty_cancelled` | INT | 0 | |
| `cancel_reason` | TEXT | NULL | |

### `purchase_orders`
| Field | Type | Default | Notes |
|---|---|---|---|
| `is_dropship` | BOOL | false | (R7) |
| `is_verbal_order` | BOOL | false | |
| `vendor_confirmation_number` | TEXT | NULL | |
| `qbo_id`, `qbo_sync_status`, etc. | — | — | |
| `version` / `updated_at` | DATETIME | now() | Optimistic lock |

### `po_lines`
| Field | Type | Default | Notes |
|---|---|---|---|
| `over_received` | BOOL | false | Set true when receipts exceed ordered (R6) |
| `over_received_qty` | INT | 0 | Cumulative over-received |
| `qty_cancelled` | INT | 0 | (R6) |
| `cancel_reason` | TEXT | NULL | |
| `cancelled_at` | DATETIME | NULL | |
| `cancelled_by_id` | INT FK | NULL | |

### `payments`
| Field | Type | Default | Notes |
|---|---|---|---|
| `payment_direction` | TEXT | 'incoming_from_customer' | + refund_to_customer, incoming_from_vendor, refund_to_vendor (R11) |
| `qbo_id`, `qbo_sync_status`, etc. | — | — | |
| `version` / `updated_at` | DATETIME | now() | Optimistic lock |

### `core_charges`
| Field | Type | Default | Notes |
|---|---|---|---|
| `location_id` | INT FK | NULL | To `core_locations` (R10) |
| `grace_days_snapshot` | INT | NULL | Captured at issue time |
| `is_overdue` | BOOL | false | Set true when past grace window (R3) |

### `warranty_claims`
| Field | Type | Default | Notes |
|---|---|---|---|
| `warranty_type` | TEXT | 'vendor' | vendor \| jaks_extended (R4) |

### `audit_logs`
| Field | Type | Default | Notes |
|---|---|---|---|
| `field_name` | TEXT | NULL | For per-field change tracking (R6) |
| `old_value` | TEXT | NULL | Stringified |
| `new_value` | TEXT | NULL | |

---

## New Tables

### `customer_addresses`
```sql
id              INTEGER PRIMARY KEY
customer_id     INTEGER NOT NULL FK customers(id)
address_type    TEXT NOT NULL  -- billing | shipping | job_site | other
label           TEXT           -- "Main shop", "Job site #3"
street_line1    TEXT
street_line2    TEXT
city            TEXT
state           TEXT
zip_code        TEXT
contact_name    TEXT
phone           TEXT
is_default_shipping  BOOL DEFAULT false
is_default_billing   BOOL DEFAULT false
is_active       BOOL DEFAULT true
created_at, updated_at
```

### `inventory_transactions`
```sql
id              INTEGER PRIMARY KEY
product_id      INTEGER FK products(id)
qty_delta       INTEGER  -- positive or negative
transaction_type  TEXT  -- po_receipt | invoice_finalize | invoice_void |
                        --  adjustment | transfer | rma_accept |
                        --  so_commit | so_release | initial_load |
                        --  manual_no_po_receipt | core_scrap
ref_doc_type    TEXT
ref_doc_id      INTEGER
reason          TEXT
note            TEXT
unit_cost       REAL          -- relevant for receipts (drives avg cost)
before_qty      INTEGER
after_qty       INTEGER
user_id         INTEGER FK
created_at      DATETIME
```

### `inventory_transfers`
```sql
id              INTEGER PRIMARY KEY
product_id      INTEGER FK
qty             INTEGER
source_location_id  INTEGER
dest_location_id    INTEGER
reason          TEXT
note            TEXT
ref_doc_type    TEXT
ref_doc_id      INTEGER
performed_by_user_id  INTEGER FK
created_at      DATETIME
```

### `credit_memos`
```sql
id              INTEGER PRIMARY KEY
cm_number       TEXT UNIQUE  -- CM-2026-XXXX
customer_id     INTEGER FK
original_invoice_id  INTEGER FK NULL
trigger_type    TEXT  -- manual | accepted_ra | approved_warranty |
                       -- locked_invoice_correction | overcharge | pricing
total_amount    REAL
applied_amount  REAL DEFAULT 0
unapplied_amount  REAL DEFAULT 0
tax_amount      REAL DEFAULT 0
status          TEXT  -- open | applied | reversed
reason          TEXT
notes           TEXT
created_at      DATETIME
locked_at       DATETIME
qbo_id, qbo_sync_status, etc.
version, updated_at
```

### `credit_memo_lines`
```sql
id              INTEGER PRIMARY KEY
credit_memo_id  INTEGER FK
original_invoice_line_id  INTEGER FK NULL
product_id      INTEGER FK NULL
description     TEXT
qty             INTEGER
unit_price      REAL
tax_amount      REAL
total_amount    REAL
```

### `credit_memo_allocations`
```sql
id              INTEGER PRIMARY KEY
credit_memo_id  INTEGER FK
invoice_id      INTEGER FK
amount_applied  REAL
applied_at      DATETIME
applied_by_user_id  INTEGER FK
```

### `vendor_credit_memos`
```sql
id              INTEGER PRIMARY KEY
vcm_number      TEXT UNIQUE  -- VCM-2026-XXXX
vendor_id       INTEGER FK
original_vendor_bill_id  INTEGER FK NULL
trigger_type    TEXT  -- overcharge | vendor_return_accepted | defective |
                       -- core_dispute | freight_adjustment | warranty_credit
total_amount    REAL
applied_amount  REAL DEFAULT 0
unapplied_amount  REAL DEFAULT 0
status          TEXT  -- open | applied | reversed
reason          TEXT
notes           TEXT
created_at, qbo fields
```

### `vendor_credit_memo_allocations`
```sql
id              INTEGER PRIMARY KEY
vcm_id          INTEGER FK
vendor_bill_id  INTEGER FK
amount_applied  REAL
```

### `vendor_returns` (RA to vendor, NOT cores)
```sql
id              INTEGER PRIMARY KEY
vr_number       TEXT UNIQUE  -- VR-2026-XXXX
vendor_id       INTEGER FK
original_po_id  INTEGER FK NULL
original_vendor_bill_id  INTEGER FK NULL
reason          TEXT
status          TEXT  -- draft | shipped | accepted | rejected | partial | closed
expected_credit REAL
actual_credit   REAL
credit_difference  REAL
restocking_fee  REAL
tracking_number TEXT
rma_number      TEXT
shipped_at      DATETIME
vendor_decision_at  DATETIME
created_at, created_by_user_id, version
```

### `vendor_return_lines`
```sql
id, vendor_return_id FK, product_id FK, qty, expected_unit_credit, actual_unit_credit, vendor_outcome
```

### `customer_statements`
```sql
id              INTEGER PRIMARY KEY
statement_number  TEXT UNIQUE  -- ST-2026-XXXX
customer_id     INTEGER FK
generated_at    DATETIME
date_range_start  DATE
date_range_end    DATE
opening_balance  REAL
closing_balance  REAL
total_invoiced  REAL
total_paid      REAL
total_credits_applied  REAL
total_interest_accrued  REAL
current_due, due_30, due_60, due_90, due_120  -- aging buckets
pdf_path        TEXT NULL
snapshot_json   TEXT  -- full snapshot of line-by-line data
created_by_user_id  INTEGER FK
```

### `notifications`
```sql
id              INTEGER PRIMARY KEY
user_id         INTEGER FK NULL  -- NULL = system-wide
severity        TEXT  -- info | warning | error | critical
type            TEXT  -- vendor_bill_discrepancy | po_over_receipt | etc.
entity_type     TEXT NULL
entity_id       INTEGER NULL
message         TEXT
created_at      DATETIME
acknowledged_at DATETIME NULL
acknowledged_by_user_id  INTEGER NULL
dismissed_at    DATETIME NULL
```

### `core_locations` (already in plan, confirm)
```sql
id              INTEGER PRIMARY KEY
name            TEXT  -- "Core Shelf", "Core Holding", "Ready for PAI", etc.
vendor_id       INTEGER FK NULL  -- for "Ready for [Vendor]" locations
is_in_transit   BOOL DEFAULT false
is_active       BOOL DEFAULT true
display_order   INTEGER
```

### `core_location_movements`
```sql
id              INTEGER PRIMARY KEY
core_charge_id  INTEGER FK
from_location_id  INTEGER FK NULL
to_location_id    INTEGER FK
moved_by_user_id  INTEGER FK
reason          TEXT
note            TEXT
created_at      DATETIME
```

### `communication_log` (R12)
```sql
id              INTEGER PRIMARY KEY
customer_id     INTEGER FK NULL  -- NULL if outbound to non-customer (e.g., vendor)
vendor_id       INTEGER FK NULL  -- for vendor-side communications
channel         TEXT NOT NULL    -- email | sms | phone_call | manual_note
direction       TEXT NOT NULL    -- outbound | inbound
status          TEXT NOT NULL    -- queued | sent | delivered | failed | bounced | logged_only
provider        TEXT NULL        -- smtp | m365 | twilio | manual | null
provider_message_id  TEXT NULL   -- for delivery callbacks (Phase 2)

-- Content
to_address      TEXT             -- email or phone number
from_address    TEXT
subject         TEXT NULL        -- email subject
body            TEXT             -- final rendered body (after template substitution)
template_used   TEXT NULL        -- name of template if one was used

-- Linking
related_entity_type  TEXT NULL   -- quote | invoice | so | po | ra | warranty | research | core_slip | statement
related_entity_id    INTEGER NULL

-- Audit
sent_by_user_id INTEGER FK
sent_at         DATETIME
delivered_at    DATETIME NULL
failed_reason   TEXT NULL

-- Compliance
consent_verified  BOOL DEFAULT false
opt_out_check_passed  BOOL DEFAULT true

created_at      DATETIME
-- NO updated_at, NO deleted_at — immutable audit log
```

### `communication_attachments` (R12 — Phase 2 use, schema ready now)
```sql
id              INTEGER PRIMARY KEY
communication_id  INTEGER FK
file_path       TEXT
file_name       TEXT
file_size       INTEGER
mime_type       TEXT
created_at      DATETIME
```

---

## New / Updated Constants & Enums

```python
# app/constants.py additions

class FulfillmentSource(str, Enum):
    STOCK = "stock"
    BACKORDER = "backorder"
    LINKED_PO = "linked_po"
    SPECIAL_ORDER = "special_order"
    DROPSHIP = "dropship"

class SOLineStatus(str, Enum):
    # in-stock flow
    STOCK = "stock"
    RESERVED_STOCK = "reserved_stock"
    # awaiting flows
    AWAITING_STOCK = "awaiting_stock"
    AWAITING_PO_RECEIPT = "awaiting_po_receipt"
    AWAITING_SPECIAL_ORDER_PO = "awaiting_special_order_po"
    # dropship flow
    VENDOR_CONFIRMED = "vendor_confirmed"
    SHIPPED_DIRECT = "shipped_direct"
    # terminal
    FULFILLED = "fulfilled"
    INVOICED = "invoiced"
    CLOSED = "closed"
    CANCELLED = "cancelled"

class WarrantyType(str, Enum):
    VENDOR = "vendor"
    JAKS_EXTENDED = "jaks_extended"

class InventoryAdjustmentReason(str, Enum):
    CYCLE_COUNT_OVER = "cycle_count_over"
    CYCLE_COUNT_SHORT = "cycle_count_short"
    DAMAGED = "damaged"
    LOST = "lost"
    FOUND = "found"
    VENDOR_RETURN = "vendor_return"
    WARRANTY_SCRAP = "warranty_scrap"
    CORE_SCRAP = "core_scrap"
    INTERNAL_USE = "internal_use"
    INITIAL_INVENTORY_LOAD = "initial_inventory_load"
    CORRECTION = "correction"
    OTHER = "other"

class InventoryTransactionType(str, Enum):
    PO_RECEIPT = "po_receipt"
    INVOICE_FINALIZE = "invoice_finalize"
    INVOICE_VOID = "invoice_void"
    ADJUSTMENT = "adjustment"
    TRANSFER = "transfer"
    RMA_ACCEPT = "rma_accept"
    SO_COMMIT = "so_commit"
    SO_RELEASE = "so_release"
    INITIAL_LOAD = "initial_load"
    MANUAL_NO_PO_RECEIPT = "manual_no_po_receipt"

class PaymentDirection(str, Enum):
    INCOMING_FROM_CUSTOMER = "incoming_from_customer"
    REFUND_TO_CUSTOMER = "refund_to_customer"
    INCOMING_FROM_VENDOR = "incoming_from_vendor"
    REFUND_TO_VENDOR = "refund_to_vendor"

class CreditMemoTrigger(str, Enum):
    MANUAL = "manual"
    ACCEPTED_RA = "accepted_ra"
    APPROVED_WARRANTY = "approved_warranty"
    LOCKED_INVOICE_CORRECTION = "locked_invoice_correction"
    OVERCHARGE = "overcharge"
    PRICING = "pricing"

class VendorCreditMemoTrigger(str, Enum):
    OVERCHARGE = "overcharge"
    VENDOR_RETURN_ACCEPTED = "vendor_return_accepted"
    DEFECTIVE = "defective"
    CORE_DISPUTE = "core_dispute"
    FREIGHT_ADJUSTMENT = "freight_adjustment"
    WARRANTY_CREDIT = "warranty_credit"

class AddressType(str, Enum):
    BILLING = "billing"
    SHIPPING = "shipping"
    JOB_SITE = "job_site"
    OTHER = "other"

class NotificationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class LineType(str, Enum):  # extend existing
    # ...existing...
    CORE_CHARGE = "core_charge"
    FREIGHT = "freight"
    SHIPPING = "shipping"
    LOCAL_DELIVERY = "local_delivery"
    CC_SURCHARGE = "cc_surcharge"
    TAX = "tax"
    WARRANTY = "warranty"
    MISC_FEE = "misc_fee"

NON_DISCOUNTABLE_LINE_TYPES = {
    LineType.CORE_CHARGE, LineType.FREIGHT, LineType.SHIPPING,
    LineType.LOCAL_DELIVERY, LineType.CC_SURCHARGE, LineType.TAX,
    LineType.WARRANTY, LineType.MISC_FEE,
}

class Permission(str, Enum):
    INVENTORY_ADJUST = "inventory_adjust"
    NEGATIVE_INVENTORY_OVERRIDE = "negative_inventory_override"
    VOID_LOCKED_INVOICE = "void_locked_invoice"
    REPUSH_QBO = "repush_qbo"
    MERGE_CUSTOMERS = "merge_customers"
    CHANGE_SETTINGS = "change_settings"
    VIEW_AUDIT_LOG = "view_audit_log"
    DEACTIVATE_MASTER = "deactivate_master"
    REVERSE_PAYMENT = "reverse_payment"
    ISSUE_CREDIT_MEMO = "issue_credit_memo"
    SEND_SMS = "send_sms"
    SEND_EMAIL = "send_email"

class PreferredContactMethod(str, Enum):
    PHONE = "phone"
    TEXT = "text"
    EMAIL = "email"

class CommunicationChannel(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    PHONE_CALL = "phone_call"
    MANUAL_NOTE = "manual_note"

class CommunicationDirection(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"

class CommunicationStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    BOUNCED = "bounced"
    LOGGED_ONLY = "logged_only"  # Phase 1 default — no actual send

class SMSConsentMethod(str, Enum):
    VERBAL = "verbal"
    SIGNED_FORM = "signed_form"
    ONLINE_FORM = "online_form"
    IMPORTED = "imported"  # for legacy customers
    OTHER = "other"
```

---

## Settings Keys (new defaults)

```python
# Add to settings.py DEFAULTS dict:

# Interest
"interest_grace_days": ("10", "Days past due before interest accrues"),

# CC surcharge
"cc_surcharge_pct": ("3.0", "Credit card convenience fee %"),

# Followup
"default_followup_offset_days": ("7", "Default quote follow-up offset"),

# Cores
"core_return_grace_days": ("45", "Days customer has to return cores"),
"core_return_reminder_threshold_pct": ("75", "% of window before reminder"),

# AR aging
"ar_aging_buckets_days": ("0,30,60,90,120", "AR aging bucket cutoffs"),

# Search
"search_results_per_type": ("10", "Max results per entity type in global search"),

# Tax
"default_sales_tax_rate": ("0.0", "Default sales tax rate %"),
"company_tax_jurisdiction": ("", "Company's tax jurisdiction (state)"),

# QBO
"jaks_env": ("sandbox", "sandbox | production"),
"qbo_sandbox_prefix": ("TEST-", "Prefix for test records"),

# Time / locale
"business_timezone": ("America/Denver", "Local timezone for date display"),
"business_close_time": ("23:59", "End-of-day lock time (local)"),

# Inventory
"allow_negative_inventory_admin_override": ("true", "Permit admin to allow negative inventory with audit"),
"low_stock_threshold_default": ("2", "Default low-stock alert threshold"),

# Special orders
"special_order_require_deposit_default": ("true", "Require deposit on special orders"),

# Company info (for templates)
"company_name": ("JAKS Diesel Parts", "Company name on documents"),
"company_address": ("", "Multi-line company address"),
"company_phone": ("", ""),
"company_email": ("", ""),
"company_website": ("", ""),
"company_signature_disclaimer": ("", "Footer disclaimer for emails"),

# Warranty
"jaks_warranty_reserve_account": ("Warranty Reserve", "Accounting category for JAKS-extended warranty credits"),

# Concurrency
"concurrency_check_field": ("updated_at", "Field used for optimistic locking"),

# Communication (R12) — provider abstraction config
"messaging_email_provider": ("null", "null | smtp | m365 | gmail"),
"messaging_sms_provider": ("null", "null | twilio"),
"messaging_log_only_mode": ("true", "Phase 1: log communications but do not actually send"),

# SMTP config (Phase 2 — kept here for forward-compat)
"smtp_host": ("", ""),
"smtp_port": ("587", ""),
"smtp_username": ("", ""),
"smtp_password_encrypted": ("", "Encrypted at rest — never plaintext"),
"smtp_from_address": ("", ""),
"smtp_from_name": ("JAKS Diesel Parts", ""),
"smtp_use_tls": ("true", ""),

# Twilio config (Phase 2)
"twilio_account_sid": ("", ""),
"twilio_auth_token_encrypted": ("", "Encrypted at rest"),
"twilio_from_number": ("", ""),

# Outbound rate limits (sanity guard)
"messaging_max_outbound_per_hour": ("100", "Sanity rate limit per Keith's user"),
"messaging_max_outbound_per_customer_per_day": ("3", "Avoid spamming a single customer"),
```

---

## Service-Layer Behavior Changes

### `InvoiceService`

**`finalize_invoice` (or current lock method)** — new behavior:
1. Snapshot tax fields from customer (`tax_rate_snapshot`, `tax_exempt_snapshot`)
2. Compute per-line `tax_amount` based on `is_taxable` + tax_rate_snapshot
3. Compute invoice `tax_amount` = sum of line tax amounts
4. Check inventory: if any line qty > qty_available → hard block unless admin has `NEGATIVE_INVENTORY_OVERRIDE` (audit logged)
5. Decrement `qty_on_hand` on each in-stock line (creates `inventory_transactions` with type=`invoice_finalize`)
6. Release `qty_committed` if the invoice came from an SO (one net decrement, not double)
7. Insert `price_history` rows for each line (with `customer_id`, not NULL)
8. Bump `cross_references.successful_sale_count` for any cross-ref used
9. Set `locked_at` if past `business_close_time` or other lock trigger
10. Auto-create `core_charges` rows for products with `has_core=true`
11. Auto-assign serial numbers — block save until each serialized line has a serial selected

**Remove from existing behavior:**
- The current invoice-level CC surcharge toggle. Per R1, surcharge is computed at payment time on the card portion only.

**New method: `apply_customer_credit(invoice_id, amount)`**
- Validates: amount ≤ customer.credit_balance, amount ≤ invoice.balance_due
- Creates a special PaymentAllocation with source="credit_balance"
- Decrements customer.credit_balance
- Updates invoice balance_due
- Audit logged

**New method: `void_invoice(invoice_id, reason)`**
- Block if locked AND qbo_pushed → require credit memo instead
- Block if any payments exist (even partial)
- Otherwise: reverse inventory transactions, mark `status=voided`, audit logged

### `PaymentService`

**`record_payment` — new behavior:**
- Snapshot card surcharge: if `payment_method=card`, compute surcharge = `amount × cc_surcharge_pct / 100`, store on payment
- Validate: sum of allocations ≤ invoice balance OR explicit overpayment-to-credit option
- If overpayment: prompt to credit customer (allow only with explicit flag, never silent)
- Set `payment_direction` based on context
- Audit logged
- Update `customer.credit_balance` only if explicit overpayment flag set

**`reverse_payment` — new behavior:**
- Always creates a reversal Payment row (never deletes original)
- Reverses each allocation (creates negative allocations)
- Restores `customer.credit_balance` if the original payment had credited it
- Audit logged
- BOOKKEEPING role allowed, with note required

### `SalesOrderService`

**`add_line` — new behavior:**
- Determine `fulfillment_source` based on qty_available vs requested:
  - qty_available ≥ requested → `stock` → auto-commit qty_committed
  - qty_available < requested AND linked PO exists → `linked_po` (set `linked_po_line_id`)
  - qty_available < requested AND no linked PO → manager decision: `backorder` | create PO + `linked_po` | `special_order` (require deposit if setting on)
- Validate against negative inventory rules
- Set initial status per fulfillment_source state diagram

**`commit_inventory` (new internal method)** — increments `qty_committed`

**`release_committed` (new internal method)** — decrements `qty_committed`

**`cancel_so` — new behavior:**
- Release all committed qty back to available
- If deposits collected: require manual resolution (do not auto-refund or auto-credit)
- Mark all lines as cancelled
- Audit logged

**`convert_to_invoice` — new behavior:**
- Auto-allocate any unapplied SO payments to the new invoice
- If invoice total ≤ SO payments → invoice immediately PAID
- If partial fulfillment → allocate deposit proportionally across invoices
- Release committed qty proportional to invoiced lines

**`fulfill_line(so_line_id, qty)`** — state transition method per the SOLineStatus flow

### `POService`

**`receive_po — new behavior:**
- Update `product.cost` via moving weighted average:
  ```
  new_avg = ((qty_on_hand × current_avg) + (qty_received × receipt_unit_cost))
            / (qty_on_hand + qty_received)
  ```
- Update `product.last_cost` = receipt_unit_cost
- Allocate received qty to linked SO lines FIFO (oldest first)
- Remaining qty → general stock (qty_on_hand)
- Check for over-receipt → set `over_received=true`, `over_received_qty`, audit flag
- Create `inventory_transactions` with type=`po_receipt`

**`approve_vendor_bill` — new behavior:**
- Auto-approve if: qty matches AND total variance ≤ $1 OR ≤ 0.5%
- Auto-approve if: billed qty ≤ received qty (partial bills)
- Flag for manual review if: unit price differs, freight not on PO, larger variance

**`cancel_po_line(po_line_id, qty, reason)`** — new method
- Set `qty_cancelled`, `cancel_reason`, `cancelled_at`, `cancelled_by_id`
- PO closes when `qty_received + qty_cancelled = qty_ordered`

### `QuoteService`

**`add_line` — new behavior:**
- Auto-apply `customer.discount_pct` to line `discount_pct` if line is discountable
- Reject discounts on non-discountable line types (per `NON_DISCOUNTABLE_LINE_TYPES`)

**`update_line_discount(line_id, new_pct)`**
- If new_pct ≠ customer.discount_pct → set `discount_overridden=true`
- Audit logged

**`convert_to_invoice` — new behavior:**
- Filter `is_included=True` lines only
- Mark quote `outcome=won`, link to invoice
- Remove from active quote list

**`duplicate_quote(quote_id)`** — new method
- Copy all lines into new quote with fresh `quote_number`
- Set `is_duplicate_of_quote_id`
- Customer field stays editable
- Original quote untouched

### `CoreService`

**`open_customer_core` — new behavior:**
- Snapshot `core_return_grace_days` from settings
- Set initial `location_id` = NULL (not yet returned)

**`receive_customer_core` — new behavior:**
- Require inspection outcome
- If ACCEPTED → set `location_id` = Core Shelf (default), status = ready_for_credit
- If HOLD → set `location_id` = Core Holding
- If REJECTED → set `location_id` = Rejected Core
- If DAMAGED → set `location_id` = Questionable Core
- Audit logged

**`issue_core_credit(core_id, credit_method)`** — new method
- ACCOUNT_CREDIT → increment customer.credit_balance
- CHECK → create refund Payment (direction=refund_to_customer) — does NOT touch credit_balance
- HOLD → no balance change yet
- Audit logged

**`mark_overdue_cores(cron)`** — new method (run nightly)
- For each open core: if today - created_at > grace_days_snapshot, set `is_overdue=true`
- Trigger notification at threshold (75% of grace window)
- Notification only — never auto-bill

**`move_core(core_id, dest_location, reason, note)`** — new method
- Create `core_location_movements` row
- Update `core_charges.location_id`
- Audit logged

**`process_vendor_credit_difference(core_id, expected, actual, resolution)`** — new method
- Resolutions: ABSORB | CHARGE_CUSTOMER | DISPUTE | WRITE_OFF
- Default ABSORB (per R3), but flag for follow-up
- If CHARGE_CUSTOMER → create invoice line for the difference
- Audit logged

### `ResearchService`

**`suggest_promote_to_proven(cross_ref_id)`** — new query method
- Returns True if cross_ref has been used in ≥3 successful sales
- UI surfaces this; user confirms promotion

**`create_cross_ref_from_research(research_item_id, ref_type, ref_number, brand)`** — already exists, ensure default status = `found`

### `InventoryService` (NEW)

**`adjust_inventory(product_id, qty_delta, reason, note, user_id, unit_cost=None)`**
- Validate user has `INVENTORY_ADJUST` permission
- Validate reason is in InventoryAdjustmentReason enum
- Require note if reason in {OTHER, CORRECTION, MANUAL_OVERRIDE}
- If `qty_delta > 0` and `unit_cost` provided → update product.cost via moving average
- If `qty_delta > 0` and no unit_cost → use current product.cost (warn)
- Create `inventory_transactions` row
- Audit logged

**`transfer_inventory(product_id, qty, source_loc, dest_loc, reason, note, user_id)`**
- Validate user has `INVENTORY_ADJUST` permission
- Does NOT change qty_on_hand (location movement only)
- Create `inventory_transfers` row
- Audit logged

**`receive_without_po(product_id, qty, unit_cost, source, reason, note, user_id)`**
- Admin only
- Creates `inventory_transactions` with type=`manual_no_po_receipt`
- Updates qty_on_hand
- Updates moving average cost
- Audit logged

### `CreditMemoService` (NEW)

**`create_credit_memo(customer_id, trigger, lines, original_invoice_id=None, reason=)`**
- Generates CM-YYYY-NNNN via bump_counter
- Creates `credit_memos` row, lines
- Initial state: unapplied (status=open, unapplied_amount = total)
- credit_balance NOT touched yet
- Audit logged

**`apply_credit_memo(cm_id, invoice_id, amount)`**
- Validates: amount ≤ cm.unapplied_amount, amount ≤ invoice.balance_due
- Creates `credit_memo_allocations` row
- Decrements cm.unapplied_amount, increments cm.applied_amount
- Reduces invoice.balance_due
- If still unapplied after all targets exhausted → unapplied amount adds to customer.credit_balance
- Audit logged

**`reverse_credit_memo(cm_id, reason)`**
- Creates a reversing/debit memo (not hard delete)
- Restores any applied amounts (reverses allocations)
- Audit logged

### `VendorCreditService` (NEW — mirrors CreditMemoService for vendor side)

Same shape as above but for `vendor_credit_memos` + `vendor_credit_memo_allocations`.

### `StatementService` (NEW)

**`generate_statement(customer_id, date_range_start, date_range_end)`**
- Queries: opening balance, invoices in range, payments in range, credits in range
- Computes: interest accrued (simple interest, monthly rate, past grace days)
- Computes: aging buckets (current / 30 / 60 / 90 / 120)
- Stores snapshot in `customer_statements` table
- Returns statement object + PDF path (when PDF gen is wired)

### `NotificationService` (NEW)

**`notify(user_id, severity, type, entity_type, entity_id, message)`**
- Creates `notifications` row
- (Email delivery: Phase 2)

**`mark_acknowledged(notification_id, user_id)`** / **`dismiss(notification_id)`**

**Notification triggers (registered via service events):**
- Vendor bill discrepancy → on POService.bill_flagged()
- PO over-receipt → on POService.receive() when over_received=true
- Inventory adjustment by another user → on InventoryService.adjust()
- Core overdue → on CoreService.mark_overdue_cores() nightly
- Warranty vendor decision → on WarrantyService.record_decision()
- QBO push failure → on any service's qbo_push() failure
- Low stock threshold → on inventory change events
- Big invoice (> threshold) → on InvoiceService.finalize()
- Negative inventory override → on InvoiceService.finalize() / SalesOrderService.add_line()

### `SearchService` — behavior updates

- Strip non-numeric chars from phone search
- Combined OEM/cross-ref result row ("[OEM#] → [JAKS-SKU] Equivalent")
- Substring match anywhere, ranks exact/start higher
- Recently-used boost (last 30 days)
- Type priority order: products/cross-refs → customers → invoices → quotes → SOs → vendors → POs
- Hide inactive by default; `include_inactive=True` param

### `CustomerService` (extend / new)

**`detect_duplicate(name, phone, email)`** — new method
- Normalizes inputs (lowercase, strip spaces, digits-only for phone)
- Returns list of possible matches

**`merge_customers(source_id, target_id, user)`** — admin-only, future Phase
- Reassigns all invoices/payments/quotes/SOs/cores/RAs/warranties/call_logs
- Soft-deletes source
- Combines credit_balance
- Audit logged with full before-state snapshot

### `MessagingService` (NEW — R12 communication architecture)

**Design — provider abstraction layer:**

```python
# app/services/messaging/providers.py
class MessagingProvider(Protocol):
    """Pluggable provider interface — same shape for SMTP, M365, Twilio, null."""
    def send_email(self, *, to: str, subject: str, body: str,
                   attachments: list[Path] = None) -> SendResult: ...
    def send_sms(self, *, to: str, body: str) -> SendResult: ...

class NullMessagingProvider(MessagingProvider):
    """Phase 1 default — logs only, never actually sends."""
    def send_email(self, **kw) -> SendResult:
        return SendResult(status="logged_only", provider_message_id=None)
    def send_sms(self, **kw) -> SendResult:
        return SendResult(status="logged_only", provider_message_id=None)

class SMTPMessagingProvider(MessagingProvider):  # Phase 2
class M365MessagingProvider(MessagingProvider):  # Phase 2
class TwilioSMSProvider(MessagingProvider):       # Phase 2
```

**`MessagingService` methods:**

**`render_template(template_name, variables: dict) -> RenderedMessage`**
- Loads template from `app/messaging_templates/<name>.txt` (Phase 1 — file-based)
- Substitutes `{customer_name}`, `{quote_number}`, `{invoice_number}`, `{balance_due}`, `{due_date}`, `{core_due_date}`, `{tracking_number}`, `{salesperson_name}`, etc.
- Returns subject + body
- Variables undefined in template → leave placeholder visible (don't silently substitute empty)

**`send(customer_id, channel, template_name, variables, related_entity=None, sent_by_user_id, override_address=None) -> Communication`**
- Validate consent:
  - `do_not_contact=False`
  - If channel=sms: `allow_sms=True` AND `sms_consent_at IS NOT NULL`
  - If channel=email: `allow_email=True`
- Validate rate limits (`messaging_max_outbound_per_*` settings)
- Render template
- Call provider.send_*() (NullProvider in Phase 1)
- Log to `communication_log` regardless of provider result
- Return the Communication record
- If consent/rate check fails → raise CommunicationBlockedError (logs nothing — caller decides)

**`log_manual_communication(customer_id, channel, body, related_entity=None, sent_by_user_id)`**
- For copy/paste workflows where the user manually sent via their own email client
- Creates a `communication_log` entry with `status=logged_only`, `provider=manual`
- No consent/rate check (user already sent it themselves)

**`record_inbound(customer_id, channel, body, sent_by_user_id, related_entity=None)`**
- For logging incoming customer communications (call notes, replies)
- direction=inbound

**`record_consent(customer_id, channel, method, sent_by_user_id)`**
- Sets `sms_consent_at` or `email_consent_at` + `sms_consent_method`
- Audit logged

**`record_opt_out(customer_id, sent_by_user_id, reason)`**
- Sets `opt_out_at`, clears `allow_sms`, `allow_email`, sets `do_not_contact`
- Audit logged

**`provider_for(channel)`** — internal factory
- Reads settings → returns appropriate provider class (NullProvider in Phase 1)

**Templates to ship in `app/messaging_templates/`:**

| File | Variables | Purpose |
|------|-----------|---------|
| `dealer_parts_request.txt` | customer_name, vin, esn, part_description | Already in ResearchService — extract to template file |
| `vendor_parts_request.txt` | part_description, oem_number, urgency | Already in ResearchService — extract |
| `quote_followup.txt` | customer_name, quote_number, quote_date, salesperson_name | R8 |
| `invoice_past_due.txt` | customer_name, invoice_number, balance_due, days_past_due | R8 |
| `core_return_reminder.txt` | customer_name, core_slip_number, days_remaining | R8 |
| `statement_cover.txt` | customer_name, statement_period | R8 |
| `warranty_update.txt` | customer_name, claim_number, status, vendor_decision_notes | R8 |
| `special_order_ready.txt` | customer_name, part_description, ready_date | R8 |
| `payment_received.txt` | customer_name, payment_amount, payment_date, invoice_number | R8 |
| `missing_info_request.txt` | customer_name, quote_number, missing_field | R8 |

**Refactor:** Move `generate_dealer_request_template` and `generate_vendor_request_template`
from `ResearchService` into `MessagingService.render_template()` calls. ResearchService
calls MessagingService.render_template internally for backward compat.

**Security note:** All provider credentials (`smtp_password_encrypted`, `twilio_auth_token_encrypted`)
must use the Fernet-based encryption layer (Phase 2). For Phase 1 (NullProvider), no
credentials exist — just placeholders in settings.

### `BaseService` updates

**`assert_can(permission, user)`** — new helper
- Checks user.role against Permission enum
- Raises PermissionError if denied

**Optimistic locking helper:**
```python
def check_version(self, record, submitted_updated_at):
    if record.updated_at != submitted_updated_at:
        raise ConcurrentEditError(
            "This record was changed by another user. Refresh before saving."
        )
```

---

## New Service Methods (Summary)

| Service | Method |
|---|---|
| `InvoiceService` | `apply_customer_credit`, `void_invoice` |
| `PaymentService` | (rewrite `record_payment`, `reverse_payment`) |
| `SalesOrderService` | `commit_inventory`, `release_committed`, `fulfill_line`, `cancel_so` (rewrite) |
| `POService` | `cancel_po_line`, allocation logic in `receive_po` |
| `QuoteService` | `duplicate_quote`, `update_line_discount` |
| `CoreService` | `move_core`, `mark_overdue_cores`, `process_vendor_credit_difference`, `issue_core_credit` |
| `ResearchService` | `suggest_promote_to_proven` |
| `InventoryService` | (new module) `adjust_inventory`, `transfer_inventory`, `receive_without_po` |
| `CreditMemoService` | (new module) `create_credit_memo`, `apply_credit_memo`, `reverse_credit_memo` |
| `VendorCreditService` | (new module) full mirror of CreditMemoService |
| `StatementService` | (new module) `generate_statement` |
| `NotificationService` | (new module) `notify`, `mark_acknowledged`, `dismiss` |
| `MessagingService` | (new module) `render_template`, `send`, `log_manual_communication`, `record_inbound`, `record_consent`, `record_opt_out` |
| `SearchService` | (rewrite ranking + phone normalization) |
| `CustomerService` | `detect_duplicate`, `merge_customers` (future) |

---

## Implementation Priority Order

These are ordered so that Keith can keep building UI on the existing surface
without conflicts. Each phase is self-contained and tested before moving on.

### Phase A — Schema foundation (1 session, no behavior change yet)
1. Add all new columns to existing tables (`customers`, `products`, `invoices`,
   `invoice_lines`, `quotes`, `sales_orders`, `so_lines`, `purchase_orders`,
   `po_lines`, `payments`, `core_charges`, `warranty_claims`, `audit_logs`,
   `cross_references`)
2. Create all new tables (`customer_addresses`, `inventory_transactions`,
   `inventory_transfers`, `credit_memos` + lines/allocations, `vendor_credit_memos`,
   `vendor_returns`, `customer_statements`, `notifications`,
   `core_locations` if not built, `core_location_movements`)
3. Add new constants/enums to `app/constants.py`
4. Drop-and-recreate `data/jaks.db` (all test data)
5. Seed default core_locations
6. Seed default user (already done)
7. Add new settings keys to defaults dict
8. **Gate: all migrations apply cleanly; smoke test passes.**

### Phase B — Inventory + cost integrity (1–2 sessions)
1. Implement `InventoryService` (adjust, transfer, receive_without_po)
2. Rewrite `POService.receive_po` to update moving average cost + allocate to linked SOs
3. Implement negative-inventory hard block + admin override on `InvoiceService.finalize`
4. Implement SO line commit/release in `SalesOrderService`
5. Wire `inventory_transactions` for every inventory-changing event
6. **Gate: receive PO → cost updates correctly; invoice finalize decrements stock; SO commit reserves qty.**

### Phase C — Sales Order fulfillment sources (1 session)
1. Add fulfillment_source logic to `SalesOrderService.add_line`
2. Implement linked-PO FIFO allocation on receipt
3. Implement SO line status state machine
4. Implement deposit-to-invoice auto-allocation
5. **Gate: SO with linked PO → PO receipt → SO becomes reserved_stock. SO with deposit → invoice has correct balance_due.**

### Phase D — Tax + locked invoice rules (1 session)
1. Implement tax snapshotting on `InvoiceService.finalize`
2. Implement per-line `is_taxable` + `tax_amount`
3. Enforce locked invoice edit rules (block specific operations, allow others)
4. Allow payment recording on locked invoices
5. Block void on QBO-pushed invoices
6. **Gate: tax computed correctly on mixed taxable/non-taxable invoice; locked invoice rejects edits.**

### Phase E — Customer credit + payment rewrite (1 session)
1. Rewrite `PaymentService.record_payment` (allocations, surcharge at payment time, direction)
2. Implement `InvoiceService.apply_customer_credit`
3. Implement payment reversal flow
4. Wire all credit_balance increase/decrease events
5. **Gate: customer credit applied to invoice works; refund check from credit works; balance never goes negative.**

### Phase F — Credit memos (1–2 sessions)
1. Implement `CreditMemoService`
2. Wire automatic credit memo creation triggers (accepted RA, approved warranty, void of locked invoice)
3. Implement `apply_credit_memo` allocation logic
4. **Gate: RA → credit memo → applied to invoice or sits as unapplied credit.**

### Phase G — Vendor credits + returns (1 session)
1. Implement `VendorCreditService` (mirror of CreditMemoService)
2. Implement `vendor_returns` workflow (separate from cores)
3. **Gate: vendor over-charge → VCM → applied to next vendor bill.**

### Phase H — Cores + locations (1 session)
1. Implement core location movement service
2. Implement overdue core detection
3. Implement vendor credit difference resolution
4. Wire location transitions per inspection outcomes
5. **Gate: customer returns core → goes to right location based on inspection; overdue cores flagged.**

### Phase I — Statements + reports (1 session)
1. Implement `StatementService.generate_statement`
2. Update AR aging report with new bucket logic (configurable cutoffs, voided excluded)
3. Update sales by customer/product (use locked_at, exclude voided, subtract credit memos)
4. Update inventory valuation (use avg cost, configurable inclusion of zero-qty)
5. Add new reports: Open POs, Core Charges Outstanding, Overdue Invoices + Interest, Sales Tax Collected
6. **Gate: statement generation accurate; AR aging matches manual calculation; tax report matches collected tax.**

### Phase J — Search + concurrency (1 session)
1. Implement optimistic locking helper on `BaseService`
2. Wire version check into all financial record save paths
3. Rewrite `SearchService` ranking + phone normalization + OEM combined results
4. Add `include_inactive` flag
5. **Gate: two simultaneous edits → second one rejected with clear error; OEM search returns combined result.**

### Phase K — Notifications + audit log enhancements (1 session)
1. Implement `NotificationService`
2. Wire notification triggers throughout services
3. Add per-field audit logging (old_value / new_value) to master data services
4. **Gate: vendor bill discrepancy creates notification; customer name change creates audit row with old/new.**

### Phase L — Communication foundation (1 session, no actual send)
1. Add customer communication preference columns (`preferred_contact_method`,
   `allow_sms`, `allow_email`, `do_not_contact`, consent fields)
2. Create `communication_log` + `communication_attachments` tables
3. Implement `MessagingService` with `NullMessagingProvider` only
4. Implement `render_template` + variable substitution
5. Ship initial 10 templates in `app/messaging_templates/`
6. Refactor `ResearchService.generate_*_template()` to call `MessagingService.render_template()`
7. Wire `log_manual_communication` so copy/paste workflows leave a trail
8. **Gate: copy/paste workflows log to communication_log; rendering a template substitutes variables correctly; consent check blocks send when missing.**

### Phase M — QBO push (deferred until Keith says go)
1. Implement OAuth flow
2. Implement push methods for invoice, payment, vendor bill, credit memo
3. Implement sandbox/production mode separation
4. Wire failure handling + retry
5. **Gate: invoice pushes to QBO sandbox successfully; failure does not lock invoice.**

### Phase N — Real messaging providers (Phase 2 — when Twilio/SMTP credentials are ready)
1. Implement `SMTPMessagingProvider` + `M365MessagingProvider`
2. Implement `TwilioSMSProvider`
3. Implement Fernet-based credential encryption layer
4. Wire delivery callbacks (Twilio webhooks, SMTP bounces)
5. Implement STOP/HELP keyword handling for inbound SMS
6. **Gate: real email/SMS sends from app; consent + opt-out flow works end-to-end.**

### Phase O — Phase 1 late items
1. Serial number tracking implementation
2. Vendor kit BOM / JAKS-built kit BOM
3. Customer merge tool

---

## Open Assumptions to Confirm

These were inferred or set as defaults — flag if any are wrong:

1. **`interest_grace_days` is per-customer** (column on customers table). Could alternatively be a single global setting.
2. **Core return reminder threshold = 75% of grace window** (so day 34 of 45). Configurable in settings.
3. **`updated_at` is the optimistic-lock check field** (not a separate `version` integer).
4. **Tax MVP = single invoice-level rate** with per-line `is_taxable` flag. Multi-jurisdiction tax (TaxJar) is Phase 2.
5. **Refund check from customer credit** = creates a Payment with `payment_direction=refund_to_customer`, decrements credit_balance, no QBO push in Phase 1.
6. **Statement number format** = `ST-2026-XXXX`. No prior convention specified.
7. **Vendor return number format** = `VR-2026-XXXX`.
8. **Cross-ref "successful sale count" bump** = on every invoice finalization that used that cross-ref in search. Reset on void? Probably yes — decrement on void.
9. **"Internal use / shop use" inventory adjustment** still decrements qty_on_hand; does it also need a separate cost account tracking? (Phase 2 if so.)
10. **`payment_direction` defaults to `incoming_from_customer`** on all existing payment records. Migration sets it.
11. **Templates stored as text files** in `app/messaging_templates/` (Phase 1). Phase 2 may move to a DB table for in-app editing. The file format is plain text with `{variable_name}` placeholders.
12. **`NullMessagingProvider` is the Phase 1 default** — every `send()` call logs to `communication_log` with `status=logged_only` and never actually transmits. Real send is gated on a settings switch (Phase 2).
13. **Sanity rate limits** (`messaging_max_outbound_per_hour=100`, `per_customer_per_day=3`) are conservative defaults — adjust after observing real usage.

---

*This document is the source of truth for backend work during Keith's UI build.*
*Update it as decisions are refined or scope changes.*
*Match work to the priority phase before starting — do not skip ahead.*
