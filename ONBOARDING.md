# JAKS Inventory — Project Onboarding

**Owner:** Keith (JAKS diesel parts dealership, Omaha NE)
**Stack:** FastAPI + HTMX + Alpine.js + Tailwind CSS + SQLAlchemy 2.0 + SQLite
**Type:** Local web app — runs on a single machine, accessed via browser at localhost

---

## What This Is

A full rebuild of JAKS's inventory/ERP system. Replaces a broken legacy system.
Phase 1 covers the complete daily operations workflow:

```
PO → Receive → Quote → Sales Order → Invoice → Payment → Cores
```

---

## Key Directories

```
rebuild/
├── app/
│   ├── models/          # SQLAlchemy models — ONE file per domain
│   ├── services/        # ALL business logic lives here — routers are thin shells
│   ├── routers/         # FastAPI route handlers — no business logic
│   ├── templates/       # Jinja2 + HTMX templates
│   │   ├── base.html    # App shell: nav, header, slide-overs, toast system
│   │   └── [domain]/    # list.html, detail.html, new.html, _partials.html
│   ├── constants.py     # Every enum and status value — no magic strings anywhere
│   ├── database.py      # SQLAlchemy engine + Base + SessionLocal
│   ├── deps.py          # FastAPI dependencies (get_db, etc.)
│   ├── seeds.py         # Reference data seeded on startup (scraper sources)
│   └── settings_utils.py # bump_counter() for document number sequences
├── PHASE_1_PLAN.md      # Build order, schema decisions, all open questions
├── MOCKUP_PLAN.md       # Screen-by-screen UI specs + modal/slide-over specs
├── FIGMA_DESIGN_BRIEF.md # Design tokens, components, color palette
├── QUOTING_REQUIREMENTS.md # Quote builder detailed spec (keyboard-first, 45s target)
├── UX_NAVIGATION_REQUIREMENTS.md # Nav structure, Recently Viewed, Log Call
└── INTERVIEW_NOTES.md   # All workflow decisions from Keith interviews
```

---

## Architecture Rules

**1. Service layer owns all business logic.**
Routers call services and return templates. Nothing else.
- `InvoiceService` owns `invoice.status` — nothing else writes it
- `CRMService` owns `customer.credit_balance` — nothing else writes it
- Services call `self.db.flush()` after adds; **caller commits**

**2. Constants are the only source of truth for status values.**
`app/constants.py` contains every `StrEnum`. Never use raw strings for statuses.

**3. Gap check before any new UI work.**
Before writing any route or template: read the plan, check the schema, fix gaps first.
Mid-build schema changes are 3-5x more expensive than upfront fixes.

**4. Number sequences via `bump_counter()`.**
All document numbers go through `app/settings_utils.bump_counter(db, key, prefix)`.
Format: `PREFIX-YEAR-NNNN` (e.g. `INV-2026-0001`).
Keys: `next_invoice_number`, `next_quote_number`, `next_so_number`, `next_po_number`,
`next_ra_number`, `next_wc_number`, `next_ri_number`, `next_core_slip_number`, `next_vcr_number`

**5. HTMX + Alpine.js patterns.**
- HTMX handles all server round-trips (forms, search, tab content, partial updates)
- Alpine.js handles local UI state only (show/hide, toggles, live calc)
- Slide-overs: shared containers in `base.html`, content loaded by HTMX
- Toasts: OOB swap to `#toast-container` — `hx-swap-oob="beforeend:#toast-container"`

---

## Navigation Structure (Locked)

```
Dashboard
SALES      → Customers / Quotes / Sales Orders / Invoices
PURCHASING → Vendors / Purchase Orders
INVENTORY  → Products
CORES      → Core Charges / Returns / Warranty Claims
REPORTS    → Reports
SYSTEM     → Settings
```

---

## Global Components in base.html

| Component | How to trigger |
|---|---|
| Log Call slide-over | `@click="logCallOpen = true"` |
| Inline creation slide-over | `hx-get="/[domain]/quick-create-form"` + `hx-target="#create-slide-content"` + `@click="createSlideOpen = true; createSlideTitle = 'New X'"` |
| Toast (success) | Server returns `<div id="toast-container" hx-swap-oob="beforeend">…</div>` |
| `record-created` event | Fired after quick-create saves — originating field listens with `@record-created.window` |
| Ctrl+K | Auto-focuses global search from any screen |

---

## Model Files

