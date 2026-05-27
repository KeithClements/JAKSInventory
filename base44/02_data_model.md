# 02 — Data Model

The desktop app's schema is the result of 115 sequential migrations. Below is
the **consolidated target schema** Base44 should build to. Where a table is
optional for MVP, it is marked `(optional)`.

Conventions:
- All `id` columns: `INTEGER PRIMARY KEY AUTOINCREMENT` (SQLite) or `BIGSERIAL` (Postgres).
- All `*_at` columns: `TIMESTAMP` defaulting to `CURRENT_TIMESTAMP`.
- Money: `REAL` (USD); always 2-decimal display.
- Booleans: `INTEGER 0/1` in SQLite, native `BOOLEAN` in Postgres.

---

## A. Core catalog

### `products`  (≈92 columns in current schema)
Primary stock master. Source of truth for SKU, cost, list price, on-hand.

Essential columns:
```
id, sku UNIQUE NOT NULL, handle, url, title, category, subcategory,
manufacturer, supplier, engine, condition (NEW/REMAN/USED/CORE/KIT),
product_type,

pai_cost REAL, cost REAL, selling_price REAL, compare_at_price REAL,
map_price REAL, core_charge REAL, price_category_id FK,

pai_sku, pai_status, pai_stock, pai_link,
description_html, brief_description, kit_contents,

image_count INT, qty_on_hand INT, reorder_point INT, min_qty INT, max_qty INT,
bin_location, stage,

shopify_id, shopify_variant_id, shopify_status, shopify_published_at,
shopify_inventory_item_id, needs_shopify_update, handle,

oem_part_number, oem_manufacturer,
truck_manufacturer, truck_model, truck_system,

has_core 0/1, core_sell_price, core_return_days, core_notes,
supplier_warranty_enabled, supplier_warranty_value, supplier_warranty_unit,
jaks_warranty_enabled, jaks_warranty_value, jaks_warranty_unit,
jaks_warranty_charge, warranty_percentage REAL DEFAULT 10.0,
is_warrantable 0/1,

vendor_description, vendor_availability, vendor_alternates,
private_label 0/1, private_label_part_number, preferred_vendor_id FK,

weight, weight_unit DEFAULT 'LBS',
length, width, height, dimension_unit DEFAULT 'IN', shipping_cost,

tags, seo_title, seo_description, publish_shopify 0/1,
notes_public, notes_internal,
requires_serial 0/1, requires_serial_receive 0/1, requires_serial_warranty 0/1,

created_at, updated_at, scraped_at, uploaded_at, shopify_synced_at,
cost_updated_at, pricing_updated_at, local_modified_at,
hhp_last_price_check, hhp_price_history
```

### `product_qty_tiers`
Per-product quantity discount tiers (replaces flat tier system for ad-hoc SKUs).
```
id, product_id FK, name TEXT, min_qty INT, price REAL,
discount_pct REAL, sort_order INT, created_at
```

### `product_images`
```
id, product_id FK, url TEXT, local_path TEXT, position INT,
is_primary 0/1, alt_text, created_at
```

### `product_interchanges` / `xref`
OEM crossrefs. A SKU has many crossrefs.
```
id, product_id FK, type ('OEM'|'aftermarket'|'private'),
number TEXT, manufacturer TEXT, notes, created_at
```

### `suggested_sells`
"Customers who bought X also bought Y."
```
id, product_id FK, suggested_product_id FK,
relationship_type ('recommended'|'required'|'upsell'|'cross-sell'),
notes, sort_order, created_at
```

### `product_fitments`
Engine/truck applicability.
```
id, product_id FK, engine_manufacturer, engine_model, engine_year_from, engine_year_to,
truck_manufacturer, truck_model, system, notes
```

### `part_supersessions`
Chain of OEM replacements.
```
id, old_number TEXT, new_number TEXT, manufacturer TEXT, effective_date, notes
```

---

## B. Pricing v2 (categories × tiers × bands)

