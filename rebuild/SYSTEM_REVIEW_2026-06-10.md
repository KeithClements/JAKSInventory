# JAK's Diesel ERP — Full System Review (2026-06-10)

**Method:** 16 subsystem auditors read the actual routers/services/models/templates (not the UI), 47 Critical/High claims were adversarially re-verified against the cited code, 43 survived, 4 were refuted. Branch: `backend/workflow-series-3`. 883 tests in suite.

**Overall grade: B-. Daily-use verdict: NOT YET — run a 2–3 week wiring sprint first.**

The transactional engine is genuinely sound: one totals engine drives every invoice figure, finalize is atomic with a re-finalize guard, the inventory ledger and moving-average COGS are mathematically verified, and the backorder→PO→receive→auto-commit loop passes real tests. Going live today would not crash — it would quietly lose money: every card payment eats the processing fee, every credit memo is a dead document, freight/warranty lines vanish on quote→SO conversion, AP bills can be double-entered and never marked paid, denied cores are never charged back, and one routine cleanup action (renaming a manufacturer) silently splits the 13k-part catalog. Every one of these is a located, wiring-level fix — no redesign needed.

**Four scary claims were REFUTED on verification (good news):**
1. "13k imported parts quote at $0" — FALSE. `price_override` is populated from the import; quoting prices are real.
2. "Backordered lines can ship into negative inventory at finalize" — FALSE. The finalize inventory guard exists and blocks.
3. "AI-categorize API call is broken" — FALSE. `output_config` is the correct parameter; the feature works.
4. "Product list has no pagination / 13k-row DOM freeze" — FALSE. Server-side paging (100/page) exists.

---

## 1. Navigation & workflow — B+ shell, B- workflow

The full chain works end-to-end: quote → convert to SO → per-line "Order" button creates a draft PO for the preferred vendor → receive against PO (auto-commits to the linked SO, FIFO) → fulfill SO to invoice → finalize → take payment. That spine is test-verified.

Friction a counter person will hit:
- **Convert is buried.** Convert-to-Invoice is inside a More▾ menu; Convert-to-SO is a dropdown with radio buttons + confirm. These are the most common actions on a quote — make Convert the primary CTA.
- **Child lines (upgrade/optional) take 2× the clicks** of a primary add — search twice for an Economy/Recommended/Premium injector quote.
- No keyboard shortcut for New Quote/New Invoice (F2 only opens Quick-Add Product). No "Start Quote" button on the customer record.
- **Dead ends that look unfinished:** credit memos can be created but never applied/closed; cores reach "ready to ship" with no way to batch them to PAI; Duplicate Quote service exists with no route or button; the SO "Fulfilled" tab always shows zero; the disabled "Export CSV" button appears on 8 of 9 list screens; the green "Live" dot means nothing.
- Quote list hard-caps at 150 rows with no pagination — the All tab breaks after ~6 months of volume.

## 2. Product management — B-

| Capability | Status |
|---|---|
| Internal SKU | ✅ JAKS scheme locked, unique at DB level; backfill script built but **not yet run** — legacy `JAKS-PAI-#` SKUs (which leak vendor numbers) persist until you set vendor digits and run it |
| Vendor part number | ✅ on ProductVendorSource (multi-vendor) |
| Manufacturer part number / OEM / cross-refs | ✅ CrossReference table, 8-state lifecycle, searched by the line adder |
| Category/subcategory | ✅ 3-level tree |
| Engine make/model | ⚠️ picker only on quotes; SO/invoice/product forms are free text |
| Application notes | ⚠️ ProductApplication table populated by import, **no UI to view/edit** |
| Cost/sell/margin | ⚠️ engine correct, but `product.selling_price` (hardcoded 30% fallback) still used in 8+ templates incl. `invoices/new.html` data-price |
| Images | ⚠️ raw PAI CDN URLs, no validation/re-fetch |
| Stock status | ✅ on-hand/committed/available + ATP tooltip |
| Vendor source | ✅, but **ghost preferred vendor after soft-delete** (confirmed High) shows deleted vendor + stale cost |
| Hidden vendor numbers | ✅ quote/invoice prints show JAKS SKU only (verified today) |
| House brand/performance flag, product family | ❌ not present (superseded_by exists; no family grouping) |
| Duplicate detection | ⚠️ app-level only — **no DB uniqueness** on (product_id, vendor_id), cross-ref grain, or applications |

