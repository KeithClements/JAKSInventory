# JAKS Inventory — Phase 1 Build Plan
*Compiled: 2026-05-22 | Status: ACTIVE — Do not deviate without updating this document*
*Source of truth: All decisions derive from INTERVIEW_NOTES.md + PLAN.md + this file*

---

## Part 1 — How ERPs Are Built: Research Findings

*Studied: NetSuite SuiteSuccess methodology, Odoo implementation playbook, SAP Business One ASAP
framework, QuickBooks Enterprise setup order, Epicor Eagle dealer implementation, Microsoft Dynamics
365 Sure Step. Applied to the JAKS use case.*

---

### Finding 1 — Schema Stability Is the Most Expensive Problem

Every ERP implementation study shows the same failure pattern: UI is built before the data model is
stable, and schema changes after go-live cost 3–5× more than getting it right before writing a single
route. The fastest ERP projects spend 25–30% of total time on schema design before any UI work.

**For JAKS:** Schema must be fully designed and reviewed before any workflow screens are built.
We already have a working schema — but it is **incomplete**. The interview revealed:
- `sales_orders` + `so_lines` tables missing
- `po_receipts` + `po_receipt_lines` + `vendor_bills` + `vendor_bill_lines` tables missing
- `warranty_claims` + `return_authorizations` + `return_lines` tables missing
- `cross_references` table missing
- `price_history` table missing
- `product_serial_numbers` table missing
- Customer fields missing: `payment_terms`, `interest_rate`, `tax_rate`
- Invoice/SO fields missing: `customer_po_number`, `customer_job_number`, `esn`, `engine_manufacturer`, `engine_model`, `so_payment_mode`
- Quote fields missing: `follow_up_date`, `outcome`, `lost_reason`
- Vendor fields missing: `vendor_code` (for SKU prefix), `payment_terms`, `vendor_confirmation_number`
- Product fields missing: `vendor_core_charge`, `customer_core_charge` (separate — cores are marked up)

**Rule:** No new route or template is written until the schema pass is 100% complete.

---

### Finding 2 — The Build Layer Cake (Universal ERP Order)

Every ERP methodology — regardless of vendor or industry — follows this build sequence.
Skipping a layer or building out of order causes rework:

```
Layer 1 — FOUNDATION
  Database schema, ORM models, service layer skeleton,
  settings/configuration, number sequencing, audit hooks

Layer 2 — MASTER DATA (Reference Records)
  Vendors → Products → Customers
  (entities that transactions reference — must exist first)

Layer 3 — INBOUND TRANSACTIONS (Inventory IN)
  Purchase Orders → Goods Receipts → Vendor Bills
  (this is how inventory enters the system — must work before selling)

Layer 4 — OUTBOUND TRANSACTIONS (Inventory OUT / Revenue)
  Quotes → Sales Orders → Invoices → Payments
  (the revenue cycle — depends on inventory being trustworthy)

Layer 5 — SPECIAL PROCESSES
  Core charges, Returns, Warranty claims
  (depend on invoices and POs both being solid)

Layer 6 — VISIBILITY
  Reports, Dashboard, Global search
  (read-only views of everything above — build last, but design for from day one)
```

**For JAKS:** This is the exact order we will follow. Each layer is a gate — Layer 3 does not
start until Layer 2 is solid in real use.

---

### Finding 3 — Master Data Is the Multiplier

NetSuite's SuiteSuccess and Odoo's official implementation guide both call out the same thing:
**the quality of master data determines the quality of every transaction downstream.**

If a product record has the wrong cost, every invoice using that product has the wrong margin.
If a customer record has the wrong tax rate, every invoice is wrong.
If a vendor's core charge fields are missing, core lifecycles break.

**For JAKS:** Products are the hardest master data to get right because they need:
- Correct cost (from PAI/HHP/ATL — scraper enrichment)
- Correct vendor linkage (for SKU prefix + sourcing)
- Correct core charge amounts (both vendor cost and customer charge)
- Correct cross-references (for search to return alternatives)

This is why Keith confirmed: **scrapers/enrichment first.** Enrichment is not a convenience —
it is the mechanism that makes master data correct at entry time.

---

### Finding 4 — Service Layer Is Non-Negotiable

SAP B1, Dynamics 365, and Odoo all enforce a hard rule: **no business logic in the UI layer.**
This is called "separation of concerns" and it is why these systems survive version upgrades.

When logic is in a template or a router function:
- A bug in the quote screen might silently affect invoice totals
- Changing tax logic breaks 6 different places
- Testing is impossible without loading a browser

When logic is in a service:
- QuoteService.calculate_total() is tested once, used everywhere
- PricingService.apply_discount() is the single source of truth
- Every router calls the service — the service is the ERP

**For JAKS:** The service layer is already planned (QUOTING_REQUIREMENTS.md).
It must be scaffolded **before** routes are built. Services defined:
- `SearchService` — global search, all entity types
- `ProductService` — SKU generation, availability, enrichment dispatch
- `PricingService` — markup, discount, surcharge, core markup
- `QuoteService` — line totals, quote state, follow-up
- `SalesOrderService` — SO state, payment mode, fulfillment
- `InvoiceService` — lock logic, QBO push, inventory deduction
- `PaymentService` — apply payment, running balance
- `CoreService` — core lifecycle, credit/check decision
- `POService` — 3-way match, receipt, bill creation
- `WarrantyService` — claim state machine
- `VendorAvailabilityService` — scraper dispatch (PAI/HHP/ATL)
- `ESNLookupService` — stub only (architecture placeholder)
- `CRMService` — call log, follow-up, lost sale

---

### Finding 5 — Inventory Must Be Trustworthy Before Selling

Epicor Eagle's implementation guide (the most relevant — automotive/truck parts) has a hard
rule: **do not build the invoice workflow until the receiving workflow is solid.**

Reason: if invoices can be created before the PO receipt flow works, inventory goes negative
immediately and the numbers are meaningless from day one. Every dealer that skips this step
spends months cleaning up phantom stock.

**For JAKS:** Purchase Orders + Receiving must be fully working — and tested with real POs from
PAI, HHP, and ATL — before the Quote → Invoice flow is opened for daily use.

---

### Finding 6 — Financial Integrity Rules (Non-Negotiable)

From all ERP methodologies, these are treated as absolute rules:

1. **All monetary calculations happen server-side.** Frontend displays values; never trusts them.
2. **Invoice numbers are sacred.** Once issued, never reused. Gaps are acceptable; gaps are auditable.
3. **Inventory changes only through controlled events.** PO receipt, invoice save, and manual
   adjustment are the only valid triggers. Never a direct qty edit.
4. **Every financial event is timestamped and attributed.** Who did what, when.
5. **Payment cannot exceed invoice total.** System enforces, not just displays a warning.
6. **Credit memos are the only way to modify a locked invoice.** No backdating.

**For JAKS:** These rules are baked into the service layer. Routers that try to bypass them
will fail at the service level.

---

### Finding 7 — The "Crawl, Walk, Run" Gate Model

NetSuite's SuiteSuccess and SAP's ASAP both use phase gates — explicit checkpoints where
a phase is declared stable before the next begins. The most important gate is Phase 1 → Phase 2.

Phase 1 is done when:
- Real transactions are being processed (not just test data)
- No data integrity issues have been found in 2 weeks of daily use
- Keith can run a full cycle (PO → receive → quote → invoice → payment) without a workaround
- Wife can see customer balances and push to QBO without issues
- Cores are tracking correctly through their full lifecycle

**For JAKS:** Phase 2 (Shopify, scraper surface, TaxJar) does not start until Keith signs off
on Phase 1 being stable in daily use.

---

## Part 2 — Phase 1 Scope

