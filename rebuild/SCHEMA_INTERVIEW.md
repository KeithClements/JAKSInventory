# JAKS Inventory — Schema Interview
*Date: 2026-05-22 | Status: COMPLETE — Feed directly into Step 0 (Schema Finalization)*
*Do not begin schema work without reading this file in full.*

---

## SECTION A — Products & Pricing

---

### A1 — Can one product be sourced from multiple vendors?

**Answer:** Yes — one logical product may be sourced from multiple vendors. However, each vendor source gets its own vendor-specific SKU and pricing record. Do NOT create duplicate product records per vendor.

**Example:**
```
PAI gasket  →  JAKS-PAI-123456
HHP equivalent  →  JAKS-HHP-789012
```
Same interchangeable part. Different SKUs. Different costs. Different availability. Different lead times.

**Vendor code rule:** Every vendor requires a 3–4 character vendor code configured on the vendor record. The SKU is assembled by the system: `JAKS-[VENDOR_CODE]-[PART_NUMBER]`. Keith types only the part number; the prefix is automatic.

**Schema decision:**
```
products                         ← master/logical product record
product_vendor_sources           ← one row per vendor that supplies this product
  product_id FK
  vendor_id FK
  vendor_part_number             ← vendor's own part number for this item
  vendor_sku                     ← assembled JAKS SKU (JAKS-PAI-123456)
  vendor_cost                    ← what JAKS pays this vendor
  is_preferred_vendor            ← which source to default to
  lead_time_days
  last_cost_updated_at
  notes
```

**Impact on existing model:** Current `product.vendor_id` FK (one-to-one) must be replaced with the `product_vendor_sources` join table. A `preferred_vendor_id` may be stored on the product for quick access.

---

### A2 — How deep are product categories?

**Answer:** Three levels maximum. First two levels required; third is optional.

```
Level 1: Major Group     (required)    e.g. Engine Parts
Level 2: Category        (required)    e.g. Gaskets
Level 3: Subcategory     (optional)    e.g. Head Gaskets
```

**Schema decision:**
```
product_categories
  id
  name
  parent_id FK (self-referential, nullable)  ← supports 1, 2, or 3 levels
  level                                       ← 1 | 2 | 3
  is_active
```

Products reference the leaf category. The tree is traversed up for breadcrumb display and filtering.

---

### A3 — Do you sell by any unit other than "each"?

**Answer:** Nearly all products are discrete "each" units today. Fractional quantities are not currently needed. However, the schema must be flexible for future expansion (oils, fluids, hose-by-foot, etc.).

**Schema decision:**
- Add `unit_of_measure` field to `products` — default `"EA"`
- Invoice and SO lines inherit product UOM, can be overridden
- Quantity fields are integers for now — no fractional support in V1
- Future UOM values: EA, SET, KIT, BOX, GAL, QT, PAIR, FT

```
products.unit_of_measure  TEXT DEFAULT 'EA'
```

---

### A4 — Do you set min/max stock levels or reorder points?

**Answer:** Yes — min/max fields should exist on products so the low-stock dashboard widget can use real thresholds instead of a global setting.

**Schema decision:**
```
products.reorder_point       INTEGER NULLABLE   ← flag when qty_on_hand drops to/below this
products.max_stock_level     INTEGER NULLABLE   ← optional upper bound for reorder qty suggestion
```

Both nullable — if null, product falls back to global low-stock threshold in settings.

---

### A5 — What happens to a product you no longer sell?

**Answer:** Products should have a status field — not just active/inactive. Superseded products are especially common in diesel parts (old OEM number replaced by new OEM number).

**Supported statuses:**
| Status | Meaning |
|---|---|
| `active` | Normal — appears in search, orderable |
| `inactive` | Hidden from search, not orderable, retained in history |
| `superseded` | Replaced by a newer product — links to successor |
| `discontinued` | No longer available from any vendor |
| `special_order` | Available but not stocked — must be ordered per request |

**Schema decision:**
```
products.status              TEXT DEFAULT 'active'
products.superseded_by_id    INTEGER FK NULLABLE   ← points to the replacement product
```

When a superseded product is searched, system shows: "This part has been superseded — see [replacement]."

---

## SECTION B — Customers & Contacts

---

### B1 — Can a customer have more than one location or ship-to address?

**Answer:** Yes. One primary billing address per customer, plus multiple optional ship-to addresses. Most customers will have one address today, but multi-location shops, fleets, and dealerships need multiple delivery locations.

**Schema decision:**
```
customer_addresses
  id
  customer_id FK
  address_type       TEXT    ← 'billing' | 'shipping'
  is_primary         BOOLEAN
  label              TEXT NULLABLE   ← e.g. "Main Shop", "Satellite Location"
  street
  city
  state
  zip
  country            DEFAULT 'US'
  notes
```

Billing address stays on the customer record directly (for performance). `customer_addresses` handles the overflow and ship-to options.

---

### B2 — Do you track multiple contacts at one customer?

**Answer:** Yes. One main account record, multiple contacts underneath it.

**Example contacts at one customer:**
- Owner (decision maker)
- Parts Manager (who calls)
- Accounts Payable (who processes payments)
- Service Manager

