# Module: Core Processing

Sub-screens: Processing Dashboard · Customer Cores · Vendor Returns.

**Existing code:** `jaks_inventory/ui/processing_center_screen.py`,
`customer_cores_screen.py`, `vendor_returns_combined_screen.py`,
`vendor_cores_board.py`, `core_dialog.py`, `core_return_flow.py`
**Mockup:** `mockups/core_processing_mockup.html`

**Read alongside:** `03_business_rules.md#cores`, `04_core_processes.md#p4`.

---

## Processing Dashboard

### KPI strip
- Cores in (this week)
- Cores out (returned to vendor)
- Awaiting customer credit ($)
- Awaiting vendor credit ($)
- 90+ day aging ($)

### Attention chips
- `N customer cores 60-90d`
- `N customer cores 90+d`
- `N vendor obligations ready to ship`
- `N RGAs awaiting credit`

### Aging buckets table
Group `customer_cores` by `(due_back_by - now)` bucket:
- 0-30d (green)
- 30-60d (gold)
- 60-90d (amber)
- 90+ (red)

Click bucket → drill to Customer Cores filtered.

---

## Customer Cores

Detail view of outstanding `customer_cores` rows.

### Filters
`[Status ▾] [Customer ▾] [Aging bucket ▾] [Product ▾] [×]`

### Columns
Issued date, Due by, Customer, Invoice #, Product, Qty, Core $, Aging, Status.

### Row actions
- **Accept Return** → see [04_core_processes.md#p4a](../04_core_processes.md)
- **Mark Forfeited** (90+d only, requires admin confirmation)
- **Print Reminder** — PDF to mail/fax to customer

### Customer core return dialog
```
┌─ CUSTOMER CORE RETURN ──────────────────────────────────────┐
│  Customer: Acme Trucking                                    │
│  Outstanding cores (5):                                     │
│  ☐ INV-…831  Turbo HX35   $250.00   Due 2026-06-15  31 days │
│  ☐ INV-…802  Injector     $ 90.00   Due 2026-05-30  45 days │
│  ...                                                        │
│                                                             │
│  Returned serial (if required): [____________]              │
│  Condition: ⦿ Acceptable  ◯ Damaged  ◯ Wrong part           │
│  Notes: [________________________________________]          │
│                                                             │
│  Credit issued: $250.00 → ⦿ Store credit  ◯ Cash refund     │
│                                                             │
│  [Cancel]                              [Accept Return]      │
└─────────────────────────────────────────────────────────────┘
```

---

## Vendor Returns (RGAs)

**Existing code:** `vendor_returns_combined_screen.py`,
`vendor_cores_overview_screen.py`, `vendor_credits_screen.py`,
`rga_shipments_screen.py`

### KPI strip
- Draft RGAs ($)
- Awaiting vendor RGA #
- In transit ($)
- Awaiting credit ($)
- Credit received MTD ($)

### Vendor Cores Board

Two-pane:
- **Left:** vendors with outstanding obligations, count + $ value badge.
- **Right:** selected vendor's obligation rows.

Bulk select rows → **Create RGA** button enabled when ≥1 row selected.

### RGA lifecycle
`Draft → Submitted → Approved → Shipped → Credited`

Status transitions written to `vendor_return_audit`.

### RGA dialog
- **Header:** Vendor, RGA# (theirs), our internal #, type (core/warranty/overstock).
- **Lines:** the selected obligations, with editable expected credit per line.
- **Shipping:** carrier, tracking, weight, dimensions, ship date.
- **Credit reconciliation:** when vendor issues credit memo, enter actual
  credit. Difference between expected and actual creates a variance note.

### Vendor credits

When an RGA is credited, a `vendor_credit` is recorded and pushed to QBO as
a `VendorCredit`. Visible on Vendor detail → Credits tab. Can be applied to
future bills.
