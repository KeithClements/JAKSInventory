# Integration: Shopify Sync

**Existing code:** `jaks_inventory/shopify/*`.

Shopify is the public-facing storefront. Local app is system of record;
Shopify is a downstream channel.

---

## Modes

| Mode | Behavior |
|------|----------|
| **disabled** | No traffic. |
| **read_only** | Pull orders, no pushes. |
| **read_write** | Push products & inventory; pull orders. (Default.) |

---

## Product publishing

A product publishes to Shopify when **all** are true:
- `publish_shopify = 1`
- `qty_on_hand > 0`
- Has at least one image
- Has SEO title + description (or auto-generated fallback)

On publish:
- Generate handle from SKU + title (slugify).
- Push product create or update (with metafields for OEM, vendor, fitment).
- Save `shopify_product_id` and `shopify_variant_id` back to `products`.
- Set status to `active` on Shopify.

### Auto-unpublish
When `qty_on_hand` falls to 0 AND `auto_unpublish_when_zero = 1`, set
Shopify status to `draft` (not deleted — preserves SEO history). When qty
returns >0, set back to `active`.

### Image sync
Image order matches `product_images.sort_order`. We push file binaries
(not links) so Shopify CDN hosts them.

---

## Inventory level pushes

On any local `qty_on_hand` change (via `adjust_qty`), enqueue a Shopify
inventory level update for the relevant location. Worker pushes in batch
every 30 s to stay within rate limits.

---

## Order pull

A scheduled job runs every **10 minutes** (configurable):
1. Pull Shopify orders with status filter (default: `paid` + `pending`).
2. For each order:
   a. Try to match customer by email/phone. If miss, create a customer with
      tag `shopify_guest`.
   b. Create a Sales Order in **Draft** state with `source = 'shopify'`.
   c. Attach line items by matching SKU → local product. Unmatched SKUs
      flag the SO for manual review.
   d. Store `shopify_order_id` for idempotency. Re-running the pull won't
      duplicate.
3. Notify the sales team via Dashboard activity feed.

---

## Order → invoice automation (optional)

In `read_write` mode + `auto_finalize_shopify = 1`, a Shopify paid order
becomes:
- SO Draft → SO Confirmed automatically.
- If `stock available`, allocate. If not, mark backordered.
- Once shipped, auto-create Invoice and apply Shopify payment record.

---

## Webhook handling

If Shopify webhooks are enabled:
- `orders/create` → instantly pull order (don't wait for 10-min job).
- `orders/paid` → mark local SO as paid.
- `orders/cancelled` → cancel local SO + restock.
- `inventory_levels/update` (if pull enabled) → reconcile.

---

## Tags & collections mapping

Configurable mapping `products.category → shopify_collection_id`. Allows
collections to auto-populate.

Tags pushed:
- Product condition (`new` / `reman` / `used`)
- Has-core / no-core
- Manufacturer
- Free-shipping (computed)
- Any custom tags in `products.tags`

---

## For a Base44 implementation

Shopify has a published REST + GraphQL API. Base44 can call it directly from
server-side actions; no sidecar needed (unlike scrapers).

Implement as:
- A `shopify_sync_cache` table to track last-pushed values per product
  (avoid unnecessary writes).
- Scheduled actions: product sync (5 min), inventory sync (30 s), order
  pull (10 min).
- Webhook receiver action with HMAC verification.
