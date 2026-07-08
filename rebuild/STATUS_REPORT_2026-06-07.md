# JAKS Diesel ERP — Full Status Report Card
**Date:** 2026-06-07 · **Branch:** backend/workflow-series-3 · **Method:** 16 code-grounded subsystem audits + adversarial verification of every high/critical risk + cross-cutting synthesis. Graded against the actual running code paths, **not** plan docs or self-reports.

**Ground-truth test run (this report):** `4 failed, 1120 passed, 55 skipped, 5 xfailed, 1 xpassed` in 60.8s. The plan banner claims "2 reds" — there are **4**, and two are smart-import classification failures the plan never mentions (`test_s18_classification`).

---

## 1. Overall Program Status — Grade: **C+**

This is a **real, mostly-correct ERP with a genuinely diesel-shaped data model and a money spine that works end-to-end** — not a prototype. It is also **not yet trustworthy for unattended daily operations**, because a cluster of *silent* correctness defects make the numbers it reports wrong, and the catalog data layer (cost + SKU) is not loaded. The architecture deserves a B+/A; the integrity, data-load, and reporting-correctness layer is a C. Blended, weighted toward the money/inventory paths: **C+**.

**Fully working (verified end-to-end):**
- Quote → Sales Order → PO → Receive → Invoice → Payment spine, with **atomic single-commit finalize**, correct AR balance, and void rollback that reads real `INVOICE_SALE` ledger rows.
- Moving-average COGS written on PO receipt (`inventory_service.py:292-318`).
- Core-charge lifecycle: auto-add `CORE_CHARGE` child line at finalize, customer return with **single-credit idempotency guard**, inspection hold, per-core location trail.
- Returns (RA) full lifecycle with auto credit memo on close (idempotency-guarded) + inventory restock.
- **Credit memo issuance UI exists** (route + templates + "Issue Credit Memo" button) — the audit's "no credit memo UI" claim was **refuted** on verification.
- 3-way match with working **Correct & Reconcile** override path.
- Auth enforced globally (`enforce_login` middleware), PBKDF2 passwords, HttpOnly+SameSite=Lax session cookie, demo DB-wipe gated behind admin + production-env check.

**Partially working:** Reports (math polluted by core charges), Search (correct logic, but defeats its own indexes), Customers (AR aging bar silently dead), Sales Orders (deposit-cancel wiring broken), Smart Import (review queue is a dead-end — no Apply route reachable).

**Built but weak:** Product master (strong model, but FK enforcement OFF and four child tables unindexed), Categories/Vendors maintenance (no merge/reparent/rename-cascade), Serial tracking (skeleton only).

**Missing:** QBO payment push, vendor-bill payment/AP aging, ESN capture on warranty claims, serial capture/consume, CSV export backends (8 dead buttons), reorder/dead-stock/vendor-performance/quote-conversion reports.

**What blocks real daily use today:** (a) all 13k PAI parts have `cost=0` → every margin reads ~100% and inventory valuation reads ~$0; (b) no JAKS SKU on any part until `backfill_sku_scheme.py` is committed/run; (c) sales/margin reports count core deposits as revenue; (d) deposit-cancel silently orphans money; (e) suggested-sell upsells quote retail to fleet/dealer customers; (f) FK off + unindexed child tables.

