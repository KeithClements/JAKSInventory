# JAKS Inventory — Quoting & Search Requirements
*Compiled from owner interview — 2026-05-22*
*Status: ACTIVE — Review before touching any quote, search, product, or CRM screen*

---

## ⚠️ Critical Architectural Directive

> **Read this before writing a single line of quote or search code.**

The quoting system is NOT a basic invoice form.
It is a **live operational console** used while a customer is on the phone.

All business logic must live in a **service layer** (`/services`), not in UI forms, buttons, or screen components. The UI calls services. Services own the logic. This makes it possible to wire ESN lookup, vendor APIs, and cross-reference engines later without rebuilding screens.

---

## 1. Global Search Architecture

### Real-World Trigger
When a customer calls, the first thing Keith types is usually a **company name**.
But the system must be able to find anything from one search bar.

### Search Must Cover ALL of the Following
| Input Type | Examples |
|---|---|
| Company name | "Mike's Diesel", "Mike" |
| Phone number | "402-669-1234", "4026691" |
| Address | "123 Main" |
| Part number / SKU | "311148", "PAI-311148" |
| OEM number | "3803748", "C3803748" |
| Vendor SKU | "ISX141386" |
| Cross-reference | Any alternate PN that maps to a stocked product |
| ESN | "79485732" (Engine Serial Number) |
| VIN | Vehicle identification number |
| Existing quote number | "Q-2026-0042" |
| Existing invoice number | "INV-2026-0099" |
| Quote/invoice history | "who bought a 311148 last month" (future) |

### Search Behavior Requirements
- **Partial text matching** — "311" finds "311148"
- **Numeric substring matching** — "4026691" finds phone "402-669-1234"
- **Cross-reference lookups** — typing an OEM number surfaces the PAI equivalent
- **Keyboard-only workflow** — results appear as-you-type, no mouse required
- **Single search bar** — not separate searches per module
- **Context-aware results** — grouped by type (Customer, Product, Invoice, Quote)

### Example
Typing `4026691` should instantly return:
- ✅ OEM numbers matching that string
- ✅ Vendor SKUs matching that string
- ✅ Product cross-references containing that string
- ✅ Existing quotes/invoices referencing that part
- ✅ Inventory records matching that string

### Implementation Notes
- Build a `SearchService` that queries all entity types in one call
- Index cross-references in a dedicated `cross_references` table (product_id, cross_ref_number, source)
- Global search bar lives in the top header — accessible from any screen
- Results dropdown appears inline (HTMX or Alpine.js), never a full page reload
- Keyboard shortcut to focus search (e.g., `/` or `Ctrl+K`)

---

## 2. Quote Building Workflow

### Reality
Quotes are built **live, on the phone**, while researching parts simultaneously.
This is not a retail checkout. The customer is waiting.

### The System Must Support
- Rapid part lookup while talking
- Cross-referencing on the fly (customer gives OEM, Keith finds the PAI equiv)
- Vendor comparison during the call
- Real-time inventory visibility inline
- Non-stocked / vendor-sourced items (see Section 3)
- Multiple line items built quickly without mouse

### Quote Screen Must Feel Like
- A **keyboard-driven operations console**
- Not a web form

---

## 3. Quoting Non-Stocked Items

### Frequency: Very Common

This is not an edge case. Keith frequently quotes parts he does not have in stock.

### The System Must Support
- Adding any part to a quote regardless of inventory level
- Sourcing from vendor (PAI, HHP, ATL) live during quoting
- Displaying vendor availability and ETA inline on the quote line
- Noting "vendor-sourced" vs "in-stock" per line
- Inventory quantity of zero must NOT block a quote line from being added

### Implementation
- Each quote line has a `source` field: `stock | pai | hhp | atl | other`
- Each quote line has an `eta` field (optional, free-form or date)
- Quote line shows availability badge inline (In Stock / Ships from PAI / ETA 2 days)

---

## 4. Quote Screen — Visible Information Priority (Ranked)

> These are ranked by operational importance during a live customer call.
> The UI layout must reflect this priority — most important = most prominent.

