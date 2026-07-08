# JAK's Diesel ERP — Scraper Export Requirements

**Audience:** the PAI Info scraper at `C:\Users\keith\PAI Info` (separate repo) — whoever
maintains the Shopify-CSV export.
**Consumed by:** *Products → Import → Pricing Update → Sell price (scraper Shopify export)*
in the ERP.
**As of:** 2026-06-11.

## TL;DR

The ERP currently consumes your `exports/pai_shopify_*.csv` files but only reads sell
price + compare-at price. **Two new columns** would let the same single CSV refresh
**Our Cost** and the **Manufacturer** dropdown for all 13k products in one pass — turning
the existing $0/manual cost field into a real number, and lighting up the manufacturer
filter the counter uses every day.

Adds to your Shopify-format export (everything else unchanged):

| Column header (exact) | Source while scraping        | Example value     | What the ERP does with it                              |
|-----------------------|------------------------------|-------------------|--------------------------------------------------------|
| `Our Cost`            | Authenticated dealer price   | `4.20`            | Writes `ProductVendorSource.vendor_cost` (PAI vendor) + cost-history row |
| `Manufacturer`        | Engine make for the part     | `Cummins`         | Writes `Product.manufacturer` (canonicalized)          |
| `availability`        | Vendor portal stock state    | `out_of_stock`    | Writes per-vendor availability; **auto-hides OOS parts from the storefront push, auto-deactivates discontinued parts** |
| `change_type`         | Your own delta bookkeeping   | `price`           | Optional / ignored by the ERP — lets you ship *delta-only* files (see below) |

All four columns are **optional** — the ERP only updates each field when its column is
present (and non-blank) in the row, so you can roll them in independently. Any column
the ERP doesn't recognize is silently ignored, so extra bookkeeping columns are safe.

> **Adding NEW products in the same file:** the *Pricing Update* import never creates
> products. To add new parts AND refresh price/cost/availability on existing ones in a
> single pass, use **Full Product Import** — it creates missing SKUs and updates the
> rest. So one combined export (new + changed + availability) goes through Full Import;
> a price/availability-only refresh of the existing catalog goes through Pricing Update.

---

## Column 1: `Our Cost`

> **Two-vendor files (2026-06-11):** one export may now mix PAI and
> Interstate-McBee rows, distinguished by the Variant SKU prefix —
> `JAKS-PAI-<part#>` vs `JAKS-IMB-<part#>`. `Our Cost` means the same thing
> on every row: the authenticated dealer price from THAT vendor's portal
> (scraper column `parts.your_cost`). The ERP resolves the vendor from the
> SKU prefix and writes the cost to that vendor's source on the product —
> a JAKS-XYZ- prefix with no matching vendor record is counted
> (`skipped_no_xyz_source`), never re-routed to another vendor.

### Where to get it
The vendor's portal shows the dealer cost only when logged in (the same login the
scraper already uses for everything else). On the part page it's labeled "Dealer",
"Net", or just shown next to a strikethrough MSRP. Whatever number drives your
gross-margin column (`parts.your_cost`) is the right number — for PAI rows it's
the PAI dealer price, for IMB rows the Interstate-McBee dealer price.

### How to emit it
- Header: literally `Our Cost` (with a space) or `pai_cost` — the ERP accepts either,
  plus `vendor_cost`, `dealer_cost`, `net_cost`, and `cost` as fallbacks.
- Value: plain decimal, no `$`, no thousands separator. Example: `4.20`, `127.55`.
- Blank cell = "don't touch the cost for this row" (not "set cost to 0"). The ERP
  will leave the existing `vendor_cost` alone.
- **Image-only follow-up rows must stay blank** — the ERP treats blank + blank-SKU
  rows as image rows and silently skips them.

### What the ERP will do
- For each row with `Our Cost > 0`, resolve the vendor from the SKU prefix
  (`JAKS-PAI-` → PAI, `JAKS-IMB-` → Interstate-McBee; no prefix = legacy PAI),
  find THAT vendor's `ProductVendorSource` on the product and update
  `vendor_cost` + write a `ProductCostHistory` row (`notes = "Scraper refresh
  (<VENDOR> cost)"`).
- Products without that vendor's source are counted under
  `skipped_no_vendor_source` plus a per-vendor labeled key
  (`skipped_no_pai_source` / `skipped_no_imb_source`) so PAI gaps and IMB gaps
  are distinguishable in the result panel — never silently dropped.
- `product.cost` (moving-average COGS) is **not** touched. That field only changes on
  PO receipts. The vendor_cost feeds future receipt cost via the preferred-vendor
  seam, which is the correct accounting separation.