**Phase-1 ready:** the money spine, receiving/inventory ledger, cores, RA, credit memos, payments, PO lifecycle.
**Not Phase-1 ready:** QBO (it's effectively Phase 1.1), smart-import apply path, serial tracking, the reporting-correctness and data-load items above.

---

## 2. Screen-by-Screen Review

> Format: **Screen [grade] — status.** Key issues.

### Dashboard `/` — **A-**
Live queries throughout (7 KPI tiles, revenue chart, top customers, follow-ups, alerts). *Confusing:* "Open Quotes" tile counts only `draft` (`dashboard.py:59`) — sent-but-unconverted quotes don't show. *Risky:* Top Customers lifetime sum includes core deposits. *Cleanup:* Open-Cores value duplicated inline (`dashboard.py:91-94`) instead of calling `ReportService`.

### Products List — **B** / Product Detail — **B+** / Inventory Adjust — **A-**
*Works:* autosave, tabbed edit, live Alpine pricing math, end-to-end inventory adjustment. *Confusing:* list price + margin badge use `Product.selling_price` (hardcoded 30% fallback, `product.py:300`) — disagrees with the tier price the quote line will compute. *Missing:* no vendor part-number column; no Inventory Transaction History tab; no "Create PO for this product" from a low-stock row. *Risky:* vendor/category sort loads the **entire 13k table into Python** (no SQL LIMIT); a wrong `unit_cost` on adjust permanently skews moving-average with no undo.

### Customers List — **B** / Customer Detail — **B+**
*Works:* 5 tabs, CRM, statements, credit flows all functional. *Risky:* **AR aging bar never renders** — `_balance_widget.html` reads `d1_30/d31_60/d61_90/d90_plus` but the backend returns `1_30/31_60/61_90/over_90`; the 90+ warning never fires. *Confusing:* `last_contacted` shows a raw datetime string; credit-hold stripe keys only off `customer_status`, so a hold set via the flag editor shows no stripe. *Missing:* customer-type edit on the detail form (only `new.html` has it — stale comment at `detail.html:289-290` says "once P2-D1 ships," but it shipped); multi-contact/multi-address edit UI; pagination; working Export CSV.

### Quotes List — **B** / Quote Workspace — **B-**
*Works:* the 45-second hot path is genuinely fast — 2-char search across SKU/OEM/cross-ref/vendor-SKU, one-click add fills price/cost/disc, cores auto-attach, warranty tier picker. *Risky:* **suggested-sell chips quote retail to tiered customers** (`_line_row.html:484,503` hardcode `unit_price` into `hx-vals`, bypassing `PricingService`); optimistic locking silently disabled (hidden `updated_at` not injected); warranty chip price wrong for unconfigured products. *Missing:* **Duplicate Quote is unreachable** (service method exists, no route/button); structured lost-reason picker is passed by the route but the UI takes free text instead. *Risky:* N+1 product load in the Jinja margin loop on the list; 150-row hard cap with no "showing 150 of N" warning.

### Sales Orders List — **B** / SO Workspace — **B-**
*Works:* SO → fulfill → invoice end-to-end; backorder → draft PO → link → FIFO allocate on receipt; deposit collect + auto-allocate on fulfill. *Risky (correctness bugs):* **deposit cancel always leaves the deposit unapplied** — the resolution `<select>` sits *outside* the cancel `<form>` with a hardcoded hidden `leave_open` (`sales_orders/workspace.html:94-105`); `cancel_line` can set `qty_ordered=0` and prematurely flip the SO to INVOICED; HOLD + deposit cancel has no UI path (SO gets stuck); cancelled PARTIAL SO leaks `qty_backordered`. *Dead UI:* "Fulfilled" tab always shows 0 (SOs go OPEN/PARTIAL→INVOICED, never FULFILLED).

### Invoice List — **A-** / Invoice Workspace — **B+** / Payments — **B**
*Works:* one unified totals engine, correct finalize guards, authoritative `is_taxable` tax gate, atomic finalize, payment allocate/reverse/NSF/account-credit all tested. Invoice list search is excellent (invoice#/customer/PO#/ESN/phone/SKU/cross-ref/serial). *Confusing:* `apply_cc_surcharge` checkbox implies a real fee but it's informational-only — never enters the invoice total (by R1 design, but the UI doesn't say so). *Risky:* NSF fee invoice is created in **DRAFT and never auto-finalized** — NSF charges never enter open AR without manual action; payment modal only renders on OPEN/PARTIAL, so a PAID invoice needing a refund has no action button; both lists hard-capped (200 invoices / 300 payments) with no pagination. *Refuted over-claims:* overpayment is **not** "lost" — it parks as `Payment.amount_unallocated` by design (but is only visible on the Payments screen).

### Purchase Orders List — **A-** / PO Workspace — **B** / Receiving Queue — **B+** / Vendor Bills + 3-Way Match — **C+**
*Works:* create → send → receive (partial/full, multi-receipt) → bill → match, all genuinely end-to-end; receipt writes the ledger, bumps on-hand, decrements on-order, applies moving-average, FIFO-allocates to linked SO lines. *Refuted over-claims:* "Approve Anyway dead end," "Resolve/Credit UI absent," and "PARTIAL PO leaks on-order" were all **refuted** — the override path and Accept/Reject/On-Hold/Create-Credit forms exist. *Real gaps:* **receiving-slip print route doesn't exist** (disabled icon misleads staff, `receiving_queue.html:210`); freight-in landed cost never allocated; Received vs Billed both show green (AP can't tell at a glance).

### Core Tracking — **B+** / Returns(RA) — **A-** / Warranty — **C+** / Vendor Returns — **B-**
*Works:* core 4-stage queue with inline forms; RA full lifecycle with auto credit memo + restock. *Risky:* warranty has **no ESN field on the claim** (PAI/HHP will reject submissions without it) and no WarrantyType selector; `record_vendor_credit()` is never reachable from the UI; **VCR batch creation route is missing** (cores pile up in SHIPPED_TO_VENDOR with `vcr_id=null`, the `/cores/vcr/...` print routes are dead); core-denial "charge back to customer" records intent but **never debits**; overdue-core alerts require manual `mark_overdue_cores()`. **Serial tracking is skeleton-only** — no capture at receive, no consume at invoice (a real liability for remanned heads).

### QBO Sync — **C+** (see §9)
Invoice push works and is fail-soft; everything else is a gap.

### Reports + Reports Landing — **B-** (see §10)

### Global Search / Ctrl+K — **B+** / Line-adder search — **B** / Products-list search — **C** (see §3 & §6)

### Smart Import + Direct Importer — **B-** (see §7)

### Category Maintenance — **C** / Vendor List — **B-** / Vendor Detail — **B** (see §8)
*Vendor Detail bug:* `_preview_panel.html:54` references `primary.title` which doesn't exist on `VendorContact` → **Jinja `UndefinedError` (500)** whenever the preview dock loads a vendor with a primary contact.

### Settings — **B-** / Search bar / Navigation shell — **B**
QBO connection card works but credentials live inside the main Settings form while Connect/Disconnect sit outside it (easy to click Connect before Save). Nav shell has a hardcoded "Live" green dot and a hardcoded "K / Keith" avatar.

---

## 3. Workflow Review (how the business actually works)

The literal chain **runs end-to-end without corrupting money on a clean transaction.** Specific answers:

| Question | Verdict |
|---|---|
| Counter person quote fast? | **Yes** — 2-char search, one-click add, auto price/cost/core. But suggested-sell chips quote the wrong (retail) price to tiered customers, and Duplicate-Quote is unreachable. |
| Find by SKU / part# / vendor# / OEM / cross-ref / partial? | **Mostly** in the line-adder (JAKS SKU → OEM/cross-ref → vendor part# → description). **But `manufacturer_part_number` is never searched anywhere** (PAI populates it), and the **products-list search does NOT join cross-references** — typing an OEM number there returns nothing. Every WHERE wraps columns in `lower(replace(...))`, defeating the indexes → full scans at 13k parts. |
| Create a customer fast without duplicates? | Duplicate **warning** exists on create; no hard block. Bulk customer import has **no role check**. |
| Convert a quote cleanly? | **Yes** — header/ESN/lines/cores carry forward to SO and Invoice. |
| Stocked vs sourced both work? | **Yes** — `special_order_only` flag, backorder demand tracked, drop-ship modeled. |
| POs from demand? | **Yes** — SO backorder line → draft PO → link → FIFO allocate on receipt. |
| Receiving updates inventory correctly? | **Yes** — ledger + on-hand + on-order + moving-average + SO allocation all correct. |
| Invoice shows payment / balance / QBO status / core status? | Payment+balance **yes**; QBO column **yes**; core status **yes**. (QBO status is real but the underlying sync is incomplete.) |
| Cores without confusion? | Customer-return + credit **yes**; vendor-side batch (VCR) and denial-chargeback are **incomplete**. |
| Accounting gets clean transactions? | **No, not yet** — QBO never receives payments, CC surcharge is dropped, and sales reports count cores as revenue. |

**Bottom line:** the workflow is logical and largely complete operationally; it breaks down at **reporting truthfulness, deposit-cancel, QBO accounting, and a few silent-money paths.**

---

## 4. Diesel Parts Business Fit — Grade: **B**

**Properly built for diesel (not generic):**
- One Product = one record with **many `ProductVendorSource`** rows (PAI, HHP, ATL each with their own vendor part#, cost, lead time), `CrossReference` for OEM/competitor numbers, `ProductApplication` for engine-make/model/CPL/ESN fit.
- **JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]** customer-facing SKU with frozen components, vendor number hidden inside the digit.
- Separate `vendor_core_charge` vs `customer_core_charge`; full `CoreCharge`/`CoreReturnEvent`/`VendorCoreReturn` lifecycle.
- ESN / engine make / engine model on **every** Quote/SO/Invoice header; engine make→model cascading pickers.
- Moving-average COGS (not vendor-quote mirror); 3-way match tuned for over-receipts PAI distributors actually see; drop-ship; verbal/phone PO as a first-class status; tier pricing for fleet/dealer/repair-shop accounts.
- Outstanding-Cores report is one of the better-built screens.

**Still generic / missing for diesel:**
- **Serial tracking for heads is a skeleton** (no capture/consume) — remanned-head traceability is absent.
- **Warranty claims can't capture ESN** — PAI/HHP submissions need it.
- **Bundles (head + gasket kit + bolts):** `ProductKit`/`ProductKitLine` tables exist but are scaffold-only — no kit explode at quote time.
- **Truck make / application data** has no manual-edit screen (enrich-from-CSV only).
- Brand/manufacturer are **free-text strings**, not FKs — "PAI" / "pai" / "P.A.I." all coexist.
- Upsell prompts exist (suggested-sell chips) but price wrong for tiered customers.
- Notes: customer-visible vs internal **is** modeled (`notes` vs `internal_notes`).

---

## 5. Data Structure Review — Grade: **B-** (Product master: **B-**)

**The model is strong and diesel-correct.** The problems are integrity-in-Python-not-the-DB:

- **FK enforcement is OFF database-wide** — `database.py` has no `PRAGMA foreign_keys=ON` connect listener, so every ForeignKey is documentation-only. Any delete route, batch script, or ORM-bypass can silently orphan invoice lines / PO lines / core charges. *(Top risk #1.)*
- **Zero indexes on the four hot child tables:** `invoice_lines(invoice_id)`, `quote_lines(quote_id)`, `so_lines(so_id)`, `po_lines(po_id)` — every total computation is a table scan once real volume lands.
- **Missing UniqueConstraints:** `(product_id, vendor_id)` on `product_vendor_sources` (duplicate vendor rows + two "preferred"), `(product_id, ref_type, ref_number)` on `cross_references` (same OEM number twice → product appears twice in search), `(vendor_id, vendor_sku)`.
- **`brand` / `manufacturer` are free-text on Product**, not FK to the seeded `Brand`/`Manufacturer` tables → reports silently split on case/typo.
- **No Alembic** — schema is `create_all` + 58 inline `ALTER`s (`_PENDING_COLUMN_ADDITIONS`). Idempotent and OK for column adds, but there is **no migration discipline for real data** and no rollback. `data/jaks.db` is still treated as "throwaway/re-importable," which becomes false the day real invoices exist.
- **SQLite in DELETE journal mode** (not WAL) — multi-station counter writes will contend.
- **Audit/history:** good where it exists (`ProductCostHistory`, `CompetitorPriceHistory`, `AuditLog`, append-only `InventoryTransaction`), but `InvoiceService(db, 1)` hardcodes user #1 in `_workspace_context` (`invoices.py:104`) so finalize/payment audit attribution is wrong.

**Is the product master strong enough for…** Shopify ✅ (shopify_* fields, SEO, images), ERP inventory ✅ (ledger + cache), Quotes/POs ✅, Smart import ✅ (staging tables), Cross-references ✅, Vendor pricing ✅ (per-source), Competitor pricing ✅ (unique-constrained), eBay/Amazon ⚠️ (fields exist, no integration), QBO ⚠️ (sync mixin good, but free-text brand/mfr will splinter QBO income mapping). **The schema can support all of it once FK + UniqueConstraints + indexes land and brand/manufacturer move to FKs.**

---

## 6. UI / UX Review — Grade: **B-**

Consistent, well-built design system. Specific, actionable items:

- **Self-host JS.** Alpine/HTMX/Chart.js load CDN-only with no `/static/vendor` fallback, and Alpine is on a floating `@3.x.x` tag (`base.html:16-18`). A shop-internet hiccup kills *all* interactivity. Pin + self-host before go-live.
- **Preview dock misaligns.** `macros/preview_dock.html:14` hardcodes `left-64`; collapse the sidebar and the dock overlaps the nav. Bind `left` to `sidebarCollapsed`.
- **8 dead "Export CSV" buttons** on primary lists (quotes/customers/invoices/POs/vendors/SOs/reports) — wire them or hide them; dead buttons on the main screens erode trust.
- **6 `hx-confirm` native dialogs survive** on `products/detail.html` subcomponents — managed-Chrome policy can suppress them, firing destructive vendor-source/cross-ref deletes silently. Replace with the in-app `jakConfirm` modal (governance rule).
- **Phone click opens the dock, not a dialer** (`customers/list.html:278`) — add `@click.stop` + `tel:`.
- **No pagination anywhere** — lists silently end at hard caps (150 quotes / 200 invoices / 300 payments) with no "showing X of N." A quote typed months ago is unfindable.
- **Customer-list search doesn't auto-fire** (needs Enter) while Ctrl+K fires at 2 chars — inconsistent.
- **Quote Workspace:** "Save Standard v2" pill shows "Saved" before any change; → Invoice is buried in a "More ▾" dropdown while → Sales Order is a prominent button (counter who gets verbal approval has to hunt for invoice conversion).
- Stale inline `style='flex-direction: row-reverse'` (`base.html:392`) never folded into the compiled class.

---

## 7. Import / Smart Import Review — Grade: **B-**

**What works:** the review-queue *analysis* (7-question analyzer), staging tables (`ImportBatch`/`ImportCandidate`), and `apply_approved` **service** are built and tested. Full vs Pricing-Update modes, dry-run, idempotency, and JAKS-SKU-vs-vendor-part separation are correct.

**What's broken / missing for safe smart import:**
- **The review queue is a dead end** — `apply_approved` has **no HTTP route / no "Apply Approved" button**. Candidates get approved and never enter the catalog. This is the entire point of the feature.
- **`test_s18_classification` is FAILING** (2 tests) — the classifier that's supposed to map scraper rows to categories is genuinely broken right now, not just unwired.
- **Direct Full Import bypasses the review gate entirely** and **silently creates garbage categories** from the Shopify "Type" column (`product_import_service.py:499-503`) — `'ENGINE PARTS '` and `'ENGINE PARTS'` become two categories; only `[:200]` truncation, no sanitization/flagging.
- **Enrich-from-CSV has no dry-run** — one wrong file writes thousands of bad cross-refs to the live 13k catalog with no undo and no row provenance.
- Duplicate recognition exists for SKU/cross-ref but **non-Shopify feeds (Interstate-McBee, SAMPA)** have no column mapping → everything lands in `needs_review`.
- Candidate preview dock is **read-only** — no way to fix a wrong `matched_product_id`, wrong category, or wrong engine-make before approving (a wrong CROSS_REF match sends data to the wrong part).

**To make smart import safe:** add the `apply_approved` route+button; fix the classifier (the red tests); make Full Import *flag* unknown categories instead of creating them (or warn it bypasses review); add dry-run+preview to enrich; add per-candidate edit/override in the dock.

---

## 8. Category / Manufacturer / Vendor Maintenance — Grade: **C+**

The owner's stated problem — "the import separated things badly" — **has no fix path in the app today.**

- **Manufacturer/brand rename orphans 13k products.** `update_manufacturer` (`category_service.py:220-254`) writes only the `Manufacturer` row; `Product.engine_manufacturer` is free-text with no FK/cascade. Renaming permanently diverges the catalog and breaks the engine-make filter + picker.
- **No merge, no reparent, no bulk-reassign.** Can't merge two duplicate categories, can't move a category to a new parent, can't bulk-move products X→Y.
- **Category SKU `code` field has no UI input** — a category added through the screen produces a malformed/empty JAKS SKU segment for every product tagged to it.
- **Delete silently deactivates** when a node has products, with no explanation.
- Engine make→model cascading dropdowns work but are **quote-header only** (not reused on product detail); truck_make/application data has no manual-edit screen.
- **Vendor detail preview crashes** (`_preview_panel.html:54` `primary.title`); no `vendor_code` uniqueness guard; `VendorProgram` model + return-policy fields exist with no UI.

---

## 9. QBO / Accounting Review — Grade: **C+** → treat as **Phase 1.1, not Phase 1**

The strategy is right — accounting-summary push (generic income items, never per-SKU) keeps QBO from becoming a shadow inventory system. Invoice push is genuinely **fail-soft** (success → `mark_synced`/lock, fail → `mark_sync_failed`; never touches the money path). But:

- **Payments are never pushed.** QBO AR shows every invoice unpaid forever → bank reconciliation is impossible. This alone disqualifies QBO as an AR ledger until payment push exists.
- **CC surcharge is silently dropped** from the QBO payload (`qbo_service.py:53`) *and* from `invoice.total` → QBO revenue is permanently below bank deposits on every card sale.
- **Tokens + `client_secret` are plaintext** in `jaks.db` (`qbo_client.py:15-17`) — a copied backup hands over a live accounting connection. Fernet encryption is acknowledged-but-not-done.
- **"Modified" re-sync tab detects drift but has no re-push action.**
- **`InvoiceService(db, 1)`** hardcoded user on push → wrong audit attribution.
- **No `TxnDate`/`DueDate`** in the payload → QBO AR aging wrong from day one.
- Bulk "Sync All Unsynced" fires N synchronous API calls with no count/preview.

**Before QBO can be trusted:** build `push_payment()` + route; decide CC-surcharge handling; encrypt tokens; add TxnDate/DueDate; thread the real user_id; add re-push to the Modified tab.

---

## 10. Reports Review — Grade: **B-**

Nine reports run end-to-end. The math is mostly right, with two serious exceptions and a data problem.

| Report | Status | Phase-1? |
|---|---|---|
| AR Aging | ✅ A- (no drill-down, dead CSV) | **Required** |
| Open POs | ✅ A- | **Required** |
| Outstanding Cores | ✅ A (best-built) | **Required** |
| Overdue Invoices + Interest | ✅ A- (30-day-month approx) | **Required** |
| Sales Tax Collected | ✅ B+ (taxable base ignores discount) | **Required** |
| Lost Sales | ✅ B+ (no win-rate denominator) | Nice |
| Inventory Valuation | ⚠️ B- — reads **~$0** because all 13k parts have `cost=0` | **Required (after cost load)** |
| **Sales by Customer** | ❌ C+ — **gross sales include core deposits**; margin wrong | **Required — fix** |
| **Sales by Product** | ❌ C+ — core lines share parent `product_id` → SKU revenue inflated | **Required — fix** |

**The core-in-revenue bug** (`report_service.py:229, 235-237, 346`) inflates revenue 20-30% on every cored invoice — for a shop where a rebuilt injector set carries $600-1200 in cores, the owner is making pricing decisions on fiction. **Every "Export CSV" is a dead stub** — the accountant workflow is blocked on day one.

**Missing reports** (Phase 1.1+): inventory movement history, dead stock, reorder/low-stock standalone, vendor performance, product profitability (the real one), quote conversion rate, QBO-unsynced, customer purchase history.

---

## 11. Risk Review (ranked, post-verification)

> Note: verification **downgraded** several raw claims — login *is* globally enforced (so authz gaps are role-not-anonymous), SameSite=Lax *is* set (so CSRF is lower), credit-memo UI *exists*, overpayment is *not* lost. The list below is the corrected, honest ranking.

### CRITICAL (silent wrong numbers / data corruption — fix before any real use)
1. **All 13k PAI parts have `cost=0` and no JAKS SKU** until `backfill_sku_scheme.py` runs → 100% margin on every sale, ~$0 valuation, blank/fallback SKUs. *(Data load.)*
2. **Reports count core deposits as revenue & COGS** (`report_service.py:229,346`) → every margin/sales figure wrong.
3. **FK enforcement OFF + four child tables unindexed** (`database.py`) → silent orphaning + counter latency the moment volume lands.
4. **Deposit-cancel silently orphans money** (`sales_orders/workspace.html:94-105`) → no refund record, no credit, no AR trail.
5. **Suggested-sell chips quote retail to tiered customers** (`_line_row.html:484,503`) → wrong price to fleet/dealer customers on the hot path.

### HIGH
6. **Credit-hold dual state machine** — a held account can read "clear" at the counter (collection risk).
7. **AR aging bar never renders** (`_balance_widget.html` key mismatch) → overdue AR invisible at a glance.
8. **Product list/preview price uses hardcoded 30%** (`product.py:300`) → disagrees with the quote line's tier price.
9. **Search defeats its own indexes** (function-wrapped WHEREs) + **products-list search ignores cross-references** → OEM lookup returns nothing on that screen; full scans at 13k parts.
10. **QBO never pushes payments + CC surcharge dropped + tokens plaintext** → QBO AR permanently wrong; books ≠ bank; live-secret exposure.
11. **`cancel_line` can prematurely flip an SO to INVOICED** (`qty_ordered=0`) → lost lines, locked SO.
12. **Smart-import apply path is a dead end + classifier failing** → the safe-import feature doesn't actually import; Full Import creates garbage categories.
13. **Manufacturer/brand rename orphans 13k products** (no cascade) → owner's cleanup need has no safe path.

### MEDIUM
14. **Vendor/customer mutation + customer-import routes have no role gate** — any logged-in user (incl. SALES) can edit credit limits / deactivate a PAI vendor; audit attribution hardcoded to user #1.
15. **CDN-only JS** kills interactivity offline; **6 `hx-confirm`** can be suppressed → silent destructive deletes.
16. **NSF fee invoice stuck in DRAFT** → NSF charges never enter AR.
17. **VendorContact preview 500** (`_preview_panel.html:54`).
18. **Serial tracking skeleton** → no remanned-head traceability; **warranty has no ESN** → PAI/HHP rejections.

### LOW
19. No WAL mode (multi-station contention later). 20. Session cookie missing `secure=True`. 21. 8 dead Export-CSV buttons. 22. Preview dock misalign on sidebar collapse. 23. Stale comments/“Series 1” footers.

---

## 12. Testing Review — Grade: **B-**

`4 failed, 1120 passed, 55 skipped, 5 xfailed, 1 xpassed`. Broad, real (isolated engines) coverage of the business-logic happy path — moving-average COGS, core lifecycle, 3-way match, AR buckets, PAI import idempotency, SKU scheme, and the full quote→SO→invoice→payment spine.

**Currently RED (must fix before calling the suite a gate):**
- `test_ar_aging_buckets_and_last_contacted.py::test_buckets_correct_buckets` (the uncommitted AR-aging WIP)
- `test_s18_classification.py::test_full_import_applies_classification` **(smart-import classifier broken — not in the plan)**
- `test_s18_classification.py::test_full_import_flags_unclassifiable_as_needs_review` **(same)**
- `test_template_renders.py::test_w4_cost_variance_only_no_qty_over_narrative` (brittle, flagged "do-not-chase")

**Coverage gaps (missing tests):**
- **`JAKS_SKIP_AUTH=1` globally** → HTTP-layer auth has **zero** regression coverage. One missing decorator on a money route = unauthenticated prod access, untested. Add a no-skip-auth test hitting money routes unauthenticated.
- **No product-search-by-vendor-part-number test** — the single most diesel-counter-specific path (PAI catalog # lookup) is unguarded.
- No duplicate-customer-detection test; no concurrent-inventory/drift test; no CC-surcharge math test; no category-cleanup test; no QBO-payment-push test (feature absent).
- `xfail` on `test_no_tbl_classes` (~559 L1 UI violations) silently accumulates new violations.

---

## 13. Phase Readiness

**✅ Phase-1 ready now:** quote→SO→PO→receive→invoice→payment spine; moving-average COGS; core lifecycle + RA; credit memos; payments (allocate/reverse/NSF/account-credit); PO/receiving/3-way-match; global auth.

**🔧 Fix before Phase 1 (the go-live list — mostly 1-3 line fixes + 2 data jobs + 1 migration):**
1. `database.py`: add `PRAGMA foreign_keys=ON` listener + `CREATE INDEX` on the four child FKs.
2. Run the PAI **cost load** (pricing-update `pai_cost`) so `product.cost` ≠ 0.
3. Commit + run `scripts/backfill_sku_scheme.py --apply` (set PAI `vendor_number=9` first).
4. Exclude `CORE_CHARGE` lines from revenue+COGS in `get_sales_by_customer`/`get_sales_by_product`.
5. Pass `PricingService` prices to products list/preview (stop rendering the 30% property).
6. Strip `unit_price/unit_cost` from suggested-sell chip `hx-vals`.
7. Fix deposit-cancel `<select>` placement (+ the HOLD cancel form).
8. Fix AR aging-bar keys + `last_contacted` formatting.
9. Synchronize credit-hold (`customer_status` ↔ `CustomerFlag.CREDIT_HOLD`).
10. Add role gates to vendor/customer mutation+import routes; fix `InvoiceService(db, 1)`.
11. Self-host + pin Alpine/HTMX/Chart.js.

**🟡 Phase 1.1:** QBO payment push + CC-surcharge reconciliation + Fernet token encryption; indexed/normalized search columns (or FTS5) + cross-ref join on products list; `/admin/inventory/reconcile`; wire CSV exports (AR/overdue/valuation first); SQL pagination; `qty_backordered` decrement on cancel; manufacturer/brand rename cascade + category merge/reparent; serial capture/consume; ESN on warranty claims; VCR batch route; **fix the smart-import apply route + classifier.**

**🟢 Phase 2:** overpayment surfaced on customer/statement; vendor-bill payment + AP aging; freight-in landed cost; missing reports (reorder/dead-stock/movement/vendor-perf/conversion); admin UIs (markup tiers, type-defaults, multi-contact/address); brand/manufacturer FK migration + the three UniqueConstraints.

**🔵 Phase 3:** WAL + multi-station hardening; brute-force lockout + `/admin/users`; ESN/EngineConfig autocomplete; optimistic-locking wired; server-side PDF; QBO vendor-bill/credit-memo push.

---

## 14. Final Report Card

| Area | Grade | Status | Main Issue | Recommended Fix |
|---|---|---|---|---|
| **Overall system** | **C+** | Partial | Working money spine, but silent correctness defects + unloaded cost/SKU data | Clear the 11-item go-live list; defer QBO to 1.1 |
| Product master | B- | Built-but-weak | FK off, child tables unindexed, free-text brand/mfr, SKU empty on 13k | FK PRAGMA + indexes + run backfill; UniqueConstraints in P2 |
| Inventory | B- | Partial | All 13k parts cost=0; no ledger-reconcile route | Load PAI cost; add `/admin/inventory/reconcile` |
| Quotes | B- | Partial | Chips bypass tier pricing; N+1 on list; Duplicate unreachable | Strip chip hx-vals; joinedload; add Duplicate route |
| Customers | B- | Partial | AR aging bar dead; credit-hold dual-state | Fix `_balance_widget` keys; sync hold mechanisms |
| Vendors | B- | Partial | Preview-panel `primary.title` 500; no role gate; no code-uniqueness | Fix template; add role gates + dup check |
| Purchase orders | B | Working | Receiving-slip route missing; freight-in not landed | Add slip route; allocate freight (P2) |
| Receiving | B | Working | Only the printable slip is missing | Add `GET /purchase-orders/{id}/receiving-slip` |
| Invoicing | B | Working | NSF fee invoice stuck in DRAFT | Auto-finalize in `process_nsf` |
| Payments | B | Working | Overpayment only visible on Payments screen | Surface unapplied balance on customer/statement (P2) |
| Core tracking | B- | Partial | VCR batch route missing; denial-chargeback is a no-op | Add VCR route; disable chargeback until built |
| QBO sync | C+ | Partial | No payment push; CC surcharge dropped; plaintext tokens | Build payment push; reconcile surcharge; Fernet (P1.1) |
| Reports | B- | Partial | Cores counted as revenue; valuation ~$0; dead CSV | Exclude core lines; load cost; wire CSV exports |
| Search | B- | Built-but-weak | Indexes defeated; products list skips cross-refs; mfr# unsearched | FTS5/normalized columns; add cross-ref join + mfr# |
| Imports | B- | Partial | Apply route missing + classifier RED; Full Import makes junk categories | Wire apply route; fix classifier; flag unknown categories |
| Categories | C+ | Built-but-weak | Rename orphans 13k; no merge/reparent; no SKU-code input | Cascade rename; add merge/reparent + code field |
| UI/UX | B- | Partial | CDN-only JS; dead dock on collapse; 8 dead CSV buttons; 6 hx-confirm | Self-host JS; bind dock; wire/hide exports; jakConfirm |
| Data model | B- | Built-but-weak | Integrity in Python not DB; FK off; missing constraints | FK PRAGMA + UniqueConstraints + indexes now |
| Testing | B- | Partial | 4 RED; auth bypass baked into suite; no vendor#/dup-customer test | Fix reds; no-skip-auth test; search/dup tests |
| Diesel fit | B | Working | Serial + ESN-on-warranty absent; bundles scaffold-only | Add serial capture/consume + ESN field (P1.1) |
| **Phase 1 readiness** | **C+** | Partial | Reporting fiction + data load + silent-money paths; QBO not ready | Clear go-live list; load data; defer QBO |

---

## 15. Final Action Plan

### IMMEDIATE — must fix now (go-live blockers)
- `database.py`: `@event.listens_for(engine,"connect")` → `PRAGMA foreign_keys=ON`; add `CREATE INDEX IF NOT EXISTS` on `invoice_lines(invoice_id)`, `quote_lines(quote_id)`, `so_lines(so_id)`, `po_lines(po_id)`.
- `report_service.py:229,235-237,330-349`: exclude `line_type==CORE_CHARGE`/`is_core_line` from gross_sales and cost in `get_sales_by_customer` + `get_sales_by_product`.
- Run pricing-update `pai_cost` import to populate `product.cost`; commit + run `scripts/backfill_sku_scheme.py --apply` (PAI `vendor_number=9`).
- `_line_row.html:484,503`: remove hardcoded `unit_price/unit_cost` from suggested-sell chip `hx-vals`.
- `sales_orders/workspace.html:94-105`: move `deposit_resolution <select>` inside the cancel `<form>`, drop the hardcoded hidden input; add the field to the HOLD cancel form.
- `_balance_widget.html:19,23,28`: rename aging keys to `1_30/31_60/61_90/over_90`.
- **Fix the 2 `test_s18_classification` reds** (smart-import classifier) + the AR-aging red.

### NEXT — should fix soon
- `products.py` list+preview routes: build `{product_id: PricingService.sell_price_for(p)}`; stop rendering `p.selling_price` (`list.html:432`, `_preview_panel.html:67`).
- `customers.py` update + `customer_service`: sync `customer_status` ↔ `CustomerFlag.CREDIT_HOLD`.
- `vendors.py` + `customers.py`: add `require_admin`/bookkeeping role gates to all create/update/deactivate/import routes; fix `InvoiceService(db, 1)` → real user_id (`invoices.py:104`).
- `qbo_service.py` + `qbo.py`: build `push_payment()` + `/qbo/payments/{id}/push`; decide CC-surcharge handling; add `TxnDate`/`DueDate`.
- `base.html:16-18`: self-host Alpine/HTMX/Chart.js under `app/static/vendor`, pin Alpine to exact `3.14.x`.
- `search_service.py` + `products.py:119-133`: normalized indexed columns (or FTS5) + join `cross_references` into the products-list search; add `manufacturer_part_number` to the WHERE.
- Add `/admin/inventory/reconcile` calling `ProductService.get_qty_on_hand()`.
- Wire the **smart-import Apply-Approved route + button**; make Full Import *flag* unknown categories.
- Fix `vendors/_preview_panel.html:54` (`primary.title`).

### POLISH — improves usability
- Fernet-encrypt QBO tokens/`client_secret` + the `*_encrypted` SMTP/Twilio keys; add `secure=True` behind an HTTPS flag.
- Wire CSV export backends (AR aging, overdue, valuation first) via `StreamingResponse`; remove the rest of the dead buttons.
- `category_service.update_manufacturer/update_brand`: cascade rename to `Product.engine_manufacturer`/`brand`.
- Decrement `qty_backordered` on SO line cancel; payment-required gate before fulfilling `payment_mode=FULL`.
- Bind preview-dock `left` to `sidebarCollapsed`; `@click.stop`+`tel:` on phone anchors; replace the 6 `hx-confirm` with `jakConfirm`.
- Fix `cancel_line` `qty_ordered=0`/`is_fully_invoiced`; auto-finalize the NSF fee invoice.

### LATER — not needed for Phase 1
- Reorder/dead-stock/movement/vendor-performance/quote-conversion reports; inventory valuation `vendor_cost` fallback.
- Serial capture at receive + consume at finalize; ESN field on `WarrantyClaim` + create form; VCR batch-creation route.
- `UniqueConstraint(product_id,vendor_id)` / `(product_id,ref_type,ref_number)` / `(vendor_id,vendor_sku)`; brand/manufacturer → FK migration; WAL mode.
- SQL pagination across lists; order vendor/category product sort in SQL; markup-tier + type-defaults + multi-contact/address admin UIs.
- Vendor-bill payment + AP aging; freight-in landed cost; core-denial chargeback (or remove); overpayment on statement.
- Brute-force lockout + `/admin/users`; optimistic locking (hidden `updated_at`) on quote/invoice/SO headers; category merge/reparent tooling.

---

*Honest bottom line: the bones are good and genuinely diesel-shaped. The program is roughly **two focused days** from being safe for daily parts operations — most blockers are 1-3 line template/service fixes plus the cost+SKU data load and one schema migration. The trap is the silent ones: the system will happily show you confident, wrong margin and revenue numbers until items #1-#3 are fixed. Fix those first, load the data, defer QBO to 1.1, and you can run the counter on it.*
