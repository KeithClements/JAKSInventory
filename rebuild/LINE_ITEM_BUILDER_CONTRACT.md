# Unified Line-Item Builder — UI Contract
*Backend · Published 2026-05-30*
*Owner: Backend lane · Audience: UI Builder (lane/ui-builder), UI Architect (lane/ui-architect)*

---

## What changed on the backend

The four document workspaces (Quote, SO, Invoice, PO) each had their own product
search and their own add-line quirks. The backend now provides **one** search
contract and **one** add-line behaviour so a single shared front-end line-adder can
serve all four.

New / changed surface area — **all additive, nothing currently working broke**:
- **1 new endpoint:** `GET /line-items/product-search` (JSON). This is the canonical
  search for every workspace.
- **Search normalization:** SKU / OEM / cross-ref / vendor-SKU matching is now
  separator- and case-insensitive — `"OK-1"`, `"ok1"`, `"OK 1"` all match the same
  part. Lives in **one** place (`SearchService` + `utils.normalize_part`).
- **Unified add-line contract:** all four `…/lines` POSTs accept the same field set,
  and the **service layer auto-fills** description / price / cost from the product —
  so an "immediate add-on-select" POST of just `product_id` + `qty` yields a complete
  line.
- The SO product-search 500 (`Product.name`) is fixed; `/sales-orders/_/product-search`
  no longer crashes.

The old per-document search endpoints still work (see Migration) so you can move one
screen at a time.

---

## 1. The single search endpoint

```
GET /line-items/product-search?q=<text>
```

- `q` < 2 chars → `[]`.
- Matches **SKU → OEM/cross-ref → vendor SKU → description**, ranked, separator/case
  insensitive. `match_type` tells you which matched.
- Backed by `SearchService.search_products`; max 8 results.

### JSON shape (one object per hit)

| Key | Type | Notes |
|---|---|---|
| `product_id` | int | |
| `sku` | str | canonical; `part_number` is a kept alias |
| `title` | str | canonical; `description` is a kept alias |
| `unit_cost` | float | canonical; `current_cost` is a kept alias |
| `suggested_sell` | float | sell price (markup or override) |
| `qty_on_hand` | int | |
| `qty_available` | int | on-hand − committed |
| `vendor_name` | str \| null | preferred vendor |
| `match_type` | str | `part_number` · `cross_ref` · `vendor_sku` · `description` |
| `cross_ref_number` | str \| null | the OEM/competitor number that matched |
| `last_sold_price` / `last_sold_date` | float/str \| null | most recent invoice line |
| `has_core` | bool | product carries a core charge |
| `vendor_core_charge` | float | core cost (PO) |
| `customer_core_charge` | float | core charge billed to customer (sales docs) |

> Both canonical keys (`sku`/`title`/`unit_cost`) and legacy aliases
> (`part_number`/`description`/`current_cost`) are present, so the existing quote
> line-adder keeps working unchanged. Prefer the canonical keys in new code.

`GET /quotes/product-search` is now a **deprecated thin alias** that returns this exact
shape — retire it once the quote workspace points at `/line-items/product-search`.

---

## 2. Add-line POST contract (per document)

| Document | URL | qty field | money fields | Returns (HTMX swap body) | Status guard |
|---|---|---|---|---|---|
| Quote | `POST /quotes/{id}/lines` | `qty` | `unit_price`, `unit_cost`, `discount_pct` | `_line_row.html` (or full `_lines_tbody.html` via `HX-Retarget` when a core child auto-adds) | DRAFT / SENT |
| Sales Order | `POST /sales-orders/{id}/lines` | `qty` *(legacy `qty_ordered` still accepted)* | `unit_price`, `unit_cost` | `sales_orders/_lines_section.html` | OPEN / PARTIAL |
| Invoice | `POST /invoices/{id}/lines` | `qty` | `unit_price`, `unit_cost` | `invoices/_lines_and_totals.html` | DRAFT only |
| Purchase Order | `POST /purchase-orders/{id}/lines` | `qty` *(legacy `qty_ordered` still accepted)* | `unit_cost`, `core_charge_per_unit` *(no sell price)* | `purchase_orders/_lines_section.html` | DRAFT / VERBAL |

Shared rules for all four:
- **`product_id` is optional.** Omit it for a free-text / misc line (send `description`).
- **Auto-fill:** when `product_id` is present and `description` / `unit_price` /
  `unit_cost` are blank or `0`, the service fills them from the product
  (`title`, `selling_price`, `cost`). POs are cost-only and also backfill
  `core_charge_per_unit` from `vendor_core_charge`.
- **Core children:** Quote and Invoice auto-add a locked child core line when the
  product has a core — expect more than one row back in that case.
- **Quote child-mode (unchanged):** `parent_line_id` + `line_role`
  (`upgrade_option`/`optional`/`warranty`/`suggested`) still drive sub-line inserts.

---

## 3. Immediate add-on-select — what the shared component must do

This is the front-end you (UI lane) own. The backend is ready for it.

1. Debounced `GET /line-items/product-search?q=…`; render the JSON results dropdown.
2. **On result click → immediately POST** `{ product_id, qty }` to the document's
   add-line URL (qty from a small stepper, default 1). No staging step.
3. Swap the returned partial into the document's lines region; reset the search box.
4. Qty / price / discount are edited **inline** afterward via the existing
   `…/lines/{line_id}` update routes.

Suggested config surface for one shared partial used by all four workspaces:

```
{
  searchUrl: "/line-items/product-search",
  postUrl:   "/<doc>/{id}/lines",
  mode:      "sell" | "cost",        // PO = cost; show unit_cost + core, hide sell
  showDiscount: true | false,        // false for PO
  target / swap: "<lines region selector>",   // per-doc returned partial
  childMode:  { parentLineId, lineRole }       // Quote only; optional
}
```

The current quote `lineAdder` (in `quotes/workspace.html`) is the working reference —
generalise it into the shared partial rather than starting from scratch.

---

## 4. Migration checklist (UI lane)

- [ ] Build **one** shared line-adder partial + JS (config above); include it in all
      four workspaces.
- [ ] Point each workspace's search at `GET /line-items/product-search` (Quote can drop
      `/quotes/product-search`).
- [ ] Once a screen is migrated, delete its old search partial + endpoint:
      `sales_orders/_product_search_results.html`,
      `purchase_orders/_product_search_results.html`,
      `invoices/_search_results.html`, and the `…/_/product-search` routes.
- [ ] Remove the now-redundant scattered de-dash patches (po_product_search,
      products-list search) — normalization lives once in `SearchService`.
- [ ] Retire the `/quotes/product-search` alias.

No visual redesign is required to adopt this — it's a plumbing swap. Style passes are
the Architect lane's call.

---

## Verification (backend, green as of 2026-05-30)

`tests/test_line_item_builder.py` covers: `normalize_part`; normalized SKU **and**
OEM/cross-ref search; the endpoint JSON shape incl. core fields; and immediate-add
(`product_id` + `qty` only) for all four document types.

```
.venv\Scripts\python.exe -m pytest tests/test_line_item_builder.py tests/test_smoke.py tests/test_smoke_subendpoints.py -v
```

The smoke sub-endpoint suite's previously **KNOWN-FAILING** SO product-search cases now
pass. (QA owns `test_smoke_subendpoints.py`; its "KNOWN FAILING" annotations for SO can
be dropped now that the crash is fixed.)