| Rank | Field | Notes |
|---|---|---|
| 1 | **Availability** | In stock qty + vendor availability |
| 2 | **Vendor ETA** | When can we get it if not in stock |
| 3 | **Competitor Pricing** | HHP/ATL market price for live negotiation |
| 4 | **Final Sell Price** | Always visible, always editable |
| 5 | **Margin %** | Important internally, lower visual priority |

### Per-Line Quote Row Must Show
- [ ] QOH (Quantity On Hand) — always visible
- [ ] Vendor availability (PAI, HHP, ATL) — pulled inline
- [ ] ETA from vendor if not in stock
- [ ] Competitor/market price (HHP scraped price)
- [ ] Our sell price (editable)
- [ ] Margin % (calculated live)
- [ ] Source indicator (stock / PAI / HHP / ATL)
- [ ] Alternate vendor options (collapsed, expandable)

### Last Sold Price
Must be instantly visible when a product is added to a quote:
- "Last sold to this customer at $X on [date]"
- "Last sold to anyone at $X on [date]"
This prevents inconsistent pricing and supports live negotiation.

---

## 5. Sales Type — Kits, Bundles, Packages

### Reality
Customers frequently buy **entire jobs**, not single parts.

### Common Package Types
- Inframe kits (engine rebuild — many components)
- Cylinder head jobs (head + gaskets + hardware)
- Turbo replacement packages (turbo + gaskets + hardware)

### System Must Support
- Kit/bundle SKUs that expand to multiple line items
- Suggested upsells / related products per item
- Package quoting (add a kit, lines auto-populate)
- Individual components also sold separately

### Implementation Notes
- `product_kits` table: kit_product_id → [component_product_id, qty]
- When a kit is added to a quote, expand to component lines automatically
- User can remove/modify individual components
- "Suggest related items" on quote line (e.g., add gasket set when adding turbo)

---

## 6. Brand Substitution

### Current Reality
Brand substitution is **uncommon**. Primary vendor is **PAI**.

### Exceptions
- Private-labeled cylinder heads from alternate vendors
- Occasional HHP or ATL sourcing when PAI is unavailable

### System Should Support
- Preferred/default vendor per product
- Alternate vendor relationships (same part, different vendor)
- Private-label product mapping (vendor X part = our part Y)
- **Should NOT** assume constant vendor substitution

---

## 7. Biggest Causes of Lost Sales (Ranked)

> Understanding these drives the build priority.

| Rank | Cause | System Response |
|---|---|---|
| 1 | **No inventory** | Instant vendor availability on every quote line |
| 2 | **Pricing** | Competitor pricing visible during quoting |
| 3 | **Slow quoting** | Keyboard-first, 45-second quote target |
| 4 | **Shipping delays** | ETA visible inline |
| 5 | **Lack of follow-up** | CRM follow-up queue (Phase 2) |
| 6 | **Forgot to call back** | Quote aging alerts + callback reminders |

---

## 8. Critical Information Visible During Every Quote

These must ALWAYS be visible inline — not hidden behind a click or modal:

| Field | Source | Visibility |
|---|---|---|
| QOH | Local inventory | Always shown on quote line |
| Last sold price | Invoice history | Shown when product is added |
| Vendor availability | PAI/HHP/ATL API or scraper | Pulled per line |
| Competitor pricing | HHP scraped price | Shown per line |
| Margin % | Calculated: (sell - cost) / sell | Shown per line, live |
| ETA | Vendor data | Shown per line when out of stock |

---

## 9. Quote Follow-Up — LOCKED (Session 4)

### The Problem
Forgotten quotes = lost money. The system must NOT rely on human memory.

### Quick Follow-Up Bar (LOCKED)
Appears at the bottom of every quote. One-click actions — no extra screen:

```
[ Follow Up Tomorrow ]  [ Waiting Customer ]  [ Waiting Vendor ]  [ Truck Down ]  [ No Follow Up ]
```

Clicking any option:
1. Sets follow_up_date on the quote
2. Sets follow_up_status (pending_customer | pending_vendor | truck_down | no_follow_up)
3. Optionally opens a 2-line notes field for: customer concern, competitive pricing note, callback time, urgency
4. Auto-saves quote

