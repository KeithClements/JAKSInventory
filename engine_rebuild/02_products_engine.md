# Products Engine

Owns: catalog, cross-reference, cost, pricing, qty-on-hand. The single
canonical store of "what we sell, what it costs, what it's worth".

---

## Tables

```sql
CREATE TABLE products (
    id                      BIGSERIAL PRIMARY KEY,
    sku                     TEXT UNIQUE NOT NULL,
    title                   TEXT NOT NULL,
    condition               TEXT NOT NULL,           -- NEW / REMAN / USED / CORE / KIT
    manufacturer            TEXT,
    category                TEXT,
    subcategory             TEXT,

    -- Cost
    cost                    NUMERIC(12,4) NOT NULL DEFAULT 0,
    cost_source             TEXT NOT NULL DEFAULT 'manual',   -- 'pai' or 'manual'
    cost_updated_at         TIMESTAMPTZ,
    pai_cost                NUMERIC(12,4),
    pai_cost_updated_at     TIMESTAMPTZ,

    -- Selling
    selling_price           NUMERIC(12,2),
    compare_at_price        NUMERIC(12,2),
    map_price               NUMERIC(12,2),
    price_category_id       BIGINT REFERENCES price_categories(id),
    price_override_amount   NUMERIC(12,2),           -- per-product override of grid
    price_override_reason   TEXT,
    qty_tiers_enabled       BOOLEAN NOT NULL DEFAULT FALSE,

    -- Cores
    has_core                BOOLEAN NOT NULL DEFAULT FALSE,    -- customer side
    core_sell_price         NUMERIC(12,2) DEFAULT 0,
    core_return_days        INT DEFAULT 90,
    has_vendor_core         BOOLEAN NOT NULL DEFAULT FALSE,    -- vendor side
    vendor_core_amount      NUMERIC(12,2) DEFAULT 0,

    -- Inventory
    qty_on_hand             INT NOT NULL DEFAULT 0,
    reorder_point           INT NOT NULL DEFAULT 0,
    min_qty                 INT DEFAULT 0,
    max_qty                 INT,

    -- Vendor
    preferred_vendor_id     BIGINT REFERENCES vendors(id),
    pai_sku                 TEXT,
    pai_link                TEXT,
    oem_part_number         TEXT,

    -- Competitive
    hhp_url                 TEXT,
    hhp_price               NUMERIC(12,2),
    hhp_last_check          TIMESTAMPTZ,
    atl_url                 TEXT,
    atl_price               NUMERIC(12,2),
    atl_last_check          TIMESTAMPTZ,

    -- Volatility / scrape priority
    pricing_volatility_score  NUMERIC(6,3) DEFAULT 0,    -- 0..1
    refresh_priority_score    NUMERIC(6,3) DEFAULT 0,

    -- Lifecycle
    stage                   TEXT NOT NULL DEFAULT 'active',   -- active / draft / archived
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cross-reference (interchanges)
CREATE TABLE product_interchanges (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT REFERENCES products(id) NOT NULL,
    xref_number     TEXT NOT NULL,
    xref_brand      TEXT,                            -- 'OEM' / 'PAI' / 'HHP' / 'ATL' / 'CUSTOM'
    source          TEXT NOT NULL,                   -- 'pai' / 'hhp' / 'atl' / 'manual'
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, xref_number, xref_brand)
);

-- Per-product quantity tiers (only consulted if products.qty_tiers_enabled)
CREATE TABLE product_qty_tiers (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT REFERENCES products(id) NOT NULL,
    min_qty         INT NOT NULL,
    discount_pct    NUMERIC(6,3) NOT NULL,
    sort_order      INT NOT NULL DEFAULT 0
);

-- Pricing tier grid
CREATE TABLE customer_tiers (
    id      BIGSERIAL PRIMARY KEY,
    code    TEXT UNIQUE NOT NULL,                    -- 'retail' / 'dealer' / 'fleet' / 'wholesale'
    name    TEXT NOT NULL,
    sort_order INT
);

CREATE TABLE price_categories (
    id      BIGSERIAL PRIMARY KEY,
    code    TEXT UNIQUE NOT NULL,
    name    TEXT NOT NULL
);

CREATE TABLE cost_bands (
    id      BIGSERIAL PRIMARY KEY,
    code    TEXT NOT NULL,                           -- 'B1' / 'B2' ...
    min_cost NUMERIC(12,2) NOT NULL,
    max_cost NUMERIC(12,2)
);

CREATE TABLE tier_category_discounts (
    tier_id     BIGINT REFERENCES customer_tiers(id),
    category_id BIGINT REFERENCES price_categories(id),
    band_id     BIGINT REFERENCES cost_bands(id),
    discount_pct NUMERIC(6,3) NOT NULL,
    PRIMARY KEY (tier_id, category_id, band_id)
);

-- Pending cost changes (require approval)
CREATE TABLE pending_cost_changes (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT REFERENCES products(id) NOT NULL,
    old_cost        NUMERIC(12,4) NOT NULL,
    new_cost        NUMERIC(12,4) NOT NULL,
    pct_change      NUMERIC(6,3) NOT NULL,
    source          TEXT NOT NULL,                   -- 'pai_scrape'
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'pending', -- pending / approved / rejected
    decided_at      TIMESTAMPTZ,
    decided_by      BIGINT,
    decision_notes  TEXT
);

-- Market change events (volatility detection)
CREATE TABLE market_change_events (
    id              BIGSERIAL PRIMARY KEY,
    trigger_product_id BIGINT REFERENCES products(id) NOT NULL,
    old_cost        NUMERIC(12,4),
    new_cost        NUMERIC(12,4),
    pct_change      NUMERIC(6,3),
    scope           TEXT NOT NULL,                   -- 'engine_family' / 'category' / 'vendor'
    scope_value     TEXT,                            -- e.g. 'S60' or 'Turbo' or vendor_id text
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    fanout_count    INT DEFAULT 0
);
```

