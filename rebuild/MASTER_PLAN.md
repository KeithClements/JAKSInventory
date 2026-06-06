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
| R1 | **CC convenience fee is informational — NOT a bug, NOT to be added to invoice total** | The card-processing surcharge is an **estimate displayed for customer awareness only** ("~$X if paid by card"). It is **never charged in-system** — the processor handles it at swipe time. `invoice.total` and `balance_due` **intentionally exclude the fee.** Source: `app/invoice_totals.py:117-120`. Ruled: Suite-C W-3, e2e Bug4, D-5e, full-audit Bug4. **Any QA flag claiming the fee should appear in the total is a false positive — close without action.** The fee display/caption is UI polish; math changes violate R1. | Backend (locked) |

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

> **✅ 2026-06-04 — QBO Phase-1B BUILT + the Phase-2 UI wave largely shipped. The "gated"/"scaffold-only" notes below are SUPERSEDED for QBO.**
> Ground-truthed against the live tree (`backend/workflow-series-3` @ `9f50216`, fast-forward-merged into local `main`): **966 tests pass**, only the 6 known cosmetic reds.
> - **QBO (Phase 1B) is BUILT — no longer gated.** Hand-rolled OAuth2 (connect/callback/refresh/disconnect) + REST client (httpx, no new deps); **accounting-summary invoice push** (each line-type → a generic income item; cc_surcharge + tax excluded; customer resolve/create; one-time item setup); **bulk push** (Sync Selected / Sync All Unsynced via `/qbo/invoices/push-batch`); Settings "Connect to QuickBooks" card; invoice-list **QBO status column + filter tabs**; workspace Push button. Push is best-effort and **NEVER touches the money path** (success→`mark_synced`/lock, fail→`mark_sync_failed`). **Owner connected the live sandbox and pushed test invoices successfully.** Commits `3873e0e`/`e636760`/`e09a5c9` + the UI wave. *Still deferred within 1B:* payments / vendor-bills / credit-memos push; token-at-rest encryption (Fernet); AST-tax reconciliation (kill-switch = `qbo_push_tax`).
> - **Phase-2 UI wave shipped:** §7.2 **enrichment sync** (`ProductApplication` + `POST /products/enrich-sync`, `370820b`); invoice-list QBO column + bulk sync + **sortable/sticky headers**; **unified customer Timeline tab**; sortable+sticky on customers/quotes/payments; **PDF branding** (formatted phone + Terms + one shared company dict across every print, `d8f33fa`); customer **Acct # + 4-state Status + Timeline-first tabs**; dynamic customer preview dock; **products F2 shortcut + prominent margin**; cost-bracket **pricing grid**; tabbed Settings; quotes-list Open/Print/Email + follow-up colors. Remaining UI consumes (Backend seams already in @`9f50216`): quote-workspace always-visible actions + intelligence render, dashboard Top-Customers/Follow-Ups widgets, Prepared-By print render.
> - The **§9.1 "QBO gated" row** and the **§16 "scaffold only" / "Integrations D+" grades** below are HISTORY — corrected in place.
>
> *The 2026-06-02 banner below is retained as the prior milestone (Phase 1A sign-off).*