Before closing the quote, the user can also add context WITHOUT leaving the quote:
- Customer concern / objection
- Competitive pricing issue
- Waiting for customer approval
- Truck down urgency flag
- Requested callback date
- Special sourcing notes

### Dashboard Widget (LOCKED)
"Quotes Requiring Follow-Up" widget shows all quotes with follow_up_date ≤ today and outcome = pending.
Sorted by urgency: Truck Down first, then oldest first.

### Quote Statuses (add)
- `pending_customer` — waiting for customer to decide
- `pending_vendor` — waiting for vendor information
- `follow_up_needed` — generic follow-up flag
- Existing: draft, sent, converted, declined

### Implementation
- Follow-up bar is always visible on quote screen (Phase 1)
- `quote.follow_up_date`, `quote.follow_up_status`, `quote.follow_up_notes` fields
- Dashboard widget reads these fields (Phase 1)
- Full `quote_followups` table for multi-follow-up history (Phase 2)

---

## 10. The Perfect Quote Screen — Owner's Words

> "I never touch the mouse."
> "I can build a quote in 45 seconds."
> "I can see all vendor options instantly."
> "I can pull cross references quickly."

### Non-Negotiable UX Requirements
- **Keyboard-first** — Tab through every field, Enter to confirm, `/` to search
- **Speed** — Adding a line item: type 3 chars of SKU → arrow down → Enter = done
- **Instant vendor visibility** — Availability loads inline without clicking anything
- **Fast cross-referencing** — Typing OEM number finds PAI equivalent immediately
- **No page reloads** — All quote interactions are in-page (HTMX)

---

## 11. ESN-Based Part Lookup — Major Future Goal

> This feature does not exist yet but **must influence the architecture now**.
> Do not build walls that block this later.

### Desired Workflow
1. Customer provides ESN (e.g., `79485732`)
2. User enters ESN into system
3. User types generic term: `turbo`
4. System:
   - Queries engine lookup source (e.g., parts.cummins.com or equivalent)
   - Identifies the correct OEM part number for that engine + component
   - Cross-references OEM PN to stocked/vendor products
   - Returns: OEM PN, PAI equivalent, inventory status, vendor availability, pricing, ETA

### Architectural Requirements (Must Build For Now)
- `ESNLookupService` — interface defined now, implementation deferred
- `CrossReferenceService` — already needed; ESN lookup will feed into it
- Do NOT hardcode engine/part logic inside UI screens
- External lookup services plugged in via service layer, not UI layer
- Database must support: `esn_lookups`, `engine_configs`, `oem_part_numbers` tables (scaffold now, populate later)

---

## 12. Service Layer Architecture

### Directive
Business logic lives in `/services`. The UI is a thin shell that calls services.

### Required Services

| Service | Responsibility |
|---|---|
| `SearchService` | Global search across all entities |
| `ProductService` | Product lookup, availability, cross-refs |
| `PricingService` | Markup calc, margin calc, last-sold price, competitor price |
| `QuoteService` | Quote creation, line management, conversion to invoice |
| `CoreService` | Core charge lifecycle (buy → sell → return → credit) |
| `VendorAvailabilityService` | Query PAI/HHP/ATL for stock and ETA |
| `CrossReferenceService` | OEM ↔ vendor ↔ internal SKU mapping |
| `ESNLookupService` | *(future)* ESN → engine config → OEM parts |
| `InvoiceService` | Invoice creation, payment recording, QBO push |
| `CRMService` | Call logs, follow-up tasks, quote aging |

### File Structure Target
```
rebuild/
├── app/
│   ├── services/
│   │   ├── __init__.py
│   │   ├── search.py          ← SearchService
│   │   ├── product.py         ← ProductService
│   │   ├── pricing.py         ← PricingService
│   │   ├── quote.py           ← QuoteService
│   │   ├── invoice.py         ← InvoiceService
│   │   ├── core.py            ← CoreService
│   │   ├── vendor_avail.py    ← VendorAvailabilityService
│   │   ├── cross_ref.py       ← CrossReferenceService
│   │   ├── crm.py             ← CRMService
│   │   └── esn_lookup.py      ← ESNLookupService (stub)
│   ├── models/
│   ├── routers/
│   └── templates/
```