### `price_categories`
```
id, code UNIQUE, name, sort_order, active 0/1
```

### `cost_bands`
```
id, code, name, min_cost REAL, max_cost REAL, sort_order
```

### `customer_tiers`
```
id, code UNIQUE ('RETAIL'|'DEALER'|'FLEET'|'WHOLESALE'|...),
name, is_default 0/1, sort_order
```

### `tier_category_discounts`
The 3-axis grid: which discount % applies to category C × tier T × band B.
```
id, tier_id FK, category_id FK, band_id FK, discount_pct REAL
```

### `manufacturer_categories`, `vendor_categories`
Default category resolution if a product has none.
```
id, manufacturer_or_vendor TEXT, category_id FK
```

---

## C. Customers & Sales

### `customers`
```
id, name NOT NULL, code (auto: CUST-####), customer_type ('walk-in'|'business'|'fleet'),
tier_id FK customer_tiers, default_tax_rate_id FK, on_hold 0/1, hold_reason,
phone, email, website,
billing_address_id FK, shipping_address_id FK,
credit_limit REAL, credit_terms TEXT, balance REAL,
notes_public, notes_internal, created_at, updated_at,
qbo_customer_id, shopify_customer_id
```

### `customer_addresses`, `customer_employees`, `customer_notes`
Standard hub tables.

### `customer_credits`
Open credit memos / store credit.
```
id, customer_id FK, amount REAL, reason, source_type, source_id,
balance_remaining REAL, created_at, qbo_credit_memo_id
```

### `customer_warranties`
Per-line warranty terms captured at sale time.

### `quotes`
```
id, number UNIQUE, customer_id FK, status ('Draft'|'Sent'|'Won'|'Lost'|'Expired'),
contact_name, contact_phone, contact_email,
subtotal, tax, total, deposit_required REAL, eta TEXT,
manufacturer_job 0/1, esn TEXT, vin TEXT,
warranty_offered 0/1, extended_warranty_offered 0/1,
follow_up_at, follow_up_owner, lost_reason_code, lost_reason_notes,
created_at, updated_at, sent_at, won_at, lost_at, expires_at,
qbo_estimate_id, shopify_draft_order_id
```

### `quote_lines`
```
id, quote_id FK, product_id FK NULL, sku TEXT, description TEXT,
qty INT, price REAL, cost REAL, discount_pct REAL, core_charge REAL,
warranty_charge REAL, tax_rate_id FK, line_total REAL,
parent_line_id FK (for kit children), kind ('part'|'kit'|'core'|'labor'|'discount'),
state ('pending'|'allocated'|'shipped'),
sort_order INT
```

### `quote_status_log` — every status change recorded.
### `quote_comments`, `quote_options` — multi-option quotes.

### `sales_orders` (SOs)
Same shape as quotes but adds:
```
quote_id FK NULL, pick_ticket_printed_at, packing_slip_printed_at,
ship_method, tracking_number, ship_via, shipped_at,
qbo_invoice_id, qbo_sales_order_id
```

### `so_lines` — mirrors `quote_lines` with `state` actually used.

### `invoices`
```
id, number UNIQUE, customer_id FK, sales_order_id FK NULL,
status ('Open'|'Partial'|'Paid'|'Void'|'Refunded'),
subtotal, tax, total, balance,
finalized_at, first_printed_at, sent_at,
qbo_invoice_id, qbo_sync_status, qbo_synced_at
```

### `invoice_lines` — same shape as `so_lines`, snapshotted at invoice time.

### `invoice_payments`
```
id, invoice_id FK, method ('cash'|'card'|'check'|'ach'|'credit'),
amount REAL, reference TEXT, paid_at, qbo_payment_id,
cc_convenience_fee REAL, surcharge REAL
```

### `discount_lines` — header-level discounts applied to a doc.

### `lost_sales`
```
id, customer_id FK, product_id FK NULL, sku, reason, estimated_value,
created_at, source ('quote'|'walk-in'|'inquiry')
```

---