**Schema decision:**
```
customer_contacts
  id
  customer_id FK
  name
  title / role
  phone
  email
  is_primary         BOOLEAN
  is_billing_contact BOOLEAN
  notes
  is_active
```

---

### B3 — Do you need to store tax exemption certificate numbers?

**Answer:** Yes. Customer record should store whether the customer is taxable or tax-exempt, the certificate number, and support an image upload of the certificate document.

**Schema decision:**
```
customers.is_tax_exempt           BOOLEAN DEFAULT 0
customers.tax_exempt_cert_number  TEXT NULLABLE
customers.tax_exempt_cert_file    TEXT NULLABLE   ← file path to uploaded image/PDF
customers.tax_rate                REAL DEFAULT 0.0
```

Certificate image stored in `/data/attachments/customers/{customer_id}/tax_cert.*`

---

### B4 — Do any customers share a phone number?

**Answer:** Yes — phone number is NOT globally unique. Multiple shops, related businesses, shared front desks, and multi-location customers may share numbers.

**Schema decision:** No unique constraint on `customers.phone`. Global search on phone number returns all matching customers, grouped in results.

---

## SECTION C — Quotes & Sales Orders

---

### C1 — Do quotes expire?

**Answer:** Quotes should support expiration dates with a visible warning when expired, but expired quotes must NOT be hard-locked. User can still reopen, revise, or convert at any time.

**Reasoning:** Vendor pricing changes frequently. Customers often call back weeks later. The system should guide decisions, not block legitimate business judgment.

**Schema decision:**
```
quotes.expires_at       DATE NULLABLE
quotes.validity_days    INTEGER DEFAULT 30   ← used to auto-calculate expires_at
```

**QuoteService behavior:**
- If `expires_at < today`: status displays "Expired" badge — orange/yellow
- Quote is still fully functional — no blocking
- On convert to invoice: warn if quote is expired, allow user to proceed

---

### C2 — If a customer calls back on a 6-month-old quote, what do you do?

**Answer:** Reactivate the old quote and update pricing to current levels.

**Schema decision:**
```
quotes.reactivated_at      DATETIME NULLABLE
quotes.reactivated_by      INTEGER FK NULLABLE   ← user_id
quotes.original_expires_at DATE NULLABLE         ← preserve the original expiry for history
```

**QuoteService.reactivate():** resets `expires_at`, re-prices all lines from current product costs, sets status back to `draft` or `open`, logs the reactivation.

---

### C3 — Can one invoice pull lines from more than one Sales Order?

**Answer:** Yes — an invoice can include lines from multiple Sales Orders. Default workflow is one-to-one SO → Invoice, but combining is supported when the user intentionally wants it.

**Schema decision:**
```
invoice_lines.so_line_id    INTEGER FK NULLABLE   ← links this invoice line back to its SO line
invoices.primary_so_id      INTEGER FK NULLABLE   ← the "main" SO (for display/reference)
```

Multiple SO lines pointing to different SOs can exist on the same invoice. The `primary_so_id` handles the common case; the line-level FK handles the full many-to-one relationship.

---

### C4 — Can a Sales Order be partially invoiced?

**Answer:** Yes. Invoice only what is ready. The remaining/backordered items should appear in a separate section on the printed invoice so the customer knows nothing was forgotten.

**Invoice printed document — backordered section:**
```
ITEMS BILLED — THIS INVOICE:
  3x Gasket Set    ........    $XXX

REMAINING ITEMS — NOT YET INVOICED:
  1x Filter Kit    ........    Backordered — ETA Friday
```

**Schema decision:**
```
so_lines.qty_invoiced    INTEGER DEFAULT 0   ← cumulative qty billed across all invoices
so_lines.status          TEXT                ← 'open' | 'partial' | 'invoiced' | 'cancelled'
```

`qty_remaining = qty_ordered - qty_invoiced`

SO status rolls up from line statuses: all lines invoiced → SO status = `invoiced`.

---

## SECTION D — Payments

---

### D1 — Can one payment cover multiple invoices?

**Answer:** Yes. A single customer payment (e.g., one check) may be split across multiple open invoices.

**Schema decision — payment allocations:**
```
payments
  id
  customer_id FK
  payment_date
  payment_method     ← 'cash' | 'check' | 'card' | 'credit'
  check_number       TEXT NULLABLE
  amount_received    REAL
  notes

payment_allocations
  id
  payment_id FK
  invoice_id FK
  amount_applied     REAL
```

A payment with no allocations yet represents an unapplied credit on the customer account. `PaymentService.apply()` handles allocation logic.

---

### D2 — Can one invoice receive payments in multiple methods?

**Answer:** Yes — confirmed intentional. Multiple payment records of different types can be applied to the same invoice over time. Each is a separate `payment_allocations` row.

**Example:**
```
Invoice #INV-2026-0042  Total: $500
  Payment 1: $200 cash     — 2026-05-15
  Payment 2: $300 check    — 2026-05-22
  Status: Paid
```

---

### D3 — What happens when a check bounces?

**Answer:** The payment should be reversible, the invoice re-opens with the original balance due, and the system should support adding an NSF fee.

