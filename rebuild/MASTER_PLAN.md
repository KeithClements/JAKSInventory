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

> **🟢 STRATEGIC REVIEW — 2026-05-31 (status-refresher). BOTH CORE SPINES PROVEN GREEN.**
> Verified against the test suite (**477 passing**, non-visual) — not self-reports:
> - **Quote→SO→Invoice→Payment** and **PO→Receive→Inventory** both pass **end-to-end** in
>   `tests/test_e2e_flows.py` (in-stock sale→paid; OOS→SO→deposit→linked-PO-receive→partial invoice;
>   PO receive + moving-avg cost). One-click line-add (§8H shared adder) is live in all 4 workspaces.
> - **Cores / Returns / Warranty / Vendor-Returns / Reports** now functionally tested — 52 behaviour
>   tests (`9d0ced2`), all passing.
> - **O3** automatic SQLite backup/restore shipped (`619a156`). Warranty Queue **#15** QB2 shipped (`e93cef9`).
>
> **Remaining for Phase-1A go-live — RECONCILED 2026-05-31 (PM, external-review ground-truth).**
> Most of the earlier (a)–(e) list has since LANDED. Verified this pass against code + targeted tests:
> - ~~(a) Cores BUG-2/BUG-4~~ ✅ **DONE** `e119c19` — `tests/test_cores_lifecycle.py` 16 green
>   (`credit_issued_at` idempotency stamp + skip location-movement when `location_id is None`).
> - ~~(c) O3 restore acceptance~~ ✅ **DONE** — `tests/test_backup_restore.py` green (`619a156`).
> - ~~(d) `…/_/product-search` route cleanup~~ ✅ **DONE** `331a872` (4 endpoints deleted, 224 routes).
> - ~~(e) O4 vendor contacts~~ ✅ `c17f2b6` · ~~O5 markup→settings~~ ✅ `7f69572`. Search dash/case
>   normalization ✅ live + tested (`normalize_part`, `tests/test_line_item_builder.py` 16 green).
>
> **⚡ SINCE THE PM BANNER (2026-05-31, late status-refresher — 545 non-visual tests green):** two of the
> items below have LANDED — **(2) cost-variance** ✅ `c36769c`+`c5b49ac` (3-way match now flags DISCREPANCY
> on unit-cost variance, qty-only gate closed, QA-guarded); **(3) O2-enforce** ✅ `07f7747`+`c4f10f9`
> (auth middleware: unauthenticated production requests → `/login`, HTMX gets `HX-Redirect`). Also landed:
> void-balance fix `52a9fdd`, the schema-drift CI gate, quote-flow cluster Bugs 1/3/5/6/8 (`e93048d`/`d9ee57d`),
> Save Standard v2 (`59e7fa5`), Cores #16 (`c6468af`) + Returns #17 (`6416a9d`). **NEW cosmetic bug:** quote PDF
> never prints part #s — `quotes/print.html:327/413/458` reference `line.product.part_number` (does not exist;
> the real attr is `ProductVendorSource.vendor_part_number`), so the `{% if %}` is always falsy. Non-blocking.
> **Net: the only true remaining go-live gate is (1) the owner's hand-run §8 acceptance.** Code-side, 1A is
> essentially complete; (5) O6 surcharge is in-flight, Activity Log (contract `dd28051`) + beta sandbox are fast-follow.
>
> **What ACTUALLY remains for 1A go-live (priority order):**
> - **(1) OWNER END-TO-END ACCEPTANCE of both spines — the real gate.** Code + `tests/test_e2e_flows.py`
>   are green, but Keith has **never personally run** the §8 cross-workflow rows (sale→paid; OOS→SO→
>   receive→invoice; core→credit) on a fresh build — the owner test sheet's §8 is entirely unmarked.
>   This is a *confidence* gate, not a code gap. Schedule a guided owner pass.
> - **(2) Vendor-bill COST-ONLY mismatch auto-approves — money-correctness bug (Backend).** A bill with
>   exact qty but wrong unit cost (e.g. 10@$110 vs 10 received @$100) sets `status=APPROVED` and queues
>   QBO PENDING; only qty variance is flagged. `po_service.py:632-633` + model `has_discrepancy`
>   (`purchase_order.py:269-272`) test only `qty_billed > qty_received`, never `unit_cost`. The cost
>   variance IS computed (`compute_match_line` cost_variance flag) but is not a hard approval gate. Unguarded by tests.
> - **(3) O2 login — ENFORCE (owner ruled 2026-05-31).** Mechanism shipped (`90245d0`: pbkdf2 + signed
>   cookie, `/login`,`/logout`) but today opt-in: `deps.py:20` `DEFAULT_USER_ID=1`, `deps.py:23-34`
>   silently falls back to user 1, no guard. **Backend ticket:** add a global session dependency /
>   middleware so production routes redirect to `/login` when there's no valid session, and make the
>   `deps.py` fallback raise/redirect instead of defaulting to user 1 (keep a test/dev bypass). +QA test.
> - **(4) Owner-flagged data/money bugs to confirm + fix:** Customer CSV/Excel import drops phone+email
>   (`§1.2h` — data integrity); card surcharge can't be un-selected once chosen (`§1.9e` — money-path UX).
>   Re-confirm B5 vendor-reactivate / B6 products-bulk landed cleanly (`82f7495`).
> - **(5)** O6 customer card-surcharge default+override (Backend, money path, needs migration);
>   Cores Queue **#16** / Returns Queue **#17** (UI-Builder, copy `receiving_queue.html`).
>
> **Cosmetic / NOT go-live blockers** (confirmed this pass — do NOT jump the queue): UI-lint gate red is
> **5 cosmetic failures / 167 pass** (628 `tbl-*`, 54 inline x-transition, color+stripe allowlist — the
> lint module's own docstring says "report only"); `tbl-*` still defined in `base.html:133` (~28 screens
> still L1); login page uses CDN Tailwind in `auth.py:44` (trivial fix: link local `/static/css/app.css`).
> The **"repo mojibake" claim is REFUTED** — a raw-byte scan found zero; the bytes are valid UTF-8 (§ /
> em-dash), i.e. a local terminal codepage display issue, not repo corruption. Visual-regression pixel
> diffs remain known-unstable (mutable-DB), **not** workflow failures.

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

