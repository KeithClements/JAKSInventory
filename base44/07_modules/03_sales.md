# Module: Sales

Sub-screens: Quotes · Lost Sales · Sales Orders · Invoices · Deliveries · CRM · Returns

This file covers Quotes/SOs/Invoices in detail; the others are summarized.
Returns are detailed in `04_core_processes.md#p5`.

---

## Quotes

**Existing code:** `jaks_inventory/ui/quotes_screen.py`, `quote_dialog.py`
**Mockup:** `mockups/new_quote_modal_mockup.html`

### KPI strip
- Open quotes ($)
- Sent this week
- Won this week ($)
- Conversion % (rolling 30d)
- Avg days to close

### Attention chips
- `N follow-ups due` (`follow_up_at <= now AND status='Sent'`)
- `N expiring this week`
- `N awaiting deposit`
- `N no contact in 7d`

### Filter row
`[Status ▾] [Owner ▾] [Customer type ▾] [Source ▾] [Age ▾] [×]`

### Table columns
Q#, Customer, Owner, Total, Lines, Status pill, Follow-up, Age, Last contact.

### Quote dialog

```
┌─ HEADER ────────────────────────────────────────────────────────┐
│  Customer ▾   Contact ▾   ESN/VIN [_____]   Manufacturer job ☐  │
├─ LINES ─────────────────────────────────────────────────────────┤
│  + Add part │ Part Finder │ Suggested sells                     │
│  SKU  Title  Qty  Price  Disc%  Core  Warranty  Total  Notes    │
│  ─── core charge child line indented under parent if has_core   │
├─ TOTALS ────────────────────────────────────────────────────────┤
│  Subtotal  Tax  Shipping  Discount  Deposit  Grand Total        │
├─ OPTIONS ───────────────────────────────────────────────────────┤
│  ETA   Ship method   Tax rate   Warranty offered   Deposit %    │
│  Lost reason (when status=Lost)                                 │
├─ COMMENTS / FOLLOWUP ───────────────────────────────────────────┤
│  Free-text   Next follow-up at   Owner                          │
├─ ACTIONS ───────────────────────────────────────────────────────┤
│  [Cancel]  [Save Draft] [Send Quote] [Convert to SO] [Mark Lost]│
└─────────────────────────────────────────────────────────────────┘
```

Part Finder integration is mandatory: SKU autocomplete searches products,
crossrefs, vendor SKUs, and ESN history simultaneously.

### Multi-option quotes

A quote can carry 2-3 alternative configurations (`quote_options` table). The
dialog has an "Add option" tab. Customer chooses one before conversion.

### Lost reason capture

When status flipped to Lost, modal asks for `lost_reason_code` and optional
notes. Codes: `Price`, `Lead time`, `Found locally`, `No longer needed`,
`Competitor: HHP`, `Competitor: ATL`, `Other`. Writes `lost_sales` row.

---

## Lost Sales

**Existing code:** `jaks_inventory/ui/lost_sales_screen.py`

Read-only analytics: who/what we lost and why.

KPI strip: lost count this week / month / year, $ value, top reason.
Filters: reason, owner, customer, date range.
Drill-down: click reason → all losses for that reason grouped by week.

---

## Sales Orders

**Existing code:** `jaks_inventory/ui/sales_orders_screen.py`, `new_so_dialog.py`,
`so_detail_dialog.py`, `pack_and_ship_dialog.py`, `pick_dialog.py`
**Mockup:** `mockups/sales_orders_mockup.html`

### KPI strip
- Open SOs ($)
- Shipped this week ($)
- Backordered lines
- Avg days SO→Invoice
- Drop-ship pending

### Attention chips
- `N backordered lines` — needs PO
- `N awaiting pick`
- `N shipped, not invoiced` (>24h)
- `N partial shipments`

### Filter row
`[Status ▾] [Ship method ▾] [Customer ▾] [Backorder ▾] [×]`

### Detail dialog

Same body as quote dialog plus:
- **Pick ticket** print button
- Per-line state (pending / allocated / picked / packed / shipped)
- Pack & Ship modal: enter carrier, tracking, ship date
- **Convert to Invoice** button (allowed when any line shipped or
  invoice-on-order configured)

### Drop-ship lines

A line marked drop-ship from vendor creates a PO line on save. The PO is
linked via `purchase_orders.linked_so_id`. Pre-receipt the SO line shows a
"shipped from vendor" badge.

---

## Invoices

**Existing code:** `jaks_inventory/ui/invoices_screen.py`, `invoice_dialog.py`,
`payment_dialog.py`, `invoice_return_dialog.py`, `refund_credit_dialog.py`
**Mockup:** `mockups/invoices_mockup.html`

### KPI strip
- Open AR ($)
- Paid today ($)
- Overdue ($ and count)
- Avg days to pay
- Today's invoices count

### Attention chips
- `N overdue`
- `N >30 days`
- `N >60 days`
- `N >90 days`
- `N awaiting credit memo`

### Filter row
`[Status ▾] [Customer ▾] [Date range ▾] [Has balance ▾] [Has core ▾] [×]`

### Detail dialog

Same body as SO with these additions:
- **Take Payment** button
- Payments tab: list of `invoice_payments` rows
- **Print** / **Email** / **SMS** invoice
- **Refund / Credit** button (manager-only)
- **Void** (admin-only, sets `status='Void'` and reverses inventory + QBO)

### Payment dialog
Fields: method (`cash`/`card`/`check`/`ach`/`credit`), amount, reference,
CC convenience fee (auto for cards if configured), notes. Save creates
`invoice_payments` row, updates balance, pushes Payment to QBO.

### Statement / Aging integration
Each invoice's age bucket is computed live (`days_open = today - finalized_at`)
and rolls up to Aging AR screen.

---

## Deliveries

**Existing code:** `jaks_inventory/ui/deliveries_screen.py`,
`jaks_inventory/ui/daily_route_screen.py`,
`jaks_inventory/ui/delivery_fuel_dialog.py`

Tracks SOs that are being delivered by JAK's own trucks (not common carrier).
Map view optional for MVP — table-only is acceptable.

Columns: SO#, Customer, Address, Driver, ETA, Status (queued/in_transit/delivered),
Distance, Fuel.

---

## CRM

**Existing code:** `jaks_inventory/ui/crm_screen.py`

Pipeline / activity feed view of customer engagement. For MVP, consider
folding into Customers Hub. If kept standalone:

- Pipeline columns: New / Quoted / Negotiating / Won / Lost
- Activity feed per customer: every quote / call note / SMS
- Tasks: "follow up with Acme on quote Q-2026-00012"

---

## Returns

**Existing code:** `jaks_inventory/ui/returns_screen.py`,
`jaks_inventory/ui/invoice_return_dialog.py`

KPI strip: returns this week (count + $), restocking fee revenue, defect rate.
Attention chips: `N awaiting inspection`, `N restocking fee waived`.

Flow detailed in `04_core_processes.md#p5`.
