# Axle — Go-Live Data Load Runbook

**Created:** 2026-07-07 · **Owner:** Keith (jaksdiesel@gmail.com) · **Target DB:** `data/jaks.db` (live)

Goal: get real operating data into the ERP for go-live — products on the shelf, open AR, and
customer history — sourced the smart way (QBO for money/history, existing catalog for products,
a one-time count for physical stock). QBO stays the accounting book of record; the ERP loads a
**one-time historical snapshot** that is stamped `qbo_sync_status = SKIPPED` and therefore never
pushes back into QBO (no double-booking).

## Locked decisions (2026-07-07)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Physical stock model | **Stocked** — seed real on-hand for the ~225 items (~$68.6k). Build a CSV opening-count loader. |
| D2 | QBO history depth | **Open AR (all ages) + closed invoices from the last 12 months** for customer history. |
| D3 | Delivery method | **Run the proven scripts** — no new wizard UI. |
| D4 | Products from QBO? | **No.** Catalog already loaded (31,036 products, 30,384 active). QBO's ~225 items are a worse subset. |
| D5 | Numbering | Imported historical docs keep the `QBO-<ref>` prefix → no collision with the native `INV-YYYY-NNNN` counter. |

## Current reality (ground-truthed against code + live DB, 2026-07-07)

- **Products:** 31,036 total / 30,384 active / 28,621 priced / **0 on-hand**. Vendor-sourced, cross-referenced. Already "on the shelf" as a sellable catalog; on-hand=0 is correct for drop-ship, must be seeded for stocked items.
- **Customers:** 43 ERP records. 4 have a company-vs-person naming mismatch vs QBO (duplicate risk on push); 7 are junk/test to purge.
- **AR in ERP:** none — lives in QBO. QBO open AR (refreshed 2026-07-07) = **$70,383.74** across **6 customers** (The Mechanics LLC $44,946.87 · Manuel Colin $10,554.96 · Family Machine LLC $8,961.00 · AP Diesel Services $2,750.10 · Inline 6 Diesel Repair $1,631.95 · UZB Truck Trailer Repair $1,538.86). The Mechanics carries a −$0.04 credit that must be carried to reconcile to the penny.
- **AP in ERP:** none — QBO open bills (refreshed 2026-07-07) = **$62,970.34** across **8 bills** (Sampa USA LLC, Central Turbos, Diesel Fuel Technologies, Migao). **Sampa USA LLC is not one of the 5 configured vendors** — importer will create it.
- **QBO integration:** push-only (ERP→QBO), sandbox-connected + preflight-green. History importer (`scripts/qbo_history_import.py`) proven to the penny into `data/jaks_sandbox.db`, but **hard-gated against the production realm**.
- **Vendors:** 5 configured with SKU digits (PAI=9, IMB=3, DFT=1, MIG=2, CTT=4).

## What I build (small, reversible) — ✅ BUILT + DRY-RUN VERIFIED 2026-07-07