> **✅ PHASE 1A SIGNED OFF — 2026-06-02 (status-refresher). The 05-31 banners below are HISTORY.**
> Ground-truthed against the live tree, not self-reports: **801 tests pass** (only 6 non-functional
> reds — 5 cosmetic `test_ui_lint` + 1 brittle `test_template_renders` W-4, do-not-chase). **Both
> spines are owner-proven:** Keith hand-ran Suite B (sale→paid · OOS→SO→receive→invoice · core→credit)
> and the 11-row Suite D money pass — all green. Signed off in `PHASE_1_TEST_PLAN.md` §13.
> - **All 05-31 "remaining go-live gates" are CLOSED:** O2 attribution (`customers.py` no longer
>   hardcodes user 1) + enforce; bookkeeper user seeded; backup restore **admin-gated**; cost-variance
>   3-way gate (`bd65c69`); CSRF = SameSite-Lax + written waiver; `/account` password change + default-pw warning.
> - **`tests/test_payments.py` (15)** closed the D-5 automated-net gap; quote-badge EXPIRED bug fixed (`0549b35`).
> - **The program is now in PHASE 2 (active build)** — see `PHASE_2_PLAN.md` (12 decisions locked 2026-06-02).
>   Shipped: customer flags / type-defaults / metrics / credit-warn, invoice-intelligence panel, SO dashboard
>   metrics, lost-sales. In flight: SO metric-strip render, scraper-code removal, §5.4 core dashboard,
>   `credit_status` SO/invoice seam. Vendor-catalog integration **removed from ERP scope** (P2-D9 — the
>   standalone PAI Info tool feeds Shopify; a narrow enrichment-sync of cross-refs + CPL/ESN onto existing
>   products is the only catalog item left). **§10's queue below is Phase-1-era — Phase 2's queue is `PHASE_2_PLAN.md §8`.** QBO (Phase 1B) was gated as of 06-02 — **now BUILT 06-04 (see the banner above).**
>
> *Everything below this line is historical record (2026-05-29 → 06-02) — kept for context; not a to-do list.*

> **History — superseded by the ✅ banner above (kept for the record, not a to-do list).** The road to
> sign-off ran through four status-refresher passes; every gate they tracked has since landed:
> - **2026-05-29** — reconciled a badly-stale "not built" list: PO receive, 3-way match, SO→Invoice,
>   finalize/lock/void, core/warranty/RA state machines, payments, credit memos, reports and PDFs were
>   all already implemented + routed (Workflow Series 1–5, 125 tests). Lesson: trust the code, not old to-do text.
> - **2026-05-30** — owner screen-by-screen test caught two live core-loop regressions the 125 tests
>   missed (SO add-line `Product.name` phantom; PO-receive `SOLineStatus` `NameError`). Both fixed +
>   regression-guarded (`3a700fd` · `e1d632d` · `634d056`); coverage now exercises SO-linked PO lines.
> - **2026-05-31** — both spines proven green end-to-end (`tests/test_e2e_flows.py`; 477→545 non-visual
>   tests) and the final 1A gates closed: cost-variance 3-way (`c36769c`), O2-enforce (`07f7747`/`c4f10f9`),
>   O3 backup (`619a156`), O4/O5/O6, route cleanup (`331a872`), quote-flow + Save-Standard clusters.
> - **2026-06-02** — the last gate, the owner's hand-run acceptance of both spines, passed → **signed off**.
>
> One cosmetic nit logged along the way remains open (non-blocking): the quote PDF doesn't print part #s —
> `quotes/print.html` references `line.product.part_number` (real attr is `ProductVendorSource.vendor_part_number`).

### 9.0 — What was on this list and is now DONE ✅ (do not rebuild)

DB recreate · inline quick-create slide-overs (customer/product/vendor) · global Ctrl+K search ·
customer balance mini-panel on quote · PO receive → inventory + moving-avg cost · 3-way match
(receipt → vendor bill → discrepancy resolution) · SO deposit collection (Full/Deposit/None) ·
SO→Invoice (`fulfill_and_invoice`) · invoice finalize / lock / void / apply-credit · payment
record / reverse / NSF · full core lifecycle · Return Authorization workflow · warranty claim
state machine · credit memos + vendor credits · statements · in-app notifications · quote
duplicate + reactivate · customer import · report suite (9 reports) · dashboard metrics
(data-connected) · PO / SO / Core-slip / VCR / RA / Warranty print templates + `/pdf` routes.

**Landed since (Phase-1 hardening + Phase-2 foundation, 2026-06-02):** O2 user attribution + login
enforcement (`07f7747`) · bookkeeper user seed · backup restore admin-gate · `/account` password change
+ default-pw warning · cost-variance 3-way gate (`c36769c`/`bd65c69`) · O6 card surcharge (`d098176`) ·
After-Sale core-return from invoice (`61eaace`/`ab71243`) · customer flags / type-defaults / metrics /
credit-warn · invoice-intelligence panel · SO dashboard metrics + PO-rollup · structured lost-sales
(won/lost reasons) · quote-badge EXPIRED fix (`0549b35`).

