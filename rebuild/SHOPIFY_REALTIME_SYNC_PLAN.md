# Shopify Near-Real-Time Availability + Auto-SEO Sync — Plan

**Date:** 2026-06-24
**Goal:** Be a top-notch power seller. Never sell a part that the vendor (PAI/IMB) can't ship and that we can't drop-ship. Keep the storefront fresh automatically as vendor stock changes, and auto-generate SEO so every product is listed professionally.

---
## STATUS (2026-06-24)
- **Phase 0 — BUILT, TESTED, REVIEWED, LIVE-MIGRATED (uncommitted).** The hide/re-list engine ships. Adversarial review found 1 CRITICAL durability bug (flag persisted only on a coarse chunk-commit → a crash could strand a hidden listing as un-re-listable) — **fixed** (commit per changed product). Full suite **2304 + new green**. Migration `0006` applied to live `data/jaks.db` (column on all 30,931 products).
- **Phase 3 — BUILT, TESTED, REVIEWED (uncommitted, BOTH repos).** The one-click `.bat` pipeline. **Found + fixed the real reason availability never reached Shopify:** the scraper exported `in_stock`/`status`, but the ERP reads an `availability` column (`in_stock`/`out_of_stock`/`discontinued`) — they never matched. Now: scraper emits `availability` (AxleForge `export_jaks.py` + `_availability()`), a section-scrape CLI (`scrape_section.py`), an ERP bridge (`import_and_push.py`, in-process — no auth wall), and two `.bat`s. Adversarial review found 3 issues — **all fixed/documented** (pointer-advanced-before-scrape → advance-on-success; missing-key half-state → explicit `enc:` guard; sweep behavior documented). Scraper suite **590 green**; bridge dry-run validated end-to-end (of 22,675 PAI rows, 20,540 matched → **2,331 OOS + 613 discontinued** would be flagged). Real export carries **18,037 in_stock / 3,671 out_of_stock / 967 discontinued**.
- **KEY FINDING:** the engine is **inert until availability data is imported** — `vendor_availability` is blank for all live products, so a dry-run audit hides 0. The ~46 live-OOS parts stay live until the scraper's availability is imported (one-time catch-up, or the first Phase-3 section run).
- **Decisions locked:** one-click = **double-click `.bat`** on the scraper side; push = **fully automatic** on section completion; re-list = **only listings the ERP itself hid**; **sold-out state for top sellers** wanted → **Phase 5**.
- **Op note:** any `.bat`/script that pushes must export `JAKS_FERNET_KEY` so the Shopify token decrypts.
- **IMB pipeline + ERP write-path VERIFIED (2026-06-24).** The ERP had never actually pushed (all prior Shopify writes went through Claude Code/MCP). Verified the ERP token against the LIVE store: `write_products`+`write_inventory` granted (hide/re-list + stock authorized), 20/20 sampled product links resolve, 20/20 cached statuses match live. **Found + fixed 1 gap:** token lacks `read_locations`, so the stock-sync `locations` query was denied → `_location_id()` now falls back to an id-only query (verified resolves the warehouse). **IMB extended:** `scrape_imb.py` (full-catalog refresh via the price API, auto-login, no CAPTCHA — the FAST lane) + `Refresh IMB + Push.bat`. IMB bridge dry-run: 10,292/10,297 matched → **3,733 IMB out-of-stock flagged** (IMB had zero availability sync before). **Timing reality:** push is minutes but gated by the scrape — PAI ~4-day cycle (slow page scrape), IMB daily-capable (fast API).
- **Phase 2 — BUILT, TESTED (uncommitted, ERP).** Auto-SEO. `AIDescriptionService.backfill_seo()` generates + **persists** `seo_description` (+ `seo_title` when blank) — the write-back the manual "Suggest with AI" button never did. `scripts/backfill_seo.py` (dry-run default, `--apply/--limit/--overwrite/--ids`); `import_and_push.py --seo` fills a section before push (opt-in, cost-gated). Key-name reconcile so one Anthropic key serves SEO + the Smart-Import classifier. **6,114 of 30,931 products need SEO** (rest already have it). 10 tests; full suite **2,316 green**.