**Schema decision:**
```
payments.status              TEXT DEFAULT 'applied'   ← 'applied' | 'reversed' | 'nsf'
payments.reversed_at         DATETIME NULLABLE
payments.reversal_reason     TEXT NULLABLE            ← 'nsf' | 'stop_payment' | 'error'
payments.nsf_fee             REAL DEFAULT 0.0

payment_allocations.is_reversed  BOOLEAN DEFAULT 0
```

**PaymentService.reverse_nsf():** reverses all allocations, re-opens invoice balance, optionally adds NSF fee line to a new invoice or the original.

---

### D4 — Do you track check numbers?

**Answer:** Yes. Check number should be stored on every check payment for reconciliation.

**Schema decision:** Already captured above — `payments.check_number TEXT NULLABLE`.

---

## SECTION E — Purchase Orders & Receiving

---

### E1 — Can one PO have items from multiple vendors?

**Answer:** No. Each PO is tied to a single vendor. If ordering from multiple vendors, create separate POs per vendor.

**Schema decision:** `purchase_orders.vendor_id` is a required single FK — no change from current plan.

---

### E2 — Can one shipment contain items from multiple POs?

**Answer:** Yes. PAI may ship one box containing items from two POs placed on different days. The receiving workflow must allow one receipt session to allocate across multiple open POs from the same vendor.

**Schema decision:**
```
po_receipts
  id
  vendor_id FK           ← the vendor who shipped
  received_at
  received_by            ← user_id FK
  tracking_number        TEXT NULLABLE
  carrier                TEXT NULLABLE
  notes

po_receipt_lines
  id
  receipt_id FK
  po_id FK               ← which PO this line belongs to (can differ per line)
  po_line_id FK
  qty_received
  condition_notes        TEXT NULLABLE
```

One `po_receipts` record; each `po_receipt_lines` row explicitly references which PO it fulfills.

---

### E3 — Do vendors ever send you credits outside of core returns?

**Answer:** Yes. Volume rebates, pricing corrections, damaged shipment credits, freight adjustments, promotional allowances, and vendor returns all create non-core vendor credits.

**Schema decision:**
```
vendor_credits
  id
  vendor_id FK
  credit_number          TEXT NULLABLE   ← vendor's credit memo number
  credit_date
  credit_type            TEXT            ← 'rebate' | 'price_correction' | 'damaged_goods'
                                            | 'freight_adjustment' | 'promotional' | 'return' | 'other'
  amount
  po_id FK NULLABLE      ← if related to a specific PO
  qbo_id                 TEXT NULLABLE
  qbo_sync_status        TEXT DEFAULT 'pending'
  notes
  status                 TEXT DEFAULT 'open'   ← 'open' | 'applied' | 'closed'
```

---

### E4 — Does a vendor ever ship directly to your customer (drop-ship)?

**Answer:** Yes — especially important for future Shopify and eBay orders. Most orders flow through the shop, but direct-to-customer shipping from the vendor must be supported.

**Schema decision:**
```
purchase_orders.is_drop_ship         BOOLEAN DEFAULT 0
purchase_orders.drop_ship_customer_id INTEGER FK NULLABLE
purchase_orders.drop_ship_address_id  INTEGER FK NULLABLE   ← references customer_addresses
```

**InvoiceService / POService behavior:** When `is_drop_ship = true`, the PO PDF shows the customer's ship-to address instead of JAKS's address. Inventory impact: drop-ship PO receipt does NOT increase qty_on_hand (goods never touched JAKS stock). Invoice is created normally.

---

## SECTION F — Core Charges

---

### F1 — Is there a time limit for a customer to return a core?

**Answer:** Yes — support a return deadline but enforcement is flexible (relationship-based). Default: 30 days.

**Schema decision:**
```
core_charges.return_deadline     DATE NULLABLE       ← set at invoice time
core_charges.is_overdue          BOOLEAN             ← computed: today > return_deadline
settings.default_core_return_days  INTEGER DEFAULT 30
```

Dashboard widget shows cores overdue by customer. System warns but does not hard-block late returns.

---

### F2 — Can a core be partially returned?

**Answer:** Yes. Credit the returned quantity, leave the remainder open.

**Schema decision:**
```
core_charges.qty_charged     INTEGER   ← how many cores customer owes
core_charges.qty_returned    INTEGER DEFAULT 0
core_charges.qty_outstanding INTEGER   ← computed: qty_charged - qty_returned
core_charges.status          TEXT      ← 'open' | 'partial' | 'returned' | 'credited' | 'closed'
```

Each partial return creates a `core_return_events` record:
```
core_return_events
  id
  core_charge_id FK
  qty_returned
  returned_at
  credit_method    TEXT   ← 'account_credit' | 'check'
  credit_amount
  processed_by     FK user_id
  notes
```

---

### F3 — What if the vendor denies a core return?

**Answer:** Rare, because cores are usually inspected before accepting from the customer. But vendor rejection handling must be supported. Resolution (who absorbs the cost) is a manual business decision — the system records the outcome.