**Landed since (QBO 1B + Phase-2 UI wave, 2026-06-03 → 06-04):** **QBO Phase-1B** — OAuth2 +
REST client + accounting-summary invoice push + bulk sync + Settings/list/workspace UI
(`3873e0e`/`e636760`/`e09a5c9`, owner-tested vs live sandbox) · **§7.2 enrichment sync** (`370820b`) ·
invoice-list **QBO status column + bulk push + sortable/sticky headers** · **unified customer Timeline
tab** · sortable+sticky on customers/quotes/payments · **PDF branding** (formatted phone + Terms + one
shared company dict across every print, `d8f33fa`) · customer **Acct # + 4-state Status + Timeline-first
tabs** · dynamic customer preview dock · **products F2 shortcut + prominent margin** · cost-bracket
**pricing grid** · tabbed Settings · quotes-list Open/Print/Email + follow-up colors. 966 tests green.
**Remaining UI consumes (Backend seams in @`9f50216`):** quote-workspace always-visible actions +
intelligence render, dashboard Top-Customers/Follow-Ups widgets, Prepared-By print render.

### 9.1 — Genuinely not built / deferred (BACKEND)

| Feature | Status | Notes |
|---|---|---|
| QBO OAuth + **invoice** push | **✅ BUILT 2026-06-04** | OAuth2 + REST client + accounting-summary invoice push + bulk sync + Settings/invoice-list/workspace UI; 17+ tests; owner-tested against the live sandbox. Fails-soft — never touches the money path. **Still deferred within 1B:** payments / vendor-bills / credit-memos push; Fernet token encryption; AST-tax reconcile (`qbo_push_tax` kill-switch). |
| Vendor availability + ESN scrapers (PAI/HHP/ATL) | **REMOVED from ERP scope (P2-D9, 2026-06-02)** | The ERP never scrapes. `vendor_availability_service.py` / `esn_lookup_service.py` stubs + `scraper.py` models + scraper routes/seed are being deleted (Backend). Live pricing/catalog lives in the standalone PAI Info tool → Shopify. |
| Product enrichment sync (cross-refs + CPL/ESN) | **✅ BUILT (`370820b`)** | One-way sync from the scraper's CSV export onto stocked products (match `jaks_sku`→`products.sku`; never creates products / touches cost/sell). `ProductApplication` model + `ProductEnrichmentService` + `POST /products/enrich-sync` + UI trigger; 9 tests. Spec: `PHASE_2_PLAN.md` §7.2. |
| Real email/SMS send | NullProvider only | `MessagingService` logs to `communication_log` (`logged_only`); SMTP/M365/Twilio providers are Phase 2. |
| Server-side PDF rendering | Falls back to browser print | WeasyPrint v68.1 installed but GTK/Pango missing on Windows → `?auto=1` browser print. Install GTK to enable true PDF (no code change). |

### 9.2 — The real Phase-1 gap: UI maturity rollout 🟡

> **Full system audit (workflows, UI, security, go-live grades):** see **§16 ERP System Audit Report Card** (2026-05-31).

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

> **✅ SUPERSEDED 2026-06-02 — this entire Phase-1 queue is DONE.** Sprints 1–5 below (the original
> build order) and the 05-31 "current" items 1–5 all landed: cost-variance 3-way gate (`c36769c`/`bd65c69`),
> O2-enforce (`07f7747`), O6 surcharge (`d098176`), Cores #16 / Returns #17 queues, CSV import phone/email
> fix, and the owner's end-to-end acceptance of both spines — **signed off** (`PHASE_1_TEST_PLAN.md` §13).
> The only Phase-1 carry-over is the **operational** data-safety cutover (one real backup→restore + set a
> strong admin pw at `/account`) — not code.
>
> **➡️ The live build queue is now `PHASE_2_PLAN.md` §8** (12 decisions locked 2026-06-02; build order
> starts at Customer Notes+Flags). Everything below this line is Phase-1 history — kept for the record,
> not a to-do list.

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

