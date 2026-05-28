# JAKS Inventory — Mockup & Modal Plan
*Created: 2026-05-23 | Source: Interview Sessions 1–4 + FIGMA_DESIGN_BRIEF.md*
*Use this alongside Figma. One section per screen. All modals and slide-overs listed.*

---

## How to Use This Document

For each screen:
- **Purpose** — one sentence
- **Entry points** — how you navigate here
- **Layout zones** — what goes where
- **Key content** — what must be visible without clicking
- **Actions** — buttons, shortcuts, conversions
- **Modals / slide-overs triggered** — what opens from here
- **Figma notes** — components needed, variants, states

Build in the priority order listed in Section 1.

---

## 1. Screen Priority

| Priority | Screen | Type | Complexity |
|---|---|---|---|
| 1 | App Shell (sidebar + header) | Layout | Medium |
| 2 | Quote Builder | Full screen | ⭐ Highest |
| 3 | Quote Pop-out Window | Full screen (stripped) | High |
| 4 | Customer Detail | Full screen | High |
| 5 | Dashboard | Full screen | Medium |
| 6 | Invoice Detail | Full screen | Medium |
| 7 | Sales Order Detail | Full screen | Medium |
| 8 | Core Management Workspace | Full screen | High |
| 9 | Global Search Overlay | Overlay | Medium |
| 10 | Products List + Detail | Full screen | Medium |
| 11 | Customers List | Full screen | Low |
| 12 | Vendors List + Detail | Full screen | Low |
| 13 | Purchase Order Detail | Full screen | Medium |
| 14 | Reports Hub | Full screen | Low |
| 15 | Settings | Full screen | Low |

---

## 2. Global Components (appear on every screen)

