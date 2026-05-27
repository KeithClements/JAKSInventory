# Module: Inventory

Sub-screens (sidebar order):

1. [Products](#products)
2. [Bulk Import](#bulk-import)
3. [Adjustments](#adjustments)
4. [Locations](#locations) (optional MVP)
5. [Kits](#kits)
6. [Audit](#audit)

The Products screen is the most important; everything else supports it.

---

## Products

**Existing code:** `jaks_inventory/ui/products_screen.py` (modal:
`product_workbench_dialog.py` + `product_workbench_sections.py`)
**Mockup:** `mockups/inventory_products_redesign.html`,
`mockups/products_and_inventory_tabs_redesign.html`,
`mockups/products_screen_fixes_v2.html`,
`mockups/product_workbench_plan.html`

### KPI strip

| Tile | Source |
|------|--------|
| Total SKUs | `count(products WHERE stage != 'archived')` |
| Inventory value | `sum(qty_on_hand * cost) WHERE qty_on_hand > 0` |
| Below reorder | count of `qty_on_hand + on_order < reorder_point` |
| Stale costs (>90d) | count of `cost_updated_at < now-90d` |
| Tagged for PO | count of `tagged_for_po = 1` |

### Attention chips

- `N below reorder` → filter to low stock
- `N awaiting Shopify publish` → `needs_shopify_update=1`
- `N missing OEM number` → empty `oem_part_number`
- `N missing images` → `image_count=0`
- `N stale PAI cost (>30d)` → `pai_cost_updated_at < now-30d`

### Filter row

`[Type ▾] [Vendor ▾] [Category ▾] [Status ▾] [Has image ▾] [Has core ▾] [×]`

Plus a sticky **search input** that searches SKU, title, OEM, vendor SKU, ESN
(via `search_by_part_number` service).

### Table columns

| Col | Source | Notes |
|-----|--------|-------|
| ☐ | selection | bulk actions |
| 🏷 | flag emojis | red=overdue restock, amber=stale cost, gold=tagged for PO |
| SKU | products.sku | bold |
| Title | products.title | clickable → open workbench |
| Type | condition | pill (NEW / REMAN / USED / CORE / KIT) |
| Vendor | preferred_vendor name | dim |
| Cost | products.cost | right-align, 2 dec |
| List | products.selling_price | right-align |
| Margin | (list-cost)/list * 100 | colored (green ≥25%, gold ≥10%, red <10%) |
| Qty | qty_on_hand | bold if 0, red if <reorder |
| On Order | sum open PO lines | dim |
| Reorder | reorder_point | inline-editable |
| Category | category | inline-editable |
| Updated | updated_at | relative |

Inline-edit: Cost, List, Reorder, Vendor, Category. Save on focus-out.

### Header actions

```
[ + New Product ]  [ More ▾ ]                [ ✨ AI Catalog Import ]  [ Import CSV ]
                    │
                    ├ Quick Entry        ← keyboard-fast bulk add
                    ├ Tag for PO         ← mark selected for next PO run
                    ├ Print Labels       ← barcode labels
                    ├ Bulk Edit          ← bulk_edit_dialog
                    ├ Bulk Adjust Qty    ← bulk_adjust_dialog
                    ├ Export             ← CSV
                    └ Sync to Shopify    ← force-push selected
```

### Product Workbench dialog

Replaces both `add_product_dialog.py` and `edit_product_dialog.py`. Two-pane
layout per the mockup `product_workbench_plan.html`:

```
┌─────────────────────────────────────────────────────────────────┐
│  ProductWorkbench — NEW PRODUCT / EDIT 'SKU'                    │
├──────────────┬──────────────────────────────────┬───────────────┤
│  SECTION     │                                  │   META PANEL  │
│  NAV         │       SECTION EDITOR             │   (right)     │
│              │                                  │               │
│  Basic*      │   form for the active section    │   NEW mode:   │
│  Core Charge │                                  │     progress  │
│  Platform    │                                  │     required  │
│  Supplier*   │                                  │     checklist │
│  Warranty    │                                  │   EDIT mode:  │
│  Pricing*    │                                  │     quick     │
│  Inventory   │                                  │     stats     │
│  Notes       │                                  │               │
│  Shipping    │                                  │               │
│  Xref        │                                  │               │
│  Suggested   │                                  │               │
│  Images      │                                  │               │
│  Shopify     │                                  │               │
├──────────────┴──────────────────────────────────┴───────────────┤
│  [Cancel]                       [Save Draft] [Save & Close]      │
└─────────────────────────────────────────────────────────────────┘
```

The 13 sections, in order:

| # | Section | Required? | Fields |
|---|---------|-----------|--------|
| 1 | **Basic** | ✓ | sku, title, condition, manufacturer, category, subcategory, brief_description, oem_part_number |
| 2 | **Core Charge** | — | has_core, core_sell_price, core_return_days, core_notes |
| 3 | **Platform** | — | oem_manufacturer, engine, truck_manufacturer, truck_model, truck_system |
| 4 | **Supplier** | ✓ | preferred_vendor_id, pai_sku, pai_link, vendor_description, vendor_availability, vendor_alternates, private_label, private_label_part_number |
| 5 | **Warranty** | — | supplier_warranty (enabled/value/unit), jaks_warranty (enabled/value/unit/charge), warranty_percentage |
| 6 | **Pricing** | ✓ | cost, pai_cost, selling_price, compare_at_price, map_price, price_category_id, plus PricingTiersTable (per-product qty tiers) |
| 7 | **Inventory** | — | qty_on_hand (read-only after create), reorder_point, min_qty, max_qty, bin_location, requires_serial, stage |
| 8 | **Notes** | — | notes_public, notes_internal |
| 9 | **Shipping** | — | weight, weight_unit, dimensions (L/W/H), dimension_unit, shipping_cost |
| 10 | **Xref** | — | list of `product_interchanges` with add/remove |
| 11 | **Suggested** | — | list of `suggested_sells` (cross-sell graph) |
| 12 | **Images** | — | icon-view grid of `product_images` with drag-reorder + import |
| 13 | **Shopify** | — | handle, shopify_status, tags, seo_title, seo_description, publish_shopify |

Each section is a self-contained widget exposing:
- `populate(product: dict)` — load values
- `collect() -> dict` — return DB-shaped kwargs
- `validate() -> list[str]` — return error list (empty = OK)
- `changed` signal

#### Meta panel (right)

**NEW mode:** completion progress (X of 13 sections filled), required checklist
(✓ / ○), an AI suggestions hint that activates when title ≥ 8 chars.

**EDIT mode:** Quick stats card (SKU/status/qty/inv value/price/cost/updated),
large color-coded margin %, activity counts (images/xrefs/suggested sells).

#### Shortcuts

| Combo | Action |
|-------|--------|
| `Ctrl + S` / `Ctrl + Enter` | Save & close |
| `Esc` | Reject |
| `Alt + ↑ / ↓` | Previous / next section |
| `Ctrl + /` or `Ctrl + F` | Focus section filter |
| `Ctrl + 1..5` | Jump to first section of group N |

### Behaviour rules

- SKU is immutable in EDIT mode.
- `update_product` must whitelist its kwargs to the actual `products` table
  columns to avoid OperationalError when a section emits a forward-looking key.
- Tier rows persist via separate `product_qty_tiers` table; the dialog calls
  `pricing.save_tiers(product_id)` after a successful save.

---

## Bulk Import

**Existing code:** `jaks_inventory/ui/bulk_import_screen.py`

### Purpose

Import a vendor's product catalog (PAI CSV, Cummins CSV, custom CSV) in one shot.

### Steps

1. Upload CSV or paste raw text.
2. Column mapping screen: drag each source column to a target field. Auto-suggest based on header names.
3. Validation: required fields present, type coercion (numbers/booleans), dedupe pass against existing SKUs.
4. Preview first 10 rows.
5. Commit → progress bar → results: created N, updated M, skipped K, errors with downloadable error CSV.

Bulk imports always run as background workers and write a `scrape_runs` entry
with source `manual_import`.

---

## Adjustments

**Existing code:** `jaks_inventory/ui/adjustments_screen.py`,
`jaks_inventory/ui/adjust_qty_dialog.py`, `jaks_inventory/ui/bulk_adjust_dialog.py`

### KPI strip

| Tile | Source |
|------|--------|
| Adjustments today | count |
| Net qty Δ today | sum |
| $ value Δ today | sum(qty_delta * cost) |
| Shrinkage (30d) | sum where reason in shrinkage codes |

### Table

Columns: timestamp, SKU, title, delta, reason, reason_code, user, notes, doc#.

### Add Adjustment dialog

Fields: product (picker), qty_change (±), reason_code (dropdown), reason_text,
notes. Reason codes: `RECEIVE`, `SHIP`, `RETURN`, `CYCLE_COUNT`, `DAMAGE`,
`THEFT`, `WRITE_OFF`, `TRANSFER`, `MANUAL`.

Saving creates `adjustments` row AND `inventory_audit` row AND calls
`adjust_qty` to update on-hand. Cross-window `inventory_changed` signal.

### Bulk

`bulk_adjust_dialog` accepts paste-from-spreadsheet (TSV) with columns
`sku, delta, reason, notes`. Same backend per row.

---

## Locations (optional MVP)

**Existing code:** `jaks_inventory/ui/locations_screen.py`

Single-warehouse mode is fine for MVP. Schema supports multi-warehouse via
`location_stock` join table. UI:

- Locations table: code, name, address, is_default.
- Per-location stock view: select location, see all SKUs with qty there.
- Transfers screen for moving stock between locations.

---

## Kits

**Existing code:** `jaks_inventory/ui/kits_screen.py`
**Mockup:** `mockups/inventory_kits_mockup.html`

A kit is a parent SKU whose sale **explodes** to multiple lines.

### Kit definition

- `kits.parent_product_id` → the SKU customers buy.
- `kit_components` rows: child product + qty per kit.
- `kits.explode_on_sale = 1` → at quote-line time, replace the kit line with
  expanded child lines (preserves parent reference via `parent_line_id`).
- If `explode_on_sale = 0`, the kit is sold as a single line and component
  qtys are deducted from on-hand when the kit ships.

### Screen

- Table of kits with: parent SKU, name, component count, total parts cost,
  build availability (= min over components of `floor(qty_on_hand / qty_per_kit)`).
- Detail panel: components table with qty editor.

---

## Audit

**Existing code:** `jaks_inventory/ui/audit_screen.py`

Read-only timeline of every `inventory_audit` row.

Filters: product, user, reason, date range.
Columns: timestamp, SKU, delta, qty_before, qty_after, reason, source_type,
source_id (click to jump to PO/SO/Invoice/Adjustment), user, notes.

KPI strip: events today / this week / this month, error events count.
