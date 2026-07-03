# Handoff: Axle ERP — Brand & Core Screens

## Overview
Axle is a heavy-duty diesel **parts counter ERP**: quotes, cores, inventory, purchasing, and QuickBooks sync, used by counter staff and admins all day at desktop, with light read-and-act flows on phone. This package contains the finalized **brand identity (Hub logo system)**, the **core screen designs** (dashboard, quote/invoice builder, parts catalog, tablet + phone frames), a **print-ready quote document**, and a **clickable prototype** demonstrating intended interactions.

Tagline: *"Run your shop on Axle."* Subline: *"The heavy-duty parts counter system for quotes, cores, inventory, purchasing, and QuickBooks sync."*

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing intended look and behavior, **not production code to copy directly**. The task is to **recreate these designs in the target codebase's existing environment** using its established patterns and libraries — or, if no environment exists yet, choose the most appropriate stack (the brief assumes a server-rendered app; these designs do not require a SPA) and implement the designs there.

## Fidelity
**High-fidelity.** Colors, typography, spacing, and interaction states are final and should be recreated pixel-faithfully. The data shown (part numbers, customers, prices) is realistic sample data, not real content.

## Brand / Logo
The mark is the **"Hub"**: a keyed axle-end built from concentric circles on a 64×64 grid.
- Geometry: outer ring r=28 stroke 4; inner ring r=17 stroke 3; bore circle r=6.5 filled; keyway rect 4×6 r=1 at (30,20). "Simple" build (≤24px, favicon): outer ring + bore r=9 only.
- Colors: ring `#5a6630` (mil) + bore `#e7891d` (amber) on light; ring `#9cb23c` + bore `#ffb24d` reversed on dark; one-color ink `#0b0c0d` for B&W print; white knockout on green.
- Wordmark: "Axle" in Oswald 700, uppercase, line-height 0.8, letter-spacing .01em. Sub-line "PARTS COUNTER SYSTEM" in IBM Plex Mono, letter-spacing .3em, ~20–25% of wordmark size.
- Clear space = bore diameter on all sides. Minimum sizes: icon 16px, lockup 18px cap height.
- Don'ts: no stretching, no second accent color, no busy backgrounds, no shadows/bevels/rotation, wordmark only in Oswald.
- Ready-to-use SVGs are in `assets/` (see Assets below). Full spec with visual examples: `Axle Logo System.html`.

## Screens / Views

### 1. App shell (all desktop screens)
- **Grid**: `232px` fixed dark sidebar + fluid workspace. Sidebar collapses to a `56px` icon rail at tablet (768).
- **Sidebar (dark chrome — chosen)**: bg `#191c1f`; brand row (27px hub mark + AXLE wordmark white 21px + mono sub-label) with 1px `#2f343a` bottom border; nav items Oswald 500 uppercase 12px, color `#a9afb6`, icon 15px `#7c838b`, padding 8.5px 10px, radius 6px; **active item**: bg `#5a6630`, white text, icon `#b6cf45`; count badges mono 10px in `#2f343a` pills. Footer: mono 10px `#7c838b`, top border, shop/user line with `#b6cf45` highlights.
- **Top bar**: white, 56px, 1px `#cdd1d5` bottom border, 20px side padding. Breadcrumb in Oswald 500 **mixed case** 14px: dim `#7c838b` parents, `/` separators `#a9afb6`, current page `#0b0c0d` 600. Status chip beside it (see chips). Actions right-aligned.
- **Workspace**: bg `#f2f3f0`, body padding 18px 20px, 16px gaps.

### 2. Dashboard (1280×800)
- KPI row: 4 equal cards (white, 1px `#cdd1d5`, radius 10px, padding 14px 16px) — label Oswald 600 uppercase 10.5px `#7c838b`; value IBM Plex Mono 600 24px (accent colors: amber-text `#b06511` for cores, `#c0392b` for stock-outs, `#5a6630` for revenue); sub-line Barlow 11.5px `#565d65`.
- Below: Open Quotes table (flex 1) + 320px "Needs Attention" rail. Alert rows: status dot + bold 12.5px title + 11.5px `#565d65` detail, 1px `#e7e9eb` separators. QuickBooks sync card at rail bottom.
- Quote rows navigate to the quote on click.

