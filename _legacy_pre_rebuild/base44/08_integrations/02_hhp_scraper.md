# Integration: HHP (Highway & Heavy Parts) Scraper

**Existing code:** `sources/hhp_scraper.py`, bridge
`jaks_inventory/scraper/hhp_bridge.py`, pipeline `phases/*`.

Highway & Heavy Parts is a primary competitor. We scrape their catalog to:
- Discover new parts we don't yet carry.
- Track competitive pricing.
- Pull product copy and images as a starting point for our own listings.

---

## Approach

HTTP-only scraping (no browser) using `httpx` + `BeautifulSoup`.

### Constants
```python
BASE_URL = "https://www.highwayandheavyparts.com"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"  # Advanced Woo Search Pro
REQUEST_DELAY = 2.0   # seconds between requests
```

### Why admin-ajax.php
HHP runs on WooCommerce with the **Advanced Woo Search Pro** plugin. Its
search endpoint accepts a POST with `action=aws_action&keyword=...` and
returns a JSON payload with products including price, image, link, and
out-of-stock flag. This is *much* faster than scraping search result pages.

For product detail (full description, all images, related products), we
fetch the product URL HTML and parse with BeautifulSoup.

---

## 5-Phase pipeline

Implemented in `phases/`. The HHP bridge orchestrates these in order:

### Phase 1 — Scan
- Iterate the configured set of HHP categories
  (`core.constants.HHP_MAIN_CATEGORIES`).
- For each category page, paginate and collect every product URL +
  basic data (title, list price, image, in-stock flag).
- Output: `phases/output/scan_<runId>.json`

### Phase 2 — Scrape (detail)
- For each URL discovered: GET, parse, extract:
  - Full description (HTML)
  - All image URLs
  - Specifications table
  - OEM cross-reference numbers
  - Related products
  - Frequently Bought Together
- Output: `phases/output/scrape_<runId>.json`

### Phase 3 — PAI Enrich
- For each scraped item, attempt to find a PAI equivalent via OEM matching.
- If matched, fetch PAI cost so the new product has the right cost basis.
- Output: same JSON, augmented with `pai_match` block.

### Phase 4 — Review (human-in-the-loop)
- UI presents the enriched results in a table.
- Reviewer can:
  - Accept (will be created or updated)
  - Edit (override any field before commit)
  - Reject (skipped, recorded in `scrape_runs.skipped`)
- Output: a frozen "approved" set.

### Phase 5 — Upload
- For each approved row, either:
  - Create a new product (if SKU not in `products`)
  - Update an existing product's `hhp_url`, `hhp_price`, `hhp_price_history`
- Write a `scrape_runs` row with totals.

The pipeline is resumable: each phase writes its output to disk and the
next phase reads from it.

---

## Price history tracking

The `products` table carries:
- `hhp_url` — canonical link
- `hhp_price` — last observed competitor price
- `hhp_price_history` — JSON array `[{date, price}, ...]` (last 24 entries)
- `hhp_last_price_check` — timestamp

A scheduled nightly job re-checks the HHP price for every product with a
known `hhp_url` and appends to history if changed.

---

## Rate limits & politeness

- `REQUEST_DELAY = 2.0 s` between requests, jittered ±20%.
- Single-flight per run.
- Respect `robots.txt` directives where present.
- User-Agent identifies the app.
- Backoff on 429/503.

---

## Sample search call

```python
import httpx

resp = httpx.post(
    "https://www.highwayandheavyparts.com/wp-admin/admin-ajax.php",
    data={"action": "aws_action", "keyword": "s60 turbo"},
    headers={"User-Agent": "JAKsDieselPRO/1.0"},
    timeout=30,
)
data = resp.json()
# data["data"] = [{"title": ..., "price": "$1,234.00", "permalink": ..., ...}, ...]
```

---

## For a Base44 implementation

Same pattern as PAI: run the scraper as a **Python sidecar** with HTTP
endpoints:
- `POST /hhp/search?q=...`
- `POST /hhp/scan` → starts a full pipeline run, returns run_id
- `GET /hhp/runs/{id}` → status & progress
- `GET /hhp/runs/{id}/review` → reviewable rows
- `POST /hhp/runs/{id}/approve` → commit selected rows back into Base44 via
  its REST API.

Pipeline JSON outputs sit on the sidecar's disk; review happens through
Base44 calling the sidecar API and posting back the curated set.
