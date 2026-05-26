# JAKS Inventory — Owner Interview Notes
*Running document — append each session's answers here*
*Do NOT derive architecture directly from this file — see the requirements .md files*
*This is the raw source of truth. Requirements docs are the derived spec.*

---

## Session 1 — Initial Discovery Interview
*Date: 2026-05-21*

### Business Overview
- Small heavy-duty diesel parts dealership, primarily local, growing toward online (Shopify, eBay)
- Two users: Keith (day-to-day operations) + wife (bookkeeping)
- Customers: local diesel repair shops (B2B), actively growing customer base
- Primary vendors: PAI Industries (preferred), Highway and Heavy Parts (HHP), ATL Diesel

### Key Decisions Locked
- Architecture: FastAPI + HTMX + SQLite, local web server, browser-based
- QBO: push invoices, payments, and vendor bills only — no inventory sync
- CC surcharge: 3% configurable in settings
- Startup: Windows Service (auto-starts on boot)
- Database: SQLite now, SQLAlchemy ORM for future PostgreSQL migration path

### What to Cut
Fleet management, ESN lookup UI (future only), barcode scanning, period close wizard,
work orders, daily routes, SMS/marketing automation, tiered pricing (replaced by per-customer discount %),
complex QBO inventory sync.

---

## Session 2 — First Impressions & Quoting Interview
*Date: 2026-05-22*

---

### Section 1 — First Impressions & Navigation

**Q1: Initial testing feedback?**

Biggest issue: constant workflow interruption from having to leave active screens to create missing records.

Example broken flow: Start quote → no customer → leave → create customer → back to quote → no product → leave → create product → no vendor → leave → create vendor → return through multiple screens. Full detail in `UX_NAVIGATION_REQUIREMENTS.md`.

**Q1b: Does sidebar nav match how you think about the business?**

- "Catalog" and "Products" seem redundant (they are — Catalog is a section, Products is the only link under it, they feel like the same thing)
- Sales grouping (Quotes, Invoices) makes sense functionally but "I'm not from an accounting standpoint — perhaps better left"
- Research done — traditional ERP structure recommended (Customers/Vendors as section headers rather than Sell/Buy)
- **Decision: Keep traditional naming. Do NOT use Sell/Buy.**
- Nav rebuild deferred — continue interview first

**Q1c: Dashboard — what do you want to see every morning?**

- Current widgets are a good starting point
- No changes needed yet — revisit after workflows are solid

---

### Section 2 — Invoices & Quotes

**Q4: How do you get an invoice to a customer? What happens after "Create Invoice"?**

Delivery methods: **print, text, and email** — all three used.

**Critical workflow insight — Sales Orders are needed:**

After creating an invoice, the system should pull inventory and bill the customer.
BUT: a step is needed before invoicing for items not yet on hand.

- If item **in stock** → can invoice immediately
- If item **not in stock** → should become a **Sales Order** while product awaits receipt
- Keith wants the ability to **collect payment at the Sales Order stage** (before goods received) — this is intentional and unique to his workflow
- If someone tries to invoice a zero-stock item: either auto-revert to Sales Order OR allow negative inventory with a behind-the-scenes PO prompt
- **Needs research** on standard ERP SO → Invoice flow before building

*→ See Section 2 Research Notes below for findings*

**Q5: Do you modify invoices after creation?**

Yes — frequently. Reasons:
- Add an additional part to an existing invoice
- Remove a part if same-day or close timeframe (to avoid writing a credit)
- Invoices need an editable/"open" state before they are locked/sent
- Once locked, changes require a credit memo

**Q6: Invoice/quote numbering format?**

Likes current format: `INV-2026-0001`, `Q-2026-0001` ✅
No changes needed.

**Q7: Returns and credit memos?**

Two distinct processes — **both require a signed customer document:**

**Standard Return (wrong part, etc.):**
- Customer signs a return document acknowledging the return
- JAKS issues a refund (written check to customer)
- Not warranty-related

**Warranty Claim (part failed under warranty):**
- Similar to HHP and ATL Diesel's warranty process (these are the benchmarks)
- Customer signs a warranty claim document
- JAKS submits the claim to the **vendor** for approval
- Vendor reviews and either **approves or denies**
- If approved → credit issued to customer (or replacement)
- If denied → no credit, customer informed
- Key: warranty claims can be **pending vendor decision for days or weeks**
- Warranty workflow needs status tracking: `pending_customer` → `submitted_to_vendor` → `vendor_approved` / `vendor_denied` → `credited` / `closed`

**Q8: Net terms and overdue?**

- Some customers are on **net 30**
- After 30 days overdue → **interest charge applies**
- Interest rate is **set per customer** based on the relationship (not a global rate)
- System needs: due date on invoice, overdue flag, per-customer interest rate field

**Q9: Customer PO numbers and job identifiers?**

Yes — the following customer-provided fields should be optional and displayable on Sales Orders and Invoices:
- Customer PO number
- Customer Job number
- ESN (Engine Serial Number)
- Engine Manufacturer
- Engine Model

*ESN / engine info is useful for both quoting context and customer identification when no PO number is provided.*

---

### Section 2 — Research Notes: Sales Order → Invoice Flow (ERP Standard)

**Standard B2B wholesale/distribution flow (NetSuite, Odoo, SAP, QuickBooks Enterprise):**

```
Quote → Sales Order → [Fulfillment/Receive] → Invoice → Payment
```

| Stage | What Happens | Inventory Impact |
|---|---|---|
| Quote | Proposal, no commitment | None |
| Sales Order | Customer confirmed, items committed | Qty committed ↑ (reserved) |
| Receive (if backordered) | PO received, goods arrive | Qty on hand ↑ |
| Invoice | Goods shipped/delivered, AR created | Qty on hand ↓ |
| Payment | Cash applied | AR cleared |

**Keith's specific requirement (differs from standard):**
- Can collect payment at the **Sales Order stage** even before goods arrive
- Standard ERPs typically invoice first then collect — Keith wants to collect a deposit/full payment on the SO
- This is valid for a small dealer pre-collecting before ordering from vendor

**How to handle zero-stock invoicing (decision needed before build):**

*Option A — Auto-revert to SO:*
If item has zero stock when invoice is created → automatically becomes a Sales Order. Invoice is created only when all items are fulfilled.

*Option B — Allow negative inventory:*
Invoice anyway, inventory goes negative. Behind-the-scenes prompt or auto-PO creation brings it back to zero.

*Option C — Hybrid (recommended):*
- If all items in stock → offer "Invoice directly"
- If any item out of stock → offer "Create Sales Order" with option to collect payment now
- When SO is fulfilled → convert to Invoice with one click
- Flag backorder lines clearly on the SO

**Recommendation:** Option C. Matches Keith's stated workflow. Research confirms this is how Odoo and NetSuite handle it for B2B distribution.

**Impact on schema:**
- Add `sales_orders` table (mirrors invoices but with `status: open | partial | fulfilled | invoiced | cancelled`)
- Add `so_lines` table with `qty_committed`, `qty_fulfilled`, `source: stock | backorder`
- Invoice has optional `sales_order_id` FK (converted from SO)
- Add `qty_committed` to products (already exists ✅)

---

### Section 2 — Research Notes: Return & Warranty Process

**Standard dealer warranty flow (modeled on HHP / ATL Diesel):**

HHP and ATL both require:
1. Dealer submits a warranty claim with part details, failure description, and supporting info
2. Vendor reviews (can take days to weeks)
3. Vendor approves (issues credit) or denies
4. Dealer issues credit to end customer if vendor approved

**Required for JAKS:**

| Document | Trigger | Signed By |
|---|---|---|
| Return Authorization (RA) | Customer returning any part | Customer |
| Warranty Claim Form | Part failed under warranty | Customer |