### What Is IN Phase 1

| Module | Why Phase 1 |
|---|---|
| Schema finalization (all tables) | Foundation — cannot be deferred |
| Service layer skeleton | Foundation — cannot be deferred |
| Settings + number sequencing | Foundation — cannot be deferred |
| Navigation rebuild | Usability — the current nav is broken |
| Inline creation (Customer, Product, Vendor) | Usability — #1 blocking UX problem |
| Vendors — full CRUD + vendor code | Master data — unlocks products |
| Products — full CRUD + enrichment button | Master data — unlocks everything |
| Customers — full CRUD + call log + house account | Master data — unlocks all sell-side |
| Purchase Orders + 3-way match | Inventory IN — must work before selling |
| Global search (one bar, all entities) | Daily operations — Keith uses constantly |
| Quotes — keyboard-first, 45-second target | Revenue cycle entry point |
| Sales Orders — full lifecycle | Missing workflow gap — confirmed required |
| Invoices — with lock logic, CC surcharge | Revenue cycle — the money document |
| Payments — cash/check/card, running balance | Close the AR loop |
| Core charges — full lifecycle + printed doc | Critical business process, unique to JAKS |
| Returns (Return Authorization) | Standard dealer workflow |
| Warranty Claims — vendor approval flow | Standard dealer workflow |
| Dashboard — operational widgets | Daily use visibility |
| QBO push — invoices, payments, vendor bills | Wife's bookkeeping depends on this |
| PDF generation — invoices, POs, core docs | Print/email delivery required |

### What Is NOT In Phase 1

| Feature | Phase |
|---|---|
| Shopify sync | Phase 2 |
| eBay listings | Phase 3 |
| TaxJar (automated tax) | Phase 2/3 |
| ESN lookup (live scraper) | Phase 3 |
| Customer Excel import | Phase 1 (late — after core workflows solid) |
| Serial number tracking (cylinder heads) | Phase 1 (late) |
| Vendor kit BOM / JAKS-built kit BOM | Phase 1 (late) |
| QBO customer pull / import | Phase 2 |
| Lost sales log | Phase 1 (light — just a field on quote) |
| Full report suite | Phase 1 (basic); Phase 2 (full suite) |

---

## Part 3 — Build Order (Strict Sequence)

Each step is a gate. The next step does not begin until the current step is **working in real use**,
not just technically built.

---

### STEP 0 — Schema Finalization
**Time estimate: 1–2 sessions**
**Gate: Nothing else starts until this is done.**
**Input document: `rebuild/SCHEMA_INTERVIEW.md` — read that file first. It supersedes the field list below.**

Complete the database schema. Every table, every column, every relationship, every index.
This is the most important step in the entire project.

**Tables to add/modify:**

```sql
-- Add to customers:
payment_terms          TEXT DEFAULT 'cod'   -- 'cod' | 'net_30' | 'net_60'
interest_rate          REAL DEFAULT 0.0     -- annual % for overdue
tax_rate               REAL DEFAULT 0.0     -- if not exempt
credit_balance         REAL DEFAULT 0.0     -- running credit (cores, overpayments)

-- Add to vendors:
vendor_code            TEXT                 -- e.g. 'PAI', 'HHP', 'ATL' — used in SKU
payment_terms          TEXT DEFAULT 'net_30'
vendor_confirmation_number TEXT             -- for verbal orders

-- Add to products:
vendor_core_charge     REAL DEFAULT 0.0     -- what vendor charges JAKS
customer_core_charge   REAL DEFAULT 0.0     -- what JAKS charges customer (marked up)
has_serial_number      BOOLEAN DEFAULT 0    -- cylinder heads etc.
kit_type               TEXT                 -- NULL | 'vendor_kit' | 'custom_kit'

-- Add to quotes:
follow_up_date         DATE
outcome                TEXT DEFAULT 'pending'  -- 'pending'|'won'|'lost'|'no_decision'
lost_reason            TEXT

-- New table: sales_orders
id, so_number, customer_id, quote_id (FK optional),
status (open|partial|fulfilled|invoiced|cancelled),
payment_mode (full|deposit|none),
deposit_amount REAL,
customer_po_number, customer_job_number, esn,
engine_manufacturer, engine_model,
created_at, updated_at, notes

-- New table: so_lines
id, so_id, product_id, qty_ordered, qty_committed,
qty_fulfilled, unit_price, discount_pct,
core_charge, source (stock|backorder)

-- New table: po_receipts
id, po_id, received_at, received_by, notes

-- New table: po_receipt_lines
id, receipt_id, po_line_id, qty_received

-- New table: vendor_bills
id, po_id, vendor_id, bill_number, bill_date,
due_date, status (pending|approved|discrepancy|paid),
total_amount

-- New table: vendor_bill_lines
id, bill_id, po_line_id, qty_billed, unit_cost

-- New table: return_authorizations
id, ra_number, customer_id, invoice_id (FK optional),
reason, status (draft|open|received|closed),
created_at

-- New table: return_lines
id, ra_id, product_id, qty, unit_price, restocking_fee

-- New table: warranty_claims
id, claim_number, ra_id (FK optional), customer_id,
vendor_id, product_id, failure_description,
submitted_at, vendor_decision (approved|denied|pending),
vendor_decision_at, credit_amount,
status (draft|submitted_to_vendor|vendor_approved|
        vendor_denied|customer_credited|closed)

-- New table: cross_references
id, product_id, ref_type (oem|competitor|vendor_alt),
ref_number, brand, notes,
status TEXT DEFAULT 'researching'
  -- 'researching'|'found'|'proven'|'dealer_confirmed'|'vendor_confirmed'|'bad_cross'|'obsolete'
  -- "found" = likely correct (Google/competitor site); "proven" = confirmed in real JAKS sale

-- New table: price_history
id, product_id, customer_id (nullable), unit_price,
sold_at, invoice_id

-- New table: product_serial_numbers
id, product_id, serial_number,
po_receipt_line_id (nullable), invoice_line_id (nullable),
status (in_stock|sold|returned)

-- New table: product_images
id, product_id, file_path, source (manual|pai|hhp|atl),
is_primary, created_at

-- New table: research_items
id, ri_number,                               -- RI-2026-XXXX
customer_id,
quote_id (FK optional),
quote_line_id (FK optional),
assigned_user_id,
status TEXT                                  -- researching|waiting_dealer|waiting_vendor|found|proven|closed
urgency TEXT DEFAULT 'normal',               -- normal|urgent|truck_down  ← confirm R-B
search_term, oem_number, vin, esn, engine_model,
notes,
callback_due_at, resolved_at,
resolved_product_id (FK optional),
resolution_notes,
created_at, updated_at

-- New table: research_activity_log
id, research_item_id,
activity_type TEXT                           -- called_dealer|emailed_vendor|customer_replied|vendor_confirmed|found_online|other
notes, logged_by, logged_at

-- Modify quote_lines (add research fields):
research_status TEXT                         -- NULL|researching|waiting_dealer|waiting_vendor|found|proven
research_item_id (FK optional → research_items)

-- Scaffold (empty — architecture only):
product_kits, product_kit_lines,
quote_followups, esn_lookups, engine_configs,
lost_sales_log

-- New table: core_slips
id, slip_number (CORE-2026-XXXX), invoice_id, customer_id,
created_at, printed_at, status (open|received|closed)

-- New table: vendor_core_returns (VCR)
id, vcr_number (VCR-2026-XXXX), vendor_id,
status (draft|shipped|vendor_accepted|vendor_rejected|partial|disputed|closed),
tracking_number, rma_number,
expected_credit, actual_credit, credit_difference,
resolution (absorbed|charged_to_customer|disputed|written_off),
resolution_notes, shipped_at, vendor_decision_at,
created_at, created_by_id

-- New table: vendor_core_return_lines
id, vcr_id, core_charge_id, part_number, description,
qty, expected_unit_credit, actual_unit_credit,
vendor_outcome (accepted|rejected|partial|disputed)

-- New table: core_locations
id, name, description, is_active
-- Examples: Core Shelf, Core Holding, Ready for PAI, Questionable Core, Rejected Core

-- Add to core_charges:
core_slip_id (FK → core_slips, nullable)
vcr_id (FK → vendor_core_returns, nullable)
location_id (FK → core_locations, nullable)
inspection_outcome (accepted|hold|rejected|damaged|wrong_core|partial)
inspected_at, inspected_by_id
credit_method (account_credit|check|hold|rejected|partial)
```