### Rule
If a router has more than ~10 lines of logic, that logic belongs in a service.
Routers: receive request → call service → return template response.

---

## 13. Database Tables to Add / Scaffold

The following tables are needed to support this architecture.
Some are needed now. Some are scaffolded now, used later.

| Table | Needed When | Purpose |
|---|---|---|
| `cross_references` | Now | OEM/vendor PN ↔ product mapping |
| `quote_line_availability` | Now | Vendor availability snapshot per quote line |
| `product_kits` | Soon | Kit → component relationships |
| `quote_followups` | Phase 2 | CRM follow-up tasks per quote |
| `price_history` | Now | Last-sold price per product per customer |
| `esn_lookups` | Future | ESN → engine config cache |
| `engine_configs` | Future | Engine model → OEM part relationships |
| `competitor_prices` | Soon | Scraped HHP/ATL prices per product |

---

## 14. What This Means for the Current Build

### Immediate Changes Needed
1. **Global search bar** in the header — single input, searches everything
2. **`cross_references` table** — add to schema now, populate from scrapers
3. **`/services` directory** — create now; migrate existing router logic in
4. **Quote line** — add `source`, `eta`, `vendor_availability`, `margin_pct`, `last_sold_price` fields
5. **`price_history` table** — record sell price per product per customer on every invoice save

### Do Not Build Yet (But Do Not Block)
- ESN lookup UI (architecture only)
- Full vendor availability API calls (stub the service, wire UI hooks)
- Kit/bundle UI (add `product_kits` table but no UI yet)
- Follow-up CRM (table + service stub only)

---

## 15. Parts Research Management

### Reality
Part lookup frequently fails on the first attempt — the customer is waiting, or will call back.
Research begins: dealer callbacks, vendor outreach, Google searches, competitor sites.
Today this is fragmented across browser tabs, text messages, handwritten notes, and emails — nothing linked to the customer, quote, or part that triggered it.

### This Is NOT "Notes"
Research items are **operational** — not passive. They are:
- Actionable (someone must resolve them)
- Assigned (a specific user owns each one)
- Blocking revenue (no resolved research = no quote price = no sale)
- Time-sensitive (customer is waiting, or truck is down)
- Self-documenting (resolved items become permanent institutional knowledge)

**Name them:** Research Items / Pending Research — not notes.

### Research Lives Inside the Quote (Confirmed)
Unresolved lines stay on the quote. The quote line status cell is visually highlighted until the research status is resolved. Quote is the operational context — research does not spin off into a separate task list.

Each unresolved line generates a linked `research_items` record (RI-2026-XXXX).

### Quote Line Research States

| Status | Color | Meaning |
|---|---|---|
| (none) | — | Normal line — no research needed |
| Researching | 🟡 Yellow | Part actively being investigated |
| Waiting Dealer | 🔵 Blue | Awaiting dealer VIN/part callback |
| Waiting Vendor | 🔵 Blue | Awaiting vendor pricing/availability |
| Found | 🟢 Green | Likely correct — not yet proven in a real sale |
| Proven | 🟢 Bold | Confirmed correct through a real sale/use |
| Bad Cross | 🔴 Red | Known incorrect — do not use |
| Obsolete | ⚫ Gray | Superseded or replaced |

### Cross-Reference Confidence — Full Status Set

Cross-references now carry a `status` field. This is the confidence rating Keith described:

| Status | Meaning |
|---|---|
| Researching | Still investigating this cross |
| Found | Likely correct (Google / competitor site) |
| Proven | Confirmed in a real JAKS sale — high confidence |
| Dealer Confirmed | OEM/dealer VIN lookup verified this cross |
| Vendor Confirmed | PAI / HHP / ATL confirmed this cross |
| Bad Cross | Known incorrect — never use |
| Obsolete | Superseded by a newer part number |

Over time, the cross-reference table becomes institutional intelligence. Proven and Dealer Confirmed crosses are the most valuable — they are grounded in real operational resolutions.

