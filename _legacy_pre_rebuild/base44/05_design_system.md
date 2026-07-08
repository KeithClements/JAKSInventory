# 05 — Design System

This section defines the visual + interaction grammar. **All screens must
follow this — no exceptions.** Use the existing HTML mockups under
`mockups/` as the canonical visual reference.

---

## Palette (dark theme, single mode)

```
Backgrounds
  --bg          #0c1116    full-window
  --panel       #131a22    cards, tab content
  --panel-2     #1a232d    nested panels, table body
  --panel-3     #232f3b    table headers, hover
  --border      #2a3540    1px dividers
  --border-hi   #3a4856    emphasized dividers (current tab, focus)

Text
  --text        #e6edf3    body
  --dim         #8b98a5    secondary text, helper labels
  --faint       #5d6b78    disabled, placeholder

Accents
  --accent-new       #46c1ad    "Add new" — teal
  --accent-edit      #97a345    "Edit existing" — olive
  --ai-purple        #b06bff    AI assistant / suggestion
  --gold             #ffc857    pending / warning chip
  --green            #3fb950    OK / paid / above target
  --amber            #d29922    caution
  --red              #f85149    error / overdue / below cost
```

These names mirror the constants in `jaks_inventory/ui/theme.py` and the
`_BG / _PANEL / _ACCENT_*` tokens in `product_workbench_dialog.py`.

---

## Typography

- Font stack: `Inter, "Segoe UI", system-ui, sans-serif`.
- Sizes: `11px small`, `13px body`, `15px subheading`, `18px section`, `22px title`.
- Weights: `400 body`, `500 emphasis`, `600 heading`, `700 number callouts`.
- Numbers in tables / KPIs: tabular-nums, right-aligned.
- Money: 2 decimals, always with `$`. Never use `USD` suffix.
- Colors override weight: a green "$1,234.56" is `--green` + 600.

---

## Layout grammar (every list screen)

```
┌──────────────────────────────────────────────────────────────────┐
│  TITLE BAR  │  primary action ▸  │  more ▾  │  search  │  ⚙     │  56 px
├──────────────────────────────────────────────────────────────────┤
│  KPI STRIP  │  3–6 tiles, equal width                            │  88 px
├──────────────────────────────────────────────────────────────────┤
│  ATTENTION  │  chips: "3 below reorder" "2 stale costs"          │  36 px
├──────────────────────────────────────────────────────────────────┤
│  FILTER ROW │  single row, [type ▾] [vendor ▾] [status ▾] [×]     │  44 px
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                          DATA TABLE                              │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  FOOTER  │  "245 rows · 12 selected · $48,231 total value"       │  32 px
└──────────────────────────────────────────────────────────────────┘
```

### Rules
- **Title bar:** one primary action (e.g. `+ New Quote`), then a `More ▾` menu,
  then global search input, then a settings cog.
- **KPI strip:** always 3–6 tiles. Each tile = `LABEL` (dim 11px) over
  `VALUE` (700 22px) over optional `DELTA` (green/red 12px). No charts here.
- **Attention row:** zero-or-more chips. Each chip clickable, filters the
  table to the matching rows. Use `amber` background for warnings, `red` for
  errors. Hide the row entirely when no attention items.
- **Filter row:** dropdowns + a clear-all `×`. Live-filters the table.
  Persist last selection in localStorage per screen.
- **Table:** alternating row colors (`panel-2` / `panel-3`). Sticky header.
  Row hover = `panel-3` + slight tint. Selected row = `--accent-edit` left
  border 3px. Resize-to-content for short columns, stretch for descriptions.
- **Footer summary:** always show row count and any meaningful sum. Hide if
  table is empty.

---

## Components

### Pills (status, condition)

Solid background, rounded `9999px`, 11px text 600.

| Pill | Background | Text |
|------|-----------|------|
| `NEW` (condition) | `#1f3a3e` | `#46c1ad` |
| `REMAN` | `#3a3030` | `#d29922` |
| `USED` | `#2a2a30` | `#8b98a5` |
| `CORE` | `#2a2a30` | `#5d6b78` |
| `KIT` | `#2a2538` | `#b06bff` |
| `Paid` | `#1f3a26` | `#3fb950` |
| `Open` | `#3a3a1f` | `#ffc857` |
| `Overdue` | `#3a1f1f` | `#f85149` |
| `Draft` | `#232f3b` | `#8b98a5` |
| `Sent` | `#1f2a3a` | `#5fa3ff` |