### 3. Quote / Invoice builder (1280×800)
- Main column: "Add part" search field (white, 1px `#cdd1d5`, radius 6px, mono `/` kbd hint) above the Line Items card.
- **Line items table**: columns # / Part / Bin / Qty / Unit / Core / Ext. Part cell = part number (IBM Plex Mono 700 12px `#0b0c0d`) over description (Barlow 12px `#2f343a`). Bin mono `#7c838b`. Qty = stepper (bordered pill: − / value / +, mono 12px, 44px+ total hit area). Unit/money in mono 11.5px right-aligned. **Core charges** = small amber chips: mono 10.5px, bg `#fdf3e4`, text `#b06511`, border `#f0d6ae`, radius 4px, content like `+100.00`; em-dash plain `#a9afb6` when none. **Ext column emphasized**: mono 700 12.5px `#0b0c0d`.
- Row states: hover `#f6f7f2`; price-alert row (`hot`) `#fffdf7`; selected `#eef2de` + inset 3px left bar `#5a6630`.
- Card footer: Add Line / Scan ghost buttons + right-aligned **amber alert chip** (⚠ icon 13px, Oswald 600 uppercase 10.5px, bg `#fdf3e4`, border `#efd3a8`, radius 5px) e.g. "Price expires 06/15 — line 3".
- **Right rail (284px)**: Customer card; Totals card (kv rows 12.5px, core charges in `#b06511`, "exempt" dimmed); **Quote Total** = the cash-register moment: min-height 74px, padding 20px 16px, gradient `#22262b→#191c1f`, **2px amber top border**, label Oswald 600 uppercase 11px `.12em` `#a9afb6`, value IBM Plex Mono 600 **26px** `#b6cf45`, vertically centered; margin row (label + mono % + 7px progress bar in `#6d7b39→#9cb23c` gradient, admin-only); QuickBooks sync card (8px green LED with `#eaf3e4` halo).
- Top bar actions: Print (ghost), Save (ghost), **Convert to Invoice (amber `#e7891d`, white text)** — the primary action.

### 4. Parts catalog (1280×800)
- Filter bar: search (flex 1) + filter chips (Oswald 500 uppercase 11px, bordered pills radius 14px; active = solid `#191c1f` white).
- Table columns: Part # / Description / Bin / On hand / Comm. / Avail / Core / List / Status. Avail bolded; status = dot+label (`#5a9a3c` In stock, `#e7891d`+`#b06511` Low, `#c0392b` Out).
- Footer pagination: mono 11px, bordered page pills, active solid green.
- **Detail drawer (300px)**: part number mono 600 16px `#3c4520`; list/cost/core kv rows; stock-by-location grid; cores-due alert card; "Add to Quote" primary button (green `#5a6630`).
- Row click selects (green tint + left bar) and populates the drawer.

### 5. Tablet (768×1024)
Sidebar → 56px icon rail (centered icons, simple hub mark). Catalog drops to 5 columns: Part # / Description (ellipsized) / Avail / List / Status. Drawer becomes a push-in panel or routes to a detail page (implementer's choice).

### 6. Phone (390×844) — read-and-act-light
- Top bar: dark `#191c1f`, simple hub 24px + AXLE 18px + context label right (mono 10px).
- **Part lookup**: large search field (15px text + scan icon in green); result cards (white, radius 10px, padding 13px 14px): part number mono 600 14px, description 13px, price right mono 600 14px with core note 10px amber under it; meta row (status dot + avail, bin) mono 11px; two buttons "Details" (ghost) / "Add to Quote" (green), both ≥44px tall, equal flex.
- **Quote view**: header shows quote number + Draft chip + customer; line list (pn + ellipsized desc, ×qty, ext); dark total block; Print (ghost) / Convert to Invoice (amber) bottom buttons ≥48px.
- Bottom tab bar: 4 tabs (Lookup, Quotes, Cores, Customers), icon 19px + Oswald 9.5px uppercase, active `#5a6630`.

### 7. Printed quote (Letter, B&W safe)
See `Axle Quote Print.html` — implement as the print stylesheet / PDF template. Letter size, margins .7in/.75in. One-color **ink** logo (no green/amber dependence), 2.5px ink rule under header, three-column meta (From / Prepared for / Terms), items table with 1.5px ink header rule, right-aligned totals block (3.1in) with 2.5px ink rule above grand total, terms box with 3px amber left border (renders gray in B&W fine), mono footer. Body 12.5px ≈ 9.5pt — at or above print floor.

