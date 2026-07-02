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
  Category Maintenance
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

> **✅ 2026-06-14 — STATUS REFRESHER. 8 of the 10 audit blockers below are closed; live testing now active on `Testing Feedback/TESTING_FEEDBACK_R4_GOLIVE_TRIAL.md`.**
> Ground-truthed against `HEAD` (`b721d19`) + working-tree edits. The catalog now has the **JAKS SKU scheme APPLIED at scale**: `select count(*) from products where part_seq is not null` = **27,508 of 27,509** — auto-mint + backfill ran, the 13k+ leak SKUs (`JAKS-PAI-#`) are **gone** (0 rows). **CDN-only JS is gone** (`base.html:24-26` self-hosts Alpine 3.14.9 / HTMX 1.9.12 / Chart 4.4.3, the §8 lib pin). **Suggested-sell chip tier-pricing bug FIXED** (`_line_row.html:484-488 / 503-508` explicitly omit price, route through `PricingService.sell_price_for_tier` — comments call out the audit fix). **Hardcoded `InvoiceService(db, 1)` audit user gone** (0 router matches). **AR aging bar keys consolidated** (`_balance_widget.html` now uses backend's `current/1_30/31_60/61_90/over_90`). **Credit-hold dual state synced** (`customer_service.py:155,186` syncs `customer_status` ↔ `CustomerFlag.CREDIT_HOLD`).
>
> **Of the 10 audit blockers — 8 closed, 1 mitigated, 1 partial:**
> - **#1 FK + child indexes ✅** · **#2 deposit-cancel orphan ✅** · **#3 reports COGS double-count ✅** (lines 229/346 are now docstring shapes, the line-type filter takes care of cores) · **#4 suggested-sell tier bypass ✅** · **#6 credit-hold dual state ✅** · **#7 hardcoded audit user ✅** · **#8 CDN libs ✅** · **#10 AR aging keys ✅**.
> - **#5 `Product.selling_price` 30% fallback ⚠️ mitigated** — the property is still 30% (`product.py:311`), but every callsite that prices a customer (search, CSV, line-add, pickers) routes through `PricingService.sell_price_for(product)` which respects the setting; the property docstring documents the workaround. Leave as-is unless the model property leaks into a customer-facing path.
> - **#9 PAI cost data 🚨 STILL ZERO** — `select count(*) from products where cost = 0 or cost is null` = **27,508 of 27,509**. The SKU half of #9 is done; the **cost half is unresolved** — every margin reads ~100% and inventory valuation reports ~$0. Until the owner runs the PAI Info → Pricing-Update import, do NOT trust dashboard margin / sales-by-product / inventory-valuation numbers. (This is data-ops, not a code fix — `scripts/full_import.py` is the route.)
>
> **In flight (uncommitted — DO NOT collide):**
> - **Auto-SKU + fast vendor part# entry on `/products/new`** (this session): `app/routers/products.py` (+169), `app/services/product_service.py` (+195), `app/templates/products/new.html` (+420), `tests/test_products_new_form.py` (NEW, 414 lines, **14/14 green**), `tests/test_regression_b1_b2.py` (+5). New endpoints `GET /products/classify-part` + `GET /products/twin-check`. POST writes Product + `ProductVendorSource(is_preferred=True)` + `CrossReference(VENDOR_ALT, ref_number=typed part#)` for search hot-path. Legacy callers (importer, quick-create) take the manual-SKU branch unchanged. E2E verified — owner can commit when satisfied.
> - 11 other files in the working-tree pile (`database.py`, `main.py`, `models/product.py`, `routers/quotes.py`, `services/quote_service.py`, `services/sales_order_service.py`, `services/search_service.py`, `base.html`, `templates/sales_orders/_header_actions.html`, `templates/invoices/_header_actions.html`, `services/search_index.py`) — separate work piles from the prior 2026-06-10..13 wave (Shopify sync, image supersession, search hardening); commit cadence is owner's call.
>
> **Live testing reality:** owner is filling in `Testing Feedback/TESTING_FEEDBACK_R4_GOLIVE_TRIAL.md` right now. The R4 trial sheet is the **5-lifecycle gate** (Purchasing spine A · Revenue in-stock B · Revenue backorder+deposit C · Cores D · A/R + statements E) + a 20-section screen-by-screen pass + the data-integrity spot checks. **No marks are filled in yet** — treat every workflow as "code says ready, owner-verification pending."
>
> *The 2026-06-07 audit banner below is retained as the prior milestone.*

> **🔍 2026-06-07 — FULL 16-SUBSYSTEM CODE AUDIT (16 auditors → adversarial risk verification → synthesis). Overall grade: C+. Verdict: "usable for daily parts ops AFTER a ~10-item fix list; QBO is Phase 1.1, not Phase 1." Full report: `STATUS_REPORT_2026-06-07.md`.**
> Ground-truthed against the working tree (incl. dirty/untracked WIP), not plan prose. The money spine (quote→SO→PO→receive→invoice→payment) runs end-to-end with atomic finalize + real-ledger void rollback — it will NOT corrupt a clean transaction. What holds the grade at C+ is a cluster of *silent* correctness defects + an unloaded cost/SKU data layer.
>
> **Test reality corrected:** `4 failed, 1120 passed, 55 skipped` (NOT "2 reds"). The 2 unlisted reds are NEW + real: `test_s18_classification` ×2 — the smart-import classifier is genuinely broken, not just unwired. (Other 2 = AR-aging WIP + brittle W-4 do-not-chase.)
>
> **Fix-before-Phase-1 blockers (verified):**
> 1. ✅ **LANDED 2026-06-07** — FK enforcement was OFF DB-wide + 4 child tables unindexed → `database.py` now sets `PRAGMA foreign_keys=ON` per connect + `_apply_index_migrations()` creates `ix_{invoice,quote,so,po}_lines_*`.
> 2. ✅ **LANDED 2026-06-07** — deposit-cancel orphaned money (resolution `<select>` outside the form + hardcoded `leave_open`) → `sales_orders/workspace.html` OPEN **and** HOLD cancel forms now bind `deposit_resolution` via Alpine.
> 3. ❌ Reports count CORE_CHARGE deposits as revenue+COGS (`report_service.py:229,346`) — every Sales-by-Customer/Product margin is wrong. *(file dirty — Backend lane.)*
> 4. ❌ Suggested-sell chips quote retail to tiered customers (`_line_row.html:484,503` hardcode `unit_price` into hx-vals, bypassing PricingService). *(file dirty — UI lane.)*
> 5. ❌ Product list/preview price uses hardcoded 30% (`product.py:300` `selling_price`) — disagrees with the quote's tier price.
> 6. ❌ Credit-hold dual state machine (`customer_status` vs `CustomerFlag.CREDIT_HOLD`, unsynced) — a held account can read "clear" at the counter.
> 7. ❌ Vendor/customer mutation+import routes have no role gate; `InvoiceService(db, 1)` hardcodes audit user (`invoices.py:104`).
> 8. ❌ CDN-only Alpine/HTMX/Chart.js (`base.html:16-18`) — self-host + pin before go-live.
> 9. ⏳ **DATA:** all 13k PAI parts `cost=0` (margin reads 100%, valuation ~$0) → run pricing-update `pai_cost`; no JAKS SKU until `scripts/backfill_sku_scheme.py --apply` is committed + run.
> 10. ❌ AR aging bar never renders (`_balance_widget.html` keys `d1_30/d90_plus` vs backend `1_30/over_90`) — **folded into the in-flight AR-aging WIP; leave to that lane.**
>
> **In-flight (dirty/untracked — DO NOT re-dispatch or collide):** Credit-memo issuance UI is BUILT-uncommitted (`credit_memos.py` router + templates + `test_credit_memo_routes.py`, wired `main.py:341-342`) → the audit's "no credit-memo UI" is **REFUTED**. Smart-import **Apply route exists** (`import_review.py` `POST /{batch}/apply` → `apply_approved`, wired `main.py:341`) → "dead-end" is **REFUTED**; the classifier is still red. AR-aging consolidation (`ar_aging_utils.py`) in progress.
>
> **QBO = Phase 1.1, NOT Phase-1-done:** invoice push works, but **no payment push** (QBO AR stays open forever), CC surcharge dropped from QBO books, tokens plaintext (`qbo_client.py:15-17`). This supersedes the 2026-06-04 "QBO BUILT" framing *for go-live purposes* — the push exists, but the accounting is not yet trustworthy.
>
> *Verified-refuted over-claims — do NOT carry forward as blockers: "$0 quotes" (`price_override` carries a real sell price), "no CSRF = forgeable" (SameSite=Lax set + tested), "anonymous access" (login globally enforced — gap is role, not auth), "overpayment lost" (parks as `amount_unallocated` by design), PO "Approve Anyway dead end" / "Resolve UI absent" (forms exist).*
>
> *The render-wave banner below is retained as the prior milestone.*

> **✅ 2026-06-07 — render wave COMPLETE · tier-pricing WIRED · product catalog live · LINT GATE GREEN. 1121 tests pass; 2 reds = 1 brittle W-4 (do-not-chase) + 1 in-flight AR-aging WIP (`test_ar_aging_buckets`, impl uncommitted).**
> Ground-truthed against `HEAD` (`04df870`). The 5 `test_ui_lint` design-system reds were cleared by the §8W lint gate (`c6a30db`) + the motion.html macro; Returns + Warranty full state-machine lifecycle tests landed (`a892618`). **In flight (dirty tree, do not collide):** Import Review queue (`import_review_service` — real) + AR-aging bucket consolidation (`ar_aging_utils` — real) + customer §6 UI polish (`04df870`).
> Earlier-in-day ground-truth (`fc57750`):
> - **Tier-pricing is no longer cosmetic.** `customer.pricing_tier` is now read at line-add — `PricingService.tier_discount_pct()`/`sell_price_for_tier()` (`pricing_service.py:93/107`) read a per-tier discount from settings and are consulted in `quote_service.py:116/513/590` + `invoice_service.py:325` (`4f4b5db`); the confusing per-entry-form dropdown was removed (`9db732b`, owner decision); locked by `test_tier_pricing_and_demo_gate.py`. **The 2026-06-05 audit's one "real" item is CLOSED.** (Per-customer `discount_pct` auto-apply already worked.)
> - **Demo-reset is now production-gated** — `JAKS_ENV=production` → 403 on GET+POST `/admin/demo/reset` (`4f4b5db`, same test). The audit's DB-wipe hardening note is closed.
> - **The Phase-2 render wave is COMPLETE** (governance PASS `300fddd`): quote-workspace always-visible actions (`quotes/_header_actions.html`), quote intelligence chips, dashboard **Top-Customers + Open-Follow-Ups widgets + shrunk revenue chart** (`dashboard.html:143/168/191`), Prepared-By print render. The "Remaining UI consumes" notes in the banners below are SUPERSEDED.
> - **Product catalog is live + organized:** 13k+ PAI parts imported + paginated (100/pg), product images + 2-col edit, schema-v2 pricing + competitor panel, Full/Pricing-Update importer, and the **vendor-independent JAKS SKU scheme** `JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]` (`fc57750`). **Vendor Returns List upgraded L1→L2** (`6809b47`). `data/jaks.db` is throwaway/re-importable from the PAI CSV — restore via re-run Full Import, NOT demo reset.
> - **The ONE gate left is owner-run, not code:** the §8 end-to-end acceptance (hand-run lifecycles A–E with real data) + a full pass of Cores/Returns/Warranty/Vendor-Returns/Reports (automated-proven, never owner-tested) + the operational cutover (one real backup→restore + strong admin pw).
>
> *The 2026-06-05 audit banner below is retained as the prior milestone.*

> **🔍 2026-06-05 GO-LIVE AUDIT — reconciled (status-refresher). It scored 68/100 "not deployable," but 3 of its 4 blockers are already addressed in-tree; one is real.**
> Ground-truthed each against the current code:
> - ~~Ungated DB-wipe (`demo.py:73`)~~ → **admin-gated + confirmation** (`demo.py:76`, `require_admin`). Minor hardening only (could add a sandbox-env guard); NOT a deploy blocker.
> - ~~No CSRF~~ → **SameSite-Lax cookie** (`auth.py:72`) — the documented local-2-user-LAN waiver. No per-form tokens; revisit only if internet-exposed.
> - ~~Double-credit~~ → **guarded** (`core_service.py:726`, BUG-4 `credit_issued_at` idempotency, `e119c19`). Not a bug.
> - **Fake tier-pricing → REAL.** `customer.pricing_tier` is stored + shown in a dropdown but **no pricing/quote/invoice logic reads it** (0 refs) — a customer's tier never changes price (only `discount_pct` does). Logged in §9.1.
> - "Cores/Returns/Warranty/Reports/E2E never owner-tested" → **automated-proven** (`9d0ced2`; 980 tests green) but the **owner hand-walked only the spines** (Suite B/D). Fair *confidence* gap → schedule an owner walk-through; not a code defect.
> **Net: the audit's headline overstates risk (mostly stale). Real items = tier-pricing bug + the operational cutover (one real backup→restore + strong admin pw) before real data.**

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
> - **2026-06-06** — 3-way match gained a write-side *correction* path: `POService.correct_match_line` edits the PO/bill so they genuinely reconcile (must-match gate, records-only, `→ PENDING`, audited `MATCH_CORRECTED`) — alongside the existing accept/credit decisions, not replacing them. Backend + workspace UI + `tests/test_match_correct.py` (12). Closes the owner gap "approving a discrepancy doesn't actually fix it." See §2.4.
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
**pricing grid** · tabbed Settings · quotes-list Open/Print/Email + follow-up colors.

**Landed since (render wave + pricing + catalog, 2026-06-07):** render wave COMPLETE (governance
PASS `300fddd`) — quote-workspace always-visible actions + intelligence chips, dashboard
Top-Customers/Open-Follow-Ups widgets + shrunk revenue chart, Prepared-By print render · **tier-pricing
wired** into quote/invoice line-add (`4f4b5db`) · **demo-reset production-gated** (`4f4b5db`) ·
13k+ product catalog imported + paginated + images + schema-v2 pricing · **JAKS SKU scheme** (`fc57750`) ·
**Vendor Returns List L1→L2** (`6809b47`) · 3-way match write-side correction (`test_match_correct`). **1079 tests green.**

**Landed since (audit-blocker close-out + entry-flow rewrite, 2026-06-08 → 06-14):** **8 of 10 audit blockers closed in-tree** (FK enforcement, deposit-cancel orphan, reports COGS double-count, suggested-sell tier bypass, credit-hold dual state, hardcoded audit user, CDN libs self-hosted, AR aging keys) · **Shopify push as master**: ERP-as-master sync (`d859034`), Smart-Import image-URL delta (`46a68d1`), clean ATL images supersede watermarked PAI (`b45b257`), safe status-preserving re-publish for live listings (`b721d19`) · **Multi-vendor mint live** (`f18aec9`/`118c9da`/`24f4ce1`): full_import resolves vendor per row from feed-SKU prefix, IMB mints on digit `3` (PAI = `9`) — 27,508 of 27,509 products now carry a real `part_seq`-stamped JAKS SKU, the leak SKUs are gone · **Auto-SKU + fast vendor part# entry on `/products/new`** (this session, uncommitted): vendor pick + Vendor Part # typing auto-fills engine/category/cost via `GET /products/classify-part`, twin-detect via `GET /products/twin-check`, writes `ProductVendorSource(preferred=True)` + `CrossReference(VENDOR_ALT)` search mirror; 14/14 new tests green; E2E verified end-to-end.

### 9.1 — Genuinely not built / deferred (BACKEND)

| Feature | Status | Notes |
|---|---|---|
| **Product categorization & classification system** | **✅ BUILT 2026-06-06** (see §18) | Dedicated **Inventory → Category Maintenance** screen (owns Category/Subcategory/Product-Family tree + sort order + active + default markup + import rules; also Brand & Manufacturer/Engine-Make lists). Products List gets **filters** (Category/Subcategory/Family/Manufacturer/Brand/Needs-Review/Uncategorized) + **bulk Assign Category/Manufacturer** + **Manage Categories** link only. Importer: Shopify **Type → top-level category only**; Title/Tags/Body-HTML/OEM/Applications → suggest subcategory/family/manufacturer; low-confidence → `needs_review` + **Import Review queue**. Enforces **Brand ≠ Vendor ≠ Manufacturer/Engine-Make** (today the importer hard-codes `brand`+`manufacturer` both to "PAI"). Full spec + 4 owner forks in **§18**. |
| ~~UX — customer `pricing_tier` decorative label~~ | **✅ RESOLVED 2026-06-07** | Both halves now done: (1) `customer.discount_pct` auto-applies to quote/invoice lines (already worked, `test_tier_pricing.py`); (2) `pricing_tier` is now **wired into price resolution** — `PricingService.tier_discount_pct()`/`sell_price_for_tier()` (`pricing_service.py:93/107`) read a per-tier discount from settings and are consulted at line-add in `quote_service.py:116/513/590` + `invoice_service.py:325` (`4f4b5db`); the confusing per-entry-form dropdown was removed (`9db732b`, owner decision). Locked by `test_tier_pricing_and_demo_gate.py`. No longer a gap. |
| QBO OAuth + **invoice** push | **✅ BUILT 2026-06-04 → COMPLETED 2026-06-10 (R2/R3)** | OAuth2 + REST client + accounting-summary invoice push + bulk sync + Settings/invoice-list/workspace UI; fails-soft — never touches the money path. **R2/R3 closed the rest of 1B:** `push_payment` + `push_vendor_bill` + `push_credit_memo`, Fernet token encryption at rest (`JAKS_FERNET_KEY`, `cryptography` 48.0.1 pinned), real user attribution + AuditLog rows on every push. **Only deferred now:** AST-tax detection + background retry worker (needs a scheduler — Phase 3); `Vendor.qbo_vendor_id` persistence (re-resolves per push, hasattr-gated). |
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
| ~~Serial number tracking UI (cylinder heads)~~ | **✅ BUILT 2026-06-10 (R3)** — capture textarea at PO receive (`SerialService`, fail-safe) → FIFO auto-assign at invoice finalize → release on void. |
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

> **🟢 2026-06-14 — LIVE-TESTING PRIORITY. Owner is hand-walking the R4 GOLIVE TRIAL sheet. Stability + small UX papercuts now outrank ambitious feature work — every recommendation below assumes that posture.**
>
> **Top of queue (in this order):**
>
> 1. **Owner-driven papercut backlog** — whatever the R4 trial sheet (`Testing Feedback/TESTING_FEEDBACK_R4_GOLIVE_TRIAL.md`) surfaces gets dispatched as one-off seam/template fixes. Treat each ❌/⚠️ as the only thing the next lane works on until cleared. **Do NOT start a new feature series while live-testing is open.**
> 2. **PAI cost-data backfill (data-ops, not code)** — 27,508 / 27,509 products have `cost = 0`. Owner runs `scripts/full_import.py` against the latest PAI Info `pai_shopify_all.csv` (or `Pricing Update` mode) — no code change needed. **Blocks** trustworthy dashboard margins / Sales-by-Product / Inventory Valuation reports. Until done, mute or banner those reports in live use to avoid misreading $0 valuations as data corruption.
> 3. **AI product description automation** *(owner ask 2026-06-14)* — speeds up the "<3 min to add a vendor + product" go-live gate (§11). **Pick Claude over Grok** for this — better instruction-following + structured-output for high-volume catalog work. Two-stage build (Sprint 6 below); the templates run on Sonnet 4.6 first, then Haiku 4.5 sweeps the backlog. Builds **on top of** the just-shipped auto-SKU + fast vendor part# entry flow — same form, same `ProductService.create_product` orchestrator.
>
> **SPRINT 6 — AI Description Generator** *(plan-forward, not yet built)*
>
> *Goal:* Owner picks vendor, types vendor part #, clicks **"Suggest description"** → Claude returns title + description + meta-description + SEO keywords, owner skims, accepts, saves. Same flow for batch back-fill against the existing 27k-product catalog.
>
> **6.1 — Claude client + settings card.** Settings → AI tab: API key (encrypted at rest via the existing `JAKS_FERNET_KEY` seam, mirror QBO's pattern), per-environment model selection (default Sonnet 4.6 for interactive, Haiku 4.5 for batch), prompt template (editable, with hard-coded business-voice anchor — "B2B heavy-duty diesel parts, plain English, no fluff, OEM cross-refs when known"). New service: `app/services/ai_description_service.py` — single-product `suggest_one(product, vendor, part_number)` + batch `suggest_batch(product_ids, model="haiku")`. Structured-output tool definition forces `{title, description, meta_description, seo_keywords[]}`.
>
> **6.2 — `/products/new` "Suggest description" button.** Inline button in the Identity card. On click: HTMX POST `/products/ai-suggest` with the current vendor + part # + any title/description already typed. Returns a slide-over with the four suggested fields side-by-side with the current values; owner clicks individual **Accept** buttons or **Accept All**. Same suggest path also lives on Product Detail (for backfill on existing rows).
>
> **6.3 — Batch backfill from Products list.** Filter to "Description blank" or "AI not run" tag → bulk select → "Suggest descriptions" → background job streams Haiku results into a Review Queue tab (mirrors the Smart-Import Review Queue pattern). Each suggestion is review-required; nothing writes without owner approval. Idempotent — re-run safe.
>
> **6.4 — Tests + cost guardrail.** Unit tests with a mocked Anthropic client (no live API in CI). Per-run + per-day token budget settings; refuse to start a batch that exceeds the budget; banner the current spend on the Review Queue.
>
> *Hard rule:* AI-generated text is **always review-required**. No description is written to a customer-facing field without an owner click. The model is a draft generator, not a source of truth.

> **✅ SUPERSEDED 2026-06-02 — this entire Phase-1 queue is DONE.** Sprints 1–5 below (the original
> build order) and the 05-31 "current" items 1–5 all landed: cost-variance 3-way gate (`c36769c`/`bd65c69`),
> O2-enforce (`07f7747`), O6 surcharge (`d098176`), Cores #16 / Returns #17 queues, CSV import phone/email
> fix, and the owner's end-to-end acceptance of both spines — **signed off** (`PHASE_1_TEST_PLAN.md` §13).
> The only Phase-1 carry-overs are **owner-run, not code**: **(a)** the §8 end-to-end acceptance —
> hand-run lifecycles A–E with real data + a full pass of Cores/Returns/Warranty/Vendor-Returns/Reports
> (automated-proven, never owner-tested); **(b)** the **operational** data-safety cutover (one real
> backup→restore + set a strong admin pw at `/account`). Both are go-live gates the code already supports.
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

3-way match discrepancy handling (PO workspace → **Vendor Bills**):
- A bill line flags when billed qty > received/ordered (`over_billed`) or billed unit cost ≠ PO cost by ≥ 1¢ (`cost_variance`); either sets the bill to `DISCREPANCY` and blocks approval.
- **Decision-only resolutions (don't change the numbers):** Accept / Reject / Hold / Clear (`resolve_match_line`) + Create vendor credit (`create_match_vendor_credit`). Accept/Clear suppress the flag but leave the PO and bill divergent — the "Approve Anyway" path.
- **Correct & Reconcile (2026-06-06 — edits the numbers):** `POService.correct_match_line()` lets AP edit the PO unit cost and/or the bill line qty/cost so the PO and bill actually match, with a mandatory reason. Three supported fixes (owner-confirmed): update PO to match bill (vendor price rose), correct a mis-keyed bill entry, fix a qty over-bill. **Must-match gate** — refuses to write unless the line reconciles (variance = 0); otherwise nothing changes and the error explains the residual. **Records-only** — does NOT re-cost already-received inventory (the moving-avg cost booked at receipt is intentionally left untouched, per owner decision). Recomputes the bill total, marks the line `match_resolution = CORRECTED`, audits before→after (`MATCH_CORRECTED`), and opens `DISCREPANCY → PENDING` so AP explicitly clicks **Approve Bill** (never auto-approves). UI: per-line inline editor + reconciled "Approve Bill" row in `purchase_orders/workspace.html`; route `POST /purchase-orders/{po}/bills/{bill}/lines/{line}/correct`; `APPROVE_VENDOR_BILL` perm. Tests: `tests/test_match_correct.py` (12).

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

- [x] **2-user login ENFORCED on production routes; every invoice / payment / adjustment is attributed to the signed-in user** *(O2 — ✅ DONE: `deps.py` production routes require a real session; the user-1 fallback is test-env-only. R3 added real user attribution to QBO pushes too.)*
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
- [~] *(Phase 1B — not a 1A go-live blocker)* Wife pushes **invoices** to QBO with one click ✅ **built + owner-tested 2026-06-04** (single + bulk); **payments / vendor-bills / credit-memos push ✅ BUILT 2026-06-10 (R2/R3)** — remaining is operational: reconnect the live (non-sandbox) company + set `JAKS_FERNET_KEY`
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

> **🔍 2026-06-06 STATUS-REFRESHER VERIFICATION (ground-truthed against the live tree @ `1393ae6`).**
> - **Tests: 983 passed / 6 failed / 55 skipped** (verified live, 64s). All 6 reds are non-functional
>   (5 `test_ui_lint` cosmetic-governance + 1 brittle `test_template_renders` W-4). **Zero functional reds;
>   zero service stubs** (`NotImplementedError` gone — scraper services deleted as planned).
> - **Tier-pricing blocker DOWNGRADED — it is NOT a mispricing bug.** Per-customer pricing genuinely flows:
>   `QuoteService.add_line` auto-applies `customer.discount_pct` to each discountable line
>   (`quote_service.py:119-122`) and `InvoiceService.create_draft` stamps `invoice.discount_pct` from the
>   customer — regression-locked by `tests/test_tier_pricing.py` (`1393ae6`). The only cosmetic gap is the
>   `pricing_tier` **label** (wholesale/fleet/dealer) which no pricing logic reads — decide: relabel it or
>   wire tier→default-discount. Reframed in §9.1 / §17.2 (was "High — silent mispricing").
> - **Real-data cutover is now LIVE, not theoretical.** `data/jaks.db` = 20 MB with **13,153 products**
>   (PAI catalog import, `255e92b`/`67988ae`) + a pre-import `.bak`. Customers (9) + invoices (20) are still
>   seed-level → **catalog loaded, live transactional use not yet begun** (ideal soft-launch posture).
>   This makes the O3 backup→restore drill + strong-password cutover the top operational gate.
> - **The one true status gap is owner-acceptance BREADTH:** the two spines are owner-proven, but the R3
>   sheet's result columns for Cores / Returns / Warranty / Vendor-Returns / Reports / the 5 E2E flows are
>   **still blank** — automated-proven, never hand-walked on the current build. That is a confidence gap, not
>   a code defect. **In flight (risky to touch):** After-Sale Service from invoice (`returns.py` / `warranty.py`
>   + `_new_picker` templates, uncommitted).

### 17.1 — Fixed 2026-06-05 ✅ (commit `acf3c34`, branch `backend/workflow-series-3`; 970 tests green; re-verified 983 green @ `1393ae6`)

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
| **Owner test pass** — now = the **R4 go-live trial sheet** (`Testing Feedback/TESTING_FEEDBACK_R4_GOLIVE_TRIAL.md`, **still blank 2026-06-10**): 5 lifecycles A–E + screen-by-screen + DI spot checks + one backup→restore drill | **High** | M | Never owner-tested end-to-end on the current build. **THE remaining go-live gate.** DB already reset clean for it. |
| **CSRF** on all state-changing POSTs | **High** | M | Still absent app-wide (SameSite-Lax waiver stands for the 2-user LAN). Revisit only if internet-exposed. |
| ~~**Tier-pricing label: relabel or wire**~~ | — | — | **✅ CLOSED 2026-06-07** — `pricing_tier` wired into price resolution (`4f4b5db`) + the confusing dropdown removed (`9db732b`). |
| ~~Encrypt QBO OAuth tokens at rest~~ | — | — | **✅ DONE 2026-06-10 (R2)** — Fernet behind `JAKS_FERNET_KEY` (`cryptography` 48.0.1 pinned; legacy plaintext reads fine). Operational step: set the key before connecting the real company. |
| Force admin password change on first login | Med | S | Dashboard banner (17.1) is the interim; hard redirect-on-login still to do. |
| ~~`demo-reset` env-guard (refuse on the prod instance)~~ | — | — | **✅ DONE** — `JAKS_ENV=production` → 403 on GET+POST (`4f4b5db`). |
| Seed bookkeeper (wife) user + set strong admin/bookkeeper passwords | High | S | Operational §11 cutover step (bookkeeper seeded; strong passwords still to set at `/account`). |
| ~~`products/list.html` export-button wiring~~ | — | — | **✅ DONE** — Products Export CSV wired (17.1) + R1-13 shipped the other 5 CSV exports. |

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

## 18. Product Categorization & Classification — Key Specs

*Added 2026-06-06 (owner request); **BUILT the same night** (owner: "knock them all out"). The four §18.9 forks stand on their recommended defaults (owner approved "yes dispatch" → "build on integration branch").*

> **✅ §18 BUILT & VERIFIED 2026-06-06 on `backend/workflow-series-3`.** Increments 1–7 shipped + tested — ≈24 new tests; full functional suite **1051 passed**, only the 6 pre-existing cosmetic reds (`test_ui_lint`×5 + W-4). What landed:
> 1. **Schema** — `product_categories` += `sort_order`/`default_markup_pct`/`import_keywords`; new owner-maintained `brands` + `manufacturers` tables (seeded from `BRANDS` + `ENGINE_MAKES`).
> 2. **Importer de-conflation** — stops writing `"PAI Industries"` into `manufacturer`; Brand≠Vendor≠Manufacturer.
> 3. **`CategoryService` + `/categories` router** (tree + brand + manufacturer CRUD).
> 4. **Inventory → Category Maintenance** screen + nav link.
> 5. **Products List** — Category/Subcategory/Family (tree) · Manufacturer · Brand filters + **Needs-Review/Uncategorized** tabs + bulk **Assign Category/Manufacturer** + **Manage Categories** link; row badge now shows Engine Make.
> 6. **`ClassificationService`** — engine-make from Applications + keyword→deepest-category match + confidence gate → `needs_review`; wired into `full_import` (Shopify Type → top-level category only).
> 7. **Import Review queue** (`/products/review`) + nav; per-row assign clears the flag.
> **Backfill applied to the live 13,154-part catalog** (backup `data/jaks.db.pre-s18-backfill-20260606.bak`): **13,123 de-conflated · 7,354 engine makes set · 76 flagged · 0 left as "PAI Industries".** NOT git-committed (working tree is a large pre-existing WIP pile — review + commit pending). Follow-ups: owner sets per-category `import_keywords` on the Maintenance screen then re-runs `scripts/backfill_s18_classification.py --apply` to auto-deepen subcategories; UI-lint allowlist pass on the 2 new templates.*

### 18.1 — Why (the problem today)
- The importer hard-codes `brand = "PAI"` **and** `manufacturer = "PAI Industries"` on every part (`product_import_service.py`), so **Brand, Vendor, and Manufacturer are conflated** across the whole ~13,153-part catalog.
- Classification stops at Shopify **Type → one flat level-1 category** (`_resolve_category`). Subcategory, Product Family, and Manufacturer/Engine-Make are never derived.
- The Products List has **no** Category/Subcategory/Family/Manufacturer/Brand filters, no Needs-Review/Uncategorized filter, and no "Manage Categories" link.
- `product.needs_review` exists in the schema but **nothing ever sets it** — there is no Import Review queue.

### 18.2 — Three separate axes (the core rule)
**Brand, Vendor, and Manufacturer/Engine-Make are three different things and must never be merged.**

| Axis | Means | Values | Stored on |
|---|---|---|---|
| **Brand** | The parts brand on the box | PAI · Interstate-McBee · SAMPA · JAK'S (house) | `product.brand` (owner-maintained list) |
| **Vendor Source** | Who JAK'S buys / imports it from | (the vendors) | `ProductVendorSource → Vendor` — **already separate, unchanged** |
| **Manufacturer / Engine Make** | The engine platform the part is for | Cummins · CAT · Detroit · Mack · Volvo · International · Paccar · Mercedes | `product.engine_manufacturer` (already standardized via `ENGINE_MAKES`) |

→ The legacy `product.manufacturer` column (today = "PAI Industries") is **retired**: stop writing the vendor name into it; migrate any real value to `brand`. "Manufacturer" in the UI = `engine_manufacturer`. *(Fork A1.)*

### 18.3 — Category structure (the maintained master)
- **Three-level tree: Category → Subcategory → Product Family.** Re-labels today's Group/Category/Subcategory. `product_categories` is already self-referential (`parent_id` + `level` + `is_active`); **Product Family = the deepest level (3).** A product's `category_id` points at a Family leaf and rolls up to its Subcategory + Category. The free-text `product.product_family` column becomes a denormalized rollup of the leaf (or is dropped). *(Fork A2.)*
- New on `product_categories`: **`sort_order`** (int) · **`default_markup_pct`** (float, nullable — wired later, see O5) · **import rules** (keywords/regex + source-field weights; JSON or child table).

### 18.4 — Inventory → Category Maintenance (NEW screen)
The master-structure editor. **Not** on the Products List.
- **Category tree:** add/rename/reparent Category · Subcategory · Product Family; set **sort order**, **active/inactive**, optional **default markup**.
- **Brand list** and **Manufacturer/Engine-Make list:** add/rename/activate/deactivate (Manufacturer list seeded from `ENGINE_MAKES`; replaces the hardcoded `MANUFACTURERS` in `products.py`). *(Fork A3.)*
- **Import classification rules** live here, attached per node: keyword / Tag / OEM-prefix / Application rules that map import signals → Subcategory / Product Family / Manufacturer.

### 18.5 — Products List (stays a fast working screen)
Add only — **no structure editing here**:
- **Filters:** Category · Subcategory · Product Family · Manufacturer/Engine-Make · Brand · **Needs Review** · **Uncategorized**.
- **Bulk action:** **Assign Category / Manufacturer** to selected rows (the checkbox/`bulk-status` plumbing already exists; add the assign route + a real bulk toolbar form).
- **Link:** **Manage Categories** → the Category Maintenance screen.

### 18.6 — Importer classification (rule-driven, confidence-gated)
- **Shopify Type → broad (top-level) Category ONLY.** Never deeper.
- **Subcategory · Product Family · Manufacturer/Engine-Make → *suggested*** by matching **Title, Tags, Body HTML, OEM references, and Applications** (the last two already parsed by `parse_body_html`) against the §18.4 rules.
- **Brand** comes from an explicit brand signal, else defaults to the import source's brand (e.g. PAI) — **never** the manufacturer.
- **Confidence gate:** a rule match classifies the field; **ambiguous or no match → leave the field blank and set `needs_review = True`.** Never force a part into a wrong category.
- Idempotent + dry-run behavior preserved.

### 18.7 — Import Review queue (NEW)
- Every `needs_review = True` product surfaces in an **Import Review queue** (the Products List "Needs Review" filter + a focused queue view).
- Owner triages: accept the suggestion · pick the correct value · bulk-assign. Resolving clears `needs_review`.

### 18.8 — Existing-catalog backfill *(Fork A4)*
- Back up `data/jaks.db` first, then **run the new rules across the existing ~13,153 parts:** split Brand vs Manufacturer, populate Engine Make from Body HTML/Tags, assign the Category tree where confident; **everything uncertain lands in Import Review** rather than guessing.

### 18.9 — Forks pending owner confirmation
*Written in on the recommended default; change here and the spec above follows.*

| Fork | Default (written in) | Override |
|---|---|---|
| **A1 Manufacturer** | One field = Engine Make (`engine_manufacturer`); retire legacy `manufacturer` | Two fields (part-OEM ≠ engine make) |
| **A2 Product Family** | Level 3 of the category tree | Independent cross-cutting tag |
| **A3 Brand/Mfr lists** | Owner-maintained on the Maintenance screen | Code constants · or free-text + autocomplete |
| **A4 Existing 13k** | Backfill now; low-confidence → Review | New imports only · or safe-fields-only |

### 18.10 — Build order (when approved)
1. **Schema:** `product_categories` + `sort_order`/`default_markup_pct`/rules; Brand + Manufacturer maintained lists; retire the `manufacturer` write. (Backend)
2. **Category Maintenance screen** + nav entry. (Backend + UI)
3. **Importer rules + confidence gate** → set `needs_review`; Type → top-category only. (Backend)
4. **Products List** filters + bulk Assign + Manage-Categories link. (UI)
5. **Import Review queue.** (UI)
6. **Backfill run** over the live catalog (after a verified jaks.db backup). (Backend, one-shot)

---

## 19. System Review 2026-06-10 — Verified Findings & Daily-Use Sprint (AUTHORITATIVE punch list; supersedes §16.6/§17.2 ordering)

> **Method:** 16 subsystem auditors read the real code; all 47 Critical/High claims adversarially
> re-verified at `file:line` — **43 confirmed, 4 refuted**. Full report: `SYSTEM_REVIEW_2026-06-10.md`.
> **Overall B− / daily-use readiness C+.** **DAILY USE STARTED 2026-06-10** → the §19.2 money leaks
> are now bleeding real dollars and outrank everything else in §10/§17.

### 19.1 — Do NOT re-litigate (verified-REFUTED claims)
1. 13k imported parts do **NOT** quote at $0 (`price_override` populated).
2. Backordered lines can **NOT** ship into negative inventory (finalize guard blocks).
3. AI-categorize `output_config` **IS** the correct API param (feature works).
4. Products list **IS** paginated (100/page server-side).

### 19.2 — SPRINT R1 "Stop the bleeding" — ✅ **IMPLEMENTED 2026-06-10, same day** (all located, wiring-level)

> **Status:** all 16 items below SHIPPED via 13 file-partitioned agents, each adversarially
> verified (0 broken verdicts) + 5 orchestrator hardening patches from verifier edge findings
> (multi-invoice surcharge uses `min()` of mixed pct snapshots; NaN rejected on CM apply;
> NSF hidden from the payment allocate card; category code alnum-clamped to 6; chargeback
> recorded as a NEGATIVE CoreReturnEvent so repeat return→denial cycles net out — metrics
> tile filters `credit_amount > 0` to stay gross). Tests **1307 → 1438 green** (+131; only
> red = pre-existing W-4 template test). DEFERRED to R2: Fernet QBO-token encryption
> (`cryptography` not in venv) and SO-deposit card surcharge (sales_order_service.py:818).
> **OWNER DECISION resolved 2026-06-10 ("shortfall"):** partial vendor core-shortfall
> resolved CHARGED_TO_CUSTOMER now claws back only the shortfall (expected − actual),
> capped at the remaining issued credit; outright denials still reverse in full.
> `_charge_back_customer_credit(max_amount=…)` + 2 tests.

| # | Fix | Where | Why (confirmed) |
|---|---|---|---|
| R1-1 | Quote→SO carries MISC/FREIGHT/NOTE/WARRANTY (exclude only CORE_CHARGE) | `quote_service.py:381` | Freight/warranty revenue silently dropped on the daily hot path — underbilling |
| R1-2 | `is_reversed` filter in `Payment.amount_allocated` | `app/models/invoice.py:295` | NSF/reversal strands funds; allocate() blocks re-applying |
| R1-3 | Pass `apply_surcharge`/`surcharge_pct` to `record_payment` | `invoices.py:932`, `payments.py:130` | Card surcharge displayed but never collected — JAKS eats every processing fee |
| R1-4 | Credit-memo `apply` + `close` routes & detail-page buttons (services complete) | `app/routers/credit_memos.py` | Every CM issued is financially inert |
| R1-5 | Duplicate (vendor_id, bill_number) guard + `mark_bill_paid` route | `po_service.py`, `purchase_orders.py` | Double-pay risk; PAID unreachable → AP can never close |
| R1-6 | **CRITICAL:** manufacturer/brand rename cascade → `Product.engine_manufacturer`/`brand` | `category_service.py:220` | Rename splits the 13k catalog; parts vanish from engine-make filters |
| R1-7 | Vendor quick-create code `[:10]`→`[:4]`; category `code` input on create/edit forms | `vendors.py:194`, `categories/index.html` | SKU-scheme corruption on every new vendor/category |
| R1-8 | Clear `is_preferred` on vendor-source soft-delete + `is_active` filter in `preferred_vendor_source` | `products.py:806`, `product.py:327` | Ghost deleted vendor + stale cost in search/dock/exports |
| R1-9 | Core money trio: CHARGED_TO_CUSTOMER chargeback; set `credit_invoice_id` at create; call `mark_overdue_cores()` at startup | `core_service.py`, `main.py` | JAKS eats denied cores; slips print NULL invoice; aging liability silent |
| R1-10 | SO cancel hygiene: cascade-cancel CORE_CHARGE children; decrement `qty_backordered` | `sales_order_service.py` | Phantom core deposits inflate SO totals; demand metric drifts from day one |
| R1-11 | Render `credit_warn` on quote/SO/invoice workspaces (ctx already computed) | 3 workspace templates + quote ctx | Credit-hold customers invoiced with zero warning |
| R1-12 | Receive form qty defaults 0 + explicit "Receive All" | `purchase_orders/workspace.html` | One careless click marks partial deliveries fully received |
| R1-13 | CSV exports: AR aging / overdue / sales-tax / invoices / customers | `reports.py`, `invoices.py`, `customers.py` | Collections + accountant blocker; 8 of 9 Export buttons are dead stubs |
| R1-14 | Margin truth: `vendor_cost` fallback + zero-cost warning banner; sales-tax `taxable_revenue`→`line_total`; dashboard overdue `func.date()` | `report_service.py:239,357,897`, `dashboard.py:50` | Margins read ~100% (cost=0 catalog); tax base overstated; overdue counts disagree |
| R1-15 | Security: session secret → env var (DB fallback); gate `JAKS_SKIP_AUTH`; **Fernet QBO tokens before connecting the real company** | `main.py:67`, `qbo_client.py` | Secret stored in the DB it protects; bare env var kills all auth |
| R1-16 | Block non-PAI feeds in `full_import` until vendor w/ confirmed digit exists; filter DUPLICATE rows in `apply_approved` | `product_import_service.py:589`, `import_review_service.py:367` | Vendor digit '9' SKU corruption; wedged batches never reach APPLIED |

### 19.3 — SPRINT R2 "Close the loops" — ✅ **MOSTLY IMPLEMENTED 2026-06-10** (8 agents, same day as R1)

> **SHIPPED:** VCR batch UI (create/ship/vendor-decision routes + cores-queue batching + open-VCRs card
> + the dark §5.4 dollar tiles now rendered; reuses R1-9 chargeback/double-credit guards per core) ·
> warranty ESN column+input+print+migration + warranty_type select + POST /warranty/{id}/vendor-credit
> + vendor-less-submit guard (type-gated: JAKS_EXTENDED submits vendor-less) · QBO `push_payment`
> (refuses unsynced-invoice/reversed/NSF, fail-soft) + Fernet token-encryption-at-rest behind
> `JAKS_FERNET_KEY` (`cryptography` 48.0.1 installed+pinned; legacy plaintext reads fine; no key =
> unchanged) + multi-DisplayName auto-bind refusal + Push-to-QBO on payment detail · engine picker on
> SO+invoice workspaces · **SO-deposit card surcharge** (derives from customer.card_surcharge_pct) ·
> competitor-number search strategy + import now mints competitor cross_references w/ GLOBAL collision
> guard + `_norm_col` widened to ()+#% (ready for scraper competitor loads) · low-stock reorder report
> + CSV + index card + nav tab · Duplicate Quote route + More-menu (any status) · structured Mark Lost
> (LostReason select + conditional competitor name/price) · RA expected-vs-actual credit (partial
> returns credit only `qty_returned_to_stock`; also fixed a latent restocking-fee mispairing in
> close_ra) · receiving-slip print route + queue button · invoice list selectinload + sortable
> number/customer/due_date/total/balance. **Owner shortfall decision implemented** (§19.2 note).
> **NOT done from this list:** products-list OEM search via SearchService (R3), "Create PO from low
> stock" action (report ships without it), QBO audit rows/real user_id. Watch items: SCRAP/QUARANTINE
> receipts now credit $0 (by rule — receive-form qty records what came back); line-adder badge for
> competitor matches falls back to 'PART'.

### 19.4 — SPRINT R3 "Make it solid" — ✅ **IMPLEMENTED 2026-06-10/11** (9 agents)

> **SHIPPED:** category REPARENT + MERGE (subtree level recompute, cycle/depth guards, keyword
> union, descendant-inclusive tree counts) + word-boundary classifier · DB unique-index batch
> at startup (PVS (product,vendor) WHERE active, cross-ref grain, account_number, (vendor,bill#)
> — each probed for pre-existing dupes first: skip+WARN, never wedge boot, never delete) ·
> admin inventory resync routes (/admin/inventory/resync{,-all}; ledger recompute correctly
> treats SO commitments as still-on-hand) · products-LIST search now finds OEM/vendor/competitor
> numbers (same normalization as the line adder; tab counts honor ?q=) + amber COMP badge in the
> line adder · **freight landed-cost**: PO.freight_in_cost allocated per ordered unit by line
> value at receipt, folded into moving-average COGS + ProductCostHistory; zero-freight =
> bit-identical; vendor_cost never polluted · **serial numbers live**: capture textarea at PO
> receive (SerialService, fail-safe) → FIFO auto-assign at invoice finalize → release on void ·
> **statement persistence**: ST-YYYY-NNNN minted (flush-not-commit), bucket cols match
> ar_aging_utils (over_90; due_120 dead-legacy), snapshot_json archive re-renders ORIGINAL
> numbers after edits, history card on the statement page · **QBO AP/AR-credit legs**:
> push_vendor_bill (APPROVED/PAID-gated, vendor resolve w/ multi-match refusal + auto-create,
> COGS expense lines, freight line) + push_credit_memo (invoice item map) + real user attribution
> + AuditLog rows on every push success/failure + buttons w/ chips on PO-workspace bills + CM
> detail · **import column-mapping**: unknown headers park as NEEDS_MAPPING → mapping screen w/
> fuzzy prefill + saved per-vendor ImportMappingTemplates (upsert by name) → feeds the unchanged
> stage→review→apply pipeline (all R1 guards intact); known Shopify/JAKS feeds = zero change ·
> **full-chain E2E** (10 ordered steps, money to the cent: quote→SO→PO+freight→receive→fulfill→
> finalize→card pay w/ surcharge→core return→shortfall chargeback→ledger sweep).
> **THE E2E CAUGHT + WE FIXED a real money bug:** `fulfill_and_invoice` forwarded the SO's
> auto-generated CORE_CHARGE child AND create_invoice re-derived one → core deposit
> double-billed on every SO fulfillment of a core product. Fixed in sales_order_service
> (derived core children not forwarded; bookkeeping still advances so SOs reach INVOICED).
> **Deferred:** QBO AST detection + background retry worker (needs scheduler — Phase 3);
> Create-PO-from-low-stock bulk action; Vendor.qbo_vendor_id persistence (re-resolves per push,
> hasattr-gated to auto-persist if the column lands); qbo drift entries for vendor_bills/
> credit_memos only needed on pre-mixin DBs (live DB verified to have them).

### 19.4b — R4 "Scraper delta-refresh + queue hygiene" — ✅ IMPLEMENTED 2026-06-11

> Owner ask: re-running the 13k-row scraper export should surface ONLY the ~2k rows
> where cost/pricing actually changed, and applied candidates should leave the
> review queue ("no reason to keep them there").
> **SHIPPED:** (1) Pricing Update *sell* mode reads the scraper's Shopify CSV and
> refreshes price_override + compare_at + PAI vendor_cost (+history) + manufacturer
> (canonicalized vs the 6-make dropdown; unmapped surfaced) — `Our Cost` +
> `Manufacturer` column contract documented in `SCRAPER_REQUIREMENTS.md` for the
> scraper repo; 50% threshold rail on sell price only. (2) Smart Import skip-unchanged
> staging: UPDATE rows identical to the catalog are tallied (`ImportBatch.unchanged_count`),
> never staged; changed rows carry `ImportCandidate.diff_json` rendered as a
> Current→Incoming table in the preview dock (closes the R1-audit "reviewers approve
> updates blind" gap); toggle on the upload form (default ON). `_apply_update` now also
> writes compare_at / PAI vendor_cost (+history) / canonical manufacturer — COGS still
> receipt-only. (3) Queue hygiene: applied candidates are DELETED on apply (tallies live
> on the batch header, which survives as history); swept DUPLICATEs leave too; new
> admin-gated Delete-batch button + route (closes the "staging tables fill forever" gap).
> Gotcha: `_finalize_batch` no longer recounts applied (accumulated in apply_approved);
> parse_shopify_csv now emits optional `cost`/`manufacturer` keys → full_import seeds
> vendor_cost on NEW products when the scraper provides it.

### 19.5 — Brand alignment workstream (parallel, low-risk)
Customer-facing print docs (quote/invoice/statement/core slip) adopt the JAK'S brand kit
(`D:\Work Folder\Website\JAK's Diesel Website (8)\brand-kit.html` + `assets/jaks.css`):
military-green `#5a6630` + amber + steel palette, Oswald/Barlow/IBM Plex Mono, hazard divider motif.
Route through the existing `documents/_company_header.html`/`_footer.html`/`_styles` partials +
`get_company_dict()` seam — **print templates only; do NOT restyle the ERP app shell** (§3 design system stays locked).

### 19.6 — Report card (2026-06-10 verified; grades below were PRE-sprint)
Navigation B+ · Products B− · Inventory B · Purchasing B · Receiving B− · Quotes B− · Invoicing B− ·
Customers B− · Cores C+ · QBO C+ · Reporting B− · UI/UX B− · **Readiness C+ → target A after R1+R2.**
Grade path: R1 = stop money loss (C+→B), R2 = close vendor/QBO loops (B→A−), R3 + 2 weeks clean daily use = A.

> **2026-06-10 (evening) — R1+R2+R3+R4 ALL SHIPPED** (`248ea09` / `40eea95` / `a53bbe2` / `674491a..f18aec9`),
> each wave adversarially verified; the R3 full-chain E2E (`test_r3_e2e_full_chain.py`, money to the cent)
> caught + fixed a real core-deposit double-bill in `fulfill_and_invoice`. **Code readiness ≈ A−.**
> What separates A− from A is no longer code: **(1)** the owner R4 trial sheet is still blank,
> **(2)** the operational cutover (strong passwords · backup→restore drill · real-catalog Full Import ·
> live-QBO reconnect + `JAKS_FERNET_KEY`), **(3)** two weeks of clean daily use. Live DB was reset to a
> clean trial state (130 seed products, 1 invoice) — the 13k catalog re-imports at cutover by design.
> Code still open (small): force-password-change redirect · §19.5 brand-kit prints (not started) ·
> §17.3 counter-readiness tier (pick ticket / daily close / CASH walk-in) · CSRF (waived for LAN).
> In flight uncommitted: vendor SKU-digit auto-assign + freeze (`vendors.py` + `sku_service.py`).

---

## 20. Customer-Facing SKU — REVERT to Vendor Part Numbers (decision 2026-06-16)

> **DECISION LOCKED 2026-06-16 (owner interview). REVERSES the 2026-06-06 opaque SKU scheme**
> (`JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]`, see `SKU_SCHEME_SPEC.md`). That scheme was built **and applied
> to the live catalog** — all 29,659 products carry it — but created too much confusion: **10%** are
> meaningless `JAKS-GEN-#####`, **2,730** kits landed under a junk `INFO` code, the derived codes are
> cryptic (`HEABF` / `SEAOR` / `CAMC`), the opaque vendor digit is `9` (PAI) on nearly everything, and —
> worst — it **masks the very number staff need to order, cross-ref, and test a part**. The masking only
> ever belonged on private-label product.
>
> **STATUS 2026-06-16 (UNCOMMITTED):** Steps 1–2 + 4–5 SHIPPED. ① Revert applied to live `jaks.db` (all
> 29,659 `product.sku ← vendor_part_number`, 0 collisions; backup `jaks.db.pre-sku-revert-20260616-104559.bak`)
> via new `scripts/revert_sku_to_vendor_part.py`. ② Create path (`product_service` + `routers/products`)
> **and the importer** (`product_import_service.full_import`) de-masked — both now use the vendor part #
> (importer is the catalog restore path, so this stops a re-import re-masking). ④ `sku_service.py` +
> `backfill_sku_scheme.py` shelved dormant. ⑤ Tests updated to §20 — **1871 functional pass, 0 fails**
> (66 reds are all pre-existing visual-baseline drift). ③ **Private-label two-number UI SHIPPED** — `products/new.html` reworked: is_house_brand
> checkbox + JAKS Product # field (SKU = your number when on, vendor part # when off), masking/twin UI removed.
> All of §20 now shipped (UNCOMMITTED beyond `500474b`, which carried steps ①②④⑤). Cleanup left (non-blocking): dead `/products/twin-check` route + quick-create "generated automatically" wording; INFO/GEN category junk is now browse/filter-only.

### 20.1 — The model (locked)
- **Standard products (PAI, McBee/IMB — effectively all ~29.6k):** the SKU **is the vendor's real part
  number**, used everywhere (internal screens · customer documents · PO). No masking; no engine/category
  codes in the SKU.
- **Private-label products only (owner-manufactured, `M`-prefix):** two numbers —
  - **Vendor Part #** (`M2239250HH`) — lives on the vendor source; prints on the **PO to the vendor**.
  - **JAKS Product #** (`2239250S3`) — owner-typed **free-form**; this is `product.sku` (your system +
    customer documents). Carries meaning for the customer (e.g. `S3` = stage-3 head).
- **Flag:** the existing **`products.is_house_brand`** boolean marks a part private-label — **no new
  column**. `is_house_brand = 0` → SKU = vendor part #; `= 1` → SKU = owner-typed JAKS Product #, with the
  vendor # kept on the source for the PO.
- **No toggle.** With masking gone there is nothing to switch; the earlier "global mask toggle" idea is dropped.

### 20.2 — Why it's safe (verified 2026-06-16)
- Every one of the 29,659 products already carries its real vendor part # on the preferred source
  (`ProductVendorSource.vendor_part_number`, e.g. PAI `040000`); `product.manufacturer_part_number` is
  empty everywhere. Revert = `product.sku ← vendor_part_number`.
- **0 collisions** — all 29,659 vendor part numbers are distinct, so the `UNIQUE(sku)` index won't trip.
- Line items reference `product_id` + a `description` snapshot (no SKU snapshot), so the SKU renders live —
  but the DB holds only trial documents (**4 quotes · 1 SO · 6 invoices · 2 POs**); nothing real is
  disturbed. The 29.6k catalog is the re-importable PAI feed.
- PO print already references `vendor_part_number`, so the private-label PO flow is half-wired.

### 20.3 — Build order
1. **Revert script** — `product.sku ← preferred source.vendor_part_number` for all standard products;
   **two-phase temp→final** (respects `UNIQUE(sku)`); auto-backs-up `jaks.db`; **dry-run default**,
   `--apply` to write. *(Backend, one-shot — eyeball the old→new samples before applying.)*
2. **Stop the masking mint** — the product-create path (`product_service.py` / `routers/products.py`) no
   longer calls `assign_new_sku`: standard parts default SKU = vendor part #; `is_house_brand` parts let
   the owner type the JAKS Product #.
3. **Private-label two-number UI** — product new/detail: when `is_house_brand` is on, expose the JAKS
   Product # as the SKU and keep the Vendor Part # on the source (already prints on the PO). Label it
   clearly ("Private label — my own number").
4. **Shelve the masking** — keep `sku_service.py` + `scripts/backfill_sku_scheme.py` in-repo but out of
   the active path (possible future ERP feature); retire the masking assertions in `tests/test_sku_service.py`.
5. **Docs/memory** — mark `SKU_SCHEME_SPEC.md` reverted; update the `jaks-sku-scheme` memory.

### 20.4 — Deferred (no longer SKU-blocking)
- **INFO / GEN category junk** — once SKUs are vendor part #s the derived codes vanish from the SKU, so
  this becomes a **browse/filter** cleanup only: re-map the bogus `INFO` ("Information") category → a real
  **KIT** category (2,730 kits), and give the ~2,984 uncategorized hardware items (`BOLT`/`SCREW`/`WASHER`)
  a **FASTENER** category. Low priority.
- **Re-masking option** — the opaque scheme stays shelved-but-available if the owner later wants
  source-protection on the standard catalog (or to sell it as an ERP feature).

### 20.5 — Forks pending owner confirmation
*Written in on the recommended default; change here and the build follows.*

| Fork | Default (written in) | Override |
|---|---|---|
| **S1 Private-label inventory** | None entered yet → private-label UI is a fast-follow after the revert | Some exist now → build the UI with the revert |
| **S2 Masking code** | Shelve dormant (possible ERP feature) | Remove entirely |
| **S3 Standard customer SKU** | Bare vendor part # (e.g. `040000`) shown to customers | Re-mask later if the leak matters |

---

## 21. Update 6.16 — Sellable-ERP Audit + Owner Decisions (verified)

> **Source:** full 16-subsystem `jaks-erp-status-report` audit (42 agents, adversarially verified — 24
> of 25 serious risks confirmed against the cited code, 1 refuted). **Overall grade: C+.** The bones are
> strong (real inventory ledger, one totals engine, moving-avg COGS isolated from vendor quotes, properly
> modeled core lifecycle, 3-way-match PO/receipt/bill). The grade is dragged by ~12 **verified money/inventory
> edge defects** that produce wrong dollars or wrong parts in daily use. This section is the AUTHORITATIVE
> punch list; it supersedes §16/§17/§19 ordering where they conflict.

### 21.1 — Owner decisions LOCKED 2026-06-16
1. **Credit hold = WARN.** Finalize/SO-convert for a held customer shows a warning + requires an explicit
   override flag (warn-and-confirm with audit note). NOT a hard block.
2. **Over-receipt = CONFIRM AND ALLOW.** Receiving more than ordered prompts a confirm, then proceeds.
   No server-side hard cap.
3. **Quote tax = default from customer.** A tax-exempt customer's quote stays tax-free; a taxable
   customer's quote gets tax. The clerk can **toggle tax on/off per quote** when needed.
4. **Competitor cross-refs → NORMAL part search.** Load HHP/ATL/IMB as `ref_type='competitor'` rows in
   `cross_references` so they surface in the standard part typeahead (not a separate competitor screen).
5. **DEPLOYMENT IS INTERNET-EXPOSED.** ⚠️ This promotes security from "LAN-medium/later" to **immediate**:
   CSRF on state-changing POSTs, QBO client-secret + token encryption, the RBAC void/payment gate, cookie
   `secure` flag, and HTTP security headers are now go-live blockers, not polish.
6. **Multi-tenant SaaS = SOMEDAY goal.** Do NOT stop adding single-tenant surface area. No `company_id`
   retrofit now. Revisit when resale is a near-term goal.
7. **`jaks.db` is THROWAWAY** — real trial transactions run through it but everything posts to the **LIVE
   QBO** (not sandbox) and the catalog is re-importable from the scraper. No backup-before-migration step
   required. **Implication:** wrong QBO pushes write to real books, and the plaintext QBO secret is a real
   credential-leak exposure → reinforces decision #5.

### 21.2 — fix_before_phase1 — IMMEDIATE sprint (✅ SHIPPED 2026-06-16, UNCOMMITTED)
*All line numbers re-verified against current code before editing. **11 code fixes shipped +
1 false-positive refuted + 1 owner data-load**; new regression file `tests/test_s21_audit_fixes.py`
(13 tests) locks them in. 0 new test failures (the 12 reds are all pre-existing — §21.4).*

| # | Fix | Status | File(s) |
|---|---|---|---|
| 1 | ~~Remove vendor_cost clobber on receipt~~ | ⚪ **REFUTED — no fix needed** | already history-only (`product_service.py` `compare_and_record_cost_change`/`_sync_cost_from_preferred`; receipt path `po_service.py:397/416` never writes `vendor_cost`). The §8N fix IS present; the audit verifier read a stale blob. |
| 2 | Close `CoreCharge` rows on invoice void | ✅ SHIPPED | `services/invoice_service.py` `void_invoice` — closes OPEN/PARTIAL, not-yet-returned/credited cores; logs+skips returned ones |
| 3 | RBAC gate on void + payment | ✅ SHIPPED | `void_invoice` asserts `VOID_LOCKED_INVOICE` unconditionally; new `RECORD_PAYMENT` perm (ADMIN+BOOKKEEPING) gates `record_payment`; routers surface `PermissionError`. New `Permission.RECORD_PAYMENT` in `constants.py`; `base.py` `_ROLE_PERMISSIONS` + test-harness unknown-actor guard |
| 4 | Credit-memo lines in statements | ✅ SHIPPED | `services/statement_service.py` `generate_statement` (nets pre-period CMs into opening balance + in-period CMs as credit lines); `statement_print.html` teal Credit-Memo row |
| 5 | Receive `VERBAL_ORDER` POs | ✅ SHIPPED | `routers/purchase_orders.py` `can_receive` (`_workspace_ctx`) + receive-route guard |
| 6 | Credit hold = **WARN + confirm** (#1) | ✅ SHIPPED | `routers/invoices.py` finalise bounces to `?credit_hold=1`; `invoices/workspace.html` amber banner + "Finalize anyway" (`confirm_credit_hold=1`). *SO-convert warn deferred — finalize is the AR gate.* |
| 7 | Product-list price/margin key | ✅ SHIPPED | `templates/products/list.html` — single resolved `_sell` from `sell_price_map` drives both price column + margin badge |
| 8 | FULL-payment guard | ✅ SHIPPED | `services/sales_order_service.py` `fulfill_and_invoice` blocks `payment_mode==FULL` while `deposit_amount < subtotal` |
| 9 | Quote tax (#3) | ✅ SHIPPED | `models/quote.py` (`is_taxable`/`tax_rate_snapshot` + `taxable_base`/`tax_amount`/`total`), `database.py` migration, `quote_service.create_quote` default-from-customer, `routers/quotes.py` `_totals_ctx` + `POST /quotes/{id}/toggle-tax`, `_totals.html`+`print.html`, carry-forward in `convert_to_invoice` |
| 10 | Encrypt `qbo_client_secret` + Fernet key (#5/#7) | ✅ SHIPPED | `routers/settings.py` (encrypt on save, blank=keep, mask in form), `settings/index.html` no-echo + "saved" hint, both `.bat` launchers bootstrap `JAKS_FERNET_KEY` from `%USERPROFILE%\.jaks_fernet.key` |
| 11 | AI-categorize schema enforcement (#4-data is #12) | ✅ SHIPPED | `services/ai_categorization_service.py` — forced `tool_choice`/`tool_use` (GA, no beta header) replaces `output_config`; tests updated |
| 12 | Competitor cross-refs → normal search (#4) | 🟡 **CODE-READY — owner data load** | search (`search_service.py:168-224`) + importer (`pricing_update_competitor`, route `POST /products/import-run` mode=pricing/source=competitor) BOTH already wired. Run a scraper competitor CSV through **Products → Import → Pricing Update → Competitor**. No code change. |

### 21.3 — Security now-blockers (promoted by decision #5, internet-exposed)
- CSRF tokens on demo-reset, invoice void, payment POST routes.
- QBO client-secret + access/refresh token encryption at rest (Fernet) — #10 above.
- Cookie `secure` flag + HTTP security headers (X-Frame-Options, CSP).
- RBAC HTTP route tests (SALES user blocked on finalize/void/reverse/issue-credit-memo) — #3 above.

### 21.4 — Verified-REFUTED / downgraded (do NOT re-litigate)
- **SO list tab counts always zero** — REFUTED (backend passes counts; the template stub comment misled).
- MPN duplicate products, ProductApplication re-import multiplication, N+1 totals, margin-unreliable-until-purchase — all **downgraded to low**; acceptable at current scale.

### 21.5 — Security now-blockers (#5 internet-exposed) — ✅ SHIPPED 2026-06-16, UNCOMMITTED
Live-verified in the preview (auth on, file DB): CSRF enforced (no-token→403, token→passes), all 4
security headers present, CSP doesn't break Alpine/htmx, native-form token injection works.
- **CSRF** — `app/security.py` `CSRFMiddleware` (pure-ASGI double-submit cookie; buffers+REPLAYS the body
  so the `_csrf` form field never starves the downstream route; validates header OR form field OR
  multipart). Skips validation when there's no valid session (auth redirects those). Wired in `base.html`
  (htmx `X-CSRF-Token` header + native-form hidden `_csrf` injector). Test bypass honored. 7 new tests.
- **Security headers** — `security_headers_middleware`: X-Frame-Options DENY · X-Content-Type-Options
  nosniff · Referrer-Policy same-origin · CSP (self + 'unsafe-inline'/'unsafe-eval' for Alpine + Google
  Fonts origins; `JAKS_DISABLE_CSP=1` escape hatch).
- **Secure cookie** — `auth.py` login + CSRF cookie get `Secure` when HTTPS (direct or `X-Forwarded-Proto`)
  or `JAKS_SECURE_COOKIES=1`; OFF by default so a plain-HTTP LAN run still logs in.
- Affected production-mode tests updated (backup/auth/security send tokens). `tests/test_s21_csrf_security.py`.

### 21.6 — Phase 1.1 batch — ✅ SHIPPED 2026-06-16, UNCOMMITTED (`tests/test_s21_phase11.py`, 11 tests)
- **`CoreCharge.vendor_id` stamped at creation** from `product.preferred_vendor_source` (`core_service.py`).
- **`resync_qty_committed` / `resync_qty_on_order`** (`product_service.py`) + admin routes
  (`/admin/inventory/resync-committed/{id}`, `…/resync-on-order/{id}`, `…/resync-availability-all`).
- **Daily overdue-core scan thread** (`main.py` `_start_overdue_core_scheduler`, mirrors Shopify scheduler).
- **Case-insensitive Brand/Manufacturer rename cascade** (`category_service.py`; obsolete exact-match test
  rewritten to assert the new behavior).
- **6 missing report CSV exports** (`reports.py`: sales-by-customer/-product, inventory-valuation, open-pos,
  outstanding-cores, lost-sales) + export buttons enabled in the 6 templates.
- **Dashboard QBO chip** now reflects real `connection_summary()` state (was hardcoded "ready").

### 21.7 — Counter findability + scale — ✅ SHIPPED 2026-06-16 (`tests/test_s21_search.py`, 6; commit `aef175a`)
Live-verified against the real 29k catalog ("Cummins" → 5 parts by engine fit; backfill+indexes clean).
- **Barcode/UPC exact search** (`search_service.py`, match_type `barcode` → SCAN chip) — scanned code resolves to its part.
- **Engine-application search** — queries `ProductApplication` (make/model) for "Cummins ISX"/"ISX"; was populated but never queried (match_type `engine_app` → ENGINE chip; ranked below part #).
- **`products.sku_norm`** precomputed INDEXED column (Product before_insert/update listener + startup backfill) — SKU search no longer normalizes every row per keystroke.
- **New FK indexes** — `invoice_lines.product_id`, `product_applications.product_id`.

### 21.8 — Post-PAI catalog cleanup — ✅ SHIPPED 2026-06-16 (`tests/test_s21_applications.py`, 6)
Live-verified on the real catalog (product detail → Applications tab → add Cummins ISX → appears → delete).
- **ProductApplication edit UI** — new "Applications" tab on product detail (`detail.html`) with the engine
  fitment list (`_applications_list.html`) + an add form using the standardized **engine make/model picker**
  (`macros/engine_picker.html`); HTMX add/delete swap the whole `#apps-list` tbody (dedup-safe).
- **Service CRUD** `ProductService.add_application` (idempotent on the make/model/cpl grain) /
  `remove_application`; routes `POST /products/{id}/applications`, `DELETE …/{app_id}`.
- **Bulk product-reassign-to-category** — already existed (`POST /products/bulk-assign` sets category_id +
  manufacturer); confirmed, no rebuild needed.
- Net effect: engine fitment is now editable AND searchable (pairs with §21.7 engine-application search).

### 21.9 — Final cleanup batch — ✅ SHIPPED 2026-06-16 (commit `e15b7f8`; suite 1975/12; live-verified)
- **Vendor-bill list + AP aging** — `ReportService.get_ap_aging` (AR-aging mirror, net of applied vendor
  credits); `/reports/ap-aging` + CSV; `/purchase-orders/bills` standalone list (tabs + net-of-credit
  balance); AP-Aging report-nav tab + Vendor Bills sidebar link; `VendorBill.vendor` viewonly relationship.
- **Self-hosted fonts** — `scripts/fetch_fonts.py` → 11 latin woff2 + `fonts.css` under `static/fonts/`;
  base.html drops the Google Fonts CDN; CSP drops googleapis/gstatic; woff2 MIME registered. Verified 11
  fonts load, Oswald renders, zero Google dependency.
- **SO-convert credit-hold warn** — `/quotes/{id}/convert-to-so` bounces an on-hold quote to `?credit_hold=1`
  with a "Convert to SO anyway" banner (completes the §6 gate alongside invoice finalize).
- **Barcode scan→auto-add** — line adder auto-adds when a search resolves to exactly one `barcode` match;
  SCAN/ENGINE chips colored.
- **`CreditMemo.applied_amount`→computed** — derived from non-reversed allocations (drift-proof);
  `unapplied_amount` kept stored (close moves residual to credit_balance, not derivable). Writes removed.

### 21.10 — Mostly SHIPPED 2026-06-16 (commits `<qbo>`/`<features>`/`<bulk+pager>`)
- **QBO push hardening** ✅ — `Vendor.qbo_vendor_id` persisted · 429 Retry-After backoff · AST no-override
  retry (`_is_ast_tax_error`) · `unsynced_invoice_ids(pending_only)` (Sync-All skips ERROR) ·
  `retry_failed_pushes` + background scheduler (`main.py`, ~30 min while connected, retry ceiling).
- **Backup-before-migration** ✅ — `_apply_inline_migrations` snapshots the live SQLite file to `backups/`
  before any ALTER (only when a column is genuinely missing; file DB only; best-effort).
- **Quote-conversion + Vendor-performance reports** ✅ — `get_quote_conversion` (win-rate + value) /
  `get_vendor_performance` (PO count/value, fill-rate, 3-way-match discrepancies) + routes + nav + CSV.
- **Warranty ESN gate** ✅ — `submit_to_vendor` requires an ESN on VENDOR claims when
  `warranty_require_esn` setting is on (default off — opt-in PAI/IMB compliance gate).
- **Post accrued interest** ✅ — `CRMService.post_interest_charge` → DRAFT MISC_FEE invoice (operator
  finalizes) + `POST /customers/{id}/post-interest` + "Charge Interest" button.
- **Bulk month-end statements** ✅ — `generate_bulk_statements` (every customer with a balance) +
  `POST /customers/statements/bulk-generate` + AR-aging button. (Generation only — no email provider.)
- **List pagination** ✅ — `utils.compute_pager` + `macros/_pager.html`; invoice/quote/SO lists now page
  (`?page=`) instead of a silent `limit(150/200)`.

### 21.11 — Genuinely deferred (Phase 2-3 / SaaS)
- **Server-side session revocation** — DEFERRED: needs invasive changes to the just-hardened auth hot path
  (per-request DB lookup + token plumbing) with real lockout risk; revisit deliberately, not in a sweep.
- **Mobile/tablet workspace layout** — DEFERRED: large responsive re-layout; high regression risk to the
  dense desktop UI; needs its own design pass.
- **schema_version tracking** — low value now that backup-before-migration ships (owner #7 throwaway DB).
- **ESN→CPL range validation** against ProductApplication — only the non-empty ESN gate shipped; the
  range check is unreliable against free-text `esn_range`.
- Real email/SMS send (NullProvider only); server-side PDF (GTK); pagination on the remaining minor lists.
- **SaaS multi-tenancy (`company_id` scoping) = someday (#6).**

---

## 22. Customer Communications — real Email/SMS send on documents + texting consent *(plan, 2026-06-17 — NOT built yet)*

Owner ask: make "Send" on a quote/SO/invoice actually transmit; add an **"OK to text"** button on the
customer profile; make **both email and SMS testable** once configured.

### 22.1 — Current state (verified 2026-06-17)
- **"Send" on a quote only flips status DRAFT → Sent** (`QuoteService.send_quote`) — it transmits NOTHING.
  **Invoices and Sales Orders have no send/email action at all.** (This is the source of the confusion.)
- **The messaging engine is already complete and capable** — `app/services/messaging_service.py`:
  real **SMTP** (Workspace/365) + **Twilio SMS** providers, a global **`messaging_log_only_mode`
  kill-switch (default ON)**, per-customer **consent + rate-limit** gating, a **`communication_log`**
  audit of every attempt (SENT/FAILED/LOGGED_ONLY + `failed_reason`), `.txt` templates, and
  `record_consent()` / `record_opt_out()`. It is simply **not wired to any document button.**
- **Consent columns already on `Customer`**: `allow_email` (default True), `allow_sms` (default False),
  `sms_consent_at` + `sms_consent_method`, `email_consent_at`, `do_not_contact`, `opt_out_at`,
  `preferred_contact_method`. A communications page exists; **no "OK to text" control yet.**
- **Design reality**: SMS **cannot** carry a PDF; **email can** (provider `send_email` already accepts
  `attachments`, but `MessagingService.send()` does not yet forward them).

### 22.2 — Decisions (RECOMMENDED defaults — owner interviewed 2026-06-17, not yet confirmed; flip any here)
- **D1 — Send UX = ONE "Send" button → channel picker dialog.** Pre-filled recipient; rep picks
  Email / Text / both; **Text is greyed with a reason when the customer has no OK-to-text**. *(alts:
  separate Email+Text buttons · auto-send by `preferred_contact_method`.)*
- **D2 — Channel content: email carries the PDF; SMS is a short heads-up.** SMS = doc # + amount +
  shop phone ("reply here or call …"). **A public "view link" is deferred** until the app is
  internet-reachable (today it's shop-LAN only). *(alts: SMS view-link · minimal note.)*
- **D3 — "OK to text" = one-click verbal consent.** Sets `allow_sms` + `sms_consent_at` +
  `method='verbal'` + audit (who/when); a matching **"Do not text / opt out"** turns it off. *(alts:
  method picker Verbal/In-person/Written/Web · plain on-off toggle.)*
- **D4 — Testability = Settings → Messaging "Send test".** Type any email/phone → live success/failure
  from the real provider; plus the visible log-only kill switch and the per-customer communication log.

### 22.3 — Function A: Send on quote / SO / invoice
- New `POST /{doc}/send-message` on quotes → invoices → SOs, opening `documents/_send_dialog.html`
  (HTMX/Alpine), pre-filled from the customer + the chosen template.
- Dialog: channel checkboxes (Email/Text), editable recipient + subject/body (seeded from template),
  "PDF will be attached" indicator for email, live SMS-consent state.
- Submit → `MessagingService.send(...)` per channel. **Extend `send()` to forward `attachments`**;
  render the doc PDF (reuse the existing `/{id}/pdf` + `document_render`) to a temp file and attach for
  **email only**. SMS uses the SMS template.
- **Status side-effect**: a successful (or log-only) email/SMS marks a quote **Sent** (reuse
  `send_quote`); invoice/SO record a "last sent" via the communication_log (no status-model change).
  Keep a **"just mark as sent, don't transmit"** option in the dialog.
- Honors the **log-only kill switch** (logs, no transmit) so it's safe before go-live. Every attempt is
  already written to `communication_log`.
- Templates to add (reuse existing tone): `{quote,invoice,so}_send_email.txt` / `_send_sms.txt`.

### 22.4 — Function B: "OK to text" on the customer profile
- Header button on `customers/detail.html` (+ communications page): **"OK to text"** (one-click) →
  `POST /customers/{id}/sms-consent` → `MessagingService.record_consent(id, SMS, 'verbal')`. Renders a
  green **"Texting OK since <date>"** chip once set.
- Companion **"Do not text"** (narrow: `allow_sms=False`, keeps email) and **"Opt out (all)"**
  (`record_opt_out` → `do_not_contact`). Optional email-unsubscribe toggle for symmetry.
- This consent state is exactly what the Function A dialog reads to enable/disable the Text channel.

### 22.5 — Testability (must cover BOTH channels)
- **Settings → Messaging card**: "Send test email" (to typed address) + "Send test SMS" (to typed
  number) → call the **real** provider, surface inline SENT/FAILED + the provider's error text. This is
  how the owner proves SMTP/Twilio before any customer ever gets a message.
- Visible **`messaging_log_only_mode`** toggle; per-customer **Communications** tab shows every send.
- **Automated tests** (a fake provider — never hit real SMTP/Twilio): `_provider_for` selection,
  consent gating on the send route (Text blocked without consent), dialog render, test-send route returns
  the provider result, log rows written, kill-switch forces `LOGGED_ONLY`, PDF attached on email path.

### 22.6 — Build order
1. `MessagingService.send()` `attachments` passthrough + a doc-PDF→temp-file helper.
2. **Settings test-send routes + UI** (safe way to verify SMTP/Twilio first).
3. **OK-to-text / opt-out buttons** (Function B) — small; unblocks SMS consent.
4. **Send dialog + routes**: quote → invoice → SO (Function A).
5. Templates + tests. **Flip `messaging_log_only_mode` OFF only after both test-sends pass.**

### 22.7 — Out of scope / deferred
Inbound SMS replies (webhook), public view-links, marketing/bulk blasts, scheduled/drip sends, Twilio
delivery-status callbacks. **Compliance**: keep SMS strict (explicit consent + honor opt-out for
10DLC/TCPA); auto-append "Reply STOP to opt out" to the first SMS to a number.

---

## 23. System Review 2026-06-29 — Verified Audit + Phase 0–6 Remediation Plan (AUTHORITATIVE)

*16-subsystem multi-agent audit → adversarial verification (re-read cited code for every high/critical risk to confirm/refute) → cross-cutting synthesis. 46 agents, ~3.4M tokens. Overall grade **B−**; Phase-1 readiness **C+ (PARTIAL)**. Full report: `ERP_AUDIT_2026-06-29.md`. Lane handoff: `HANDOFF_2026-06-29.md`.*

> **NOTE on drift vs §21.2:** §21.2 claimed a "fix_before_phase1" sprint SHIPPED 2026-06-16. The 2026-06-29 audit re-read the live code and several of those claims do NOT hold in `backend/workflow-series-3` today — re-verified by hand before this section was written. Treat §23 as current ground truth where it conflicts with §21.

### 23.1 — Verified-REFUTED (do NOT re-litigate or "fix")
- **CSRF** — fully implemented as ASGI double-submit middleware (`security.py:92`, registered `main.py:98`, tested). The old go-live note is stale.
- **Core-charge customer ≥ vendor guard** — exists + enforced at all 3 product write paths (`product_service.py:65-82`, calls 117/198/358). No path can persist an inverted core charge.
- **NSF-DRAFT money loss** — not real; BOOKKEEPING already holds REVERSE_PAYMENT + FINALIZE_INVOICE (`base.py:64-76`).
- **Alembic "silent swallow"** — covered; every 0002–0007 column mirrored in the unconditional inline list (`database.py:320-349`).
- **`test_phase2_seams` xfail** — pins an already-fixed bug (`quotes.py:407-408` reads correct keys). Drop the stale marker.
- **Inventory cache "drift"** — NOT a live accounting bug (SQLAlchemy unit-of-work prevents it). Code-duplication maintenance risk only (4 services repeat the pattern).

### 23.2 — PHASE 0 — fix-before-daily-use — ✅ SHIPPED 2026-06-29, UNCOMMITTED
*Parallelized into 7 file-disjoint lanes — see §23.4 and `HANDOFF_2026-06-29.md`. Regression files: `tests/test_s22_*` (7 files, 61 new tests). **Full non-visual suite green: 2436 passed / 0 failed** (54 visual deselected — the documented unstable Playwright suite). All 8 defects fixed; stale tests across 9 files updated to the new contracts (bill→PENDING, vendor_code uniqueness, import→needs_review).*

> **Behavior changes to know:** (1) **Default-password gate is ON in real runs** — logging in as `admin/admin` now force-redirects to `/account` until rotated (dev too; set `JAKS_ADMIN_PASSWORD` or rotate once). (2) **Clean vendor bills land PENDING** and require an explicit Approve before they're payable / QBO-eligible; the PO advances to BILLED on approval, not at bill creation. (3) **PHASE-1 FOLLOW-UP:** the dashboard `/` (post-login landing) is now ADMIN/BOOKKEEPING-gated — harmless today (no SALES user seeded), but a counter clerk will 403 on login until a SALES landing page / reduced dashboard exists.

| # | Defect (CONFIRMED) | Evidence | Lane |
|---|---|---|---|
| 1 | Reports + Dashboard have NO role gate — any clerk sees cost basis, AR aging, competitor prices | `reports.py` routes carry only `Depends(get_db)`; `dashboard.py` same; `ReportService` has zero `assert_can` | A |
| 2 | `vendors.name` + `vendor_code` not unique — duplicate PAI forks cost history, breaks 3-way match, aliases SKU namespace | `vendor.py:15` no `__table_args__`; no probe in `vendors.py` create paths | B |
| 3 | Cancelled SO lines print as live | `cancel_line` (`sales_order_service.py:265`) sets `qty_ordered=qty_invoiced`, never `qty_cancelled`; `print.html:326` only skips on `qty_cancelled>=qty_ordered` | C |
| 4 | FULL-payment gate uses subtotal not subtotal+tax — taxable special orders ship under-collected | `sales_order_service.py:458` `so_value = so.subtotal`; SalesOrder has no `.tax_amount`/`.total` (Quote does, same file `models/quote.py`) | C |
| 5 | Vendor bills auto-approve past AP | `po_service.py:867` sets APPROVED on clean match; `approve_bill:1108` refuses already-APPROVED | D |
| 6 | QBO posts every invoice + payment on the sync date | `qbo_service.py:806-819`/`:371-379` omit `TxnDate` (vendor-bill payload `:498` sets it — proven pattern) | E |
| 7 | Garbage categories on direct `full_import` | `product_import_service.py:1717-1732` creates any level-1 category, no allowlist, no `needs_review` | F |
| 8 | admin/admin echoed in launch .bat, no forced rotation, `JAKS_ENV` unset, LAN-exposed | `Start JAKS ERP.bat:12`; `main.py:295/317` seed admin/admin + bookkeeper/bookkeeper; banner dismissable | G |

**Phase 0 success test:** SALES user → 403 on `/reports/*` and `/dashboard`; second "PAI" refused; taxable FULL SO won't fulfill until tax collected; cancelled SO line prints marked/hidden; clean vendor bill lands PENDING with an Approve button; fresh QBO push books on the txn date; non-allowlisted import category goes to `needs_review`; default password forces rotation + `JAKS_ENV=production` in both launchers.

### 23.3 — PHASE 1–6 (dependency-ordered; each phase = Goal / Difficulty / Value)
**Phase 1 — Stabilize the spine** *(Med / High)* — ✅ **ALL 6 ITEMS SHIPPED** (uncommitted): multi-PO single-shipment receiving + fix last-PO-only status loop (`po_service.py:489`); `competitor_part_number_norm` column+index+listener+`_TARGETS` before loading ATL/HHP; `qbo_push_batch` → plain `def` (stop event-loop freeze); net `credit_balance` into `available_credit`; engine picker → Manufacturer table + `POST /products/reclassify-all` (dry-run); cross-vendor OEM xref check before creating a NEW product (stop PAI/IMB dup products → add vendor source instead). *Success: receive a 3-PO truck in one action ✅; HHP-number search instant ✅; IMB re-import of a PAI part adds a source not a 2nd product ✅.*

> **Phase 1 progress (2026-06-30, uncommitted):**
> - ✅ **Net `credit_balance` into `available_credit`** — held account credit now offsets AR exposure in all three credit computations (headroom, `would_exceed_credit` over-limit test, projected-balance message) so they stay mutually consistent: `available = limit − open_ar + credit_balance`. Sites: `customer_metrics_service.py`, `customer_service.py` (credit_status + would_exceed_credit). Test `tests/test_phase1_spine_fixes.py`; updated `test_customer_metrics.py` (−200→−175, the corrected netted value).
> - ✅ **`qbo_push_batch` event-loop freeze** — the blocking per-invoice push loop (each a sync httpx round-trip to Intuit) now runs OFF the event loop via `run_in_threadpool` (new plain helper `_run_push_batch`; route stays async to read the request body). JSON contract unchanged; per-invoice independence preserved. Test `tests/test_phase1_spine_fixes.py` + existing `test_qbo_push_batch.py` still green.
> - ✅ **Multi-PO single-shipment receiving — DONE (backend + UI, verified live).** The model already supported it (`POReceipt.vendor_id` + `POReceiptLine.po_id` per line). **Backend:** `create_receipt` now re-evaluates the status of EVERY PO touched (was last-PO-only) so a multi-PO receipt can close one PO while leaving another partial, + a cross-vendor guard (a receipt spans one vendor); new `get_open_receivable_lines_for_vendor()`. **UI:** vendor-scoped **Receive Shipment** screen (`GET/POST /purchase-orders/receive-shipment`, `receive_shipment.html`) — vendor picker → every open line across that vendor's receivable POs grouped by PO, in ONE form; "Receive All", per-line condition/serials (reuses the per-PO receive markup + existing primitives, no new UI). Entry point on the Receiving Queue header. The per-PO `/{po_id}/receive` path is unchanged. Tests: `tests/test_phase1_multi_po_receiving.py` (5, service) + `tests/test_phase1_receive_shipment_route.py` (4, E2E). Live-verified: received across PO-2026-0004 (→RECEIVED) + PO-2026-0005 (→PARTIAL, 4 outstanding) in one action; test POs cleaned up. *Screen is a workspace form (not a governed L2 list) — flagged for a UI-governance pass but introduces no new primitives.*
> - ✅ **Cross-vendor OEM xref dedup on import** — `full_import` snapshots every existing OEM `CrossReference` (normalized number → product id) once per run; a row not matched by the vendor-namespaced `(vendor_code, vendor_sku)` key but whose OEM number already belongs to a product from ANOTHER vendor's feed now adds this vendor as a `ProductVendorSource` on that product instead of minting a duplicate (the classic PAI→IMB overlap). Merges any new OEM numbers; refreshes in place (not a duplicate insert) if the SAME vendor already carries the product under a renumbered part#; a norm spanning >1 existing product (pre-existing catalog dupes) is left ambiguous on purpose — no auto-guess. New `summary["matched_by_xref"]`/`["xref_match_ambiguous"]` counters. Test `tests/test_phase1_oem_xref_dedup.py` (7 cases).
> - ✅ **`competitor_part_number_norm`** — new indexed column on `CompetitorPrice` (mirrors `ref_number_norm`/`vendor_*_norm`/`sku_norm`: `before_insert`/`before_update` listener via the shared `_norm_part_value`, added to `search_index._TARGETS` for startup backfill+index, + the `database.py` ALTER-column parity list). `search_service`'s competitor-number strategy now reads the indexed column instead of wrapping every row in a SQL normalize function per keystroke — the last unindexed part-number search path. Test `tests/test_phase1_competitor_part_number_norm.py` (6 cases).
> - ✅ **Engine picker → Manufacturer table + `POST /products/reclassify-all`** — the `engine_picker` macro's Make dropdown now reads `CategoryService.engine_make_names(db)` (the owner-maintained `manufacturers` table, `["Other"]` appended since `seed_manufacturers()` deliberately excludes it as a free-text escape hatch) instead of the hardcoded `constants.ENGINE_MAKES`, across all 4 call sites (invoices/SO/quote workspaces + product new/detail) — an owner rename in Category Maintenance now shows up in the picker immediately, no more drift between two parallel lists. `ENGINE_MODELS_BY_MAKE` stays a constant (not DB-backed) — a make with no entry falls back to `['Other']` in the macro's own JS. New `ProductImportService.reclassify_all(dry_run=True)` (route `POST /products/reclassify-all`, mirrors `backfill-manufacturers`) re-runs `ClassificationService.classify()` across every active product missing category/engine-make/engine-model, filling ONLY blanks — never overwrites an already-confirmed value, even if the classifier now suggests something different. UI: a second "Catalog Utilities" card on `products/import.html` (Preview/Apply, mirrors the existing manufacturer-backfill card). **Bug found + fixed while wiring the UI:** BOTH this card's and the pre-existing manufacturer-backfill card's `fetch()` POSTs never stamped the CSRF header — `base.html`'s `csrfToken()` is scoped inside its own IIFE (unreachable from other templates) and `htmx:configRequest` only stamps `hx-*` requests, not plain `fetch()` — so both had 403'd since CSRF was added (§21.3), completely silently (never reported, likely never actually used). Live-verified: `POST /products/reclassify-all` → 403 before the fix → 200 after, both cards now apply real changes to the live catalog. Tests `tests/test_phase1_engine_manufacturer_and_reclassify.py` (13 cases, incl. a CSRF-wiring regression + a live route dry-run/apply test).

**Phase 2 — Product & inventory cleanup** *(Med / High)* — ✅ **ALL 6 ITEMS SHIPPED** (committed, not yet pushed): ProductApplication batch importer (sku/make/model/cpl/esn_range); dead-stock report (`get_dead_stock`+route+template); reorder-to-PO from low-stock; brand merge + constrain brand/manufacturer to managed lists; markup-tier admin UI; inventory-valuation cost-source callout.

> **Phase 2 progress (2026-07-01):**
> - ✅ **Inventory-valuation cost-source callout** (@54ba0f4) — valuation MATH unchanged (still raw `Product.cost`, per the locked rule); adds new `totals.cost_source_breakdown` (receipt/manual/vendor counts) + `totals.zero_cost_recoverable_count` (zero-cost SKUs that DO have an active vendor source, i.e. `effective_cost` would price them non-zero) via a correlated `EXISTS` (not a join — no GROUP BY fan-out). Per-row `cost_source`/`recoverable_cost` in the detail table + CSV export. Test `tests/test_phase2_valuation_cost_source.py` (6).
> - ✅ **Dead-stock report** (@54ba0f4) — new `ReportService.get_dead_stock(days=90)`: active products with stock on hand and no sale via a finalized, non-core invoice line within the window — companion to Low Stock. "Last sold" is a single GROUP BY subquery (never materializes invoice_lines/invoices in Python). Never-sold rows sort first. Route `GET /reports/dead-stock` (+CSV), nav entry, landing card. Test `tests/test_phase2_dead_stock.py` (13).
> - ✅ **Reorder-to-PO from Low Stock** (@54ba0f4) — new `POService.create_pos_from_reorder(items)`: groups checked rows by each product's PREFERRED ACTIVE vendor source into ONE draft PO per vendor. Route re-derives qty from the SAME `get_low_stock()` data the page rendered (a submitted qty can never override it); an empty submission is always "nothing selected," never "every row." Checkboxes + bulk action on `low_stock.html`. Test `tests/test_phase2_reorder_to_po.py` (10). **Live-verified**: created a real PO with correct vendor/qty/cost, then cleaned up the test artifacts.
> - ✅ **Brand merge + constrain brand/manufacturer to managed lists** (@54ba0f4) — new `CategoryService.merge_brand`/`merge_manufacturer` (mirrors `merge_category`, always soft-deactivates src, never hard-deletes). `merge_manufacturer` reassigns BOTH `Product.manufacturer` and `Product.engine_manufacturer` — the `Manufacturer` model docstring calls them "the same concept" and both now read the one managed table. The product-form Manufacturer field used to read a hardcoded 8-entry `MANUFACTURERS` constant with no owner-editable backing; Brand was plain free text on both `new.html`/`detail.html`. Both are now constrained `<select>`s (new `manufacturer_names(db)`/`brand_names(db)`), legacy-preserving fallback (an out-of-list value still shows selected). `routers/products.py`'s own `MANUFACTURERS` constant is untouched — separate import-canonicalization vocabulary. Also fixed a found-in-passing gap: `product_update`'s 422 re-render was missing `engine_makes`/`engine_models_by_make`/`category_tree` entirely. Tests `tests/test_phase2_brand_manufacturer_merge.py` (13). **Incident during live-verify:** clicking the FIRST "Merge" button found (not a fresh test row) merged the real "PAI" brand into "Interstate-McBee" on the dev catalog — 20,638 products' `brand` field + the PAI Brand row deactivated. Recovered via `ProductVendorSource.vendor_id` (untouched by the merge — it only wrote `Product.brand`) with explicit owner sign-off before the repair write. See `[[live-verify-bulk-mutation-caution]]` memory — going forward, live-verify destructive/bulk actions ONLY against fresh throwaway test records.
> - ✅ **Markup-tier admin UI** (pending commit) — closes the exact gap the old preview route's own copy flagged ("Editing the tiers and the Activate toggle are pending Backend save routes"). New CRUD routes `POST /settings/pricing/tiers` (create/update/delete) + `POST /settings/pricing/toggle-active` (flips `markup_tiers_active`), inline in `routers/settings.py` matching `/settings/locations`'s own no-dedicated-service convention (`PricingService` is documented "stateless math — does NOT write to the database", so CRUD doesn't belong there). Full editable tier table + Activate-grid toggle on the Settings → Pricing tab, replacing the old read-only preview-only table (the Preview-impact dry-run itself is unchanged, still useful pre-activation). Delete is a real hard-delete (no FK from Product — tiers are matched by cost-bracket at read time, not referenced per-row). Test `tests/test_phase2_markup_tier_admin.py` (13).
> - ✅ **ProductApplication batch importer** (pending commit) — new `ProductImportService.import_applications(text, dry_run=True)`: CSV of sku/make/model/cpl/esn_range applied onto EXISTING products only (same never-create contract as every `pricing_update_*` mode). Idempotent on the model-docstring-locked dedup grain (product_id, engine_make, engine_model, cpl) — a repeat row refreshes `esn_range` instead of duplicating. Bulk-committed (one commit-or-rollback), not per-row, unlike `ProductService.add_application`'s own per-call commit — a real applications feed can be thousands of rows. New "Applications (Engine Fitment)" mode on the Import screen. **Bug found + fixed while wiring this:** `productImport()`'s `fetch()` also never stamped the CSRF header — the ENTIRE product importer (Full Import + Pricing Update + this new mode), the PRIMARY catalog-ingestion path for the whole 31k-SKU business, had 403'd since CSRF was added, silently. Fixed with the same local `_csrfToken()` helper used for the two Catalog Utilities buttons (§23.3 Phase 1). Test `tests/test_phase2_applications_importer.py` (11, incl. the CSRF regression).
>
> Full suite (server down): 2636 passed after items #2/#3/#4/#6; items #1/#5 added 24 more passing tests on top (final count pending the closing full-suite run). All 6 items live-verifiable via automated TestClient coverage; #3 was also verified live in the browser. Not yet pushed as of this note.

**Phase 3 — Core lifecycle hardening** *(Low–Med / Med–High)* — default `warranty_require_esn=true` + blank-ESN warning; require/auto-create a VCR for single-core submits; standalone overdue-cores report; warranty search by ESN + replacement-parts tracking (`replacement_invoice_line_id`); ProductSerialNumber lookup screen.

**Phase 4 — Reporting & controls** *(Low–Med / High)* — AP aging on bills list + `paid_at` + partial-bill payments; customer purchase-history drill-down; render the computed revenue-trend chart; fix dashboard low-stock count cap + monthly-revenue discount math; per-IP failed-login throttle; drop stale xfail; add duplicate-payment + over-fulfill tests.

**Phase 5 — Integrations** *(Med / Med)* — QBO `LinkedTxn`+`TxnDate` on credit memos; "Set up items" forced prerequisite + Test Connection; optional nightly auto-push; Smart Import cross-vendor merge surfaced in review queue; index `vendor_sku` in enrichment; verify AI model id (`ai_categorization_service.py:46`).

**Phase 6 — Sellable polish (only if Option B committed)** *(High / conditional)* — multi-tenant isolation + onboarding + per-tenant secrets; Postgres migration + true row locks; session-token revocation; ProductKit BOM explosion; ESNLookup/EngineConfig integration.

### 23.4 — DO NOT BUILD YET
ProductKit BOM explosion · ESNLookup vendor-portal API · messaging delivery (keep log-only) · eBay · multi-tenant infra · more AI/automation depth · Postgres/row-locks/session-revocation · anything cosmetic — until Phase 0 is in and JAK's has run on it daily for 60–90 days.

### 23.5 — Direction (recommended): **Option A — keep building as JAK's internal ERP first**, then earn Option B (sellable) after the Phase-0 list lands and a 60–90-day daily-use trial holds. Hard rule: **no scraper/import data reaches the product master except via staging → review → approval** (pipeline exists; the only leak is the *direct* `full_import` path — Phase 0 #7).

### 23.6 — Live headed-browser QA 2026-06-30 (complements §23; observed defects the static audit could not see)
*A visible Playwright pass drove the full chain Customer→Product→Quote→SO→PO→Receive→Invoice→Payment→Core, breaking each step, then root-caused every symptom in code/DB. Full report: `QA_REPORT_2026-06-29_headed_browser.md`. The money/inventory engine is correct (totals carry through, moving-avg cost on receipt, oversell blocked, cores tracked). The blockers below are mostly **recent-hardening regressions that only manifest in the browser** — not contradicting §23.1's "CSRF works" (it does — that is exactly why the modal `.submit()` path is rejected).*

**🔴 CRITICAL — ✅ BOTH FIXED + LIVE-VERIFIED 2026-06-30 (uncommitted):**
1. **New Product won't save (silent).** `app/templates/products/new.html` Margin % input had `step="0.1"` but is auto-filled with a 2-decimal value (e.g. `23.08`) → HTML5 marks the form invalid → *Save Product* fired no request, no error. Field has no `name` (display-only). **FIXED:** Margin **and** Markup % both → `step="any"` (Price is `step="0.01"` = matches `r2()`, left as-is; the productPricing factory rounds all boxes to 2 decimals so Markup had the identical trap). **Verified:** filled cost $850 (margin auto-fills `23.08`), `form.checkValidity()` now `true`, clicked the real *Save Product* → product #30935 created.
2. **Confirm-modal actions fail CSRF.** `app/templates/macros/confirm_modal.html:133` submitted via `f.submit()`, which skips the submit-event CSRF stamper (`base.html:2038`) → POST has no `_csrf` → middleware rejects. Broke **SO "Fulfill & Invoice"**, **Quote "Convert to Invoice"**, **Close Credit Memo**, **Category merge**. **FIXED:** `f.submit()` → `f.requestSubmit()`. **Verified:** clicked the real *Fulfill & Invoice* button → **INV-2026-0002 created from SO-2026-0002, no CSRF error**. *Two sibling instances of the same class also fixed:* `purchase_orders/workspace.html:1111` (inline confirm modal → "Cancel PO" `formId` path) and `payments/new.html:33` (customer-select `onchange="this.form.submit()"` on a POST form) — both `.submit()` → `.requestSubmit()`; verified `requestSubmit()` present + no bare `.submit()` in live-served HTML.

**🟠 HIGH:**
3. **New/special-order parts quote & sell at $0.00 until first receipt. — ✅ FIXED + LIVE-VERIFIED 2026-06-30.** Estimated sell = `product.cost × markup` (`models/product.py`); `product.cost` (moving-avg COGS) is 0 until a receipt, even though `vendor_sources.vendor_cost` is known. **FIXED:** new `Product.effective_cost` property = COGS when received, else the preferred (—else any active) `vendor_sources.vendor_cost`; both pricing layers route through it — `Product.selling_price` (the quote/SO/invoice line-default source via `apply_product_line_defaults`) and `PricingService.sell_price_for` (search/CSV/pickers/preview, guarded so received-stock paths keep zero-query behaviour). **Verified:** product #30935 (cost 0, vendor_cost $850, the QA-repro CAT C15 head) added to a real draft quote (Q-2026-0004) via the real `add_line` endpoint with `unit_price=0` persisted at **$1105.00** (was $0.00); margin display still reads ~100% pre-receipt (expected, COGS-based, unchanged). Regression test `tests/test_zero_cost_vendor_fallback.py` (7 cases); full suite 2456 pass. *Known minor edge left as-is: with `markup_tiers_active=true` (off by default) an un-received part's markup tier is matched on cost 0, not the substituted vendor cost.* Optional follow-up not done: a hard "no cost on file" warning on a still-$0 line.
4. **No logout / no Account link in the UI; avatar hardcoded "K"/"Keith"** (`base.html:685-690`). `/logout` in zero templates; `/account` only in the conditional banner. **✅ FIXED 2026-06-30 (uncommitted):** new `resolve_current_user` ASGI middleware (`security.py`, registered `main.py:176`) → `request.state.current_user`; `base.html:692-756` renders a real avatar dropdown with initials from the logged-in user (falls back username-initial → `?`), **Account settings** (`/account`) + **Sign out** (`/logout`). Test `tests/test_chrome_and_security.py`.

**🟡 MEDIUM — ✅ ALL FIXED 2026-06-30 (uncommitted):**
- taxable customers' quotes/invoices defaulted to **"Exempt"** + `default_sales_tax_rate=0` — **FIXED:** doc taxable flag now defaults from `customer.is_tax_exempt` alone (`quote_service.py:60-61`, mirrored in `invoice_service.py`/`sales_order_service.py`); totals panels relabel **"0% — no rate set"** vs "Exempt" (`quotes/_totals.html`, `invoices/_totals_panel.html`, detail/print). Now honors the §21.1 "default-from-customer + clerk-toggle" decision on the document default. *Owner operational prerequisite remains: set a real `default_sales_tax_rate`/jurisdiction before go-live.* Test `tests/test_tax_default_and_core_liability.py`.
- **"Today's Cash" = $0** after a same-day payment — **FIXED:** `_local_day_utc_window()` in `dashboard.py` buckets by the shop's LOCAL day (local midnight→midnight converted back to naive-UTC half-open window; offset from OS `datetime.now().astimezone()`). Test `tests/test_dashboard_todays_cash.py`. *Deferred twin: `statement_service.py:141-142` + dashboard Monthly-Revenue card share the pattern → Phase 4.*
- dup-customer detection **email-only, not phone** — **FIXED:** `normalize_phone()` (`customer_service.py:35`) → soft-**warn** on normalized-phone collision (distinct from the hard email block; shared phone is legit for a 2nd contact). Test `tests/test_customer_validation.py`.
- **Inventory Valuation report ~27s** — **FIXED:** `get_inventory_valuation_summary()` (`report_service.py:631`) one-SQL `GROUP BY` (value/units/SKU/zero-cost by category) + pagination + drill-down (`reports/inventory_valuation.html`, `routers/reports.py`); **~27s → ~560ms.** Test `tests/test_inventory_valuation_perf.py`.
- **Google Fonts blocked by CSP** — **FIXED:** `security.py:208-209` allows `fonts.googleapis.com` (style-src) + `fonts.gstatic.com` (font-src). Test `tests/test_chrome_and_security.py`.
- negative cost + invalid email accepted server-side — **FIXED:** `product_service.py:88-116` rejects negative money on create/update/autosave/quick-create (0 still allowed); `is_valid_email()` (`customer_service.py`→`customers.py:34`) rejects malformed email pre-persist. Tests `tests/test_product_validation.py`, `tests/test_customer_validation.py`.

**🟢 LOW — ✅ ALL FIXED 2026-06-30 (uncommitted):** raw-JSON 404 → friendly HTML 404 handler (`main.py:618` → `errors/404.html`; browser/non-HTMX only) · `Cache-Control: no-store` on auth HTML (`security.py:241-255`) · Alpine Focus plugin vendored (`static/vendor/alpine-focus.3.14.9.min.js`, `base.html:31`) so `x-trap` works · login `required` (`login.html:44/49`) · JAKS→Axle page-title sweep (19 templates, zero "JAKS Inventory" titles remain; printed "JAK's Diesel" company name kept intentionally per [[axle-rebrand]]) · phantom "$0.00 < $0.00" core banner (`products/new.html:500-505` `coreFlag` returns false when core off + requires strict cust<vend) · Invoice-Intelligence "Core Liability $0.00" on a $250-core invoice → draft core-liability now falls back to invoice core lines (`invoice_metrics_service.py`).

**Verified WORKING (do not re-litigate):** quote→SO→invoice→payment totals + bidirectional linking + double-convert block; receipt-driven inventory with correct moving-avg cost; **oversell blocked** (clear message + admin `NEGATIVE_INVENTORY_OVERRIDE`); invoice finalize→read-only lock; payment→PAID/$0; full core lifecycle (separate customer/vendor amounts, never taxed, return→credit with auto receipt slip, server-enforced customer≥vendor guard); CSRF middleware genuinely active; duplicate vendor-part/SKU blocked (422); cross-entity search incl. phone; 14-report suite; dashboard accurate except Today's Cash.

**Recommended sequencing:** ✅ **§23.6 IS NOW FULLY CLEARED (uncommitted).** CRITICAL #1/#2 committed (@2cbb89e/@293e34f); HIGH #3 + HIGH #4 + every MEDIUM + every LOW shipped via a 6-lane file-disjoint batch (Lane A global chrome/security · B tax-defaults + core-liability · C Today's-Cash · D customer validation · E product validation + phantom-banner · F valuation perf) plus a serial branding-title sweep. **Full suite: 2524 passed / 55 skipped / 3 xfailed / 3 xpassed / 0 failed** (197s). 7 new `tests/test_*.py` document each lane. Only non-code residuals: (a) owner configures a real tax rate/jurisdiction before go-live; (b) the deferred `statement_service.py:141-142` + Monthly-Revenue utcnow twin → Phase 4. **Next real work: commit this batch, then Phase 1 — Stabilize the spine (§23.3).**

---

*This document is the single source of truth for all JAKS Inventory build decisions.*
*Update it as decisions change. All other planning documents are superseded.*
*Last updated: 2026-06-30 (later-3) — **§23.6 FULLY CLEARED (uncommitted) — HIGH #4 + every MEDIUM + every LOW fixed via a 6-lane file-disjoint batch + serial branding sweep.** Lane A: avatar dropdown w/ real initials via `resolve_current_user` middleware + Account/Sign-out, CSP allows Google Fonts, `Cache-Control: no-store` on auth HTML, friendly HTML 404, Alpine Focus plugin vendored, login `required`. Lane B: doc taxable flag defaults from `customer.is_tax_exempt` + "0% — no rate set" vs "Exempt" labels + draft core-liability falls back to invoice core lines. Lane C: Today's-Cash local-day UTC-window bucketing. Lane D: normalized-phone dedup (soft-warn) + server-side email validation. Lane E: server-side negative-cost guard + phantom "$0<$0" core-banner fix. Lane F: SQL GROUP-BY valuation summary + pagination (~27s→560ms). Branding: 19 templates, zero "JAKS Inventory" page titles remain (print "JAK's Diesel" company name kept intentionally). 7 new `tests/test_*.py`; CSS rebuilt. **Full suite 2524 passed / 55 skipped / 3 xfailed / 3 xpassed / 0 failed.** Residuals: owner configures a real tax rate; deferred `statement_service`/Monthly-Revenue utcnow twin → Phase 4. Prior: 2026-06-30 (later-2) — **§23.6 HIGH #3 $0-pricing FIXED + LIVE-VERIFIED (uncommitted):** new `Product.effective_cost` (COGS when received, else preferred/any-active `vendor_sources.vendor_cost`) now drives both `Product.selling_price` and `PricingService.sell_price_for` (guarded — received-stock paths keep zero-query). Un-received special-order parts quote at vendor-cost×markup instead of $0. Verified live: QA-repro product #30935 (vendor_cost $850) added to quote Q-2026-0004 via the real `add_line` endpoint persisted at **$1105.00** (was $0.00). New `tests/test_zero_cost_vendor_fallback.py` (7 cases); full suite **2456 pass**. CRITICAL #1/#2 already FIXED+PUSHED (@293e34f/@2cbb89e). Still open: HIGH #4 logout-UI, MEDIUM tax-defaults-exempt + Today's-Cash UTC. Prior: 2026-06-30 (later) — **§23.6 CRITICAL #1 + #2 FIXED + LIVE-VERIFIED (uncommitted):** New-Product Margin **and** Markup % → `step="any"` (factory rounds to 2 decimals → both had the `step="0.1"` trap) — verified by creating product #30935 through the real Save button; confirm-modal `formId` submit `confirm_modal.html:133` `.submit()`→`.requestSubmit()` — verified by creating **INV-2026-0002 from SO-2026-0002** through the real Fulfill & Invoice button (no CSRF error). Two sibling `.submit()`→`.requestSubmit()` fixes in the same pass: `purchase_orders/workspace.html:1111` (Cancel-PO confirm) + `payments/new.html:33` (customer-select onchange). HIGH $0-pricing + tax-default + Today's-Cash items remain open. Prior: 2026-06-30 — **added §23.6 Live headed-browser QA** (complements §23): visible Playwright E2E pass found 2 CRITICAL browser-only regressions — (1) New Product silently won't save (`products/new.html` Margin `step="0.1"` vs auto-filled `23.08`; fix `step="any"`), (2) confirm-modal `formId` actions fail CSRF (`confirm_modal.html:133` `.submit()`→`.requestSubmit()`; breaks Fulfill&Invoice / Convert-to-Invoice / Close-Credit-Memo / Category-merge) — plus HIGH $0-pricing-until-receipt (`product.cost`=COGS=0 pre-receipt; fall back to vendor_sources.vendor_cost) and no-logout-UI/hardcoded-avatar; MEDIUM tax-defaults-exempt+0%, Today's-Cash UTC-vs-local, phone-dedup, 27s inventory-valuation, CSP-blocks-fonts; engine/money chain otherwise verified working live. Report: `QA_REPORT_2026-06-29_headed_browser.md`. Prior: 2026-06-29 — **added §23 System Review 2026-06-29** (AUTHORITATIVE): 16-subsystem multi-agent audit + adversarial verification → overall B−, Phase-1 readiness C+. §23.1 lists 6 verified-REFUTED claims (CSRF, core-charge guard, NSF, Alembic-swallow, phase2_seams xfail, inventory drift — do NOT re-litigate); §23.2 = PHASE 0 fix-before-daily-use (8 CONFIRMED defects, re-verified by hand against live code; several §21.2 "shipped" claims do NOT hold today — drift), parallelized into 7 file-disjoint lanes A–G; §23.3 = Phase 1–6 dependency-ordered roadmap; §23.4 do-not-build-yet; §23.5 direction = Option A (internal first). Full report `ERP_AUDIT_2026-06-29.md`, lane handoff `HANDOFF_2026-06-29.md`. Prior: 2026-06-17 — **added §22 Customer Communications** (plan only): wire real Email/SMS "Send" on quote/SO/invoice + an "OK to text" consent button on the customer profile + Settings test-send for both channels. Found the messaging engine already complete (SMTP+Twilio, log-only kill switch, consent + communication_log) but unwired; "Send" on a quote today only flips status (transmits nothing); invoices/SOs have no send. 4 recommended decisions captured (one Send button→channel picker · email=PDF/SMS=heads-up · one-click verbal OK-to-text · Settings "Send test"). Build order + testability defined; nothing built yet. Prior: 2026-06-16 — **added §21 Update 6.16**: verified 16-subsystem sellable-ERP audit (C+), 7 owner decisions LOCKED (credit-hold=warn, over-receipt=confirm-allow, quote-tax=default-from-customer+clerk-toggle, competitor-xrefs=normal-search, **deployment INTERNET-EXPOSED → security now-blocker**, multi-tenant=someday, jaks.db=throwaway-but-LIVE-QBO). **§21.2 immediate sprint (11 fixes), §21.5 security blockers (CSRF+headers+Secure cookie, live-verified), and §21.6 Phase-1.1 batch (6 items) ALL SHIPPED UNCOMMITTED** — full suite 1947 pass / 12 fail (all 12 pre-existing); #1 vendor_cost-clobber REFUTED, #12 competitor-xref = owner data-load. New test files test_s21_audit_fixes.py / test_s21_csrf_security.py / test_s21_phase11.py. See §21. Prior: 2026-06-10 (evening status-refresher reconciliation) — **R1+R2+R3+R4 all SHIPPED** (`248ea09`/`40eea95`/`a53bbe2`/`674491a..f18aec9`); ledger reconciled: QBO 1B marked COMPLETE (payments/vendor-bills/credit-memos push + Fernet, R2/R3), serials marked BUILT (R3), §17.2 closed rows struck (tier-pricing · Fernet · demo-reset guard · products export), §11 O2 marked enforced. **Remaining gate = owner-run R4 trial sheet (blank) + operational cutover**, not code. Prior note: **§19 added (verified system review, B−, authoritative punch list) and Sprint R1 §19.2 implemented the same day** (16/16 money/integrity fixes, adversarially verified, 1438 tests green). Next: §19.3 Sprint R2. Prior note (2026-06-06) — status-refresher pass: 983 tests verified green; tier-pricing downgraded from blocker to a label decision (money path proven real); real-data cutover (13,153-part catalog) recorded as the live operational gate; owner-acceptance breadth named as the one true status gap. See §17 banner. **Added §18 Product Categorization & Classification spec** (Inventory → Category Maintenance screen; Products-List filters/bulk-assign/Manage-Categories link; importer rules + Import Review queue; Brand/Vendor/Manufacturer-Engine-Make separation) — **BUILT & verified the same night** (increments 1–7; 1051 tests pass; backfill applied to the live 13,154-part catalog). Not yet git-committed. See §18 banner. **2026-06-16: added §20 — REVERT the opaque SKU scheme to vendor part numbers** (owner interview; reverses the 2026-06-06 `JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]` scheme that is live on all 29,659 products and caused too much confusion); plan = `product.sku ← vendor_part_number`, private-label parts (`is_house_brand`) keep a separate owner-typed JAKS Product # while the vendor # still prints on the PO; revert verified safe (0 collisions, trial-only documents) — dry-run pending owner review. See §20.*
