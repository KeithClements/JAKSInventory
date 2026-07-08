# Public API Surface

FastAPI on the on-prem box. Session-cookie auth (single-tenant). All
mutating endpoints accept an `Idempotency-Key` header.

## Conventions
- JSON bodies; ISO-8601 timestamps; decimals as strings to avoid float drift.
- Errors: `{ "error":{ "code","message","details" } }`.
- Pagination: `?limit=&cursor=` with `next_cursor` in response.
- All list endpoints support `?q=` free-text, `?updated_since=`.

## Auth
```
POST   /auth/login              { username, password } -> sets cookie
POST   /auth/logout
POST   /auth/pin-check          { pin } -> { ok:true } for risky ops
GET    /auth/me
```

## Products (see `02_products_engine.md`)
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
POST   /products/{id}/refresh-cost
POST   /products/{id}/price-check
POST   /products/{id}/override-price
DELETE /products/{id}/override-price
GET    /products/{id}/cross-refs
POST   /products/{id}/cross-refs
DELETE /products/{id}/cross-refs/{xrefId}
GET    /products/{id}/qty-tiers
PUT    /products/{id}/qty-tiers
POST   /pricing/resolve
GET    /pending-cost-changes
POST   /pending-cost-changes/{id}/approve
POST   /pending-cost-changes/{id}/reject
GET    /alerts/competitive
POST   /alerts/competitive/{id}/ack
GET    /market-change-events
```

## Customers
```
GET    /customers
GET    /customers/{id}
POST   /customers
PATCH  /customers/{id}
GET    /customers/{id}/credits
GET    /customers/{id}/cores       -- outstanding customer_core_events
GET    /customers/{id}/orders      -- quotes+SOs+invoices unified
POST   /customers/merge            -- merge duplicates
```

## Vendors
```
GET    /vendors
GET    /vendors/{id}
POST   /vendors
PATCH  /vendors/{id}
GET    /vendors/{id}/obligations   -- open vendor_core_obligations
GET    /vendors/{id}/credits
```

## Quotes / SOs / Invoices
```
GET    /quotes
POST   /quotes
PATCH  /quotes/{id}
POST   /quotes/{id}/send
POST   /quotes/{id}/convert-to-so
POST   /quotes/{id}/mark-lost      -- requires lost_reason_code
GET    /lost-sales

GET    /sales-orders
POST   /sales-orders
PATCH  /sales-orders/{id}
POST   /sales-orders/{id}/pick
POST   /sales-orders/{id}/pack-ship
POST   /sales-orders/{id}/convert-to-invoice

GET    /invoices
POST   /invoices
PATCH  /invoices/{id}
POST   /invoices/{id}/finalize
POST   /invoices/{id}/void
POST   /invoices/{id}/payments     -- record payment
POST   /invoices/{id}/credit-memo  -- issue credit memo / refund
```

## Purchasing
```
GET    /purchase-orders
POST   /purchase-orders
PATCH  /purchase-orders/{id}
POST   /purchase-orders/{id}/send
POST   /purchase-orders/{id}/cancel
GET    /purchase-orders/{id}/receipts
POST   /purchase-orders/{id}/receipts        -- receive (full or partial)

GET    /low-stock
POST   /low-stock/auto-tag
POST   /purchase-orders/from-tagged          -- wizard: build draft POs
```

## Cores (see `03_cores_engine.md`)
```
GET    /cores/customer-events
GET    /cores/customer-events/{id}
POST   /cores/customer-events/{id}/return    -- W3 happy path
POST   /cores/customer-events/{id}/expire    -- manual expire

GET    /cores/vendor-obligations
GET    /cores/vendor-obligations/{id}
POST   /cores/rgas                           -- W4 create RGA from obligations
POST   /cores/rgas/{id}/ship                 -- attach carrier/tracking
POST   /cores/rgas/{id}/apply-credit         -- W5 vendor credit
POST   /cores/vendor-obligations/{id}/reject -- W6

GET    /cores/units                          -- physical core inventory
GET    /cores/units/{id}/audit               -- transition log
```

## Inventory
```
GET    /inventory/audit
POST   /inventory/adjust                     -- single adjustment
POST   /inventory/adjust-bulk
GET    /kits
POST   /kits
PATCH  /kits/{id}
```

## QBO (see `04_qbo_sync_engine.md`)
```
GET    /qbo/status
GET    /qbo/preflight
POST   /qbo/mode
POST   /qbo/connect
GET    /qbo/queue
POST   /qbo/queue/{id}/retry
GET    /qbo/conflicts
POST   /qbo/conflicts/{id}/resolve
GET    /qbo/account-map
PUT    /qbo/account-map
POST   /qbo/sync-all/{entity}
```

## Email (see `05_email_engine.md`)
```
GET    /email/config
PUT    /email/config
POST   /email/test
GET    /email/templates
PUT    /email/templates/{key}
POST   /email/send
GET    /email/queue
GET    /email/log
GET    /email/suppressions
```

## Scrapers (see `06_scrapers_engine.md`)
```
GET    /scrape/queue
POST   /scrape/queue
GET    /scrape/runs
POST   /scrape/pai/login
POST   /scrape/pai/refresh-session
```

## SMS (kept per F decisions)
```
GET    /sms/config
PUT    /sms/config
POST   /sms/send
GET    /sms/queue
GET    /sms/log
```

## Pricing tier grid
```
GET    /pricing/tiers
PUT    /pricing/tiers
GET    /pricing/categories
PUT    /pricing/categories
GET    /pricing/bands
PUT    /pricing/bands
GET    /pricing/grid                         -- full discount matrix
PUT    /pricing/grid                         -- bulk upsert
```

## Reports / search
```
GET    /reports/sales?from=&to=&group_by=
GET    /reports/margin
GET    /reports/aging-ar
GET    /reports/stale-cost
GET    /search?q=                            -- unified: parts, customers, vendors, ESN-less now
```

## Events stream (for any client / future UI)
```
GET    /events?since=cursor                  -- long-poll, returns recent domain events
```

Domain events emitted:
- `product.created`, `product.updated`, `product.cost_change_pending`,
  `product.cost_change_approved`
- `pricing.map_violation`
- `competitor.undercut`
- `market_change_event`
- `customer.created`, `customer.updated`
- `vendor.created`, `vendor.updated`
- `quote.created`, `quote.sent`, `quote.converted`, `quote.lost`
- `so.created`, `so.shipped`, `so.invoiced`
- `invoice.finalized`, `invoice.voided`, `payment.applied`, `credit_memo.issued`
- `po.sent`, `po.received`, `bill.posted`
- `core.received_from_vendor`, `core.sold`, `core.returned_by_customer`,
  `core.shipped_to_vendor`, `core.credited_by_vendor`, `core.rejected_by_vendor`,
  `core.customer_expired`
- `inventory.adjusted`
- `qbo.push_done`, `qbo.push_failed`, `qbo.conflict`
- `email.sent`, `email.failed`
- `scrape.completed`, `scrape.parse_error`