## D. Inventory operations

### `inventory_audit`
Append-only log for every qty change. **Required for any inventory mutation.**
```
id, product_id FK, delta INT, reason ('PO_RECEIVE'|'SO_SHIP'|'ADJUST'|'COUNT'|'RETURN'|'CORE'|'TRANSFER'),
source_type, source_id, qty_before, qty_after, user_id, notes, created_at
```

### `adjustments`
```
id, product_id FK, qty_change INT, reason TEXT, reason_code TEXT,
notes, created_at, created_by, qbo_journal_entry_id
```

### `locations` (optional for MVP)
```
id, code, name, address, is_default 0/1
```

### `location_stock` (optional)
```
id, location_id FK, product_id FK, qty_on_hand INT
```

### `transfers`, `transfer_lines` (optional) — between locations.

### `kits`, `kit_components`
```
kits: id, parent_product_id FK, name, explode_on_sale 0/1
kit_components: id, kit_id FK, product_id FK, qty INT, sort_order
```

### `serials`
```
id, product_id FK, serial_number, status ('available'|'reserved'|'sold'|'returned'),
acquired_at, sold_at, customer_id FK NULL, invoice_id FK NULL, notes
```

---

## E. Purchasing

### `vendors`
```
id, code UNIQUE, name NOT NULL, vendor_type ('reman'|'new'|'service'),
contact_name, phone, email, website, terms ('Net30'|'COD'|...),
cutoff_time_local TIME, payment_method, account_number, notes,
qbo_vendor_id, default_carrier, lead_time_days INT
```

### `purchase_orders`
```
id, number UNIQUE, vendor_id FK, status ('Draft'|'Sent'|'Acknowledged'|'Partial'|'Received'|'Closed'|'Cancelled'),
order_date, expected_date, received_date,
ship_to_location_id FK, bill_to_address, ship_method,
subtotal, tax, freight, total,
linked_so_id FK NULL,        -- when PO is fulfilling a specific SO
notes, created_at, sent_at,
qbo_po_id, qbo_bill_id
```

### `po_lines`
```
id, po_id FK, product_id FK, qty INT, qty_received INT,
unit_cost REAL, line_total REAL, core_charge REAL,
expected_date, notes
```

### `purchase_receipts`
```
id, po_id FK, receipt_number, received_at, received_by,
packing_slip_number, bol_number, carrier, tracking_number, notes
```

### `purchase_receipt_lines`
```
id, receipt_id FK, po_line_id FK, qty INT, condition_notes,
serials TEXT (JSON array if requires_serial_receive)
```

### `landed_costs`
```
id, po_id FK, kind ('freight'|'duty'|'broker'|'other'),
amount REAL, allocation ('value'|'qty'|'weight'), notes
```

---

## F. Core handling

