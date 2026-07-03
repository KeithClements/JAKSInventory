# Engine principles

1. **Headless.** Every operation is callable as a function and/or an API
   endpoint. Nothing in the engine should require a screen to function.
2. **Idempotent writes.** Every mutation accepts an `idempotency_key`
   (client-supplied). Same key = same result, no duplicate side effects.
3. **Auditable.** Every change to money, inventory, or external system state
   writes a row to an append-only audit table (`inventory_audit`,
   `qbo_sync_log`, `email_log`, `core_ledger`).
4. **Event-driven.** Mutations emit domain events; jobs subscribe.
   Events: `product.updated`, `invoice.finalized`, `payment.applied`,
   `core.returned`, `po.received`, `rga.shipped`, `rga.credited`,
   `scrape.completed`.
5. **Single source of truth per concern.**
   - Inventory + on-hand → engine DB.
   - Accounting ledger → QBO.
   - Live catalog cost/list → PAI portal (cached locally).
6. **No silent rounding.** Currency stored as decimal; conversions explicit.
7. **Background work runs in workers**, never in the request path.
8. **Failure surfaces.** Any failed external call lands in a retry queue
   visible via an API endpoint. No silent swallowing.
9. **Schema migrations are forward-only**, numbered, run at startup,
   guarded so re-runs are no-ops.
10. **Secrets** (PAI cookies, QBO tokens, SMTP creds) live in an encrypted
    store keyed off an installation secret, never in plaintext config.