## Interactions & Behavior
- Sidebar nav switches sections; active state as specced. Out-of-scope sections shown at 38% opacity in the prototype.
- Qty steppers: clamp at 0; Ext = unit × qty; totals recompute live (subtotal + per-unit core charges × qty + shop supplies; tax honors customer tax-exempt flag).
- Convert to Invoice: status chip Draft→Invoice (green), total label "Quote Total"→"Invoice Total", action disappears, QuickBooks card shows "invoice queued". In production this is a server action.
- Catalog: search filters across part #, description, category (server-side in production); empty state row "No parts match …". Row click = select + populate drawer. "Add to Quote" navigates to the open quote.
- Print: opens the print document; `window.print()` for PDF.
- Table row hover `#f6f7f2` everywhere. All transitions instant or ≤150ms ease-out; no decorative animation.
- Responsive: 1280 full / 768 icon-rail + condensed columns / 390 bottom-tab app with card lists. Hit targets ≥44px on touch.

## State Management
- `page` (dash | quote | inventory), `selectedPart`, per-line `qty` map, `converted` flag, `searchQuery`.
- Tweak-style preferences worth shipping: table density (regular/compact = td padding 8px→5px), margin-bar visibility (admin-only permission, not a user toggle, in production).
- Server data: quotes (lines, customer, terms, status), parts (stock by location, committed, cores), alerts, QuickBooks sync status + timestamp.

## Design Tokens
```css
/* brand */
--mil-deep:#3c4520; --mil:#5a6630; --mil-2:#6d7b39; --mil-bright:#9cb23c; --mil-glow:#b6cf45;
--amber:#e7891d; --amber-2:#ffb24d; --amber-bg:#fdf3e4; /* amber text on light: #b06511 */
/* status */
--stock:#5a9a3c; --stock-bg:#eaf3e4; --danger:#c0392b; --danger-bg:#f9e9e6;
/* neutrals */
--ink:#0b0c0d; --carbon:#141619; --steel-900:#191c1f; --steel-800:#22262b; --steel-700:#2f343a;
--steel-600:#454b52; --steel-500:#565d65; --steel-400:#7c838b; --steel-300:#a9afb6;
--steel-200:#cdd1d5; --steel-150:#dde0e3; --steel-100:#e7e9eb; --paper:#f2f3f0;
/* type */
--font-disp:"Oswald",sans-serif;      /* headings, labels, buttons, nav — 400/500/600/700 */
--font-body:"Barlow",sans-serif;      /* body, descriptions, table text — 400–700 */
--font-mono:"IBM Plex Mono",monospace;/* part numbers, money, bins, timestamps — 400/500/600 */
```
- Radii: 4px chips · 5–6px buttons/inputs · 10px cards · 14px filter pills.
- Type scale (desktop): table 12.5px / mono-in-tables 11.5px / labels 10–11px uppercase / card values 24–26px mono / page h1 20px Oswald.
- Spacing: 4/8/10/14/16/20 px rhythm; table cells 8px 10px (compact 5px 10px).
- Shadows: essentially none on light surfaces (1px borders instead); dark overlays use real shadows.
- Status colors are reserved: green=ok/in-stock, amber=warning/core/expiring, red=out/danger. Never reuse for decoration.

## Assets
All original (no third-party assets). In `assets/`:
- `axle-icon.svg` — full-color mark for light surfaces
- `axle-icon-reversed.svg` — for dark surfaces
- `axle-icon-white.svg` / `axle-icon-ink.svg` — one-color knockout / B&W print
- `axle-favicon.svg` — simplified build (outer ring + bore only) for ≤24px
- `axle-app-tile.svg` — green app-tile with white knockout mark
Lockups (mark + Oswald wordmark) are intentionally **not** baked into SVGs — render the wordmark as live text next to the icon per the Brand/Logo spec so it stays crisp and translatable. Icons in the UI are inline SVGs (24×24 grid, stroke 2, currentColor) — recreate with your icon library or lift from `erp-shared.jsx`.
Fonts: Oswald, Barlow, IBM Plex Mono — all on Google Fonts (self-host for production/print).

## Files
- `Axle Prototype.html` + `erp-app.jsx` — **clickable prototype** (start here): dashboard → quote → catalog, live totals, search, convert flow
- `Axle ERP Screens.html` — static artboards: chrome comparison, dashboard, catalog, 768/390 frames
- `erp-shared.jsx` / `erp-quote.jsx` / `erp-catalog.jsx` / `erp-dashboard.jsx` / `erp-responsive.jsx` — screen components
- `axle-erp.css` — the complete stylesheet; **the most authoritative reference for exact values**
- `Axle Logo System.html` — full brand spec (anatomy, lockups, color rules, clear space, don'ts)
- `Axle Quote Print.html` — print/PDF quote template
- `tweaks-panel.jsx`, `design-canvas.jsx` — design-review tooling only; **do not implement**
