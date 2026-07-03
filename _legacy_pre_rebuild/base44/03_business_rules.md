# 03 — Business Rules

The non-obvious rules that the desktop app enforces. **These are not optional.**

---

## Pricing

### Selling price resolution order

When a quote/SO/invoice line is created, the unit price is determined by this
**ordered** lookup. First non-null wins:

1. **Manual override** — user typed a price directly into the line.
2. **Contract price** — `pricing_overrides` row matching `(customer_id, product_id)` within effective dates.
3. **Customer-tier × Category × Cost-band discount** — see grid below.
4. **Product `selling_price`** column (the list price).
5. **`pai_cost × markup`** — fallback markup, default 1.5×.

### Tier discount grid

```
discount_pct = tier_category_discounts.discount_pct
   WHERE tier_id      = customer.tier_id
     AND category_id  = resolve_product_category(product)
     AND band_id      = find_band_for_cost(product.cost)
```

`resolve_product_category(product)` falls back through:
`product.price_category_id` →
`manufacturer_categories[product.manufacturer]` →
`vendor_categories[product.preferred_vendor]` →
`default_category`.

Final price: `selling_price * (1 - discount_pct/100)`.

### MAP enforcement

If `product.map_price > 0` and the calculated unit price falls **below** MAP,
the quote line must show a warning chip. Saving is still allowed but the chip
must be explicit. Never silently bump the price up.

### Quantity tiers

If `product_qty_tiers` rows exist for a product, those override the
category-tier grid for that single SKU. Pick the tier whose `min_qty` is the
largest value `≤ line.qty`.

### Compare-at price

Display-only. Used for "was $X, now $Y" UI. Never used for cost/margin math.

---

## Cores (the most-asked-about subsystem)

### Three core handling modes (per product)

Stored in `products.core_handling` (one of):

| Mode | Meaning | Customer pays core? | Vendor charges core? |
|------|---------|--------------------|--------------------|
| `refundable` | Refundable if returned within window | Yes | Yes |
| `conditional_exchange` | Refundable only if vendor inspection passes | Yes | Yes (held) |
| `no_charge` | New part, no core | No | No |

### Core charge appears on which lines?

On any quote/SO/invoice line where `product.has_core = 1` AND `product.core_charge > 0`.
Render as a **separate** line item directly under the parent part line.
`quote_lines.parent_line_id` links the core line to its parent.

### Customer side — when a customer returns a core

1. Customer brings in a spent unit.
2. App searches outstanding `customer_cores` for that customer + product (FIFO).
3. If a matching obligation exists and the return is within `due_back_by`:
   - Mark the `customer_core` row `returned`.
   - Create a `customer_credit` for `customer_core.core_charge`.
   - Customer can apply credit to a new invoice or take cash refund.
4. The returned physical unit becomes a `vendor_core` obligation (we now owe
   the vendor a return on this unit).
5. If returned after `due_back_by`: mark `forfeited`; no credit; unit becomes
   our property (resell as USED or scrap).

### Vendor side — RGAs

Cores accumulate per vendor. When a threshold is hit (per-vendor setting,
e.g. 10 units or $500 value), purchasing creates a `vendor_return` (RGA):

1. Select outstanding `vendor_core_obligations` for that vendor.
2. Submit RGA to vendor (PDF/email).
3. Mark `submitted_at`.
4. When vendor approves → `Approved`; print/ship → `Shipped`; vendor issues
   credit memo → `Credited` and `actual_credit` is recorded.
5. Credit memo posts as a vendor credit in QBO.

### Core aging

The Cores dashboard shows aging buckets: `0–30d`, `30–60d`, `60–90d`, `90+d`.
Cores past their `due_back_by` and not yet returned show in **red**.

---

## Warranties

### Two warranty layers

1. **Supplier warranty** — what the OEM/distributor honors (e.g. "12 months, parts only").
   Stored on `products.supplier_warranty_*`.
2. **JAK warranty** — the extended warranty JAK sells on top.
   Stored on `products.jaks_warranty_*`. Default rate `warranty_percentage = 10%`.

### Warranty on a quote/invoice line

- If `is_warrantable = 1`, the line shows a "+ Extended warranty" toggle.
- If toggled on, a separate warranty charge line is added:
  `warranty_charge = line_subtotal * warranty_percentage / 100`.
- Snapshot the chosen warranty terms onto `customer_warranties` at invoice
  finalization so future claims have the historical agreement.

### Warranty claim flow (simplified)

1. Customer reports failure.
2. Look up `customer_warranties` by customer + ESN/serial.
3. If covered → issue replacement at $0 + open vendor warranty return.
4. If not covered → standard return process or new sale.

---

## Returns / RMAs

### Customer-side return rules

- A return must reference an existing invoice line.
- The return must occur within `return_window_days` (default 30, customizable per category).
- `restocking_fee_pct` applies (default 15 % for non-defective).
- Restocking fee is **waived** if the line was marked defective OR if the
  return is within 48h.
- Items in `condition = USED` are non-returnable except for defects.
- Core returns are NOT regular returns — they go through the core flow above.

### Return creates:
- A `return` record linked to the original invoice.
- A `credit_memo` (= `customer_credits` row) for the refunded amount.
- An inventory `adjustment` of `+qty` (back on the shelf) unless the item is
  scrap (`disposition = 'scrap'`).
- A `qbo_sync_queue` entry to push the credit memo.

---

## Inventory rules

### qty_on_hand integrity

`products.qty_on_hand` is **derived from** `inventory_audit` entries.
Every mutation must go through one of these flows:

| Action | Audit reason | Delta |
|--------|--------------|-------|
| PO receive | `PO_RECEIVE` | + |
| SO ship (or invoice finalize) | `SO_SHIP` | − |
| Customer return | `CUSTOMER_RETURN` | + |
| Vendor return shipped | `VENDOR_RETURN` | − |
| Manual adjustment | `ADJUST` | ± |
| Cycle count | `COUNT` | ± |
| Transfer in/out | `TRANSFER_IN` / `TRANSFER_OUT` | ± |
| Kit explode (on sale) | `KIT_EXPLODE` | − components, − kit if tracked |

Never directly UPDATE `products.qty_on_hand` from UI code. Always go through
`db.adjust_qty(product_id, delta, reason, source_type, source_id, user_id)`
which writes the audit row, updates the column, and emits the
`inventory_changed` signal.

### Reservations

When an SO line is created with state `pending`, the qty is **reserved** but
not yet shipped. `available = qty_on_hand - sum(reserved SO line qtys)`.
Quotes do NOT reserve stock; only SOs do.

### Reorder alerts

A SKU is "below reorder" when `qty_on_hand + on_order < reorder_point`.
The Products screen and Low Stock screen show these with an alert chip.

### Cycle counts

A count is a planned event that produces zero-or-more `ADJUST` entries with
`reason_code = 'COUNT'`. Counts must include `counter_user`, `count_at`,
and per-line `expected_qty` vs `actual_qty`.

---

## QBO synchronization

### Modes

- `mock` — never touch QBO. Used in dev/demo.
- `read_only` — pull from QBO, never push.
- `read_write` — bidirectional sync. **Production default.**

### Push triggers

Any of these enqueues a `qbo_sync_queue` row:

| Action | Pushes |
|--------|--------|
| Save new/changed product | Item upsert |
| Save customer | Customer upsert |
| Save vendor | Vendor upsert |
| Finalize invoice | Invoice + Payment (if any) |
| Receive PO fully | Bill |
| Issue customer credit | CreditMemo |
| Adjustment with reason=ADJUST | JournalEntry (for write-off categories only) |

### Queue worker

A background job drains `qbo_sync_queue` every 60 s with exponential backoff
on failures (1m → 5m → 15m → 1h, max 5 attempts). After 5 failures the row is
marked `failed` and surfaces in Sync Center for manual replay.

### Conflict resolution

If both sides changed the same entity since last sync:
- **Local wins** for: prices, costs, on-hand, descriptions.
- **QBO wins** for: AR balances, invoice payment status, customer credit limit,
  vendor terms.

### Webhook receiver

Subscribe to QBO webhooks for Customer / Invoice / Payment / Bill / Item.
Each event creates a `qbo_webhook_events` row and a corresponding pull task
in `qbo_sync_queue`.

---

## Shopify rules

- A product is publishable to Shopify iff `publish_shopify = 1` AND
  `qty_on_hand > 0` (or `allow_oversell = 1` setting).
- The handle is `products.handle` if non-empty, else slugified from title.
- Inventory level pushes after every `adjust_qty` for any SKU with
  `shopify_id IS NOT NULL`.
- Orders pull-down on a schedule (every 10 min); each becomes a draft SO.
- Customer match: by `shopify_customer_id`, then by email.

---

## Permissions (4 roles + viewer)

| Capability | admin | sales | purchasing | warehouse | viewer |
|------------|:-----:|:-----:|:----------:|:---------:|:------:|
| View any screen | ✓ | ✓ | ✓ | ✓ | ✓ |
| Edit products | ✓ | ✓ | ✓ | — | — |
| Adjust qty | ✓ | — | ✓ | ✓ | — |
| Create quote | ✓ | ✓ | — | — | — |
| Convert quote→SO | ✓ | ✓ | — | — | — |
| Create invoice | ✓ | ✓ | — | — | — |
| Void invoice | ✓ | — | — | — | — |
| Take payment | ✓ | ✓ | — | — | — |
| Issue refund/credit | ✓ | manager | — | — | — |
| Create PO | ✓ | — | ✓ | — | — |
| Receive PO | ✓ | — | ✓ | ✓ | — |
| Edit vendor | ✓ | — | ✓ | — | — |
| Edit customer credit limit | ✓ | — | — | — | — |
| Run scrapers | ✓ | ✓ | ✓ | — | — |
| Change settings | ✓ | — | — | — | — |
| Connect/disconnect QBO | ✓ | — | — | — | — |

`manager` is a soft override: a sales user can request elevation; admin must
approve. Out of scope for MVP — implement after launch.

---

## Tax

- Each customer has a default `tax_rate_id`. Each line can override.
- Tax-exempt customers: store the exemption certificate file path on
  `customers.tax_exempt_certificate`.
- Tax is calculated at line level, summed to the doc.
- Sync tax rates to QBO `TaxCode` mapping in `tax_rates.qbo_tax_code_id`.

---

## Numbering schemes

Auto-generated, monotonic, never reused. Stored in `sku_sequences` /
`document_sequences`:

| Doc | Prefix | Example |
|-----|--------|---------|
| Quote | `Q` | `Q-2026-00012` |
| Sales Order | `SO` | `SO-2026-00045` |
| Invoice | `INV` | `INV-2026-00831` |
| Purchase Order | `PO` | `PO-2026-00112` |
| RGA / Vendor Return | `RGA` / `VR` | `RGA-PAI-0008` |
| Receipt | `RCV` | `RCV-2026-00321` |
| Adjustment | `ADJ` | `ADJ-2026-00077` |
| Credit Memo | `CM` | `CM-2026-00019` |

Year resets on Jan 1.

---

## Currency, rounding, money

- All money stored as `REAL` USD (no cents-as-int trick).
- Display: 2 decimals with locale formatting (`$1,234.56`).
- Internal math: keep full precision until display.
- Tax rounding: per line, not per cart, to match QBO behavior.
- Never accept money input without sanity bounds (`> 0`, `< 1e7`).
