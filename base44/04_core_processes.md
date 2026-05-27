# 04 — Core Processes

The end-to-end flows that have to work for the business to function.

For each process the spec gives: **trigger → preconditions → steps → outputs →
side effects**. Read alongside [03_business_rules.md](03_business_rules.md).

---

## P1. Product entry

**Trigger:** Counter sales receives a part they've never sold. Or purchasing
needs to add a vendor's catalog item.

### P1a. Manual entry (single SKU)
1. Click **+ New Product** on the Products screen.
2. ProductWorkbench dialog opens (see [07_modules/02_inventory.md](07_modules/02_inventory.md)).
3. User fills sections (Basic, Pricing, Supplier minimum required).
4. On save:
   - `create_product(**data)` writes `products` row.
   - Cross-window signal `product_changed` fires.
   - QBO Item upsert enqueued.
   - Shopify publish enqueued if `publish_shopify = 1`.

### P1b. AI Catalog Import (PAI bulk)
1. Click **AI Catalog Import** on Products screen.
2. User pastes a list of PAI part numbers OR uploads CSV.
3. Scraper (see [08_integrations/01_pai_scraper.md](08_integrations/01_pai_scraper.md))
   pulls each part's data from portal.pai.com.
4. Results land in a review table:
   - Match column: 🟢 new, 🟡 existing different cost, 🔴 conflict.
5. User clicks **Accept** per row OR **Bulk Accept**.
6. Same downstream as P1a (signals, QBO, Shopify).

### P1c. HHP-driven sourcing
1. Tools → HHP Scraper screen.
2. User picks a category (HHP_MAIN_CATEGORIES from `core/constants.py`).
3. 5-phase pipeline runs: scan → scrape → PAI enrich → review → upload.
4. Each phase emits progress; review phase is interactive.
5. Final upload calls `sync_products_batch` which dedup-matches by OEM number
   or HHP SKU, creates new SKUs, and links cross-refs.

### P1d. Bulk Import CSV
1. Inventory → Bulk Import.
2. Upload CSV → mapping screen (auto-suggests column → field).
3. Validation pass: required fields, type coercion, dedupe check.
4. Commit → progress bar → results report (created / updated / skipped / errors).

**Outputs (all paths):** `products` rows, `product_images`, `product_interchanges`,
optionally `product_fitments`.

---

## P2. Quote → Sales Order → Invoice → Payment

**The single most important flow in the app.** Everything else exists to support it.