---

## Cost source rule (B1)

```python
def update_cost_from_pai(product, new_pai_cost):
    if product.cost_source == "pai":
        # PAI owns this SKU's cost
        propose_cost_change(product, new_pai_cost, source="pai_scrape")
    else:
        # Manual SKU \u2014 store PAI cost as reference only
        product.pai_cost = new_pai_cost
        product.pai_cost_updated_at = now()
```

A new product created from a PAI SKU is born with `cost_source='pai'`. A
product created manually is born with `cost_source='manual'`. Switching
sources is an explicit admin action with audit.

---

## Pending cost change flow (B2)

```python
PCT_THRESHOLD = 0.05   # configurable

def propose_cost_change(product, new_cost, source):
    old = product.cost
    pct = (new_cost - old) / old if old else 1.0

    if abs(pct) < PCT_THRESHOLD:
        # within tolerance \u2014 apply immediately
        product.cost = new_cost
        product.cost_updated_at = now()
        return

    insert_pending_cost_change(
        product_id=product.id,
        old_cost=old,
        new_cost=new_cost,
        pct_change=pct,
        source=source,
    )
    emit("product.cost_change_pending", {...})

    # Volatility detection \u2014 fan out to related SKUs
    if abs(pct) >= 0.10:        # 10%+ shift is a market signal
        create_market_change_event(product, old, new_cost, pct)
```

Approval API:
- `GET  /pending-cost-changes?status=pending`
- `POST /pending-cost-changes/{id}/approve` → applies cost, updates product
- `POST /pending-cost-changes/{id}/reject`  → discards proposal

---

## Pricing resolver

Resolves the **final price** for `(product, customer, qty)`. Order:

1. **Per-product override** — if `products.price_override_amount` is set,
   use it. Skip everything else.
2. **Per-product qty tiers** — if `products.qty_tiers_enabled` and qty
   matches a `product_qty_tiers` row, apply that discount to selling_price.
3. **Tier × category × band grid** — look up
   `tier_category_discounts[customer.tier][product.category][product.cost_band]`,
   apply discount_pct to `selling_price` (or compute from cost × markup if
   selling_price is null).
4. **Flat selling_price** — if grid lookup misses, use selling_price as-is.
5. **Cost × default markup** — last-resort fallback if selling_price is
   null. Default markup per category from `settings.default_markups`.

Then:
- **MAP soft-warn:** if final < `map_price`, return the price BUT flag
  `MAP_VIOLATION` in the resolution metadata. Calling code (quote/SO/invoice
  service) records the violation, optionally requires manager PIN.

```python
@dataclass
class PriceResolution:
    final_price: Decimal
    source: str                 # 'override' / 'qty_tier' / 'tier_grid' / 'flat' / 'markup'
    base_price: Decimal
    discount_pct: Decimal
    map_violation: bool
    explanation: list[str]      # human-readable steps for audit

def resolve_price(product, customer, qty) -> PriceResolution: ...
```

The resolver is **pure** — no DB writes. Quote/SO/Invoice services capture
the resolution and persist it on the line.

---

## Pricing refresh — event-driven (B3)

No nightly full scan. Instead, a **`pricing_refresh_queue`** with triggers:

| Event | Action |
|-------|--------|
| `product.viewed_in_workbench` | enqueue refresh for this SKU, prio 9 |
| `quote_line.added` | enqueue refresh for this SKU, prio 8 |
| `manual.refresh_requested` | enqueue refresh for this SKU, prio 10 |
| `po.line_under_review` | enqueue refresh for this SKU + related, prio 7 |
| `margin.warning_triggered` | enqueue, prio 7 |
| `staleness.threshold_exceeded` | enqueue, prio 3 |
| `market_change_event.fanout` | enqueue all SKUs in scope, prio 5 |

### Refresh priority score

```python
def refresh_priority_score(product) -> float:
    score = 0.0
    if product.last_sold_at and (now() - product.last_sold_at) < 90d:
        score += 0.4                                # active SKU
    if product.qty_on_hand > 0:
        score += 0.2
    if product.hhp_url or product.atl_url:
        score += 0.1                                # competitive SKU
    score += min(product.pricing_volatility_score, 0.3)
    return score
```

