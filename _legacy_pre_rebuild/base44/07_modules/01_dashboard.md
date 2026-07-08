# Module: Dashboard

**Path in existing app:** `jaks_inventory/ui/dashboard_screen.py`
**Mockup:** `mockups/main_window_dashboard_redesign.html`

## Purpose

A 30-second read of the business. Should answer: *Are we OK today?*

## Layout

Use the standard list-screen grammar but skip the data table — the dashboard
is composed entirely of KPI tiles + small lists.

```
┌──── TODAY ───────────────────────────────────────────────────────┐
│  Revenue   Invoices   Payments   New Quotes   Conversion %       │
│  $4,328    7          $3,915     12           58%                │
└──────────────────────────────────────────────────────────────────┘

┌──── ATTENTION ───────────────────────────────────────────────────┐
│ ( 3 quotes follow-up due ) ( 5 invoices overdue ) ( 12 cores 90+d )│
│ ( 2 POs awaiting send ) ( 7 SKUs below reorder ) ( QBO 1 failed ) │
└──────────────────────────────────────────────────────────────────┘

┌──── RECENT ──────────────────────┐  ┌──── PERFORMANCE THIS WEEK ──┐
│ • SO-2026-00045 created          │  │ Revenue this week  $32,118  │
│ • Invoice INV-…831 paid $1,200   │  │ Gross margin       24.7 %   │
│ • PO-2026-00112 received         │  │ AR added           $8,910   │
│ • RGA-PAI-0008 shipped           │  │ AR collected       $7,205   │
│ ...                              │  └──────────────────────────────┘
└──────────────────────────────────┘
```

## KPI tiles (default 5)

| Label | Value | Delta |
|-------|-------|-------|
| Revenue today | sum of invoice payments today | vs yesterday |
| Invoices today | count finalized today | vs yesterday |
| Payments today | sum of invoice_payments today | n/a |
| New quotes | count quotes created today | n/a |
| Conversion % | won quotes / sent quotes (rolling 30d) | vs prev 30d |

Each tile is clickable and deep-links to a filtered list.

## Attention chips

Computed from these queries:

| Chip | Source |
|------|--------|
| `N quotes follow-up due` | `quotes WHERE follow_up_at <= now AND status='Sent'` |
| `N invoices overdue` | `invoices WHERE due_date < now AND balance > 0` |
| `N cores 90+ days` | `customer_cores WHERE due_back_by < now-90d AND status='outstanding'` |
| `N POs awaiting send` | `purchase_orders WHERE status='Draft'` |
| `N SKUs below reorder` | `products WHERE qty_on_hand + on_order < reorder_point` |
| `QBO N failed` | `qbo_sync_queue WHERE status='failed'` |

## Recent activity feed

Read from a unified events stream (`get_recent_events` in `db/inventory.py`).
Each entry: timestamp, icon, title, subtitle, deep link.

Event kinds:
- `so_created`, `so_shipped`, `so_invoiced`
- `invoice_paid`, `invoice_overdue`
- `po_sent`, `po_received`, `po_partial`
- `quote_sent`, `quote_won`, `quote_lost`
- `rga_shipped`, `rga_credited`
- `adjustment`, `cycle_count`
- `qbo_error`

## Refresh

Every 60 s background poll. Manual refresh button in title bar. Subscribe to
all cross-window signals.