**Owner decisions (locked in interview):**
- Out of stock at vendor → **hide the listing completely** (Shopify status DRAFT).
- Back in stock → **auto-republish** to Shopify (no human review).
- Source of truth → **ERP** (decision validated below — the scraper is architecturally barred from pushing).
- SEO descriptions → **fully automatic** on first publish, for **every** product (PAI + IMB + turbos).
- Cadence → scraper runs **1–2×/day**, both apps on the **same Windows machine**, scraper currently run **manually**.
- Scale → **20,000+ SKUs**.

---

## 1. Root cause of the oversell (this is the headline)

The customer bought a part PAI was out of **not by bad luck** — there are two real, confirmed bugs in the availability feature shipped at `@d357785`. The feature sets internal flags correctly but **never changes the live storefront**.

### Bug A — The "de-list gap" (the big one)
`_apply_availability_to_product()` in [app/services/product_import_service.py](app/services/product_import_service.py) (~line 1650) does this on `out_of_stock`:

```python
product.vendor_availability = eff   # 'out_of_stock' — and that's it
```

The **only** downstream effect is that [app/services/shopify_service.py](app/services/shopify_service.py) `sync_linked()` / `sync_inventory_all_linked()` **exclude** that product from the *next* outbound push:

```python
Product.vendor_availability.notin_((OUT_OF_STOCK, DISCONTINUED))
```

That means: a part already **ACTIVE** on Shopify **stays ACTIVE and buyable**. Marking it OOS just stops *future refreshes* from touching it. **Nothing pulls the live listing down.** There is no `productUpdate(status:DRAFT)` anywhere in the availability path. `discontinued` is the same story — it sets `is_active=False` in the ERP but never tells Shopify.

### Bug B — Variants are created **untracked**, so qty=0 does nothing
In `to_product_set_input()` (~line 275 of shopify_service.py) every variant is created with `inventoryItem.tracked = false`. **Shopify treats untracked variants as infinitely in stock.** So even if we pushed `quantity = 0`, customers could still buy. To make "0 = unbuyable" actually work you must first set `tracked:true` **and** `inventoryPolicy:DENY`. Today neither is set, and `inventoryPolicy` is never written at all.

**Net:** the availability feature is ~70% built. The missing 30% is the only 30% that touches the live storefront. Fixing this is independent of all the automation work and should ship first.

---

## 2. Architecture decision — ERP is the source of truth (confirmed, don't fight it)

Your idea was to have the scraper push to Shopify directly and write a "last sent" note on the Shopify side that the ERP reads back. **Recommend against** — for a concrete reason found in the code, not just principle:

- The scraper's [C:/Users/keith/AxleForge/app/shopify_sync.py](file:///C:/Users/keith/AxleForge/app/shopify_sync.py) is **deliberately PARKED**. Its first line is literally `PARKED — NOT WIRED INTO THE APP` and the docstring says *"the scraper must NOT push to Shopify. Publishing moved to the ERP publish step."* That architectural call was already made, and it's the right one.
- A "note on Shopify, read back by the ERP" creates **two sources of truth** that can disagree (Shopify outage, theme migration, manual merchant edit, store swap → you lose history). The ERP already owns pricing rules, customer pricing, cost, SEO, the publish token, and the link GIDs.
- The clean version of your "last sent note" instinct is correct — it just belongs **on the ERP side** as a `shopify_synced_at` column, not round-tripped through Shopify. (See Phase 1.)

```
                        ┌─────────────────────────────────────────┐
  AxleForge scraper     │                  ERP                     │     Shopify
  (PAI html, IMB API,   │  (single source of truth + push engine)  │  (display layer only)
   turbos via Cadence)  │                                          │
        │  CSV drop      │   import → availability flags →           │   productUpdate(status)
        └──────────────► │   RECONCILE vs live → auto-SEO →          │ ◄── inventory SET + DENY
       exports/*.csv     │   DELTA push                             │     REST published:true
        (same machine,   └─────────────────────────────────────────┘
         local disk)
```

The scraper's job ends at "write an accurate CSV." The ERP's job is "make the storefront match the CSV."

---

## 3. What already exists (so we build the gap, not the whole thing)