**Schema decision:**
```
core_charges.vendor_status       TEXT NULLABLE   ← 'pending' | 'accepted' | 'rejected'
core_charges.vendor_denial_reason TEXT NULLABLE
core_charges.vendor_decision_at  DATETIME NULLABLE
core_charges.denial_resolution   TEXT NULLABLE   ← 'absorbed_by_jaks' | 'charged_to_customer' | 'disputed'
core_charges.denial_notes        TEXT NULLABLE
```

---

## SECTION G — Returns & Warranty

---

### G1 — Is there a time limit for customer returns?

**Answer:** Return policy depends on multiple factors — vendor policy, product type, whether the item was special-ordered, and customer relationship. The system should evaluate and warn, never hard-block.

**Policy hierarchy:** Product level → Vendor level → Manager discretion

**Schema decisions:**

*Vendor fields:*
```
vendors.return_window_days          INTEGER NULLABLE
vendors.restock_fee_percent         REAL NULLABLE
vendors.special_order_returnable    BOOLEAN DEFAULT 0
```

*Product fields:*
```
products.is_returnable              BOOLEAN DEFAULT 1
products.return_policy_type         TEXT NULLABLE   ← 'standard' | 'non_returnable'
                                                       | 'special_order' | 'warranty_only'
products.return_window_override_days INTEGER NULLABLE
```

*Return transaction fields:*
```
return_authorizations.requested_at
return_authorizations.approved_by       FK user_id
return_authorizations.override_reason   TEXT NULLABLE
return_authorizations.vendor_policy_snapshot TEXT NULLABLE  ← JSON snapshot of policy at time of return
```

**Operational rule:** System guides and warns. User always has override. Business judgment is preserved.

---

### G2 — What happens to returned inventory?

**Answer:** Returned inventory does NOT automatically go back into available stock. The user inspects and selects a disposition before any inventory impact occurs.

**Supported dispositions:**
| Disposition | Inventory Impact |
|---|---|
| `return_to_stock` | qty_on_hand ↑ |
| `quarantine` | Moves to quarantine bin — not sellable |
| `vendor_return` | Staged for vendor RMA — not sellable |
| `damaged` | Written to damaged status — not sellable |
| `warranty_review` | Held for warranty evaluation |
| `scrap` | Written off — inventory ↓ via adjustment |

**Schema decision:**
```
return_lines.disposition         TEXT   ← see enum above
return_lines.inspected_by        FK user_id NULLABLE
return_lines.inspected_at        DATETIME NULLABLE
return_lines.condition_notes     TEXT NULLABLE
return_lines.qty_returned_to_stock INTEGER DEFAULT 0
```

**V1 UI prompt after return is received:**
```
"Inventory Disposition:"
[ ] Return to Available Stock
[ ] Hold in Quarantine
[ ] Stage for Vendor Return
[ ] Mark as Damaged
[ ] Scrap / Write Off
```

---

### G3 — Do you charge a restocking fee on all returns, or only some?

**Answer:** Supported but not rigidly automatic. Policy is stated (e.g., "up to 15%") but the user has full discretion to waive, reduce, or apply a custom amount per return.

**Schema decisions:**

*Settings:*
```
settings.default_restock_fee_percent   REAL DEFAULT 15.0
```

*Vendor override (future):*
```
vendors.default_restock_fee_percent    REAL NULLABLE
```

*Product override (future):*
```
products.restock_fee_percent           REAL NULLABLE
products.non_returnable                BOOLEAN DEFAULT 0
```

*Return transaction:*
```
return_authorizations.restock_fee_percent  REAL DEFAULT 0.0
return_authorizations.restock_fee_amount   REAL DEFAULT 0.0
return_authorizations.restock_fee_waived   BOOLEAN DEFAULT 0
return_authorizations.override_reason      TEXT NULLABLE
```

**Recommended invoice/quote footer text:**
> *Returns subject to inspection and may incur up to a 15% restocking fee. Special-order and electrical items may not be returnable.*

---

### G4 — Can one warranty claim cover multiple line items from the same invoice?

**Answer:** Yes. A failure may involve multiple related parts from the same job. One warranty claim covers all related parts.

**Schema decision — use a child lines table:**
```
warranty_claims                     ← header record
  id
  claim_number
  customer_id FK
  invoice_id FK
  claim_date
  status                            ← see statuses below
  failure_description
  submitted_to_vendor_at DATETIME NULLABLE
  vendor_id FK
  vendor_decision                   ← 'pending' | 'approved' | 'denied'
  vendor_decision_at DATETIME NULLABLE
  vendor_decision_notes
  resolution_type                   ← 'credit' | 'replacement' | 'denied'
  total_credit_amount REAL
  notes

warranty_claim_lines                ← child records — one per affected part
  id
  warranty_claim_id FK
  invoice_line_id FK
  product_id FK
  qty_claimed
  approved_qty
  resolution                        ← 'credit' | 'replacement' | 'partial_credit' | 'denied'
  credit_amount REAL
  replacement_invoice_line_id FK NULLABLE
```

**Warranty claim statuses:**
`draft` → `submitted_to_vendor` → `vendor_approved` → `customer_credited` → `closed`
`draft` → `submitted_to_vendor` → `vendor_denied` → `customer_notified` → `closed`

