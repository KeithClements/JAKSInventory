# QBO Sync Engine

OAuth2 to Intuit, queue-based push, polled pull. **Default mode: mock.**

## Modes

| Mode | Behavior |
|------|----------|
| `mock` | All push paths run end-to-end, queue records, but no live HTTP. **Default.** |
| `read_only` | Pull only. Push events queue but log "skipped (read_only)". |
| `read_write` | Full operation. Push lives; pull poll runs. |

Mode is in `settings_kv['qbo.mode']`. Flip via API only; never code-default
to `read_write`.

## Tables

```sql
CREATE TABLE qbo_credentials (
    realm_id        TEXT PRIMARY KEY,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    access_expires_at TIMESTAMPTZ NOT NULL,
    refresh_expires_at TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE qbo_account_map (
    purpose         TEXT PRIMARY KEY,    -- 'income','inventory_asset','cogs','tax:CA',...
    qbo_account_id  TEXT NOT NULL,
    qbo_account_name TEXT
);

CREATE TABLE qbo_entity_refs (
    local_entity    TEXT NOT NULL,       -- 'customer','vendor','item','invoice',...
    local_id        BIGINT NOT NULL,
    qbo_id          TEXT NOT NULL,
    qbo_sync_token  TEXT,                -- for stale-write detection
    last_pushed_at  TIMESTAMPTZ,
    last_payload_hash TEXT,              -- for idempotency
    PRIMARY KEY (local_entity, local_id)
);

CREATE TABLE qbo_sync_queue (
    id              BIGSERIAL PRIMARY KEY,
    entity          TEXT NOT NULL,
    op              TEXT NOT NULL,       -- 'create' / 'update' / 'void'
    local_id        BIGINT NOT NULL,
    payload_hash    TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending/in_progress/done/failed_retry/failed
    attempts        INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error      TEXT,
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE TABLE qbo_sync_log (
    id              BIGSERIAL PRIMARY KEY,
    queue_id        BIGINT,
    entity          TEXT,
    op              TEXT,
    local_id        BIGINT,
    qbo_id          TEXT,
    status          TEXT,
    request_summary TEXT,
    response_summary TEXT,
    error           TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Push entities (D2)

The engine enqueues sync rows for these and no others:

| Local event | Entity | Op | QBO entity |
|-------------|--------|----|-----------|
| `customer.created/updated` | customer | create/update | Customer |
| `vendor.created/updated` | vendor | create/update | Vendor |
| `product.created/updated` (when `cost_source` settled) | item | create/update | Item (non-inventory) |
| `invoice.finalized` | invoice | create | Invoice |
| `invoice.voided` | invoice | void | Invoice (Void) |
| `payment.applied` | payment | create | Payment |
| `sales_receipt.finalized` (cash sale, no AR) | sales_receipt | create | SalesReceipt |
| `credit_memo.issued` | credit_memo | create | CreditMemo |
| `po_receipt.billed` | bill | create | Bill |
| `vendor_credit.applied` | vendor_credit | create | VendorCredit |

**Explicitly NOT pushed** (per D2 answers):
- Estimates (quotes stay engine-side)
- Purchase orders (only the resulting Bill goes to QBO)
- Inventory adjustments — engine owns the audit trail; QBO sees COGS at
  invoice time
- Core liability JEs — cores stay engine-side until they materialize as a
  CreditMemo (customer credit) or VendorCredit

## Items in QBO are NON-inventory (D5)

QBO `Item.Type = "Service"` or `"NonInventory"`. The engine never tells QBO
about qty changes. When an invoice is pushed, the line carries:

- ItemRef (the local product's `qbo_id`)
- Qty
- UnitPrice
- Description
- Income account (from `qbo_account_map['income']`)
- Tax (mapped tax code)

QBO records the income; engine records the inventory + COGS internally.

## Push worker

Loop every 60 s. For each `pending` row where `next_attempt_at <= now`:

1. Set `status='in_progress'` (atomic).
2. Refresh payload from current local state (don't trust the row's snapshot).
3. Compute `payload_hash`; if unchanged AND `qbo_entity_refs.last_pushed_at`
   recent AND op=update → skip as no-op.
4. Build QBO payload via entity-specific builder.
5. **Idempotency:** for `Invoice`/`Payment`/`CreditMemo`/`Bill`/
   `SalesReceipt`/`VendorCredit`, set `DocNumber` from local number
   (`INV-2026-00831`). Before POST, query QBO for that DocNumber. If found,
   adopt that `qbo_id` and mark done.
6. POST. On success → record `qbo_id`, `qbo_sync_token`, `last_payload_hash`;
   status `done`; write `qbo_sync_log` row.
7. On failure → increment attempts, compute backoff, store error.

### Backoff

`1m → 5m → 15m → 1h → 6h → fail`. After 5 attempts, `status='failed'` —
human queue.

### Stale-write (sync token mismatch)

On `Stale Object` from QBO:
1. Pull QBO's current copy.
2. Three-way merge: local, QBO, last-pushed snapshot.
3. If conflict-free, re-push with new sync token.
4. If conflicting fields, write to `qbo_conflicts` table for human review.

## Pull worker (D3)

Polled every 5 min (configurable):

- **Customer updates** — QBO `CDC` (Change Data Capture) endpoint since
  `last_pull_at`. Apply non-conflicting changes; conflicts → `qbo_conflicts`.
- **Payments entered directly in QBO** — list Payments since last poll,
  ignore those with local `qbo_id` already, create local `invoice_payments`
  rows for the rest. Match by Invoice DocNumber.

```sql
CREATE TABLE qbo_pull_state (
    entity          TEXT PRIMARY KEY,
    last_pulled_at  TIMESTAMPTZ NOT NULL,
    last_cursor     TEXT
);

CREATE TABLE qbo_conflicts (
    id              BIGSERIAL PRIMARY KEY,
    entity          TEXT NOT NULL,
    local_id        BIGINT,
    qbo_id          TEXT,
    field           TEXT,
    local_value     TEXT,
    qbo_value       TEXT,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    resolution      TEXT,
    resolved_by     BIGINT
);
```

## Account mapping required before flip to read_write

Before switching mode from `mock` to `read_write`, validator must pass:

- `income`, `inventory_asset` (not used but kept for future), `cogs` mapped
- At least one tax code mapped per local `tax_rates` row
- `sales_discounts`, `restocking_fee`, `core_liability`, `vendor_core_clearing`
  mapped
- All active customers and vendors have a `qbo_id` (one-time backfill)
- All non-archived products have a `qbo_id`

`GET /qbo/preflight` returns a checklist with pass/fail per requirement.

## API surface

```
GET    /qbo/status              -> mode, queue depth, fails, last sync at
GET    /qbo/preflight           -> readiness checklist
POST   /qbo/mode                -> { "mode":"mock"|"read_only"|"read_write" }
POST   /qbo/connect             -> begins OAuth (returns Intuit URL)
POST   /qbo/callback            -> finishes OAuth
POST   /qbo/disconnect

GET    /qbo/queue?status=...
POST   /qbo/queue/{id}/retry
POST   /qbo/queue/{id}/cancel

GET    /qbo/account-map
PUT    /qbo/account-map         -> bulk update mappings

GET    /qbo/conflicts
POST   /qbo/conflicts/{id}/resolve   -> { "winner":"local"|"qbo", "value":... }

POST   /qbo/sync-all/customers    -> backfill
POST   /qbo/sync-all/vendors
POST   /qbo/sync-all/items
```