Products list search can't find parts by OEM number — it rolls its own filter instead of using SearchService. The line adder finds "4089102"; the catalog screen doesn't. Daily confusion guaranteed.

## 3. Category maintenance — C (weakest area reviewed)

Basic CRUD works (tree + brands + manufacturers at /categories/, soft-delete guards, bulk-assign from products list, review queue). What's missing is everything a messy 13k-part import actually needs:
- **No merge** ("Injector"/"Injectors"/"Fuel Injectors" can't be consolidated)
- **No reparent/move** (can't move Injectors under Engine Components without delete+recreate)
- **No rename cascade — the program's only verified CRITICAL.** `update_manufacturer` renames the lookup row but never updates free-text `Product.engine_manufacturer`. Renaming "CUMMINS"→"Cummins" makes every tagged part invisible to the engine-make filter. One UPDATE statement fixes it.
- **No category `code` input on the form** — every new category mints a machine-derived SKU segment you can't control; wrong JAKS SKUs are frozen at mint.
- Parent rows count only direct products (a parent with 500 parts in children shows "0 items"); classifier keyword match has no word boundaries; import auto-creates junk top-level categories from any unrecognized CSV Type string.

**Where it should live:** keep it under **Products** (it's catalog administration counter staff touch during import cleanup — burying it in Admin adds friction), with destructive ops (merge, rename, reparent) permission-gated. Inventory is the wrong home — categories are about the catalog, not stock.

## 4. Smart Import — B-

What's already right: stage→review→apply gate works end-to-end; duplicate detection checks both `product.sku` and `vendor_sku` (re-importing the same 13k feed doesn't double-create); cross-ref matching flags candidates against existing OEM numbers; confident-vs-needs-review split; AI categorization works (refuted claim); preview dock with category/price corrections; dry-run; admin-gated apply; pricing-update mode correctly never touches COGS.

What it needs, in order:
1. **Vendor digit guard (confirmed High):** `full_import` is PAI-pinned — any non-PAI CSV mints vendor digit '9', brand='PAI', and a PAI vendor source, corrupting the SKU namespace. Block imports until a vendor record with a confirmed unique digit exists.
2. **Current-vs-incoming diff** in the preview dock — reviewers approve UPDATE rows blind today.
3. **Column-mapping step with saved per-vendor templates** — headers are hard-coded aliases; a SAMPA/IMB feed with "Part Number" instead of "Variant SKU" silently imports SKU-less. This is the only safe path to multi-vendor feeds.
4. **DUPLICATE-row filter in apply** — feeds with repeated SKUs wedge the batch so it never reaches APPLIED and the Apply button stays lit forever.
5. **Global cross-ref collision check** — same OEM number can silently attach to two different products, breaking the disambiguation search exists to provide.
6. Gate `_resolve_category` auto-creation; add batch delete; deprecate the direct `/products/import` route (it bypasses the review gate entirely — two import paths with no guidance is dangerous).

## 5. Inventory & receiving — B (strongest core), receiving B-

Verified working: on-hand/available/committed (full SO_COMMITTED ledger), receiving against POs with moving-average COGS (math verified, incl. zero-stock first receipt), receive-without-PO, backorder→PO with FIFO auto-commit on receipt, adjustments with permission gates, ledger row on every mutation.

Flags:
- **Receive form pre-fills the FULL outstanding qty** — one careless click marks a partial delivery fully received and allocates customer SOs against stock that isn't there. Default to 0 + explicit "Receive All" button.
- **`qty_backordered` only ever decrements on PO receipt** — SO cancellations inflate the demand metric permanently from day one.
- **No ledger re-sync tool**, and the one recompute method (`get_qty_on_hand`) omits SO_COMMITTED txns, so it would resync wrong. Cache drift is unrecoverable without DB surgery.
- **Serial tracking for cylinder heads is a dead scaffold** — model exists, zero write paths. No capture at receive, no consume at invoice.
- **Inventory valuation ≈ $0 and margins read 100%** until parts flow through PO receipts (cost=0 on the imported catalog). Reports need a vendor_cost fallback + warning banner.
- Freight is never landed into COGS (`landed_cost_per_unit` never written) — at $150–500/LTL shipment that's real margin error.
- Damaged goods route through vendor returns (works) but there's no damaged-at-receipt disposition and no receiving slip to check goods off against.

## 6. Purchase orders & vendor bills — B pipeline, AP can't close

The PO→send→receive→bill pipeline with the 3-way match queue and full discrepancy resolution is **the strongest subsystem audited**. Per-line backorder→PO from SOs works; over-receipt detection works; vendor separation via ProductVendorSource works.

Confirmed holes:
- **Duplicate vendor bill numbers are unguarded** — the same PAI invoice entered twice creates two approvable bills. Double-pay risk on your first real A/P cycle.
- **Bills can never be marked PAID** — no route, no service method, no UI. AP sits APPROVED forever; vendor statements can't be reconciled.
- No standalone vendor-bills list (only per-PO + the match queue) and no AP aging.
- No PO-from-low-stock reorder action; the low-stock tab is a list with no button.
- Freight/misc on bills never allocates to inventory cost (see §5) — for now it's effectively expense-only, which is at least QBO-compatible, but margin reports won't include it.

## 7. Quotes / SOs / invoices — B-

The counter hot path is real: search (SKU/OEM/vendor#, separator-normalized) → one-click add → tier pricing via PricingService → core child line auto-add → inline qty/price/disc/margin edit with back-calc → live totals → autosave with honest dirty state → print/PDF. Partial fulfillment, deposits with proportional carry-over, void-with-rollback all test-verified. This does feel like a parts-counter system, not an accounting app.

Confirmed problems, in priority order:
- **Quote→SO conversion silently drops MISC/FREIGHT/NOTE/WARRANTY lines** ([quote_service.py:381](app/services/quote_service.py:381) filters to PRODUCT only; convert-to-invoice carries everything). Direct underbilling on the daily hot path.
- **Warranty lines hardcode unit_cost=0** — every warranty upsell books 100% margin.
- **Credit-hold customers can be quoted/SO'd/invoiced with zero warning** — `credit_status` is computed and passed to contexts, but the warn macro renders only on the customer detail Account tab.
- **SO line cancel orphans the CORE_CHARGE child** — phantom core deposits inflate the SO subtotal.
- No tax preview on quotes (customer sees tax only after invoice conversion).
- Search dropdown price hint uses the hardcoded-30% property, so the preview can disagree with the actual tier price the line gets.
- Mark Lost sends free text only — competitor name/price fields (your most valuable lost-sale signal) are never captured though the service accepts them.
- Invoice list: shows QBO state via tabs incl. `modified_since_sync` (good), but no sortable columns and an N+1 on allocations.

## 8. Core tracking — C+

The individual lifecycle is end-to-end: charge auto-created at finalize → customer return → inspect (accept/hold/reject) with double-credit guard → ship to vendor → vendor decision, with location movement audit trail and slip print.

The vendor side leaks money on four confirmed fronts:
1. **CHARGED_TO_CUSTOMER on a vendor-denied core is a no-op** — the credit already issued is never reversed; JAKS eats every denied core.
2. **No VCR batch UI exists at all** — model + print templates are there, but no route to create a VCR, add cores, or record the vendor decision. You cannot batch 15 cores into one box to PAI from any screen; "ready to ship" accumulates forever.
3. **`mark_overdue_cores()` is never called by anything** — aging core liability accumulates silently.
4. `credit_invoice_id` is never written, so every core slip prints with a NULL invoice reference (one-line fix).

Also: the §5.4 dollar tiles (outstanding liability, credits issued, vendor recoveries) are computed but not rendered in the template.

## 9. Customer management — B-

All the basics check out: company/contact/phone/email/addresses, tax-exempt with cert field, terms (COD/Net-30/Net-60), credit limit + hold flag, notes, CRM call log, unified timeline (quotes/SOs/invoices/payments/activities), statements with print/PDF, AR aging consistent across widget/statement/report, fuzzy duplicate detection on create, search by name/phone(digit-normalized)/email/account#.

Gaps: import dedup is exact-name only (weaker than the create form — misspelled dupes import silently); no merge tool for dupes that get through; `account_number` has no uniqueness constraint; statements are never persisted (no dispute trail — the model exists, zero writes, and its `due_120` bucket disagrees with the runtime's `over_90`); tax-exempt cert expiry is stored but never surfaced; the timeline "Mark done" button silently no-ops in the browser (204 with no HTMX wiring).

## 10. QBO sync — C+ (right posture, one-legged execution)

The architecture matches your stated preference exactly: ERP owns operations, QBO is bookkeeping-only, push is accounting-summary style, fails soft, and **never touches the money path** (success→mark synced/lock, fail→mark failed). CORE_CHARGE maps to its own income item. Invoice push + batch push are tested.

Why the books would still be fiction from day one:
- **No payment push** — QBO AR stays open forever on every invoice.
- **No vendor bill push** — QBO AP and COGS are zero; PAI invoices never enter the books.
- **No credit memo push.**
- **Customer resolution blindly binds the first DisplayName match and commits it permanently** — wrong-customer AR for same-name fleet accounts. Refuse auto-bind on multiple hits.
- **OAuth tokens + client secret are plaintext in jaks.db**; a copied DB file is a 100-day window into your books. Fernet-encrypt before connecting the real company.
- Sync visibility: status chips + filter tabs exist (synced/pending/failed/modified-since-sync), but there's no sync-status report, no audit rows on push, every push is attributed to user 1, and failed-sync retry is 100% manual.
- No AST detection: if `qbo_push_tax` is set wrong, QBO either rejects invoices or double-taxes silently.

**Multi-invoice batch sync verdict:** safe in design (per-invoice fail-soft, idempotent mark-synced) — keep it operator-triggered from the filtered tab, never automatic, until payment push exists and pushes write audit rows. Never push: drafts, voided invoices, POs/receipts (operational docs), or anything inventory-quantity-shaped — that posture is already correct in code.

## 11. Reporting — B-

Exists and correct: AR aging (A-), Open POs (A-), Outstanding cores (A-), Overdue invoices + interest (A-), Sales by customer, Sales by product, Inventory valuation, Sales tax collected, Lost sales log, plus a live dashboard (7 KPI tiles, revenue chart, top customers, follow-ups, low-stock top-10).

Missing for a diesel parts business: **low-stock/reorder report** (dashboard caps at 10 rows, no print/export — you need this every morning), **sales by category**, **dead stock/slow-moving** (you'll sit on stale diesel inventory and never see it), **vendor purchases/performance** (fill rate, lead time), **quote conversion rate**, **customer purchase history detail**, **QBO sync status report**, **inventory movement history**.

Confirmed defects: **zero CSV export anywhere** (every report has a disabled button — you can't hand AR aging to collections or sales tax to your accountant); margins are fiction until receipts populate costs (needs vendor_cost fallback + warning banner); the sales tax report's taxable-revenue column ignores line discounts (overstates the base you'd file on); dashboard overdue count uses UTC datetime while the report uses dates — the two numbers will disagree.

## 12. UI/UX — B-

The design system is coherent and genuinely counter-appropriate: dark olive sidebar, color-coded row stripes (credit-hold red > quotes amber > invoices blue), Ctrl+K global search with keyboard nav, Qty→Price→search Enter-key flow, ATP hover tooltip, honest autosave pill, shared jakConfirm modal, margin show/hide toggle.

Specific changes:
- **Make first-class:** Convert-to-SO/Invoice on quotes (out of the More▾ menu); Take Payment as the *only* primary CTA on finalized invoices.
- **Demote to a More▾ menu:** QBO push and Issue Credit Memo on the invoice header — 7 inline buttons wrap to a second row at 1366×768 (confirmed, common shop monitor).
- **Hide:** the 8 dead "Export CSV" buttons (a disabled button trains users to stop trying) — or better, wire the routes; the meaningless green "Live" dot.
- **Fix:** SO line cancel still uses a native browser `hx-confirm` (accidental dismissals release committed inventory); PO workspace has a local re-implementation of the confirm modal; Log Call submits with no customer selected; sticky table headers exist only on the customer list — add to products/invoices/quotes/SOs; invoice list has no column sort; the always-expanded 5-field vehicle/engine grid bulks every invoice (quote collapses it — copy that); no focus trap in modals; mobile has no search affordance at all (Ctrl+K bar is hidden `sm:` down with no tap target).
- Empty states are good (3-case macro). Error states: report index catches all failures into one generic banner with every KPI as $0 — you can't tell which report broke.

## 13. Risk review — ranked

| Risk | Rank | Evidence |
|---|---|---|
| Manufacturer/brand rename splits the 13k catalog | **CRITICAL** | No cascade to free-text `Product.engine_manufacturer`; the obvious post-import cleanup makes parts invisible to filters |
| Bad imports (vendor digit '9' corruption, junk categories, wedged batches) | **HIGH** | PAI-pinned full_import; auto-category creation; DUPLICATE rows block APPLIED |
| Inventory cost errors (cost=0 catalog, freight never landed, 100% margins) | **HIGH** | All sales/margin/valuation reports fiction until receipts; no fallback or warning |
| Core tracking money leaks (chargeback no-op, no VCR, silent aging) | **HIGH** | JAKS eats denied cores; vendor loop can't close |
| Hidden close-out gaps (surcharge, CMs, NSF, AP) | **HIGH** | Confirmed wiring holes that bleed money invisibly |
| QBO one-legged sync + wrong-customer binding + plaintext tokens | **HIGH** | Books unusable; permanent wrong-AR risk; credential exposure |
| Duplicate products | **MEDIUM-HIGH** | App-level dedup works for same-SKU re-import, but no DB constraints and no fuzzy part-number fallback — a reformatted CSV creates thousands of dupes |
| Poor category structure compounding | **MEDIUM-HIGH** | No merge/reparent means every import mess becomes permanent |
| Users editing vendor/manufacturer data wrong | **MEDIUM** | Free-text brand/manufacturer columns, no FK to lookup tables; vendor-number hiding on customer docs is solid |
| Duplicate customers | **MEDIUM** | Good fuzzy guard on create; weak on import; no merge tool |
| Hidden sync failures | **MEDIUM** | Fail-soft + failed tab exist; no report, no audit rows, manual retry |
| Missing audit trail | **MEDIUM** | Inventory ledger is complete; QBO pushes and user attribution are not (user_id=1 hardcoded) |

Security (cross-cutting, fix before any internet exposure): session signing secret lives inside the SQLite DB it protects; `JAKS_SKIP_AUTH` env var disables ALL auth with no production guard; QBO tokens plaintext. CSRF downgraded to Medium (SameSite=Lax is a real defense on a LAN app).

## 14. Report card

| Area | Grade |
|---|---|
| Navigation | B+ |
| Product management | B- |
| Inventory | B |
| Purchasing | B |
| Receiving | B- |
| Quotes | B- |
| Invoicing | B- |
| Customer management | B- |
| Core tracking | C+ |
| QBO sync | C+ |
| Reporting | B- |
| UI/UX | B- |
| **Readiness for daily use** | **C+** |

## 15. Final recommendations

### Top 10 fixes BEFORE daily use (≈2–3 weeks; every item is located and small)
1. **Manufacturer/brand rename cascade** — [category_service.py:220](app/services/category_service.py:220). The one critical; one UPDATE statement.
2. **Quote→SO line filter** — [quote_service.py:381](app/services/quote_service.py:381): carry MISC/FREIGHT/NOTE/WARRANTY (exclude only CORE_CHARGE). Stops underbilling on every converted quote.
3. **Wire the CC surcharge** — pass `apply_surcharge` in [invoices.py:932](app/routers/invoices.py:932) and [payments.py:130](app/routers/payments.py:130). Stops eating every card fee.
4. **NSF one-liner** — `is_reversed` filter in `Payment.amount_allocated` ([invoice.py:295](app/models/invoice.py:295)). Unstrands reversed funds.
5. **Credit memo apply/close routes + buttons** — services are already complete; the documents are just dead.
6. **AP closure** — duplicate (vendor_id, bill_number) guard + `mark_bill_paid` route. Stops double-pay and lets AP reconcile.
7. **SKU-scheme integrity bundle** — category `code` input on forms; vendor quick-create truncation [:10]→[:4]; block non-PAI feeds until a vendor with a confirmed digit exists; clear ghost preferred on vendor-source delete. Then set vendor digits and run the SKU backfill.
8. **Core money trio** — implement CHARGED_TO_CUSTOMER chargeback; set `credit_invoice_id` at creation; call `mark_overdue_cores()` from startup.
9. **Counter safety pair** — render `credit_warn` on quote/SO/invoice workspaces (contexts already pass it); receive form defaults to qty 0 with an explicit "Receive All"; cascade-cancel core children + decrement `qty_backordered` on SO cancel.
10. **Truth & paper** — CSV export for AR aging/overdue/sales-tax/invoices/customers; vendor-cost fallback + zero-cost warning banner in margin reports; sales-tax `line_total` fix; security trio (session secret to env var, gate `JAKS_SKIP_AUTH`, Fernet-encrypt QBO tokens).

### Top 10 improvements AFTER go-live
1. VCR batch UI (create / add cores / vendor decision) — model and prints already exist.
2. QBO `push_payment`, then vendor bills and credit memos; customer-match disambiguation; audit rows + real user_id.
3. ESN field on warranty claims (PAI rejects claims without it) + warranty_type selector + record-vendor-credit route.
4. Engine make/model picker on SO/invoice/product forms; products-list OEM search via SearchService.
5. Competitor part numbers written into cross_references at import + a competitor search strategy — **must land before you load the PAI scraper competitor data.**
6. Low-stock reorder report with preferred-vendor columns + "Create PO from low stock" bulk action.
7. Category merge + reparent tools; word-boundary classifier; descendant-inclusive counts.
8. Freight landed-cost allocation into moving-average COGS.
9. Serial number capture at PO receive / consume at invoice finalize (cylinder heads).
10. Counter polish: Duplicate Quote route, structured Mark Lost with competitor capture, quote tax preview, statement persistence, invoice list sort + N+1 fix, dead-stock and quote-conversion reports.

### Do NOT touch right now
The invoice totals engine and atomic finalize (D-1 tax gate included); the inventory ledger + moving-average COGS; the 3-way match queue; the Smart Import stage→review→apply gate; the SKU scheme design; the QBO fail-soft posture (never blocks the money path); the design-system primitives (jakConfirm, line adder, preview dock, autosave pattern). These are the load-bearing walls and they're verified good.

### Already working well
The quote counter hot path end-to-end; SO commit/backorder routing with deposits and void rollback; the PO pipeline (strongest subsystem); payment record/spread/NSF flows; the individual core lifecycle with double-credit guards; the RA lifecycle; fuzzy customer dup detection; AR aging consistency across all three surfaces; Ctrl+K; cross-ref search in the line adder; 883 tests with real money-path depth.

### Sellable someday?
**The bones are sellable; the product is not — yet.** What's genuinely differentiated for the niche: core tracking with customer/vendor margin separation, OEM/vendor/competitor cross-reference identity, ESN/engine fitment on documents, multi-vendor sourcing with an opaque customer-facing SKU scheme, backorder→PO→auto-commit, and a QBO posture (ERP owns ops, QBO books only) that most small parts shops actually want and can't get from QBO-centric tools. Generic SMB ERPs don't have any of that; that's a real wedge for diesel/HD parts counters.

What stands between this and a sellable product: it's a single-tenant SQLite app with secrets in the DB, an auth bypass env var, no role enforcement on routes, no installer/upgrade story (inline ALTERs on startup), no Postgres option, vendor-specific import logic pinned to PAI, and no docs/onboarding. To sell it: harden security, abstract the import layer into saved per-vendor templates, finish the QBO second leg, add a clean deploy/upgrade path, and run it at JAK's for 6 months as the reference customer. The honest sequence is: make it boringly reliable for your own counter first — that same work is exactly what makes it sellable.