### VIN-to-Part-Number Dealer Lookup Workflow

1. Customer doesn't know part number (e.g., airbag)
2. Customer provides VIN (last 8 digits usually, sometimes full VIN)
3. Keith calls the appropriate dealership
4. Dealer crosses VIN → provides OEM part number + dealer price
5. System generates a **Dealer Request template** pre-filled from the quote/customer record:
   - Customer name, VIN, ESN, engine model, part description, callback phone
6. Resolution: OEM number logged in `research_items.oem_number` → optional cross-ref entry

### One-Click Request Templates

**Dealer Request** — for VIN/part lookups:
Auto-fills: customer name, VIN, ESN, engine model, requested part description, callback info.

**Vendor Request** — for sourcing and pricing:
Auto-fills: part info, OEM number if known, urgency, callback info.

Delivery: generates formatted text for copy/paste into email.
*(System sending email directly: Phase 2. Phase 1 = copy/paste only. ✅ LOCKED R-A)*

### Research Activity Timeline

Each research item logs a running timeline:
- `called_dealer`
- `emailed_vendor`
- `customer_replied`
- `vendor_confirmed`
- `found_online`
- `other` (free text)

This becomes part of the permanent record and is available for review by anyone in the hierarchy.

### Callback Tracking Priority (Confirmed)

**Research callbacks > quote follow-up calls** in priority.

Reason: a research callback is pre-quote. The customer has no price yet. Without resolving the research, there is no sale to follow up on. Quote follow-up assumes a completed quote was already delivered.

Assignment rule: creator of the research item = assigned owner. Higher-hierarchy users can view and manage all open research items across users.

### Auto Cross-Reference on Resolution

When a research item is resolved and a product/OEM is identified:
- System **prompts** the user: "Add cross-reference for [OEM#] → [Product]?" with Accept / Skip
- Status is set based on resolution source: `proven` (real sale) / `dealer_confirmed` / `vendor_confirmed`
- This entry powers future searches — the system already knows the answer next time
- ✅ LOCKED R-C: Never auto-create silently. Always confirm-prompt first.

### Dashboard: Research Queue Widget (Confirmed)

```
Research Queue (open)
  🟡  5   Researching
  🔵  8   Waiting Dealer
  🔵  3   Waiting Vendor
  ⚠️  2   Urgent / Truck Down
```

Own dedicated widget — separate from Quotes Requiring Follow-Up.
✅ LOCKED R-D: Research Queue and Quote Follow-Up are two distinct widgets on the dashboard.

### New Required Service

Add to service layer: `ResearchService`
- Create / update research items
- Log activity entries
- Generate dealer and vendor request templates
- Resolve research → optionally create cross-reference entry

### New Required Tables

See PHASE_1_PLAN.md Step 0 for full schema. Summary:
- `research_items` — one record per unresolved part inquiry
- `research_activity_log` — running timeline of actions per research item
- `cross_references.status` — add confidence status column to existing planned table

### New Number Sequence

`ri_counter` → RI-2026-XXXX (reference number for emails, callbacks, filing)

### R-Series Decisions (All Locked)

| # | Decision | Locked Value |
|---|---|---|
| R-A | Template delivery | ✅ Copy/paste text output only. System email is Phase 2. |
| R-B | Urgency flag | ✅ Formal tier: Normal / Urgent / Truck Down |
| R-C | Cross-ref on resolution | ✅ Confirm-prompt before creating. Never auto-create silently. |
| R-D | Research Queue widget | ✅ Own separate dashboard widget. Not merged with quote follow-ups. |

---

*This document is the source of truth for quoting and search architecture.*
*Update it as new requirements emerge. Do not override with assumptions.*

---

## 16. Quote Pop-Out Window (LOCKED — Session 4)

### The Problem
The business is interruption-heavy. A new customer call comes in mid-quote.
The user needs to start Quote B without losing Quote A.

### Required Behavior
- Any quote can be "popped out" into a separate browser window
- Pop-out window contains the full quote builder — no functionality lost
- Multiple quotes can be open in separate windows simultaneously
- Main app window remains fully usable (navigation, product lookup, customer search, etc.)
- All pop-out quotes auto-save continuously
- Closing a pop-out window does NOT lose work — draft saved to DB

