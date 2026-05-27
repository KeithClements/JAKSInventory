# Integration: QuickBooks Online Sync

**Existing code:** `jaks_inventory/qbo/*`, screens in
`jaks_inventory/ui/sync_center.py`, `qbo_screen.py`.

QBO is the system of record for accounting. JAK's Diesel PRO is the system
of record for inventory, sales, purchasing, and cores. Sync is **outbound
heavy** (we push) with selective pulls.

---

## Connection

OAuth2 (Intuit). Stored:
- `realm_id` (the QBO company ID)
- `access_token`, `refresh_token`, `expires_at`
- Refresh worker keeps token alive.

Tokens are stored in `qbo_credentials` table, encrypted at rest using a key
derived from the operator's installation secret.

---

## Operating modes

Configured per-environment in Settings → QuickBooks:

| Mode | Behavior |
|------|----------|
| **mock** | Default for dev. All calls are no-ops, queue still records. Used to build/test flows without touching real QBO. |
| **read_only** | Pulls for reconciliation only. Push operations log "would push" and queue, but no API write. |
| **read_write** | Full bidirectional. Default for production. |

A red/amber/green banner across the top of accounting screens always
reflects the current mode.

---

## Push triggers (event-driven)

When these happen, an entry is added to `qbo_sync_queue`:

| Local event | QBO entity | QBO op |
|-------------|-----------|--------|
| Product created/updated | Item | create / update |
| Customer created/updated | Customer | create / update |
| Vendor created/updated | Vendor | create / update |
| Invoice finalized | Invoice | create |
| Invoice voided | Invoice | void |
| Invoice payment received | Payment | create |
| Sales receipt (cash sale) | SalesReceipt | create |
| Credit memo issued | CreditMemo | create |
| Quote sent (optional) | Estimate | create |
| PO sent | PurchaseOrder | create |
| Receipt posted (when invoiced by vendor) | Bill | create |
| Vendor credit | VendorCredit | create |
| Inventory adjustment | JournalEntry | create |
| Period close | JournalEntry batch | create |

Each row carries: entity, op, ref_id (local), payload JSON, retries, status,
last_error, next_attempt_at.

---

## Queue worker

A background loop runs every N seconds (default 60). For each `pending` row:

1. Acquire row (status → `in_progress`).
2. Build the payload (idempotently — refetch local data fresh).
3. POST to QBO with the Intuit SDK.
4. On success: mark `done`, store `qbo_id` in the source entity
   (`invoices.qbo_id`, `customers.qbo_id`, etc.), write `qbo_sync_log`.
5. On failure: increment `retries`, compute next backoff,
   set `status = failed_retry`, store error.
6. After 5 failures: `status = failed`. Surfaces in Sync Center for human
   triage.

### Exponential backoff
`1m → 5m → 15m → 1h → 6h` then human-only retry.

### Conflict resolution
If QBO returns `Stale Object` (entity changed since we read):
1. Pull QBO's current copy.
2. Three-way merge: local, QBO, last-known-pushed.
3. If conflict is safe (different fields), apply both. Otherwise mark for
   review.

### Idempotency
For Invoice / Payment / CreditMemo, we generate a `DocNumber` from our
local number (`INV-2026-00831`) and use it as the QBO `DocNumber`. On retry,
we first query QBO for that DocNumber — if present, we adopt that QBO ID
and mark done. This avoids duplicates from network blips.

---

## Pulls

Selected entities are pulled on schedule or webhook:

- **Customer** — webhook on update; we refresh balance, terms, addresses.
- **Invoice payment** — webhook; if a payment lands in QBO that didn't
  originate locally, we add it to `invoice_payments` and recompute balance.
- **Item** — daily reconciliation pass to detect SKU diffs.

Pull worker honours rate limits and writes to `qbo_webhook_events`.

---

## Webhooks

QBO can be configured to POST to our webhook endpoint (in the sidecar or the
Base44 server action) on entity changes. Each event:

1. Verify signature against the configured verifier secret.
2. Insert `qbo_webhook_events` row.
3. Enqueue appropriate refresh op in `qbo_sync_queue`.

---

## Account mapping (Settings → QBO)

The user maps these in Settings before going live in `read_write`:
- Income account
- Inventory asset account
- COGS account
- Tax payable accounts (one per `tax_rate`)
- Sales discounts account
- Restocking fee account
- Core liability account (for customer core charges held)
- Vendor core asset / clearing account

The QBO push will fail loudly if any of these are unmapped.

---

## Period close

A monthly process:
1. Lock invoices/payments/etc. for the closing period (status flag).
2. Push any laggard queue items.
3. Verify via reconciliation report.
4. Mark the period closed in `accounting_periods`.

After close, edits to that period are blocked or require a reversing entry.

---

## For a Base44 implementation

The QBO sync layer is mostly server-side actions, which Base44 supports
natively. The queue table + worker is implementable as:
- A `qbo_sync_queue` collection.
- A scheduled action (every 60 s) that processes pending rows.
- A webhook endpoint action for inbound QBO webhooks.

OAuth2 redirect URI is a custom Base44 page that completes the Intuit flow
and stores tokens.