**Warranty Claim States:**
```
draft → submitted_to_vendor → vendor_approved → customer_credited → closed
                           ↘ vendor_denied → customer_notified → closed
```

**Impact on schema:**
- `return_authorizations` table: ra_number, customer_id, invoice_id, reason, status, created_at
- `return_lines` table: ra_id, product_id, qty, unit_price, restocking_fee
- `warranty_claims` table: claim_number, ra_id (optional), customer_id, vendor_id, product_id, failure_description, submitted_at, vendor_decision, vendor_decision_at, credit_amount, status
- Credit memo links back to original invoice

---

## Session 3 — Sections 3–11 Interview
*Date: 2026-05-22*

---

### Section 3 — Payments & Money

**Q10: How do customers pay you? What payment types do you accept?**

- **Credit/debit card** — two modes:
  - **Online:** QBO Payments link embedded in emailed invoice (customer clicks and pays)
  - **In-person:** Tap/chip card reader at the counter
- **Check** — standard business check
- **Cash** — accepted
- 3% credit card surcharge applies (already in settings, configurable)

**Q11: Do any customers carry a running balance / house account?**

Yes.
- Some customers accumulate charges over time (house account / running tab)
- Keith can write a check to a customer who has a credit balance (e.g., returned cores, overpayment)
- System needs: per-customer running balance visible at a glance

**Q12: Net terms — who gets them and how do you track overdue?**

- **Net 30** for established customers (house accounts)
- **COD** for newer or less-established customers
- Overdue → interest charge kicks in (rate set per customer — already captured in Q8)
- System needs: aging summary, overdue flag, per-customer terms field (`net_30`, `cod`, etc.)

**Q13: Sales tax — how does it work for your customers?**

- Tax rates vary **per customer**
- Some customers are **fully tax-exempt** (already have `is_tax_exempt` flag in Customer model ✅)
- Some customers are **taxable** — rate depends on their location/status
- System needs: per-customer tax rate field (or exempt flag), applied at invoice time
- TaxJar integration is Phase 3 — for now, manual rate per customer is sufficient

---

### Section 4 — Products & Inventory

**Q14: How do you figure out what to charge for a part?**

- **Cost-first workflow:** Keith typically looks up what he paid (or will pay) for the part first
- Then applies a markup percentage to get the selling price
- Sometimes price-overrides a specific part if market price is known
- Per-customer discount % is then applied on top
- System needs: cost visible during product entry, markup % field, price override option, per-customer discount applied at quote/invoice time (all already designed ✅)

**Q15: How do you want to identify/number your parts (SKU)?**

Format confirmed: **`JAKS-[VENDOR_CODE]-[PART_NUMBER]`**
- Example: `JAKS-PAI-123456`, `JAKS-HHP-789012`, `JAKS-MIG-54321`
- Vendor code (e.g., `PAI`, `HHP`, `MIG`) is set on the **Vendor record** — not typed manually each time
- When creating a product, selecting the vendor auto-populates the vendor code prefix
- Keith types only the part number portion; system assembles the full SKU

**Q16: Cross-referencing — if a customer asks for a competitor part number, how should that work?**

- Customer calls with an OEM number or competitor SKU
- Search should return:
  1. **The requested part** (exact match) first
  2. **Alternative/equivalent parts** JAKS stocks (cross-references)
- Need to be able to see: "customer asked for X, we can supply Y (equivalent)" — show both in results
- System needs: `cross_references` table (already planned ✅) — also useful for quoting substitutions

**Q17: Do any products need serial number tracking?**

Yes — **cylinder heads** specifically.
- Each cylinder head unit has its own serial number
- Serial numbers must be tracked: which serial # was sold to which customer on which invoice
- Potentially also: which serial # came in on which PO (for traceability)
- Other products: no serial tracking needed (standard qty tracking is fine)
- System needs: `product_serial_numbers` table, linkable to PO receipt lines and invoice lines

**Q18: Do you want product photos?**

Yes.
- Images should display in the product detail screen within the app
- Scrapers (PAI, HHP, ATL) should pull product images automatically when enriching a product record
- Keith may also want to manually upload/attach images
- Images are also useful when listing to Shopify/eBay (Phase 2/3)
- System needs: image storage path in product record, scraper to save images locally

**Q19: Do you sell kits or bundled parts?**

Yes — **two distinct kit types:**

**Type 1 — Vendor Kit (pre-assembled by vendor):**
- Vendor sells it as one SKU (e.g., a gasket kit with 12 individual gaskets)
- JAKS buys it as one unit and sells it as one unit
- The sub-parts may or may not be individually tracked
- Example: PAI sells "Kit Part# 12345" — JAKS sells it the same way

**Type 2 — JAKS-Built Kit (custom assembly):**
- Keith assembles the kit himself from individual parts in inventory
- Each component is its own product with its own inventory count
- When a kit is sold, each component's inventory is decremented
- JAKS assigns a kit SKU; the kit has a BOM (bill of materials)
- Example: Keith pulls 3 individual parts from stock, bundles them, sells as "JAKS Engine Kit"

System needs:
- `product_kits` table (already scaffolded ✅)
- Kit type flag: `vendor_kit` | `custom_kit`
- BOM lines: kit_id → component product_id + qty
- On invoice: JAKS-built kit decrements each component; vendor kit decrements as single SKU

---

### Section 5 — Cores Deep Dive

**Q20: When a customer returns a core and gets credit — how do they receive that credit?**

**Customer's choice — offer both options:**
1. **Apply to next invoice** (account credit — reduces next bill)
2. **Issue a check** (refund if no upcoming invoice or customer prefers cash)

Keith decides case-by-case based on the customer relationship. System should let him choose which at the time of core return processing.

*→ Answers D3 from open decisions for the core/return side.*

**Q21: Is the core charge always shown on quotes, sales orders, and invoices?**

**Yes — always.**
- Every quote or invoice that includes a core-eligible part must show the core charge as a separate line item
- Core charge is **not optional** — it must appear automatically when the product has a core charge configured
- The customer sees: [Part price] + [Core charge] on the document
- When the core is returned: core line is credited (either to account or check)

**Q22: When you ship a core back to the vendor, what documentation do you need?**