- [ ] **2-user login ENFORCED on production routes; every invoice / payment / adjustment is attributed to the signed-in user** *(O2 — mechanism shipped `90245d0`; owner RULED enforce 2026-05-31. Today still OPT-IN, falling back to user 1 at `deps.py:20` — Backend to add the session guard.)*
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
- [~] *(Phase 1B — not a 1A go-live blocker)* Wife pushes **invoices** to QBO with one click ✅ **built + owner-tested 2026-06-04** (single + bulk); payments / vendor-bills / credit-memos still to build
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

## 16. ERP System Audit Report Card

*Added: 2026-05-31 — comprehensive audit of workflows, UI, code health, features, security, and Phase 1A go-live readiness. Use this section to prioritize lanes; reconcile §9 bullets when code changes.*

**Audit sources:** codebase (`app/routers/`, `app/services/`, templates), `JAKS_UI_Change_Plan.md` §6/§9, `TESTING_FEEDBACK.md`, automated test suite (~330+ `def test_*` across 36+ files).

**Standing rule reminder:** Financial integrity rule (header) requires visible error banners — broad `except Exception` → redirect on money routes is a known violation of spirit if not letter.

### 16.1 — Overall GPA: **B+** _(2026-06-02 — up from B−; Phase 1A go-live readiness now A, signed off)_

| Lens | Grade | Summary |
|---|---|---|
| Backend / workflows | **A−** | Core ERP paths built, routed, and tested |
| Daily-use proof (owner) | **A−** | Owner-run Suites B+D all green (`PHASE_1_TEST_PLAN.md` §13) |
| UI consistency / polish | **B−** | Main lists L2; detail/report/dashboard L1 |
| Security (2-user local LAN) | **B** | Login enforced + O2 attribution done; CSRF = SameSite-Lax + waiver; full RBAC is Phase 2 |
| Integrations (QBO, scrapers, email) | **B−** _(2026-06-04)_ | QBO Phase-1B invoice push BUILT + owner-tested vs live sandbox; scrapers removed by design; email still Phase 2 |
| Phase 1A go-live readiness | **A — SIGNED OFF 2026-06-02** | Owner-signed (`PHASE_1_TEST_PLAN.md` §13); both spines proven (Suites B+D); 801 tests green |

**Verdict (updated 2026-06-02):** **Phase 1A signed off** — both spines proven end-to-end via owner-run Suites B+D + 801 green tests; P0/P1 hardening (O2 attribution+enforce, backup admin-gate, cost-variance gate, CSRF waiver, `/account` pw) complete. Now in active **Phase 2** build (`PHASE_2_PLAN.md`). The lens grades below are the 2026-05-31 snapshot — still fair for UI/integration polish, but readiness and owner-proof have since moved to A.

### 16.2 — Report card by indicator

#### Sales workflow (Quote → SO → Invoice → Payment) — **B+**

| Built ✅ | Gap ❌ / ⚠️ |
|---|---|
| Quote workspace: autosave, line roles, warranty tiers, upgrades | Owner has not signed off quote/SO/invoice add-line on current build |
| Quote → SO (OOS) and Quote → Invoice (in-stock) | Customer pre-fill from customer detail still flaky (B4) |
| SO deposits: Full / Deposit / None | SO workspace not full L3 |
| Invoice finalize, lock, void, credit memo | Core-slip auto-popup deferred (O7 → Phase 2) |
| Payments: record, allocate, reverse, NSF | O6 surcharge: partial — see §16.4 |

**Code disruption history:** SO `Product.name`, PO receive `SOLineStatus`, invoice search phantom attrs — fixed with regression tests (`test_regression_b1_b2.py`, `test_e2e_flows.py`). Lesson: router attribute typos + bare `except` hide production failures.

#### Purchasing (PO → Receive → 3-way match → Vendor bill) — **A−**

