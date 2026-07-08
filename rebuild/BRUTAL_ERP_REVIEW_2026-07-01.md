# JAK's Diesel ERP — Brutal End-to-End Review
**Date:** 2026-07-01 · **Branch:** backend/workflow-series-3 · **Reviewer:** Claude (16-agent code audit + adversarial verification + live headed-browser testing on a sandboxed copy of the live `jaks.db`)

**Method note (so you can trust or attack this report):** I ran the app against a fresh copy of your real 81 MB `data/jaks.db` on an isolated port, logged in, and drove the UI. Sixteen subsystem auditors read the actual code; every Critical/High was then re-read adversarially to confirm or refute it — **13 risks were verified, 0 were refuted.** Where I cite a line number, it's from the current tree. Where I cite a number like "reorder_point > 0 on 2 of 30,935 products," I queried your live data.

---

## EXECUTIVE SUMMARY

You have built something genuinely more capable than "an internal tool," and the engineering quality of the money/inventory spine is real — one shared invoice-totals engine, a true 3-way match with an explicit approve gate, moving-average COGS with freight landing, a dual-ledger core-charge system, ~2,500 passing tests, real CSRF + auth. Purchasing/Receiving alone is A- work.

But the grade is for **today**, weighted on money and inventory accuracy, and the verified defect list is disqualifying for daily use as-is. The problems are **not architectural** — they are precisely-located point defects sitting exactly where they hurt most:

