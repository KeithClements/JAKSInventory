# Shopify availability + pricing alignment — rollout guide

Built 2026-07-04. Everything here is **OFF / legacy by default** — nothing on the
live store changes until you enable a setting or run the staged migration. The ERP
stays the single source of truth; the scraper still only feeds CSV → ERP → Shopify.

---

## 1. Instant price push (LIVE, on by default)

Edit a product's price / SEO / tags in the ERP and the linked Shopify listing now
updates immediately:

- **On Save** — a full save of a *linked* product schedules a background push
  (fail-soft; never delays the save). Toggle with setting `shopify_push_on_edit`
  (default `1`). Autosave/per-keystroke does **not** push — only the Save button.
- **"⬆ Push to Shopify now"** button on the product page — pushes on demand and
  shows the result. Only appears when the product is linked to a live listing.

No push happens for unlinked products (nothing to update) or when Shopify isn't
configured. The nightly sync remains the batch fallback.

---

## 2. Sold-out availability model (staged — opt-in)

Setting `shopify_availability_mode`: `hide` (legacy, default) | `sold_out` (new).

| Situation | Legacy `hide` | New `sold_out` |
|---|---|---|
| Own stock, vendor in stock | Live, buyable | Live, buyable (keeps selling past your shelf) |
| Own 0, vendor in stock | Live, buyable (untracked) | **Available to order** — buyable, tracked, policy CONTINUE |
| Own stock, vendor out | **Hidden (404)** | Live — sells your shelf, then blocks |
| Own 0, vendor out | **Hidden (404)** | **Live "Sold out" page** — tracked, qty 0, can't oversell |
| Own 0, vendor unknown | Live | **Available to order** (never blocks an unknown reading) |
| Discontinued / deactivated | Hidden | Hidden (DRAFT) — the only truly-hidden case |

Buyability = **own shelf stock OR any vendor in stock**. Decision logic lives in
[`app/services/availability_policy.py`](app/services/availability_policy.py) and is
fully unit-tested.

### To roll it out

1. **Create the metafield definition** in Shopify Admin → Settings → Custom data →
   Products → Add definition:
   - Namespace and key: `custom.availability_state`
   - Type: Single line text
   (The ERP writes `in_stock` / `available_to_order` / `sold_out` here.)

2. **Preview** (nothing changes):
   ```
   .venv\Scripts\python.exe -m scripts.migrate_availability_states --refresh
   ```
   Shows how many listings become in-stock / available-to-order / sold-out / hidden,
   how many currently-hidden pages get **re-listed**, how many get hidden.

3. **Apply** (converts the live store + flips the mode):
   ```
   set JAKS_FERNET_KEY=<your key>
   .venv\Scripts\python.exe -m scripts.migrate_availability_states --apply
   ```
   Resumable (checkpoint in `data/.migrate_states_resume.json`), idempotent. Use
   `--limit 50` first for a smoke test. This re-lists the ~3,675 pages the ERP hid
   under the legacy model as live "Sold out" / "Available to order" pages.

4. **Theme** — add a badge to the product template so shoppers see the state. Paste
   into `snippets/availability-badge.liquid` and render it on the product page
   (`{% render 'availability-badge' %}`):

   ```liquid
   {%- assign state = product.metafields.custom.availability_state -%}
   {%- if state == 'in_stock' -%}
     <span class="avail avail--in">✅ In stock — ships today</span>
   {%- elsif state == 'available_to_order' -%}
     <span class="avail avail--order">🔵 Available to order — ships in 3–7 business days</span>
   {%- elsif state == 'sold_out' -%}
     <span class="avail avail--out">⛔ Sold out — <a href="tel:YOURNUMBER">call for ETA</a></span>
   {%- endif -%}
   ```
   (Theme edits belong in the storefront repo at `D:\Work Folder\Website`, not here.)

Once `sold_out` mode is on, the nightly sync and the instant push keep every
listing's status, inventory policy, tracked qty, and `availability_state` in step.

---

## 3. Web order → ERP inventory feed (built — off by default)

Setting `shopify_order_poll_enabled` (default `0`). When on, the ERP polls Shopify
orders every few minutes (`shopify_order_poll_interval_min`, default 5) and
decrements ERP stock for each sold line — idempotent (each order processed once,
pack-aware). A previously-decremented order that is later **cancelled/refunded** on
Shopify is automatically **restocked** on the next poll (once). **Turn this on
before you start tracking real shelf inventory**, otherwise a counter sale and a
web sale can both take the last unit.

Preview / run manually:
```
.venv\Scripts\python.exe -m scripts.sync_shopify_orders          # dry-run
.venv\Scripts\python.exe -m scripts.sync_shopify_orders --apply  # decrement
```

**Scope requirement:** the ERP custom-app token needs the **`read_orders`** scope.
Today's token has products + inventory + files only, so add `read_orders` (Shopify
Admin → Settings → Apps → Develop apps → your app → API scopes) and reinstall. Until
then the poll returns a clear scope error and changes nothing.

---

## 4. Locked-price margin alert (built — surfaced in the weekly audit)

When you hand-edit a price it's locked against the scraper (correct). The new
nightly/weekly check flags any *locked* price whose margin fell below
`shopify_margin_floor_pct` (default 15%) because vendor cost rose underneath it —
so a locked price can't silently sink below cost. Run on demand:
```
.venv\Scripts\python.exe -m scripts.audit_locked_margins          # report
.venv\Scripts\python.exe -m scripts.audit_locked_margins --apply  # flag needs_review
```

---

## 5. Hygiene — weekly live-refresh audit

Setting `shopify_weekly_audit_enabled` (default `0`). When on, once a week the ERP
reads every linked listing's **live** status from Shopify and then reconciles —
closing the stale-cache gap that previously let vendor-OOS parts stay live until
they happened to reappear in a section push. It also runs the locked-margin alert.

---

## Settings summary

| Setting | Default | Effect |
|---|---|---|
| `shopify_push_on_edit` | `1` | Auto-push a linked product to Shopify on Save |
| `shopify_availability_mode` | `hide` | `sold_out` enables the live-page model |
| `shopify_order_poll_enabled` | `0` | Poll Shopify orders → decrement ERP stock |
| `shopify_order_poll_interval_min` | `5` | Poll cadence (minutes) |
| `shopify_margin_floor_pct` | `15` | Locked-price below-margin alert threshold |
| `shopify_weekly_audit_enabled` | `0` | Weekly live-refresh + reconcile + margin alert |