### Implementation
- "Pop Out" button on every quote (opens `window.open('/quotes/{id}/popup', '_blank', 'width=1200,height=800')`)
- Popup route returns a stripped layout (no sidebar) with full quote builder
- HTMX/Alpine.js interactions work identically in popup
- Quote auto-save fires every 10 seconds via HTMX polling or Alpine.js watch

### V1 Priority
Quote pop-out is V1 (Phase 1).
Sales Order pop-out and Invoice pop-out are Phase 2.

---

## 17. Quote Auto-Save (LOCKED — Session 4)

### Required Behavior
- Quote drafts auto-save continuously while being built
- User NEVER loses work from navigating away, closing a tab, or interruption
- Save indicator always visible: "Saved 5 seconds ago" / "Saving..." / "Save failed — retry"
- Explicit Ctrl+S also supported
- "Save & Close" button available

### States
- `auto-saving` — spinning indicator
- `saved` — green checkmark + "Saved X seconds ago"
- `error` — red indicator + "Save failed. Retry?" with retry button
- `draft` — quote has never been explicitly saved (very brief window on creation)

---

## 18. Dual Workflow — Customer-First vs. Part-First (LOCKED — Session 4)

### The Problem
The system must NOT force customer selection before part research begins.

### Two Supported Entry Points

**Customer-First (most common):**
1. Search customer (Ctrl+K or search bar)
2. Select customer → opens new quote with customer set
3. Search parts and add lines
4. Send quote

