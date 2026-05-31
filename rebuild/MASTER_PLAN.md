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

### Owner Decisions — Locked 2026-05-30 (Phase 1A scope)

*Frozen by Keith. These govern current work; lanes build to them. Several **authorize new
Phase-1A backend work that overrides the 2026-05-29 "backend support-mode / no new work" freeze
for these items only.***

| # | Decision | Locked answer | Touches |
|---|---|---|---|
| O1 | **Phase split** | **Phase 1A = full operational ERP WITHOUT QBO** (the go-live target). **Phase 1B = QBO** (OAuth + push; the old "Backend Phase M"). QBO is **not** a 1A go-live blocker. | Plan / all |
| O2 | **Auth** | Add **minimal 2-user login** (Keith + wife). Every financial/audit event is **attributed to the signed-in user** (satisfies Architecture Rule #4). Replaces the placeholder at `main.py:92`. **In 1A scope.** | Backend |
| O3 | **Data safety** | **Automatic `data/jaks.db` backup + a tested restore** must exist **before 1A go-live.** This is the cutover that ends jaks.db's "disposable" status once real data is loaded. | Backend |
| O4 | **Vendor contacts** | **Support multiple contacts per vendor** (`vendor_contacts` table already exists). Vendor detail gets a Contacts card: list + add/edit/delete + mark primary. | Backend + UI-Builder |
| O5 | **Markup** | **Move markup rules into Settings** — out of the hardcoded 30% fallback (`product.py:202`). Min: a global default markup %; per-category override preferred. | Backend + Settings UI |
| O6 | **Card surcharge** | **Per-customer default surcharge %, overridable per invoice** at entry. Replaces the one-way 3% toggle (owner-test 1.9.e). | Backend + UI |
| O7 | **Core-slip popup** | **Deferred to Phase 2.** The auto-trigger "print core return slip" popup at invoice finalize (TODO in `invoices.py`) is **not** 1A. Manual core-slip/VCR printing stays available. | — (deferred) |
| O8 | **Receiving-slip print** | **Optional for Phase 1A** — the `/receiving-slip` print route (currently a disabled placeholder) is nice-to-have, **not** a 1A go-live blocker. | — (optional) |
| O9 | **QA actions** | QA **verifies** the partial quote-line research-status UI (§9.3) and **triages the 2 cores edge-case bugs** from `9d0ced2` into 1A-fix vs deferred. | QA → Backend |

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

> **🔴 OWNER FUNCTIONAL TEST RECONCILIATION — 2026-05-30 (supersedes the 05-29 "support mode" claim below).**
>
> **✅ UPDATE (same day, later):** all three blockers below are **FIXED + regression-guarded** —
> `3a700fd` (B1/B2 core-loop unblock) · `634d056` (B3 invoice search) · `e1d632d` (QA regression
> gate: real-query SO product-search + linked-PO receive with full inventory-ledger assertions; 115
> tests green). B5/B6 (vendor reactivate, products bulk-status/export) also landed (`82f7495`).
> B4 (quote customer pre-fill) + search dash-normalization are implemented but **uncommitted** in the
> working tree as of this writing. The text below is retained as the root-cause record / lesson.
> Keith ran a screen-by-screen functional test (`Testing Feedback/TESTING_FEEDBACK5.30.26.docx`).
> It surfaced **two LIVE core-loop blockers that the 125 passing tests did NOT catch**, both
> introduced by recent "support-mode" commits:
> - **Sales Order add-line is dead** — `app/routers/sales_orders.py:281,382` reference `Product.name`,
>   a phantom attribute (model has `title`/`description`). Every product search 500s → no line can
>   be added. Regression from commit `b097ddd`.
> - **PO Receive is dead** — `app/services/po_service.py:448` uses `SOLineStatus` but commit `fa73f57`
>   dropped it from the import block (lines 20-23) → `NameError` on any receive of an SO-linked PO
>   line, swallowed by a bare `except` into "receipt was not recorded." Tests pass because no test
>   creates SO-linked PO lines. This **refutes** the 05-29 §8G claim that "can't receive" was only a
>   discoverability issue — it is a hard crash.
> - **Invoice add-line** was the same class of bug (`suggested_sell_price` phantom in
>   `invoices/_search_results.html`); fix is in the working copy, **uncommitted**.
>
> **Effect on status:** Backend is **NOT in clean support mode** — it has two live regressions from
> its own commits. The "Quote/Returns won't open" report was a real HTTP 500 (old Starlette
> `TemplateResponse` API) **already fixed in this branch by `b514196`** — owner tested a pre-pull
> build; needs re-pull + re-test, not new work. Same for invoice "change customer" (cosmetic fix
> already in `bcda974`). The whole CORES / RETURNS / WARRANTY / REPORTS / cross-workflow E2E section
> is **entirely unverified** (owner marked "need to test"). **Action queue + lane tickets:
> see the 2026-05-30 status-refresher output.** Do not trust "complete" below until owner re-test
> on a fresh pull confirms the business task end-to-end.

> **⚠️ RECONCILED 2026-05-29 against the actual codebase (service + router + template audit).**
> The previous version of this section was badly stale: it listed PO receive, 3-way match,
> SO→Invoice, invoice finalize/lock/void, core lifecycle, warranty/RA state machines, payment
> reversal/NSF, credit memos, quote duplicate/reactivate, the report suite, dashboard widgets,
> and most document PDFs as "stub" / "not built." **All of those are now implemented and routed.**
> Backend Workflow Series 1–5 landed them (125 tests passing); backend is in **support mode**.
> **Do not use the old text as a to-do list — trust the code.** The genuine remaining gap is the
> **UI maturity rollout**, not backend logic.

### 9.0 — What was on this list and is now DONE ✅ (do not rebuild)

DB recreate · inline quick-create slide-overs (customer/product/vendor) · global Ctrl+K search ·
customer balance mini-panel on quote · PO receive → inventory + moving-avg cost · 3-way match
(receipt → vendor bill → discrepancy resolution) · SO deposit collection (Full/Deposit/None) ·
SO→Invoice (`fulfill_and_invoice`) · invoice finalize / lock / void / apply-credit · payment
record / reverse / NSF · full core lifecycle · Return Authorization workflow · warranty claim
state machine · credit memos + vendor credits · statements · in-app notifications · quote
duplicate + reactivate · customer import · report suite (9 reports) · dashboard metrics
(data-connected) · PO / SO / Core-slip / VCR / RA / Warranty print templates + `/pdf` routes.

### 9.1 — Genuinely not built / deferred (BACKEND)

| Feature | Status | Notes |
|---|---|---|
| QBO OAuth + push (invoices, payments, vendor bills, credit memos) | **Deliberately gated** | Standing decision 2026-05-29: do not start until UI rollout + core ops stable. `qbo_*` fields dormant. Backend Phase M. |
| Vendor availability scrapers (PAI/HHP/ATL) | Stub by design | `vendor_availability_service.py` — all 3 methods `raise NotImplementedError`. Phase 2. |
| ESN lookup scraper | Stub by design | `esn_lookup_service.py` — `raise NotImplementedError`. Phase 3. |
| Real email/SMS send | NullProvider only | `MessagingService` logs to `communication_log` (`logged_only`); SMTP/M365/Twilio providers are Phase 2. |
| Server-side PDF rendering | Falls back to browser print | WeasyPrint v68.1 installed but GTK/Pango missing on Windows → `?auto=1` browser print. Install GTK to enable true PDF (no code change). |

### 9.2 — The real Phase-1 gap: UI maturity rollout 🟡

Backend can do these workflows; the **screens are still raw L1 `tbl-*` tables** (see
`JAKS_UI_Change_Plan.md` §6 Rollout Order — the authoritative UI status). Remaining L1→L2/L3 work:

- **Lists L1→L2:** Sales Orders, Vendors, Cores, Warranty, Returns, Vendor Returns, Payments.
- **Detail/workspace:** Product Detail (L1→L2); SO / Warranty / Returns / Vendor-Return workspaces.
- **Reports:** all 9 reports render in raw `tbl-*` — functional, unpolished (AR Aging + Statements are owner/wife-facing).
- **Ceiling work (quality→10):** Tailwind CDN → compiled build + tokens; CI lint gate on the design system; a11y/focus pass; state matrix (skeletons / empty / error); customer-facing PDF polish.
- **Known UX gaps:** core-slip popup not auto-triggered at invoice finalize (TODO in `invoices.py`); quote add-line two-step staging reads as broken (§8C.1); cross-workspace action-header standard open (§8B).

### 9.3 — Schema built, UI not built (Phase-1 late)

| Feature | Notes |
|---|---|
| Serial number tracking UI (cylinder heads) | Models + `product_serial_numbers` exist; no UI. |
| Kit BOM management UI (vendor + JAKS-built) | `product_kits` / `product_kit_lines` exist; no UI. |
| Quote pop-out window (second browser window) | Spec locked — `window.open('/quotes/{id}/popup')`. Not built. |
| Research status on quote lines | `ResearchService` implemented; quote-line research UI is partial — **verify before scheduling**. |

### Phase 2 (after Phase 1 stable in daily use)

Vendor-availability pills on quote workspace (live PAI/HHP/ATL) · Shopify product push + order
sync · TaxJar (automated sales tax) · QBO customer pull / import · real email/text delivery ·
Option-Groups visual rendering (color-coded quote sections) · "View Related" slide-over per quote
line · auto-open slide-over for high-value bundles · full P&L / advanced financial reports.

### Phase 3
eBay listings · full TaxJar (multi-state) · ESN lookup scraper live · serial-number + kit-BOM UI.

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

## 11. Go-Live Checklist (Phase 1A Complete — operational ERP, no QBO)

> **Phase split locked 2026-05-30 (§5 O1):** This gates **Phase 1A** go-live (operational ERP
> *without* QBO). **Phase 1B = QBO** OAuth + push — see the 1B line below; QBO is **not** a 1A blocker.

Keith signs off when ALL of these work in real daily use (not test data):

- [ ] **Minimal 2-user login works; every invoice / payment / adjustment is attributed to the signed-in user** *(O2)*
- [ ] **Automatic `data/jaks.db` backup runs on schedule; a restore has been tested from a backup** *(O3)*
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
- [ ] *(Phase 1B — not a 1A go-live blocker)* Wife pushes invoices, payments, vendor bills to QBO with one click
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
| Purchase Order PDF | ✅ Built (template + fallback) | GET /purchase-orders/{id}/print · /pdf |
| Core Return Slip (CORE-XXXX) | ✅ Built (template + fallback) | GET /cores/slips/{id}/print · /pdf |
| Vendor Core Return Sheet (VCR) | ✅ Built (template + fallback) | GET /cores/vcr/{id}/print · /pdf |
| Return Authorization document | ✅ Built (template + fallback) | GET /returns/{id}/print · /pdf |
| Warranty Claim form | ✅ Built (template + fallback) | GET /warranty/{id}/print · /pdf |
| Sales Order PDF | ✅ Built (template + fallback) | GET /sales-orders/{id}/print · /pdf |

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
*Last updated: 2026-05-29 — §9 reconciled against the codebase; §12 PDF status corrected.*
