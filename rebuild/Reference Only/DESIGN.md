# JAKS Inventory — Design Specification
*Living document — updated as each screen is finalized*
*Last updated: 2026-05-24 | Status: Authoritative*

> **Read this before touching any template.**
> The companion research document is `researchdesign.md`.

---

## 1. Design Philosophy

JAKS Inventory is an **operational console**, not a marketing website or consumer app.
Keith builds quotes while a customer waits on the phone. His wife records payments while reviewing a check.
Every design decision must reduce friction for these two operators — not add visual interest.

**The core principle:** *Boring works.*

- Consistency over creativity
- Density over whitespace
- Predictability over surprise
- Speed over polish

**The mental model:** Bloomberg Terminal meets QuickBooks.
Dark sidebar. Dense white content. Monospace identifiers. Olive green accent. No gradients. No animations beyond transitions that communicate state.

**What we are not building:**
- A SaaS landing page
- A consumer mobile app
- A marketing dashboard

---

## 2. Brand Identity

### Logo
```
┌─────────────────────────────────┐
│  JAKS Inventory                 │  ← sidebar logo area (h-16, px-4)
│  ════ ══════════                │
│  white  brand-300               │
└─────────────────────────────────┘
```
- "JAKS" → `text-white font-bold text-xl tracking-tight`
- "Inventory" → `text-brand-300` (#b0be71 — warm readable olive on dark bg)

### Color Palette

| Name       | Hex       | Tailwind Class  | Primary Use |
|------------|-----------|-----------------|-------------|
| brand-50   | #f4f5e9   | `bg-brand-50`   | Very light tint backgrounds |
| brand-100  | #e6e9cc   | `bg-brand-100`  | Badge backgrounds (badge-brand) |
| brand-200  | #cdd399   | `text-brand-200`| Rarely used |
| brand-300  | #b0be71   | `text-brand-300`| Logo text, sidebar accents (on dark) |
| brand-400  | #94a84e   | `border-brand-400`, `focus:ring-brand-400` | Focus rings, active borders |
| brand-500  | #788436   | —               | Mid-range, rarely needed |
| brand-600  | #5e6928   | `hover:bg-brand-600` | Button hover state |
| **brand-700** | **#4b5320** | `bg-brand-700`, `text-brand-700` | **PRIMARY — buttons, active states, links** |
| brand-800  | #363c18   | `hover:bg-brand-800` | Pressed state |
| brand-900  | #232710   | `text-brand-900`| Rarely used |

**Semantic colors (never replace with brand):**

| Color  | Semantic Meaning | Badge Example |
|--------|-----------------|---------------|
| Green  | Active, Paid, Confirmed, In-stock, Preferred | `bg-green-100 text-green-800` |
| Red    | Error, Overdue, Cancelled, Rejected, 0-stock | `bg-red-100 text-red-800` |
| Amber  | Pending, Partial, Draft, Waiting | `bg-amber-100 text-amber-800` |
| Blue   | Informational, Sent, In-progress | `bg-blue-100 text-blue-800` |
| Gray   | Inactive, Historical, Closed | `bg-gray-100 text-gray-600` |
| Teal   | Special credit actions (warranty credits) | `bg-teal-100 text-teal-700` |
| Purple | Follow-up: vendor-related status (waiting on vendor) | `bg-purple-100 text-purple-800` |

### Surface Colors

| Surface          | Color          | Class            |
|-----------------|----------------|------------------|
| Page background  | #f9fafb        | `bg-gray-50`     |
| Sidebar          | #0f172a        | `bg-slate-900`   |
| Header bar       | #ffffff        | `bg-white`       |
| Cards            | #ffffff        | `bg-white`       |
| Card headers     | #f9fafb ~60%   | `bg-gray-50/60`  |
| Table headers    | #f9fafb        | `bg-gray-50`     |
| Input background | #ffffff        | `bg-white`       |
| Active nav bg    | #1e293b        | `bg-slate-800`   |

---

## 3. Typography Rules

### Rule 1 — Identifiers are always monospace
Part numbers, SKUs, invoice numbers, quote numbers, PO numbers, claim numbers → **always** `font-mono`.
Reason: operators scan lists quickly; monospace ensures consistent character width for visual alignment.

```html
<!-- Correct -->
<span class="font-mono text-brand-700">14-1234</span>
<span class="font-mono text-xs text-brand-700">INV-2026-0041</span>

<!-- Wrong — never use for identifiers -->
<span class="font-medium text-brand-700">14-1234</span>
```

### Rule 2 — Currency is always right-aligned + tabular-nums
```html
<td class="px-4 py-1.5 text-right tabular-nums text-sm text-gray-700">$1,240.00</td>
```

### Rule 3 — Table headers are uppercase small-caps
```html
<th class="px-4 py-2.5 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
  Part Number
</th>
```

### Font Size Hierarchy

| Element | Size | Weight | Color | Notes |
|---------|------|--------|-------|-------|
| Page heading (h1) | text-base (14px) | font-semibold | text-gray-900 | In header bar |
| Card section title | text-sm (13px) | font-semibold | text-gray-800 | card-title |
| Table data | text-sm (13px) | normal | text-gray-700 | tbl-td |
| Table headers | text-xs (11px) | font-semibold | text-gray-500 | uppercase |
| Form labels | text-xs (11px) | font-semibold | text-gray-500 | uppercase tracking-wide |
| Status badges | text-xs (11px) | font-semibold | varies | rounded-full |
| Sidebar nav links | text-sm (13px) | font-medium | text-slate-300 | |
| Sidebar section labels | text-xs (11px) | font-semibold | text-slate-500 | uppercase |

### Font Stack
The app uses the system default sans-serif (no external font loaded — local app, no CDN font dependency needed):
```css
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
```
This is Tailwind's default. Do not change until Keith explicitly requests a custom font.

---

## 4. Spacing & Density

Keith confirmed: **Compact ERP style** — maximum data visible, ~20–25 rows before scrolling.

### Table Row Density
```
Compact (chosen):  td { padding: 6px 16px; }  → ~32px row height
                   Tailwind: py-1.5 px-4

Comfortable:       td { padding: 10px 16px; }  → ~44px row height
                   Tailwind: py-2.5 px-4

Spacious:          td { padding: 14px 16px; }  → ~56px row height
                   Tailwind: py-3.5 px-4
```

### Standard Spacing Values

| Context | Value | Tailwind |
|---------|-------|---------|
| Table td | 6px top/bottom, 16px left/right | `py-1.5 px-4` |
| Table th | 10px top/bottom, 16px left/right | `py-2.5 px-4` |
| Card body | 20px all | `p-5` |
| Card header | 14px top/bottom, 20px left/right | `py-3.5 px-5` |
| Dense panel body | 16px all | `p-4` |
| Form input | 8px top/bottom, 12px left/right | `py-2 px-3` |
| Inline form input | 6px top/bottom, 12px left/right | `py-1.5 px-3` |
| Button standard | 8px top/bottom, 16px left/right | `py-2 px-4` |
| Button small | 6px top/bottom, 12px left/right | `py-1.5 px-3` |
| Button xs | 4px top/bottom, 8px left/right | `py-1 px-2` |
| Page content | 16px–24px | `p-4 md:p-6` |
| Card stack | 16px between cards | `space-y-4` |

---

## 5. Component Specifications

### 5.1 Buttons

```
Primary (brand olive):
┌─────────────────────┐
│   + New Quote       │  bg-brand-700, text-white, hover:bg-brand-600
└─────────────────────┘
Class: btn-primary  (or inline: px-4 py-2 text-sm font-medium text-white bg-brand-700 hover:bg-brand-600 rounded-lg transition-colors)

Secondary (neutral gray):
┌──────────────────┐
│   Cancel         │  bg-white, border-gray-300, text-gray-700, hover:bg-gray-50
└──────────────────┘
Class: btn-secondary

Danger (red):
┌──────────────────┐
│   Void Invoice   │  bg-red-600, text-white, hover:bg-red-700
└──────────────────┘
Class: btn-danger

Ghost (text only):
  ← Back to List      text-gray-500, hover:text-gray-700
Class: btn-ghost
```

**Size modifiers** (combine with btn-primary etc.):
- `btn-sm` → `px-3 py-1.5 text-xs`
- `btn-xs` → `px-2 py-1 text-xs`

**Header action buttons** (in the sticky page header):
Always use the **small** size variant. The header bar is h-14 (56px) — standard buttons fit but small looks better.

### 5.2 Form Inputs

```
Normal state:
┌──────────────────────────────────────────┐
│ Search by company name or phone…         │  border-gray-300, text-sm
└──────────────────────────────────────────┘

Focus state (brand olive ring):
┌══════════════════════════════════════════╗  ← ring-1 ring-brand-400 border-brand-400
║ Gulf Coast Diesel                        ║
╚══════════════════════════════════════════╝

Error state:
┌──────────────────────────────────────────┐  ← ring-1 ring-red-400 border-red-400
│ (empty)                                  │
└──────────────────────────────────────────┘
  ⚠ This field is required               ← text-xs text-red-600 mt-1
```

**Classes:**
- Normal: `form-input` (CSS utility class in base.html)
- Inline/tight: `px-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-brand-400 focus:border-brand-400`

### 5.3 Form Labels
```
CUSTOMER NAME *                 ← text-xs font-semibold uppercase tracking-wide text-gray-500
┌──────────────────────────┐
│ Gulf Coast Diesel        │
└──────────────────────────┘
```
Class: `form-label` → `block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1`

### 5.4 Cards
```
┌─────────────────────────────────────────────────┐
│ CUSTOMER INFORMATION                      [Edit] │  ← card-header: bg-gray-50/60, border-b
├─────────────────────────────────────────────────┤
│                                                 │
│  Company    Gulf Coast Diesel                   │  ← card-body: p-5
│  Phone      (985) 555-0122                      │
│  Terms      Net 30                              │
│  Tax Rate   0%                                  │
│                                                 │
└─────────────────────────────────────────────────┘
```
HTML:
```html
<div class="card">
  <div class="card-header">
    <h2 class="card-title">Customer Information</h2>
    <button class="btn-secondary btn-xs">Edit</button>
  </div>
  <div class="card-body">
    <!-- content -->
  </div>
</div>
```

### 5.5 Tables (Compact ERP Style)

```
┌─────────────────────────────────────────────────────────────────────┐
│ PART #       DESCRIPTION              QOH   COST      SELL    STATUS│ ← tbl-head (bg-gray-50)
├─────────────────────────────────────────────────────────────────────┤
│ 14-1234      Alternator 12V 90A         8   $89.00  $130.00  Active │ ← tbl-row (hover:bg-gray-50/70)
│ 14-1235      Alternator 12V 120A        3  $112.00  $163.00  Active │
│ 14-2001      Starter Motor 24V          0  $145.00  $211.00  Active │ ← QOH 0 → text-red-600
│ 22-0088      Fuel Pump Assembly        12   $67.00   $97.00  Active │
└─────────────────────────────────────────────────────────────────────┘
```

Rules:
- Part # column: `font-mono text-brand-700`
- Currency columns: `text-right tabular-nums`
- QOH = 0: `text-red-600`
- QOH > 0: `text-gray-700` (no special color — zero is the exception)
- Row hover: `hover:bg-gray-50/70 transition-colors cursor-pointer`

### 5.6 Status Badges

```
●  Active        bg-green-100 text-green-800   (products, customers)
●  Draft         bg-gray-100 text-gray-600
●  Sent          bg-blue-100 text-blue-800
●  Partial       bg-amber-100 text-amber-800
●  Paid          bg-green-100 text-green-800
●  Overdue       bg-red-100 text-red-800        ← text-red-700 for amounts
●  Void          bg-red-100 text-red-600
●  Accepted      bg-green-100 text-green-800
●  Declined      bg-red-100 text-red-800
●  Converted     bg-brand-100 text-brand-800    ← quote converted to invoice
●  Cancelled     bg-gray-100 text-gray-400
```
Structure: `<span class="badge badge-green">Active</span>`

### 5.7 Tab Bars (Detail Pages)

```
Account │ Invoices │ Quotes │ Call Log
────────┘          │        │
                   (inactive tabs: gray, no border)
```
Active tab: `border-b-2 border-brand-700 text-brand-700 font-medium`
Inactive tab: `border-b-2 border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300`

### 5.8 Flash Banners (Query-param driven)

```
✓  Saved successfully.                    ← bg-green-50 border-green-200 text-green-800
✕  Cannot cancel a billed PO.             ← bg-red-50 border-red-200 text-red-800
ℹ  Items received and inventory updated.  ← bg-blue-50 border-blue-200 text-blue-800
```
Auto-dismiss: 4 seconds (saved/received), 6 seconds (error).
Triggered via `?saved=1`, `?error=message`, `?received=1` on redirect URL.

### 5.9 Toast Notifications (HTMX OOB)

```
         ┌─────────────────────────────┐
         │ ✓  Quote saved              │  ← bottom-right, slides up, 4s auto-dismiss
         └─────────────────────────────┘
```
Used for: async HTMX actions (autosave, follow-up status change)
Not used for: full-page form submissions (those use flash banners via redirect)

---

## 6. Global Shell Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ┌──────────────┐ ┌──────────────────────────────────────────────────────────┐│
│ │ SIDEBAR      │ │ HEADER (h-14, sticky, z-30, bg-white, border-b)          ││
│ │ w-60         │ │ [≡ mobile] [Page Title]      [Search…Ctrl+K]  [Log Call] ││
│ │ bg-slate-900 │ ├──────────────────────────────────────────────────────────┤│
│ │              │ │ [Flash Banner — conditionally rendered]                  ││
│ │ JAKS         │ ├──────────────────────────────────────────────────────────┤│
│ │ Inventory    │ │                                                          ││
│ │              │ │  MAIN CONTENT  (p-4 md:p-6, flex-1, overflow-y-auto)    ││
│ │ ── SALES ──  │ │                                                          ││
│ │  Dashboard   │ │  {% block content %}{% endblock %}                       ││
│ │▸ Quotes      │ │                                                          ││
│ │  Sales Orders│ │                                                          ││
│ │  Invoices    │ │                                                          ││
│ │  Customers   │ │                                                          ││
│ │              │ │                                                          ││
│ │ ─ PURCHASING │ │                                                          ││
│ │  Vendors     │ │                                                          ││
│ │  Purch Orders│ │                                                          ││
│ │              │ │                                                          ││
│ │ ─ INVENTORY  │ │                                                          ││
│ │  Products    │ │                                                          ││
│ │              │ │                                                          ││
│ │ ─ CORES      │ │                                                          ││
│ │  Core Charges│ │                                                          ││
│ │  Returns     │ │                                                          ││
│ │  Warranty    │ │                                                          ││
│ │              │ │                                                          ││
│ │ ─ REPORTS    │ │                                                          ││
│ │  Reports     │ │                                                          ││
│ │              │ │                                                          ││
│ │ ─ SYSTEM     │ │                                                          ││
│ │  Settings    │ │                                                          ││
│ └──────────────┘ └──────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────────────┘

Toast container (fixed bottom-right, z-100, stacks upward)
Slide-over backdrop + panel (fixed, z-50, slides from right)
```

**Active nav link visual:**
```
│ ▌ Quotes         │  ← brand-300 left border (border-l-2), bg-slate-800, text-white
│   Invoices       │  ← no border, text-slate-300, hover:bg-slate-700
```

---

## 7. Screen-by-Screen Design Plan

### 7.1 Dashboard

**Purpose:** Morning status board. What do I owe? Who owes me? What needs action today?

**Above-fold layout (visible on 1080p without scrolling):**
```
┌──────────────────────────────────────────────────────────────────────┐
│  OPEN AR          OVERDUE           OPEN SALES ORDERS    FOLLOW-UPS  │
│                                                                       │
│  $24,800          $6,200            12 orders             8 today     │
│  38 invoices      7 invoices        3 unfulfilled         2 urgent    │
│                   > 30 days         awaiting inventory    ● Truck Down │
└──────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────┐  ┌────────────────────────────────┐
│ TODAY'S FOLLOW-UPS                │  │ OPEN SALES ORDERS              │
│                                   │  │                                │
│ ●🔴 Gulf Coast Diesel  Truck Down │  │ SO-2026-0041  Gulf Coast  $840 │
│ ●   Bayou Fleet        Due today  │  │ SO-2026-0038  Bayou Fleet  $320│
│ ●   River Road Diesel  Due today  │  │ SO-2026-0035  River Road $1,100│
│     …                             │  │ …                              │
│              [View All Follow-Ups]│  │            [View All Orders →] │
└───────────────────────────────────┘  └────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ OVERDUE INVOICES                                                      │
│                                                                       │
│ INV-2026-0021  Gulf Coast Diesel   $1,240   42 days  [View Invoice]  │
│ INV-2026-0018  Bayou Fleet         $  870   38 days  [View Invoice]  │
│ INV-2026-0015  River Road Diesel   $2,100   35 days  [View Invoice]  │
└──────────────────────────────────────────────────────────────────────┘
```

**Design notes:**
- KPI stat tiles: 4 across on desktop, 2x2 on tablet
- Overdue stat tile: `text-red-600` for the dollar amount
- Truck Down follow-ups: shown with 🔴 or `bg-red-50 border-l-4 border-red-500`
- No auto-refresh — this is a local app, manual F5 to refresh

---

### 7.2 Quote List

**Purpose:** See all quotes, filter by status, find a quote to reopen.

```
┌── Quote List ─────────────────────────────────────────────────────────┐
│ [All] [Draft] [Sent] [Accepted] [Declined] [Expired] [Converted]      │
│                                              [ + New Quote ]           │
├───────────────────────────────────────────────────────────────────────┤
│ QUOTE #     CUSTOMER              DATE       TOTAL   STATUS  FOLLOW-UP│
├───────────────────────────────────────────────────────────────────────┤
│ Q-2026-0041 Gulf Coast Diesel     05/24/26  $1,240  Sent     05/25    │
│ Q-2026-0038 Bayou Fleet Service   05/22/26  $  870  Draft    —        │
│ Q-2026-0035 River Road Diesel     05/20/26  $2,100  Sent     05/26    │
│ Q-2026-0031 Gulf Coast Diesel     05/18/26  $  340  Accepted —        │
│ Q-2026-0028 Murphy's Diesel       05/15/26  $  670  Expired  —        │
└───────────────────────────────────────────────────────────────────────┘
```

**Design notes:**
- Status filter tabs: `bg-gray-100 rounded-lg p-1` container, `bg-white shadow` on active pill
- Follow-up date: red if overdue, amber if today, gray if future
- Expired quotes: `text-gray-400` entire row (muted)
- Click row → full page quote workspace

---

### 7.3 Quote Workspace (THE MOST IMPORTANT SCREEN)

**Purpose:** Live operational console. Build a quote while a customer is on the phone. Target: 45 seconds.

```
┌── Quote Workspace ─────────────────────────────────────────────────────────┐
│ [← Quotes]                    Q-2026-0041                    [Print] [···] │  ← header bar actions
├─────────────────────────────────────────────────────────────────────────────┤
│ CUSTOMER *                QUOTE DATE      ESN / ENGINE         CUSTOMER PO  │
│ ┌─────────────────────┐  05/24/2026      ┌──────────────┐     ┌──────────┐ │
│ │ Gulf Coast Diesel ▾ │[+]               │ 1234567      │     │ PO-12345 │ │
│ └─────────────────────┘                  └──────────────┘     └──────────┘ │
│                                                                             │
│ NOTES                                                                       │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ Customer needs alternator and starter for their Cummins ISX             │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ 🔍 Search part#, OEM#, description — or type free text          [Add]  │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
│    ↓ SEARCH RESULTS DROPDOWN (appears while typing)                         │
│   ┌───────────────────────────────────────────────────────────────────────┐ │
│   │ 14-1234  Alternator 12V 90A PAI         QOH: 8  $89→$130  Last: $125 │ │
│   │ 14-1235  Alternator 12V 120A PAI        QOH: 3  $112→$163            │ │
│   │ 14-2001  Starter Motor 24V PAI          QOH: 0  $145→$211  Last: $210│ │
│   └───────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│  #  PART #      DESCRIPTION            QOH   QTY    PRICE      TOTAL  CORE │
│ ── ──────────── ─────────────────────  ────  ─────  ─────────  ─────  ──── │
│  1  14-1234     Alternator 12V 90A PAI   8  [  1] [$130.00]  $130.00  —    │
│  2  14-1234C    Core charge — Alt 12V    8     1   $ 45.00   $ 45.00  ●    │
│  3  14-2001     Starter Motor 24V         0  [  1] [$211.00]  $211.00  —   │
│  4              [Free text line…]             [  1] [       ]  $  0.00  —  │
│                                                                             │
│                                   [+ Add Line]  [+ Add Free-Text Line]     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                           Subtotal          $386.00         │
│                                           Core charges      $ 45.00         │
│                                           Tax (0%)          $  0.00         │
│                                           ─────────────────────────         │
│                                           TOTAL             $431.00         │
│                                                       [Save]  ✓ Saved       │
│                                         [Send to Customer] [Convert → SO]  │
├─────────────────────────────────────────────────────────────────────────────┤
│ Follow-Up: [Follow Up Tomorrow] [Waiting on Customer] [Waiting on Vendor]   │
│            [🔴 Truck Down]      [No Follow Up]              Due: May 25     │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Line table — inline editing spec (the confirmed friction point):**
- `QTY` and `PRICE` columns: inline `<input>` elements, no click-to-edit — always editable
- Qty input: `w-16 text-center`, auto-select on focus
- Price input: `w-24 text-right tabular-nums`, auto-select on focus
- Enter key in Qty → jumps to Price
- Enter key in Price (or Tab) → clears search, focuses search bar for next part
- Delete [×] button: appears on row hover (right side)
- Core charge rows: visually distinguished, slightly muted, not editable (auto-calculated)

**Search dropdown spec:**
```
┌────────────────────────────────────────────────────────────────────┐
│ 14-1234  Alternator 12V 90A PAI Industries            QOH: 8  ●   │ ← Part# match
│          $89.00 cost · $130.00 sell                               │
│          Last sold: $125.00  (03/15/26)                ← brand-400│
├────────────────────────────────────────────────────────────────────┤
│ 14-1235  Alternator 12V 120A PAI Industries           QOH: 3  ●   │
├────────────────────────────────────────────────────────────────────┤
│ 14-2001  Starter Motor 24V PAI Industries             QOH: 0  ✕   │ ← 0 = red
└────────────────────────────────────────────────────────────────────┘
```
- Part number: `font-mono text-brand-700 font-semibold`
- Description: `text-sm text-gray-700`
- QOH badge: green if > 0, red if 0
- Cost/sell: `text-xs text-gray-500`
- Last sold: `text-xs text-brand-400` (muted olive)
- Keyboard: ↑↓ arrows to navigate, Enter to select, Escape to close

---

### 7.4 Customer List

**Purpose:** Find a customer, open their record.

```
┌── Customers ──────────────────────────────────────────────────────────┐
│ [Search by name or phone…]                        [ + New Customer ]  │
├───────────────────────────────────────────────────────────────────────┤
│ COMPANY              CONTACT         PHONE            TERMS   BALANCE │
├───────────────────────────────────────────────────────────────────────┤
│ Gulf Coast Diesel    Mike Landry      (985) 555-0122  Net 30  $1,240  │
│ Bayou Fleet Service  Tony Breaux      (504) 555-0177  Net 15  $    0  │
│ River Road Diesel    Carlos Menendez  (225) 555-0199  COD     $2,100  │
└───────────────────────────────────────────────────────────────────────┘
```

**Design notes:**
- Balance > 0: `text-red-600` (they owe us money)
- Balance = 0: `text-gray-400` (clean)
- Active badge on customer name if using `Active` status
- No status tabs — customers are either active or not (filter in search)
- Click row → full detail page (from sidebar nav context)

---

### 7.5 Customer Detail

**Purpose:** 360-degree view of a customer. Account info, invoices, quotes, call history.

```
┌── Gulf Coast Diesel ──────────────────────────────────────────────────┐
│ [← Customers]  ● Active   Balance: $1,240    [New Quote] [New Invoice]│
├───────────────────────────────────────────────────────────────────────┤
│ Account │ Invoices │ Quotes │ Call Log │ Sales Orders                  │
│ ────────┘                                                             │
│                                                                       │
│ ┌──────────────────────────────┐ ┌──────────────────────────────────┐ │
│ │ CONTACT INFO          [Edit] │ │ FINANCIAL SUMMARY                │ │
│ │─────────────────────────────│ │──────────────────────────────────│ │
│ │ Company   Gulf Coast Diesel  │ │ Open AR          $1,240.00       │ │
│ │ Contact   Mike Landry        │ │ Credit Balance   $    0.00       │ │
│ │ Phone     (985) 555-0122     │ │ Payment Terms    Net 30          │ │
│ │ Email     mike@gulfcoast.com │ │ Interest Rate    1.5%/month      │ │
│ │ Address   123 Main St        │ │ Tax Exempt       No              │ │
│ │           Houma, LA 70360    │ │ Discount %       5%              │ │
│ └──────────────────────────────┘ └──────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────┘
```

**Invoices tab:**
```
│ INVOICE #      DATE      AMOUNT     BALANCE    STATUS    DUE DATE    │
│ INV-2026-0041  05/24/26  $1,240.00  $1,240.00  Open      06/23/26    │
│ INV-2026-0038  05/15/26  $  870.00  $    0.00  Paid      —           │
│ INV-2026-0031  04/30/26  $2,100.00  $2,100.00  Overdue   05/30/26    │  ← red row
```

---

### 7.6 Invoice List

**Purpose:** AR management view. See all invoices by status.

```
┌── Invoices ───────────────────────────────────────────────────────────┐
│ [All] [Open] [Overdue] [Partial] [Paid] [Void]    [ + New Invoice ]   │
│ [Search invoice #, customer PO, ESN…]                                 │
├───────────────────────────────────────────────────────────────────────┤
│ INVOICE #      CUSTOMER         DATE      AMOUNT    BALANCE   STATUS  │
├───────────────────────────────────────────────────────────────────────┤
│ INV-2026-0041  Gulf Coast       05/24/26  $1,240.00 $1,240.00 Open   │
│ INV-2026-0038  Bayou Fleet      05/22/26  $  870.00 $  435.00 Partial│
│ INV-2026-0035  River Road       05/18/26  $2,100.00 $2,100.00 Overdue│  ← red
│ INV-2026-0031  Gulf Coast       04/30/26  $  340.00 $    0.00 Paid   │  ← muted
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.7 Invoice Detail

**Purpose:** Review/finalize an invoice, record payment, push to QBO.

```
┌── INV-2026-0041 ──────────────────────────────────────────────────────┐
│ Gulf Coast Diesel · Open · Due: 06/23/2026    [Record Payment] [Print]│
│ [Finalize] [Push to QBO] [Void]                                       │
├─────────────────────────────────────┬─────────────────────────────────┤
│ BILL TO                             │ INVOICE DETAILS                 │
│ Gulf Coast Diesel                   │ Invoice #   INV-2026-0041       │
│ Mike Landry                         │ Date        05/24/2026          │
│ (985) 555-0122                      │ Due Date    06/23/2026          │
│                                     │ Customer PO PO-12345            │
│                                     │ ESN         1234567             │
├─────────────────────────────────────┴─────────────────────────────────┤
│ PART #      DESCRIPTION                   QTY   UNIT PRICE    TOTAL  │
│ 14-1234     Alternator 12V 90A PAI          1     $130.00    $130.00 │
│ 14-1234C    Core charge — Alternator        1     $ 45.00    $ 45.00 │
│ 14-2001     Starter Motor 24V               1     $211.00    $211.00 │
│                                                                       │
│                                        Subtotal          $386.00     │
│                                        Core charges      $ 45.00     │
│                                        Tax               $  0.00     │
│                                        ─────────────────────────     │
│                                        TOTAL             $431.00     │
│                                        Amount Paid       $    0.00   │
│                                        Balance Due       $431.00     │
├───────────────────────────────────────────────────────────────────────┤
│ PAYMENTS                                                              │
│ No payments recorded.                                                 │
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.8 Product List

**Purpose:** Inventory lookup, quick cost/sell reference.

```
┌── Products ───────────────────────────────────────────────────────────┐
│ [Search part#, description…]   [Active ▾]           [ + New Product ] │
├───────────────────────────────────────────────────────────────────────┤
│ PART #       DESCRIPTION                 QOH  QO  COST     SELL STATUS│
├───────────────────────────────────────────────────────────────────────┤
│ 14-1234      Alternator 12V 90A PAI        8   0  $ 89.00 $130.00  ● │
│ 14-1235      Alternator 12V 120A PAI       3   2  $112.00 $163.00  ● │
│ 14-2001      Starter Motor 24V PAI         0   0  $145.00 $211.00  ● │ ← QOH red
│ 22-0088      Fuel Pump Assembly HHP       12   0  $ 67.00 $ 97.00  ● │
└───────────────────────────────────────────────────────────────────────┘
```

**Design notes:**
- QOH column: red if 0, normal gray if > 0
- QO (qty on order): shows pending PO receipts
- Part # always `font-mono text-brand-700`

---

### 7.9 Product Detail

**Purpose:** Full product record. Pricing, vendor sources, cross-references, images.

```
┌── 14-1234 — Alternator 12V 90A ───────────────────────────────────────┐
│ [← Products]  ● Active                        [Edit] [Enrich from PAI]│
│                                                                        │
│ Overview │ Pricing │ Vendor Sources │ Cross-References │ Images        │
│ ─────────┘                                                             │
│ ┌──────────────────────────────┐ ┌──────────────────────────────────┐  │
│ │ PRODUCT INFO          [Edit] │ │ STOCK & PRICING                  │  │
│ │─────────────────────────────│ │──────────────────────────────────│  │
│ │ SKU      JAKS-PAI-14-1234    │ │ On Hand         8                │  │
│ │ Title    Alternator 12V 90A  │ │ Committed       1                │  │
│ │ Brand    PAI Industries      │ │ Available       7                │  │
│ │ Category Alternators         │ │ On Order        0                │  │
│ │ Core     Yes — $45.00        │ │ Cost            $89.00           │  │
│ │ Weight   12 lbs              │ │ Markup          30%              │  │
│ └──────────────────────────────┘ │ Sell Price      $130.00   →      │  │
│                                  └──────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 7.10 Sales Order List

```
┌── Sales Orders ───────────────────────────────────────────────────────┐
│ [Open] [Partial] [Fulfilled] [Invoiced] [Cancelled]  [ + New Order ]  │
├───────────────────────────────────────────────────────────────────────┤
│ ORDER #        CUSTOMER         DATE      TOTAL     STATUS   PAYMENT  │
│ SO-2026-0041   Gulf Coast       05/24/26  $1,240    Open     None     │
│ SO-2026-0038   Bayou Fleet      05/22/26  $  870    Partial  Deposit  │
│ SO-2026-0035   River Road       05/20/26  $2,100    Open     Full     │
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.11 Sales Order Detail

```
┌── SO-2026-0041 ───────────────────────────────────────────────────────┐
│ Gulf Coast Diesel · Open                 [Record Payment] [Convert →] │
├─────────────────────────────────────────────────────────────────────  ┤
│ Payment collected: None               Deposit: —   Full payment: —    │
├───────────────────────────────────────────────────────────────────────┤
│ PART #      DESCRIPTION               QTY  AVAIL  UNIT PRICE   TOTAL │
│ 14-1234     Alternator 12V 90A          1    ✓     $130.00    $130.00 │
│ 14-2001     Starter Motor 24V           1    ✕     $211.00    $211.00 │ ← backordered
├───────────────────────────────────────────────────────────────────────┤
│                                     TOTAL            $431.00          │
│                                     [Convert to Invoice when ready]   │
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.12 Purchase Order List

```
┌── Purchase Orders ─────────────────────────────────────────────────── ┐
│ [All] [Draft] [Sent] [Partial] [Received] [Billed] [Cancelled]        │
│                                                     [ + New PO ]      │
├───────────────────────────────────────────────────────────────────────┤
│ PO #          VENDOR         DATE      TOTAL     STATUS               │
│ PO-2026-0041  PAI Industries 05/24/26  $1,240    Sent                 │
│ PO-2026-0038  HHP            05/22/26  $  870    Received             │
│ PO-2026-0035  ATL Diesel     05/20/26  $  340    Billed               │
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.13 Purchase Order Detail

```
┌── PO-2026-0041 ───────────────────────────────────────────────────────┐
│ PAI Industries · Sent              [Receive Items] [Create Bill] [···] │
├───────────────────────────────────────────────────────────────────────┤
│ PART #       DESCRIPTION           QTY ORD  QTY RCV  UNIT COST  TOTAL │
│ 14-1234      Alternator 12V 90A        5        5     $89.00   $445.00 │
│ 14-2001      Starter Motor 24V         2        0     $145.00  $290.00 │ ← pending
├───────────────────────────────────────────────────────────────────────┤
│                                             PO Total    $735.00        │
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.14 Cores List

**Purpose:** Track customer and vendor core liabilities.

```
┌── Core Charges ───────────────────────────────────────────────────────┐
│ [Customer Cores] [Vendor Cores]                                       │
├───────────────────────────────────────────────────────────────────────┤
│ CUSTOMER          PART #       DESCRIPTION       CHARGE   DATE   STATUS│
│ Gulf Coast Diesel 14-1234C     Core - Alt 12V    $45.00  05/24  Open  │
│ Bayou Fleet       14-2001C     Core - Starter    $75.00  05/20  Open  │
│ River Road Diesel 22-0088C     Core - Fuel Pump  $35.00  05/10  Open  │
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.15 Warranty List

```
┌── Warranty Claims ─────────────────────────────────────────────────── ┐
│ [Open] [Draft] [Submitted] [Approved] [Denied] [Credited] [Closed]    │
│                                                  [ + New Claim ]      │
├───────────────────────────────────────────────────────────────────────┤
│ CLAIM #      CUSTOMER         PART           CREDIT    STATUS         │
│ WC-2026-0012 Gulf Coast       14-1234        $130.00   Submitted      │
│ WC-2026-0011 Bayou Fleet      14-2001        $211.00   Approved       │
└───────────────────────────────────────────────────────────────────────┘
```

---

### 7.16 Settings

```
┌── Settings ───────────────────────────────────────────────────────────┐
│ ┌──────────────────────────────────────────────────────────────────┐  │
│ │ COMPANY INFORMATION                                    [Save]    │  │
│ │ Company Name   JAKS Diesel Parts                                 │  │
│ │ Address        123 Industrial Blvd, Houma, LA 70360              │  │
│ │ Phone          (985) 555-0100                                    │  │
│ │ Email          info@jaksdiesel.com                               │  │
│ └──────────────────────────────────────────────────────────────────┘  │
│ ┌──────────────────────────────────────────────────────────────────┐  │
│ │ DEFAULTS                                                [Save]   │  │
│ │ Default Markup %      30%                                        │  │
│ │ CC Surcharge %         3%                                        │  │
│ │ Default Payment Terms  Net 30                                    │  │
│ └──────────────────────────────────────────────────────────────────┘  │
│ ┌──────────────────────────────────────────────────────────────────┐  │
│ │ DOCUMENT NUMBERING                                               │  │
│ │ Invoice prefix   INV     Next: INV-2026-0042                     │  │
│ │ Quote prefix     Q       Next: Q-2026-0042                       │  │
│ │ SO prefix        SO      Next: SO-2026-0042                      │  │
│ │ PO prefix        PO      Next: PO-2026-0042                      │  │
│ └──────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 8. Quote Workspace — Deep Dive

### 8.1 Keyboard Flow
```
[Customer selector] → Tab
[ESN field]         → Tab
[Notes field]       → Tab
[Search bar]        → type part# → ↓ navigate results → Enter select
                    → line added → focus returns to search bar automatically
[Qty input]         → Tab  → [Price input] → Enter/Tab
                    → focus jumps back to search bar for next part
[Last line Price]   → Tab  → back to search bar
```

### 8.2 Line Table — Inline Editing Fix

The current friction: clicking qty/price is awkward. The fix:

```html
<!-- Qty cell — always an input, no click-to-edit pattern -->
<td class="px-4 py-1.5 w-16">
  <input type="number" min="1"
         class="w-14 text-center text-sm border border-gray-300 rounded
                focus:outline-none focus:ring-1 focus:ring-brand-400 focus:border-brand-400
                tabular-nums"
         value="1"
         @keydown.enter="$nextTick(() => $el.closest('tr').querySelector('.price-input').focus())">
</td>

<!-- Price cell — always an input -->
<td class="px-4 py-1.5 w-24 text-right">
  <input type="number" step="0.01"
         class="price-input w-20 text-right text-sm border border-gray-300 rounded
                focus:outline-none focus:ring-1 focus:ring-brand-400 focus:border-brand-400
                tabular-nums"
         value="130.00"
         @keydown.enter="$dispatch('focus-search')">
</td>
```

Auto-select on focus (so you don't have to clear the field first):
```javascript
document.querySelectorAll('.price-input, .qty-input').forEach(el => {
  el.addEventListener('focus', () => el.select());
});
```

### 8.3 Follow-Up Bar
```
Follow-Up: [Follow Up Tomorrow]  [Waiting on Customer]  [Waiting on Vendor]
           [🔴 Truck Down]       [No Follow Up]                  Due: May 25
```
- Active pill: filled brand/status color, white text
- Inactive pill: white bg, colored border, colored text, hover tint
- Status → color mapping:
  - Follow Up Tomorrow (called) → Blue
  - Waiting on Customer (emailed) → Amber
  - Waiting on Vendor (left_vm) → Purple
  - 🔴 Truck Down (interested) → Red (ring-2 ring-red-300 urgent indicator)
  - No Follow Up (declined) → Gray

---

## 9. Navigation Flows

### 9.1 Quote → Invoice
```
Phone rings → [New Quote] → Quote Workspace
Customer gives parts needed → type part# → Enter → line added × N
→ Review totals → [Save] → ✓ Saved indicator
→ Customer says OK → [Convert to Invoice] → Invoice Detail
→ Customer wants to pay now → [Record Payment] → amount, method
→ [Print PDF] or [Email] to customer
```

### 9.2 Out-of-Stock → Sales Order
```
Quote Workspace → [Convert to SO] → Sales Order Detail
                → [Record Deposit] → payment recorded
                → [+ Create PO] → PO for missing items
→ PO Detail → [Receive Items] → inventory updated
→ Sales Order → status updates to Fulfilled
→ [Convert to Invoice] → Invoice Detail → [Finalize]
```

### 9.3 Inline Creation (Zero Context Loss)
```
Mid-quote, no customer found →
  [+ New Customer] clicked →
  Slide-over panel opens from right →
  Fill: Company name, phone, terms →
  [Save] →
  Panel closes →
  New customer auto-selected in quote →
  Return to building quote (no page reload, no lost state)
```

### 9.4 Core Lifecycle
```
Invoice with core line item:
  14-1234  Alternator          $130.00
  14-1234C Core - Alternator   $ 45.00  ← auto-added, customer owes return

Customer returns core →
  [Core Charges] → find open core → [Record Return]
  → Credit to account OR issue check

Vendor core return:
  [Vendor Cores tab] → batch select accumulated cores
  → [Log Shipment to Vendor] → print core return doc
  → Vendor credit received → [Record Credit]
```

---

## 10. Slide-Over Panel Specification

**Trigger contexts:**
| Trigger | Width | Content |
|---------|-------|---------|
| [+ New Customer] from quote/invoice | w-96 | Quick-create customer form |
| [+ New Product] from quote/PO | w-96 | Quick-create product form |
| [+ New Vendor] from product/PO | w-96 | Quick-create vendor form |
| [Log Call] from header bar | w-96 | Log call form |
| Clicking customer link mid-workflow | w-[480px] | Customer balance mini-view |

**Panel structure:**
```
┌──── right edge of viewport ─────────────────┐
│ ┌─────────────────────────────────────────┐ │
│ │ New Customer                         [×]│ │  ← slide-over-header
│ ├─────────────────────────────────────────┤ │
│ │                                         │ │
│ │  Company Name *                         │ │
│ │  ┌───────────────────────────────────┐  │ │
│ │  │                                   │  │ │
│ │  └───────────────────────────────────┘  │ │
│ │                                         │ │  ← slide-over-body (overflow-y-auto)
│ │  Phone                                  │ │
│ │  ┌───────────────────────────────────┐  │ │
│ │  │                                   │  │ │
│ │  └───────────────────────────────────┘  │ │
│ │                                         │ │
│ ├─────────────────────────────────────────┤ │
│ │ [Cancel]                    [Save & →] │ │  ← slide-over-footer
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

- Backdrop: `bg-black/40 backdrop-blur-sm`
- Transition: `translate-x-full → translate-x-0`, 200ms ease-out
- On save: panel closes, originating field auto-populated with new record

---

## 11. Print / PDF Document Design

### Quote PDF
```
┌──────────────────────────────────────────────────────────────────────┐
│  ████████████████████████████████████████████████████████████████████│
│  █ JAKS DIESEL PARTS                       Quote Q-2026-0041        █│  ← brand #4b5320 header
│  █ 123 Industrial Blvd, Houma LA 70360     Date: 05/24/2026         █│
│  █ (985) 555-0100  info@jaksdiesel.com     Valid: 30 days            █│
│  ████████████████████████████████████████████████████████████████████│
│                                                                       │
│  BILL TO:                          ESN: 1234567                      │
│  Gulf Coast Diesel                 Engine: Cummins ISX               │
│  Mike Landry                       Customer PO: PO-12345             │
│  123 Fleet Dr, Houma LA 70360                                         │
│                                                                       │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ PART #        DESCRIPTION                QTY  UNIT     TOTAL    │ │
│ │──────────────────────────────────────────────────────────────────│ │
│ │ 14-1234       Alternator 12V 90A PAI       1  $130.00  $130.00  │ │
│ │ 14-1234C      Core charge — Alternator     1  $ 45.00  $ 45.00  │ │
│ │ 14-2001       Starter Motor 24V            1  $211.00  $211.00  │ │
│ │                                                                  │ │
│ │                                    Subtotal          $386.00    │ │
│ │                                    Core charges      $ 45.00    │ │
│ │                                    Tax               $  0.00    │ │
│ │                                    ─────────────────────────    │ │
│ │                                    TOTAL             $431.00    │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  Terms: Net 30. Core charges credited upon return of serviceable core.│
│  Questions? (985) 555-0100 · info@jaksdiesel.com                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Print rules:**
- Header bar: `background: #4b5320; color: #fff;`
- Part # column: monospace font
- Currency: right-aligned, tabular-nums
- Core lines: always separate line items, never bundled
- ESN / Customer PO: always shown in header block when present
- Page size: US Letter (8.5" × 11"), margin: 0.75"
- Engine: WeasyPrint (Python, already scaffolded in print.html)

---

## 12. Status Badge Reference

### Quotes

| Status | Display | Badge Class | When |
|--------|---------|-------------|------|
| Draft | Draft | `badge-gray` | Created, not sent |
| Sent | Sent | `badge-blue` | Emailed/texted to customer |
| Accepted | Accepted | `badge-green` | Customer confirmed |
| Declined | Declined | `badge-red` | Customer said no |
| Expired | Expired | `badge-gray` | Past valid date |
| Converted | Converted | `badge-brand` | Turned into invoice/SO |

### Invoices

| Status | Display | Badge Class | When |
|--------|---------|-------------|------|
| Draft | Draft | `badge-gray` | Open, editable |
| Sent | Sent | `badge-blue` | Sent to customer |
| Partial | Partial | `badge-amber` | Partial payment received |
| Paid | Paid | `badge-green` | Fully paid |
| Overdue | Overdue | `badge-red` | Past due date |
| Void | Void | `badge-red` | Cancelled/reversed |

### Sales Orders

| Status | Display | Badge Class | When |
|--------|---------|-------------|------|
| Open | Open | `badge-blue` | Created, all items pending |
| Partial | Partial | `badge-amber` | Some items received |
| Fulfilled | Fulfilled | `badge-green` | All items in stock |
| Invoiced | Invoiced | `badge-brand` | Converted to invoice |
| Cancelled | Cancelled | `badge-gray` | Cancelled |

### Purchase Orders

| Status | Display | Badge Class | When |
|--------|---------|-------------|------|
| Draft | Draft | `badge-gray` | Not sent |
| Verbal | Verbal | `badge-amber` | Called in, not formal PO |
| Sent | Sent | `badge-blue` | Sent to vendor |
| Partial | Partial | `badge-amber` | Partial receipt |
| Received | Received | `badge-green` | Fully received |
| Billed | Billed | `badge-brand` | Bill created |
| Cancelled | Cancelled | `badge-gray` | Cancelled |

### Cores

| Status | Display | Badge Class | When |
|--------|---------|-------------|------|
| Open | Open | `badge-amber` | Core owed to/from us |
| Returned | Returned | `badge-blue` | Core physically returned |
| Credited | Credited | `badge-green` | Credit applied |
| Closed | Closed | `badge-gray` | Complete |

### Warranty Claims

| Status | Display | Badge Class | When |
|--------|---------|-------------|------|
| Draft | Draft | `badge-gray` | Not yet submitted |
| Submitted | Submitted | `badge-blue` | Sent to vendor |
| Approved | Approved | `badge-green` | Vendor approved |
| Denied | Denied | `badge-red` | Vendor denied |
| Credited | Credited | `badge-brand` | Customer credited |
| Closed | Closed | `badge-gray` | Complete |

### Products

| Status | Display | Badge Class | When |
|--------|---------|-------------|------|
| Active | Active | `badge-green` | In catalog, quotable |
| Superseded | Superseded | `badge-amber` | Replaced by another SKU |
| Discontinued | Discontinued | `badge-gray` | No longer sold |

---

## 13. Screen Design Priority Queue

Work through screens in this order. Each must be complete before moving to the next.

| # | Screen | Why This Rank | Key Fix Needed |
|---|--------|--------------|----------------|
| 1 | **Quote Workspace** | Most used. Confirmed friction: line table editing | Inline qty/price inputs, tab flow, compact rows |
| 2 | **Invoice Detail** | Revenue-critical. Needs dense line table + payment section | Same compact table treatment |
| 3 | **Customer Detail** | Used every call. Tabs need visual hierarchy | Tab design, financial summary card |
| 4 | **Dashboard** | Morning status board. Above-fold KPIs are the priority | 4 stat tiles + 2 list widgets layout |
| 5 | **Product List** | Daily lookup. Needs compact rows, QOH callout | Compact table, red QOH=0 |
| 6 | **Invoice List** | AR management. Filter tabs must be clear | Status tabs, overdue row styling |
| 7 | **Quote List** | Follow-up management. Follow-up date column | Due date color coding |
| 8 | **Sales Order Detail** | New workflow. Backorder + payment status display | Line availability indicator |
| 9 | **Purchase Order Detail** | Receiving workflow. QTY ORD vs QTY RCV display | Side-by-side qty columns |
| 10 | **Product Detail** | Enrichment + pricing tabs. Dense information | Tab layout, vendor source table |
| 11 | **Customer List** | Entry point to customer records | Compact, balance column |
| 12 | **Cores List** | Unique workflow. Two-tab layout | Customer/vendor tab switch |
| 13 | **Warranty Detail** | Complex state machine | Decision form inline |
| 14 | All list pages | Global consistency pass | Apply compact row standard globally |
| 15 | **Print/PDF** | Quote and invoice print output | Brand header, professional layout |

---

## 14. Implementation Checklist

For each screen before marking complete:

- [ ] All primary buttons use `btn-primary` (bg-brand-700)
- [ ] All form inputs use `form-input` or focus:ring-brand-400
- [ ] All table rows use compact `py-1.5` padding
- [ ] All identifiers (part#, invoice#, PO#) use `font-mono text-brand-700`
- [ ] All currency is right-aligned with `tabular-nums`
- [ ] All table headers use `tbl-th` (uppercase, text-xs, gray)
- [ ] Status badges use the `.badge` + `.badge-[color]` system
- [ ] Active tabs use `.tab-active` (border-brand-700)
- [ ] Cards use `.card` + `.card-header` + `.card-body`
- [ ] Empty states have an icon + message + primary CTA
- [ ] Error states surface via `?error=` flash banner or HTMX OOB toast
- [ ] Semantic green preserved for: Active, Paid, Preferred, Accepted, In-stock

---

*End of DESIGN.md — update this document when design decisions change.*
*Companion: researchdesign.md — ERP design research backing each decision above.*
