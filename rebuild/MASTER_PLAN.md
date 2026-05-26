# JAKS Inventory — Master Plan
*Consolidated: 2026-05-25 | Supersedes: PLAN.md, PHASE_1_PLAN.md, PLANDESIGN.md, QUOTING_REQUIREMENTS.md, SCHEMA_INTERVIEW.md, INTERVIEW_NOTES.md, DESIGN.md, FIGMA_DESIGN_BRIEF.md, MOCKUP_PLAN.md, researchdesign.md, UX_NAVIGATION_REQUIREMENTS.md*

**Standing rule:** No route, template, or service method is written without reading this document first.
**Financial integrity rule:** No transactional route shall ever: silently fail / directly mutate DB / bypass service layer / swallow exceptions. Required: visible error banners / audit logging / rollback behavior / centralized transaction handling.

---

## 1. Business Context

**JAKS** — small B2B diesel parts dealership. Two users: **Keith** (operations, sales, purchasing) and **wife** (bookkeeping). Primary vendors: PAI Industries (preferred), HHP, ATL Diesel. Customers: local diesel repair shops.

**Why we rebuilt:** Previous PySide6 desktop app grew to 100+ screens with no stable core — fix one thing, break another. Rebuilt as a local web app: FastAPI + HTMX + Alpine.js + Jinja2 + Tailwind CSS + SQLite + SQLAlchemy.

**Stack:**
| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Database | SQLite local file (SQLAlchemy ORM — PostgreSQL-ready) |
| Frontend | HTMX + Alpine.js + Jinja2 |
| Styling | Tailwind CSS (CDN) |
| PDF | WeasyPrint (GTK fallback → browser print) |
| Auth | itsdangerous session tokens |

---

## 2. Architecture Rules (Non-Negotiable)

1. **Service layer owns all business logic.** Routers receive → call service → return template. No logic in routers or templates.
2. **All monetary calculations happen server-side.** Frontend displays; never trusts client values.
3. **Inventory changes only through controlled events.** PO receipt, invoice save, manual adjustment. Never a direct qty edit.
4. **Every financial event is timestamped and attributed.** Who did what, when.
5. **Invoice numbers are sacred.** Once issued, never reused. Gaps are auditable.
6. **Payment cannot exceed invoice total.** Service enforces, not just warns.
7. **Credit memos are the only way to modify a locked invoice.** No backdating.
8. **JAKS Inventory is the operational source of truth.** QBO is the accounting source of truth. JAKS pushes TO QBO — never pulls.

---

## 3. Design System (Locked)

**Philosophy:** Boring works. Density over whitespace. Predictability over surprise. "Bloomberg Terminal meets QuickBooks."

**Brand:** Olive green `#4b5320` (brand-700). Sidebar: `bg-slate-900`. Logo: "JAKS" white + "Inventory" brand-300.

**Semantic colors (NEVER replace with brand color):**
- Green = Active, Paid, Confirmed, In-stock
- Red = Error, Overdue, Cancelled, 0-stock
- Amber = Pending, Partial, Draft, Waiting
- Blue = Informational, Sent, In-progress
- Gray = Inactive, Historical, Closed
- Purple = Waiting on vendor
- Teal = Warranty credit actions

