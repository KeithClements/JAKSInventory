# Integration: ATL Diesel Scraper

**Existing code:** `sources/atl_diesel.py`

ATL Diesel (atldiesel.com) is a Shopify-based competitor. We scrape for
competitive pricing + product copy + Frequently Bought Together graph.

---

## Approach

`aiohttp` + `BeautifulSoup`, no browser.

### Constants
```python
ATL_BASE_URL    = "https://atldiesel.com"
ATL_SEARCH_URL  = "https://atldiesel.com/search?q={query}"
ATL_RATE_LIMIT  = 1.5    # seconds between requests
ATL_TIMEOUT     = 30
ATL_MAX_IMAGES  = 10
```

### Browser-impersonation headers (anti-403)

Shopify's bot mitigation will return 403 on requests from "obvious" scrapers
(default `python-requests` User-Agent, missing Accept-Language, etc.).
The scraper sends a complete browser header set:

```python
ATL_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
    "Accept": "text/html,application/xhtml+xml,...",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    # etc.
}
```

These headers are mandatory; without them ATL frequently blocks requests.

---

## What we extract

Per product:
- Title, SKU, price, compare-at price
- Description (HTML)
- All product images (up to `ATL_MAX_IMAGES`)
- Specs table
- **Frequently Bought Together** (Shopify metafield rendered in DOM)
- **Related Products**
- Vendor / manufacturer
- Tags / collections

These flow into the same downstream phases used by HHP scraper (PAI Enrich →
Review → Upload). FBT and Related are stored in `suggested_sells` so the
quote / SO dialogs can surface them automatically.

---

## Entry points

```python
from sources.atl_diesel import search_atl, fetch_atl_product

# search returns light results
hits = await search_atl("s60 injector")

# detail fetch
detail = await fetch_atl_product(hits[0]["url"])
```

---

## Rate limits & politeness

- `ATL_RATE_LIMIT = 1.5 s` per request, jittered.
- Single concurrent request per session.
- Backoff on 429/503: 1 → 3 → 9 → 27 s, then abort.
- Aborted runs surface in Scraper Admin with the failing URL.

---

## Use cases

1. **Part Finder live search** (lower priority than PAI / HHP because ATL is
   strictly competitive intel).
2. **Competitive pricing watch** — nightly check of ATL prices for SKUs with
   known `atl_url`, store delta vs our list price.
3. **Suggested sells discovery** — ATL's FBT graph is a useful seed for our
   own suggested-sells data. Importing this is operator-approved per product.

---

## For a Base44 implementation

Same sidecar pattern as PAI and HHP:
- `POST /atl/search?q=...`
- `GET  /atl/product?url=...`
- `POST /atl/scan` — batch competitive price check for our known atl_url's.

ATL specifically requires the browser headers — if Base44's built-in HTTP
client tries to call atldiesel.com directly without these headers, it will
get 403s. Always route through the sidecar.
