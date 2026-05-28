# JAKS Inventory — Figma Design Brief
*Generated 2026-05-23 from owner interviews, quoting requirements, and UX notes.*
*Use this doc alongside Figma. Update it when decisions change.*

---

## 1. Design Philosophy

**This is an operational tool, not a consumer app.**

- Dense information > spacious layouts
- Keyboard-first, mouse-optional
- Everything visible at a glance — no digging
- Fast enough to use while a customer is on the phone

Think: QuickBooks Pro meets an ERP ops console. Clean, professional, information-dense.

---

## 2. Design System

### Color Palette

| Token | Hex | Use |
|---|---|---|
| `sidebar-bg` | `#1E2433` | Sidebar background (dark slate) |
| `sidebar-hover` | `#2A3245` | Sidebar item hover |
| `sidebar-active` | `#3B4563` | Active nav item |
| `sidebar-text` | `#94A3B8` | Nav label (inactive) |
| `sidebar-text-active` | `#F1F5F9` | Nav label (active) |
| `sidebar-section` | `#64748B` | Section header label |
| `page-bg` | `#F8FAFC` | Main content area background |
| `card-bg` | `#FFFFFF` | Cards, tables, panels |
| `border` | `#E2E8F0` | Card borders, dividers |
| `text-primary` | `#0F172A` | Primary body text |
| `text-secondary` | `#64748B` | Labels, secondary info |
| `text-muted` | `#94A3B8` | Placeholder, muted |
| `accent` | `#2563EB` | Primary action (blue) |
| `accent-hover` | `#1D4ED8` | Button hover |
| `accent-light` | `#EFF6FF` | Accent tint (badge bg) |
| `success` | `#16A34A` | In stock, paid, accepted |
| `success-light` | `#F0FDF4` | Success badge bg |
| `warning` | `#D97706` | Follow-up needed, partial |
| `warning-light` | `#FFFBEB` | Warning badge bg |
| `danger` | `#DC2626` | Overdue, void, NSF |
| `danger-light` | `#FEF2F2` | Danger badge bg |
| `neutral` | `#475569` | Draft, pending |
| `neutral-light` | `#F1F5F9` | Neutral badge bg |

### Typography