| Built ✅ | Gap ❌ |
|---|---|
| PO create, partial/full receive, inventory + moving-avg cost | PO workspace L3 on **HOLD** (#11 in UI plan) |
| 3-way match + discrepancy resolution | `vendors.py` has no service layer — direct ORM |
| PO / Receiving / Match queues (QB2) | Receiving-slip print optional (O8) |

#### Inventory & products — **B**

| Built ✅ | Gap ❌ |
|---|---|
| Products list = L2 reference | Product Detail L1, **HOLD** (#10) |
| Inventory via controlled events only | Serial number UI, kit BOM UI not built |
| Cross-refs, suggested sells | Enrichment scrapers Phase 2 stubs |
| Markup from Settings (O5 partial) | `Product.selling_price` still 30% fallback when `markup_pct` NULL; no per-category override |

#### Cores, returns, warranty, vendor returns — **B**

| Built ✅ | Gap ❌ |
|---|---|
| Core lifecycle + print/PDF (slip, VCR) | Core-slip popup not auto-triggered at invoice finalize |
| RA workflow + credit memos | Returns / warranty workspaces L1 (`tbl-*`, `confirm()`) |
| Warranty state machine + tests | Vendor Returns list = only major list still on `tbl-*` |
| Cores + Warranty queue boards (QB2) | Returns Queue (#17) not built |

#### Reporting & dashboard — **C+**

| Built ✅ | Gap ❌ |
|---|---|
| 9 reports, server-side math, `tests/test_reports.py` | All reports `tbl-*` — wife-facing polish weak |
| Dashboard KPIs (SOs, follow-ups, overdue, cores, research) | Dashboard legacy tables, not L2 dock pattern |
| Customer statements + print/PDF | Statement screen polish deferred |

#### UI / UX maturity — **B−**

| Level | Screens |
|---|---|
| L2 ✅ | Products, PO, Invoices, Customers, SO, Vendors, Payments; PO Receiving/Match queues |
| L2 ⚠️ unverified | Quotes list, Returns list — pass recorded during HTTP 500 era; re-test post-`b514196` |
| L3 ✅ | Invoice workspace |
| L3 ⚠️ | Quote workspace — owner re-test required |
| HOLD | Product detail (#10), PO workspace (#11) |
| L1 legacy | All detail pages, dashboard, all reports, returns/warranty/vendor-return workspaces |

**Consistency gaps:** Alpine modals (invoice) vs `window.confirm()` (returns, warranty, quote convert); `JAKS_UI_Change_Plan.md` §1 mapping stale vs §6 rollout.

#### Data integrity & financial controls — **B+**

**Strengths:** Service layer on money paths; `invoice_totals.py`; audit logging; invoice lock; payment caps; 3-way match cost-variance gate (`po_service.py`, `tests/test_bill_cost_variance.py`); optimistic locking.

**Weaknesses:**

| Issue | Location | Impact |
|---|---|---|
| ~63 `except Exception` on routers | invoices, PO, cores, payments, reports, etc. | Errors → redirect; masks bugs |
| `CURRENT_USER_ID = 1` hardcoded | `app/routers/customers.py:36` | CRM/call-log audit always user 1 |
| Direct ORM writes | customers, vendors, products bulk | Some paths skip audit |
| Inline `ALTER TABLE` migrations | `app/database.py` | No Alembic — OK for local SQLite |
| `qbo_sync_status = PENDING` with no push | models | Misleading until Phase 1B |

#### Security & access control — **C+**

| Present ✅ | Missing ❌ |
|---|---|
| PBKDF2 passwords, signed session cookie (`app/auth.py`) | **No CSRF** on POST forms |
| Global login middleware (`app/main.py` L48–64) | **No role gates** on backup restore, admin, settings |
| `JAKS_SKIP_AUTH` for tests only | **Any logged-in user can POST `/admin/backup/restore`** |
| FastAPI docs disabled | Default password `"admin"` if `JAKS_ADMIN_PASSWORD` unset |
| `_ROLE_PERMISSIONS` + `assert_can()` in services | Used on ~9 services only; customers router ignores session user |
| | Second user (wife) not seeded — manual `users` row required |

**Local LAN, 2 trusted users:** acceptable with P1 fixes. **Internet-exposed:** not acceptable without CSRF, role gates, strong defaults.

#### Testing & QA — **B**

| Strong ✅ | Weak ❌ |
|---|---|
| 966 tests pass (E2E, cores, PO match, reports, auth, backup, payments, QBO, enrichment) | 6 non-functional reds remain (5 cosmetic `ui_lint` + 1 brittle W-4) |
| `tests/test_e2e_flows.py` (`@pytest.mark.acceptance`) | No full vendor CRUD / settings / dashboard tests |
| Regression: B1/B2, bill variance, O6 (`test_o6_surcharge.py`), CSV import | Visual regression known unstable |
| UI lint (`test_ui_lint.py`) — report-only gate | Smoke not CI-gated |

#### Integrations — **B−** *(2026-06-04 — QBO Phase-1B invoice push built + owner-tested)*

| System | Status |
|---|---|
| QBO (Phase 1B) | **BUILT 2026-06-04** — OAuth2 + REST client + accounting-summary invoice push + bulk sync + Settings/list/workspace UI; owner-tested vs live sandbox. Payments/vendor-bills/credit-memos + Fernet encryption still deferred within 1B |
| Email/SMS | `NullMessagingProvider` — logs to `communication_log` only |
| PAI/HHP/ATL scrapers | `vendor_availability_service.py` → `NotImplementedError` |
| ESN lookup | `esn_lookup_service.py` → `NotImplementedError` |
| Shopify / eBay / TaxJar | Phase 2/3 |
| Server PDF | WeasyPrint → browser print on Windows without GTK |

### 16.3 — Phase 1A owner items (O1–O9) — audit status

| ID | Requirement | Audit status |
|---|---|---|
| O1 | 1A without QBO / 1B = QBO | ✅ Locked |
| O2 | 2-user login + attribution | ⚠️ Middleware ✅; `customers.py` still `CURRENT_USER_ID = 1`; wife user not seeded |
| O3 | Auto backup + tested restore | ✅ `backup_service`, startup hook, `tests/test_backup_restore.py`; restore not admin-gated |
| O4 | Vendor contacts | ✅ `vendors.py` + `tests/test_vendor_contacts.py` |
| O5 | Markup in Settings | ⚠️ Global `default_markup_pct` ✅; 30% fallback + no category override |
| O6 | Per-customer card surcharge + invoice override | ⚠️ `card_surcharge_pct` + tests (`test_o6_surcharge.py`); verify UI end-to-end |
| O7 | Core-slip popup | Deferred Phase 2 |
| O8 | Receiving-slip print | Optional placeholder |
| O9 | QA triage (research UI, core edge bugs) | ⬜ Open |

### 16.4 — Critical missing areas (priority queue)

**P0 — Trust in daily use**
1. Owner functional test pass — fill `TESTING_FEEDBACK.md` on current build
2. Fix audit attribution — replace `CURRENT_USER_ID = 1` with `get_current_user_id` in `customers.py`
3. Seed second user (wife, `BOOKKEEPING` role) or document creation steps
4. Re-test Quotes / Quote workspace / Returns after latest fixes (§8G gate)

**P1 — Before irreplaceable production data**
5. Gate `/admin/backup/restore` behind admin permission
6. Add CSRF tokens on HTMX POST forms
7. Reduce bare `except Exception` on money routes → log + visible error banner
8. Enforce strong admin password via env on first run

**P2 — Sustainable polish**
9. Lift HOLD: Product Detail (#10), PO workspace (#11) after P0 clears
10. Port Vendor Returns list to L2 (last unported operational list)
11. Reports + dashboard off `tbl-*` (wife-facing: AR Aging, Statements)
12. Replace `window.confirm()` on returns/warranty with Alpine modals

**P3 — After 1A stable**
13. ~~Phase 1B: QBO OAuth + push~~ — ✅ **DONE 2026-06-04** (invoice push built + owner-tested; payments/vendor-bills/credit-memos still within 1B)
14. Phase 2: scrapers, real email, Shopify, TaxJar
15. Phase 3: eBay, multi-state tax, ESN live, serial/kit UI

### 16.5 — Code disruption hotspots

| Area | Issue | Severity |
|---|---|---|
| `customers.py:36` | `CURRENT_USER_ID = 1` | High — wrong audit trail for wife |
| Money routers | ~63× `except Exception` → silent redirect | High — violates error-banner rule |
| `vendors.py` | No service layer | Medium — no audit on vendor CRUD |
| `MASTER_PLAN.md` §9 | Some bullets stale vs code (O2 enforce, cost variance) | Low — planning confusion |
| `JAKS_UI_Change_Plan.md` §1 | Stale L1 labels for completed L2 lists | Low — builder confusion |
| Inline migrations | No Alembic | Medium long-term |
| QBO fields | `PENDING` never synced | Low until 1B |

### 16.6 — Go-live readiness snapshot

```
                    GO-LIVE (Phase 1A) — ✅ SIGNED OFF 2026-06-02
    Backend workflows     ██████████  100%  both spines owner-proven (Suites B+D)
    Automated tests       ██████████  ~99%  801 pass; only 6 non-functional reds
    Owner acceptance      ██████████  100%  Keith signed PHASE_1_TEST_PLAN §13
    Security hardening    █████████░  ~90%  O2 attribution+enforce, backup gate, CSRF waiver, /account
    UI L2/L3 (main paths) ████████░░  ~80%  Phase-2 chips/panels rolled out; long-tail polish ongoing
    §11 checklist signed  ██████████  100%  signed (QBO line = 1B, not a 1A gate)
    Integrations          █████░░░░░  ~55%  QBO Phase-1B invoice push BUILT + owner-tested; email Phase 2
```

**Honest assessment (2026-06-02):** **Phase 1A is signed off** — both revenue/inventory spines complete end-to-end with correct data, owner-validated (Suites B+D). The only pre-real-data cutover items are operational, not code: perform one real backup→restore and set a strong admin password. The program is now in **Phase 2** (`PHASE_2_PLAN.md`) — customer/SO/invoice intelligence is shipping; **no broken core workflow gates it.**

**Related docs:** `JAKS_UI_Change_Plan.md` (UI rollout + §9 functional gate), `TESTING_FEEDBACK.md` (owner test sheet), `BACKEND_IMPLEMENTATION_PLAN.md` (backend letter-phases A–O).

---

## 17. What's Left to Go-Live — Independent Audit + Remediation (2026-06-05)

> **Reality check on §16.6.** An independent production-readiness audit (multi-agent
> code sweep, owner-verified at `file:line`) graded the app **68 → 73/100 after same-day
> fixes — strong engine, NOT deployable as-is.** The §16 "✅ SIGNED OFF / A−" is
> over-optimistic: the R3 acceptance sheet (`Testing Feedback/TESTING_FEEDBACK_R3_GOLIVE.docx`)
> has **blank result columns for Cores, Returns, Warranty, Vendor Returns, Reports, and all
> five end-to-end flows** — they were never owner-tested. The sign-off rests on the two spines
> (Suites B+D) + automated tests, not the whole app. **This section is the authoritative
> remaining punch list and supersedes the §16.6 snapshot.**

### 17.1 — Fixed 2026-06-05 ✅ (commit `acf3c34`, branch `backend/workflow-series-3`; 970 tests green)

- **Authorization holes closed:** `/admin/demo/reset`, `POST /settings/`, `/admin/backup/run`
  now `require_admin` (were reachable by any logged-in user / forged POST → DB wipe / pricing
  + QBO-token rewrite). `demo.py`, `settings.py`, `backup.py`.
- **Double-credit closed:** `RAService.close_ra` + `WarrantyService.credit_customer` now guard on
  an existing credit memo (`credit_memo.ra_id`/`warranty_claim_id`) so a crash between the memo
  commit and the status flip can't issue a second customer credit. + regression test in
  `tests/test_returns_ra.py`.
- **Invoice-number gaps closed:** `bump_counter` now `flush`es instead of `commit`ing, so a
  rolled-back document rolls back its number ("invoice numbers are sacred"). `settings_utils.py`.
- **Default-password security banner** on the dashboard (auto-clears once changed).
- **UX quick wins:** quote keyboard loop restored (`id=line-search-input`), Products Export CSV
  wired + export tab-slugs aligned to the list, list-search de-dash on quote/SO/PO/invoice numbers,
  "On Hand"/"Unit Price" headers, autofocus on payment + vendor-return forms, dead-template banners.
- **WITHDRAWN — customer-discount header seed:** would have *double-discounted* every quote
  (line builder already applies `customer.discount_pct` per `quote_service.py:118-122`). Not a fix.

### 17.2 — GO-LIVE BLOCKERS still open (close before real production data)

| Item | Sev | Effort | Notes |
|---|---|---|---|
| **Owner test pass** — Cores, Returns, Warranty, Vendor Returns, Reports, 5 E2E flows + one backup→restore drill | **High** | M | Never owner-tested. Biggest confidence gap. Fill the R3 sheet with real data. |
| **CSRF** on all state-changing POSTs | **High** | M | No CSRF anywhere (`main.py:58`). All-or-nothing app-wide pass. Catastrophic routes now admin-gated, so exposure is reduced but not eliminated. |
| **Tier pricing: fix or kill** | **High** | M | `pricing_tier` is stored + shown but never affects price (`pricing_service.py:82`). Silent mispricing of fleet/dealer accounts. |
| Encrypt QBO OAuth tokens at rest | Med | M | Plaintext in `data/jaks.db` (`qbo_client.py:133`). **Needs the `cryptography` dependency (not currently installed).** |
| Force admin password change on first login | Med | S | Dashboard banner (17.1) is the interim; hard redirect-on-login still to do. |
| `demo-reset` env-guard (refuse on the prod instance) | Low | S | Belt-and-suspenders atop the new admin gate. |
| Seed bookkeeper (wife) user + set strong admin/bookkeeper passwords | High | S | Operational §11 cutover step. |
| `products/list.html` export-button wiring | Low | S | Left uncommitted (another lane has the file open); the route half is committed. |

### 17.3 — Counter-readiness (Tier 2 — a real parts counter needs these)

| Feature | Sev | Effort | Notes |
|---|---|---|---|
| Counter receipt + pick ticket + packing slip | High | M | Only formal full-page invoice PDFs exist today. |
| Customer purchase-history + one-click reorder | High | M | History is doc-level, not part-level; no "same as last time". |
| Daily close / cash-drawer / payment-method summary | High | M | No EOD report; dashboard shows only a single "today's payments" sum. |
| Seeded CASH/Counter customer + fast cash-sale path | Med | L | `customer_id` is `NOT NULL` on quote/SO/invoice — no anonymous walk-in sale. |

### 17.4 — Polish (Tier 3 — after 1A stable)

| Item | Sev | Effort | Notes |
|---|---|---|---|
| Post-sale forms (returns/warranty/payment) repopulate-on-error + typeahead pickers | High | M | Currently wipe all typed data on a bad submit; product picker is a full-catalog `<select>`. |
| Standardize ~22 native `confirm()`/`alert()` → `jakConfirm`; delete the duplicate PO-workspace modal | Med | M | Migration guide already in `macros/confirm_modal.html`. |
| Column sort on SO / PO / Vendor lists | Med | S | `sortable_th` macro + `apply_sort` already exist (used by invoices/quotes/payments). |
| `vendor_returns` list → L2 (last raw `tbl-*` operational list) | Med | M | No search/filter/sort today. |
| Legacy `.tbl-*` tables (all reports + detail subtables) — migrate or formally un-ban | Med | L | Currently banned by governance but still in use. |
| Revenue-path friction: quote pre-fill parity (skip the Create-Quote screen), backorder→PO inline vendor picker, "receive all" on the receiving-queue row | Med | S–M | See audit §4 R2/R3/R5. |
| Alembic migrations (replace inline `ALTER TABLE`); broaden tests (vendor CRUD, settings, dashboard) | Med | M | Durability + coverage. |

### 17.5 — Path to 100

- **73 → ~82 "Deployable":** finish 17.2 (mostly small) + the owner test pass.
- **82 → ~92 "Counter-ready":** 17.3 features + tier-pricing decision.
- **92 → 100 "Proven":** 17.4 polish + **two weeks of real daily use with zero data-integrity surprises** (the last points are earned in production, not coded). This is the §11 "no data integrity issues in 2 weeks" gate.

---

*This document is the single source of truth for all JAKS Inventory build decisions.*
*Update it as decisions change. All other planning documents are superseded.*
*Last updated: 2026-06-05 — §17 go-live remaining punch list added (independent audit + acf3c34 remediation); supersedes the §16.6 "signed off" snapshot.*