---

## SECTION H — Vendors & Contacts

---

### H1 — Do you have multiple contacts at the same vendor?

**Answer:** Yes. Same pattern as customers — one vendor record, multiple contacts underneath.

**Example contacts at PAI:**
- Sales / Pricing contact
- Warranty / Claims contact
- Accounts Receivable contact
- Returns / RMA contact
- Shipping / Tracking contact

**Schema decision:**
```
vendor_contacts
  id
  vendor_id FK
  name
  role / title
  phone
  email
  is_primary              BOOLEAN
  is_sales_contact        BOOLEAN
  is_warranty_contact     BOOLEAN
  is_returns_contact      BOOLEAN
  is_accounting_contact   BOOLEAN
  is_active               BOOLEAN DEFAULT 1
  notes
```

---

### H2 — Do vendors ever give you volume rebates or promotional allowances?

**Answer:** Yes. Volume discounts and rebates should be tracked for purchasing decisions. V1: track as a program record with a dashboard reminder when approaching threshold. Full accrual accounting not required in V1.

**Schema decision:**
```
vendor_programs
  id
  vendor_id FK
  program_name
  program_type           TEXT   ← 'volume_rebate' | 'promotional' | 'tier_discount' | 'other'
  threshold_amount       REAL NULLABLE
  discount_percent       REAL NULLABLE
  rebate_amount          REAL NULLABLE
  start_date             DATE NULLABLE
  end_date               DATE NULLABLE
  notes
  is_active              BOOLEAN DEFAULT 1
```

**Dashboard behavior:** When creating a PO, if vendor has an active program, show a reminder: "You've spent $X,XXX with PAI this quarter. $X,XXX until you hit the 5% rebate threshold."

---

## SECTION I — Users & Permissions

---

### I1 — Should Keith and his wife have different permission levels?

**Answer:** Both Keith and his wife would have Owner privileges. But the schema must support roles for future staff expansion.

**Roles (V1):**

| Role | Access |
|---|---|
| `admin` | Full access — create, edit, delete, void, all settings |
| `bookkeeping` | View all, edit financial records, push to QBO — no delete/void |
| `sales` | Quotes, customers, products, invoices, payments — no settings, no delete |
| `read_only` | View only — no create, edit, or delete |

**Schema decision:**
```
users
  id
  name
  username
  password_hash
  role              TEXT   ← 'admin' | 'bookkeeping' | 'sales' | 'read_only'
  is_active         BOOLEAN DEFAULT 1
  last_login_at     DATETIME NULLABLE
  created_at

user_sessions
  id
  user_id FK
  session_token
  created_at
  expires_at
  ip_address        TEXT NULLABLE
```

V1: `itsdangerous` signed session tokens already in requirements.txt. Build role-check middleware in `app/deps.py`.

---

### I2 — Do you need an audit log?

**Answer:** Yes — especially for invoices, payments, credits, inventory changes, QBO sync, and deletes/voids. V1 does not need to track every field change, but major financial/operational events must be logged.

**Priority records to audit:**
- Invoices (create, edit, lock, void)
- Payments (apply, reverse, NSF)
- Credits and credit memos
- Returns and warranty claims
- Purchase Orders (create, receive, approve bill)
- Inventory adjustments
- QBO sync events (success, failure)
- Deletes and voids (any record)

**Schema decision:**
```
audit_log
  id
  user_id FK NULLABLE        ← null if system event
  entity_type                ← 'invoice' | 'payment' | 'inventory' | 'po' | 'return' | etc.
  entity_id
  action                     ← 'created' | 'edited' | 'locked' | 'voided' | 'deleted'
                                | 'payment_applied' | 'payment_reversed' | 'qbo_synced'
                                | 'inventory_adjusted' | 'nsf' | 'core_received' etc.
  old_value                  TEXT NULLABLE   ← JSON snapshot of changed fields
  new_value                  TEXT NULLABLE   ← JSON snapshot after change
  notes                      TEXT NULLABLE
  changed_at                 DATETIME DEFAULT now()
  ip_address                 TEXT NULLABLE
```

**AuditService:** Called by every service when a significant event occurs. Thin — just inserts rows. Queried by an audit log viewer on record detail pages.

---

## SECTION J — Numbering & Startup

---

### J1 — Do sequence numbers reset each year?

**Answer:** Yes. Sequences reset on January 1st each year.

```
INV-2026-0001  ...  INV-2026-1247
→ January 1, 2027:
INV-2027-0001
```

**Schema decision:** Year is embedded in the document number. Settings store `next_{type}_number` as an integer that resets annually. A background task (or startup check) detects year rollover and resets counters.

```
settings.current_sequence_year    INTEGER   ← compared on startup; if different → reset all counters
settings.next_invoice_number      INTEGER DEFAULT 1
settings.next_quote_number        INTEGER DEFAULT 1
settings.next_so_number           INTEGER DEFAULT 1
settings.next_po_number           INTEGER DEFAULT 1
settings.next_ra_number           INTEGER DEFAULT 1
settings.next_wc_number           INTEGER DEFAULT 1
```