| Capability | Status | Where |
|---|---|---|
| GraphQL `productSet` publish (idempotent on stored GID), default DRAFT | ✅ built | shopify_service.py `publish_product` |
| Partial safe update (price + SEO + tags, won't clobber manual edits) | ✅ built | `update_listing_fields` / POST `/shopify/update-batch` |
| Inventory absolute SET (`inventorySetOnHandQuantities`), batched ≤250 | ✅ built | `sync_inventory` |
| Connect-and-link by SKU → captures product/variant/inventoryItem GIDs | ✅ built | `match_and_link` / POST `/shopify/link-products` |
| Re-list to Online Store with no `write_publications` scope (REST `published:true`) | ✅ built | `.shopify-work/publish_rest.py` pattern |
| Vendor availability ingest (`in_stock`/`out_of_stock`/`discontinued` + synonyms) | ✅ built | `normalize_availability` / `_apply_availability_to_product` |
| Scraper emits `in_stock` + `status` columns (IMB qty from API, Backordered→0) | ✅ built | AxleForge `export_jaks.py` |
| AI SEO generator (Claude sonnet-4-6, forced tool-use, title+desc+meta) | ✅ built | `AIDescriptionService.suggest_for_product` |
| Nightly background sync scaffold (daemon thread, OFF by default) | ✅ built | main.py `_start_shopify_scheduler` |
| **Hide a live listing on OOS (`productUpdate status:DRAFT`)** | ❌ **MISSING** | — |
| **Re-list on back-in-stock (status:ACTIVE + published:true)** | ❌ **MISSING** | — |
| **Tracking + `inventoryPolicy:DENY` so qty=0 blocks purchase** | ❌ **MISSING** | — |
| **Delta / changed-only push + `shopify_synced_at`** | ❌ **MISSING** | — |
| **Auto-SEO on create/publish (generator is manual-only, no DB write-back)** | ❌ **MISSING** | — |
| **Scraper→ERP→push automation (today it's a manual CSV upload)** | ❌ **MISSING** | — |

**Token reality (verified):** the ERP token has `write_products` + `write_inventory` but **lacks `write_publications`**. That is enough to hide (`status:DRAFT` is under `write_products`); re-listing to the Online Store channel uses the REST `published:true` workaround already in use. No new scope strictly required to ship the fix.

---

## 4. The build — phased

### Phase 0 — STOP THE BLEEDING — ✅ DONE (built, tested, reviewed, live-migrated; uncommitted)
*Fixes the live oversell risk **today**, even before any automation. Decoupled from everything else.*

**Shipped:** `ShopifyService.reconcile_availability()` (hide via `productUpdate status:DRAFT`; re-list via REST `published:true`, gated on the new `shopify_hidden_by_erp` flag), `refresh_live_status()` (authoritative live-status read), wired into `sync_linked` (+ the nightly/sync-now worker), Alembic `0006` + `database.py` mirror + model column, `scripts/audit_shopify_availability.py` (dry-run default, `--refresh`, `--apply`), and `tests/test_shopify_reconcile_availability.py` (12 tests). **Prerequisite to act:** import availability data (see KEY FINDING above).


0.1 **`reconcile_availability()`** — new method in `ShopifyService`. For each **linked** product, compare ERP-desired vs live Shopify status:
   - desired-hidden (`vendor_availability` ∈ {out_of_stock, discontinued} OR `is_active=False`) **and** `shopify_status == ACTIVE` → `productUpdate(input:{id, status:DRAFT})`.
   - desired-live (`in_stock`, `is_active=True`) **and** `shopify_status == DRAFT` (and it was *us* that hid it) → `productUpdate(status:ACTIVE)` + REST `PUT products/{id}.json {status:active, published:true}`.
   - Stamp `product.shopify_status` after each change.

0.2 **Tracking + policy hardening** (belt-and-suspenders so qty truly gates buyability): when syncing a linked variant, ensure `inventoryItemUpdate(tracked:true)` + set `inventoryPolicy:DENY` via `productVariantsBulkUpdate`. Then qty=0 = unbuyable even if a listing slips through un-hidden.

0.3 **Wire into the existing manual sync** — call `reconcile_availability()` from `sync_linked()` so today's **"Sync now"** button immediately closes the gap. No new schedule required to get value.

0.4 **One-time audit + backfill** — scan all currently-live ACTIVE listings against current `vendor_availability` and DRAFT the ones already OOS. There are almost certainly live OOS listings right now (the one that sold was one). This is the immediate clean-up; run it once after 0.1 lands.

0.5 **Guardrail:** only ever auto-DRAFT/auto-ACTIVATE listings the ERP is linked to and (for re-list) that the ERP itself previously hid — never resurrect a listing a human manually drafted. Track this with a `shopify_hidden_by_erp` boolean (Phase 1 column) so re-list is safe.

**Deliverable:** the manual "Sync now" button correctly hides OOS and re-lists restocked parts. Oversell risk closed.

### Phase 1 — Delta engine + sync state (makes 20k feasible)
1.1 New Product columns (Alembic `0006`): `shopify_synced_at` (DateTime), `shopify_desired_status` (String), `shopify_hidden_by_erp` (Bool), and a `shopify_sync_hash` (price+qty+seo+status fingerprint).
1.2 `sync_linked` becomes **changed-only**: push a product only when its hash differs from last push. Turns a ~13k full re-push into "the few hundred that actually changed." This is the disciplined, ERP-side version of your "last sent note" idea.
1.3 For occasional full-catalog runs, add an optional `bulkOperationRunMutation` path (async JSONL, billed as one op — confirmed usable from our own token; the old "bulk blocked" note was the MCP tool surface only, not self-hosted scripts).

### Phase 2 — Auto-SEO — ✅ DONE (built, tested; uncommitted, ERP)
2.1 ✅ `AIDescriptionService.backfill_seo(product_ids=None, *, limit, dry_run, overwrite, progress)` — finds active products with blank `seo_description`, calls `suggest_for_product()`, and **writes the meta straight to `Product.seo_description`** (+ `seo_title` when blank). Commits per product (resumable); fail-soft per product; a missing key stops early. NEVER touches the customer-facing `title`/`description`.
2.2 ✅ `scripts/backfill_seo.py` = bulk catalog backfill (dry-run default; `--apply/--limit/--overwrite/--ids`; reports token cost). `import_and_push.py --seo` fills a section's missing SEO before the push (opt-in — off by default so the daily run stays cheap).
2.3 ✅ Pushed to Shopify as native `seo.description` by the existing `_build_listing()` path — no Shopify-side change.
2.4 ✅ Reconciled the key names: `ai_categorization_service.api_key()` falls back to `get_anthropic_api_key()` (the `anthropic_api_key_encrypted` the Settings page writes), so one key serves both.
**Status:** 6,114 of 30,931 active products need SEO. Owner runs `scripts/backfill_seo.py --apply` (needs `JAKS_FERNET_KEY` + the Anthropic key, both already configured). Auto-on-create for brand-new products = a small future follow-up (the engine is ready).

### Phase 3 — The one-click `.bat` bridge — ✅ DONE (built, tested, reviewed; uncommitted, BOTH repos)
*Turns "manual upload" into one double-click.* Bridges over **local disk + in-process Python** (no HTTP), sidestepping the session-login + CSRF wall.

**The load-bearing fix found here:** the scraper exported `in_stock`/`status` columns, but the ERP importer reads an `availability` column with `in_stock`/`out_of_stock`/`discontinued` values — **they never matched**, which is why `vendor_availability` was blank for all 30,931 products. Fixed by emitting the `availability` column.

**Shipped:**
- **Scraper (AxleForge):** `app/export_jaks.py` now emits `availability` via `_availability(status, discontinued_at, stock_qty, raw)` (discontinued→discontinued; explicit IMB/PAI label; real zero stock→out_of_stock; `not_available`→out_of_stock as the SAFE lean; auto-reverses). `scripts/scrape_section.py` = headless PAI section scrape (catalog partition, `--auto` rotates 1→4, advances **only on success**) + export. `app/turbo_consolidate.py` + `app/zeki_export.py` carry blank `availability` (curated turbos not auto-hidden).
- **ERP:** `scripts/import_and_push.py` = in-process bridge: `pricing_update_sell()` (availability + price, never creates) → `sync_linked(section_ids)` (reconcile + price/stock, scoped to the CSV). Dry-run default; `--apply`; `--reconcile-only`. Guards a missing `JAKS_FERNET_KEY` (encrypted-token `enc:` check).
- **`.bat`s (AxleForge):** `Scrape PAI Section + Push.bat` (daily one-click, auto-rotates, fully automatic), `Catch Up Shopify Now (no scrape).bat` (one-time backlog from existing data). Both load `JAKS_FERNET_KEY` from `%USERPROFILE%\.jaks_fernet.key`, use each repo's venv, abort the push if the scrape fails.

**Known limits (documented in the `.bat`):** PAI login is CAPTCHA-gated → needs a saved session; vanished-from-catalog parts aren't auto-retired daily (run the Weekly Checkup or `--sweep`); brand-new parts go to the ERP review queue, not the store; per-section push is not yet delta (Phase 1) so it re-pushes the section's price/stock each run (~minutes, unattended).
3.x **TODO:** fix the stale `SCRAPER_EXPORT_SPEC.md` (still says `in_stock` is "always 1") to document the new `availability` column contract.

### Phase 4 — Frequency & freshness tuning — ◑ PARTLY DONE (IMB fast lane built)
4.1 ✅ **IMB is the fast lane** (price API + auto-login, no CAPTCHA) → `scrape_imb.py` + `Refresh IMB + Push.bat` refresh the WHOLE IMB catalog unattended; run daily or more often. (10,297 IMB parts; 3,733 currently out-of-stock.)
4.2 **PAI is the slow lane** (HTML page scrape, CAPTCHA login) → the daily section `.bat` gives a ~4-day full-catalog cycle. The push after a scrape is minutes, but freshness is bounded by the scrape — PAI availability is NOT real-time (a known, inherent limit).
4.3 **Fast lane (open):** prioritize re-checking parts that recently sold or sit in open quotes/SOs.
4.4 **Token note:** the ERP custom-app token has `write_products`+`write_inventory` (enough for hide/re-list/stock) but NOT `read_locations` — `_location_id()` falls back to an id-only locations query. Adding `read_locations` to the app would also work but isn't required.

---

### Phase 5 — "Sold Out" state for top sellers (owner opted in)
*Instead of hiding a popular OOS part outright, keep its page live + indexed but block purchase — preserves SEO value on high-demand parts.*
5.1 Enable variant tracking (`inventoryItemUpdate tracked:true`) + set `inventoryPolicy:DENY` + qty 0 so the listing shows "Sold Out" but can't be bought (untracked variants are treated as infinitely available — must be tracked first).
5.2 A per-product or per-category opt-in (top sellers / recently-sold) chooses "sold-out badge" over "hide". Default stays **hide** (Phase 0); this is the curated exception.
5.3 Reuses the reconcile engine — just a third action ("mark sold out") alongside hide/re-list.

## 5. Recommended sequencing

1. **Phase 0** now — closes the active money leak, ships on its own, testable via the manual button + the one-time audit.
2. **Phase 1** next — delta + sync-state, so Phase 0 and everything after is cheap at 20k.
3. **Phase 2** — auto-SEO (independent; can run in parallel with Phase 1).
4. **Phase 3** — automation bridge (depends on 0–2 being solid; this is what makes it hands-off).
5. **Phase 4** — tune cadence once it's all flowing.

## 6. Open questions to confirm before Phase 3
- **Headless PAI scraping risk:** scheduling the full PAI scrape unattended raises bot-detection exposure. OK to start with scheduled **IMB** (API, safe) auto-sync and keep **PAI** as a one-click-then-auto-reconcile until we trust the headless run?
- **Re-list trust:** auto-republish only parts the ERP itself hid (safe), or also parts that were DRAFT for other reasons? (Recommend: only ERP-hid.)
- **"Hide completely" vs "sold out badge":** you chose hide (DRAFT). Phase 0.2 still adds the DENY safety net. Confirm you don't also want a visible "out of stock" state for SEO juice on popular parts.
```
