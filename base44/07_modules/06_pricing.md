# Module: Pricing

Sub-screens: Price Lists · Pricing Maintenance · Tiered Pricing.

**Existing code:** `jaks_inventory/ui/price_lists_screen.py`,
`pricing_maintenance_screen.py`, `tiered_pricing_screen.py`,
`pricing_overview_screen.py`

Read alongside `03_business_rules.md#pricing`.

---

## Tiered Pricing (the heart of pricing)

A 3-axis grid editor:

```
                   ┌──── CATEGORIES ─────────────────────────┐
                   │  Filters   Turbos   Engine   Cooling    │
┌──── TIERS ──┐    ├─────────┬─────────┬─────────┬───────────┤
│ Retail      │ B1 │   0%    │   0%    │   0%    │   0%      │
│ Dealer      │ B1 │   8%    │  10%    │  10%    │   8%      │
│             │ B2 │  10%    │  12%    │  12%    │  10%      │
│ Fleet       │ B1 │  12%    │  15%    │  14%    │  12%      │
│             │ B2 │  15%    │  18%    │  17%    │  15%      │
│ Wholesale   │ B1 │  20%    │  22%    │  22%    │  20%      │
└─────────────┘    └─────────┴─────────┴─────────┴───────────┘
                       Bx = cost band (B1: $0–$50, B2: $50–$250 ...)
```

### Editor controls
- Add tier (row), Add category (column), Add band (row sub-group).
- Cell click → inline edit % with live preview chip showing example final price.
- "Copy from..." action: clone a tier's grid as the starting point for a new one.
- **Test pricing** panel on the side: enter SKU + customer → shows the
  resolved price with each step of the resolution explained.

### Manufacturer / vendor default mapping
Sub-tab to map `manufacturer → price_category` and `vendor → price_category`
so a product with no explicit category still resolves to one.

---

## Price Lists

A price-list is a frozen snapshot of (customer-tier × catalog) at a moment
for export to a customer.

### Actions
- **New Price List**: pick tier, pick category subset, pick effective date.
- Generate → produces a downloadable CSV/PDF.
- **Send to customer**: emails the PDF and records in `customer_notes`.

### Reuse
Previously generated price lists are kept (`price_lists` table with
`generated_at`, `tier_id`, `path`).

---

## Pricing Maintenance

Bulk operations on `products` pricing fields:

- Mass markup change: pick a vendor/category, set new markup %, **Preview**
  table shows old → new for each affected SKU, **Apply** commits.
- Stale cost report: SKUs with `cost_updated_at < threshold`. Bulk re-scrape
  via PAI or mark for vendor outreach.
- MAP enforcement audit: list SKUs where any tier's effective price is below
  `map_price`. Bulk fix or override per row.

### Always preview before apply.
Never apply mass changes without showing the row-by-row diff first.