| Style | Font | Size | Weight |
|---|---|---|---|
| Sidebar label | Inter | 12px | 500 |
| Sidebar section | Inter | 10px | 700 (uppercase) |
| Body | Inter | 14px | 400 |
| Body strong | Inter | 14px | 600 |
| Table header | Inter | 12px | 600 (uppercase) |
| Table cell | Inter | 13px | 400 |
| Page title | Inter | 20px | 700 |
| Card title | Inter | 15px | 600 |
| Badge | Inter | 11px | 600 |
| Monospace (doc#) | JetBrains Mono / Consolas | 13px | 400 |

### Spacing Scale
`4px` base unit. Common values: 4, 8, 12, 16, 20, 24, 32, 48.

### Border Radius
- Cards, panels: `8px`
- Buttons: `6px`
- Badges/pills: `4px`
- Input fields: `6px`
- Slide-over panel: `0` (flush to screen edge)

### Elevation / Shadow
- Cards: `box-shadow: 0 1px 3px rgba(0,0,0,0.08)`
- Dropdown / popover: `box-shadow: 0 8px 24px rgba(0,0,0,0.12)`
- Slide-over: `box-shadow: -8px 0 32px rgba(0,0,0,0.16)`
- Modal: `box-shadow: 0 20px 60px rgba(0,0,0,0.20)`

---

## 3. Status Badge System

Every entity has a status. Badges must be scannable at a glance.

### Quote Badges
| Status | Label | Color |
|---|---|---|
| DRAFT | Draft | Neutral |
| SENT | Sent | Accent (blue) |
| CONVERTED | Won | Success (green) |
| DECLINED | Lost | Danger (red) |
| EXPIRED | Expired | Warning (amber) |

### Invoice Badges
| Status | Label | Color |
|---|---|---|
| DRAFT | Draft | Neutral |
| OPEN | Open | Accent (blue) |
| PARTIAL | Partial | Warning |
| PAID | Paid | Success |
| VOID | Void | Danger |
| OVERDUE | Overdue | Danger — with clock icon |

### Sales Order Badges
| Status | Label | Color |
|---|---|---|
| OPEN | Open | Accent |
| PARTIAL | Partial | Warning |
| FULFILLED | Fulfilled | Success |
| CANCELLED | Cancelled | Danger |

### Purchase Order Badges
| Status | Label | Color |
|---|---|---|
| DRAFT | Draft | Neutral |
| SENT | Ordered | Accent |
| PARTIAL | Partial | Warning |
| RECEIVED | Received | Success |
| CANCELLED | Cancelled | Danger |

---

## 4. App Shell Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ SIDEBAR (240px fixed)  │  HEADER (full width, 56px)            │
│                        │  [ Global Search ── Ctrl+K ──────── ] │
│  JAKS Inventory        │  [ User / Settings ]                  │
│  ─────────────────     ├───────────────────────────────────────┤
│  Dashboard             │                                        │
│                        │  PAGE CONTENT AREA                    │
│  [Recently Viewed]         │  (scrollable, max-width ~1200px,      │
│   Q-2026-0051 · 2h ago    │   padding 24px)                       │
│   Mike's Diesel · 3h ago  │                                        │
│                            │                                        │
│  ── SALES ─────────────   │                                        │
│    Customers               │                                        │
│    Quotes                  │                                        │
│    Sales Orders            │                                        │
│    Invoices                │                                        │
│                            │                                        │
│  ── PURCHASING ─────────   │                                        │
│    Vendors                 │                                        │
│    Purchase Orders         │                                        │
│                            │                                        │
│  ── INVENTORY ──────────   │                                        │
│    Products                │                                        │
│                            │                                        │
│  ── CORES ──────────────   │                                        │
│    Core Charges            │                                        │
│                            │                                        │
│  ── REPORTS ────────────   │                                        │
│    Reports                 │                                        │
│                            │                                        │
│  ── SYSTEM ─────────────   │                                        │
│    Settings                │                                        │
│                            │                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Sidebar Detail
- Fixed left, full height, dark background (`#1E2433`)
- Logo/brand at top (40px height, 16px padding)
- Section labels: 10px uppercase, `#64748B`, 16px top margin
- Nav items: 36px height, 12px horizontal padding, 12px icons, hover state
- Active item: light bg highlight + left accent bar (3px wide, accent blue)
- Bottom: version number in muted text

### Header Detail
- 56px height, white background, 1px bottom border
- Global search bar: centered or left-aligned (see Screen 3)
- Right side: notification bell + user avatar dropdown
- Keyboard hint visible: `Ctrl+K` shown as a subtle pill inside the search bar

---

## 5. Screen 1 — Dashboard

**Purpose:** At-a-glance business health on first load.

```
┌─────────────────────────────────────────────────────────────────┐
│  Good morning, Keith.          Friday, May 23, 2026             │
├──────────┬──────────┬──────────┬──────────┬────────────────────┤
│ TODAY'S  │  OPEN    │  OPEN    │  OPEN    │  FOLLOW-UPS DUE   │
│ PAYMENTS │  QUOTES  │  SALES   │  CORES   │  TODAY            │
│  $4,820  │   12     │  ORDERS  │   7      │   3               │
│ 3 pmts   │ 4 expir  │    3     │ 2 overdue│  [View Queue]     │
└──────────┴──────────┴──────────┴──────────┴────────────────────┤
│                                                                  │
│  OVERDUE INVOICES                    LOW STOCK ALERTS           │
│  ──────────────────────────────      ──────────────────────     │
│  Shop Name          INV#    Days     SKU        QOH  Min        │
│  Mike's Diesel  INV-0042   47d  $842  JAKS-PAI-311148  0   2   │
│  Fleet Plus     INV-0039   31d $1,200  JAKS-PAI-285040  1   2  │
│  [View All A/R Aging]                [View All Low Stock]       │
│                                                                  │
│  RECENT INVOICES                     RECENT CALL LOG            │
│  ──────────────────────────────      ──────────────────────     │
│  Customer       INV#   Total  Status  Customer     When  Notes  │
│  Allied Diesel  0055  $3,420  Paid    Mike's...   1h ago Quoted │
│  ...                                 ...                        │
└─────────────────────────────────────────────────────────────────┘
```

### Widget Cards
- White card, 8px radius, subtle shadow
- Metric widget: large number (28px bold), label (12px muted), sub-label (12px)
- Alert colors: overdue invoices use red accent; follow-ups use amber
- Tables: compact, 32px row height, alternating row tint (`#F8FAFC`)

---

## 6. Screen 2 — Global Search (Ctrl+K Overlay)

**Behavior:** Pressing `Ctrl+K` or `/` from anywhere opens a centered overlay.

```
┌─────────────────────────────────────────────────────────────────┐
│ ░░░░░░░░░░░ DIMMED BACKDROP (50% opacity) ░░░░░░░░░░░░░░░░░░░░ │
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ ░░░░░  ┌──────────────────────────────────────────────┐  ░░░░ │
│ ░░░░░  │ 🔍  Search customers, parts, quotes...       │  ░░░░ │
│ ░░░░░  │                                              │  ░░░░ │
│ ░░░░░  ├──────────────────────────────────────────────┤  ░░░░ │
│ ░░░░░  │ CUSTOMERS                                    │  ░░░░ │
│ ░░░░░  │  ● Mike's Diesel Service — 402-669-1234      │  ░░░░ │
│ ░░░░░  │  ● Mike's Fleet Repair                       │  ░░░░ │
│ ░░░░░  │                                              │  ░░░░ │
│ ░░░░░  │ PRODUCTS                                     │  ░░░░ │
│ ░░░░░  │  ● JAKS-PAI-311148 — Inframe Kit ISX         │  ░░░░ │
│ ░░░░░  │    QOH: 2  Cost: $842  PAI: $890             │  ░░░░ │
│ ░░░░░  │  ● JAKS-PAI-311149 — Inframe Kit N14         │  ░░░░ │
│ ░░░░░  │                                              │  ░░░░ │
│ ░░░░░  │ QUOTES                                       │  ░░░░ │
│ ░░░░░  │  ● Q-2026-0042 — Mike's Diesel — Sent        │  ░░░░ │
│ ░░░░░  │                                              │  ░░░░ │
│ ░░░░░  │ INVOICES                                     │  ░░░░ │
│ ░░░░░  │  ● INV-2026-0099 — Allied Diesel — Paid      │  ░░░░ │
│ ░░░░░  │                                              │  ░░░░ │
│ ░░░░░  │  ↑↓ navigate  ↵ open  Esc close             │  ░░░░ │
│ ░░░░░  └──────────────────────────────────────────────┘  ░░░░ │
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
└─────────────────────────────────────────────────────────────────┘
```

### Behavior Notes
- Overlay: 560px wide, vertically centered, max-height 480px with scroll
- Results appear as-you-type (debounced 150ms)
- Grouped by type (CUSTOMERS, PRODUCTS, QUOTES, INVOICES, PURCHASE ORDERS)
- Each group shows max 3 results; more available via "Show all X results"
- Keyboard: `↑↓` moves selection, `Enter` opens selected record, `Esc` closes
- Product results show: SKU, name, QOH (colored: green ≥ 2, amber = 1, red = 0)
- No result found: "Nothing found for 'xyz'" + quick-action buttons (New Quote, New Customer)

---

## 7. Screen 3 — Quote Builder  ⭐ MOST IMPORTANT SCREEN

**This is a live operational console, not a form. Design accordingly.**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ New Quote         Q-2026-0051                          [Send] [Convert ▼]   │
│                                                        [Save Draft]  [Lost] │
├──────────────────────────────────┬──────────────────────────────────────────┤
│ Customer:  [ Mike's Diesel ▼ ] + │ Quote #: Q-2026-0051                    │
│ Notes:     [ ________________ ]  │ Date: 2026-05-23    Valid 30 days        │
│ Int. Notes:[ ________________ ]  │ Expires: 2026-06-22                     │
│                                  │ Follow-up: [__________]                  │
│                                  │ Discount: [0.0%____]                     │
├──────────────────────────────────┴──────────────────────────────────────────┤
│  ADD LINE: [ type SKU, part#, OEM#, description... ▼ ]  [+ Add]            │
│            ↑ Global search — type 3 chars → arrow to select → Enter         │
├────────────────────────────────────────────────────────────────────────────│
│  #  │ DESCRIPTION              │ QOH │ AVAIL    │  ETA  │  COMP $ │ SELL $ │ DISC │ MARGIN │ TOTAL  │ ⋮ │
├────┼──────────────────────────┼─────┼──────────┼───────┼─────────┼────────┼──────┼────────┼────────┼───┤
│  1  │ JAKS-PAI-311148          │ ●2  │ PAI:In   │  —    │  $890   │ 950.00 │  0%  │  22%   │ 950.00 │ ⋮ │
│     │ Inframe Kit ISX          │     │ HHP:3    │       │         │        │      │        │        │   │
│     │ [source: stock]          │     │ ATL:—    │       │         │        │      │        │        │   │
├────┼──────────────────────────┼─────┼──────────┼───────┼─────────┼────────┼──────┼────────┼────────┼───┤
│  2  │ JAKS-PAI-285040          │ ●0  │ PAI:12   │ 2 days│  $145   │ 165.00 │  0%  │  19%   │ 165.00 │ ⋮ │
│     │ Head Gasket ISX          │     │ HHP:5    │       │         │        │      │        │        │   │
│     │ [source: PAI]            │     │ ATL:2    │       │         │        │      │        │        │   │
├────┼──────────────────────────┼─────┼──────────┼───────┼─────────┼────────┼──────┼────────┼────────┼───┤
│  + Core:JAKS-PAI-285040       │     │          │       │         │  25.00 │      │        │  25.00 │   │
│     Core charge (attached)    │     │          │       │         │        │      │        │        │   │
├────────────────────────────────────────────────────────────────────────────┤
│                                                     Subtotal:   $1,140.00  │
│                                                     Tax (0%):       $0.00  │
│                                                     TOTAL:      $1,140.00  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Quote Line Row Design — Detailed Breakdown

Each line is **2 rows tall** to fit all required data:

**Row A (main):** `#` | Description + SKU (bold) | Qty | Sell Price (editable) | Disc % | Margin % | Line Total | Actions `⋮`

**Row B (context, smaller text, muted):**
- Left: Source badge `[STOCK]` / `[PAI]` / `[HHP]` / `[ATL]` + last sold hint `"Last: $920 on 03/15"`
- Middle: Vendor availability pills: `PAI: In Stock` `HHP: 3` `ATL: —`
- ETA: `2 days` (amber when >1 day, red when >5 days or unknown)
- Right: Competitor price `HHP: $890` (dim if we're lower, red-flagged if we're higher)

### QOH Indicator
- `●2` — green dot, number 2 — in stock
- `●1` — amber dot — low
- `●0` — red dot — out of stock (does NOT block the line)

### Availability Pills
Small inline pills next to each vendor:
- `PAI: In` — green
- `PAI: 12` — shows qty available
- `PAI: —` — grey/unavailable
- `ETA: 2d` — amber

### Quote Actions (top right)
- `[⬡ Pop Out]` — opens quote in separate window (icon button, top right)
- `[Send]` — primary button (blue), marks quote as sent
- `[Convert ▼]` — dropdown: "Convert to Sales Order" / "Convert to Invoice"
- `[Ctrl+S / Saved 5s ago]` — auto-save indicator (green checkmark + timestamp)
- `[Mark Lost]` — tertiary / danger link

### Quote Header — Customer Status Panel
Compact always-visible bar below customer name:

```
Terms: Net 30  |  Open: $2,850  |  ⚠ Overdue: $600  |  ✓ Credit: $150  |  Cores Owed: 1
```
- Overdue > 0 → orange/red badge on that value
- Credit > 0 → green badge
- Cores Owed > 0 → orange badge
- Does NOT block quoting — informs judgment only

### Quick Follow-Up Bar (always visible, quote bottom)
```
[ Follow Up Tomorrow ]  [ Waiting Customer ]  [ Waiting Vendor ]  [ Truck Down ]  [ No Follow Up ]
```
One-click → sets follow_up_date + status. Optional 2-line notes expand inline.
Saves automatically. No extra screen required.

### Line Item Contextual Menu `⋮`
- Edit line
- Add note below
- Add core charge
- Move up / Move down
- Delete line

### Add Line Row Behavior
1. Focus jumps to add-line search input (auto-focus on page load)
2. User types: `311` → dropdown of matching products appears (inline, not modal)
3. Arrow keys to navigate, Enter to select → line added
4. If no match: option to "Add non-stocked item" (opens minimal form inline)
5. Qty and price are editable directly in the line — Tab through fields

---

## 8. Screen 4 — Customer Detail

```
┌─────────────────────────────────────────────────────────────────┐
│  ← Customers     Mike's Diesel Service              [Edit] [+Quote]│
├──────────────────────────────┬──────────────────────────────────┤
│  ACCOUNT INFO                │  ACCOUNT HEALTH                  │
│  ──────────────────────────  │  ──────────────────────────────  │
│  Mike Johnson                │  Open Balance:    $4,820.00      │
│  402-669-1234                │  Overdue:         $1,842.00 ⚠   │
│  mike@mikesdiesel.com        │  Oldest: 47 days                 │
│  402 Industrial Blvd         │  Credit Balance:      $0.00      │
│  Omaha, NE 68102             │  Unapplied Pmts:      $0.00      │
│                              │                                  │
│  Terms: Net 30               │  [Apply Credit] [Record Payment] │
│  Tax: Exempt                 │                                  │
│  Interest: 18% / yr          │  Core Charges Outstanding: 2     │
│  Credit Limit: $10,000       │  [View Cores]                    │
├──────────────────────────────┴──────────────────────────────────┤
│  [Quotes] [Sales Orders] [Invoices] [Payments] [Cores] [Calls]  │
├─────────────────────────────────────────────────────────────────┤
│  OPEN QUOTES                                                     │
│  Q-2026-0051  Inframe Kit ISX  $1,140  Sent    Expires 6/22 ↗  │
│  Q-2026-0048  Head Gasket set    $165  Draft              ↗    │
├─────────────────────────────────────────────────────────────────┤
│  RECENT INVOICES                                                 │
│  INV-2026-0042  $842   [Overdue 47d]  Due 04/06  [Collect]     │
│  INV-2026-0038  $1,200 [Open]         Due 05/30               ↗│
│  INV-2026-0031  $3,420 [Paid]         Paid 04/15              ↗│
├─────────────────────────────────────────────────────────────────┤
│  CALL LOG                                    [Log Call]          │
│  05/23 Keith — Outbound — Quoted ISX inframe                    │
│  05/20 Keith — Inbound — Asked about head gasket pricing        │
└─────────────────────────────────────────────────────────────────┘
```

### Layout Notes
- Left/right split: account info + health panel (side by side)
- Tab bar below for switching between Quotes / Invoices / Payments / etc.
- Each tab loads its content via HTMX (no page reload)
- Account health numbers: large, color-coded (overdue in red, zero in green)
- "Collect" quick-action on overdue invoices (direct to payment entry)

---

## 9. Screen 5 — Sales Order Detail

```
┌──────────────────────────────────────────────────────────────────────┐
│  SO-2026-0018        Mike's Diesel Service     Status: [OPEN]        │
│  From: Q-2026-0051                                        [Invoice]  │
├──────────────────────────────────────────────────────────────────────┤
│  PAYMENT MODE: Full Payment                                          │
│  Deposit Collected: $950.00 (Check #1042)    Balance: $190.00       │
├──────────────────────────────────────────────────────────────────────┤
│  #  │ DESCRIPTION              │ ORD │ RECV │ INV'D │ PRICE  │ TOTAL │
│──────────────────────────────────────────────────────────────────────│
│  1  │ JAKS-PAI-311148          │  1  │  1   │   1   │ $950   │ $950  │
│     │ Inframe Kit ISX          │     │  ✅  │       │        │       │
│  2  │ JAKS-PAI-285040          │  1  │  0   │   0   │ $165   │ $165  │
│     │ Head Gasket ISX          │     │  ⏳  │       │        │  *    │
│  3  │ Core: Gasket (deposit)   │  1  │  —   │   —   │  $25   │  $25  │
├──────────────────────────────────────────────────────────────────────┤
│  * Line 2 not yet received — cannot invoice until receipt confirmed  │
│                                                                      │
│  Subtotal: $1,140   Deposit: $950   Balance due: $190               │
└──────────────────────────────────────────────────────────────────────┘
```

### Key Concepts to Represent
- `ORD` = qty ordered, `RECV` = qty received via PO receipt, `INV'D` = qty invoiced
- ✅ = received/ready, ⏳ = waiting on vendor, ❌ = cancelled
- "Invoice" button creates an invoice from fulfilled lines only
- Deposit status visible at top (full / partial / none)

---

## 10. Screen 6 — Invoice Detail

```
┌──────────────────────────────────────────────────────────────────────┐
│  INV-2026-0055       Mike's Diesel Service     Status: [OPEN]        │
│  Due: 2026-06-22  (Net 30)                    [Record Payment] [PDF] │
├──────────────────────────────────────────────────────────────────────┤
│  Customer PO#: PO-45892    ESN: 79485732    Engine: ISX 450         │
├──────────────────────────────────────────────────────────────────────┤
│  #  │ DESCRIPTION              │ QTY │ PRICE   │ DISC │  TOTAL      │
│──────────────────────────────────────────────────────────────────────│
│  1  │ JAKS-PAI-311148          │  1  │ $950.00 │  0%  │  $950.00    │
│     │ Inframe Kit ISX          │     │         │      │             │
│  2  │ JAKS-PAI-285040          │  1  │ $165.00 │  0%  │  $165.00    │
│     │ Head Gasket ISX          │     │         │      │             │
│  3  │ Core charge: 285040      │  1  │  $25.00 │  —   │   $25.00    │
├──────────────────────────────────────────────────────────────────────┤
│                              Subtotal:              $1,140.00        │
│                              Tax (exempt):              $0.00        │
│                              CC Surcharge (0%):         $0.00        │
│                              TOTAL:                 $1,140.00        │
│                              Amount Paid:             $950.00        │
│                              BALANCE DUE:             $190.00        │
├──────────────────────────────────────────────────────────────────────┤
│  PAYMENT HISTORY                                                     │
│  05/10  Check #1042   $950.00  Deposit (from SO-2026-0018)          │
├──────────────────────────────────────────────────────────────────────┤
│  [🔒 Invoice locked at 11:59 PM on 05/10/2026]                       │
│  To make changes, you must Void and Reissue.      [Void Invoice]    │
└──────────────────────────────────────────────────────────────────────┘
```

### Lock State Visual
When locked: subtle top banner, light amber or grey tint, lock icon
"Locked" badge replaces edit affordances — only "Void" is available

---

## 11. Inline Creation Slide-over Pattern

**Used for: Quick Customer, Quick Product, Quick Vendor**

```
┌─────────────────────────────────────────────────────────────────┐
│  [MAIN PAGE — slightly dimmed by backdrop]          │           │
│                                                     │  QUICK    │
│  Customer: [ __________________ ] [+]               │  CREATE   │
│            ↑ user clicked +                         │  CUSTOMER │
│                                                     │  ─────────│
│                                                     │  Company* │
│                                                     │  [_______]│
│                                                     │           │
│                                                     │  Contact  │
│                                                     │  [_______]│
│                                                     │           │
│                                                     │  Phone    │
│                                                     │  [_______]│
│                                                     │           │
│                                                     │  Email    │
│                                                     │  [_______]│
│                                                     │           │
│                                                     │  [Cancel] │
│                                                     │  [Save &  │
│                                                     │   Select] │
└─────────────────────────────────────────────────────────────────┘
```

### Slide-over Rules
- Width: 360px, right-aligned, full height
- Backdrop: semi-transparent dark overlay behind slide-over
- Main content slightly blurred/dimmed — user knows context is preserved
- Header: "Quick Create: Customer" with × close button
- Form: required fields only (starred), clean vertical stack
- [Save & Select]: on success → closes slide-over, auto-selects the new record in the originating field
- [Cancel]: closes immediately, no changes
- ESC key closes the slide-over

### Quick Create Forms

**Quick Customer:**
- Company Name* (required)
- Contact Name
- Phone
- Email
- [Save & Select]

**Quick Product:**
- SKU* (required, auto-prefix JAKS-)
- Title*
- Vendor [dropdown] [+]
- Unit Cost
- Markup % → calculates Sell Price live
- Has Core Charge [toggle] → Core Amount field appears
- [Save & Select]

**Quick Vendor:**
- Vendor Name* (required)
- Phone
- Account #
- [Save & Select]

---

## 12. Navigation — LOCKED (Session 4, 2026-05-23)

```
Dashboard
[Recently Viewed — last 5–10 records, collapsible]

── SALES ──────────────────
  Customers
  Quotes
  Sales Orders              ← own item (active work queue)
  Invoices

── PURCHASING ──────────────
  Vendors
  Purchase Orders

── INVENTORY ──────────────
  Products

── CORES ──────────────────
  Core Charges

── REPORTS ─────────────────
  Reports

── SYSTEM ─────────────────
  Settings
```

All decisions locked. Do not reopen.

---

## 13. Key Reusable Components Inventory

Build these as Figma components with variants:

| Component | Variants |
|---|---|
| StatusBadge | DRAFT / OPEN / PARTIAL / PAID / VOID / SENT / WON / LOST / OVERDUE |
| Button | Primary / Secondary / Danger / Ghost / Icon-only |
| InputField | Default / Focus / Error / Disabled / With-icon |
| TableRow | Default / Hover / Selected / Subtotal |
| SearchBar | Header variant / Overlay variant / Inline variant |
| SlideoverPanel | Customer / Product / Vendor |
| AvailabilityPill | In Stock / N available / Out / ETA |
| QuoteLineRow | Normal / Core-charge / Note / Warranty / Optional / Hovered / Editing / WithChips |
| NavItem | Default / Active / Hover |
| MetricWidget | Single metric / Metric + trend / Metric + alert |
| Modal | Confirm / Alert / Form |
| Toast | Success / Error / Warning |
| QuickFollowUpBar | Default / One-clicked / With-notes-expanded |
| AutoSaveIndicator | Saving / Saved / Error |
| CustomerStatusPanel | Clean / Overdue-warning / Credit-available / Cores-owed |
| PopOutButton | Default / Hover |
| QuickLogCallSlideover | Empty / Customer-selected / Saved |
| RecentlyViewed | Collapsed / Expanded / With-5-items |
| CoreStatusCard | Customer-owes / Ready-to-inspect / Ready-to-credit / Ready-to-ship / Waiting-vendor / Problem |
| CoreInspectionModal | Default / Accepted / Rejected / Partial |
| VCRBatchModal | Select-cores / Review / Confirm |

---

## 14. Interaction Patterns for Figma Prototyping

### Quote Line Add Flow
1. User focuses Add Line input
2. Typing triggers dropdown (simulate with Figma overlay)
3. Arrow key selection + Enter → line appears in table
4. Cells become editable on click/tab

### Slide-over Open/Close
1. Click `+` → backdrop fades in (opacity 0→40%)
2. Panel slides in from right (translateX 100%→0)
3. On [Save & Select] → panel slides out, dropdown updates

### Global Search
1. Ctrl+K → backdrop + search overlay appears
2. Typing shows grouped results (use Figma variants for empty/results states)
3. Row hover → highlighted
4. Enter → navigate to record (new screen in prototype)

---

## 15. Screen Priority for Figma Build Order

1. **App Shell** (sidebar + header + recently viewed + global search bar)
2. **Quote Builder** — most complex, most important
   - Customer status panel
   - Quick follow-up bar
   - Auto-save indicator
   - Pop-out variant
   - Convert to SO lightweight popup
3. **Customer Detail** — second most used daily
4. **Dashboard** — first thing seen on login; includes follow-up widget + research queue
5. **Invoice Detail** — with lock state, payment history
6. **Slide-overs** — Quick Create Customer / Product / Vendor + Quick Log Call (global)
7. **Global Search overlay** (Ctrl+K)
8. **Sales Order Detail**
9. **Core Management Workspace** — tabs + status cards + inspection modal + VCR batch modal
10. **Purchase Order**
11. **Product Detail**
12. **Reports Hub** + embedded report panels in Customers/Products/Vendors screens

---

## 17. Suggested Sells & Warranty — Quote Line Chips (Session 6)

*Added 2026-05-23. These specs apply to the Quote Builder screen (Screen 3/4).*

### Quote Line Row — Full Structure (Updated)

Each product line in the quote table now occupies **up to three visual rows**:

```
┌──────────────────────────────────────────────────────────────────┐
│ ROW A — MAIN LINE                                                │
│  SKU     │ Description (editable) │ Qty │ Price │ Disc │ Tot │ ✕ │
├──────────────────────────────────────────────────────────────────┤
│ ROW B — CHIPS (only when product has configured suggestions)     │
│  Add:  [+ HEAD-BOLT] [+ GASKET-KIT ★] [+ Warranty ▼]           │
└──────────────────────────────────────────────────────────────────┘
```

**Row B (chips) rules:**
- Only appears for `line_type = 'product'` lines
- Hidden when the product has no configured suggestions and is not warrantable
- Warranty child lines (`line_type = 'warranty'`) never have a chips row

---

### Chip Types

| Chip | Style | Behavior |
|---|---|---|
| Recommended | Gray rounded pill, white bg, `+ SKU` | One-click → appends new line |
| Required ★ | Amber pill, amber bg, `+ SKU ★` | One-click → appends new line |
| + Warranty | Blue pill, blue-50 bg, shield icon | Opens inline tier picker dropdown |

**Chip row styling:**
- `bg-gray-50/50` — very light gray tint to visually separate from the main row
- `border-b border-gray-100` — same bottom border as regular rows
- Chips are small pill buttons, 12px text, 10px vertical padding
- "Add:" label in muted gray precedes the chip group

---

### Warranty Tier Picker (Inline Dropdown)

Clicking `+ Warranty` opens a dropdown panel anchored below the chip:

```
┌─────────────────────────────────────┐
│ Extended Warranty              [✕]  │
├─────────────────────────────────────┤
│ 6 Mo – Parts Only          $95.00  │
│ Standard parts coverage  10% · 6mo │
├─────────────────────────────────────┤
│ 12 Mo – Parts Only        $190.00  │
│ Extended parts coverage 10% · 12mo │
├─────────────────────────────────────┤
│ 12 Mo – Parts & Labor     $190.00  │
│ Full coverage / 100k mi  10% · 12mo│
├─────────────────────────────────────┤
│ 24 Mo – Parts & Labor     $380.00  │
│ Full coverage / 100k mi  10% · 24mo│
└─────────────────────────────────────┘
```

- Width: 288px (w-72)
- Each tier is a full-width button with hover state
- Left: tier name (bold) + subtitle (muted, 12px)
- Right: calculated price (mono, bold) + formula breakdown (muted, 12px)
- Only tiers ≤ `product.jaks_warranty_months` are shown
- Selecting a tier closes the dropdown and adds a WARRANTY child line below the parent

---

### Warranty Child Lines

When a warranty tier is selected, a new line appears in the table:

```
┌──────────────────────────────────────────────────────────┐
│ [🛡 WARR]  Extended Warranty: 12 Mo – Parts & Labor   1  │
│ (blue tint row, italic description, blue WARR badge)     │
└──────────────────────────────────────────────────────────┘
```

- Row background: `bg-blue-50/40` (light blue tint)
- SKU column: blue shield badge reading "WARR" instead of a SKU
- Description: italic, blue text (`text-blue-700`)
- Price, discount, total, margin all work normally (editable inline)
- These lines are visually indented conceptually but NOT indented structurally (full table width)

---

### Optional Lines (System 1)

Lines flagged `is_optional = true` get a subtle amber tint:

- Row background: `bg-amber-50/20`
- No badge — optional status is implied by color only in Phase 1
- Use case: warranties, accessories, optional labor, install kits, expedited freight

---

### New Components Needed in Figma

| Component | Variants |
|---|---|
| ChipRow | Empty (hidden) / Recommended-only / WithWarranty / Mixed |
| SuggestionChip | Recommended (gray) / Required (amber, star) / Warranty (blue, icon) |
| WarrantyTierPicker | Open / Closed / NoTiersConfigured |
| WarrantyTierButton | Default / Hovered |
| QuoteLineRow | (existing, add) Warranty / Optional variants |

---

## 16. New Screens Added Since Initial Brief (Session 4)

| Screen / Component | Type | Notes |
|---|---|---|
| Quote Pop-out Window | Full screen (stripped layout) | No sidebar; full quote builder |
| Quick Follow-Up Bar | Component | Always visible at quote bottom |
| Customer Status Mini-Panel | Component | Always visible in quote header |
| Auto-Save Indicator | Component | "Saved 5s ago" / Saving... / Error |
| Quick Log Call Slide-over | Slide-over | Global; accessible from any screen |
| Core Management Workspace | Full screen | 6-tab workspace with status cards |
| Core Inspection Modal | Modal | Inspection outcome + credit method |
| Vendor Core Return Batch | Modal | VCR-2026-XXXX creation + print |
| Vendor Credit Reconciliation | Modal | Expected vs. actual + resolution |
| Core Receive Slide-over | Slide-over | Search by slip# / customer / invoice |
| Core Slip Print Popup | Popup | After invoice with core items |
| Recently Viewed Panel | Sidebar component | Collapsible; last 5–10 records |
| Convert Quote → SO Popup | Lightweight popup | Only operational questions |
| Record Conflict Warning | Toast / modal | Soft warning if same record edited |

---

*This document is the source of truth for Figma design work.*
*Cross-reference with QUOTING_REQUIREMENTS.md and UX_NAVIGATION_REQUIREMENTS.md for detail.*