A **printed shipping document** is required, containing:
- **RMA number** (vendor's Return Merchandise Authorization)
- **Tracking number** (UPS/FedEx label number)
- **Sign-off section** (Keith or staff member signs to acknowledge shipment)
- Part details: part number, description, quantity
- This document stays on file as proof of return

System needs: generate a printable PDF "Core Return Shipment" document with these fields. Attach to the core charge record.

**Q23: Do you mark up the core charge amount?**

**Yes.**
- The vendor charges JAKS a core deposit (e.g., $50)
- Keith charges the customer a higher core charge (e.g., $75)
- The markup on cores is a separate profit center
- System needs: `vendor_core_charge` (what JAKS pays vendor) and `customer_core_charge` (what customer pays) as separate fields on the product record
- Margin on cores should be visible

---

### Section 6 — Purchase Orders & Vendors

**Q24: Do you ever place orders verbally/by phone and track them later?**

Yes.
- Keith calls PAI or HHP, places a verbal order, gets a confirmation number over the phone
- He wants to **log that PO in the system immediately**, even before the vendor sends a written confirmation
- The PO in the system is JAKS's record — vendor's confirmation number goes in a reference field
- System should support: `vendor_confirmation_number` field on PO, status `verbal_order` before it becomes a formal `sent` PO

**Q25: What happens when a vendor ships a partial order — how do you handle the bill?**

*Needs research — see Section 6 Research Notes below.*

Key requirement: **only pay for what arrived.** If vendor bills for 10 but only 8 arrived → flag the discrepancy, only approve 8 for payment.

**Q26: What payment terms do you have with your vendors?**

- Primary vendors: **net 30** (PAI, HHP, ATL)
- Some vendors may be COD or credit card
- System needs: `payment_terms` field on Vendor record (matches customer-side terms field)
- Vendor bills should show due date based on terms

**Q27: How do you send purchase orders to vendors?**

- **Print** (fax or hand-deliver for local)
- **Email** (PDF attachment — most common for PAI/HHP/ATL)
- Same delivery modes as invoices — print and email
- PO document should look professional: JAKS logo, vendor address, line items, totals, terms

---

### Section 6 — Research Notes: PO Partial Receipt & 3-Way Match

**Standard distribution/wholesale flow (NetSuite, Odoo, QuickBooks Enterprise):**

The "3-way match" is the industry standard for controlling vendor payments:

```
Purchase Order → Goods Receipt (what arrived) → Vendor Bill (what vendor charged)
```

| Step | Document | Purpose |
|---|---|---|
| 1 | Purchase Order | JAKS's commitment to buy |
| 2 | Goods Receipt / Receiving | Records what actually arrived, qty by qty |
| 3 | Vendor Bill / AP Invoice | Vendor's claim for payment |
| 4 | Match | System compares PO qty → received qty → billed qty |

**Discrepancy handling:**
- If vendor bills for more than received → **flag it**, hold payment on overage, contact vendor
- If vendor bills for less than received → pay what's billed (rare, windfall)
- Partial shipment: receive what arrived, PO stays open for remaining qty, bill only for received qty

**Recommended for JAKS:**

```
PO created (ordered qty) 
  → Receive shipment (partial or full) → creates InventoryAdjustment 
  → Vendor Bill created from received qty (not PO qty) 
  → If Bill qty > Received qty → system flags discrepancy, blocks approval
  → AP balance updated, payment scheduled by terms
```

**Impact on schema:**
- `purchase_orders` + `po_lines` already exist ✅
- Add `po_receipts` table: receipt_id, po_id, received_at, received_by, notes
- Add `po_receipt_lines` table: receipt_id, po_line_id, qty_received
- Add `vendor_bills` table: bill_id, po_id, vendor_id, bill_number, bill_date, due_date, status
- Add `vendor_bill_lines` table: bill_id, po_line_id, qty_billed, unit_cost
- Add `qty_received` and `qty_billed` tracking to `po_lines`
- Discrepancy flag: `qty_billed > qty_received` on any line → bill status = `discrepancy`

---

### Section 7 — Customers & CRM

**Q28: What do you actually need for customer relationship management?**

**Primary need: Quote follow-up tracking.**
- When a quote is sent and the customer hasn't responded → need a reminder / follow-up task
- "Did he call back? Did we win this job? Did we lose it?"
- System needs: follow-up date on quotes, dashboard widget for "quotes needing follow-up today"

*→ See Section 7 Research Notes below for CRM follow-up best practices.*

**Secondary needs:**
- Call log (already built ✅) — log every customer interaction
- Notes per customer — especially engine/fleet info for each shop
- Lost sale tracking: when a quote is declined, log the reason (price, availability, competitor)

**Q29: What customer-specific notes do you need to track?**

- **Engine type notes per shop:** "This shop works on [specific engine models]" — useful context when a customer calls with an ESN or just says "I need parts for my engine"
- General notes field for anything customer-specific
- Future: associate engine configs to a customer/shop (maps to ESN lookup architecture)

System needs: `customer_notes` field (free text), and eventually a `customer_engine_profiles` table linking shops to engine families they service.

**Q30: How would you want to import your existing customers?**

Three sources:
1. **Excel spreadsheet** — Keith has existing customer list in Excel
2. **QuickBooks Online** — existing QBO customers could be imported
3. **Phone contacts** — some customers are only in phone contacts

Priority for Phase 1: **Excel import** (most common, most controlled)
CSV upload → map columns → review before import

QBO customer sync (pull) can be Phase 2 — not needed for launch.

---

### Section 7 — Research Notes: Quote Follow-Up CRM

**What small B2B distributors actually need (vs. full CRM):**

Research: Zoho CRM for small dealers, Pipedrive for B2B sales, QuickBooks-adjacent CRM behavior.

The JAKS use case is not "sales pipeline management" — it's "don't let a hot quote go cold." That's a simpler, more focused problem.

**Recommended minimal CRM for JAKS:**

| Feature | What It Does | Priority |
|---|---|---|
| Follow-up date on quote | Set a "call back by" date | Phase 1 |
| Dashboard: overdue follow-ups | "These quotes need a call today" | Phase 1 |
| Quote outcome tracking | Won / Lost / No decision | Phase 1 |
| Lost reason log | Why we didn't get the job (price/stock/speed/other) | Phase 1 |
| Call log per customer | Already built ✅ | Done |
| Customer engine profile notes | What engines this shop typically works on | Phase 1 |
| Customer import (Excel CSV) | Bulk onboard existing customers | Phase 1 |
| QBO customer pull | Import from QBO | Phase 2 |

**Quote follow-up states:**
```
sent → follow_up_scheduled → [called] → won | lost | no_decision
```

**Lost sale reasons to track (from Q — Lost Sales Causes research):**
- No inventory / couldn't source in time
- Price (customer found cheaper)
- Shipping / lead time too long
- No response (customer went elsewhere without telling us)
- Wrong part / couldn't identify
- Other

**Impact on schema:**
- Add to `quotes` table: `follow_up_date`, `outcome` (enum: pending|won|lost|no_decision), `lost_reason`
- `quote_followups` table already scaffolded ✅ — populate it with: quote_id, scheduled_date, completed_at, notes, outcome
- Add `lost_sales_log` table: quote_id, customer_id, product_id, reason, competitor_name, competitor_price

---

### Section 8 — Reports & Visibility

**Q31: What reports do you need to run the business?**

Keith wants **QBO-style reports** as the benchmark:
- **Profit & Loss** — revenue vs. cost of goods vs. operating expenses
- **Sales by Customer** — who's buying the most, period over period
- **Sales by Product** — what's selling, what's not
- **Accounts Receivable Aging** — who owes money and how long overdue
- **Inventory Valuation** — current value of stock on hand
- **Open Purchase Orders** — what's been ordered and not yet received
- **Core Charges Outstanding** — cores not yet returned, aged by days outstanding

System needs: a `/reports` section with parameterized date ranges, exportable to PDF and CSV.

*Q32 and Q33 were not reached in this session — revisit in next interview.*

---

### Section 9 — Integrations
*Q34–Q36 not yet answered. See existing architecture decisions in PLAN.md.*

**Known from Session 1:**
- QBO: push invoices, payments, vendor bills only (OAuth) — no inventory sync
- Shopify: push products, sync sales (keys ready)
- eBay: Phase 3
- TaxJar: Phase 3

---

### Section 10 — Mobile & Access
*Q37–Q38 not yet answered.*

**Known from Session 1:**
- Local web server (FastAPI), browser-based
- Accessible from phone on same WiFi network
- No native app needed

---

### Section 11 — Build Priority & What's Missing

**Q41: Of everything discussed — scrapers, search, quoting, SOs — what do you want working first?**

**Scrapers / product enrichment — sooner rather than later.**
- The product entry workflow is heavily dependent on pulling accurate data from PAI, HHP, and ATL
- Every new product Keith enters should have an "Enrich" button that auto-fills: title, description, cost, images, cross-references, categories
- This is not a separate scraper tool — it's **baked into the product entry screen** as a one-click action
- Without enrichment, product entry is slow and error-prone
- Build enrichment early so every subsequent product entry is fast

**Q42: Looking at the current app — what do you like? What needs to change most?**

**What Keith likes:**
- PAI / HHP / ATL scrapers — keep them, they save time
- Product entry screen: categories, manufacturer field, ESN/engine fields — the right fields are there
- Appearance of quotes and invoices — the documents look professional

**What needs the most work:**
- The workflow interruptions (inline creation — already identified as #1 problem ✅)
- Navigation structure (Catalog/Products redundancy — already identified ✅)
- Sales Orders — the missing step between quote and invoice
- Quote screen needs to be faster and more keyboard-driven
- Reliability over everything — the current PySide6 app's instability is the core reason for the rebuild

---

## Sections Status

| Section | Status |
|---|---|
| 1 — First Impressions & Navigation | ✅ Complete |
| 2 — Invoices & Quotes | ✅ Complete |
| 3 — Payments & Money | ✅ Complete |
| 4 — Products & Inventory | ✅ Complete |
| 5 — Cores Deep Dive | ✅ Complete |
| 6 — Purchase Orders & Vendors | ✅ Complete (partial billing researched) |
| 7 — Customers & CRM | ✅ Complete (follow-up researched) |
| 8 — Reports & Visibility | ✅ Q31 answered; Q32–Q33 deferred |
| 9 — Integrations | Deferred (decisions already locked in PLAN.md) |
| 10 — Mobile & Access | Deferred (decisions already locked in PLAN.md) |
| 11 — Build Priority | ✅ Q41–Q42 answered |
| 12 — Parts Research & Callbacks | ✅ Complete (R1–R7 answered; R-A through R-D open) |
| 13 — Suggested Sells & Warranty Upsell | ✅ Complete |

---

## Open Decisions — Updated

| # | Decision | Status |
|---|---|---|
| D1 | Nav group labels: "Customers/Vendors" vs "Sell/Buy" | ✅ **Traditional (Customers/Vendors) confirmed** — see D1 research below |
| D2 | Zero-stock invoicing behavior | ✅ **Hybrid with override: warn + prompt SO, but allow invoice if user proceeds** |
| D3 | Warranty credit form | ✅ **Account credit created; Keith writes check at his discretion to refund** |
| D4 | Sales Order payment collection | ✅ **Three options: Full payment, Deposit, or No payment — user chooses per transaction** |
| D5 | Invoice edit / lock window | ✅ **Locks at end of day OR when invoice is synced to QBO / marked paid — whichever comes first** |
| D6 | Core credit method | ✅ **Customer's choice: next invoice credit OR Keith writes check** |
| D7 | Serial numbers scope | ✅ **Cylinder heads confirmed; others by exception** |
| D8 | Kit types | ✅ **Both: vendor kit (single SKU) + JAKS-built kit (BOM)** |
| D9 | Vendor bill approval | ⏳ Not yet asked |
| D10 | Customer import | ✅ **Excel Phase 1, QBO Phase 2** |
| D11 | Reports location | ⏳ Partial: in-app QBO-style reports wanted — scope to be confirmed |
| D12 | Parts Research Management | ✅ **Research lives inside quote (on quote line), cross-refs get confidence status, callbacks > follow-ups, solved research saves permanently and feeds cross-refs** |

**Open sub-questions (R-series) — needed before specified build steps:**

| # | Question | Needed Before |
|---|---|---|
| R-A | Template delivery: generate copy/paste text, or system sends email directly? | Step 10 (Quotes) |
| R-B | Formal urgency flag on research items? (Normal / Urgent / Truck Down) | Step 0 (Schema) |
| R-C | Auto cross-ref on resolve: create automatically, or confirm-prompt first? | Step 10 / Step 6 (Products) |
| R-D | Research callbacks widget: own separate dashboard widget, or combined with quote follow-ups? | Step 16 (Dashboard) |

---

## D2 — Zero-Stock Invoicing: Confirmed Behavior

**Rule:** Hybrid with user override.

When a user tries to invoice a product with zero (or would-go-negative) stock:
1. **Warn** the user that inventory will go negative
2. **Prompt:** "Would you like to create a Sales Order instead?"
3. **Allow the user to proceed with the invoice anyway** if they choose — do not block

This is intentional: Keith may know stock is incoming, may have already reserved product, or may have a reason to invoice before receipt. The system warns but trusts the operator.

**Behavior summary:**
```
Add zero-stock item to invoice
  → Warning banner: "⚠ This item has 0 in stock — inventory will go negative"
  → Prompt: [Convert to Sales Order] [Proceed with Invoice]
  → If Proceed: invoice created, inventory goes negative, flag on dashboard low-stock
  → If Convert: Sales Order created with same lines, option to collect payment
```

---

## D3 — Warranty Credit: Confirmed Behavior

**Account credit is the default outcome when a warranty claim is approved by the vendor.**
- System creates a credit memo / account credit against the customer's balance
- Keith retains the option to write a manual check refund at any time — this is an operational decision, not a system workflow
- No separate "issue check" workflow needed in the system — the credit lives on the account
- System needs: credit balance visible on customer record, credit applies automatically to next invoice (or manually applied)

---

## D4 — Sales Order Payment: Confirmed Behavior

**Three modes — Keith chooses per transaction:**

| Mode | When Used |
|---|---|
| **Full payment required** | Keith wants payment before releasing/ordering the part |
| **Deposit** | Partial upfront, balance due at invoice |
| **No payment now** | Net 30 customer, or trust relationship — collect at invoice |

UI: Payment collection field on the Sales Order form with three options. Not a global setting — decided transaction-by-transaction.

---

## D5 — Invoice Lock Window: Confirmed Behavior

**Invoice locks when the FIRST of these conditions is met:**
1. **End of current business day** (midnight, or configurable close-of-business time)
2. **Invoice is synced to QBO**
3. **Invoice is marked as paid**

While unlocked (open state): full edit — add lines, remove lines, change quantities, adjust price.
Once locked: changes require a credit memo. Edit button grays out, "Locked" badge appears.

*Note: "synced to QBO" means the QBO push has occurred. If sync hasn't run yet and it's still today, invoice stays editable.*

---

## D1 — Navigation Research: B2B Parts Dealer Patterns

*Research conducted to validate traditional Customers/Vendors structure and surface refinement questions for Keith.*

### Applications Surveyed

**1. Epicor Eagle N (Automotive/Truck Parts — Industry Standard)**
```
Top bar: Customers | Sales | Purchasing | Inventory | Vendors | Reports | Admin
```
- Customers is a standalone top-level section (accounts, history, AR)
- Sales: Quotes, Sales Orders, Invoices, Returns — all under Sales
- Vendors: standalone (vendor records, terms, contacts)
- Purchasing: POs, Receipts, AP — under Purchasing
- Inventory: Products, Categories, Adjustments
- **Key insight:** Documents (Quotes/Invoices/POs) live under their transactional section, not under the entity (not under "Customers")

**2. QuickBooks Enterprise — Wholesale & Manufacturing Edition**
```
Top nav: Customers | Vendors | Employees | Reports
Sub-menu under Customers: Create Invoice, Create Sales Order, Create Estimate (Quote)...
Sub-menu under Vendors: Enter Bills, Create PO, Receive Items...
```
- Transactions are nested under their related entity
- Customers → all sales transactions
- Vendors → all purchasing transactions
- **Key insight:** QBO groups everything under the entity, not the transaction type

**3. Odoo (Distribution/Manufacturing)**
```
App switcher: Sales | Purchase | Inventory | Accounting | CRM
Under Sales app: Customers, Quotations, Orders, Invoices
Under Purchase app: Vendors, Purchase Orders, Receipts, Bills
Inventory: Products, Operations, Reporting
```
- Separate "apps" per business function — entity + its transactions live together
- Sales app owns customers + all sales docs
- Purchase app owns vendors + all purchasing docs
- **Key insight:** Strong separation of sell-side and buy-side

**4. Zoho Books (SMB, closest to JAKS size)**
```
Left nav: Dashboard | Customers | Vendors | Items | Sales | Purchases | Banking | Reports
Under Customers: customer list only (CRM)
Under Sales: Quotes, Invoices, Sales Orders, Credit Notes, Payments
Under Purchases: Bills, Purchase Orders, Vendor Credits, Payments
```
- Entity records (Customers, Vendors) are standalone entries
- Transaction documents live under Sales / Purchases (not under the entity)
- Items (Products) standalone
- **Key insight:** Entity + transaction separation is cleaner for daily use

**5. Fishbowl (Manufacturing/Distribution SMB)**
```
Left nav: Sales | Purchasing | Manufacturing | Inventory | Accounting | Reports
Under Sales: Customer list, Quotes, Sales Orders, Invoices
Under Purchasing: Vendor list, Purchase Orders, Receipts
```
- Same pattern: entities nested under their transactional section

**6. Epicor Prophet 21 (Heavy-Duty Parts Distributor specific)**
```
Nav: Order Entry | Purchasing | Inventory | A/R | A/P | Customers | Vendors | Reports
```
- Customers and Vendors are standalone CRM/master data sections
- Transactions (Order Entry, Purchasing) are separate sections
- **Key insight:** At this scale, the transaction workflow gets its own top-level entry

---

### What the Research Shows

**Two dominant patterns emerge:**

| Pattern | How It Groups | Examples | Best for |
|---|---|---|---|
| **Entity-first** (QBO style) | Customer → their transactions; Vendor → their transactions | QuickBooks, some ERPs | Accountants and bookkeepers who think "per customer" |
| **Transaction-first** (Zoho/Epicor style) | Sales section has all sales docs; Purchasing has all PO docs | Zoho, Odoo, Fishbowl, Epicor | Operators who think "I need to create a quote" |

**For JAKS specifically:**
- Keith is the operator — he thinks "I need to create a quote" not "I need to go to the customer and then create a quote"
- Wife handles bookkeeping — she thinks "show me what this customer owes"
- **Transaction-first nav serves Keith; entity-first serves the bookkeeper**
- The hybrid (what's currently designed) gives both: entity records accessible directly, transaction docs accessible directly

---

### Proposed JAKS Nav — Refined After Research

```
Dashboard

─── CUSTOMERS ──────────────
  Customers           ← Customer records, AR, call log
  Quotes              ← All quotes (any customer)
  Sales Orders        ← All SOs (any customer)      [NEW — needs a spot]
  Invoices            ← All invoices (any customer)

─── VENDORS ─────────────────
  Vendors             ← Vendor records, AP, terms
  Purchase Orders     ← All POs (any vendor)

─── INVENTORY ──────────────
  Products
  Core Charges

─── SYSTEM ─────────────────
  Reports
  Settings
```

**Note on Sales Orders:** Sales Orders need a nav entry. Question is whether it lives under Customers alongside Quotes/Invoices, or gets its own spot.

---

### D1 — Refinement Interview Questions

The traditional structure is confirmed. These questions refine *how* it's organized within that structure:

**D1-A: Where should Sales Orders live in the nav?**

Now that Sales Orders are confirmed (the missing step between Quote and Invoice), they need a sidebar entry. Three options:

- *Option 1:* Under the CUSTOMERS section alongside Quotes and Invoices (keeps the sell-side document flow together: Quote → Sales Order → Invoice)
- *Option 2:* Its own section header (CUSTOMERS / VENDORS / **ORDERS** / INVENTORY / SYSTEM — heavier but makes SOs prominent)
- *Option 3:* Not in sidebar at all — only accessible by converting a Quote (minimalist, but limits searching open SOs)

*Research says: Zoho and Epicor put SOs under their "Sales" section with Quotes and Invoices. That matches Option 1 here.*

**D1-B: Does your wife (bookkeeper) primarily work in the app or in QBO?**

This matters because:
- If she works mainly in QBO → the app's nav should be optimized for Keith's workflow (operator-first, transaction-first)
- If she works in the app → we need quick paths to "what does this customer owe" and payment recording (entity-first)
- Both can be satisfied, but knowing the primary driver helps prioritize what goes where

**D1-C: Do you ever need to see all open Sales Orders in a list — or do you always work quote-by-quote?**

- If yes, need open-SO list view prominently (separate nav entry, dashboard widget)
- If you always follow a quote → SO → invoice flow, the SO could live contextually within the quote detail

**D1-D: Should "Core Charges" live under Inventory or get its own section?**

Current plan has it under Inventory. But cores have their own lifecycle (customer deposit, return, vendor credit) that's separate from inventory management.

- *Option A:* Keep cores under INVENTORY (simpler nav)
- *Option B:* Cores standalone section (makes the cores workflow more prominent, easier to find)

*Research note: Epicor Eagle gives cores/cores management its own section because it's a distinct revenue/liability process. Zoho doesn't have cores (not applicable). Given how important cores are to JAKS margins, Option B is recommended.*

**D1-E: Do you want "Reports" under SYSTEM or as its own top-level section?**

- *Under SYSTEM:* Reports tucked alongside Settings — less prominent, fewer clicks to reach
- *Standalone:* REPORTS as its own sidebar section — signals it's a daily operational tool, not an afterthought

*Research says: every system surveyed (Epicor, Zoho, QBO, Fishbowl) puts Reports at the top level. Recommendation: standalone.*

---

## Session 4 — Parts Research & Callback Tracking
*Date: 2026-05-23*

---

### Section 12 — Parts Research Management (R1–R7)

**R1: What do you actually do today when a part search fails?**

Current workflow is entirely ad-hoc — fragmented across four methods with no central tracking:
- Browser tab left open for the pending Google search
- Text message to self as a reminder
- Email to vendor requesting pricing or availability
- Handwritten note

All are easy to lose or forget. Nothing connects the research to the customer or quote that triggered it.

**R2: How often do you email vendors / call dealers / wait for callbacks?**

Often — and it takes time. Two distinct outreach patterns identified:

**VIN-to-part-number dealer lookup:**
Customer calls for a part (e.g., airbag) but doesn't know the part number. Keith asks for the VIN (last 8 digits usually, sometimes full). He calls the appropriate dealership, which crosses the VIN and provides the correct OEM part number and a dealer quote. Today: fully manual, slow, not tracked anywhere.

The ERP should generate a pre-filled template to send/read to the dealer — customer name, VIN, ESN, engine model, part description, callback info — auto-assembled from the quote and customer record.

Same concept applies to vendor requests: a quick template to request part sourcing info or pricing from PAI, HHP, or ATL.

**Cross-reference research today:**
OEM cross-references are found via Google and competitor websites. Keith can evaluate which crosses are reliable vs. uncertain. This maps directly to confidence statuses (see D12).

Keith's insight: *"We may want to look into a way where we can rule on known cross references. Where cross references have a status — Found, Proven, Unknown — where Found is a certain color, Proven is green."*

**R3: Should unresolved quote lines live inside the quote or become separate research tasks?**

Inside the quote. The line stays on the quote with a status indicator. The status column cell is highlighted until the status changes. This is the correct design — the quote remains the operational context for the research.

**Confirmed quote line research statuses:**
- `Researching` — actively being worked
- `Waiting Dealer` — awaiting dealer VIN/part callback
- `Waiting Vendor` — awaiting vendor availability/pricing callback
- `Found` — likely correct, not yet confirmed in a real sale
- `Proven` — confirmed correct through real use

**R4: Where should research items appear beyond the quote itself?**

Dashboard + quote list. Research items are visible in context on their quote, and also surfaced as a queue on the main dashboard.

**R5: How important is callback tracking vs. quote follow-up?**

Callback tracking is **more important** than quote follow-up. Reason: a research callback is pre-quote — without resolving the research, the customer doesn't even receive a price. Quote follow-ups assume the quote was delivered. Research callbacks are the harder blocker.

Assignment rule confirmed: whoever creates the research item is the assigned owner. Users with higher hierarchy access can view all open research items across the team.

**R6: Should resolved research be saved permanently?**

Yes — permanently, but editable later. Resolved research items become institutional knowledge. They should never be deleted.

**R7: Should solved research automatically become cross-reference entries?**

Yes. When research resolves and an OEM or vendor cross is identified, it should feed into the `cross_references` table. Whether this happens automatically or via a confirm-prompt is open (see R-C).

This is one of the highest-value discoveries in the design process. Over time, solved research accumulates into a proprietary knowledge base that makes JAKS faster and more accurate than any competitor who doesn't track this.

---

### Section 12 — Research Notes: Parts Research Architecture

**What this actually is:**
Not notes. Not CRM. This is **operational knowledge management** — a structured asynchronous research workflow that connects:

```
customer inquiry → quote line (unresolved) → vendor/dealer outreach → resolved part → cross-reference entry
```

Almost no small distributor ERP handles this well. Most systems assume part lookup succeeds instantly. Real life is asynchronous.

**New table: `research_items`**
```
id, ri_number (RI-2026-XXXX),
customer_id, quote_id (FK optional), quote_line_id (FK optional),
assigned_user_id,
status (researching | waiting_dealer | waiting_vendor | found | proven | closed),
urgency (normal | urgent | truck_down),   ← see open R-B
search_term, oem_number, vin, esn, engine_model,
notes,
callback_due_at, resolved_at,
resolved_product_id (FK optional),
resolution_notes,
created_at, updated_at
```

**New table: `research_activity_log`**
```
id, research_item_id,
activity_type (called_dealer | emailed_vendor | customer_replied | vendor_confirmed | found_online | other),
notes, logged_by, logged_at
```

**Modify `cross_references` (add status column):**
```
status TEXT DEFAULT 'researching'
-- 'researching' | 'found' | 'proven' | 'dealer_confirmed' | 'vendor_confirmed' | 'bad_cross' | 'obsolete'
```

**New service: `ResearchService`**
Responsibilities: create/update research items, generate request templates, log activity, resolve and optionally link to cross_references.

**New number sequence: `ri_counter`** → RI-2026-XXXX

**Quote line additions:**
- `research_status` — NULL (no research needed) | researching | waiting_dealer | waiting_vendor | found | proven
- `research_item_id` FK — links line to the research_items record

**Visual status colors:**

| Status | Color | Meaning |
|---|---|---|
| (none) | — | Normal resolved line |
| Researching | 🟡 Yellow | Being investigated |
| Waiting Dealer | 🔵 Blue | Awaiting dealer callback |
| Waiting Vendor | 🔵 Blue | Awaiting vendor callback |
| Found | 🟢 Green | Likely correct, not yet sale-proven |
| Proven | 🟢 Bold green | Confirmed through real sale/use |
| Bad Cross | 🔴 Red | Known incorrect — do not use |
| Obsolete | ⚫ Gray | Superseded/replaced |

**Dashboard: Research Queue widget (confirmed):**
```
Research Queue
  🟡  5   Researching
  🔵  8   Waiting Dealer
  🔵  3   Waiting Vendor
  ⚠️  2   Urgent / Truck Down
```

**One-click request templates:**
- **Dealer Request** — auto-fills: customer name, VIN, ESN, engine model, part description, callback info
- **Vendor Request** — auto-fills: part info, OEM number if known, urgency, callback info
- Delivery method: TBD — see open R-A (copy/paste text vs. system sends email)

**Long-term competitive value:**
Solved research items + proven cross-references accumulate → system becomes smarter than any individual employee → institutional knowledge that no competitor without this tracking can replicate.

---

## Session 4 — UI/UX & Design Interview
*Date: 2026-05-23*

### Section 1 — Daily Rhythm

**1.1 Morning routine:**
1. Open app → immediately check: missed follow-ups, overdue invoices, open quotes, cores due back, low stock / urgent backorders
2. Start taking inbound calls almost immediately
3. Day becomes: searching customer, searching part, checking availability, building quotes, checking vendor stock/ETA, converting quotes into orders

**1.2 Top 3 daily tasks (specific):**
1. Build a quote
2. Search Part / Create Part
3. Review Inventory

**1.3 Volume:**
7–10 customers per day (expected to grow). Majority result in a quote or quote conversion.

---

### Section 2 — The Phone Call Workflow

**2.1 Call comes in — sequence:**
Preferred: Search customer by company name or phone number → open/select customer → start quote → search part.
BUT: sometimes part research comes first (customer doesn't know the part number).
System MUST support both:
- Customer-first → quote → part search
- Part/ESN-first research → attach customer later

Long-term desired workflow: enter customer → enter ESN → engine manufacturer → generic description → program identifies OEM part → cross-references to available JAKS/vendor products.

**2.2 Out-of-stock parts:**
Quote screen must instantly show: QOH, vendor sources, preferred vendor, vendor cost, margin, expected vendor ETA, expected delivery date.
Zero stock MUST NOT block adding to quote.
Workflow: add out-of-stock item → select vendor source → system auto-calculates expected delivery → customer sees price + ETA.

**2.3 Unknown OEM part number (from customer):**
(Covered by Research Items workflow in QUOTING_REQUIREMENTS.md)

**2.4 "I'll think about it" — end of call:**
- Quote auto-saves
- Optional quick follow-up prompt appears
- One-click options: [Follow Up Tomorrow] [Callback in 3 Days] [Waiting Customer] [Waiting Vendor] [Truck Down] [No Follow Up]
- Quote status: Pending Customer OR Follow-Up Needed
- Dashboard widget "Quotes Requiring Follow-Up" surfaces these automatically
- CRITICAL: System must NOT rely on memory. Forgotten quotes = lost money.

**2.5 Customer calls back and says yes:**
1. Search customer or quote
2. Open quote instantly
3. Review pricing/availability quickly
4. Click: Convert to Sales Order
→ Small lightweight popup ONLY — asks: In stock? Source vendor? Create PO? Deposit? Expected ETA?
→ NO multiple confirmation screens. Speed is required.

---

### Section 3 — Navigation Mental Model

**3.1 Customer info from invoice:**
Click customer name directly from invoice → open customer record immediately. No back-button navigation. System feels interconnected.

**3.2 "What did I charge last time?":**
Must be visible inside the quote workflow — not a separate screen.
When product is added to a quote, show: last sold price to this customer, last sold date, previous margin, previous vendor/source.
Show in search results: "Last sold to Mike's Diesel: $1,245 on 03/14/2026 | PAI | 28% margin"

**3.3 Multiple browser tabs:**
Yes — common. Typical workflow: quote screen + Google + vendor websites + dealer systems + email/text. System must support this.

**3.4 Auto-save:**
Losing work is a major frustration today. Quote drafts MUST auto-save continuously. Navigation away from quote must never lose work. Quotes resumable after interruption.

---

### Section 4 — Speed & Keyboard

**4.1 Part search inputs (in order of how call comes in):**
OEM part number → partial OEM → description ("ISX inframe") → engine platform ("6NZ turbo") → ESN/VIN → internal SKU (repeat items).
Desired: Ctrl+K → type OEM/description → live results → arrow/auto-select → Enter → line added → qty defaults to 1 → Enter again → next line.
Search MUST support: fuzzy matching, partial numbers, cross-references, OEM/vendor/internal SKU, previous customer purchases, description keywords.

**4.2 Qty and price defaults:**
Most of the time: qty = 1, default pricing stands, preferred vendor pricing stands.
Tab only used for: adjusting qty, overriding price, changing vendor/source, special pricing.

**4.3 Save behavior:**
Auto-save continuously ("Saved 5 seconds ago" indicator) + explicit Ctrl+S + Save & Close button.
Both auto-save AND explicit save needed (confidence + workflow control).

**4.4 Keyboard shortcuts (will actually use):**
- Ctrl+K → global search
- Enter → accept/add line/advance workflow
- Tab → field navigation
- Ctrl+S → save quote
- Esc → close slide-overs/search panels
Later: F2 = quick part search, Ctrl+Enter = convert quote to SO.

---

### Section 5 — Interruptions & Edge Cases

**5.1 Mid-quote interruption (different customer calls):**
Quote auto-saves and remains accessible. Quote screen supports POP-OUT to a separate window.
Multiple quotes can be open simultaneously. Main app remains usable while quote window is open.
V1 priority: Quote pop-out. Future: SO pop-out, Invoice pop-out.

**5.2 Wrong customer on quote:**
Change customer directly on quote — preserve quote lines/notes/research.
Also: add ability to duplicate quotes.

**5.3 Forgot line after sending invoice:**
Warning: "Invoice already sent to customer." → user enters optional note/reason → save changes allowed. NOT a hard block.

**5.4 Log call from anywhere in app:**
Global "Quick Log Call" action available from any screen.
Opens right-side slide-over: search/select customer → type call notes → optionally link to quote/invoice/product → save → slide-over closes → user returns exactly where they were.

---

### Section 6 — Information on Screen

**6.1 Customer record — must-see at top (no scroll/click):**
Open balance, overdue balance, credit balance, payment terms, open quotes count, open SOs count, outstanding cores count, recent activity/last contact, last purchase date.
Answer must be instantly: "Can I sell to this customer right now? What is already open with them?"

**6.2 Margin per line:**
Yes — visible per line, but lower visual priority than availability/ETA/sell price.
Color coding: Green = healthy, Yellow = low margin, Red = below minimum.
Priority order on quote line: 1. Availability/source, 2. ETA, 3. Sell price, 4. Margin %, 5. Cost.
Must catch bad deals instantly (cost changed, price overridden, tight competitor, discount applied, core charges).

**6.3 "What did I charge last time?" speed:**
Must appear automatically in quote search results when product+customer combination is recognized. Two clicks acceptable ONLY if one click away. Preferred: appears in search dropdown automatically.

**6.4 Customer balance on quote screen:**
YES — customer status visible while building quote. Does NOT block quoting. Informs judgment.
Customer status mini-panel: Terms | Open Balance | Overdue | Credit Available | Cores Owed.
Warning colors: Overdue = orange/red, Credit available = green, Core overdue = orange.

---

### Section 7 — Multi-User

**7.1 Wife's workflow:**
Long-term: full operational access same as Keith. She should be able to: quote, SO, invoice, receive payments, view history, help with purchasing, review cores, push to QBO, manage bookkeeping.
Do NOT assume bookkeeping-only.

**7.2 Her access level:**
Full access: quotes, SOs, invoices, payments, customers, products, vendors, QBO.
Same owner-level access as Keith.

**7.3 Simultaneous use:**
Yes — both may be in system at same time.
Required: audit logs, last updated by/timestamp, soft warnings if same record being edited simultaneously.

**7.4 Her UI preference:**
Do NOT build a separate limited bookkeeping experience.
System allows customizable home/dashboard views.
Keith defaults to quote/workflow speed. She may default to invoices/QBO at first.
Both should use the same core system.

---

### Section 8 — Locked Design Decisions

**D1-A — Sales Orders in nav:** ✅ LOCKED — Own sidebar item. Sales Orders = active work queue.
**D1-B — Wife's access:** ✅ LOCKED — Full owner access. Same as Keith. No separate role.
**D1-C — Standalone SO list view:** ✅ LOCKED — Yes. It is a work queue.
**D1-D — Nav grouping:** ✅ LOCKED — Option B: SALES section (Customers, Quotes, Sales Orders, Invoices) + PURCHASING section (Vendors, Purchase Orders).
**D1-E — Reports:** ✅ LOCKED — Own sidebar section AND embedded in relevant screens.
**D11 — Cores sub-nav:** ✅ LOCKED — One Core Management workspace with tabs. See Core Workflow section below.
**D12 — Recently Viewed:** ✅ LOCKED — Yes. Last 5–10 records in sidebar or dashboard.

**Final nav structure:**
```
Dashboard
── SALES ──────────────────
  Customers
  Quotes
  Sales Orders
  Invoices
── PURCHASING ──────────────
  Vendors
  Purchase Orders
── INVENTORY ───────────────
  Products
── CORES ───────────────────
  Core Charges
── REPORTS ─────────────────
  Reports
── SYSTEM ──────────────────
  Settings
```

---

### Core Management — Full Workflow Decisions (Session 4)

**Core slip number:** CORE-2026-XXXX (new number sequence needed in settings)
**Vendor Core Return number:** VCR-2026-XXXX (new number sequence needed in settings)

**Core slip prompt:** After invoice with core items: optional popup "This invoice includes core items. [Print Core Return Slip] [Skip]"

**Receive a customer core — search by:** Core Slip # (preferred), Customer, Invoice #, Part #, Core tracking #, Phone #

**Inspection before credit (required):** Outcomes: Accepted, Hold for Review, Rejected, Damaged, Wrong Core, Partial Credit. No automatic credit without inspection.

**Customer credit method:** Default = Account Credit. Override options: Issue Check, Hold Pending Review, Reject / No Credit, Partial Credit.

**Core locations:** Separate from inventory. Examples: Core Shelf, Core Holding, Ready for PAI, Ready for HHP, Questionable Core, Rejected Core, Scrap Core.

**Vendor return shipment batch:** Core Management → Ready to Ship Vendor → select vendor → select cores → Create VCR batch → print vendor core return document → enter tracking/RMA.

**Vendor paperwork:** Do NOT show customer identity. Show: VCR#, JAKS core reference, Part#, Description, Qty, Expected credit, RMA#, Tracking#.

**Vendor acceptance outcomes:** Accepted, Rejected, Partial Credit, Disputed, Write Off, Charge Customer.
When credit differs from expected — record: expected amount, actual amount, difference, reason, resolution (Absorb / Charge Customer / Dispute / Write Off), notes.

**Core Management screen tabs:** All | Customer Cores | Ready to Inspect | Ready to Ship Vendor | Vendor Credits Pending | Problem Cores | Closed

**Core status cards at top:** Customer Owes Cores | Ready to Inspect | Ready to Credit | Ready to Ship Vendor | Waiting Vendor Credit | Problem Cores

**Core chain (internal, never shown on vendor docs):** Customer → Invoice → Core Slip → Vendor Core Return → Closed

**Design principle:** Core Management should guide the user through a complex lifecycle without requiring them to understand the accounting. Screen guides: Sold with core → customer returned it → inspect → credit customer → ship to vendor → reconcile vendor credit → close.

---

## Session 5 — Final Open Questions Resolved
*Date: 2026-05-23*

**D9 — Vendor bill approval:**
Auto-approve when PO / receipt / vendor bill all match exactly.
Any discrepancy (qty, cost, or line mismatch) → hold for manual review.

**R-A — Research template delivery:**
Copy/paste text output only for Phase 1.
System sending email or text is Phase 2.

**R-C — Cross-reference on research resolution:**
Always prompt user before creating a cross-reference entry.
Never auto-create silently — user must confirm.

**R-D — Research Queue dashboard widget:**
Own dedicated widget, separate from Quotes Requiring Follow-Up.
Two distinct widgets on the dashboard.

**Status: ALL open questions resolved. No blocking unknowns remain for Phase 1.**

---

## Session 6 — Suggested Sells, Warranty Upsell, and Option Groups
*Date: 2026-05-23*

---

### Section 13 — Suggested Sells & Warranty Upsell

**Overview: Two distinct systems confirmed by Keith**

Both are needed and serve different purposes:

---

**System 1 — Optional Lines (`is_optional` flag)**

A single flag on quote lines that marks an item as optional — not committed, not required, just offered.

Use cases:
- Warranties (JAKS extended warranty upsell)
- Add-ons and accessories
- Optional labor
- Expedited freight
- Install kits

Behavior: optional lines appear inline in the quote line table — they are NOT grouped or color-coded. They are just regular lines the customer can accept or decline. No visual grouping needed.

---

**System 2 — Option Groups (Economy / Recommended / Premium)**

A separate system for repair strategy selection. The customer chooses ONE repair strategy.

Examples:
- Economy vs. Recommended vs. Premium
- OEM vs. Aftermarket
- Budget rebuild vs. full inframe

Behavior: lines are assigned to a color-coded section (Economy = blue, Recommended = green, Premium = gold). The sections appear as distinct visual blocks in the lines table. Customer picks one strategy; the others become reference/declined lines.

---

**Suggested Sells — Phase 1 FIRST priority (confirmed by Keith)**

Keith confirmed suggested sells are the first thing to build in the quoting workflow.

**How suggestions are configured:**
- Per-product config table: `suggested_sells` — defines what to suggest per product
- Free-add: any SKU can also be searched and added manually as a suggestion

**UX behavior (confirmed):**
- Inline chips appear below each quote line showing related items
- NO auto-popup — suggestions never interrupt the quoting workflow automatically
- Manual "View Related" button per line → opens a slide-over panel showing the full suggested sells list
- Exception: high-value bundles (cylinder heads, inframes, turbos, overhaul kits) → slide-over opens automatically when the item is added to the quote

**Chip types:**

| Chip Type | Where It Appears | Behavior |
|---|---|---|
| `recommended` | Inline chip below line | Click to add to quote |
| `required` | Inline chip below line | Pre-checked in slide-over; strongly surfaced |
| `optional` | Slide-over only | Not shown inline |
| `warranty` | Inline chip below line | Opens inline tier picker (not slide-over) |

**Product configuration:**
- Product detail screen gets a "Suggested Sells" tab
- Lets Keith configure which products/chips are suggested when this product is added to a quote
- Configuration: chip type, product/SKU, display label

---

**Warranty — Both JAKS and Vendor warranty tracked**

Two distinct warranty types exist:

**Vendor/Supplier Warranty (no charge):**
- Included at no cost — it's what the vendor covers
- Tracked on the product record: `supplier_warranty_months`, `supplier_warranty_type`
- Example: PAI may offer a 2-year P&L warranty on certain parts

**JAKS Extended Warranty (paid upsell):**
- Keith adds a warranty extension on top of the vendor warranty
- Example: PAI covers 2 years P&L → Keith sells a 1-year extension on top of that
- Appears as a separate line item on the quote
- Priced using the formula: `unit_price × warranty_pct% × (months ÷ 12)`
- `warranty_pct` is a percentage default set on the product — manually overridable per quote line

**Warranty tiers (confirmed):**

| Tier | Coverage | Label |
|---|---|---|
| 6mo P-Only | Standard | 6-Month Parts Only |
| 12mo P-Only | Parts only, 1 year | 12-Month Parts Only |
| 12mo P&L | Parts & labor, 1 year | 12-Month Parts & Labor |
| 24mo P&L | Parts & labor, 2 years | 24-Month Parts & Labor |
| 36mo P&L | Parts & labor, 3 years | 36-Month Parts & Labor |

**Warranty chip → inline tier picker:**
- Clicking the warranty chip on a quote line opens an inline tier picker (not a slide-over)
- User selects the tier → warranty line is added to the quote at the calculated price
- Price is editable after selection

---

**Option Groups (System 2) — Build order: after suggested sells and warranty**

- Lines in the quote are assigned to a group via a dropdown per line
- Groups: Economy (blue), Recommended (green), Premium (gold)
- Groups are displayed as distinct color-coded sections in the lines table
- Customer picks one section; the others are declined/reference

**Phase build order confirmed by Keith:**
1. Suggested sells (first)
2. Warranty upsell
3. Option groups

---

## Session 7 — PDF, Payments, and Priority Alignment
*Date: 2026-05-24*

**Q: Payments priority — counter vs. delayed?**
Real mix of both. Counter payments (cash/check/card at pickup, same day as invoice) AND delayed
(net-30 checks, QBO payment link clicks). System must handle both flexibly. Build it
with no assumptions about when payment arrives relative to invoice date.

**Q: Which PDF document is needed first?**
Quote/Proposal first — that's what helps close the sale.
Invoice second — needed to collect payment.
Purchase Order third — less urgent, vendors understand emailed PDFs less formally.

**Q: Database state?**
All test data. Drop and recreate is fine for any schema changes. Will be told explicitly if
real data has been entered before a session.

**Q: Customer balance panel on quote screen — urgency?**
Important (Terms / Open Balance / Overdue / Credit / Cores Owed), but NOT the main
blocker. Can check customer record separately for now. Do not delay PDFs or payments
to build this.

**Q: Upgrade options on printed quote PDF?**
Show upgrade options in a dedicated "Alternatives / Upgrade Options" section on the quote PDF.
Main quote body = clean with only the active/included lines.
Customer should still see the better/good/best options clearly, but they appear below the main
line items as a distinct reference section — not as billed charges.

**Q: Research status on quote lines — urgency?**
Useful, especially for dealer/vendor research. But not the first blocker. Build after
payments and PDFs are solid.

**Q: What is actually blocking daily use today?**
"The biggest blocker is not being able to produce/send professional quote PDFs and then
smoothly convert that into invoice/payment workflow. Focus on Quote PDF first, then
invoice/payment flow, then polish the customer balance panel and research statuses."

---

### Priority Stack (locked 2026-05-24)

Ordered by Keith's explicit answer:

1. **Quote PDF** — professional quote/proposal document, print/email ready
2. **Invoice PDF** — professional invoice with totals, terms, balance due
3. **Payment workflow** — flexible (counter + delayed), record against invoice, allocations
4. **Customer balance mini-panel on quote screen** — Terms, Balance, Overdue, Credit, Cores
5. **Research status on quote lines** — visual flag, RI link, color-coded status
6. **Purchase Order PDF** — after core sell-side workflow is solid

---

## Sections Status (updated)

| Section | Status |
|---|---|
| 1 — First Impressions & Navigation | ✅ Complete |
| 2 — Invoices & Quotes | ✅ Complete |
| 3 — Payments & Money | ✅ Complete |
| 4 — Products & Inventory | ✅ Complete |
| 5 — Cores Deep Dive | ✅ Complete |
| 6 — Purchase Orders & Vendors | ✅ Complete |
| 7 — Customers & CRM | ✅ Complete |
| 8 — Reports & Visibility | ✅ Q31 answered; Q32–Q33 deferred |
| 9 — Integrations | Deferred (decisions locked in PLAN.md) |
| 10 — Mobile & Access | Deferred (decisions locked in PLAN.md) |
| 11 — Build Priority | ✅ Complete |
| 12 — Parts Research & Callbacks | ✅ Complete |
| 13 — Suggested Sells & Warranty Upsell | ✅ Complete |
| 14 — PDF Priority & Payment Workflow | ✅ Complete (Session 7) |