**Add to invoices:**
```
customer_po_number, customer_job_number, esn,
engine_manufacturer, engine_model,
locked_at, lock_reason (end_of_day|qbo_sync|paid),
sales_order_id (FK optional — if converted from SO)
```

---

### STEP 1 — Service Layer Skeleton
**Time estimate: 1 session**
**Gate: All services defined with interfaces before any route uses business logic**

Create `/app/services/` directory with one file per service.
Each file exports a class with method stubs that raise `NotImplementedError`.
Routes call services from day one — no business logic ever goes in a router.

```
app/services/
  __init__.py
  search_service.py         SearchService
  product_service.py        ProductService
  pricing_service.py        PricingService
  quote_service.py          QuoteService
  sales_order_service.py    SalesOrderService
  invoice_service.py        InvoiceService
  payment_service.py        PaymentService
  core_service.py           CoreService
  po_service.py             POService
  warranty_service.py       WarrantyService
  vendor_availability.py    VendorAvailabilityService
  esn_lookup_service.py     ESNLookupService  ← stub only
  crm_service.py            CRMService
  research_service.py       ResearchService  ← create/update research items, templates, activity log, resolve → cross-ref
```

**Priority implementation order** (fill in as the corresponding module is built):
1. `PricingService` — needed by everything that touches money
2. `SearchService` — needed by quote screen (the daily driver)
3. `ProductService` — needed by quotes, invoices, POs
4. `POService` — needed by receiving workflow
5. `QuoteService` — needed by the quote screen
6. `SalesOrderService` — needed for SO → Invoice flow
7. `InvoiceService` — needed for lock logic, QBO push
8. `PaymentService` — needed for AR
9. `CoreService` — needed for core lifecycle
10. `CRMService` — needed for follow-up, lost sales

---

### STEP 2 — Settings, Configuration, Number Sequences
**Time estimate: Already partially built — review and complete**

Verify these settings exist and seed correctly:
- `cc_surcharge_pct` — 3% configurable
- `invoice_counter` — INV-2026-XXXX sequence
- `quote_counter` — Q-2026-XXXX sequence
- `so_counter` — SO-2026-XXXX sequence (NEW — Sales Orders need a number)
- `po_counter` — PO-2026-XXXX sequence
- `ra_counter` — RA-2026-XXXX sequence (NEW — Return Authorizations)
- `warranty_counter` — WC-2026-XXXX sequence (NEW — Warranty Claims)
- `ri_counter` — RI-2026-XXXX sequence (NEW — Research Items)
- `core_slip_counter` — CORE-2026-XXXX sequence (NEW — Core Return Slips)
- `vcr_counter` — VCR-2026-XXXX sequence (NEW — Vendor Core Returns)
- `global_markup_pct` — default markup percentage
- `business_close_time` — for invoice lock logic (default: 23:59)
- `company_name`, `company_address`, `company_phone` — for PDF headers

---

### STEP 3 — Navigation Rebuild
**Time estimate: 1 session**
**Gate: Do before building any new screens — all new screens slot into the new nav**

Rebuild `base.html` sidebar using confirmed traditional structure.
Pending D1-A thru D1-E answers — using best-guess defaults until Keith confirms:

```
Dashboard
[Recently Viewed — last 5 records, collapsible]

─── SALES ──────────────────
  Customers
  Quotes
  Sales Orders
  Invoices

─── PURCHASING ──────────────
  Vendors
  Purchase Orders

─── INVENTORY ──────────────
  Products

─── CORES ──────────────────
  Core Charges

─── REPORTS ─────────────────
  Reports

─── SYSTEM ─────────────────
  Settings
```
LOCKED: D1-D and D1-E confirmed — Cores and Reports each get their own sidebar section.
LOCKED: D1-A confirmed — Sales Orders is a standalone nav item (active work queue).
LOCKED: Nav grouping = SALES + PURCHASING (not Sell/Buy, not Customers/Vendors alone).

---

### STEP 4 — Inline Creation (Slide-over)
**Time estimate: 1 session**
**Gate: Must be working before any workflow screen is "done"**

This is the #1 UX problem identified. Every dropdown with a relational record gets a `+` button.
Build the slide-over shell in `base.html` first, then wire up three quick-create forms:

1. **Quick-create Customer** — name, phone, email, company (from Quote/Invoice)
2. **Quick-create Product** — SKU, title, vendor, cost, markup/price (from Quote/Invoice/PO)
3. **Quick-create Vendor** — name, vendor code, phone, account# (from Product/PO)

Routes: `GET/POST /quick-create/{customer|product|vendor}`
Behavior: saves → returns `<option>` tag → HTMX swaps into originating `<select>`

---

### STEP 5 — Vendors (Master Data)
**Time estimate: Already built — review and complete**

Add missing fields: `vendor_code`, `payment_terms`, `vendor_confirmation_number`.
`vendor_code` is critical — it feeds the SKU prefix (`JAKS-[VENDOR_CODE]-[PART#]`).
Validate vendor code is set before a product can be linked to that vendor.

---

### STEP 6 — Products (Master Data + Enrichment)
**Time estimate: 2–3 sessions**
**This is the highest-value module per Keith's priority answer**

Products are the foundation of everything. Key changes needed:

**Schema additions:** `vendor_core_charge`, `customer_core_charge`, `has_serial_number`,
`kit_type`, images table, serial numbers table, cross_references table.

**SKU auto-generation:** When vendor is selected on the product form, SKU field auto-prefixes
with `JAKS-[VENDOR_CODE]-`. User types only the part number portion.
ProductService assembles the full SKU: `JAKS-PAI-123456`.

**Enrichment button (per Keith: sooner rather than later):**
Each product detail screen gets an `[Enrich from PAI]`, `[Enrich from HHP]`, `[Enrich from ATL]`
button. Clicking calls VendorAvailabilityService, which dispatches to the appropriate scraper.
Results are shown for review — user confirms before saving. Fields populated:
title, description, cost, images, cross-references, availability.

**Cross-reference search:** When a user searches for a part number on the quote screen,
SearchService returns:
1. Exact match (if JAKS stocks it)
2. Cross-reference matches (parts JAKS stocks that are equivalent)
Label: "Customer requested [OEM#] — you stock [JAKS-SKU] (equivalent)"

**Image display:** Product detail shows primary image. Scraper saves images to `/data/images/`.
`product_images` table tracks source (manual, PAI, HHP, ATL) and primary flag.

---

### STEP 7 — Customers (Master Data + CRM Foundation)
**Time estimate: Already partially built — add missing fields**

Add: `payment_terms`, `interest_rate`, `tax_rate`, `credit_balance`.
Credit balance is the running account balance — goes up when cores are returned or credit memos
are issued, goes down when applied to invoices.

Customer 360 view must show:
- Contact info
- Open invoices + total owed
- Credit balance
- Core charges outstanding
- Call log (already built ✅)
- Last purchase date + amount

---