### P2a. Create a quote
1. Sales → Quotes → **New Quote**.
2. Pick customer (customer_picker_dialog): existing or create new.
3. Add lines via Part Finder OR by typing SKU/OEM/keyword.
4. For each line:
   - System auto-resolves price via the resolution order in
     [03_business_rules.md#pricing](03_business_rules.md).
   - If `has_core = 1`, an automatic core-charge line appears below as a child.
   - User can toggle "+ Extended warranty" if `is_warrantable = 1`.
5. Optional: choose tax rate, shipping method, deposit %, ETA.
6. Save Draft → `quotes` + `quote_lines` rows; status `Draft`.
7. Send → PDF generated, SMS/email sent, status flips to `Sent`,
   `quote_status_log` row written.

### P2b. Lost / Won
- Lost: select reason code from `quote_lost_reasons`, optional notes.
  `lost_sales` row created for analytics.
- Won: proceeds to P2c.

### P2c. Convert quote → SO
1. From quote screen click **Convert to SO** (only when Won or Sent).
2. `new_so_dialog` opens with all lines pre-filled.
3. User confirms ship-to, ship method, ETA.
4. Save:
   - `sales_orders` row created, lines duplicated to `so_lines`.
   - Each `so_line` enters state `pending`; qty becomes **reserved**.
   - Inventory check: any line with `qty > available` shows a backorder warning.
   - Source quote status flips to `Won` if not already.

### P2d. Pick & pack
1. Warehouse opens Sales Orders screen, sees new SO.
2. Print Pick Ticket (PDF) → marks `pick_ticket_printed_at`.
3. Pull stock physically.
4. Pack & Ship dialog → enter tracking number, carrier.
5. Save:
   - `so_lines.state` → `shipped` per line.
   - `adjust_qty(product_id, -qty, 'SO_SHIP', ...)` per line.
   - SO status → `Shipped` (or `Partial` if some lines back-ordered).

### P2e. Convert SO → Invoice
1. Click **Convert to Invoice** (allowed once any line shipped, or anytime if
   the SO is set to "invoice on order").
2. `invoices` row created, `invoice_lines` snapshotted from `so_lines`.
3. Invoice status `Open`, `finalized_at = NOW`.
4. QBO push enqueued.
5. Email/SMS invoice to customer if enabled.

### P2f. Take payment
1. Open invoice → **Take Payment** button.
2. `payment_dialog`: pick method, enter amount, reference.
3. If method=card, optional CC convenience fee per
   [03_business_rules.md](03_business_rules.md).
4. Save:
   - `invoice_payments` row.
   - `invoices.balance -= amount`.
   - Status → `Partial` or `Paid`.
   - QBO Payment push enqueued.
   - `payment_received` signal fires; AR aging refreshes.

---

## P3. Purchase Order → Receive → Bill

### P3a. Create PO

Two trigger paths:

**Manual:** Purchasing → POs → **New PO** → pick vendor → add lines.

**From Low Stock:** Inventory → Low Stock → tick SKUs → **Create POs from Tagged**.
The system groups tagged SKUs by `preferred_vendor_id` and creates one Draft PO
per vendor.

**From SO:** Sales Order line marked "drop ship" auto-creates a PO line linked
via `purchase_orders.linked_so_id`.

### P3b. PO body
1. po_dialog opens. Lines list with: product, qty, unit_cost, line_total, core_charge, expected_date.
2. Header fields: vendor, terms (auto from vendor), ship-to location, ship method, notes.
3. Save Draft → `purchase_orders` + `po_lines`; status `Draft`.
4. Send → PDF, email to `vendor.email`, status `Sent`, `sent_at` stamped.
5. Vendor acknowledgement (manual): mark `Acknowledged`.

### P3c. Receive
1. PO screen → highlight a `Sent`/`Acknowledged` PO → **Receive**.
2. `receive_dialog` opens with each PO line and a `qty_to_receive` input
   pre-filled with `qty - qty_received`.
3. For each line, if `requires_serial_receive = 1`, prompt for serials.
4. Save:
   - `purchase_receipts` row + `purchase_receipt_lines`.
   - `po_lines.qty_received += received_qty`.
   - `adjust_qty(product_id, +qty, 'PO_RECEIVE', ...)` per line.
   - If any serials, write `serials` rows with `status='available'`.
   - If all lines fully received → PO status `Received`. Else `Partial`.
   - If vendor charges cores on this PO line (REMAN), open
     `vendor_core_obligations` row per unit.

### P3d. Bill
1. When PO fully received OR user clicks **Create Bill** on Partial:
   - QBO `Bill` push enqueued tying to PO.
   - `purchase_orders.qbo_bill_id` populated on success.

### P3e. Landed cost
1. After receipt, user can add freight / duty / broker line items in
   po_dialog → Landed Costs tab.
2. `landed_costs` rows created. Allocation distributes the cost across PO lines
   by chosen method (`value`, `qty`, or `weight`).
3. `products.cost` is updated to reflect the new weighted-average landed cost.

---

## P4. Customer core return → vendor RGA

### P4a. Customer brings a core back
1. Cores → Customer Cores → search by customer name or invoice number.
2. Show outstanding `customer_cores` rows with aging.
3. User selects the line(s) being returned.
4. If serialized: scan/type the returned serial; must match the issued serial
   (or accept manual override with reason).
5. Click **Accept Return**.
6. System:
   - Marks `customer_cores.status = 'returned'`.
   - Creates `customer_credits` row for the core charge.
   - Creates a `vendor_core_obligations` row tying the physical unit to its
     supplier vendor.
   - Adjusts qty for the spent unit (it's a CORE SKU, +1).
   - Customer can immediately apply credit to a new invoice or take cash refund.

### P4b. Accumulate, ship to vendor
1. Cores → Vendor Cores Board groups outstanding obligations by vendor.
2. When threshold met → **Create RGA**.
3. RGA dialog: pick which obligations to include, request RGA number from vendor.
4. After vendor responds with RGA#:
   - Enter RGA number → status `Approved`.
   - Print labels, ship → status `Shipped`, `adjust_qty(-)` for each CORE SKU.
5. When vendor issues credit:
   - Enter actual credit amount → status `Credited`.
   - QBO vendor credit posted.
   - Obligations all marked `credited`.

---

## P5. Customer return (non-core)

1. Returns screen → **New Return** → pick original invoice.
2. invoice_return_dialog shows each invoice line with `qty_to_return`.
3. For each returned line:
   - Restocking fee per [03_business_rules.md](03_business_rules.md).
   - Disposition: `restock` (back on shelf) or `scrap` (write off).
4. Save:
   - `returns` + `return_lines`.
   - `customer_credits` row for net refund (or direct refund if `refund_to_card`).
   - `adjust_qty` per line if disposition = restock.
   - QBO CreditMemo push enqueued.

---

## P6. Inventory adjustment

1. Inventory → Adjustments → **New Adjustment**.
2. `adjust_qty_dialog`: pick product, enter delta (±), pick reason code.
3. Save:
   - `adjustments` row + `inventory_audit` row.
   - If reason maps to a P&L category (shrinkage, damage), QBO journal entry
     enqueued.

Bulk version: `bulk_adjust_dialog` accepts CSV or table-paste.

---

## P7. Cycle count

1. Pre-Inventory screen → pick a slice (location, category, vendor).
2. Generate count sheet → PDF for warehouse.
3. Counter performs count, returns to app.
4. Enter actuals; each variance → `adjustments` row with `reason_code='COUNT'`.
5. Reconciliation report shows shrinkage value.

---

## P8. Scrapes (operational, not data-flow)

See [08_integrations/](08_integrations/) for full scraper specs. At a high
level the operational rules are:

- All scrapes run in background workers (never block UI).
- All scrapes write a `scrape_runs` audit row (start, end, counts, errors).
- PAI scrape requires a persistent browser profile (stored login).
- Rate-limit per source: PAI 0.8 s, HHP 2 s, ATL 1.5 s between requests.
- Output of a scrape is a **proposed** change, not an applied change. The user
  reviews and accepts (except for AI Catalog Import which is auto-accept).

---

## P9. End-of-day

1. Click **Close Day** (Settings or Dashboard).
2. App:
   - Confirms zero `qbo_sync_queue` rows in `pending`.
   - Confirms all SOs marked Shipped have Invoices.
   - Confirms cash drawer reconciliation (cash payments today vs counted).
   - Optionally locks pre-day data (no edits to docs dated < today).
3. Reports → end-of-day summary (sales, payments, AR added, qty moved).