**Part-First / Research-First (common when customer doesn't know part number):**
1. Open new blank quote (no customer yet)
2. Research parts using ESN / engine model / OEM# / description
3. Add lines, get pricing, check availability
4. Attach customer before sending

The quote screen must show both workflows without friction.
Customer field on quote must be: optional during research, required before sending.
"Customer not set" reminder appears when user clicks Send — not before.

---

## 19. Last-Sold Price in Search Results (LOCKED — Session 4)

When a product is searched and added to a quote, the search dropdown must show:
```
JAKS-PAI-311148 — Inframe Kit ISX
QOH: 2  PAI: In Stock  ETA: —

Last sold to Mike's Diesel: $1,245.00 on 03/14/2026 | PAI | 28% margin
```

When no previous sale to this customer exists:
```
Last sold (any customer): $1,190.00 on 04/02/2026 | PAI | 24% margin
```

This eliminates the need to open a separate screen to check pricing history.
PricingService must query price_history for the (product_id, customer_id) pair on every product add.

---

## 20. Quote Duplication (LOCKED — Session 4)

### Use Case
User built a quote for the wrong customer, or wants to reuse a quote as a starting point.

### Required Behavior
- "Duplicate Quote" action on every quote
- Creates a new DRAFT quote with:
  - All the same lines (product, qty, price, discount)
  - Blank customer (or optionally carry over the same customer)
  - New quote number
  - No follow-up date, no outcome, no sent status
- Original quote is not modified

### Change Customer on Existing Quote
- Customer field is editable on any DRAFT or SENT quote
- Changing customer: preserves all lines, notes, and pricing
- Warning if previous customer had a discount % that differs from new customer's default

---

## 21. Customer Status Panel on Quote Screen (LOCKED — Session 4)

A compact, always-visible status panel showing the selected customer's account health.
Positioned in the quote header area. Does NOT block quoting.

```
┌─────────────────────────────────────────────────────┐
│ Mike's Diesel Service          Terms: Net 30         │
│ Open: $2,850  ⚠ Overdue: $600  ✓ Credit: $150       │
│ Cores Owed: 1                                        │
└─────────────────────────────────────────────────────┘
```

Color coding:
- Overdue balance > 0 → orange/red badge
- Credit available > 0 → green badge
- Cores owed > 0 → orange badge

Does NOT auto-block quoting. Informs judgment only.
If customer is severely overdue, show a more prominent alert — but still allow quote.

---

## 22. Suggested Sells & Warranty Upsell (LOCKED — Session 6)

### Two Distinct Systems

These are separate, independently built features that can be layered:

**System 1 — Optional Lines**
A boolean `is_optional` flag on quote lines. Marks a line as optional (not committed). No visual grouping. Inline in the regular lines table.

Use cases: warranties, add-ons, accessories, optional labor, expedited freight, install kits.

**System 2 — Option Groups**
Color-coded section headers in the lines table. Each line is assigned to a group via a dropdown. The customer picks ONE repair strategy; the others are reference/declined.

Groups: Economy (blue), Recommended (green), Premium (gold). Assignment is per line, not per quote.

---

### Suggestion Chips — Inline, Keyboard-Accessible, No Auto-Popup

Suggestion chips appear below each quote line in a compact chip row.

**Rules:**
- Chips are NEVER auto-displayed as a popup or modal
- Chips are inline — they appear as part of the quote line row, below the main line data
- Chips are keyboard-accessible (Tab to focus chip row, Enter or Space to activate)
- A manual "View Related" button per line opens a slide-over showing the full suggested sells list
- Exception: High-value bundles (cylinder heads, inframes, turbos, overhaul kits) → the slide-over opens automatically when the item is added to the quote

**Chip types and behaviors:**

| Chip Type | Display Location | Behavior When Clicked |
|---|---|---|
| `recommended` | Inline chip below quote line | Adds item to quote as a new line |
| `required` | Inline chip below quote line | Opens slide-over with item pre-checked |
| `optional` | Slide-over panel only (not inline) | User adds from slide-over |
| `warranty` | Inline chip below quote line | Opens inline tier picker (see below) |

**Chip source:**
- Per-product config table (`suggested_sells`) — defines suggestions for each product
- Free-add: any SKU can also be manually searched and added as a suggestion

---

### Warranty Tier Picker — Inline, Triggered by Warranty Chip

When the warranty chip is clicked on a quote line:
- An inline tier picker expands below the chip row (NOT a slide-over)
- User selects a tier → warranty line is added to the quote
- Warranty line price is calculated automatically, then manually overridable

**Pricing formula:**
```
warranty_price = unit_price × warranty_pct% × (months ÷ 12)
```
- `warranty_pct` is set on the product record (default percentage)
- Price is editable per quote line after calculation

**Warranty tiers:**

| Label | Coverage | Months |
|---|---|---|
| 6-Month Parts Only | Parts only | 6 |
| 12-Month Parts Only | Parts only | 12 |
| 12-Month Parts & Labor | Parts and labor | 12 |
| 24-Month Parts & Labor | Parts and labor | 24 |
| 36-Month Parts & Labor | Parts and labor | 36 |

---

### Vendor vs. JAKS Warranty Distinction

**Vendor/Supplier Warranty** — tracked on the product record, no charge to customer:
- Fields: `supplier_warranty_months`, `supplier_warranty_type`
- This is what the vendor covers (e.g., PAI 2-year P&L)
- Informational only — shown on the product detail screen, not as a quote line

**JAKS Extended Warranty** — paid upsell, appears as a quote line:
- Extends on top of vendor warranty
- Example: PAI covers 2 years → Keith sells a 1-year JAKS extension on top
- Priced via formula above
- Added via warranty chip → tier picker on the quote

---

### Product Config: Suggested Sells Tab

The product detail screen includes a "Suggested Sells" tab:
- Lists all products/SKUs suggested when this product is added to a quote
- Each entry has: suggested product, chip type (recommended / required / optional / warranty), display label
- Keith can add, edit, or remove entries directly from this tab

---

### Option Groups — Color-Coded Sections in Lines Table

- Lines are assigned to a group via a per-line dropdown ("Group" column)
- Available groups: Economy, Recommended, Premium (or OEM vs. Aftermarket for substitution scenarios)
- The lines table renders each group as a visually distinct section with a colored header bar
  - Economy = blue header
  - Recommended = green header
  - Premium = gold header
- Lines not assigned to any group appear above the grouped sections (ungrouped / always-included lines)
- Customer picks one section — the other sections are declined or marked as reference

**Build order (confirmed):**
1. Suggested sells — first
2. Warranty upsell — second
3. Option groups — third