---

### J2 — What number do you want to start at?

**Answer:** Custom starting values must be configurable so the new system can continue from existing QBO or legacy app numbering without collisions.

**Settings page:** Each sequence has an editable "Start at" field. Admin sets these before go-live. After the first document is created, the field becomes read-only (can only be changed by resetting and confirming).

---

### J3 — Separate number sequences per document type?

**Answer:** Yes — separate sequences for every document type. Documents must NOT share a sequence. Quote, SO, and Invoice each keep their own identity through the conversion chain.

**Confirmed sequence set:**
```
INV   Invoices
Q     Quotes
SO    Sales Orders
PO    Purchase Orders
RA    Return Authorizations
WC    Warranty Claims
```

**Recommended additions (scaffold now, use later):**
```
CORE  Core tracking references (optional)
RCV   Receiving sessions (internal)
PAY   Payment receipts (optional)
```

**Conversion identity is preserved:**
```
Q-2026-0142  →  SO-2026-0098  →  INV-2026-0077
```
Each document keeps its own number. The chain is navigable via FK references, not shared numbering.

---

## SECTION K — Attachments & Notes

---

### K1 — Do you need to attach files to records?

**Answer:** Yes — support file attachments in the schema now, but keep V1 lightweight. Attachments are optional and never required for normal workflow operation.

**⚠️ Critical additional requirement — Core Document Workflow:**

Two dedicated printed core documents are required. These are **operational workflow documents**, NOT generic attachments.

**Document 1 — Customer Core Return Slip** (generated at invoice time):
- Travels with the customer when they buy a part with a core charge
- Reminds customer that a core is owed
- Contents: customer name, invoice#, date, part sold, core charge amount, return deadline, core tracking#, return instructions

**Document 2 — Vendor Core Return Sheet** (generated when shipping cores back):
- Accompanies the physical shipment to the vendor
- Contents: vendor name, shipment date, tracking#, RMA#, list of included cores (with original customer + invoice for each), expected vendor credit total

**Document type enum:**
```
'invoice'
'quote'
'sales_order'
'purchase_order'
'return_authorization'
'warranty_claim'
'customer_core_return_slip'    ← dedicated workflow document
'vendor_core_return_sheet'     ← dedicated workflow document
```

**General attachments schema:**
```
document_attachments
  id
  entity_type     TEXT   ← 'po' | 'invoice' | 'return' | 'warranty_claim'
                             | 'core_charge' | 'customer' | 'vendor' | 'product'
  entity_id       INTEGER
  file_name       TEXT
  file_path       TEXT
  file_size       INTEGER NULLABLE
  mime_type       TEXT NULLABLE
  document_type   TEXT NULLABLE   ← 'packing_slip' | 'signed_ra' | 'photo' | 'tax_cert' | 'other'
  uploaded_by     FK user_id
  uploaded_at     DATETIME
  notes           TEXT NULLABLE
```

---

### K2 — Do records need internal notes separate from customer-facing notes?

**Answer:** Yes. Many records need an internal notes field that does not print on customer-facing documents.

**Example:** "Customer was difficult about pricing — verify before discounting."

**Schema decision:** Add `internal_notes` to all major document tables:
```
quotes.internal_notes
invoices.internal_notes
sales_orders.internal_notes
purchase_orders.internal_notes
return_authorizations.internal_notes
warranty_claims.internal_notes
customers.internal_notes         ← replaces / supplements existing notes field
vendors.internal_notes
```

`notes` = prints on document. `internal_notes` = never printed, staff only.

---

## SECTION L — Shipping

---

### L1 — Do you charge customers for shipping?

**Answer:** Yes. Shipping charges appear as separate line items on invoices. Local delivery customers may be charged a fuel service charge.

**Supported charge types (as invoice line items):**
```
'shipping'
'freight'
'local_delivery'
'fuel_service_charge'
'handling'
```

**Schema decision:** Line type field already recommended — formalized here:
```
invoice_lines.line_type    TEXT DEFAULT 'product'
  Values:
  'product'
  'core_charge'
  'shipping'
  'freight'
  'local_delivery'
  'fuel_service_charge'
  'discount'
  'restocking_fee'
  'warranty_credit'
  'nsf_fee'
  'misc'
```

**Future settings:**
```
customers.delivery_type                TEXT   ← 'local_delivery' | 'pickup' | 'ship'
settings.default_fuel_service_charge   REAL
settings.default_shipping_charge       REAL
```

---

### L2 — Do you track outbound shipping costs (what you pay UPS/FedEx)?

**Answer:** Yes. Actual outbound shipping cost is tracked separately from what the customer is charged. This internal cost data is used for profitability analysis and carrier comparison.

**Schema decision:**
```
shipments
  id
  invoice_id            FK NULLABLE
  sales_order_id        FK NULLABLE
  carrier               TEXT   ← 'UPS' | 'FedEx' | 'LTL' | 'local_delivery'
                                   | 'vendor_drop_ship' | 'customer_pickup'
  tracking_number       TEXT NULLABLE
  shipping_method       TEXT NULLABLE
  actual_shipping_cost  REAL DEFAULT 0.0   ← what JAKS paid
  customer_shipping_charge REAL DEFAULT 0.0   ← what customer paid (mirrors invoice line)
  shipped_at            DATETIME NULLABLE
  delivered_at          DATETIME NULLABLE
  notes                 TEXT NULLABLE
```