**Typography:** Identifiers (SKU, invoice#, PO#) always `font-mono text-brand-700`. Currency always right-aligned `tabular-nums`. Table headers `text-xs font-semibold uppercase tracking-wide`.

**Table density:** Compact ERP — `py-1.5 px-4` on `<td>`, ~32px rows, ~20–25 rows before scroll.

**Component classes:** `.card`, `.card-header`, `.card-body`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.form-input`, `.form-label`, `.badge`, `.badge-[color]`, `.tab-active`.

---

## 4. Navigation (Locked)

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

## 5. All Locked Decisions

### Business & Workflow
| # | Decision | Answer |
|---|---|---|
| D1 | Nav structure | SALES / PURCHASING / INVENTORY / CORES / REPORTS / SYSTEM |
| D2 | Zero-stock invoicing | Warn + prompt SO, allow invoice if user proceeds |
| D3 | Warranty credit form | Account credit default; check is Keith's manual decision |
| D4 | SO payment collection | User picks per transaction: Full / Deposit / None |
| D5 | Invoice lock window | Locks at: end of business day OR QBO sync OR paid — whichever first |
| D6 | Core credit method | Customer's choice: account credit or check |
| D7 | Serial numbers | Cylinder heads confirmed; others by exception |
| D8 | Kit types | Both: vendor kit (single SKU) + JAKS-built kit (BOM) |
| D9 | Vendor bill approval | Auto-approve if PO/receipt/bill qty match exactly; discrepancy → manual |
| D10 | Customer import | Excel Phase 1 (late); QBO pull Phase 2 |
| D11 | Reports | In-app report center + embedded in relevant screens |
| D12 | Research tracking | Lives inside quote line (not separate tasks). Solved items → cross-refs. |
| D-pdf | PDF priority | Quote first, Invoice second, PO third |

### Research & Cross-References (R-series — all locked)
| # | Decision | Answer |
|---|---|---|
| R-A | Template delivery | Copy/paste text output only. System email is Phase 2. |
| R-B | Urgency flag | Formal tier: Normal / Urgent / Truck Down |
| R-C | Cross-ref on resolution | Always prompt user first. Never auto-create silently. |
| R-D | Research Queue widget | Own dedicated widget. Separate from quote follow-ups. |

### Invoice Lock Rule
Invoice unlocked: full edit — add/remove lines, change qty/price.
Invoice locked: "Locked" badge, Edit grayed, changes require credit memo.

Lock triggers (first one wins):
1. End of business day (configurable `business_close_time` setting)
2. Pushed to QBO (`qbo_sync_status = 'synced'`)
3. Fully paid

---

## 6. Schema — Tables Built ✅

All 40+ tables from SCHEMA_INTERVIEW.md and PHASE_1_PLAN.md Step 0 are in the SQLAlchemy models:

**Foundation:** settings, audit_log, users, user_sessions, inventory_locations, inventory_transactions
**Master Data:** vendors, vendor_contacts, vendor_credits, vendor_programs, customers, customer_contacts, customer_addresses, products, product_vendor_sources, product_categories, product_cost_history, product_images, product_serial_numbers, product_kits, product_kit_lines
**Sales Cycle:** quotes, quote_lines, sales_orders, so_lines, invoices, invoice_lines, payments, payment_allocations
**Purchasing:** purchase_orders, po_lines, po_receipts, po_receipt_lines, vendor_bills, vendor_bill_lines
**Special Processes:** core_charges, core_slips, vendor_core_returns, vendor_core_return_lines, core_locations, core_return_events, return_authorizations, return_lines, warranty_claims, warranty_claim_lines
**Research:** research_items, research_activity_log
**Cross-refs:** cross_references, price_history, shipments, document_attachments
**Scaffold-only (empty):** esn_lookups, engine_configs, quote_followups, lost_sales_log

---

## 7. Service Layer — Built ✅

All 13 services in `/app/services/`:
`SearchService`, `ProductService`, `PricingService`, `QuoteService`, `SalesOrderService`,
`InvoiceService`, `PaymentService`, `CoreService`, `POService`, `WarrantyService`,
`VendorAvailabilityService` *(all 3 methods stub — Phase 2)*, `ESNLookupService` *(stub — Phase 3)*, `CRMService`, `ResearchService`

---

## 8. Build Status — What Is DONE ✅

### Foundation
- [x] Full SQLAlchemy schema (all tables, all fields from SCHEMA_INTERVIEW.md)
- [x] Service layer skeleton (13 services with proper interfaces)
- [x] Settings + number sequences (INV, Q, SO, PO, RA, WC, CORE, VCR, RI)
- [x] Navigation: SALES / PURCHASING / INVENTORY / CORES / REPORTS / SYSTEM sidebar

### Design System
- [x] Base design system: all `.card`, `.btn-*`, `.form-*`, `.tbl-*`, `.badge-*`, `.tab-*` utility classes
- [x] Brand colors (olive brand-700), semantic color rules enforced

### Screens Built
- [x] Dashboard (KPI tiles + widgets)
- [x] Customers — list + detail (tabs: Account, Invoices, Quotes, Call Log, Sales Orders)
- [x] Quotes — list + workspace
- [x] Quote workspace: autosave, follow-up bar, chips row, warranty tier picker, upgrade options, context menu, QOH colored dot column, Margin % before Total column order
- [x] Sales Orders — list + detail
- [x] Invoices — list + detail
- [x] Payments — list + detail
- [x] Purchase Orders — list + detail
- [x] Vendors — list + detail
- [x] Products — list + detail (6-tab layout: Info / Sources / Cross-Refs / Images / Suggested Sells / History)
- [x] Product enrichment panel (PAI/HHP/ATL scraper buttons — buttons wired, scrapers are Phase 2)
- [x] Cross-reference confidence status (7 states, inline HTMX PATCH)
- [x] Image management tab on product detail
- [x] Core Charges — list + detail
- [x] Warranty Claims — list + detail

### PDFs
- [x] Quote PDF (`GET /quotes/{id}/pdf`) — WeasyPrint with OSError fallback → browser print
- [x] Invoice PDF (`GET /invoices/{id}/pdf`) — same pattern
- [x] Print templates: quotes/print.html, invoices/print.html (brand colors, auto-print JS on `?auto=1`)

### Features Built
- [x] Payment workflow: record payment, allocate to invoices, reverse (PaymentService)
- [x] Suggested sells chips + inline chip row on quote lines
- [x] Warranty tier picker (inline, 5 tiers, price formula)
- [x] Upgrade option system: Economy/Recommended/Premium grouping, select-upgrade, toggle-included
- [x] Quote line role system: `line_role` (primary/core/warranty/upgrade_option/optional/suggested), `is_included`, `option_label`
- [x] `_tree_sort_lines()` — parent-first ordering for quote line rendering

---

## 9. Build Status — What Is NOT YET BUILT ❌

### Immediate (must do before daily use)

| # | Feature | Why Blocking |
|---|---|---|
| 1 | **DB recreate** | `line_role`, `is_included`, `option_label` columns not in existing DB. Drop + recreate. |
| 2 | **Inline creation slide-overs** | #1 UX problem. Quote/Invoice/PO screens break when customer, product, or vendor doesn't exist yet. |
| 3 | **Global search Ctrl+K overlay** | Quote screen's product search needs this. Without it, part search is not keyboard-first. |
| 4 | **Customer balance mini-panel on quote screen** | Terms / Open Balance / Overdue / Credit / Cores Owed |
| 5 | **PO receipt workflow (3-way match)** | Inventory cannot be trusted until receiving is solid |

### Core Workflows Not Yet Wired

| Feature | Status | Notes |
|---|---|---|
| Sales Order payment collection (Full/Deposit/None) | Schema + model built, UI not wired | SalesOrderService.record_payment() is stub |
| SO → Invoice conversion | Schema built, route is stub | |
| PO Receive → inventory update | Schema built, route is stub | POService.receive() is stub |
| Vendor bill creation (3-way match) | Schema built, service is stub | |
| Core lifecycle (full: invoice → slip → customer return → vendor return → credit) | Schema built, CoreService methods are stubs | |
| Return Authorization workflow | Schema built, service is stub | |
| Warranty claim state machine | Schema built, service is stub | |
| Research status on quote lines | Schema + models built, UI not built | ResearchService is stub |
| Invoice lock logic (end-of-day / QBO / paid) | Schema built, InvoiceService.lock() is stub | |
| Invoice edit while unlocked | Basic edit works; lock enforcement missing | |

### Phase 1 — Later (not immediately blocking)

| Feature | Notes |
|---|---|
| QBO OAuth + push (invoices, payments, vendor bills) | Wife's bookkeeping gate. OAuth not started. |
| Report suite (AR aging, sales by customer, inventory val.) | Basic structure exists; queries not wired |
| Dashboard operational widgets (Research Queue, Follow-up Today, Open SOs, Overdue) | Widgets visible but not data-connected |
| Customer Excel import | CSV upload → review → import |
| Serial number tracking (cylinder heads) | Schema built, UI not built |
| Kit BOM management (vendor + JAKS-built) | Schema built, UI not built |
| Quote pop-out window (second browser window) | Spec locked — window.open('/quotes/{id}/popup') |
| PO PDF (print/email to vendor) | Print template not built |
| Core Return Slip PDF (CORE-XXXX) | Template not built |
| Vendor Core Return Sheet PDF | Template not built |
| Return Authorization document PDF | Template not built |
| Warranty Claim form PDF | Template not built |
| Lost sales log on declined quotes | Field exists, UI not built |
| Quote reactivation (6-month-old quote) | QuoteService.reactivate() is stub |
| Quote duplication | QuoteService.duplicate() is stub |
| Customer balance visible on invoice screen | Not built |
| NSF check reversal workflow | Schema built, PaymentService.reverse_nsf() is stub |

### Phase 2 (after Phase 1 stable in daily use)

| Feature |
|---|
| VendorAvailabilityService — PAI/HHP/ATL scraper wiring (all 3 methods currently raise NotImplementedError) |
| Vendor availability pills on quote workspace (PAI/HHP/ATL live data) |
| Shopify product push + order sync |
| TaxJar (automated sales tax) |
| QBO customer pull / import |
| System email/text delivery (research templates, invoices) |
| Credit memo / refund check workflow (requires LineType.CREDIT_MEMO + InvoiceService guard update) |
| Option Groups visual rendering (color-coded sections in quote line table) |
| "View Related" slide-over per quote line |
| Auto-open slide-over for high-value bundles |
| WarrantyService full implementation |
| Full P&L and financial reports |

### Phase 3
eBay listings, full TaxJar (multi-state), ESN lookup scraper live, serial number UI.

---

## 10. Next Build Queue (Priority Order)

These are ordered by what blocks daily use most directly.

### SPRINT 1 — Make It Usable (do in this order)

**1.1 — DB Recreate**
Drop `data/jaks.db`, run `init_db()`. New columns exist in models but not in the SQLite file.
Add `ri_counter` and `core_slip_counter` to `bump_counter`'s year-rollover key list in `settings_utils.py`.

**1.2 — Inline Creation Slide-overs**
The #1 UX problem. Build these three:
- Quick-create Customer (from Quote/Invoice customer dropdown)
- Quick-create Product (from Quote/Invoice/PO product search)
- Quick-create Vendor (from Product/PO vendor dropdown)

Pattern: POST to `/quick-create/{type}` → returns `<option>` fragment → HTMX swaps into originating `<select>`.

**1.3 — PO Receive Workflow**
Wire `POService.receive()`. A receipt session accepts partial/full quantities across one or more POs from the same vendor (per SCHEMA_INTERVIEW.md E2). Receipt → inventory_transactions ↑. PO status rolls up from line fulfillment.

**1.4 — Global Search Ctrl+K**
Header search overlay, keyboard navigable. `SearchService` already built — wire the UI. Returns grouped results: Customers / Products / Quotes / Invoices / Vendors. Keyboard: ↑↓ navigate, Enter select, Esc close.

**1.5 — Customer Balance Mini-Panel on Quote Screen**
Always-visible panel in quote header: Terms | Open Balance | Overdue | Credit Available | Cores Owed.
Data from: `customer.payment_terms`, sum of invoice balance_due, customer.credit_balance, count(open core_charges).

### SPRINT 2 — Close the Revenue Loop

**2.1 — Invoice Lock Logic**
`InvoiceService.lock()` — enforce the three triggers. Locked badge, grayed edit button.

**2.2 — SO → Invoice Conversion**
`SalesOrderService.convert_to_invoice()` — builds invoice from fulfilled SO lines. Pre-applies any deposit collected at SO stage.

**2.3 — SO Payment Collection**
Payment mode selector on SO form (Full / Deposit / None). `PaymentService.record_payment()` called at SO stage, carries forward.

**2.4 — Vendor Bill Creation (3-way match)**
From received PO: "Create Bill" button → `POService.create_vendor_bill()`. Auto-approve if PO/receipt/bill qty match. Flag discrepancy if qty_billed > qty_received.

### SPRINT 3 — Special Processes

**3.1 — Core Lifecycle**
Wire `CoreService` methods in order:
- `open_customer_core()` — called by InvoiceService on invoice save for each core-eligible line
- `receive_customer_core()` — inspection workflow, disposition choice
- `create_vendor_core_return()` — batch VCR, print shipment doc
- `record_vendor_decision()` — accepted/rejected/partial, resolution

**3.2 — Return Authorization Workflow**
RA creation → customer signs → receive returned goods → inventory disposition → credit memo.

**3.3 — Warranty Claim State Machine**
`draft → submitted_to_vendor → vendor_approved → customer_credited → closed`
(or `vendor_denied → customer_notified → closed`)

### SPRINT 4 — Visibility & Integration

**4.1 — Dashboard Widgets (data-connected)**
Research Queue counts, Follow-up quotes due today, Open SOs count, Overdue invoices.

**4.2 — QBO OAuth + Push**
Settings → Connect to QuickBooks → OAuth → store tokens.
Push: Invoices, Payments, Vendor Bills (on-demand per document + batch option).

**4.3 — Basic Reports**
AR Aging, Sales by Customer, Sales by Product, Inventory Valuation, Open POs, Core Charges Outstanding.

### SPRINT 5 — Polish & Late Phase 1

Research status on quote lines, quote pop-out window, quote duplication, customer Excel import, serial number UI, PO PDF, core document PDFs.

---

## 11. Go-Live Checklist (Phase 1 Complete)

Keith signs off when ALL of these work in real daily use (not test data):

- [ ] Enter new vendor + product in under 3 minutes (including enrichment)
- [ ] Create PO, receive it partially, inventory updates correctly
- [ ] Build a quote in 45 seconds on a live phone call
- [ ] Convert quote → SO (out-of-stock) or → Invoice (in-stock) in one click
- [ ] Collect payment on an SO (full, deposit, or defer)
- [ ] Invoice locks at end of day; credit memo corrects a locked invoice
- [ ] Core charges auto-appear on quotes/invoices; lifecycle closes correctly
- [ ] Core return shipment document prints with RMA and tracking
- [ ] Warranty claim moves through all states; account credit issued on approval
- [ ] Return authorization generates; credit applied to customer balance
- [ ] Wife pushes invoices, payments, vendor bills to QBO with one click
- [ ] Dashboard shows: open SOs, follow-up quotes today, overdue invoices, outstanding cores
- [ ] No data integrity issues in 2 weeks of real daily use
- [ ] Wife has no open bookkeeping accuracy questions

---

## 12. PDF Document Status

| Document | Status | Route |
|---|---|---|
| Quote PDF | ✅ Built (WeasyPrint + fallback) | GET /quotes/{id}/pdf |
| Invoice PDF | ✅ Built (WeasyPrint + fallback) | GET /invoices/{id}/pdf |
| Quote Print (browser) | ✅ Built (auto-print on ?auto=1) | GET /quotes/{id}/print |
| Invoice Print (browser) | ✅ Built (auto-print on ?auto=1) | GET /invoices/{id}/print |
| Purchase Order PDF | ❌ Not built | |
| Core Return Slip (CORE-XXXX) | ❌ Not built | |
| Vendor Core Return Sheet | ❌ Not built | |
| Return Authorization document | ❌ Not built | |
| Warranty Claim form | ❌ Not built | |
| Sales Order PDF | ❌ Not built | |

**WeasyPrint note:** Installed (v68.1) but requires GTK/Pango system libraries on Windows. Currently falls back to browser print-to-PDF via `?auto=1` redirect. Install GTK runtime from https://doc.courtbouillon.org/weasyprint/stable/first_steps.html to enable true server-side PDF — no code changes needed.

---

## 13. Number Sequences

| Prefix | Document | Counter Key |
|---|---|---|
| INV | Invoices | invoice_counter |
| Q | Quotes | quote_counter |
| SO | Sales Orders | so_counter |
| PO | Purchase Orders | po_counter |
| RA | Return Authorizations | ra_counter |
| WC | Warranty Claims | warranty_counter |
| CORE | Core Return Slips | core_slip_counter |
| VCR | Vendor Core Returns | vcr_counter |
| RI | Research Items | ri_counter |

Sequences reset annually (Jan 1). `current_sequence_year` in settings detected on startup.

---

## 14. Quote Workspace — Key Specs

**Target:** Build a quote in 45 seconds, keyboard only, while customer is on the phone.

**Keyboard flow:** Customer selector → Tab → ESN → Tab → Notes → Tab → Search bar → type part# → ↓ navigate → Enter select → Qty input → Tab → Price input → Enter → back to search bar.

**Line table columns (confirmed order):** SKU | Description | QOH | Qty | Sell $ | Disc % | Margin % | Total | (actions)

**QOH dot:** Green ≥ 2, Amber = 1, Red = 0.

**Vendor availability pills (PAI/HHP/ATL):** Deferred to Phase 2 (VendorAvailabilityService stubs).

**Customer status panel (always visible):** Terms | Open Balance | Overdue | Credit Available | Cores Owed. Does NOT block quoting. Informs judgment.

**Follow-up bar (always visible at bottom):**
```
[ Follow Up Tomorrow ] [ Waiting on Customer ] [ Waiting on Vendor ] [ 🔴 Truck Down ] [ No Follow Up ]
```

**Autosave:** Every 10 seconds + Ctrl+S + explicit Save button. "Saved X seconds ago" indicator always visible.

**Part-first workflow:** Quote can be started with no customer. Customer field optional during research, required before sending.

---

## 15. Core Charges — Key Specs

**Core markup:** `vendor_core_charge` (what JAKS pays) ≠ `customer_core_charge` (what customer pays). Margin is visible.

**Core slip prompt:** After invoice with core items: popup "This invoice includes core items. [Print Core Return Slip] [Skip]"

**Customer return search by:** Core Slip # (preferred) / Customer / Invoice # / Part # / Tracking # / Phone #

**Inspection required before credit:** Accepted / Hold for Review / Rejected / Damaged / Wrong Core / Partial Credit

**Credit methods:** Account Credit (default) / Issue Check / Hold Pending Review / Reject-No Credit / Partial Credit

**Core locations (separate from sellable inventory):** Core Shelf / Core Holding / Ready for PAI / Ready for HHP / Questionable Core / Rejected Core / Scrap Core

**Vendor paperwork:** Does NOT show customer identity. Shows: VCR#, JAKS core ref, Part#, Description, Qty, Expected credit, RMA#, Tracking#.

---

*This document is the single source of truth for all JAKS Inventory build decisions.*
*Update it as decisions change. All other planning documents are superseded.*
*Last updated: 2026-05-25*