### Why this matters
Right now every PAI product opens with **Our Cost = $0 (source: manual)**. Margin
reports show 100% on everything, the dashboard's margin column is meaningless, and
gross-margin guards on quotes can't fire. One scraper-side column closes all of those
in one run.

---

## Column 2: `Manufacturer`

### Where to get it
The engine make for the part. PAI's catalog already organizes parts by engine
platform — for the head bolt kit on Cummins ISX it's "Cummins", for a Cat 3406 piston
it's "Caterpillar". On any PAI product page you should be able to read this from:

1. The page's breadcrumb or product family tag (most reliable), OR
2. The Type column you already emit (when it carries the make), OR
3. The Applications block in the Body HTML — first engine make listed.

If a part fits multiple makes (universal gasket, hardware), leave the cell blank —
don't pick one.

### How to emit it
- Header: literally `Manufacturer` (matches the ERP's product-edit dropdown label).
  `Engine Make`, `engine_make`, `engine_manufacturer`, and `Make` are also accepted.
- Value: the engine make as a single word/phrase. The ERP canonicalizes case
  ("CUMMINS" / "cummins" / "Cummins" all land as `Cummins`).
- The ERP's canonical list (the dropdown values) is:
  - `Cummins`
  - `Caterpillar`
  - `Detroit Diesel`
  - `Mack`
  - `Volvo`
  - `International`
- Anything outside that list (e.g. `Paccar`, `Navistar`, `Isuzu`) is **stored
  verbatim** and surfaced in the import summary's `manufacturer_unmapped_sample`,
  so the owner can decide whether to add it to the canonical list.
- Blank cell = "don't touch the manufacturer for this row" (not "clear it").

### What the ERP will do
- Set `Product.manufacturer` to the canonical form, lighting up the dropdown on the
  product edit page and the manufacturer filter on the products list.
- Count unmapped values; the owner sees them in the result panel and decides whether
  to expand the canonical list (one constant in [products.py:36](app/routers/products.py:36)).
- If the value already equals the canonical, it's reported as `unchanged` and skipped
  — fully idempotent.

### Why this matters
The product detail page already has a Manufacturer dropdown wired to a 6-make list,
but it shows "— Select —" on every PAI part because the field was never populated.
The counter's daily "Cummins parts in stock for an ISX job" lookup is broken at the
catalog level until this column exists.

---

## Column 3: `availability`

The vendor's supply state for the part — **not** our stock (we don't carry these;
they're special-order). This is the signal that a part can or can't be sourced.

### Where to get it
The authenticated vendor portal shows it on the part page: "In Stock", "Out of
Stock / Backordered", or "Discontinued / No Longer Available". Use whatever field
drives your own buy/no-buy decision.

### How to emit it
- Header: literally `availability` (also accepted: `vendor_availability`,
  `stock_status`, `stock`).
- Value: free text — the ERP normalizes it. Recognized buckets:
  - **in stock** → `in_stock`, `in stock`, `available`, `yes`, `stock`
  - **out of stock** → `out_of_stock`, `out of stock`, `oos`, `backorder`,
    `backordered`, `unavailable`, `sold out`
  - **discontinued** → `discontinued`, `disc`, `obsolete`, `nla`, `eol`,
    `no longer available`
- Blank cell or any unrecognized word = **"don't touch"** — the ERP leaves the
  stored status alone. It never guesses.

### What the ERP does (owner-locked policy: full automation)
- **`out_of_stock`** → the product is **hidden from the storefront push**
  (excluded from the Shopify bulk sync/publish via a cached flag). Nothing is
  deleted; when a later feed reports it `in_stock` again, it returns to the push
  automatically.
- **`discontinued`** → the product is **deactivated** (`is_active=false`,
  status `discontinued`). It drops out of the catalog/storefront feed. This only
  fires when *every* active vendor source for the product is discontinued.
- **back `in_stock` after being discontinued/deactivated** → the ERP does **not**
  silently re-activate it (that's the dangerous direction). It flags the product
  **needs review** so the owner reactivates it on purpose.
- Result counts appear in the import summary: `availability_updated`,
  `out_of_stock_flagged`, `discontinued_deactivated`, `reactivation_suggested`.

> ⚠️ Because `discontinued` deactivates, only send it when the vendor truly drops
> the part. If you'd rather the ERP auto-detect a part that simply *vanished* from
> the feed, you must send a **full** catalog sweep (not a delta) — a missing row is
> not the same as an explicit `discontinued`, and the ERP never deactivates on
> absence.

---

## Delta exports & `change_type` (export only the changes)

You don't have to re-send the whole 13k-row catalog every run. The ERP import is
**idempotent** — a row whose price/cost/availability already matches is reported as
`unchanged` and nothing is written — so a smaller "only what changed since last run"
file is safe and faster.

- Keep your own snapshot of the last export and diff against it; emit a row only
  when something moved (price, cost, availability) or the part is new.
- Optionally tag each row with a `change_type` column (`new` / `price` / `cost` /
  `availability` / `discontinued`) for your own bookkeeping. **The ERP ignores it**
  and acts on the row's actual contents, so it's purely informational.
- One caveat (repeated from above): a delta file can't tell the ERP a part was
  *silently* discontinued — you must send an explicit `availability=discontinued`
  row for that part, or run a periodic full sweep.

Recommended cadence: a light **delta** (price + availability) frequently, plus an
occasional **full sweep** through Full Product Import to catch new parts and
reconcile discontinuations.

---

## Worked example — same CSV, two new columns

A current scraper row (truncated to the relevant columns):

```
Handle,...,Variant SKU,...,Variant Price,Variant Compare At Price,...
jaks-pai-040049,...,JAKS-PAI-040049,...,2.90,5.86,...
```

After adding the two columns (append at the end is fine — header order doesn't matter):

```
Handle,...,Variant SKU,...,Variant Price,Variant Compare At Price,...,Our Cost,Manufacturer
jaks-pai-040049,...,JAKS-PAI-040049,...,2.90,5.86,...,1.65,Caterpillar
```

That single row now refreshes **four** ERP fields on commit:

| ERP field                              | New value     |
|----------------------------------------|---------------|
| `Product.price_override`               | `2.90`        |
| `Product.compare_at_price`             | `5.86`        |
| `ProductVendorSource.vendor_cost` (PAI)| `1.65`        |
| `Product.manufacturer`                 | `Caterpillar` |

Multi-image follow-up rows keep working unchanged (blank everywhere except `Handle`,
`Image Src`, `Image Position`) — the ERP counts them under `image_rows_skipped`.

---

## What the owner sees after a commit

The import result panel adds five new fields when these columns are present:

```
mode: pricing_update     source: sell
rows: 38421              image_rows_skipped: 25216
matched: 13205           prices_updated: 117
compare_updated: 9       costs_updated: 11982        ← new
manufacturer_updated: 12647                          ← new
skipped_no_pai_source: 6                             ← new
manufacturer_unmapped_sample: [{sku, manufacturer}]  ← new (rare)
availability_updated: 213     out_of_stock_flagged: 188     ← new
discontinued_deactivated: 25  reactivation_suggested: 0     ← new
unchanged: 1077          over_threshold_skipped: 2
```

The 50% threshold rail still protects against bad-scrape blowouts on the sell-price
side; cost and manufacturer writes are independent of the threshold (a bad scrape of
one shouldn't poison the others), so the rail won't suppress those.

---

## Acceptance checklist for the scraper side

- [ ] `Our Cost` column appended to the standard Shopify export
- [ ] `Manufacturer` column appended to the standard Shopify export
- [ ] `availability` column appended (in_stock / out_of_stock / discontinued)
- [ ] (optional) delta export wired — only changed/new rows, with `change_type` tag
- [ ] Confirmed: a discontinued part is sent as an explicit `availability=discontinued`
      row (not just dropped from the file), OR a periodic full sweep is scheduled
- [ ] Dealer cost extracted from the authenticated PAI page (matches the price you'd
      pay an invoice at)
- [ ] Manufacturer detected from the part's engine platform (not from the part's
      brand, which is always "JAKS" / "PAI" on the catalog)
- [ ] Image-only follow-up rows keep these two columns blank (current behavior)
- [ ] One smoke run against the live ERP at *Products → Import → Pricing Update →
      Sell price (scraper Shopify export)*, dry-run first, confirms the summary
      reports `costs_updated > 0` and `manufacturer_updated > 0`

---

## ERP-side implementation reference

- Service: [`ProductImportService.pricing_update_sell`](app/services/product_import_service.py)
- Route: `POST /products/import-run` with `mode=pricing&source_type=sell`
- Tests: [`tests/test_r3_pricing_update_sell.py`](tests/test_r3_pricing_update_sell.py)
- Canonical manufacturer list: [`app/routers/products.py:36`](app/routers/products.py:36)
  (modify this constant + restart to add a new make to the dropdown)
