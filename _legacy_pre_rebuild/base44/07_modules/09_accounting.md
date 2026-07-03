# Module: Accounting

Sub-screens: Margins · QBO Sync Center · QBO Reconciliation · Aging AR · Reports.

**Existing code:** `jaks_inventory/ui/margin_screen.py`,
`sync_center.py`, `qbo_screen.py`, `aging_screen.py`, `reports_screen.py`

---

## Margins

Read-only analytics on profitability.

### KPI strip
- Gross margin % (rolling 30d)
- Gross profit $ (rolling 30d)
- Worst-margin SKU
- Best-margin category
- Discount % avg

### Views
- **By product:** sortable by margin %, total $, qty sold.
- **By category:** rollup with drill-down.
- **By customer:** which customers we make/lose money on.
- **By owner (salesperson):** per-rep performance.
- **Trend:** weekly margin % chart.

### Drill-down
Click any row → list of every line item that contributed, with cost / price / margin per line.

---

## QBO Sync Center

The operations dashboard for QuickBooks Online sync.

### KPI strip
- Mode (Mock / Read-only / Read-write) — banner
- Queue depth
- Failed (24h)
- Pushed (24h)
- Last successful sync at

### Queue table
Columns: queued_at, entity (Item/Customer/Invoice/...), op (create/update/void),
ref_id, attempt #, last_error, next_attempt_at, status.

Row actions: **Retry now**, **Mark resolved**, **Open in QBO**, **Open record**.

### Bulk operations
- **Sync All Customers** — push every customer not yet linked.
- **Sync All Items** — push every product.
- **Reconcile** — fetch QBO objects and detect drift vs local.

### Webhook events log
`qbo_webhook_events`: shows incoming webhooks, processed or not.

---

## QBO Reconciliation

Side-by-side: local record vs QBO record. Highlight diffs.

Filters: entity type, has-diff, date range.

Actions per diff: **Push local → QBO**, **Pull QBO → local**, **Ignore**.

Useful after period close and at month-end.

---

## Aging AR

### KPI strip
- Total open AR ($)
- Current ($)
- 1-30 ($)
- 31-60 ($)
- 61-90 ($)
- 90+ ($)

### Table
One row per customer with open balance, columns showing the 5 buckets +
total + days since last payment.

### Actions
- **Print Statement** for selected customers.
- **Email Statement** in batch.
- **Send Reminder SMS** using template.

---

## Reports

Library of saved reports. Each report = a query + a layout.

### Built-in reports
- Sales by day / week / month
- Sales by customer
- Sales by salesperson
- Sales by category / manufacturer / vendor
- Top SKUs (qty / $)
- Backorder report
- Slow-moving inventory (no sale in 180d)
- Dead stock ($ value sitting > 1yr)
- Vendor performance (on-time %, lead time)
- Tax collected
- Warranty claims
- Lost sales analysis

### Custom report builder
Pick a base entity (Invoice line, Quote line, Inventory snapshot) → drag
columns → set filters → save.

Export: CSV, PDF.
