# Integration: PAI Portal Scraper

**Existing code:** `jaks_inventory/scraper/pai_scraper.py`, helpers in
`sources/pai_builder/`, enrichment in `sources/pai_builder/pai_enrichment.py`.

PAI Industries is JAK's primary REMAN supplier. The PAI customer portal at
`portal.pai.com` is the source of truth for cost, list, stock, OEM
cross-reference, warranty, and weight.

---

## Approach

Headless browser automation with **Playwright**, using a **persistent browser
context** so the operator logs in once and the session is reused.

### Why a persistent context
- PAI portal uses session cookies + occasional 2FA.
- Cookie persistence eliminates re-login per scrape.
- The browser profile lives at `SESSION_DIR` (configurable via env).
- A first-run wizard opens the browser, lets the operator log in, then the
  rest is automated.

### Stale cache recovery
If Playwright's user-data cache becomes corrupt (Chromium update,
crash mid-write), launch raises. `_clear_pai_stale_caches()` deletes specific
cache sub-folders (`Default/Cache`, `Default/Code Cache`, etc.) and retries
once. If the second launch fails, the operator must clear the whole profile
via the **Tools → Scraper Admin → Clear PAI Session** button.

---

## Entry points

```python
from jaks_inventory.scraper.pai_scraper import search_pai_portal, PAIPartResult

results: list[PAIPartResult] = await search_pai_portal(part_number)
```

`PAIPartResult` fields:

| Field | Type | Source |
|-------|------|--------|
| `sku` | str | PAI's part number |
| `description` | str | listing description |
| `your_price` | Decimal | logged-in customer price (cost to JAK) |
| `list_price` | Decimal | retail list |
| `oem_number` | str | crossreference to OEM |
| `product_group` | str | PAI's category (e.g. "Turbochargers") |
| `warranty` | str | warranty terms text |
| `weight` | Decimal | shipping weight |
| `upc` | str | barcode |
| `sell_pack` | int | qty per pack |
| `image_url` | str | primary image |
| `image_urls` | list[str] | all images |
| `detail_url` | str | deep link |
| `stock` | dict | per-warehouse availability map |
| `in_stock` | bool | aggregate |
| `total_available` | int | sum across warehouses |
| `not_available` | bool | hard "unavailable" flag |
| `alternate_sku` | str | PAI's recommended substitute from the "Alternate" panel (cross-reference) |
| `alternate_description` | str | alternate listing description |
| `alternate_your_price` | Decimal | alternate logged-in customer price |
| `alternate_in_stock` | bool | alternate aggregate availability |
| `alternate_url` | str | alternate deep link |

---

## Rate limits & politeness

- `RATE_LIMIT = 0.8` seconds between requests.
- One concurrent scrape at a time (single-flight lock).
- User-Agent identifies the app + contact email (configurable).
- Honors any 429 or temporary 503 with exponential backoff.

---

## Use cases inside the app

1. **Part Finder live search:** debounced 800 ms after the user stops typing.
2. **Product Workbench:** "Refresh from PAI" button on the Supplier section
   pulls cost / list / warranty / weight / OEM and writes them into the
   form (user reviews before save).
3. **HHP scraper enrichment phase:** for every HHP product detected as
   having a PAI equivalent (by OEM match), fetch PAI cost so the product is
   created with the right margin.
4. **Bulk PAI catalog ingest:** an admin can paste a list of OEM numbers
   or upload a CSV and the scraper enriches each row.
5. **Stale-cost background job:** nightly worker re-fetches PAI cost for
   SKUs with `pai_cost_updated_at > 30d`, writes new cost into
   `products.pai_cost` + updates `pai_cost_updated_at`. Flags the product
   for review if cost moved >5%.

---

## Logging

Every scrape attempt writes a `scrape_runs` row:
- `source = 'pai'`
- `started_at`, `finished_at`, `status` (success / partial / failed)
- `items_found`, `items_updated`
- `error_message` if any
- `triggered_by` (user_id, source: 'manual' / 'workbench' / 'hhp_enrich' / 'cron')

---

## CLI invocation

For debugging / batch operations:

```
python -m jaks_inventory.scraper.pai_scraper --search "S60 turbo"
python -m jaks_inventory.scraper.pai_scraper --sku "PAI-ABC123"
python -m jaks_inventory.scraper.pai_scraper --bulk skus.txt --out results.csv
```

---

## For a Base44 implementation

Base44 cannot run a headless browser directly. Options:

- **Option A — Sidecar service:** keep the PAI scraper as a Python sidecar
  exposing an HTTP API (`POST /pai/search` → results JSON). Base44 calls
  this endpoint from its server-side actions.
- **Option B — Operator-assisted upload:** the operator runs the scraper
  locally (CLI) and uploads result CSVs to Base44 via Bulk Import.

Option A is recommended. The sidecar is a thin FastAPI wrapper around
`search_pai_portal`. Auth: shared bearer token in env. Deploy on a small
VM with the persistent Chromium profile mounted as a volume.