1. **The normal one-click "add line" on a Sales Order silently bills tier/wholesale customers full retail** (verified live: $130 charged where $104 was configured).
2. **A negative discount % inflates an invoice total and finalizes/locks it** with no server-side check (`calc_line_total(100, 1, -50) == 150.0`).
3. **QuickBooks is wrong by design on nearly every diesel transaction** — core charges post as income instead of a pass-through liability, and card-surcharge cash never reaches QBO, so the bank feed mismatches on every surcharged card sale.
4. **Any signed-in user of any role** can finalize invoices, reverse payments, issue credit memos, push to the live QuickBooks file, and run irreversible, **unaudited** 30,000-row catalog merges.
5. **Low-stock / reorder is inert for 30,933 of 30,935 parts** because `reorder_point` was never populated — the daily heartbeat of a parts distributor is blind, and worse, it manufactures false confidence ("no low-stock alerts" really means "no data").
6. **A one-click "Approve All & Apply" plus a fully-reachable direct-write importer** can pour an unreviewed 13k-row vendor feed straight into the live catalog (this is the single CRITICAL — and you've already lived a 20,638-row bulk-mutation mishap once).

### 1. Brutal Overall Verdict

- **Usable for JAK's Diesel today?** Not for daily operation as-is. The spine works end-to-end, but it will quietly produce wrong prices, wrong books, and blind reordering. **After ~1–2 weeks of targeted fixes: yes.**
- **Safe for real business data today?** **No.** The data model is safe (FKs enforced, uniqueness backstops, pre-migration backups). The *operations on top of it* are not: ungated merges with no audit trail, one-click import apply, unclamped discounts.
- **Ready for employees today?** **No.** A counter clerk can overcharge a fleet account, promise stock you don't have, reverse a payment, and corrupt the catalog — none of it gated, most of it silent.
- **Ready to sell as software?** **No.** It's a strong single-tenant internal ERP, not a SaaS product. No multi-tenancy, no onboarding, no billing, no per-customer isolation, and market-pricing/classification columns are 100% empty.
- **Better as an internal tool first?** **Yes, unambiguously.** Run it for JAK's, harden it under real transactions for 6–12 months, *then* consider productizing.
- **Single biggest risk:** The **import → live-catalog blast radius** (direct-write importer + "Approve All & Apply"). It touches pricing, fitment, and Shopify-facing data for 30k parts with one click and no staging.
- **Single biggest opportunity:** You are **genuinely purpose-built for heavy-duty diesel** — cores as first-class dual-ledger objects, ESN/engine fitment, cross-references, vendor part numbers. No general ERP (NetSuite, Dynamics, Odoo) does this out of the box. That focus is your moat.

### 2. Overall Grade

**C+ / 70 out of 100.** A B+/A- machine with a dozen verified point defects that, run daily as-is, would produce wrong prices, wrong books, and blind reordering. A focused fix sprint moves this to a solid **B / 82** and a real go-live.

### 3. Scorecard

| Area | Grade | Score /10 |
|---|---|---|
| Business fit | B+ | 8 |
| Quote-to-cash flow | B- | 6 |
| Purchase-to-pay flow | A- | 9 |
| Inventory accuracy | C+ | 5 |
| Product master / catalog | B- | 6 |
| Search | B- | 6 |
| Customer management | B | 7 |
| Core management | B+ | 8 |
| QBO sync safety | C+ | 5 |
| Reporting | B | 7 |
| UI/UX | B | 7 |
| Speed of use | B | 7 |
| Data model | C+ (design B+, live data C-) | 6 |
| Code quality | B+ | 8 |
| Error handling | B | 7 |
| Security | C+ | 5 |
| Auditability | C+ | 5 |
| Scalability | C+ | 5 |
| Sellability as SaaS | D | 3 |
| Competitive strength (diesel niche) | B+ | 8 |
| Employee readiness | C- | 4 |

---

## 4. PERSONA REVIEWS

### 1. Senior Software Engineer — Grade: B
- **Likes:** One shared `compute_invoice_totals` engine (no divergent copies). Real Alembic migrations *plus* an inline ALTER safety net, cross-checked by `test_schema_drift.py`. `PRAGMA foreign_keys=ON` on every connection, so FKs are real constraints. DB-level uniqueness backstops that *probe for existing duplicates and skip-with-warning rather than brick startup*. Pre-migration DB backup. ~2,500 passing tests with regression pins for found bugs.
- **Hates:** `qty_on_hand` is written directly by **five** services outside `InventoryService` (`invoice_service.py:663/1074`, `po_service.py:503`, `ra_service.py:347`, `vendor_return_service.py:190`) — directly contradicting the "NEVER mutate directly" comments. Inventory truth is social convention, not enforcement. Optimistic locking (`check_version()`) is built into 4 services but wired to only **1 route** (Sales Orders); Invoice/PO/Quote header saves silently no-op the version check.
- **Dangerous:** No double-submit / row-lock guard on `finalise()`, `void_invoice()`, `record_payment()`, or QBO push — a double-click or retry can double-act. The dual migration system (`_PENDING_COLUMN_ADDITIONS` 300+ lines *and* Alembic) is a maintenance trap; nothing cross-checks that they describe the same columns.
- **Fix first:** Consolidate the five `qty_on_hand` writers behind one entry point + nightly resync-with-drift-alert. Then atomic `UPDATE...WHERE status='draft'` claims on finalize/void.
- **Verdict:** Well-built, not yet bulletproof.

### 2. ERP Product Manager — Grade: B-
- **Likes:** The module map is *right* for a parts distributor — the quote→SO→PO→receive→invoice→payment spine is all present and wired, cores and warranty are first-class, QBO is scoped correctly as bookkeeping-only (push, not source-of-truth).
- **Hates:** Several features are built but **starved or unreachable**: Vendor Returns is fully coded with **zero nav entry point**; the reorder system has no data; market-pricing columns (`list_price`, `manufacturer_part_number`, `map_price`) are 100% empty. "Built" ≠ "usable."
- **Dangerous:** Feature-complete screens that don't actually do the job (reorder report that's always empty; RA "Vendor Return" disposition that's a dead-end label) create false confidence in a way that's worse than a missing feature.
- **Fix first:** Make what's built *reachable and fed* before building anything new. One nav link recovers the entire Vendor Returns module.
- **Verdict:** Scope is disciplined and correct; execution has "last-mile" gaps.

### 3. Heavy-Duty Parts Counter User — Grade: C+
- **Likes:** The 45-second quote workspace genuinely delivers — one-click line add, live totals, auto core/warranty child lines, clean convert-to-SO/Invoice. Search covers SKU/OEM/vendor#/cross-ref/engine fitment. Customer lookup is fast with +Inv/+Quote right on the row.
- **Hates:** Cross-ref search returns an **unranked 8-row slice** — the right part may not even be visible. Garbage cross-refs ("N/A" maps to 3,440 parts) can bury the correct hit. Margin column lies on unreceived parts (shows ~100% when cost=0).
- **Dangerous:** On a **Sales Order**, the price I see auto-filled is **full retail even for my wholesale accounts** — I'd overcharge a fleet customer all day and never know.
- **Fix first:** Exact-match-first ordering + "showing 8 of N" in the line-adder, and the tier-pricing bypass.
- **Verdict:** Fast when it works; two silent traps make it unsafe to hand a clerk today.

### 4. Purchasing / Receiving Clerk — Grade: A-
- **Likes:** This is the best part of the system. Multi-PO single-vendor receiving ("one truck, one action"), moving-average cost + freight landing, FIFO SO allocation on receipt, 3-way match with an explicit approve gate, Correct & Reconcile, vendor volume discounts. 756 targeted tests.
- **Hates:** Standalone Vendor Bills list is read-only (no inline Approve/Pay). A vendor refund without a VCR reference produces an audit-log-only entry with no ledger row.
- **Dangerous:** Editing an SO line quantity bypasses the negative-inventory block (5 on hand → edit to 50 → `qty_available` = -45 silently) — but that's a Sales bug, not mine.
- **Fix first:** Nothing structural. Upgrade the bills list to inline actions.
- **Verdict:** Ready for real receiving today.

### 5. Accounting / QBO Reviewer — Grade: C
- **Likes:** QBO is one-way push, fail-soft, token-encrypted — the right boundary. Frozen tax snapshots on invoices. Reversal-aware balances. AR aging cross-verified across three surfaces.
- **Hates / Dangerous:** **The books drift on nearly every transaction.** Core charges post to the Income account (they are pass-through liability — the code's own constants say so), overstating revenue every period. Card-surcharge cash is never sent to QBO, so the QBO payment is short of the bank deposit on every surcharged card sale — a guaranteed reconciliation mismatch. No reversal/retry story for synced-then-voided payments. Any role can push to the live company file.
- **Fix first:** **Hold QBO push entirely** until (a) a core-charge liability account exists and the Core Charge item is rebound to it, and (b) surcharge is booked. It's fail-soft, so pausing costs nothing.
- **Verdict:** Do not let this touch your live QuickBooks until the two account fixes land.

### 6. UI/UX Designer — Grade: B
- **Likes:** A genuinely coherent design system — consistent L2 list pattern (filter tabs with counts, bulk toolbar, preview dock) and QB2 queue boards across nearly every screen. Clean typography, real empty states, status chips.
- **Hates:** List tables are fixed desktop width (`min-w-[1000px]`) — on a phone they force horizontal scroll and the filter-tab bar runs off-screen. The workspace header overflows at 1366×768; the preview-dock left offset breaks list screens under 1024px.
- **Dangerous (accessibility):** The two most-used global slide-overs (Log Call, Quick-Create) have no focus trap and no dialog ARIA — keyboard/screen-reader users get stranded.
- **Fix first:** The one-line responsive fix to `preview_dock.html` (left offset only at ≥lg) repairs every list screen under 1024px.
- **Verdict:** Professional and consistent on desktop; not counter-hardware-ready on small/odd screens yet.

### 7. QA Tester / Bug Hunter — Grade: C+
- **Likes:** Email dedup is a hard DB constraint; fuzzy name/phone dedup warns with override. Negative inventory is blocked on the *add-line* path. Void does a full SO/inventory rollback. NSF payment flow exists.
- **Hates / Dangerous — things I broke (all verified):** Negative `discount_pct` inflates & finalizes an invoice. SO line-quantity edit commits stock you don't have. Bulk customer import bypasses all dedup/email validation *and* one bad row rolls back all 500. Your own live DB already contains QA junk that made it through single-create ("Bad Email Co" with email "notanemail," a customer literally named for the bug).
- **Fix first:** Clamp `discount_pct` to [0,100] in all four invoice-service write paths; mirror the R6 inventory guard onto the SO edit path.
- **Verdict:** The happy paths are pinned by tests; the adversarial edges are where it bleeds.

### 8. Operations Owner — Grade: B-
- **Likes:** This *would* run the counter — quote fast, convert clean, receive against POs, track cores you're owed, age your AR, and keep QuickBooks for the bookkeeper. That's the whole job, and it's mostly here.
- **Hates:** It doesn't yet *protect* me. It'll let a new hire overcharge a fleet account, oversell stock, or nuke the catalog, and I won't find out until the customer or the CPA calls.
- **Dangerous:** Reorder being blind means I'll run out of fast-movers and not know until a customer needs one.
- **Fix first:** The money guards (tier price, discount clamp) and reorder-point backfill — those three directly protect margin and sales.
- **Verdict:** Close. Fix the punch list and it earns its keep.

### 9. Competitive ERP/DMS Analyst — Grade: B (for the niche)
- **Likes:** Purpose-built depth a generalist can't match — cores, ESN/engine fitment, cross-references, vendor/OEM/internal SKU triplets. Epicor/NetSuite/Odoo would need heavy customization to get here.
- **Hates:** No substring/fuzzy part search at scale (every "contains" lookup is a full-table scan, 200–600ms/keystroke on 224k cross-refs). No multi-location, no counter-sale POS/cash-drawer, no EDI to PAI/IMB, no price-file automation.
- **Dangerous vs. incumbents:** Data hygiene. A real DMS wouldn't let 9,766 ambiguous cross-refs or 100%-empty pricing columns ship.
- **Fix first:** Cross-ref cleanup + FTS index. That's table stakes for "parts counter speed."
- **Verdict:** Deeper than a generalist in the niche, shallower than Epicor Eclipse on counter/logistics plumbing.

### 10. SaaS Investor / Buyer — Grade: D (as a product), B (as an asset)
- **Likes:** Real domain IP in the diesel-parts workflow. A working spine. Clean, tested code. That's a credible *foundation*.
- **Hates / Blocks a sale:** Single-tenant SQLite, no multi-tenancy, no auth/SSO/RBAC productization, no onboarding/import wizard for a *new* customer's data, no billing, no support tooling. Empty market-pricing columns undercut the "catalog intelligence" story.
- **Dangerous:** The verified money/books defects are exactly what a technical-diligence pass would find and use to kill valuation.
- **Fix first:** Don't productize yet. Prove it running JAK's for a year; capture the workflow as the moat.
- **Verdict:** Not sellable as SaaS today; potentially valuable IP after real-world hardening.

---

## 5. WORKFLOW BREAKDOWN

**Customer creation** — *Works:* 21-field form, hard email dedup, fuzzy name/phone warn+override, tax-exempt/terms/credit-limit/surcharge. *Confusing:* nothing major. *Breaks:* bulk CSV import bypasses all of it and rolls back 500 rows on one bad record. *Missing:* Settings UI for customer-type defaults (promised, absent). *Simplify:* — . *Rebuild:* the bulk import path (per-row commit + per-row validation).

**Product creation** — *Works:* Product + vendor-source + typed cross-refs + engine applications is the right model; SKU scheme frozen per-product. *Confusing:* margin shows ~100% on $0-cost unreceived parts. *Breaks:* nothing. *Missing:* `reorder_point`, `manufacturer_part_number`, `list_price` never populated. *Rebuild:* — . *Feed it, don't rebuild it.*

**Quote creation** — *Works:* genuinely one-click, live totals, auto core/warranty/upgrade trees. *Confusing:* header (ESN/PO#/job) saves only via 2s-debounce autosave; manual-save route is dead. *Breaks:* concurrency check is inert. *Simplify:* — . *Rebuild:* header save path.

**Quote → Sales Order** — *Works:* clean conversion, correct core re-derivation. *Confusing:* —. *Breaks:* —.

**Sales Order → PO** — *Works:* backorder→PO link, cost visibility, linked demand. *Breaks:* tier pricing bypassed on add-line; line-qty edit oversells. *Rebuild:* the two guards.

**PO receiving** — *Works:* multi-PO receiving, moving-average + freight, partial receive, FIFO allocation. *Confusing:* URL is `/purchase-orders/receiving` (fine). *Breaks:* nothing structural. **Best workflow in the system.**

**Receiving → invoice** — *Works:* receipt drives allocation and cost; invoice pulls correct landed cost. *Breaks:* —.

**Invoice → payment** — *Works:* shared totals engine, frozen tax, reversal-aware balances, partial payments, NSF flow. *Breaks:* negative discount inflates+locks; no finalize race guard. *Rebuild:* clamp + lock.

**Core charge & core return** — *Works:* dual customer/vendor ledger, auto core lines at finalize, accept/hold/reject return, VCR batch shipping, overdue-cores report, idempotency stamp. *Breaks:* books post to Income (QBO). **Strong lifecycle.**

**Vendor bill** — *Works:* 3-way match with approve gate, discrepancy flags, Correct & Reconcile. *Confusing:* standalone bills list is read-only. *Rebuild:* inline Approve/Pay.

**QBO sync** — *Works:* one-way push, fail-soft, encrypted token. *Breaks:* cores→income, surcharge cash dropped, any role can push, no reversal/retry for reversed payments. **Hold until fixed.**

**Product import** — *Works:* the Review Queue engine (staging, diff, category-refusal, admin gate) is well-built. *Breaks/Dangerous:* a *direct-write* importer and a "Approve All & Apply" button sit right beside it and skip review. *Rebuild:* gate/retire the direct path; default "apply" to exclude `needs_review`.

**Search & lookup** — *Works:* 6-tier ranked waterfall, one shared endpoint across all four workspaces. *Breaks:* full-table scans (slow at scale); cross-ref hits mislabeled "OEM"; line-adder returns unranked limit-8. *Rebuild:* FTS index + exact-first ordering + garbage cleanup.

**Reporting** — *Works:* 19 SQL-verified reports, RBAC-gated, CSV export, dashboard tied to same truth. *Breaks:* dashboard Monthly-Revenue chart computed but never rendered; reorder/dead-stock reports starved of data. *Simplify:* render or delete the dead chart computation.

---

## 6. BUG LIST

| # | Bug | Area | Severity | Repro | Expected | Actual | Business impact | Fix |
|---|---|---|---|---|---|---|---|---|
| 1 | Direct-write importer + "Approve All & Apply" skip review | Import | **Critical** | Import screen → either the direct `import-run` tab or Review Queue → "Approve All & Apply" (scope=all) | Unreviewed/flagged rows never touch live catalog | Both paths commit straight to live Product/CrossReference/price tables | Corrupts pricing/fitment/Shopify data for 30k parts, one click | Gate direct import behind typed confirm; default apply to exclude `needs_review`; name flagged count |
| 2 | Tier pricing bypassed on SO add-line | Sales Orders | **High** | Wholesale customer w/ 20% tier → SO → add product by id+qty only | Bill $104 (tier) | Bills $130 (retail) | Every tiered customer overcharged silently on the primary flow | Delete `selling_price` pre-fill at `sales_orders.py:354-355`; let `apply_product_line_defaults` resolve price |
| 3 | Negative discount inflates + finalizes invoice | Invoicing | **High** | Set line `discount_pct = -50`, finalize | Reject / clamp | `calc_line_total(100,1,-50)=150`; over-taxed, LOCKED invoice | Customer overbilled on a locked doc | Clamp [0,100] in `update_line`, `_add_line_internal`, `update_header`, `validate_for_finalise` |
| 4 | Core charges post as income in QBO | QBO/Accounting | **High** | Any invoice w/ core charge → QBO push | Post to liability | Posts to single Income account | Revenue overstated every period | Add `qbo_core_charge_liability_account`; rebind Core Charge item |
| 5 | Card surcharge cash never reaches QBO | QBO/Accounting | **High** | Surcharged card payment → QBO push | QBO total = bank deposit | `surcharge_amount` never read; QBO short | Bank-feed mismatch every surcharged sale | Book surcharge to income line/JE in `_build_payment_payload` |
| 6 | No role gate on money routes + ungated/unaudited catalog merge | Security | **High** | SALES/READ_ONLY login → finalize / reverse payment / push QBO / merge_category | Blocked or gated | All allowed; merges write zero audit rows | Any clerk can reverse cash, push books, reassign 30k products untraceably | Add role deps; assert `REPUSH_QBO`; add `assert_can`+`audit()` to merges |
| 7 | Reorder inert for 30,933/30,935 parts | Inventory | **High** | Low-Stock report / dashboard | Flags low fast-movers | Empty (only 2 parts have `reorder_point>0`) | Stockouts with no warning; false confidence | Backfill `reorder_point` (category defaults) + bulk editor |
| 8 | SO line-qty edit oversells | Sales Orders/Inv | **High** | 5 on hand → edit SO line to 50 | Blocked (like add-line) | `qty_available` = -45 silently | Promising stock you don't have | Port R6 guard into `update_line` delta>0 branch |
| 9 | 9,766 ambiguous/garbage cross-refs; unranked 8-row search | Search/Catalog | **High** | Cross-ref "N/A" → 3,440 parts; line-adder returns limit-8 no order_by | Correct part ranked first | Arbitrary slice; garbage collisions | Wrong-part-sold risk | Denylist purge + import validation + exact-first ordering + "N of M" |
| 10 | `qty_on_hand` written by 5 services outside InventoryService | Inventory | Medium (structural) | Code paths in invoice/PO/RA/vendor-return | Single writer + guard | Direct mutation everywhere; no drift detection | Silent stock desync on any partial failure | Consolidate writers; nightly resync + alert |
| 11 | No concurrency guard on finalize/void/payment/QBO | Money path | Medium | Double-submit finalize | Idempotent | Can double-act | Double invoices/payments/pushes | Atomic status-claim + row lock |
| 12 | Vendor Returns unreachable | Cores/Returns | Medium | No nav link; RA disposition dead-ends | Reachable + creates VendorReturn | Built but orphaned | Can't process merchandise returns to vendor | Add nav link; wire RA disposition |
| 13 | Optimistic lock inert on Invoice/PO/Quote header saves | Data integrity | Medium | Two tabs edit same header | Second save rejected | Last-write-wins silently | Lost edits | Thread hidden `_updated_at` through the 3 header routes |
| 14 | Vendor List N+1 | Vendors | Low | Load `/vendors/` | Uses aggregate maps | Lazy-loads full history per row | Slow page | Point template at existing `open_po_map`/`credit_map` |
| 15 | Dashboard Monthly-Revenue chart computed, never rendered | Reports | Low | Dashboard | Chart shows | Dead computation | Wasted work / missing insight | Render or delete |

---

## 7. MISSING FEATURES

**Critical before live use**
- Server-side price/discount guards (tier resolve on SO; clamp discount).
- Role gates on the money path + audit on catalog merges.
- QBO core-liability + surcharge booking (or keep QBO push OFF).
- `reorder_point` data + bulk editor.
- Import staging enforcement (kill the review-skip paths).

**Important after live use**
- Cross-ref garbage cleanup + FTS/substring search index.
- Concurrency/double-submit guards across finalize/void/payment/QBO.
- Vendor Returns reachable + RA→VendorReturn wiring.
- Customer bulk-import validation + per-row commit.
- Consolidated `qty_on_hand` writer + nightly drift resync.

**Nice to have later**
- Render dashboard revenue chart; product-profitability drill-in.
- Populate `list_price`/`manufacturer_part_number`/`map_price` (or de-scope dependents).
- ESN lookup/validation; "did you mean" fuzzy part matching.
- Session revocation on logout/password change.

**Do not build yet / distraction**
- Multi-tenancy / SaaS billing / onboarding wizard (premature — you're single-tenant).
- eBay integration before Shopify fields are actually populated.
- Second-vendor multi-sourcing UI before you have real dual-sourced data to prove it.
- Any new module — finish reaching/feeding what's already built first.

---

## 8. DUPLICATE / REPEATED / UNNECESSARY PROCESSES

- **Two import doors** (direct-write `import-run` *and* Review Queue) — the direct one defeats the safe one's entire purpose. Collapse to one.
- **Two migration systems** (`_PENDING_COLUMN_ADDITIONS` inline ALTERs *and* Alembic), kept in sync by hand comment. Retire the inline list onto Alembic-only with a transition cross-check test.
- **Dead templates** (`invoices/detail.html`, `invoices/new.html`, `search/_results_dropdown.html`, `reports/inventory.html`, `reports/sales.html`) and a superseded test file (`test_ar_buckets_and_last_contacted.py`) — delete to cut confusion.
- **Dead dashboard computation** (monthly revenue) — render or remove.
- **`truck_make`** column with zero UI; **`barcode`** populated on 0/30,935 — dead columns.
- **Vendor List N+1** re-introduces the exact pattern its own router already solved — the fix is to *use* what's there, not add code.

---

## 9. COMPETITOR BENCHMARK

| System | What it does better | Where JAK's ERP wins (diesel focus) | You're missing | Don't copy |
|---|---|---|---|---|
| **Epicor Eclipse** | Counter speed, real-time inventory across branches, deep distribution logistics, EDI | Cores as first-class dual-ledger; ESN/engine fitment; cross-ref depth | Multi-branch, EDI to PAI/IMB, counter POS | Its 1990s UI; heavyweight config |
| **Epicor Prophet 21** | Wholesale-distribution depth, pricing matrices, vendor rebates | Simpler, diesel-specific quote-to-cash | Rebate tracking, advanced pricing tiers | Its complexity/cost |
| **Epicor Vision** | Automotive aftermarket catalog (ACES/PIES), electronic cataloging | You could adopt ACES/PIES fitment standards | Standardized fitment data model | Full DMS scope you don't need |
| **Tekion / CDK / Reynolds / Dealertrack** | Franchise dealership DMS: service, F&I, OEM integration | You're parts-only and lighter/faster for it | Nothing relevant — different business | Their service/F&I/dealer modules entirely |
| **Microsoft Dynamics 365 / NetSuite** | Multi-entity finance, true GL, scale, ecosystem | Purpose-built diesel workflow; QBO boundary is right for your size | Real double-entry GL, multi-currency, scale | Their generality — you'd drown in config |
| **Odoo** | Modular, open-source, cheap, huge app store | Tighter diesel fit; less to configure | Odoo's inventory valuation & multi-warehouse maturity | Trying to match its breadth |

**Focus areas verdict:**
- *Parts-counter speed:* competitive on quoting; behind on search performance (fix FTS).
- *Catalog quality / cross-references:* your strength conceptually, undermined by data hygiene today.
- *Inventory control:* design is sound; enforcement + reorder data are the gaps.
- *Quote-to-order flow:* genuinely good.
- *Purchasing:* A- — competitive with anyone in your size class.
- *Accounting boundaries:* the QBO-as-bookkeeping-only decision is *correct* and better-scoped than forcing a full GL.
- *Customer follow-up:* solid (timeline, follow-ups, credit warn).
- *Reporting:* deep for your size.
- *Permissions / audit trails:* **behind everyone** — this is your weakest competitive axis right now.
- *Multi-channel:* Shopify-ready in schema, not yet in data.

**Your unique selling angle:** *"The parts system that actually understands diesel — cores, ESNs, and cross-references as first-class citizens, with QuickBooks kept where it belongs."* No generalist gives you that on day one.

---

## 10. MARKET POSITION

**Today: a strong single-shop internal ERP for a heavy-duty diesel parts distributor — not a SaaS product.**

Why: The spine is real and the diesel-specific depth is a genuine differentiator, so it's well past "inventory/quote tool." But single-tenant SQLite, no multi-tenancy/onboarding/billing, empty catalog-intelligence columns, and the verified money/control defects put it squarely at **"heavy-duty diesel parts ERP (internal)."** It becomes a **small-distributor ERP product** only after: real-world hardening, the security/audit layer, multi-tenancy, and populated catalog data. Sellable-SaaS is a 12-month-plus horizon, and only if you decide productizing is worth more than running the shop.

---

## 11. TOP 25 IMPROVEMENTS

| # | Area | Problem | Why it matters | Solution | Effort | Impact | Before go-live? |
|---|---|---|---|---|---|---|---|
| 1 | Import | Direct-write + apply-all skip review | Corrupts 30k-part catalog in one click | Gate/retire direct import; default apply excludes `needs_review` | M | High | **Yes** |
| 2 | Sales Orders | Tier price bypassed on add-line | Overcharges every wholesale customer | Delete `selling_price` pre-fill (`sales_orders.py:354-355`) + regression test | S | High | **Yes** |
| 3 | Invoicing | Negative discount inflates+locks | Overbills on locked docs | Clamp [0,100] in 4 write paths | S | High | **Yes** |
| 4 | Accounting | Cores post as income | Books wrong every period | Liability account + rebind item | M | High | **Yes** |
| 5 | Accounting | Surcharge cash dropped from QBO | Bank-feed mismatch every card sale | Book surcharge line/JE | S | High | **Yes** |
| 6 | Security | No role gate on money routes | Any clerk reverses cash/pushes books | Role deps + `REPUSH_QBO` assert | M | High | **Yes** |
| 7 | Security | Catalog merges ungated + unaudited | Untraceable 30k-row reassignment | `assert_can` + `audit()` on merges | S | High | **Yes** |
| 8 | Inventory | Reorder blind for 99.99% of catalog | Silent stockouts | Backfill `reorder_point` + bulk editor | M | High | **Yes** |
| 9 | Inventory | SO line-qty edit oversells | Promises phantom stock | Port R6 guard to `update_line` | S | High | **Yes** |
| 10 | Search/Catalog | 9,766 garbage cross-refs + unranked 8 | Wrong part sold | Denylist purge + exact-first order | M | High | **Yes** |
| 11 | QBO | No reversal/retry for synced-then-voided | Silent AR/AP drift | Flag needs-manual-reversal badge | M | Med | Soon |
| 12 | Money path | No concurrency guard | Double invoices/payments | Atomic status-claim + row lock | M | Med | Soon |
| 13 | Returns | Vendor Returns unreachable | Can't process to vendor | Nav link + wire RA disposition | S | Med | Soon |
| 14 | Data integrity | Optimistic lock inert on 3 headers | Lost edits | Thread `_updated_at` | S | Med | Soon |
| 15 | Inventory | 5 direct `qty_on_hand` writers | Silent desync | Consolidate + nightly resync | L | Med | Soon |
| 16 | Customers | Bulk import bypasses validation | Bad/dupe customers; all-or-nothing | Per-row validate + commit | M | Med | Soon |
| 17 | Search | Full-table scans | Slow counter search at scale | SQLite FTS5 index | M | Med | Soon |
| 18 | Testing | Smoke/visual opt-in | Regressions slip | Wire Playwright into CI gate | S | Med | Soon |
| 19 | Testing | No blanket CSRF/RBAC test | This class of bug found twice | Add money-route RBAC + CSRF-required suites | M | Med | Soon |
| 20 | UI | Preview-dock breaks <1024px | List screens broken on small screens | One-line responsive fix | S | Med | Soon |
| 21 | UI | Slide-overs lack focus trap/ARIA | Accessibility gap | Add `role=dialog`+trap | S | Low | Later |
| 22 | Reports | Dead revenue chart | Missing insight / wasted code | Render or delete | S | Low | Later |
| 23 | Vendors | N+1 on list | Slow page | Use existing aggregate maps | S | Low | Later |
| 24 | Catalog | Empty `list_price`/`mfr_part#`/10% uncategorized | Weak catalog intelligence | Populate via import or de-scope | L | Med | Later |
| 25 | Migrations | Dual migration system | Maintenance trap | Alembic-only + cross-check | L | Low | Later |

---

## 12. 48-HOUR FIX PLAN
*Goal: stop the bleeding on money and control. All are small, precisely located.*
1. **Delete the tier-price pre-fill** at `app/routers/sales_orders.py:354-355` (single highest-value one-line fix) + regression test posting product_id+qty only.
2. **Clamp `discount_pct` to [0,100]** in `InvoiceService.update_line`, `_add_line_internal`, `update_header`, `validate_for_finalise` + a "negative discount cannot finalize" test.
3. **Port the R6 negative-inventory guard** into `SalesOrderService.update_line`'s delta>0 branch (mirror `_add_line_internal:1056-1080`).
4. **Hold QBO push** (it's fail-soft — flip it off) until the two accounting fixes land.
5. **Add the `/vendor-returns/` nav link** to `base.html` — one line recovers a whole module.

## 13. 7-DAY FIX PLAN
6. **Close the auth gaps in one sweep:** role deps on finalize / payment reversal / credit-memo / customer-import; `assert_can(REPUSH_QBO)` on every `push_*`; `assert_can` + `audit()` on `merge_category/brand/manufacturer`.
7. **Neutralize the import bypass:** typed named-risk confirm on `/products/import-run`; change "Approve All & Apply" default scope to exclude `needs_review` with a flagged-count second confirmation.
8. **Fix QBO accounting:** add `qbo_core_charge_liability_account`, rebind the Core Charge item; book `surcharge_amount`. Then re-enable push.
9. **Backfill `reorder_point`** (category-level defaults) + bulk-edit action (mirror `products.py:844-881`).
10. **Cross-ref cleanup:** denylist-purge placeholder ref numbers, add import-time validation, add exact-match-first ordering + "N of M" truncation to the line-adder.

## 14. 30-DAY BUILD PLAN
- Concurrency hardening: atomic status-claim on finalize/void, row lock around QBO push read-check-create, double-submit regression tests.
- Thread `_updated_at` through Invoice/PO/Quote header saves so `check_version()` actually fires.
- Consolidate the five `qty_on_hand` writers behind one `InventoryService` entry point + nightly resync-with-drift-alert.
- Customer bulk-import parity (per-row validate + commit).
- QBO operational visibility: extend retry/summary to payments/bills/credit-memos; flag reversed-but-synced payments.
- Wire Vendor Returns end-to-end (RA disposition → real VendorReturn).
- Test the controls: money-route RBAC suite + blanket CSRF-required suite; make Playwright smoke a required CI gate.
- UI: responsive preview-dock fix; focus traps/ARIA on global slide-overs.

## 15. 90-DAY PRODUCT PLAN
- SQLite FTS5 (or move hot search to an indexed store) for substring part-number search.
- Populate catalog intelligence: real second-vendor overlapping import to *prove* multi-sourcing; backfill `list_price`/`manufacturer_part_number` or de-scope their dependents.
- ESN lookup/validation via the existing stubs.
- Retire the inline migration list onto Alembic-only with a transition cross-check.
- Session revocation on logout/password change; fail-closed demo-reset; loud missing-Fernet-key startup warning.
- Shopify feed: confirm every required field (title, clean category, fitment, brand, image, SEO, price, stock, customer-facing SKU) is populated before flipping eBay on.
- **Decision gate at day 90:** run the shop on it and measure, *or* commit to productization (multi-tenancy, onboarding, billing) — not both at once.

---

## 16. THINGS TO STOP BUILDING
- **New modules.** You have unreachable/starved features already built (Vendor Returns, reorder, market pricing). Finish the last mile before adding surface area.
- **The second import door.** Kill the direct-write path rather than maintaining two.
- **Multi-tenancy / SaaS scaffolding.** Premature by a year.
- **eBay** until Shopify fields are actually populated.
- **Multi-vendor sourcing UI** until you have real dual-sourced data (today every product has exactly one vendor source).
- **Dead columns/templates/computations** (`truck_make`, `barcode`, dead invoice templates, unrendered revenue chart) — delete, don't extend.

## 17. THINGS TO DOUBLE DOWN ON
- **Purchasing / Receiving / 3-way match** — your A- crown jewel; deepen it (inline bill actions, EDI to PAI/IMB later).
- **Core lifecycle** — a real diesel differentiator; keep it first-class (fix the QBO liability posting so it's also *correct*).
- **The 45-second quote workspace** — genuinely fast; protect it with the price/search fixes.
- **Cross-reference / fitment depth** — your moat vs. generalists; invest in *data hygiene* to make it trustworthy.
- **The shared totals engine + test discipline** — this is why the code is fixable at all; keep the "one engine, pinned by tests" pattern.
- **QBO-as-bookkeeping-only boundary** — the right architectural call; make it accurate, don't expand its scope.

---

## 18. FINAL BRUTAL OPINION

- **Would I trust this system with real money today?** No. It will silently overcharge tiered customers, let a negative discount inflate a locked invoice, and misbook cores and surcharges into QuickBooks on nearly every transaction. Fix the six money/books items first.
- **Would I let an employee use it today?** No. Not because it's slow — it's fast — but because a normal clerk can overcharge a fleet account, oversell stock, reverse a payment, and corrupt the catalog, all silently and mostly ungated.
- **Would I sell this to another business today?** No. It's a strong internal ERP, not a product. No multi-tenancy, no onboarding, and the verified defects would sink a diligence review.
- **What must be fixed before it becomes dangerous?** It's *already* dangerous in three specific ways — hold QBO push, and land the tier-price / discount-clamp / role-gate / import-gate / reorder fixes before daily operation.
- **What makes it potentially valuable?** It genuinely understands diesel parts distribution in a way no off-the-shelf ERP does — cores, ESNs, cross-references, vendor part numbers — sitting on clean, well-tested code with the right QuickBooks boundary.
- **Next best step:** Run the **48-hour plan** (tier price, discount clamp, SO-edit guard, hold QBO, vendor-returns nav link), then the **7-day plan** (auth gaps, import gate, QBO accounting, reorder backfill, cross-ref cleanup). That converts a C+/70 "quietly wrong" system into a B/82 you can actually put behind the counter — in about two weeks, with fixes that are almost all one-function guards, permission asserts, and a data backfill. **Do not start daily operations, and especially do not let QBO push run, until the six money/control items are done.**

---
*Evidence base: 16-subsystem code audit + adversarial re-verification of every Critical/High (13/13 confirmed, 0 refuted) + live headed-browser testing against a sandboxed copy of the 81 MB live `jaks.db`. Live data facts (30,935 products; `reorder_point>0` on 2; `qty_on_hand>0` on 4; 224,144 cross-refs with 9,766 ambiguous groups; `manufacturer_part_number`/`list_price` empty on 100%; 3,095 uncategorized; 3,111 zero-cost; 5 quotes / 2 SOs / 2 invoices / 1 payment / 3 POs total) queried directly.*