`actual_shipping_cost` does NOT print on customer invoices. Used for internal margin reporting only.

---

## Additional Schema Decisions (Architectural Notes)

---

### N1 — Inventory Locations

Even if JAKS uses one location today, the schema must support bin/shelf/warehouse locations for future expansion.

```
inventory_locations
  id
  name         TEXT   ← 'Main Warehouse' | 'Bin A-12' | 'Shelf 3' etc.
  code         TEXT NULLABLE
  description  TEXT NULLABLE
  is_active    BOOLEAN DEFAULT 1

product_location_stock
  id
  product_id FK
  location_id FK
  qty_on_hand  INTEGER DEFAULT 0
  bin_number   TEXT NULLABLE
```

V1: Create one default location "Main". All stock goes there. Multi-location UI comes later.

---

### N2 — Inventory Transactions (Full Ledger)

Never store only current qty_on_hand. Every inventory change must create a transaction record. The current qty is always derivable from the transaction log (or cached for performance).

```
inventory_transactions
  id
  product_id FK
  location_id FK NULLABLE
  transaction_type   TEXT   ← 'po_receipt' | 'invoice_sale' | 'return_to_stock'
                                | 'manual_adjustment' | 'so_committed' | 'so_released'
                                | 'transfer' | 'write_off' | 'initial_count'
  qty_change         INTEGER   ← positive = in, negative = out
  qty_after          INTEGER   ← snapshot of qty_on_hand after this transaction
  reference_type     TEXT NULLABLE   ← 'po_receipt' | 'invoice' | 'return' | 'adjustment'
  reference_id       INTEGER NULLABLE   ← FK to the source record
  reason             TEXT NULLABLE   ← for manual adjustments
  performed_by       FK user_id NULLABLE
  performed_at       DATETIME DEFAULT now()
  notes              TEXT NULLABLE
```

**Adjustment reasons enum:**
`damaged` | `lost` | `cycle_count` | `vendor_shortage` | `write_off` | `opening_count` | `correction`

---

### N3 — Vendor Cost History

Vendor costs change. The schema must store cost history so margin can be calculated accurately using the cost at time of purchase, not today's cost.

```
product_cost_history
  id
  product_id FK
  vendor_id FK
  old_cost    REAL
  new_cost    REAL
  changed_at  DATETIME
  changed_by  FK user_id NULLABLE
  po_id FK NULLABLE   ← cost discovered on this PO receipt
  notes       TEXT NULLABLE
```

`product_vendor_sources.vendor_cost` stores the current cost. `product_cost_history` stores the full trail. Invoice lines store `unit_cost` at time of sale (snapshot).

---

### N4 — QBO Sync Status (All Synced Records)

Any record that can be pushed to QBO must store sync metadata.

**Add these fields to: invoices, payments, purchase_orders, vendor_bills, vendor_credits**

```
qbo_id              TEXT NULLABLE   ← QBO's internal ID for this record
qbo_sync_status     TEXT DEFAULT 'pending'   ← 'pending' | 'synced' | 'error' | 'skipped'
qbo_last_synced_at  DATETIME NULLABLE
qbo_sync_error      TEXT NULLABLE   ← error message if sync failed
qbo_sync_retry_count INTEGER DEFAULT 0
```

---

### N5 — Freight-In / Landed Cost

Purchase orders should support freight-in cost so true product margin (including the cost to receive the goods) can be calculated.

```
purchase_orders.freight_in_cost    REAL DEFAULT 0.0   ← total freight charged by vendor on this PO
po_lines.landed_cost_per_unit      REAL NULLABLE       ← allocated freight-in per unit (computed)
```

Freight-in is allocated proportionally to PO lines by cost or qty. `InvoiceService` uses `landed_cost_per_unit` when calculating true margin on invoice lines.

---

### N6 — Barcodes / QR Codes (Design-Ready, Build Later)

Core return slips, vendor core return sheets, receiving labels, and inventory labels should be designed to accommodate barcodes or QR codes in the future without schema changes.

**Add to relevant records:**
```
core_charges.core_tracking_number   TEXT NULLABLE   ← scannable reference for future barcode
products.barcode                    TEXT NULLABLE   ← UPC or internal barcode
po_receipts.receiving_label_ref     TEXT NULLABLE
```

---

### N7 — Source of Truth Declaration (Architectural Principle)

```
JAKS Inventory App  →  Operational source of truth
                        Inventory, cores, quotes, SOs, returns, warranties

QuickBooks Online   →  Accounting source of truth
                        P&L, tax, payroll, banking reconciliation

JAKS pushes TO QBO: invoices, payments, vendor bills
JAKS does NOT pull FROM QBO: inventory, products, customers
QBO does NOT push TO JAKS: anything
```

This rule governs every integration decision in Phase 1 and beyond.

---

## Consolidated Schema Decisions — Master List