1. **`scripts/opening_inventory_import.py`** ✅ — CSV opening-count loader for D1. Columns: `sku` (falls back to vendor part #/vendor SKU), `qty_on_hand`, `unit_cost` (blank → preferred `ProductVendorSource.vendor_cost`). Applies each row through `InventoryService.adjust_inventory` (reason = `initial_inventory_load`) so `qty_on_hand`, moving-avg `cost`, ledger, and audit are all consistent. Dry-run default; `--apply` commits; idempotent (delta = counted − current, so re-runs are no-ops); unknown SKUs reported, never created. Sample sheet: `data/opening_counts.sample.csv`.
2. **Generalize `scripts/qbo_history_import.py`** ✅ — added `--live` (targets `data/jaks.db`; a live `--apply` also requires `--confirm "LOAD QBO HISTORY INTO LIVE"`), `--since YYYY-MM-DD` (floors CLOSED history; **open A/R always imports regardless of age**), a `customer_credits_raw.json` pass (nets unapplied overpayments to the penny), a vendor-alias map (QBO legal names → existing ERP vendors), and PASS/FAIL reconciliation vs. today's targets. Keeps: dry-run default, `SKIPPED` stamping, `QBO-` prefix, idempotency, `--purge`.
3. **Expose company tax fields** ✅ — added `default_sales_tax_rate` + `company_tax_jurisdiction` to the Settings form `VISIBLE_KEYS`.

### Dry-run results (live `jaks.db`, rolled back, 2026-07-07)
- **A/R $70,383.74 [PASS] · grand total $478,000.62 [PASS] · 20 open invoices [PASS] · A/P $62,970.34 [PASS]** — all to the penny.
- Customers: 24 matched existing + **1 new (Salina Transport)**; 1 account credit ($0.04, The Mechanics) applied.
- Vendors: only **Sampa USA LLC** created (DFT + Migao aliased onto existing rows).
- Product crosswalk: 20 of 128 QBO items auto-matched; **108 need owner review** (`data/qbo_staging/crosswalk_review.csv`) — informational; invoices still carry full line descriptions for history.
- Opening loader: verified on a 3-SKU test — explicit cost, blank→vendor-cost fallback, and override all correct; unknown SKU flagged.
- **Schema note:** the dry-run applied a pending `products.bin_location` migration to `jaks.db` (auto-backup `backups/jaks-premigration-20260707-083410.db`). A normal app restart applies the same; the live DB schema is now current.

## What you do (owner actions)

- Confirm `JAKS_FERNET_KEY` is exported before any QBO-touching step (key at `C:\Users\keith\.jaks_fernet.key`).
- Provide the **physical count sheet** for the ~225 stocked items (sku, qty, unit cost).
- Approve each **"apply to live"** gate after reviewing the dry-run reconciliation numbers.
- (Recommended before operating live, not before loading) clear the 4 pre-trial HIGH bugs from the 07-05 audit: inert quote Disc%, SO deposit double-book, no payment-dedupe guard, and set FERNET before connecting live QBO.

## Execution sequence

### Phase 0 — Pre-flight (safety)
- [ ] Export `JAKS_FERNET_KEY`.
- [ ] Fresh backup: copy `data/jaks.db` → `data/backups/jaks-pre-golive-load-<ts>.db`.
- [ ] Confirm live DB identity (31,036 products, counters at 0001).

### Phase 1 — Company & foundations (manual, ~30 min)
- [ ] Settings → company name / address / phone / email / website.
- [ ] Settings → sales-tax rate + jurisdiction (after I expose the fields).
- [ ] Verify the 5 vendor digits under Inventory → Vendors.

### Phase 2 — Customers + AR + history (QBO one-time) — ✅ DONE 2026-07-07
- [x] Refreshed QBO staging to 2026-07-07 (production book).
- [x] **Applied to live** — backup `data/backups/jaks-pre-qbo-history-apply-20260707-084555.db`.
- [x] **Reconciled to the penny (post-commit):** 129 invoices / 20 open / A/R **$70,383.74**; A/P **$62,970.34** (8 bills); +1 customer (Salina Transport), +1 vendor (Sampa USA LLC); The Mechanics nets $44,946.87 across 31 invoices.
- [ ] OPEN (informational): 108 of 128 QBO items need crosswalk review → `data/qbo_staging/crosswalk_review.csv` (invoices still carry full line descriptions; product-link only).
- [ ] Restart the live app so the Settings tax-field change (code) and the loaded data are all live.

### Phase 3 — Products on the shelf (opening on-hand)
- [ ] Receive count sheet → `data/opening_counts.csv`.
- [ ] **Dry-run:** `python -m scripts.opening_inventory_import data/opening_counts.csv`
- [ ] **GATE:** total on-hand valuation ≈ expected (~$68.6k); every SKU matched.
- [ ] **Apply:** add `--apply`.

### Phase 4 — Verify & cut over
- [ ] Spot-check 3 customers: open balance + recent-invoice history render correctly.
- [ ] Spot-check 3 stocked SKUs: qty_available + cost render correctly.
- [ ] Run one full quote → SO → invoice → payment → QBO push **in sandbox**.
- [ ] Close the backup + tested-restore drill (open go-live gate O3).
- [ ] At production cutover: run the customer `qbo_customer_id` binding SQL for the 4 dup-risk accounts (from `QBO_SHOPIFY_RECONCILE_FINDINGS_2026-07-02.md`).

## Rollback

Every apply step is preceded by a backup and is idempotent/purgeable:
- History import: `--purge` removes all `QBO_HISTORY_IMPORT`-tagged rows.
- Opening inventory: reverse via offsetting adjustment, or restore the pre-load backup.
- Nothing in Phases 2–3 pushes to QBO (all `SKIPPED`), so QBO is untouched throughout.