### STEP 8 — Purchase Orders + 3-Way Match (Inventory IN)
**Time estimate: 2 sessions**
**Gate: Must be fully working before invoice workflow is used in production**

This is where inventory enters the system. The 3-way match is critical for financial integrity.

**Workflow:**
```
Create PO (ordered qty, vendor, terms)
  → Send to vendor (print PDF or email)
  → Receive shipment → create po_receipt + po_receipt_lines → inventory ↑
  → Vendor sends bill → create vendor_bill → match to receipt
  → If qty_billed > qty_received → flag discrepancy, hold
  → If match → approve → push to QBO as vendor bill
```

**Verbal order support:** PO can be created with status `verbal_order` before formal
confirmation. `vendor_confirmation_number` field captures the phone reference number.

**Partial receiving:** PO stays open until all lines are fully received.
`po_lines.qty_received` tracks cumulative received quantity.
`po_lines.qty_outstanding` = `qty_ordered - qty_received`

**Inventory impact:** Only `po_receipts` trigger inventory changes. The PO itself does NOT
change inventory — this is the standard industry rule and prevents phantom stock.

---

### STEP 9 — Global Search
**Time estimate: 1 session**
**Gate: Must be ready before the quote screen is "done"**

One search bar, visible from every page, keyboard shortcut (Ctrl+K or `/`).

SearchService searches:
- Customers: company name, phone, address
- Products: SKU, title, cross-reference numbers, OEM numbers, manufacturer
- Quotes: quote number, customer name
- Invoices: invoice number, customer name, customer PO#
- Vendors: name, account number

Results grouped by type. Partial/substring matching. Returns top 10 per category.
Keyboard navigable — arrow keys, Enter to select.

---

### STEP 10 — Quotes (Keyboard-First Ops Console)
**Time estimate: 2–3 sessions**
**This is the daily driver — build it right**

From `QUOTING_REQUIREMENTS.md`:
- 45-second quote target, keyboard only
- Line items show QOH, vendor availability, ETA, margin % inline — no clicks
- Non-stocked items quotable freely (zero stock does not block)
- Per-customer discount auto-applied, overridable per line
- Core charge auto-added as separate line when product has core
- Convert → Sales Order (for out-of-stock items) or → Invoice (if in stock)
- Follow-up date field on every quote

**Price history:** When a product is added to a quote, PricingService checks `price_history`
for the last price sold to this specific customer. Shows inline: "Last sold to [customer] at $X"

**Brand substitution:** When the requested part is out of stock, system suggests stocked
alternatives from cross_references. Shown inline, one click to substitute.

**Parts Research Management (confirmed — see QUOTING_REQUIREMENTS.md §15):**