### New Tables Required (not yet in models)

| Table | Purpose |
|---|---|
| `product_vendor_sources` | Multi-vendor sourcing per product with separate SKUs and costs |
| `product_categories` | Self-referential tree, 3 levels max |
| `product_cost_history` | Historical record of cost changes per vendor |
| `product_images` | Multiple images per product, source tracking |
| `product_serial_numbers` | Serial tracking for cylinder heads and other serialized items |
| `product_kits` | Kit header record (vendor kit or JAKS-built kit) |
| `product_kit_lines` | BOM lines for JAKS-built kits |
| `customer_addresses` | Multiple addresses per customer (billing + ship-to) |
| `customer_contacts` | Multiple contacts per customer |
| `vendor_contacts` | Multiple contacts per vendor |
| `vendor_credits` | Non-core vendor credits (rebates, pricing corrections, etc.) |
| `vendor_programs` | Volume rebate / promotional discount programs |
| `sales_orders` | Missing workflow step between Quote and Invoice |
| `so_lines` | Sales Order line items with fulfillment tracking |
| `po_receipts` | Receiving sessions (can span multiple POs) |
| `po_receipt_lines` | Individual receipt lines tied to specific PO lines |
| `vendor_bills` | Vendor billing records (3-way match) |
| `vendor_bill_lines` | Line-level billing detail |
| `return_authorizations` | Standard return header |
| `return_lines` | Return line items with disposition |
| `warranty_claims` | Warranty claim header |
| `warranty_claim_lines` | Multi-part warranty claim lines |
| `cross_references` | OEM/competitor/vendor part number cross-references |
| `price_history` | Last sold price per product per customer |
| `payments` | Payment records (multi-invoice, multi-method) |
| `payment_allocations` | Payment → Invoice allocation (many-to-many) |
| `core_return_events` | Partial core return events |
| `shipments` | Outbound shipment tracking (actual cost vs. customer charge) |
| `inventory_locations` | Warehouse / bin / shelf locations |
| `inventory_transactions` | Full inventory ledger (every qty change) |
| `document_attachments` | File attachments for any entity |
| `users` | Multi-user with roles |
| `user_sessions` | Session token storage |
| `audit_log` | Financial/operational change tracking |

### Scaffold Only (empty tables, architecture placeholder)
| Table |
|---|
| `esn_lookups` |
| `engine_configs` |
| `quote_followups` |
| `lost_sales_log` |

### Existing Tables — Fields to Add

| Table | New Fields |
|---|---|
| `products` | `status`, `superseded_by_id`, `unit_of_measure`, `reorder_point`, `max_stock_level`, `vendor_core_charge`, `customer_core_charge`, `has_serial_number`, `kit_type`, `barcode`, `is_returnable`, `return_policy_type`, `non_returnable`, `restock_fee_percent`, `special_order_only`, `internal_notes` |
| `vendors` | `vendor_code`, `payment_terms`, `return_window_days`, `restock_fee_percent`, `special_order_returnable`, `internal_notes` |
| `customers` | `payment_terms`, `interest_rate`, `tax_rate`, `credit_balance`, `is_tax_exempt`, `tax_exempt_cert_number`, `tax_exempt_cert_file`, `delivery_type`, `internal_notes` |
| `quotes` | `expires_at`, `validity_days`, `follow_up_date`, `outcome`, `lost_reason`, `reactivated_at`, `reactivated_by`, `original_expires_at`, `internal_notes` |
| `invoices` | `customer_po_number`, `customer_job_number`, `esn`, `engine_manufacturer`, `engine_model`, `locked_at`, `lock_reason`, `sales_order_id`, `primary_so_id`, `qbo_id`, `qbo_sync_status`, `qbo_last_synced_at`, `qbo_sync_error`, `qbo_sync_retry_count`, `internal_notes` |
| `invoice_lines` | `line_type`, `so_line_id`, `unit_cost` (snapshot at sale time) |
| `purchase_orders` | `is_drop_ship`, `drop_ship_customer_id`, `drop_ship_address_id`, `freight_in_cost`, `qbo_id`, `qbo_sync_status`, `qbo_last_synced_at`, `qbo_sync_error`, `internal_notes` |
| `po_lines` | `qty_received`, `qty_billed`, `qty_outstanding`, `landed_cost_per_unit` |
| `payments` | `status`, `reversed_at`, `reversal_reason`, `nsf_fee`, `qbo_id`, `qbo_sync_status`, `qbo_last_synced_at`, `qbo_sync_error` |
| `core_charges` | `qty_charged`, `qty_returned`, `qty_outstanding`, `return_deadline`, `vendor_status`, `vendor_denial_reason`, `vendor_decision_at`, `denial_resolution`, `denial_notes`, `core_tracking_number` |
| `settings` | `default_core_return_days`, `default_restock_fee_percent`, `default_fuel_service_charge`, `default_shipping_charge`, `business_close_time`, `current_sequence_year`, `next_ra_number`, `next_wc_number`, `next_so_number` |

---

*This document is the complete input to Step 0 — Schema Finalization.*
*No routes, no templates, no services until every table and field in this file is in the schema.*