Used to order the queue when multiple items wait.

### Pricing volatility score

For each product, track cost changes in `product_cost_history` (separate
audit table). The volatility score is the **stdev of pct-changes over the
last 6 months**, normalized to 0..1. High-volatility products get refreshed
more aggressively when their priority comes up.

### Market change fanout

When a single SKU shows a >10% cost shift, the engine:

1. Creates a `market_change_event` with scope = engine_family / category /
   vendor.
2. Enqueues a refresh for each sibling SKU at priority 5.
3. Rate-limited worker drains the queue, never exceeding PAI's politeness
   budget.

---

## Stale-cost threshold

`pai_cost_updated_at` older than:
- 30 days for active SKUs (`last_sold_at within 90d`)
- 90 days for inactive SKUs

…enqueues a refresh at low priority. **No bulk scan.** This is the only
"scheduled" refresh, and it scales to N-per-hour, not all-at-once.

---

## Cross-reference (B4 + B5)

Cross-refs come from multiple sources, merged into `product_interchanges`:

- **PAI scrape** writes rows with `source='pai'`, `xref_brand='OEM'` for the
  OEM number, plus `xref_brand='PAI'` for PAI's own SKU.
- **HHP scrape** writes `source='hhp'`, rows for every OEM number on the
  product page.
- **ATL scrape** same with `source='atl'`.
- **Manual** entry via API, `source='manual'`.

Dedup key: `(product_id, xref_number, xref_brand)`. Identical rows across
sources collapse; latest `created_at` wins.

---

## New-product creation paths (B4)

All six paths land in the same `create_product()` service which is
responsible for: SKU generation (sequence per condition), default field
population, initial cross-ref rows, audit. Paths just differ in *where the
data comes from*.

### Path A — Manual
```
POST /products
{ "sku":"...", "title":"...", "cost":..., "selling_price":..., ... }
```

### Path B — From PAI SKU
```
POST /products/from-pai
{ "pai_sku":"ABC123" }
```
Engine fetches PAI live (or cache if fresh), maps fields, creates product
with `cost_source='pai'`, seeds OEM xref.

### Path C — From OEM number
```
POST /products/from-oem
{ "oem":"23532555" }
```
Engine searches PAI by OEM, presents matches; client picks one;
engine calls Path B.

### Path D — From CSV bulk
```
POST /products/bulk-import
{ "rows":[ {sku,title,cost,...}, ... ], "idempotency_key":"..." }
```
Returns per-row success / error.

### Path E — From HHP match
```
POST /products/from-hhp
{ "hhp_url":"...", "pai_match_sku":"..." }
```
HHP gives copy/images/competitor price; PAI gives our cost. Engine merges.

### Path F — From competitor URL paste
```
POST /products/from-competitor-url
{ "url":"https://atldiesel.com/products/..." }
```
Engine sniffs source (hhp/atl), scrapes, creates a **draft** product the
operator must complete (no cost yet because no PAI match).

---

## Competitor price tracking (B7 + B8)

- Cadence: **weekly** for SKUs with `hhp_url` or `atl_url`.
- Worker: low-priority entries in `pricing_refresh_queue` with `source='hhp'`
  or `'atl'`.
- When a competitor price has dropped below ours (`selling_price`):
  - Append to `competitive_alerts` table
  - Emit `competitor.undercut` event
  - Visible via `GET /alerts/competitive`
- **No auto-match.** No email. Pure log + alert.

```sql
CREATE TABLE competitive_alerts (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT REFERENCES products(id) NOT NULL,
    source          TEXT NOT NULL,                   -- 'hhp' / 'atl'
    our_price       NUMERIC(12,2) NOT NULL,
    their_price     NUMERIC(12,2) NOT NULL,
    diff            NUMERIC(12,2) NOT NULL,          -- our - theirs (positive = we're higher)
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by BIGINT
);
```

---

## Public API (selected)

```
GET    /products
GET    /products/{id}
POST   /products
POST   /products/from-pai
POST   /products/from-oem
POST   /products/from-hhp
POST   /products/from-competitor-url
POST   /products/bulk-import
PATCH  /products/{id}
POST   /products/{id}/refresh-cost          # manual trigger
POST   /products/{id}/override-price        # writes price_override_amount
GET    /products/{id}/cross-refs
POST   /products/{id}/cross-refs            # manual xref add
DELETE /products/{id}/cross-refs/{xrefId}
GET    /products/{id}/qty-tiers
PUT    /products/{id}/qty-tiers             # replace whole set
POST   /pricing/resolve                     # body: product_id, customer_id, qty
GET    /pending-cost-changes
POST   /pending-cost-changes/{id}/approve
POST   /pending-cost-changes/{id}/reject
GET    /alerts/competitive
POST   /alerts/competitive/{id}/ack
GET    /market-change-events
```
