# Scrapers Engine

Three sources: **PAI** (supplier; cost truth for PAI SKUs), **HHP**
(competitor + cross-ref), **ATL** (competitor + cross-ref).

All scrapers run as **rate-limited workers** consuming a single
`scrape_queue`. No nightly full re-scans (per B3). Event-driven only.

## Shared queue

```sql
CREATE TABLE scrape_queue (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,           -- 'pai' / 'hhp' / 'atl'
    operation       TEXT NOT NULL,           -- 'search','fetch_product','refresh_cost','price_check'
    payload         JSONB NOT NULL,
    priority        INT NOT NULL DEFAULT 5,  -- higher = sooner
    triggered_by    TEXT NOT NULL,           -- 'workbench_open','quote_line','manual','market_change',...
    related_type    TEXT,
    related_id      BIGINT,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    result          JSONB,
    last_error      TEXT
);

CREATE INDEX idx_scrape_queue_ready
    ON scrape_queue (source, status, priority DESC, next_attempt_at);

CREATE TABLE scrape_runs (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    operation       TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT,                    -- success / partial / failed
    items_found     INT DEFAULT 0,
    items_updated   INT DEFAULT 0,
    error_message   TEXT,
    triggered_by    TEXT
);
```

Each source has its own worker process with its own rate limit. Workers
pull from `scrape_queue WHERE source = self.source AND status='pending'
AND next_attempt_at <= now()` ordered by `priority DESC, queued_at`.

---

## PAI scraper

**Authoritative source for PAI SKU costs.** Headless browser
(Playwright) with **persistent context** so login persists.

### Constraints
- Persistent Chromium profile directory (env: `PAI_PROFILE_DIR`).
- One concurrent request, rate limit **0.8 s** between fetches.
- Stale-cache recovery: clear specific cache subdirs on launch failure;
  retry once.
- Operator log-in is manual one-time setup (kicks off a `pai_login` job
  that opens the browser headed).

### Operations
- `pai.search(query)` → list of `PAIPartResult`
- `pai.fetch_product(pai_sku)` → single `PAIPartResult`
- `pai.refresh_cost(product_id)` → fetch + write to `products` or
  `pending_cost_changes` per cost-source rule

### PAIPartResult shape
```python
@dataclass
class PAIPartResult:
    sku: str
    description: str
    your_price: Decimal          # cost
    list_price: Decimal
    oem_number: str
    product_group: str
    warranty: str
    weight: Decimal
    upc: str
    sell_pack: int
    image_url: str
    image_urls: list[str]
    detail_url: str
    stock: dict                  # {warehouse_code: qty}
    in_stock: bool
    total_available: int
    not_available: bool
```

### Triggers that enqueue PAI work
| Event | Operation | Priority |
|-------|-----------|----------|
| `product.opened_in_workbench` (cooldown 1h) | refresh_cost | 9 |
| `quote_line.added` (cooldown 1h) | refresh_cost | 8 |
| `manual.refresh` | refresh_cost | 10 |
| `po.line_under_review` | refresh_cost | 7 |
| `margin.warning` | refresh_cost | 7 |
| `staleness.threshold_exceeded` | refresh_cost | 3 |
| `market_change_event` fanout | refresh_cost (per SKU in scope) | 5 |
| New product creation (Path B/C/E) | fetch_product | 9 |

---

## HHP scraper

HTTP-only (`httpx` + `BeautifulSoup`), no browser.

### Constraints
- Rate limit **2.0 s** between requests, ±20% jitter.
- Uses Advanced Woo Search Pro endpoint `/wp-admin/admin-ajax.php` for
  search (fast JSON), product page scrape for detail.
- User-Agent identifies the app.

### Operations
- `hhp.search(query)` → quick search results
- `hhp.fetch_product(url)` → full detail: description, images, OEM xrefs,
  specs, related, FBT
- `hhp.price_check(product_id)` → quick price re-check for known `hhp_url`

### Triggers
| Event | Operation | Priority |
|-------|-----------|----------|
| Part Finder live search | search | 9 |
| `product.from_hhp` creation | fetch_product | 9 |
| Weekly competitive watch | price_check | 2 |
| Manual refresh | price_check | 10 |

### Outputs flowing back
- `hhp.price_check` writes `products.hhp_price`, `hhp_last_check`, and
  appends `(date, price)` to `hhp_price_history` (last 24 entries).
- If `hhp_price < products.selling_price` → insert `competitive_alerts` row,
  emit `competitor.undercut`.

---

## ATL scraper

`aiohttp` + `BeautifulSoup`. Shopify storefront — **must send browser
headers** to avoid 403.

### Constraints
- Rate limit **1.5 s**.
- `ATL_BROWSER_HEADERS` dict: full UA, Accept, Accept-Language,
  Accept-Encoding, Sec-Fetch-* — copied from a real Chrome session.
- Max 10 images per product (`ATL_MAX_IMAGES`).

### Operations
- `atl.search(query)`
- `atl.fetch_product(url)` → detail + FBT + related
- `atl.price_check(product_id)`

### Triggers
Same matrix as HHP, with one addition: FBT graph from ATL product detail
optionally feeds into `suggested_sells` (gated by an operator-approval
queue, not auto-applied).

---

## Worker shape (per source)

```python
class ScrapeWorker:
    source: str             # 'pai' / 'hhp' / 'atl'
    rate_limit_seconds: float
    max_concurrent = 1

    async def loop(self):
        while True:
            job = await self.next_job()         # FOR UPDATE SKIP LOCKED
            if not job:
                await sleep(5)
                continue
            try:
                result = await self.dispatch(job)
                self.mark_done(job, result)
                emit(f"{self.source}.completed", {...})
            except RateLimitedError:
                self.requeue(job, delay=60)
            except FatalError as e:
                self.fail(job, str(e))
            await sleep(self.rate_limit_seconds)
```

## Cooldowns (avoid spammy refreshes)

Per `(source, product_id, operation)` keep a `scrape_cooldowns` row:

```sql
CREATE TABLE scrape_cooldowns (
    source          TEXT NOT NULL,
    product_id      BIGINT NOT NULL,
    operation       TEXT NOT NULL,
    last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, product_id, operation)
);
```

Default cooldown: 1 hour per (product, operation). Override at enqueue time
for high-priority events (`manual.refresh` ignores cooldown).

## Backoff on failure

Per-source backoff: `30s → 2m → 10m → 30m → 2h → fail`.

429 / 503 responses count as transient; 4xx (other) and parse errors mark
the job permanently failed and emit `scrape.parse_error` for investigation.

## API surface

```
GET    /scrape/queue?source=...&status=...
POST   /scrape/queue                    -- ad-hoc enqueue
POST   /scrape/queue/{id}/retry
POST   /scrape/queue/{id}/cancel
GET    /scrape/runs?source=...

POST   /scrape/pai/login                -- opens headed browser for login
POST   /scrape/pai/refresh-session      -- clears stale cache + relaunches

POST   /products/{id}/refresh-cost      -- shortcut: enqueue PAI refresh_cost
POST   /products/{id}/price-check       -- enqueue HHP + ATL price_check
```

## Hosting note (on-prem)

Since deployment is on-prem with outbound internet only:
- Workers run as systemd services (or Windows services) alongside the API.
- PAI persistent profile lives on local disk (back it up).
- No public endpoints required for scraper operation.