### Buttons

Three variants:

```
.primary    bg: var(--accent-new); color: #0c1116; weight: 600;
.secondary  bg: var(--panel-3);     color: var(--text); border: 1px var(--border);
.ghost      bg: transparent;        color: var(--dim); border: none;
.danger     bg: transparent;        color: var(--red); border: 1px var(--red);
```

Hover: brighten background by ~8 %. Press: invert.

### Inputs

```
height: 32px
background: var(--panel-2)
border: 1px var(--border)
border-radius: 4px
focus: border 1px var(--accent-new), outline 0
placeholder: var(--faint)
```

### Tables

- Header: `panel-3` bg, `dim` text, 600 weight, 11px uppercase letterspaced.
- Body row: 36 px tall, 13 px text.
- Alternating: even rows `panel-2`, odd `panel-3` at 30 % opacity.
- Inline-editable cells: subtle pencil icon on hover; click to edit; commit
  on Enter or focus-out.

### Modals / dialogs

- Backdrop: `rgba(12, 17, 22, 0.7)` with `backdrop-filter: blur(4px)`.
- Dialog: `panel` bg, 12 px border-radius, 1 px `border-hi`, drop shadow.
- Header: title left, close `×` right.
- Body: scroll if too tall.
- Footer: actions right-aligned, Cancel ghost / Save primary.
- ESC closes (with confirm if dirty).

### KPI tile

```
┌─────────────────┐
│  REVENUE TODAY  │  ← label, dim, 11 px
│  $4,328.16      │  ← value, 22 px, 700
│  ▲ 12% vs yest  │  ← delta, 12 px, green/red
└─────────────────┘
```

### Attention chip

```
( ⚠ 3 below reorder × )
```
Clickable, filters the table when clicked. The `×` clears just that chip.

---

## Iconography

Use a single icon font (Lucide or Phosphor). Reserved emoji set for stage and
condition only:

| Emoji | Meaning |
|-------|---------|
| 🟢 | OK / matched |
| 🟡 | Partial / stale |
| 🔴 | Failed / conflict |
| ⚫ | Stubbed / placeholder |
| ✨ | AI suggestion |
| ⚠ | Warning |

No other emoji in production UI. (Mockups can be looser.)

---

## Interaction grammar

### Keyboard shortcuts (global)

| Combo | Action |
|-------|--------|
| `Ctrl + K` | Quick Nav: jump to any screen |
| `Ctrl + N` | New (context-aware: product on Products screen, quote on Quotes, etc.) |
| `Ctrl + F` | Focus the filter / search input |
| `Ctrl + S` | Save current dialog |
| `Ctrl + Enter` | Save & close |
| `Esc` | Close dialog / clear filter |
| `Alt + ↑ / ↓` | Navigate sections in workbench dialogs |
| `Ctrl + 1..9` | Jump to sidebar item N |

### Cross-window signals

When any CRUD mutates an entity, emit a signal that *all* open windows listen
to:

```
product_changed(product_id)
customer_changed(customer_id)
quote_changed(quote_id)
sales_order_changed(so_id)
invoice_changed(invoice_id)
po_changed(po_id)
inventory_changed(product_id)
payment_received(invoice_id, amount)
```

Every list screen subscribes to the matching signal and refreshes its row
inline (no full reload).

### Refresh rules

- A list screen never auto-refreshes on a timer if the user is editing a
  selection. Defer until selection cleared.
- Footers (total counts) update on every data change.
- "Last updated 3 s ago" indicator in the footer; turns amber if > 30 s.

---

## Empty states

Every list screen must define an empty state.

```
┌──────────────────────────────┐
│        (centered icon)        │
│                               │
│      No products yet          │
│   ─────────────────────────   │
│  Add your first SKU manually  │
│  or import via PAI scrape.    │
│                               │
│  [+ New Product]  [Import]    │
└──────────────────────────────┘
```

---

## Error states / toasts

- Toast appears bottom-right, 3 s auto-dismiss for success, sticky for error.
- Color matches level: green / amber / red.
- Errors always include a "Copy details" button for support.

---

## Accessibility

- Min contrast 4.5:1 on body text (palette already passes).
- Focus rings: 2 px `--accent-new` outline, never removed.
- All interactive elements reachable with Tab.
- Labels associated to inputs (no placeholder-as-label).
- Charts (where present) have a table fallback.
