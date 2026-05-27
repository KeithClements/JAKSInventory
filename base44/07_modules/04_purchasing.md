# Module: Purchasing

Sub-screens: Purchase Orders · PO Receipts · Vendors · Low Stock & Reorder.

---

## Purchase Orders

**Existing code:** `jaks_inventory/ui/po_screen.py`, `po_dialog.py`,
`po_receipts_screen.py`

### KPI strip
- Open POs ($)
- Sent this week
- Awaiting receipt
- Overdue (past expected_date)
- Avg lead time (days)

### Attention chips
- `N draft POs` — not yet sent
- `N overdue receipts`
- `N partial receipts >7d` — needs follow-up
- `N cores due for RGA`

### Filter row
`[Status ▾] [Vendor ▾] [Date range ▾] [Linked to SO ▾] [×]`

### Table columns
PO#, Vendor, Status pill, Lines, Total, Ordered, Expected, Received %.

### PO dialog
```
┌─ HEADER ──────────────────────────────────────────────────────┐
│  Vendor ▾   Terms   Ship to ▾   Ship method   Expected date   │
├─ LINES ───────────────────────────────────────────────────────┤
│  + Add line │ Suggest from Low Stock │ Import from SO         │
│  SKU  Title  Qty  Cost  Core Charge  Total  ETA  Notes        │
├─ LANDED COSTS ────────────────────────────────────────────────┤
│  Freight  Duty  Broker  Allocation (value/qty/weight)         │
├─ TOTALS ──────────────────────────────────────────────────────┤
│  Subtotal  Tax  Freight  Total                                │
├─ ACTIONS ─────────────────────────────────────────────────────┤
│  [Save Draft] [Send PO] [Email Vendor] [Receive] [Cancel PO]  │
└───────────────────────────────────────────────────────────────┘
```

### PO lifecycle states
`Draft → Sent → Acknowledged → Partial → Received → Closed` (or `Cancelled`).

State transitions are tracked in `purchase_orders.status_log` (or a dedicated
`po_status_log` table).

### Vendor communication
Stored in `vendor_po_communication` (migration 056). UI: a comment thread per
PO with email-out / email-in attribution.

---

## PO Receipts

Sub-tab of POs in the desktop app (same screen). For Base44, can be a
separate sidebar item with the same data:

### Receive dialog (one PO)
- One row per PO line with `qty_to_receive` defaulting to remaining qty.
- If `requires_serial_receive`: prompt for N serial numbers.
- Per-line: condition notes, partial accept toggle.
- Save → `purchase_receipts` + `purchase_receipt_lines` + adjusts on-hand +
  opens vendor core obligations for REMAN lines.

### Multi-receipt: receive partials over multiple visits
- Same PO can have several `purchase_receipts` rows.
- Each updates `po_lines.qty_received` incrementally.

### Print packing slip / GRN
Generate a PDF Goods Received Note for filing.

---

## Vendors

**Existing code:** `jaks_inventory/ui/vendors_screen.py`, `vendor_dialog.py`,
`vendor_import_dialog.py`

### KPI strip
- Active vendors
- Open POs across all vendors ($)
- On-time % (rolling 90d)
- Avg lead time
- Vendor credits outstanding ($)

### Filter row
`[Vendor type ▾] [Terms ▾] [Active ▾] [×]`

### Vendor dialog
- Tabs: Profile, Terms & Payments, Communication, Price Categories, Cores, History.
- **Profile:** name, code, contact, phone, email, website, address, notes.
- **Terms:** payment_method, terms (`Net30`/`COD`/etc), cutoff_time_local, lead_time_days, carrier.
- **Price Categories:** which `price_categories` this vendor supplies (drives discount grid).
- **Cores:** outstanding `vendor_core_obligations`, threshold settings.
- **History:** all POs, all RGAs, all credits.

---

## Low Stock & Reorder

**Existing code:** `jaks_inventory/ui/low_stock_screen.py`,
`jaks_inventory/ui/restock_screen.py`, `restock_wizard_dialog.py`

### Table
Columns: SKU, Title, Vendor, Qty, On Order, Reorder Pt, Suggested Order Qty.

`suggested_order_qty = max(reorder_point - qty - on_order, MOQ)`.

### Action: Create POs from Tagged
1. User ticks rows OR uses **Auto-tag below reorder** button.
2. **Create POs** → wizard:
   - Group tagged SKUs by `preferred_vendor_id`.
   - Show one draft PO per vendor, with the lines pre-populated.
   - User reviews/edits per-vendor cost, qty.
   - Confirm → POs are created in `Draft` status.
3. User then opens each draft PO to send.

### Smart suggestions (optional)
A "Suggest reorder qty" button looks at:
- 90-day average daily sales
- Lead time days
- Safety stock factor
- Min order qty
Returns a recommended `target_qty` per SKU.