This is the most complex subsystem. Read [03_business_rules.md#cores](03_business_rules.md) alongside.

### `customer_cores`
Each refundable core charge that we owe a customer back if returned.
```
id, invoice_id FK, invoice_line_id FK, product_id FK,
customer_id FK, core_charge REAL, status ('outstanding'|'returned'|'forfeited'),
due_back_by DATE, returned_at, returned_serial,
credit_id FK NULL,   -- the customer_credit issued upon return
notes
```

### `vendor_core_obligations`
Cores we owe back to a vendor (because they sold us REMAN parts; when we
accumulate enough customer-returned cores, we send them back via RGA for credit).
```
id, vendor_id FK, po_line_id FK NULL, product_id FK,
core_type ('refundable'|'conditional'|'no-charge'),
amount REAL, status ('outstanding'|'allocated'|'shipped'|'credited'|'forfeited'),
rga_id FK NULL, notes, created_at, due_back_by DATE
```

### `vendor_returns` (RGAs)
```
id, number UNIQUE, vendor_id FK, status ('Draft'|'Submitted'|'Approved'|'Shipped'|'Credited'),
type ('core'|'warranty'|'overstock'),
rga_number TEXT (vendor's number), submitted_at, shipped_at, credited_at,
expected_credit REAL, actual_credit REAL,
carrier, tracking_number, notes
```

### `vendor_return_lines`
```
id, vendor_return_id FK, product_id FK, qty INT, unit_credit REAL,
vendor_core_obligation_id FK NULL, condition_notes
```

### `vendor_return_audit` — every status change.

---

## G. Pricing maintenance

### `tax_rates`
```
id, code, name, rate REAL, jurisdiction, qbo_tax_code_id
```

### `shipping_rates`
```
id, name, carrier, method, calc_type ('flat'|'weight'|'value'),
flat_amount REAL, rate_per_lb REAL, min_charge REAL, max_charge REAL
```

### `pricing_overrides` (optional)
Customer-specific contract pricing.
```
id, customer_id FK, product_id FK, price REAL, effective_from, effective_to
```

---

## H. Customers & marketing

### `messages` (SMS / email log)
### `marketing_campaigns`, `campaign_recipients`
### `automation_rules` (optional MVP)

---

## I. Integrations & sync

### `scrape_runs`
```
id, source ('pai'|'hhp'|'atl'|'cummins'), kind ('scan'|'scrape'|'enrich'|'review'),
status ('running'|'success'|'failed'), started_at, finished_at,
items_in, items_out, error_message
```

### `qbo_sync_log`
```
id, entity_type, entity_id, action ('push'|'pull'|'webhook'),
status ('pending'|'success'|'failed'|'ignored'),
qbo_id TEXT, request_payload TEXT, response_payload TEXT,
error_message, created_at, resolved_at
```

### `qbo_sync_queue`
```
id, entity_type, entity_id, action, priority INT,
attempts INT, next_attempt_at, status, payload, created_at
```

### `qbo_webhook_events`
```
id, event_id UNIQUE, entity_type, entity_id_qbo, operation,
received_at, processed_at, status
```

### `shopify_sync_cache`
```
id, sku, shopify_product_id, shopify_variant_id, content_hash,
last_pushed_at, last_pulled_at
```

### `sms_log`
```
id, direction ('out'|'in'), to_number, from_number, body,
status, provider_message_id, related_entity_type, related_entity_id, sent_at
```

---

## J. Settings & meta

### `company_settings` — singleton row.
### `users`, `roles`, `user_roles` — for permissions.
### `settings_kv` — generic key/value store for anything not worth a column.
### `audit_log` — login, permission changes, settings changes.
### `sku_sequences` — for auto-generating SKUs from prefixes.

---

## Entity-relationship summary (text)

```
Customer ─< Quote ─< QuoteLine >─ Product
                │
                └─→ SalesOrder ─< SOLine ─→ Product
                          │
                          └─→ Invoice ─< InvoiceLine ─→ Product
                                  │
                                  └─< Payment

Vendor ─< PurchaseOrder ─< POLine ─→ Product
              │
              └─< Receipt ─< ReceiptLine ─→ POLine
              │
              └─< VendorReturn ─< VRLine ─→ Product

Invoice ─< CustomerCore ─→ Product
                │
                └─→ CustomerCredit (when returned)

Product ─< Interchange    (OEM crossrefs)
       ─< SuggestedSell   (cross-sell graph)
       ─< Fitment         (engine/truck applicability)
       ─< Image
       ─< QtyTier         (quantity discount table)

Customer ─< Address ─< Employee ─< Note ─< Credit ─< Warranty

InventoryAudit  ←  every qty change everywhere
```

## Indexes (mandatory)

- `products(sku)` UNIQUE
- `products(oem_part_number)`
- `products(preferred_vendor_id)`
- `products(stage)`
- `product_interchanges(number)` — for crossref lookup
- `customers(name)` — fuzzy search
- `quotes(customer_id, status)`
- `invoices(customer_id, status)`
- `purchase_orders(vendor_id, status)`
- `inventory_audit(product_id, created_at)`
- `qbo_sync_queue(status, next_attempt_at)`