| File | Key Models |
|---|---|
| `models/customer.py` | Customer, CustomerAddress, CustomerContact, CustomerCallLog |
| `models/vendor.py` | Vendor, VendorContact, VendorCredit, VendorProgram |
| `models/product.py` | Product, ProductVendorSource, CrossReference, ProductSerialNumber |
| `models/quote.py` | Quote, QuoteLine, SalesOrder, SOLine, QuoteFollowup, LostSaleLog |
| `models/invoice.py` | Invoice, InvoiceLine, Payment, PaymentAllocation |
| `models/purchase_order.py` | PurchaseOrder, POLine, POReceipt, POReceiptLine, VendorBill, VendorBillLine |
| `models/core.py` | CoreCharge, CoreReturnEvent, CoreLocation, CoreSlip, VendorCoreReturn, VendorCoreReturnLine |
| `models/research.py` | ResearchItem, ResearchActivityLog |
| `models/scraper.py` | ScraperSource, ScrapeRun, ScrapedItem, ScrapedCrossRef, ScraperFieldMapping |
| `models/inventory.py` | InventoryLocation, InventoryTransaction |
| `models/returns.py` | ReturnAuthorization, ReturnLine |
| `models/warranty.py` | WarrantyClaim, WarrantyClaimLine, ESNLookup, EngineConfig |
| `models/setting.py` | Setting (key/value — company info, sequences, integrations) |

---

## Service Files

| Service | Owns |
|---|---|
| `QuoteService` | Quote + QuoteLine creation, conversion to SO/Invoice |
| `SalesOrderService` | SO status, line fulfillment tracking |
| `InvoiceService` | Invoice status, lock logic, EOD lock |
| `PaymentService` | Payment recording, allocation, NSF reversal |
| `CRMService` | CustomerCallLog, credit_balance, A/R aging |
| `ProductService` | Product CRUD, vendor sources, cross-refs, cost history |
| `CoreService` | Core charge lifecycle, inspection, VCR batches |
| `ResearchService` | Research items, activity log, dealer/vendor templates |
| `POService` | PO, receipt, vendor bill, 3-way match |
| `PricingService` | Sell price calculation, markup, surcharges |
| `VendorAvailabilityService` | **STUB** — Phase 2. Returns "check manually" for now |
| `SearchService` | Global search across all entities |
| `WarrantyService` | Warranty claim lifecycle |

---

## Current Build Status

### ✅ Complete
- Full schema: 60 models, all enums, all sequences
- Navigation: correct structure, active states, Ctrl+K, Log Call button
- Inline creation: Quick Create Customer / Vendor / Product (slide-overs + routes)
- Global Log Call slide-over with customer typeahead
- Toast system (OOB swap pattern)
- Seeds: PAI, HHP, ATL scraper sources (inactive until Phase 2)

### 🔶 Built — Needs Verification
Customer list/detail, Vendor list/detail, Product list/detail,
Quote workspace, Sales Order detail, Invoice detail, PO detail,
Core Charges list, Settings

### ❌ Not Yet Built
- Returns screen (router + templates)
- Reports hub (router + templates)
- Core Slip print popup
- Record Payment slide-over
- Convert Quote→SO popup
- Receive Core slide-over
- Core Inspection modal
- VCR Batch modal
- Quote follow-up bar (Quick Follow-Up Bar on workspace)
- Dashboard metric wiring + Research Queue widget
- QBO integration (Phase 1 stub)
- PDF generation

---

## Key Design Decisions (All Locked)

- **Quote builder is a live ops console**, not a form. 45-second target. Keyboard-first.
- **Dual workflow**: customer-first OR part/ESN-first — system never forces customer before research
- **Quick Follow-Up Bar**: 5 pill buttons at bottom of every quote (no extra screen)
- **Quote pop-out**: opens in new browser window — main app stays usable
- **Auto-save**: continuous background save, "Saved 5s ago" indicator
- **Inline creation**: never leave a workflow to create a supporting record
- **Core lifecycle**: Sale → Slip → Receive → Inspect → Credit → VCR Batch → Vendor Reconciliation
- **Enrichment review**: scraper results always go through manual review before applying to product
- **Research Items** (RI-2026-XXXX): unknown parts tracked through quote workflow until resolved

---

## Running the App

```bash
cd "C:\Users\keith\Inventory Program\rebuild"
.venv\Scripts\activate      # or: source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
# Open: http://localhost:8000
```

Database is auto-created at first run. Settings and scraper sources are seeded automatically.

---

## Plan Documents (read these before making changes)

1. `PHASE_1_PLAN.md` — authoritative build order and all schema decisions
2. `MOCKUP_PLAN.md` — every screen and modal spec with ASCII layouts
3. `FIGMA_DESIGN_BRIEF.md` — design tokens, color palette, component library
4. `QUOTING_REQUIREMENTS.md` — quote builder deep spec
5. `UX_NAVIGATION_REQUIREMENTS.md` — nav structure, Recently Viewed, Log Call spec
6. `INTERVIEW_NOTES.md` — all decisions made with Keith, session by session