> **⚠️ SUPERSEDED 2026-05-31 — Sprints 1–5 below are the *original* build order and are ALL DONE**
> (DB recreate, slide-overs, PO receive, Ctrl+K, invoice lock, SO→Invoice, 3-way match, core/RA/warranty
> state machines) — proven by the 477-test suite + `tests/test_e2e_flows.py`. Kept as history only.
>
> **CURRENT priority queue — RECONCILED 2026-05-31 (PM). The prior items 1–4 are now DONE**
> (Cores BUG-2/4 `e119c19` · O3 acceptance test · route cleanup `331a872` · O4 `c17f2b6` · O5 `7f69572`).
> Remaining, in dependency order:
> 1. **Owner end-to-end acceptance pass of both spines** — Owner + QA. Run the §8 cross-workflow rows on a
>    fresh build (sale→paid; OOS→SO→receive→invoice; core→credit). The real go-live gate: code is green,
>    owner confidence is not yet established.
> 2. **Vendor-bill COST-ONLY mismatch → flag DISCREPANCY (stop auto-approving)** — Backend; money bug.
>    `po_service.py:632-633` + `has_discrepancy` (`purchase_order.py:269-272`) must also test `unit_cost`. +QA guard.
> 3. **O2 login ENFORCEMENT** — owner ruled ENFORCE (2026-05-31). Backend adds a global session guard
>    (redirect to /login when no valid session; fallback raises instead of defaulting to user 1). +QA test.
> 4. **Owner-flagged bugs** — CSV import phone+email drop · card-surcharge can't-unselect — Backend/UI; +confirm B5/B6.
> 5. **O6 customer surcharge default+override** (Backend, needs migration) · **Cores Queue #16 · Returns Queue #17** (UI-Builder).
> 6. **UI maturity rollout (§9.2)** — the long-tail L1→L2 work; never ahead of items 1–4.

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

## 16. ERP System Audit Report Card

*Added: 2026-05-31 — comprehensive audit of workflows, UI, code health, features, security, and Phase 1A go-live readiness. Use this section to prioritize lanes; reconcile §9 bullets when code changes.*

**Audit sources:** codebase (`app/routers/`, `app/services/`, templates), `JAKS_UI_Change_Plan.md` §6/§9, `TESTING_FEEDBACK.md`, automated test suite (~330+ `def test_*` across 36+ files).

**Standing rule reminder:** Financial integrity rule (header) requires visible error banners — broad `except Exception` → redirect on money routes is a known violation of spirit if not letter.

### 16.1 — Overall GPA: **B−**

| Lens | Grade | Summary |
|---|---|---|
| Backend / workflows | **A−** | Core ERP paths built, routed, and tested |
| Daily-use proof (owner) | **C** | `TESTING_FEEDBACK.md` still entirely ⬜ |
| UI consistency / polish | **B−** | Main lists L2; detail/report/dashboard L1 |
| Security (2-user local LAN) | **C+** | Login enforced; CSRF, roles, attribution gaps |
| Integrations (QBO, scrapers, email) | **D+** | Deferred by design — not broken |
| Phase 1A go-live readiness | **C+** | Close; not owner-signed |

**Verdict:** Strong rebuild — not yet certified for unsupervised daily shop use without owner functional test pass + P0/P1 hardening.

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
| ~330+ tests: E2E, cores, PO match, reports, auth, backup | `TESTING_FEEDBACK.md` entirely ⬜ — no owner sign-off |
| `tests/test_e2e_flows.py` (`@pytest.mark.acceptance`) | No full vendor CRUD / settings / dashboard tests |
| Regression: B1/B2, bill variance, O6 (`test_o6_surcharge.py`), CSV import | Visual regression known unstable |
| UI lint (`test_ui_lint.py`) — report-only gate | Smoke not CI-gated |

#### Integrations — **D+** *(deferred by design)*

| System | Status |
|---|---|
| QBO (Phase 1B) | Scaffold only — `qbo_*` fields, no OAuth/API client |
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
13. Phase 1B: QBO OAuth + push
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
                    GO-LIVE (Phase 1A)
    Backend workflows     ████████░░  ~85%
    Automated tests       ███████░░░  ~78%
    Owner acceptance      ██░░░░░░░░  ~15%
    UI L2/L3 (main paths) ██████░░░░  ~65%
    Security hardening    ████░░░░░░  ~50%
    Integrations          █░░░░░░░░░  ~10%
    §11 checklist signed  ███░░░░░░░  ~35%
```

**Honest assessment:** Backend workflow completeness is high; remaining 1A risk is **product/ops** (owner walkthrough, O2 attribution, security on backup, UI polish on wife-facing surfaces) — estimated **2–4 focused weeks**, not months of new backend.

**Related docs:** `JAKS_UI_Change_Plan.md` (UI rollout + §9 functional gate), `TESTING_FEEDBACK.md` (owner test sheet), `BACKEND_IMPLEMENTATION_PLAN.md` (backend letter-phases A–O).

---

*This document is the single source of truth for all JAKS Inventory build decisions.*
*Update it as decisions change. All other planning documents are superseded.*
*Last updated: 2026-05-31 — §16 ERP audit report card added.*