When a part search fails (zero results or customer doesn't know the part number), the quote line
does NOT dead-end. Instead, the user sets a `research_status` on that line:

| Status | Color | Meaning |
|---|---|---|
| Researching | 🟡 Yellow | Being investigated |
| Waiting Dealer | 🔵 Blue | Awaiting dealer VIN/part callback |
| Waiting Vendor | 🔵 Blue | Awaiting vendor callback |
| Found | 🟢 Green | Likely correct, not yet sale-proven |
| Proven | 🟢 Bold | Confirmed through real sale |
| Bad Cross | 🔴 Red | Known incorrect cross |
| Obsolete | ⚫ Gray | Superseded |

Each flagged line generates a linked `research_items` record (RI-2026-XXXX) via ResearchService.
The quote line cell is visually highlighted until research_status is cleared.

**One-click request templates from the quote:**
- **Dealer Request** — auto-fills: customer name, VIN, ESN, engine model, part description, callback info
- **Vendor Request** — auto-fills: part info, OEM number if known, urgency, callback info
- Template generates formatted copy/paste text (direct email send: TBD — confirm R-A before building)

**Research activity log:** On each research item, users log events (called dealer, emailed vendor,
customer replied, vendor confirmed) with timestamp and free-text notes. Timeline visible on the item.

**VIN-to-part-number flow:** Customer provides VIN → Dealer Request template auto-assembled →
dealer call → OEM part number returned → quote line updated → optional cross-reference created
(auto or prompt — confirm R-C before building).

**Open questions before building research features:** R-A (template delivery), R-B (urgency flag),
R-C (auto vs. prompted cross-ref on resolve) — see Part 6 of this document.

---

### STEP 11 — Sales Orders
**Time estimate: 2 sessions**
**This is the confirmed-missing workflow step**

Sales Order is created when:
- User explicitly chooses SO over Invoice on the quote-convert screen, OR
- User tries to invoice a zero-stock item and chooses "Create Sales Order" from the warning prompt

**SO states:** `open → partial → fulfilled → invoiced → cancelled`

**Payment at SO stage (D4 confirmed — 3 modes):**
- **Full payment** — collect now, release when goods arrive
- **Deposit** — collect deposit amount, balance due at invoice
- **No payment** — defer to invoice (net 30 customer, trusted relationship)

UI: Payment mode selector on SO form. If "Deposit" → deposit amount field appears.
PaymentService records the payment against the SO, which carries forward to the invoice.

**Convert SO → Invoice:** When all lines are fulfilled, "Convert to Invoice" button appears.
One click creates invoice with all SO lines pre-filled, deposit amount pre-applied.

**Customer-facing fields on SO:** customer_po_number, customer_job_number, esn,
engine_manufacturer, engine_model (all optional, all shown on printed SO document).

---

### STEP 12 — Invoices
**Time estimate: 2 sessions — builds on quote and SO work**

**Lock logic (D5 confirmed):** Invoice locks at whichever comes first:
1. End of business day (configurable `business_close_time` setting)
2. Invoice is pushed to QBO (`locked_at` set, `lock_reason = 'qbo_sync'`)
3. Invoice is marked fully paid (`lock_reason = 'paid'`)

While unlocked: full edit. Once locked: "Locked" badge, Edit button grayed, changes require
credit memo.

**Zero-stock warning (D2 confirmed):** Adding a zero-stock product shows inline warning.
Prompt: [Create Sales Order instead] [Proceed with Invoice]. User choice is respected.

**Core charges:** InvoiceService auto-adds core charge lines for any product with a core.
`CoreService.open_customer_core()` is called on invoice save for each core line.

**CC surcharge:** Toggle on invoice — adds 3% (from settings) as a separate surcharge line.
PricingService calculates; displayed before user saves.

**Customer-facing fields:** customer_po_number, customer_job_number, esn,
engine_manufacturer, engine_model displayed on printed invoice if populated.

**QBO push:** On-demand button "Push to QBO". InvoiceService calls QBO API, sets
`qbo_invoice_id` on the invoice, triggers lock.

**PDF:** Printable invoice with JAKS logo, customer info, line items, totals, core charge lines,
payment terms, due date.

---

### STEP 13 — Payments
**Time estimate: 1 session — builds on invoice work**

**Payment types:** Cash, Check, Card (recorded manually — card processed via QBO Payments externally).

**Running balance:** After payment, PaymentService updates `customer.credit_balance` if
payment exceeds invoice total (rare, but possible).

**CC surcharge:** If payment type is Card and surcharge not already on invoice → prompt to add.

**QBO sync:** PaymentService pushes payment to QBO, marks QBO invoice as paid.

**Partial payments:** Supported. Invoice status: `partial` until fully paid.

---

### STEP 14 — Core Charges (Full Lifecycle)
**Time estimate: 2 sessions — depends on PO and Invoice being solid**

**Core lifecycle (LOCKED — Session 4):**
```
Invoice issued with core-eligible part
  → CoreService creates customer core obligation
  → OPTIONAL popup: "This invoice includes core items. [Print Core Return Slip] [Skip]"
  → System generates Core Slip # (CORE-2026-XXXX) if printed

Customer returns core
  → Receive Core: search by Core Slip # | Customer | Invoice # | Part # | Tracking # | Phone
  → CoreService.receive_customer_core()
  → INSPECTION REQUIRED before credit:
     Outcomes: Accepted | Hold for Review | Rejected | Damaged | Wrong Core | Partial Credit
  → Credit method (user choice):
     Default: Account Credit
     Override: Issue Check | Hold Pending Review | Reject / No Credit | Partial Credit
  → Core moves to: Core Shelf / Core Holding (separate from sellable inventory)
  → Core status: returned_pending_inspection → inspected → credited

JAKS batches cores for vendor return
  → Core Management → Ready to Ship Vendor → select vendor → select cores
  → Create VCR batch (VCR-2026-XXXX)
  → Print vendor core return document (NO customer identity shown)
  → Enter tracking / RMA
  → Core status: shipped_to_vendor

Vendor decision on cores
  → Outcomes: Accepted | Rejected | Partial Credit | Disputed | Write Off | Charge Customer
  → If credit differs from expected: record expected amount, actual amount, difference, resolution
  → Resolution choices: Absorb by JAKS | Charge Customer | Dispute Vendor | Write Off
  → If accepted: vendor credit logged → QBO push
  → Core status: vendor_accepted | vendor_rejected | vendor_partial | closed
```

**Core Management screen tabs:** All | Customer Cores | Ready to Inspect | Ready to Ship Vendor | Vendor Credits Pending | Problem Cores | Closed

**Core status cards at top of screen:** Customer Owes Cores | Ready to Inspect | Ready to Credit | Ready to Ship Vendor | Waiting Vendor Credit | Problem Cores

**Core locations (separate from inventory):** Core Shelf | Core Holding | Ready for PAI | Ready for HHP | Questionable Core | Rejected Core | Scrap Core

**Vendor paperwork:** Does NOT show customer identity. Shows: VCR#, JAKS core ref, Part#, Description, Qty, Expected credit, RMA#, Tracking#.

**Core markup visibility:** Core charges detail view shows:
- `vendor_core_charge` (what JAKS pays vendor)
- `customer_core_charge` (what customer paid JAKS)
- Margin on core: `customer_core_charge - vendor_core_charge`

**Printed shipment document:** PDF generated by CoreService containing:
RMA number, tracking number, JAKS info, vendor info, part details, sign-off line.

---

### STEP 15 — Returns & Warranty Claims
**Time estimate: 2 sessions**

**Return Authorization (standard return):**
- Select customer → select invoice → select lines to return
- System generates RA number (RA-2026-XXXX)
- Print RA document for customer signature
- On receipt: credit memo issued, `customer.credit_balance += return_amount`
- Restocking fee: optional per-line field

**Warranty Claim:**
```
draft → submitted_to_vendor → vendor_approved → customer_credited → closed
                           ↘ vendor_denied → customer_notified → closed
```
- Customer signs warranty claim form (printable from system)
- JAKS submits to vendor (status: submitted_to_vendor)
- Vendor decision recorded with date (can take days/weeks)
- If approved: account credit issued to customer (D3 confirmed)
- If denied: customer notified, claim closed

---

### STEP 16 — Dashboard (Operational Widgets)
**Time estimate: 1 session — mostly already built**

Review and add:
- **Research Queue** widget (confirmed — more important than quote follow-ups): shows counts by status: Researching / Waiting Dealer / Waiting Vendor / Urgent-Truck Down. Placement relative to follow-up widget TBD — confirm R-D before building.
- **Open Sales Orders** widget (confirmed needed from interview)
- **Quotes Needing Follow-up Today** (follow_up_date = today)
- **Overdue Invoices** (past due date, not paid)
- **Outstanding Core Charges** (by customer, by vendor, days outstanding)
- Keep existing: Today's Payments, Open Quotes, Open POs, Low Stock, Recent Invoices, Call Logs

---

### STEP 17 — QBO Integration
**Time estimate: 2 sessions — OAuth + push logic**

**Push only (confirmed — no inventory sync):**
- Invoices → QBO Invoice
- Payments → QBO Payment (marks invoice paid)
- Vendor Bills → QBO Bill (from approved vendor_bills)

**OAuth flow:** Settings page → "Connect to QuickBooks" → OAuth handshake → store tokens.
Tokens refresh automatically. Disconnect button available.

**Per-push, not automatic:** User clicks "Push to QBO" on each document.
Batch push option on reports/list views (push all unpushed invoices from this week).

---

### STEP 18 — PDF Generation
**Time estimate: 1 session — used throughout**

All documents need a clean, professional PDF:
- Invoice (with JAKS logo, all customer fields, line items, payment terms)
- Quote (same format, "QUOTE" header instead of "INVOICE")
- Sales Order (same format, "SALES ORDER" header, payment mode shown)
- Purchase Order (vendor-facing, JAKS logo, vendor address, line items)
- Core Return Shipment document (RMA#, tracking#, sign-off)
- Return Authorization document (for customer signature)
- Warranty Claim form (for customer signature)

Library: `weasyprint` or `reportlab` — evaluate at build time.
Template-driven: each document type has a Jinja2 HTML template → rendered to PDF.

---

### STEP 19 — Basic Reports
**Time estimate: 1–2 sessions (basic set only)**

Keith wants QBO-style reports. Phase 1 delivers the operational basics:

| Report | What It Shows |
|---|---|
| Open Invoices / AR Aging | What customers owe, how long overdue |
| Sales by Customer | Revenue per customer, date range filter |
| Sales by Product | Revenue per product, date range filter |
| Inventory Valuation | Current qty × cost per product |
| Open Purchase Orders | POs not yet fully received |
| Core Charges Outstanding | Open cores by customer and by vendor |
| Overdue Invoices + Interest Due | Customers past net 30 + calculated interest |

All reports: date range filter, exportable to CSV.
Full P&L and financial reports: Phase 2 (requires all data to be solid first).

---

## Part 4 — Schema Dependency Map

```
settings
  └─ used by: everything (pricing, numbering, surcharge)

vendors
  └─ products (vendor_id FK)
  └─ purchase_orders (vendor_id FK)
  └─ warranty_claims (vendor_id FK)

customers
  └─ quotes (customer_id FK)
  └─ sales_orders (customer_id FK)
  └─ invoices (customer_id FK)
  └─ return_authorizations (customer_id FK)
  └─ warranty_claims (customer_id FK)
  └─ customer_call_logs (customer_id FK)

products
  └─ quote_lines (product_id FK)
  └─ so_lines (product_id FK)
  └─ invoice_lines (product_id FK)
  └─ po_lines (product_id FK)
  └─ core_charges (product_id FK)
  └─ cross_references (product_id FK)
  └─ price_history (product_id FK)
  └─ product_serial_numbers (product_id FK)
  └─ product_images (product_id FK)
  └─ return_lines (product_id FK)

purchase_orders → po_lines → po_receipt_lines ← po_receipts
              └─ vendor_bills → vendor_bill_lines

quotes → sales_orders → invoices → payments
      └────────────────────────────────────────── (optional direct: quote → invoice)

invoices → core_charges (customer side)
        → return_authorizations → return_lines
        → warranty_claims

core_charges (vendor side) ← purchase_orders
```

---

## Part 5 — Go-Live Checklist (Phase 1 Complete)

Keith signs off when all of these are true in real daily use (not test data):

- [ ] Can enter a new vendor and new product in under 3 minutes (including enrichment)
- [ ] Can create a PO, receive it partially, and see inventory update correctly
- [ ] Can build a quote in 45 seconds from a phone call with a customer on the line
- [ ] Can convert a quote to an SO (out-of-stock) or Invoice (in-stock) in one click
- [ ] Collect payment on an SO — full, deposit, or defer to invoice
- [ ] Invoice locks at end of day; can issue a credit memo to correct a locked invoice
- [ ] Core charges appear automatically on quotes and invoices; lifecycle closes correctly
- [ ] Core return shipment document prints with RMA and tracking number
- [ ] Warranty claim moves through all states; account credit issued on approval
- [ ] Return authorization generates; credit applied to customer balance
- [ ] Wife can push invoices, payments, and vendor bills to QBO with one click
- [ ] Dashboard shows: open SOs, follow-up quotes today, overdue invoices, outstanding cores
- [ ] No data integrity issues in 2 weeks of daily use
- [ ] Keith's wife has no open questions about bookkeeping accuracy

---

## Part 6 — Open Questions Before Build Starts

These need answers before the affected module is built (not before everything starts):

| # | Question | Needed Before |
|---|---|---|
| D1-A | ~~Where does "Sales Orders" go in the nav sidebar?~~ | ✅ LOCKED — Own sidebar item (work queue) |
| D1-B | ~~Does wife work in app or QBO primarily?~~ | ✅ LOCKED — Full owner access; same as Keith |
| D1-C | ~~Need standalone open-SO list view?~~ | ✅ LOCKED — Yes. It is an active work queue. |
| D1-D | ~~Cores: own sidebar section or under Inventory?~~ | ✅ LOCKED — Own CORES sidebar section |
| D1-E | ~~Reports: own sidebar section or under System?~~ | ✅ LOCKED — Own REPORTS section + embedded in screens |
| D9 | ~~Vendor bill approval: auto if qty matches, or always manual?~~ | ✅ LOCKED — Auto-approve when PO/receipt/bill all match exactly. Any discrepancy → manual review required. |
| D11 | ~~Reports: full in-app suite or basic only in Phase 1?~~ | ✅ LOCKED — Sidebar report center + embedded reports in Customers/Products/Vendors screens |
| R-A | ~~Research templates: copy/paste text output, or system sends email directly?~~ | ✅ LOCKED — Copy/paste text output only. System email/text is Phase 2. |
| R-B | ~~Research items urgency flag: formal Normal/Urgent/Truck Down tier?~~ | ✅ LOCKED — Yes, formal tier: Normal / Urgent / Truck Down |
| R-C | ~~Auto cross-ref on research resolution: automatic, or confirm-prompt first?~~ | ✅ LOCKED — Prompt user before creating cross-reference. Never auto-create silently. |
| R-D | ~~Research Queue dashboard widget: own widget, or combined with quote follow-ups?~~ | ✅ LOCKED — Own separate widget. Do not merge with quote follow-ups. |

---

---

## Part 7 — Build Status Log

*Updated after each work session. Tracks what has been built, verified, or locked.*

---

### Session 2026-05-22 — Schema & Foundation

| Item | Status |
|---|---|
| SQLAlchemy models: Product, Vendor, Customer, Quote, QuoteLine, Invoice, InvoiceLine, SalesOrder, SOLine | ✅ Built |
| SQLAlchemy models: InventoryTransaction, ProductCostHistory, ProductVendorSource, AuditLog | ✅ Built |
| SQLAlchemy models: CrossReference, ProductImage | ✅ Built |
| SQLAlchemy models: PurchaseOrder, POLine, POReceipt, POReceiptLine | ✅ Built |
| SQLAlchemy models: CoreCharge, CoreReturn, VendorCoreReturn | ✅ Built |
| SQLAlchemy models: WarrantyClaim, ReturnAuthorization, ReturnLine | ✅ Built |
| SQLAlchemy models: ResearchItem, ResearchActivity | ✅ Built |
| BaseService, AuditMixin, all service skeletons | ✅ Built |
| All constants (enums): LineType, ProductStatus, QuoteStatus, CrossRefType, etc. | ✅ Built |
| FastAPI app shell, router registration, Jinja2 templates, Tailwind CSS | ✅ Built |
| Sidebar nav (traditional ERP structure: SALES / PURCHASING) | ✅ Built |
| SearchService — SKU, OEM#, cross-ref, customer, phone substring | ✅ Built |

---

### Session 2026-05-23 — Product Detail Screen

| Item | Status |
|---|---|
| Product detail screen (6-tab layout: Info / Sources / Cross-Refs / Images / Suggested Sells / History) | ✅ Built |
| Enrichment buttons (⟳ PAI / HHP / ATL) with slide-over panel | ✅ Built |
| Cross-reference confidence status (7-state dropdown, HTMX inline PATCH) | ✅ Built |
| Image management tab (upload, set primary, remove, auto-promote) | ✅ Built |
| Warranty fields on product (supplier warranty, JAKS extension, warranty %) | ✅ Built |
| Warranty card on product detail Info tab (Alpine x-show for JAKS extension fields) | ✅ Built |
| SuggestedSell model + migration + `suggested_sells` DB table | ✅ Built |
| SuggestedSellService (get, get_inline_chips, add, update, remove) | ✅ Built |
| Suggested Sells tab on product detail (table + add form + chip preview) | ✅ Built |
| Router endpoints: PATCH cross-ref status, GET enrich-panel, POST/DELETE images, POST/PATCH/DELETE suggested-sells | ✅ Built |
| `static/uploads/products/` directory initialized | ✅ Built |

---

### Session 2026-05-23 — Quote Workspace: Chips & Warranty

| Item | Status |
|---|---|
| `QuoteLine.is_optional` field (amber tint, optional add-on lines) | ✅ Built |
| `QuoteLine.option_group` field (Economy / Recommended / Premium grouping — visual pending) | ✅ Schema only |
| `QuoteLine.parent_line_id` self-referential FK (warranty child lines) | ✅ Built |
| `Quote.selected_option_group` field | ✅ Schema only |
| `LineType.WARRANTY` constant | ✅ Built |
| `SuggestedSellType` enum | ✅ Built |
| Suggestion chips row in `_line_row.html` (gray recommended + amber required chips) | ✅ Built |
| Warranty chip + inline tier picker dropdown (Alpine.js, 5 tiers) | ✅ Built |
| Warranty child line visual treatment (blue tint + WARR shield badge) | ✅ Built |
| Optional line visual treatment (amber tint) | ✅ Built |
| `add_line` router: `parent_line_id` Form param wired through | ✅ Built |
| `workspace.html` JS: chips row cleanup on delete + duplicate dedup on update | ✅ Built |

---

---

### Session 2026-05-24 — Quote Upgrade Option System

| Item | Status |
|---|---|
| `LineRole` enum (primary / core / warranty / upgrade_option / optional / suggested) | ✅ Built |
| `QuoteLine.line_role` field (replaces is_optional for new code) | ✅ Built |
| `QuoteLine.is_included` field (controls total contribution) | ✅ Built |
| `QuoteLine.option_label` field (e.g. "Stage 2 Upgrade") | ✅ Built |
| `Quote.subtotal` filtered to `is_included=True` | ✅ Built |
| `QuoteService.add_upgrade_option()` — child line, excluded by default | ✅ Built |
| `QuoteService.select_upgrade_option()` — deselects parent + siblings, selects option | ✅ Built |
| `QuoteService.add_optional_line()` — child line, included by default | ✅ Built |
| `QuoteService.toggle_line_included()` — with cascade logic for upgrade/primary | ✅ Built |
| `QuoteService.add_line()` — auto-sets is_included=False for upgrade_option role | ✅ Built |
| `QuoteService.convert_to_sales_order()` — filters is_included=True | ✅ Built |
| `QuoteService.convert_to_invoice()` — filters is_included=True | ✅ Built |
| `_tree_sort_lines()` router helper — parent-first, children immediately after parent | ✅ Built |
| `_totals_ctx()` updated — counts only is_included lines | ✅ Built |
| `GET /quotes/{id}/lines` endpoint — full tbody refresh (for multi-row state changes) | ✅ Built |
| `POST /quotes/{id}/lines/{lid}/upgrade-option` endpoint | ✅ Built |
| `POST /quotes/{id}/lines/{lid}/optional` endpoint | ✅ Built |
| `POST /quotes/{id}/lines/{lid}/select-upgrade` endpoint | ✅ Built |
| `POST /quotes/{id}/lines/{lid}/toggle-included` endpoint | ✅ Built |
| `quotes/_lines_tbody.html` partial (full tbody replacement for multi-row swaps) | ✅ Built |
| `_line_row.html` — role-based row backgrounds + left borders (amber/green/blue/sky/orange) | ✅ Built |
| `_line_row.html` — option_label display as colored badge above description input | ✅ Built |
| `_line_row.html` — ⋮ context menu (fixed-positioned dropdown, escapes overflow:auto) | ✅ Built |
| Context menu: Add Upgrade Option (dispatches start-child-add event) | ✅ Built |
| Context menu: Add Optional Item (dispatches start-child-add event) | ✅ Built |
| Context menu: Make This Active (for upgrade_option children not yet selected) | ✅ Built |
| Context menu: Include/Exclude from Total (toggle-included) | ✅ Built |
| Context menu: Remove Line | ✅ Built |
| `lineAdder` Alpine component — child-mode state (parentLineId, childLineRole, childModeLabel) | ✅ Built |
| `lineAdder` — child-mode banner (amber bar showing what's being added to which parent) | ✅ Built |
| `lineAdder` — `@start-child-add.window` event listener | ✅ Built |
| `lineAdder` — targets `#chips-{parentId} beforebegin` in child-mode | ✅ Built |
| Warranty chip `hx-vals` — includes `line_role: "warranty"`, targets chips row beforebegin | ✅ Built |
| Suggestion chips `hx-vals` — includes `line_role: "suggested"` | ✅ Built |
| workspace.html tbody uses `sorted_lines` context var (tree-sorted) | ✅ Built |

**Note:** `line_role`, `is_included`, `option_label` are new columns on `quote_lines`. Existing databases need an ALTER TABLE or drop-and-recreate to include them.

---

### Deferred to Phase 2

| Item | Reason |
|---|---|
| Credit memo / refund check workflow | Needs warranty claim flow complete first |
| Option Groups visual rendering (color-coded sections in lines table) | After suggested sells stable |
| "View Related" slide-over per quote line | After chips proven in daily use |
| Auto-open slide-over for high-value bundles (inframes, turbos) | After manual chips confirmed |
| `WarrantyService` full implementation | Phase 2 |
| Enrichment scraper active state (PAI/HHP/ATL API) | Phase 2 |

---

---

## Part 8 — Next Build Queue (priority-ordered as of 2026-05-24)

*Keith's exact answer: "The biggest blocker is not being able to produce/send professional quote PDFs
and then smoothly convert that into invoice/payment workflow."*

---

### NEXT-1 — Database Recreate (5 minutes)
**Why:** `line_role`, `is_included`, `option_label` are new columns on `quote_lines`.
`create_all()` does not ALTER existing tables. Since all data is test data, drop `data/jaks.db`
and run `init_db()` to recreate fresh.
Also: `ri_counter` and `core_slip_counter` were not in `bump_counter`'s year-rollover key list —
add them while touching settings_utils.

---

### NEXT-2 — Quote PDF (weasyprint)
**Priority:** #1 blocker for Keith going live.
**PDF library:** `weasyprint` >= 62 (pure Python on Windows, no GTK needed since pydyf switch).
Add to requirements.txt and install.

**Quote PDF structure:**
```
[JAKS logo/name]                    [QUOTE / PROPOSAL]
[Company address / phone / email]   Quote #: Q-2026-0001
                                    Date: May 24, 2026
Bill To:                            Valid Until: Jun 23, 2026
[Customer company name]
[Customer address]
[Customer phone]

───────────────────────────────────────────────────────
 SKU          Description          Qty    Price    Total
───────────────────────────────────────────────────────
 JAKS-PAI-    Cylinder Head ISX     1    $1,245   $1,245
 [WARR]       Extended Warranty    12mo            $124.50
───────────────────────────────────────────────────────
                                        Subtotal: $1,369.50
                                           Total: $1,369.50

─── UPGRADE OPTIONS / ALTERNATIVES ─────────────────────
The following alternatives were quoted but not included
in the total above. Pricing available upon request.

 ALT  Stage 3 Performance Head      1   $1,895
─────────────────────────────────────────────────────────

Notes: [quote.notes]
This quote is valid until [valid_until]. Prices subject to change.
Thank you for your business — JAKS Diesel Parts
```

**Rules:**
- Only `is_included=True` lines appear in the main line items section
- `is_included=False` + `line_role=upgrade_option` lines appear in the Alternatives section
- Warranty/optional child lines appear under their parent (indented) in main section
- Company info pulled from settings keys: `company_name`, `company_address`, `company_phone`, `company_email`
- Endpoint: `GET /quotes/{id}/pdf` → returns PDF as attachment

---

### NEXT-3 — Invoice PDF
**Priority:** #2. Same architecture as quote PDF, different header + adds payment info.

**Invoice PDF additions vs Quote PDF:**
- Header: "INVOICE" (not "QUOTE")
- Shows: Payment Terms, Due Date
- Shows: Amount Paid, Balance Due (if partial payment)
- Shows: CC Surcharge line if applicable
- Shows: Tax line if applicable
- No Alternatives section (only sold/billed lines)
- Endpoint: `GET /invoices/{id}/pdf`

---

### NEXT-4 — Payment Workflow
**Priority:** #3. Completes the quote→invoice→paid loop.

**Design:** Flexible for counter AND delayed payments.

**From invoice detail:**
- "Record Payment" button → opens right-side slide-over
- Form: Payment Method (Cash / Check / Card), Amount, Check# (if Check), Date (defaults today), Notes
- Submit → PaymentService.record_payment() → PaymentAllocation created → invoice balance updates

**From payments list `/payments`:**
- List all payments: date, customer, method, amount, allocated invoices
- "New Payment" → same form but customer search first (for delayed/mailed checks)

**Payment model (already built):**
- `Payment` → one payment event
- `PaymentAllocation` → links payment to one or more invoices with amount_applied
- Unapplied balance stays as customer credit

**PaymentService methods needed (currently skeleton):**
- `record_payment(customer_id, method, amount, invoice_ids, check_number, notes)` → creates Payment + allocations
- `apply_to_invoice(payment_id, invoice_id, amount)` → creates/updates allocation
- `reverse_payment(payment_id, reason)` → marks reversed, restores balance

---

### NEXT-5 — Customer Balance Panel on Quote Screen
**Priority:** #4. After PDFs and payments are solid.
Mini-panel shows: Terms | Open Balance | Overdue | Credit Balance | Cores Owed
Positioned: top-right of workspace, below the header bar.
Data: customer.payment_terms, sum(invoices balance_due), sum(overdue), customer.credit_balance, count(open cores)

---

### NEXT-6 — Research Status on Quote Lines
**Priority:** #5. After the above.
Visual flag on quote line cell: color-coded status (Yellow/Blue/Green).
Inline status selector per line (dropdown).
Creates ResearchItem (RI-XXXX) via ResearchService when status is set.

---

---

### Session 2026-05-27 — Code Review, Bug Fixes, Startup, Dashboard, Inspection Gate

| Item | Status |
|---|---|
| **Code review — all 5 angles (line-by-line, removed-behavior, cross-file, language pitfalls, data integrity)** | ✅ Done |
| **BUG FIX: `GET /customers/import` + `POST /customers/import` route shadowed by `/{customer_id}` wildcard** | ✅ Fixed |
| `_safe_float()` helper — tolerates `"5%"`, `"$10"`, `"1,200"` in CSV import | ✅ Fixed |
| `is_tax_exempt` was parsed in `_parse_rows` but never written to Customer in confirm route | ✅ Fixed |
| `isinstance(rows, list)` guard after `json.loads` in `customer_import_confirm` | ✅ Fixed |
| `int(customer_id_raw)` ValueError guard in `log_call_global` | ✅ Fixed |
| **BUG FIX: `CoreSlip` created every partial return — idempotency check added** | ✅ Fixed |
| **BUG FIX: `db.rollback()` missing in create_core_slip exception handler** | ✅ Fixed |
| **BUG FIX: `CoreSlip.customer` type annotation `Mapped[Customer \| None]` → `Mapped[Customer]`** | ✅ Fixed |
| **BUG FIX: `customer_id=None` passed to `create_core_slip` — added explicit ValueError guard** | ✅ Fixed |
| **BUG FIX: `Invoice.payment_allocations` → `Invoice.allocations` (dashboard.py — crash on startup)** | ✅ Fixed |
| **BUG FIX: `Invoice.payment_allocations` → `Invoice.allocations` (reports.py ×2 — AR aging, sales)** | ✅ Fixed |
| **`START JAKS.bat`** — double-click startup for Keith | ✅ Created |
| **Smoke test** — 20/20 key pages HTTP 200 with `raise_server_exceptions=True` | ✅ Passing |
| **Dashboard: Open SOs KPI widget** — queries `SOStatus.OPEN/PARTIAL/HOLD`, live in template | ✅ Built |
| **Core inspection gate** — Accept / Hold / Reject at point of customer return | ✅ Built |
| `CoreInspectionOutcome.HOLD` constant added | ✅ Added |
| `record_customer_return()` — credit gated behind `inspection_outcome == ACCEPTED` | ✅ Done |
| `complete_inspection()` service method — finalises held cores, issues deferred credit | ✅ Built |
| `POST /cores/{id}/complete-inspection` route | ✅ Built |
| Cores list Stage 1.5 — "Pending Inspection" section with Accept/Reject decision form | ✅ Built |
| Stage 1 return form — inspection outcome dropdown replaces plain condition text | ✅ Done |
| Stage 2 (Ready to Ship) — excludes held cores from vendor queue | ✅ Fixed |

**Known pending items before go-live:**
- Quote PDF (NEXT-2) — WeasyPrint 68.1 requires GTK/Pango on Windows (not pure Python). Browser print ?auto=1 is the working path. Real PDF download requires GTK runtime install.
- QBO push (STEP 17) — Keith confirmed no hard deadline
- NEXT-6 — Research Status inline on quote lines (visual flag + status selector per line, creates RI-XXXX)

---

### Session 2026-05-27 (continued) — Payment, Print UX, Plan Audit

| Item | Status |
|---|---|
| **Audit** — NEXT-1 through NEXT-5 verified: DB columns present, counter rollover correct, payment workflow built, customer balance panel built in workspace | ✅ Confirmed all done |
| **BUG FIX: monthly revenue chart** — `func.sum(Invoice.total)` → `func.sum(InvoiceLine.unit_price * qty * (1 - disc/100))` joined through InvoiceLine.invoice | ✅ Fixed |
| **Dashboard research queue** — `joinedload(QuoteLine.quote).joinedload(Quote.customer)` + `research_status.in_(['researching','waiting_dealer'])` | ✅ Built |
| **Dashboard monthly revenue chart** — Chart.js 6-month bar chart, monthly_labels_json/monthly_totals_json | ✅ Built |
| **Print auto-trigger** — `?auto=1` added to Print/Save PDF links in `_header_actions.html` and `invoices/detail.html` | ✅ Done |
| **Invoice payment date field** — Date input (defaults today) added to Record Payment form; `payment_date` wired through to PaymentService | ✅ Done |
| **`GET /payments/new`** — Customer dropdown → open invoices list with checkboxes; auto-sums selected balances into Amount field | ✅ Built |
| **`POST /payments/new`** — Creates Payment + PaymentAllocation records for multi-invoice payments; redirects to payment detail | ✅ Built |
| `payments/new.html` template — Alpine.js `paymentForm()` component, balance map, toggleAll, recalcAmount, amountWarning | ✅ Built |
| **"+ New Payment" button** on `/payments/` header and empty-state | ✅ Added |
| **BUG FIX: Invoice void inventory reversal** — `qty_on_hand += ln.qty` should reverse exactly what was deducted, not the original qty. Finalise uses `max(0,...)` so deduction may be less than ln.qty. Fixed by looking up original INVOICE_SALE `InventoryTransaction.qty_change` to reverse precisely | ✅ Fixed |
| **BUG FIX: `float(get_setting_value_db(..., "cc_surcharge_pct"))` crash** — empty string in DB (row exists, fallback ignored). Fixed with `strip()` + try/except in both invoice GET and POST /new routes | ✅ Fixed |
| **BUG FIX: `int(get_setting_value_db(..., "default_core_return_days"))` same empty-string crash** — fixed in `core_service.py` | ✅ Fixed |
| **Smoke test** — 26/26 pages HTTP 200 with `raise_server_exceptions=True` | ✅ Passing |

---

### Session 2026-05-27 (continued 2) — Core Auto-Add, Research Status Confirmation

| Item | Status |
|---|---|
| **CONFIRMED: NEXT-6 Research Status on quote lines is ALREADY BUILT** — color-coded badges, context menu 5 statuses, POST route, ResearchService.create_research_item() on first flag | ✅ Confirmed done |
| **FEATURE: Core charge auto-add on quotes** — `QuoteService.add_line()` now auto-adds a CORE_CHARGE child line when product.has_core=True and customer_core_charge > 0; returns `list[QuoteLine]` | ✅ Built |
| **FEATURE: Core charge auto-add on invoices** — `InvoiceService.create_invoice()` auto-adds core lines post-creation; `InvoiceService.add_line()` auto-adds when product line is added to draft | ✅ Built |
| **FEATURE: Quote→Invoice conversion no double-core** — `convert_to_invoice()` filters out CORE_CHARGE lines from quote (invoice auto-adds fresh ones with correct parent_line_ids) | ✅ Fixed |
| **Smoke test** — 27/27 pages HTTP 200 with raise_server_exceptions=True | ✅ Passing |
| **Functional tests** — quote core auto-add, invoice core auto-add, quote→invoice no-double-core | ✅ All PASS |
| **BUG FIX: `invoices/_new_picker.html` missing** — workspace-flow GET /invoices/new rendered a template that didn't exist; created the slide-over customer picker partial | ✅ Fixed |
| **InvoiceLine schema migration** — added `is_auto_generated` + `is_locked_to_parent` columns; stale smoke DB deleted and recreated cleanly from init_db() | ✅ Done |
| **Test harness** — `tests/test_smoke.py` committed to repo (27 parametrized routes, pytest, DB=jaks_test_smoke.db) | ✅ Added |

**Known pending items before go-live:**
- Quote PDF (NEXT-2) — WeasyPrint 68.1 requires GTK/Pango on Windows (not pure Python). Browser print ?auto=1 is the working path. Real PDF download requires GTK runtime install.
- QBO push (STEP 17) — Keith confirmed no hard deadline

---

*This document governs Phase 1 build decisions.*
*Update it as decisions are locked or scope changes.*
*Do not start a step until the previous step's gate condition is met.*