### 2.1 Sidebar
- Dark background (#1E2433), 240px fixed width
- Top: JAKS logo / brand mark
- Below logo: Recently Viewed (collapsible)
  - Shows last 5–10 records: [icon] [type] — [name/number] — [relative time]
  - Click → opens record
- Nav sections: SALES / PURCHASING / INVENTORY / CORES / REPORTS / SYSTEM
- Each nav item: 36px height, icon + label, hover state, active state (left accent bar)
- Section labels: 10px uppercase, muted color, 16px top margin

### 2.2 Header Bar
- 56px height, white, 1px bottom border
- Left: page title (inherits from current route)
- Center: Global Search input ("Search anything... Ctrl+K")
- Right: 📞 Log Call button + notification bell + user avatar
- "📞 Log Call" → triggers Quick Log Call slide-over from any screen

### 2.3 Auto-Save Indicator (quote screens only)
- Small, top-right of quote area
- States: "Saving..." (spinner) / "✓ Saved 5s ago" (green) / "⚠ Save failed — Retry" (red)

### 2.4 Toast Notifications
- Bottom-right corner, 4-second auto-dismiss
- Types: Success (green) / Error (red) / Warning (amber) / Info (blue)
- Example: "✓ Call logged for Mike's Diesel"

---

## 3. Screen Specs

---

### Screen 1 — Dashboard

**Purpose:** Morning health check and work queue at a glance.

**Entry:** App startup / click Dashboard in sidebar.

**Layout:**
```
┌─────────────────────────────────────────────────────────────────┐
│ Good morning, Keith. — Friday, May 23, 2026          [+ Quote] │
├──────────┬──────────┬──────────┬──────────┬────────────────────┤
│ TODAY'S  │  OPEN    │  OPEN    │  OPEN    │  QUOTES NEEDING   │
│ PAYMENTS │  QUOTES  │  SALES   │  CORES   │  FOLLOW-UP        │
│  $4,820  │   12     │  ORDERS  │   7      │   3 today          │
│  3 pmts  │ 4 expir. │    3     │ 2 overdue│  [View Queue]      │
├──────────┴──────────┴──────────┴──────────┴────────────────────┤
│ RESEARCH QUEUE                    LOW STOCK ALERTS              │
│  🟡 5 Researching                 JAKS-PAI-311148  QOH:0  Min:2│
│  🔵 8 Waiting Dealer              JAKS-PAI-285040  QOH:1  Min:2│
│  🔵 3 Waiting Vendor              [View All Low Stock]          │
│  ⚠️  2 Urgent / Truck Down                                      │
├────────────────────────────────────────────────────────────────┤
│ OVERDUE INVOICES                  RECENT CALL LOG               │
│  Mike's Diesel  INV-0042  47d     Keith  2h ago  Mike's Diesel │
│  Fleet Plus     INV-0039  31d     Keith  3h ago  Allied Diesel  │
│  [View AR Aging]                  [View All Calls]              │
├────────────────────────────────────────────────────────────────┤
│ RECENT INVOICES                                                  │
│  Allied Diesel  INV-0055  $3,420  Paid     05/23               │
│  Mike's Diesel  INV-0054  $1,140  Open     05/22               │
└─────────────────────────────────────────────────────────────────┘
```

**Key content (always visible, no scroll):**
- 4 metric cards: Today's Payments, Open Quotes (with expiring count), Open Sales Orders, Open Cores (with overdue count)
- Quotes Needing Follow-Up widget (count + [View Queue] button)
- Research Queue widget (counts by status)
- Overdue Invoices table (top 3-5 rows, link to AR Aging)
- Low Stock Alerts (top 3-5 rows)
- Recent Call Log (last 3-5 entries)
- Recent Invoices table

**Actions:**
- [+ Quote] — new quote button (top right, always visible)
- [View Queue] — opens Quote follow-up list
- [View AR Aging] — opens Reports screen filtered to AR Aging
- [View All Low Stock] — opens Products list filtered to low stock
- All table rows are clickable → open that record

**Figma notes:**
- Metric widget component: value + label + sublabel + optional alert color
- Use grid layout: 4 widgets top row, 2-column below
- Research Queue widget: colored status rows with counts
- Compact table component (32px rows, alternating tint)

---

### Screen 2 — Customers List

**Purpose:** Browse and search all customers; launch new quote.

**Entry:** Click "Customers" in SALES sidebar section.

**Layout:**
```
Customers                               [+ New Customer]
─────────────────────────────────────────────────────────
[Search customers...                 ] [Filter ▼] [Sort ▼]
─────────────────────────────────────────────────────────
NAME              PHONE        TERMS    BALANCE   STATUS
Mike's Diesel     402-555-0100  Net 30  $2,850  ⚠ Overdue
Allied Diesel     402-555-0200  Net 30    $420  Open
Fleet Plus        402-555-0300  COD         $0  Current
─────────────────────────────────────────────────────────
[AR Aging Report]  [Export CSV]
```

**Actions:**
- Click row → Customer Detail
- [+ New Customer] → new customer form
- Search bar → filters list live (HTMX)
- [AR Aging Report] → opens embedded report
- [+ New Customer] also accessible as slide-over from quote screen

**Figma notes:**
- Standard list/table component
- Status badge: Overdue (red) / Open (blue) / Current (green)
- Balance column: red if overdue amount > 0

---

### Screen 3 — Customer Detail

**Purpose:** Full customer record, account health, and activity history.

**Entry:** Click customer from list or search result or link from invoice/quote.

**Layout:**
```
← Customers    Mike's Diesel Service              [Edit] [+ Quote] [+ Invoice]
═══════════════════════════════════════════════════════════════════════════════
[ACCOUNT INFO]                    [ACCOUNT HEALTH]
Mike Johnson (Contact)            Open Balance:      $2,850.00
402-555-0100                      ⚠ Overdue:         $600.00  (47 days)
mike@mikesdiesel.com              Credit Balance:    $150.00 ✓
402 Industrial Blvd               Unapplied Pmts:    $0.00
Omaha, NE 68102                   Cores Outstanding: 1 ⚠
Terms: Net 30 | Tax: Exempt
Interest: 18%/yr | Credit Limit: $10,000
Last Purchase: 05/10/2026 ($3,420)    [Apply Credit]  [Record Payment]
═══════════════════════════════════════════════════════════════════════════════
[Quotes] [Sales Orders] [Invoices] [Payments] [Cores] [Call Log]
─────────────────────────────────────────────────────────────────
[Tab content loads here via HTMX — no page reload]
OPEN QUOTES:
Q-2026-0051  Inframe ISX  $1,140  Sent  Expires 06/22  [Open] [Convert]
Q-2026-0048  Head Gasket   $165   Draft               [Open] [Delete]

RECENT INVOICES:
INV-0042  $842   ⚠ Overdue 47d  Due 04/06  [Collect]
INV-0038  $1,200  Open          Due 05/30
INV-0031  $3,420  Paid          Paid 04/15

CALL LOG:
05/23  Keith — Outbound — Quoted ISX inframe        [+ Log Call]
05/20  Keith — Inbound  — Head gasket pricing query
```

**Key content visible without scroll:**
- Account info (left column)
- Account health panel (right column): open balance, overdue amount+days, credit balance, cores owed
- Quick action buttons: Apply Credit, Record Payment
- Tab bar for sub-sections

**Actions:**
- [+ Quote] → new quote pre-populated with this customer
- [+ Invoice] → new invoice pre-populated
- [Edit] → edit customer record
- [Apply Credit] → payment entry with credit method
- [Record Payment] → payment entry slide-over
- [Collect] → payment entry for overdue invoice
- [+ Log Call] → Quick Log Call slide-over (pre-filled with this customer)
- Tab bar → HTMX-loaded sub-panels

**Modals triggered from this screen:**
- Quick Log Call slide-over
- Record Payment slide-over
- Apply Credit modal

**Figma notes:**
- Two-column header: account info left, account health right
- Account health: large numbers, color-coded
- Tab bar: standard tab component with active state
- Each tab content: table rows, 32px height
- [Collect] button on overdue rows: primary action, red/orange

---

### Screen 4 — Quote Builder ⭐

**Purpose:** Live operational console for building quotes while customer is on the phone.
Target: 45-second quote, keyboard-only.

**Entry:** Click "Quotes" → [+ New Quote] / click existing quote / [+ Quote] from Customer Detail.

**Layout:**
```
┌─────────────────────────────────────────────────────────────────────┐
│ [⬡ Pop Out]  Quote Q-2026-0051           ✓ Saved 5s ago  [Send]   │
│                                     [Convert ▼] [Mark Lost]        │
├──────────────────────────────────────────────────────────────────┤
│ Customer: [ Mike's Diesel ▼ ] [+]   │ Quote #: Q-2026-0051        │
│ ┌────────────────────────────────┐  │ Date: 2026-05-23            │
│ │ Net 30 │ Open: $2,850         │  │ Valid: 30 days → 06/22       │
│ │ ⚠ Overdue: $600 │ ✓ Cr: $150 │  │ Follow-up: [__________]      │
│ │ Cores Owed: 1                  │  │ Discount: [0.0%]             │
│ └────────────────────────────────┘  │ Notes: [_______________]    │
│                                      │ Int. Notes: [_____________] │
├──────────────────────────────────────────────────────────────────┤
│ ADD PART: [ type OEM#, SKU, description, ESN... ▼ ]  [+ Add]     │
│           ↑ Ctrl+K or click · Arrow keys · Enter to add           │
├────┬─────────────────────────┬─────┬────────┬───────┬────────┬───┤
│  # │ DESCRIPTION             │ QOH │ SOURCE │  ETA  │ SELL $ │ % │
├────┼─────────────────────────┼─────┼────────┼───────┼────────┼───┤
│  1 │ JAKS-PAI-311148         │ ●2  │ STOCK  │  —    │ 950.00 │22%│
│    │ Inframe Kit ISX         │     │ PAI:In │       │        │   │
│    │ Last to Mike: $920 03/15│     │ HHP:3  │       │        │   │
├────┼─────────────────────────┼─────┼────────┼───────┼────────┼───┤
│  2 │ JAKS-PAI-285040         │ ●0  │ PAI:12 │ 2 days│ 165.00 │19%│
│    │ Head Gasket ISX         │     │ HHP:5  │       │        │   │
│    │ Last to Mike: $145 04/02│     │ ATL:2  │       │        │   │
├────┼─────────────────────────┼─────┼────────┼───────┼────────┼───┤
│  + │ Core: Head Gasket       │  —  │   —    │  —    │  25.00 │ — │
├────────────────────────────────────────────────────────────────────┤
│                              Subtotal: $1,140.00  Total: $1,140.00 │
├────────────────────────────────────────────────────────────────────┤
│ [ Follow Up Tomorrow ] [ Waiting Customer ] [ Waiting Vendor ]     │
│ [ Truck Down ]         [ No Follow Up    ]                          │
└─────────────────────────────────────────────────────────────────────┘
```

**Key content always visible:**
- Customer status mini-panel (account health inline)
- Quote line: QOH indicator, source/vendor availability, ETA, sell price, margin %
- Last sold price to this customer (shown in line row 2)
- Auto-save indicator
- Quick Follow-Up Bar (always at bottom)

**Actions:**
- [⬡ Pop Out] → opens quote in new window
- [Send] → marks quote SENT, prompts delivery method
- [Convert ▼] → dropdown: Convert to Sales Order / Convert to Invoice
- [Mark Lost] → opens lost reason prompt
- [+ Add] / Enter → adds product line
- Per-line `⋮` → Edit / Note / Core Charge / Move Up / Move Down / Delete
- Follow-up bar → one-click sets status + optional notes

**Search dropdown behavior:**
- Appears below search input as user types
- Shows: product name + SKU + QOH indicator + vendor availability + last sold to this customer
- Arrow keys navigate, Enter selects and adds to quote
- "Not found?" → option to add non-stocked item or start Research Item

**Modals / slide-overs triggered:**
- Quick Create Customer (from [+] next to customer field)
- Quick Create Product (from "add non-stocked item")
- Convert to SO lightweight popup
- Quick Follow-Up notes (inline expand, not a modal)

**Keyboard map:**
- Ctrl+K — focus search bar
- Enter — add selected line / advance to next field
- Tab — move between qty / price fields
- Ctrl+S — explicit save
- Ctrl+Enter — convert to SO
- Esc — close search dropdown / close slide-over

**Quote line table — updated structure:**

Each product line now has up to two rows:
```
│ SKU  │ Description (editable) │ Qty │ Price │ Disc% │ Total │ Marg │ ✕ │
│ Add: [+ CHIP-SKU] [+ CHIP-SKU ★] [+ Warranty ▼]                        │  ← chips row (if any)
```

Chips row only appears for product lines that have configured suggestions or a JAKS
extended warranty option. Rules:
- Recommended chips: gray rounded pill → one-click adds product line
- Required chips: amber pill with ★ → one-click adds product line
- Warranty chip: blue pill with shield icon → opens inline tier picker
- Warranty child lines: blue tint row, `🛡 WARR` badge in place of SKU, italic description
- Optional lines: amber tint row (no badge in Phase 1)

**Mockup layout with chips:**
```
┌──────────────────────────────────────────────────────────────────────┐
│ # │ DESCRIPTION          │ QTY │ SELL $  │ DISC │ TOTAL  │ MARG │ ✕ │
├───┼──────────────────────┼─────┼─────────┼──────┼────────┼──────┼───┤
│ 1 │ JAKS-PAI-311148      │  1  │ 950.00  │  0%  │ 950.00 │  22% │ ✕ │
│   │ Add: [+HEAD-BOLT] [+SPACER-PLATE ★] [+Warranty ▼]               │
├───┼──────────────────────┼─────┼─────────┼──────┼────────┼──────┼───┤
│🛡│ Extended Warranty:    │  1  │  95.00  │  0%  │  95.00 │   —  │ ✕ │
│  │ 12 Mo – Parts Only   │     │         │      │        │      │   │
├───┼──────────────────────┼─────┼─────────┼──────┼────────┼──────┼───┤
│ 2 │ JAKS-PAI-285040      │  1  │ 165.00  │  0%  │ 165.00 │  19% │ ✕ │
│   │ Add: [+HEAD-GASKET-KIT]                                          │
└──────────────────────────────────────────────────────────────────────┘
```

**Warranty tier picker (inline, anchored to + Warranty chip):**
```
                        ┌──────────────────────────────────┐
[+HEAD-BOLT] [+Warranty]│ Extended Warranty           [✕] │
                        ├──────────────────────────────────┤
                        │ 6 Mo – Parts Only       $95.00  │
                        │ Standard parts coverage  10%·6mo │
                        ├──────────────────────────────────┤
                        │ 12 Mo – Parts & Labor   $190.00  │
                        │ Full coverage / 100k mi 10%·12mo │
                        └──────────────────────────────────┘
```

**Figma notes:**
- Two-row line item component (main row + optional chips row)
- QOH indicator: colored dot + number
- Availability pills: small colored badges per vendor
- Margin % cell: green/yellow/red based on threshold
- Last-sold row: smaller text, muted color
- Follow-up bar: 5 pill buttons, highlight on selection
- Customer status panel: compact horizontal bar with colored values
- ChipRow: light gray tint sub-row with pill buttons
- SuggestionChip: gray (recommended) or amber+★ (required) variants
- WarrantyChip: blue variant with shield SVG icon
- WarrantyTierPicker: 288px dropdown panel, anchored below chip
- WarrantyLine: blue-tinted row with WARR shield badge in SKU cell

---

### Screen 5 — Quote Pop-Out Window

**Purpose:** Same as Quote Builder but in a standalone window — no sidebar, no header nav.

**Entry:** Click [⬡ Pop Out] on any quote.

**Layout:** Full Quote Builder minus sidebar and standard header. Replace header with:
```
┌──────────────────────────────────────────────────────────┐
│ JAKS Inventory — Quote Q-2026-0051  ✓ Saved 5s ago      │
│ [← Return to main app]                    [Send] [Conv] │
└──────────────────────────────────────────────────────────┘
```
Body: identical to Quote Builder (customer panel, lines, add bar, totals, follow-up bar).

**Figma notes:**
- Stripped layout variant — no sidebar, minimal top bar
- All quote builder components reused identically

---

### Screen 6 — Sales Order Detail

**Purpose:** Track committed order: what was ordered, what's received, what's invoiced.

**Entry:** "Sales Orders" nav item / from quote conversion / from customer detail.

**Layout:**
```
← Sales Orders   SO-2026-0018  Mike's Diesel    Status: [OPEN] [Invoice]
═══════════════════════════════════════════════════════════════════════
From: Q-2026-0051 | Customer PO: PO-45892 | ESN: 79485732
Payment: Full Payment — $950.00 collected (Check #1042)  Balance: $190.00
═══════════════════════════════════════════════════════════════════════
#  DESCRIPTION           ORD  RECV  INV'D  PRICE     TOTAL
1  Inframe Kit ISX        1    ✅1    0    $950.00   $950.00
2  Head Gasket ISX        1    ⏳0    0    $165.00   $165.00  *
3  Core: Head Gasket      1    —     —     $25.00    $25.00
═══════════════════════════════════════════════════════════════════════
* Line 2 not received — waiting on PO receipt before invoicing
Subtotal: $1,140 | Deposit collected: $950 | Balance due at invoice: $190

[Record Additional Payment]  [Create PO for Backorder Lines]  [Cancel SO]
```

**Key content:**
- Payment mode + amount collected
- Per-line ORD / RECV / INV'D columns
- Status indicators: ✅ received, ⏳ waiting, ❌ cancelled
- Warning on uninvoiceable lines

**Actions:**
- [Invoice] → creates invoice from fulfilled lines only
- [Create PO for Backorder Lines] → PO creation for vendor-sourced items
- [Record Additional Payment] → payment slide-over
- [Cancel SO] → cancellation with confirmation

**Modals triggered:**
- Convert SO → Invoice (lightweight, shows which lines are eligible)
- Record Payment slide-over

---

### Screen 7 — Invoice Detail

**Purpose:** The billing document. View, collect payment, void, push to QBO.

**Entry:** "Invoices" nav / from customer detail / from SO conversion.

**Layout:**
```
← Invoices   INV-2026-0055  Mike's Diesel   Status: [OPEN]
                                        [Record Payment] [PDF] [Push to QBO]
═══════════════════════════════════════════════════════════════
Customer PO: PO-45892 | ESN: 79485732 | Engine: ISX 450
Due: 2026-06-22 (Net 30) | From: SO-2026-0018
═══════════════════════════════════════════════════════════════
#  DESCRIPTION              QTY  PRICE      DISC   TOTAL
1  JAKS-PAI-311148            1  $950.00     0%   $950.00
   Inframe Kit ISX
2  JAKS-PAI-285040            1  $165.00     0%   $165.00
   Head Gasket ISX
3  Core charge: 285040        1   $25.00     —     $25.00
═══════════════════════════════════════════════════════════════
           Subtotal:                         $1,140.00
           Tax (Exempt):                         $0.00
           CC Surcharge (0%):                    $0.00
           TOTAL:                            $1,140.00
           Amount Paid:                       $950.00
           BALANCE DUE:                       $190.00
═══════════════════════════════════════════════════════════════
PAYMENT HISTORY
05/10  Check #1042  $950.00  Deposit (from SO-2026-0018)
═══════════════════════════════════════════════════════════════
[🔒 Invoice locked at 11:59 PM 05/10/2026 — To edit, void and reissue]
                                               [Void Invoice]
```

**Lock state:** Amber/grey banner, lock icon, all edit controls hidden, only Void remains.
**Edit after send (before EOD):** Warning banner "Invoice already sent to customer" + reason field → allow save.

**Actions:**
- [Record Payment] → payment entry slide-over
- [PDF] → generates printable PDF
- [Push to QBO] → syncs to QuickBooks Online
- [Void Invoice] → void with reason
- Customer name link → Customer Detail

**Modals triggered:**
- Record Payment slide-over
- Void Invoice confirmation (reason required)
- "Invoice already sent" warning + reason field (inline, not a modal)

---

### Screen 8 — Core Management Workspace

**Purpose:** Full core charge lifecycle management — customer returns, vendor returns, credits.

**Entry:** "Core Charges" in CORES sidebar section.

**Layout:**
```
Core Management
═══════════════════════════════════════════════════════════════════════
[Customer Owes Cores: 7] [Ready to Inspect: 3] [Ready to Credit: 2]
[Ready to Ship Vendor: 5] [Waiting Vendor Credit: 4] [Problem Cores: 1]
═══════════════════════════════════════════════════════════════════════
[All] [Customer Cores] [Ready to Inspect] [Ready to Ship Vendor]
      [Vendor Credits Pending] [Problem Cores] [Closed]

[Search cores...] [Filter by vendor ▼] [Filter by customer ▼]      [Receive Core] [New VCR Batch]
───────────────────────────────────────────────────────────────────────
SLIP#        CUSTOMER         PART#             QTY  STATUS           ACTION
CORE-0042   Mike's Diesel    JAKS-PAI-285040    1   ⏳ Awaiting       [Receive]
CORE-0039   Allied Diesel    JAKS-PAI-311148    2   🔍 Inspect        [Inspect]
CORE-0035   Fleet Plus       JAKS-PAI-285040    1   ✓ Credit Issued   [View]
```

**Status cards (top row):** Click any card → filters table to that status.

**Actions:**
- [Receive Core] → Receive Core slide-over (search by slip#)
- [New VCR Batch] → VCR Batch creation modal
- [Receive] → Receive Core slide-over pre-filled
- [Inspect] → Core Inspection modal
- Row click → Core Detail view (lifecycle timeline)

**Modals / slide-overs triggered:**
- Receive Core slide-over
- Core Inspection modal
- Core Credit modal
- VCR Batch modal
- Core Slip Print popup (from invoice — see Invoice screen)
- Vendor Credit Reconciliation modal

---

### Screen 9 — Global Search Overlay (Ctrl+K)

**Purpose:** Find any record instantly from any screen.

**Entry:** Ctrl+K from any screen / click search bar in header.

**Layout:**
```
[DIM BACKDROP — full screen]
┌──────────────────────────────────────────────────────────┐
│ 🔍  Search customers, parts, quotes, invoices...         │
│─────────────────────────────────────────────────────────│
│ CUSTOMERS                                                │
│  ● Mike's Diesel Service — 402-555-0100                 │
│  ● Mike's Fleet Repair                                   │
│                                                          │
│ PRODUCTS                                                 │
│  ● JAKS-PAI-311148 — Inframe Kit ISX                    │
│    QOH: 2 ●  PAI: In  HHP: 3   Cost: $742  Sell: $950  │
│    Last sold to Mike's Diesel: $920 on 03/15/2026       │
│  ● JAKS-PAI-285040 — Head Gasket ISX                    │
│    QOH: 0 ●  PAI: 12  HHP: 5   ETA: 2 days             │
│                                                          │
│ QUOTES                                                   │
│  ● Q-2026-0051 — Mike's Diesel — Sent                   │
│                                                          │
│ INVOICES                                                 │
│  ● INV-2026-0055 — Allied Diesel — ⚠ Overdue            │
│                                                          │
│  ↑↓ navigate  ↵ open  Esc close                         │
└──────────────────────────────────────────────────────────┘
```

**Behavior:**
- 560px wide, centered, max-height 480px
- Results appear as-you-type (150ms debounce)
- Grouped by type, max 3 per group, "Show all X" link
- Arrow keys navigate, Enter opens, Esc closes
- Products show: QOH indicator, vendor availability, cost, sell price, last sold price

---

### Screen 10 — Product Detail

**Purpose:** Full product record, pricing, inventory, vendor sources, cross-references, images.

**Entry:** "Products" nav / search result / quote line click.

**Layout:**
```
← Products    JAKS-PAI-311148 — Inframe Kit ISX       [Edit] [Enrich ▼]
═══════════════════════════════════════════════════════════════════════
[IMAGE]    SKU: JAKS-PAI-311148          QOH: 2    Committed: 1
           Category: Inframe Kits        On Order: 0
           Primary Vendor: PAI           Min Stock: 2  ⚠ At minimum
═══════════════════════════════════════════════════════════════════════
PRICING                    VENDOR SOURCES
Cost (PAI): $742           PAI — JAKS-PAI-311148  Preferred  $742
Markup: 28%                HHP — HHP-INF-ISX      Alt        $780
Sell Price: $950           ATL — N/A
Core (vendor): $120
Core (customer): $150
═══════════════════════════════════════════════════════════════════════
CROSS REFERENCES         INVENTORY TRANSACTIONS   SALES HISTORY
OEM: 3803748  (Proven)   [View Ledger]            Last sold: 03/15/2026
CMN: C3803748 (Found)                             Last price: $920
PAI: INF-ISX  (Vendor)                            Times sold: 14
[+ Add Cross Ref]
═══════════════════════════════════════════════════════════════════════
[Sales by Product Report]  [Inventory Valuation]
```

**Actions:**
- [Edit] → edit product fields inline or form
- [Enrich ▼] → dropdown: Enrich from PAI / HHP / ATL → scraper modal
- [+ Add Cross Ref] → inline cross-reference entry
- [View Ledger] → inventory transaction history
- [Sales by Product Report] → embedded report

---

### Screen 11 — Purchase Order Detail

**Purpose:** 3-way match: PO → Receipt → Vendor Bill.

**Layout:**
```
← Purchase Orders   PO-2026-0014  PAI Industries   Status: [SENT]
[Receive Shipment]  [Mark Received]  [PDF]  [Email to Vendor]
═══════════════════════════════════════════════════════════════════
Ordered: 05/20/2026 | Terms: Net 30 | Confirmation#: PAI-98421
═══════════════════════════════════════════════════════════════════
#  PART#                   ORDERED  RECEIVED  BILLED   COST
1  JAKS-PAI-311148           2        2         2      $742 ea
2  JAKS-PAI-285040           5        3         0      $125 ea  ⏳
═══════════════════════════════════════════════════════════════════
RECEIPTS: PO-REC-001  05/22  Qty: 2+3   [View]
VENDOR BILL: Not yet received
[Create Vendor Bill]
```

**Actions:**
- [Receive Shipment] → receipt entry slide-over
- [Create Vendor Bill] → vendor bill form
- [PDF] / [Email to Vendor] → document actions

---

### Screen 12 — Reports Hub

**Purpose:** Management and bookkeeping report center.

**Layout:**
```
Reports
═══════════════════════════════════════════════════════════════
Date Range: [05/01/2026 — 05/23/2026]  [Run Report ▼]
═══════════════════════════════════════════════════════════════
AR & RECEIVABLES          SALES                 INVENTORY
• AR Aging                • Sales by Customer   • Inventory Valuation
• Open Invoices           • Sales by Product    • Low Stock
• Overdue + Interest      • Quote Conversion    • Movement Report
                          • Lost Sales Log
PURCHASING                CORES                 ALL
• Open POs                • Core Charges        • [All Reports]
• Vendor Spend            • Vendor Credits
• Vendor Credits
═══════════════════════════════════════════════════════════════
[Selected report renders here with filter options + table + Export CSV]
```

---

## 4. Modal & Slide-over Specs

---

### Modal 1 — Quick Create Customer (Slide-over)

**Trigger:** Click [+] next to Customer field on Quote / Invoice screen.

**Width:** 360px, right-aligned, full height.

```
Quick Create: Customer                              [×]
────────────────────────────────────────────────────
Company Name *    [________________________________]
Contact Name      [________________________________]
Phone             [________________________________]
Email             [________________________________]
                                [Cancel] [Save & Select]
```

**On save:** Slide-over closes → new customer auto-selected in originating field → toast "Customer created."

---

### Modal 2 — Quick Create Product (Slide-over)

**Trigger:** "Add non-stocked item" from quote search / [+] next to product field.

```
Quick Create: Product                               [×]
────────────────────────────────────────────────────
SKU *             [JAKS-] [________________]
Title *           [________________________________]
Primary Vendor    [ Select Vendor ▼ ] [+]
Unit Cost         [$_______]
Markup %          [28%___] → Sell: $0.00 (live calc)
Has Core Charge   [toggle] → Core Amount: [$____]
                                [Cancel] [Save & Select]
```

---

### Modal 3 — Quick Create Vendor (Slide-over)

**Trigger:** [+] next to Vendor field on Product / PO screen.

```
Quick Create: Vendor                                [×]
────────────────────────────────────────────────────
Vendor Name *     [________________________________]
Vendor Code *     [____] (e.g. PAI, HHP, ATL)
Phone             [________________________________]
Account #         [________________________________]
                                [Cancel] [Save & Select]
```

---

### Modal 4 — Quick Log Call (Global Slide-over)

**Trigger:** "📞 Log Call" button in header (any screen).

```
Log a Call                                          [×]
────────────────────────────────────────────────────
Customer *        [Search by name or phone...     ]
Call Type         (•) Inbound  ( ) Outbound  ( ) Email
Outcome           [ Select outcome ▼              ]
                  Quoted / Order Placed / Follow-Up /
                  No Answer / Info Only / Other
Notes             [________________________________________]
                  [________________________________________]
Link to (optional) [ Quote ▼ ] [ Invoice ▼ ] [ Product ▼ ]
                                [Cancel] [Save & Close]
```

**On save:** Slide-over closes → toast "Call logged for [Customer]" → user remains on current screen.

---

### Modal 5 — Convert Quote to Sales Order (Lightweight Popup)

**Trigger:** [Convert ▼] → "Convert to Sales Order" on Quote Builder.

**Design principle:** Fast. Ask only what is operationally needed.

```
Convert to Sales Order                              [×]
────────────────────────────────────────────────────
Line 1: Inframe Kit ISX        QOH: 2  ✅ In stock
Line 2: Head Gasket ISX        QOH: 0  ⏳ Order from vendor

Source for backorder lines:
  (•) PAI   ( ) HHP   ( ) ATL   ( ) Other
  Create PO automatically?  [✓] Yes

Payment at this stage:
  ( ) Full payment now  (•) Deposit  ( ) Net terms / no payment now
  Deposit amount: [$_______]
  Payment method: [Cash ▼]

Customer PO#: [___________]  (optional)
ESN: [___________]  (optional)

                          [Cancel] [Create Sales Order]
```

---

### Modal 6 — Core Slip Popup (after Invoice with Core Items)

**Trigger:** Auto-prompt after finalizing invoice that contains core charge lines.

```
This invoice includes core items.                   [×]
────────────────────────────────────────────────────
Core Slip #: CORE-2026-0042 (auto-generated)

Items with core charges:
  • Head Gasket ISX (JAKS-PAI-285040) × 1 — Core: $25

The customer should return the core to receive credit.
Give them this slip number for faster receiving.

            [Print Core Return Slip]  [Skip — I'll handle it]
```

---

### Modal 7 — Receive Core (Slide-over)

**Trigger:** [Receive Core] on Core Management screen / [Receive] on core row.

```
Receive Customer Core                               [×]
────────────────────────────────────────────────────
Search by:  [Core Slip # / Customer / Invoice / Part / Phone]
            [CORE-2026-0042_______________________]
────────────────────────────────────────────────────
Found: Mike's Diesel — Head Gasket ISX — CORE-0042
Invoice: INV-2026-0055 | Expected credit: $25.00

Qty receiving: [1]
Location:      [ Core Shelf ▼ ] (core-only locations)

Notes:         [________________________________]
                                [Cancel] [Receive & Inspect]
```

---

### Modal 8 — Core Inspection Modal

**Trigger:** [Inspect] on core row / after receive.

```
Inspect Core                                        [×]
CORE-2026-0042 | Mike's Diesel | Head Gasket ISX
────────────────────────────────────────────────────
Inspection outcome:
  (•) Accepted
  ( ) Hold for Review
  ( ) Rejected
  ( ) Damaged — describe:  [_________________________]
  ( ) Wrong Core — notes:  [_________________________]
  ( ) Partial Credit        Credit amount: [$_______]

If Accepted or Partial — issue credit to customer:
  Credit method:
  (•) Account Credit (default)
  ( ) Issue Check
  ( ) Hold Pending Review

Credit amount: $25.00
                        [Cancel] [Save Inspection & Issue Credit]
```

---

### Modal 9 — Vendor Core Return (VCR) Batch Modal

**Trigger:** [New VCR Batch] on Core Management screen.

```
Create Vendor Core Return Batch                     [×]
VCR-2026-0018 (auto-generated)
────────────────────────────────────────────────────
Vendor: [ PAI Industries ▼ ]

Select cores to include:
  [✓] CORE-0039  Head Gasket ISX × 1   Exp. credit: $90
  [✓] CORE-0040  Head Gasket ISX × 2   Exp. credit: $180
  [ ] CORE-0041  Inframe Kit    × 1   Exp. credit: $350

Total expected credit: $270.00

RMA #:           [________________]
Tracking #:      [________________]
Shipped via:     [ UPS ▼ ]
Notes:           [________________________________]

                    [Cancel] [Create VCR & Print Shipment Doc]
```

**Shipment document (auto-generated PDF):**
Shows VCR#, JAKS info, vendor info, part list, expected credits, RMA, tracking.
Does NOT show customer names or customer invoices.

---

### Modal 10 — Vendor Credit Reconciliation Modal

**Trigger:** When recording vendor response on a VCR that differs from expected.

```
Record Vendor Decision — VCR-2026-0018              [×]
PAI Industries — 3 cores
────────────────────────────────────────────────────
Per-core outcomes:
  Head Gasket ISX × 1  Expected: $90   Actual: [$90 ] (•)Accepted ( )Rejected ( )Partial
  Head Gasket ISX × 2  Expected: $180  Actual: [$90 ] ( )Accepted ( )Rejected (•)Partial
  (difference: -$90)

Total expected: $270.00
Total actual:   [$180.00]
Difference:     -$90.00

Resolution for difference:
  ( ) Absorb by JAKS
  ( ) Charge to customer
  ( ) Dispute with vendor
  ( ) Write off

Notes: [_______________________________________________]
                              [Cancel] [Save & Close VCR]
```

---

### Modal 11 — Record Payment (Slide-over)

**Trigger:** [Record Payment] on Invoice Detail or Customer Detail.

```
Record Payment                                      [×]
Invoice: INV-2026-0055 | Balance due: $190.00
────────────────────────────────────────────────────
Amount:          [$190.00]
Payment method:  (•) Check  ( ) Cash  ( ) Card  ( ) Account Credit
Check #:         [________________]
Payment date:    [2026-05-23]
Notes:           [________________________________]

Apply CC surcharge (3%)?  [ ] Yes (+$5.70)
                                [Cancel] [Record Payment]
```

---

### Modal 12 — Invoice Edit Warning (inline, not a separate modal)

**Trigger:** User edits a sent-but-not-locked invoice.

**Behavior:** Banner appears at top of invoice form (not a blocking modal):
```
⚠ This invoice has already been sent to the customer.
   Reason for edit: [_________________________________]
   [Continue Editing]
```
User fills in reason → continues editing. Reason logged in audit trail.

---

## 5. Interaction Flows for Figma Prototyping

### Flow 1: Phone Call → Quote Sent
1. Dashboard → Ctrl+K → type "Mike's Diesel" → Enter → Customer Detail
2. Customer Detail → [+ Quote] → Quote Builder (customer pre-filled)
3. Quote Builder → type "311148" in Add Part → select from dropdown → Enter
4. Quote Builder → [Send] → delivery method → done

### Flow 2: Interrupted Quote (Pop-Out)
1. Quote Builder → [⬡ Pop Out] → Quote opens in new window
2. Main window → navigate to Products or new customer
3. Return to pop-out quote → continue where left off

### Flow 3: Quote → Sales Order (Out of Stock)
1. Quote Builder → [Convert ▼] → "Convert to Sales Order"
2. Lightweight popup → select vendor, deposit, payment → [Create SO]
3. Sales Order Detail opens → [Create PO for Backorder Lines] (optional)

### Flow 4: Core Return Lifecycle
1. Invoice finalised with core → Core Slip popup → [Print Core Return Slip]
2. Core Management → [Receive Core] → search CORE-2026-0042 → [Receive & Inspect]
3. Core Inspection modal → Accepted → Account Credit → [Save]
4. Core Management → Ready to Ship Vendor → [New VCR Batch] → select cores → [Create VCR]
5. Vendor responds → VCR record → [Record Decision] → Reconciliation modal → [Save]

### Flow 5: "What did I charge last time?"
1. Quote Builder → type "311148" in Add Part
2. Search dropdown shows: "Last sold to Mike's Diesel: $920 on 03/15/2026 | PAI | 24% margin"
3. User sees price history without leaving the quote screen

### Flow 6: Interrupted — Log Call from Any Screen
1. User on Vendors screen → header "📞 Log Call" → Quick Log Call slide-over opens
2. Search "Mike's Diesel" → select → type notes → [Save & Close]
3. Slide-over closes → user is back on Vendors screen → toast "Call logged"

---

*This document drives the Figma mockup build.*
*Update it as screens are designed and decisions change.*
*Cross-reference: FIGMA_DESIGN_BRIEF.md, QUOTING_REQUIREMENTS.md, UX_NAVIGATION_REQUIREMENTS.md*
