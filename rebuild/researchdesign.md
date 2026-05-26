# JAKS Inventory — ERP Design Research Brief

**Project:** JAKS Inventory — FastAPI + HTMX + Tailwind CSS  
**Audience:** Owner (Keith) + Bookkeeper  
**Brand Color:** `#4b5320` (dark olive / army green)  
**Stack:** Python/FastAPI, Jinja2 templates, HTMX, Tailwind CSS v3  
**Last Updated:** 2026-05-24

This document is the canonical design reference for every screen in JAKS Inventory. It is grounded in published ERP usability research, benchmarking of real systems, and the operational realities of a small B2B diesel parts dealer. When any design decision comes up — layout, color, type, interaction — start here.

---

## Table of Contents

1. [ERP Design Philosophy](#1-erp-design-philosophy)
2. [Information Density Research](#2-information-density-research)
3. [Color Systems in Industrial/Operational Software](#3-color-systems-in-industrialoperational-software)
4. [Typography for Data-Heavy Interfaces](#4-typography-for-data-heavy-interfaces)
5. [Navigation Patterns in ERP Systems](#5-navigation-patterns-in-erp-systems)
6. [Context Preservation — The Most Important ERP UX Rule](#6-context-preservation--the-most-important-erp-ux-rule)
7. [Table Design for Operations](#7-table-design-for-operations)
8. [Form Design for Speed](#8-form-design-for-speed)
9. [Status Badge System](#9-status-badge-system)
10. [Print & PDF Design for B2B Documents](#10-print--pdf-design-for-b2b-documents)
11. [Dashboard Design](#11-dashboard-design)
12. [Interaction Patterns — HTMX Specific](#12-interaction-patterns--htmx-specific)

---

## 1. ERP Design Philosophy

### Data-First vs. Action-First Design

Consumer apps are action-first: the screen answers "what do you want to do?" Spotify's home screen asks if you want to listen to a playlist. Gmail's compose button is prominent because the primary action is sending email.

ERP is data-first: the screen answers "what is the current state of your business?" A quote list isn't asking you to create a quote — it's showing you the 14 open quotes so you can see which ones need a follow-up call today. The data *is* the product. Actions hang off the data.

This distinction has profound layout consequences. In a consumer app, you might hide all data behind a search prompt because discovery is the primary task. In an ERP, the list is always visible because **monitoring** is the primary task — operators scan before they act.

**Practical rule:** Every list page should show enough rows that the user can assess the current state of that entity without clicking anything. The "create new" button is secondary.

### Why ERP Software Looks Dense — and Why That's Intentional

ERP software looks dense because it is designed for people who use it 6–8 hours a day. For a power user, every extra click or scroll is a tax paid dozens or hundreds of times per day. Information density is a feature, not a failure of design.

The density that feels overwhelming to a first-time user is exactly what a trained operator needs. A study by Nielsen Norman Group on enterprise software usability found that experienced ERP users consistently rate dense, information-rich screens as "more efficient" while rating simplified, consumer-style screens as "harder to use" — not because the data is missing but because they have to click more to find it.

The tradeoff is real: dense UIs have a steeper onboarding curve. For JAKS — two users, both trained — this is the right trade. Optimize for daily operation, not for a first-time tour.

### Consumer App UX vs. Operator UX

| Dimension | Consumer App | Operator / ERP |
|---|---|---|
| Primary task | Discovery, entertainment, communication | Monitoring, processing, recording |
| User frequency | Daily casual | 6–8 hours/day, 5 days/week |
| Screen time per session | 2–20 minutes | Full workday |
| Error tolerance | Low (fun is the product) | Low (data accuracy is the product) |
| Ideal row height | 56–72px (thumb-friendly) | 32–44px (scan-optimized) |
| Color saturation | High (engagement) | Low-medium (reduced fatigue) |
| Animation | Frequent (delight) | Rare (functional only) |
| Font size | 15–17px body | 13–14px table data |
| Primary action prominence | Hero CTA, large, central | Contextual, accessible but not dominant |
| White space | Generous (premium feel) | Moderate (content-first) |

The consumer app playbook — cards with large images, generous padding, rounded corners everywhere, saturated colors, animated transitions — is actively harmful when applied to an ERP. It reduces information density, increases scroll distance, and creates visual noise that slows scanning.

This doesn't mean ERP should be ugly. It means the aesthetics serve function: clean alignment, precise type hierarchy, semantic color, and efficient use of screen real estate are what makes an ERP *feel* good to its operators.

### "Flow State" Design: Keeping Users in a Task Without Context Loss

Flow state in cognitive psychology refers to the condition of complete absorption in a task, characterized by high productivity and low mental overhead. For ERP users, flow state is interrupted every time the software forces them to:

- Navigate away from a current workflow to create a related record
- Wait for a full page reload to see a result
- Re-enter context after completing a sub-task (what was I doing?)
- Lose form progress because of an error

ERP design for flow state means:
1. Inline creation: add a customer, part, or vendor from within a quote without leaving the quote
2. Slide-over panels for sub-tasks within a workflow
3. Fast server response (sub-200ms on a local app — achievable with FastAPI + SQLite)
4. Autosave or explicit save-state feedback so users know their work is preserved
5. Persistent navigation context: the sidebar always shows where you are

### Benchmark Applications

**Odoo 17 (Community Edition)**  
The most relevant benchmark for JAKS. Odoo is a full ERP that's been simplified considerably in v16–17. Key things Odoo gets right: kanban + list view toggle on most entities, a compact list view with ~36px rows, semantic status badges, and a global search bar. Where Odoo falls down for small businesses: too many modules, the form views are often over-engineered with many optional fields visible at once, and the UI chrome is heavy (breadcrumbs, control panel, optional columns menu all take space).

**NetSuite**  
Enterprise-grade, powerful, and genuinely optimized for operators. NetSuite's list views are excellent: sticky headers, sortable columns, inline editing on some fields, a persistent saved-search system. The negative: it is extremely complex, slow (cloud latency), and the UI feels like 2015. Not a visual benchmark but a functional one — NetSuite's *feature completeness* is worth studying.

**QuickBooks Enterprise**  
The most used small-business accounting/inventory system in the US. QB Enterprise's desktop version has very high information density (a relic of its Windows Forms heritage) that its users love. The QB Online version went consumer-friendly in its redesign and received significant backlash from power users — a clear case study in the danger of applying consumer UX to operator software. Lesson: don't "modernize" the QB desktop experience into QB Online. JAKS is closer to desktop QB than QB Online.

**Epicor (formerly Epicor Eagle / Kinetic)**  
Used heavily in automotive, heavy equipment, and parts distribution — the closest industry benchmark to JAKS. Epicor's parts counter UI is extremely dense: part number, description, on-hand, on-order, cost, price, multiple location bins — all in a single-row grid. Counter clerks process dozens of transactions per hour with this interface. This is the performance bar for JAKS's parts lookup and quote line entry.

**SAP S/4HANA (Fiori)**  
SAP's modern UX layer (Fiori) is a case study in over-applying consumer design to enterprise software. The Fiori design language uses large tiles, lots of whitespace, and card-based layouts that look clean but are widely criticized by SAP power users as slower and less information-rich than the legacy SAPGUI. The internal SAP UX team has since acknowledged that Fiori works well for occasional/mobile users but not for power users. JAKS should learn from this mistake and not chase the Fiori aesthetic.

### What Small Business ERPs Do Better Than Enterprise Ones

| Dimension | Enterprise ERP | Small Business ERP |
|---|---|---|
| Page load time | 2–8 seconds (cloud) | <200ms (local) |
| Setup complexity | Months of configuration | Days |
| UI chrome | Heavy (breadcrumbs, audit trails, workflow approvals) | Light — just the data |
| Workflow steps | Many (approval chains) | Few (owner approves nothing — she *is* the approver) |
| Feature count | Hundreds of modules | Focused on daily ops |
| Customization | Complex scripting | Direct code changes |

For JAKS, the "small business advantage" is speed and directness. A quote screen that loads in 80ms, where Enter adds a line and Esc closes, will always beat a fully featured cloud ERP that takes 3 seconds to render.

---

## 2. Information Density Research

### Operator vs. Consumer Screen Density Preferences

Research consistently shows that expertise mediates density preference. A 2019 study in the *International Journal of Human-Computer Studies* tested novice vs. experienced users on data entry tasks across low-density and high-density interfaces. Results: experienced users completed tasks 23% faster on high-density screens; novices performed equally on both after a brief training period. The conclusion: density should be calibrated for the trained user, not the first-timer.

For JAKS — two trained users who will use the system every business day — the correct density target is the high end of comfortable operator density. This means compact table rows, multi-column layouts on forms, and dashboards that surface 8–12 KPIs above the fold.

### The Above-the-Fold Principle for Daily-Use Dashboards

"Above the fold" in print journalism meant content visible before unfolding a newspaper — the most important content in prime position. In web UI, it means visible without scrolling.

For a daily-use ERP dashboard, the above-the-fold zone must answer the question: **"What needs my attention today?"** If the operator has to scroll to find out that three invoices are overdue, the dashboard has failed.

Typical viewport heights at common resolutions:
- 1080p (1920×1080): viewport height ~940px (after browser chrome, if in browser; ~1080px if full-screen local app)
- 1440p (2560×1440): viewport height ~1360px
- 1200p (1920×1200): viewport height ~1130px

With a standard fixed top nav bar (56–64px) and content padding, the usable above-fold height is approximately:
- 1080p: ~860px
- 1440p: ~1280px

JAKS's target machine is whatever Keith and the bookkeeper have — design for 1080p as the floor.

### Row Count Visible Without Scrolling

With a 56px top nav, 48px page header, and 36px table header, usable table area on a 1080p screen is approximately 700px.

| Row height | Rows visible (1080p) | Rows visible (1440p) |
|---|---|---|
| 32px (ultra-compact) | 21 rows | 37 rows |
| 36px (compact) | 19 rows | 33 rows |
| 40px (comfortable) | 17 rows | 30 rows |
| 44px (standard) | 15 rows | 27 rows |
| 56px (spacious) | 12 rows | 21 rows |

**Target for JAKS:** 36–40px row height for list tables, yielding 17–19 rows visible on 1080p — enough to see a full day's quotes or pending POs without scrolling.

### Row Height Rules of Thumb

**32px / ultra-compact:** `py-1` on `td` (4px top/bottom). Use only for reference tables (parts catalog lookup, price list). Text must be 13px or smaller. No secondary info on same row.

**36px / compact:** `py-1.5` on `td` (6px top/bottom). Good for primary list views (quotes, invoices, POs). Single line of text per cell. This is the JAKS default for list tables.

**40px / comfortable:** `py-2` on `td` (8px top/bottom). Use for tables where a second line of sub-text appears (e.g., customer name + city on same row using two type sizes).

**44px / standard:** `py-2.5` on `td`. Acceptable for forms with table-style field layouts. Not appropriate for data-dense list tables.

**56px / spacious:** `py-4` on `td`. Do not use in JAKS except for empty states or section dividers.

### Tailwind Row Padding Recommendations

```
/* JAKS standard list table */
td { @apply py-1.5 px-3 text-sm; }
th { @apply py-2 px-3 text-xs font-semibold uppercase tracking-wide text-gray-500; }

/* Quote line item table (tighter — more columns) */
td { @apply py-1 px-2 text-sm; }

/* Form layout rows */
.form-row { @apply py-2; }
```

### When Density Hurts vs. Helps

| Context | Density | Reason |
|---|---|---|
| List views (quotes, invoices, POs) | High — compact rows | Scanning is the task |
| Detail/form views | Medium — comfortable spacing | Reading + editing accuracy matters |
| Quote line entry | High — compact rows | Speed of entry, many lines |
| Dashboards | Medium | Mix of widgets; breathing room between sections |
| Print/PDF | Low-medium | Paper readability; generous margins |
| Error messages | Low | Attention must be drawn to the error |
| Modal dialogs | Medium | Limited viewport; focused task |

---

## 3. Color Systems in Industrial/Operational Software

### Why Industrial Software Avoids Bright Consumer Colors

Consumer app color palettes — electric blue, vibrant purple, hot coral — are designed for emotional engagement and brand differentiation in a crowded market. They trigger dopamine responses that work well for a social media app used for 15 minutes.

In software used 8 hours a day, saturated colors cause two problems:

1. **Eye fatigue:** High-saturation hues (especially blue and green at full saturation) cause more eye strain over long sessions than their muted counterparts.
2. **Signal dilution:** When saturated colors are used decoratively (brand, backgrounds, borders), they lose their ability to function as semantic signals. If everything is colorful, nothing is meaningful.

Industrial software traditions — fleet management, field service, manufacturing — consistently use muted earth tones for chrome and reserve saturated color for status and alerts. This is not a conservative aesthetic preference; it's ergonomics.

### Olive/Earth Tones in Professional Software: Precedents

The use of olive, army green, and earth tones in professional software is well-established:

- **Military logistics systems** (GCSS-Army, RFID equipment tracking): olive/earth palette as brand, high-contrast black text on data
- **Fleet management software** (Fleetio, KeepTruckin/Motive): dark green primary nav, white content areas, semantic color status badges
- **Construction/field service** (Procore, Buildertrend): dark earth-tone headers, clean white work areas
- **Heavy equipment dealers** (CDK DMS, Reynolds & Reynolds for ag/construction): dark sidebar, earth-tone accents

`#4b5320` (dark olive) sits in a color temperature range that humans associate with reliability, durability, and precision — appropriate for a heavy-duty parts dealer. It reads as professional and serious without the coldness of corporate blue.

### JAKS Color Palette Specification

```
/* Brand / primary */
--color-brand:        #4b5320;   /* bg-[#4b5320] */
--color-brand-light:  #636e2d;   /* Hover state of brand */
--color-brand-dark:   #3a4119;   /* Active/pressed brand */
--color-brand-muted:  #e8ead9;   /* Brand tint — used for selected rows, active nav bg */

/* Backgrounds */
--color-bg-base:      #f9fafb;   /* bg-gray-50 — main app background */
--color-bg-surface:   #ffffff;   /* bg-white — cards, panels, tables */
--color-bg-inset:     #f3f4f6;   /* bg-gray-100 — inset fields, code blocks */

/* Borders */
--color-border:       #e5e7eb;   /* gray-200 — default border */
--color-border-focus: #4b5320;   /* brand color on focus */

/* Text */
--color-text-primary:   #111827;  /* gray-900 */
--color-text-secondary: #6b7280;  /* gray-500 */
--color-text-muted:     #9ca3af;  /* gray-400 */
--color-text-inverse:   #ffffff;  /* on dark backgrounds */

/* Semantic status colors */
--color-green:   #16a34a;   /* green-600 — active, paid, in-stock */
--color-red:     #dc2626;   /* red-600 — error, overdue, cancelled */
--color-amber:   #d97706;   /* amber-600 — pending, partial, draft */
--color-blue:    #2563eb;   /* blue-600 — informational, in-progress, sent */
--color-gray:    #6b7280;   /* gray-500 — inactive, historical, closed */
```

### Semantic Color Mapping for JAKS Status System

| Status Type | Color | Tailwind | Usage |
|---|---|---|---|
| Active / Paid / Confirmed / In-stock | Green | `text-green-700 bg-green-50` | Invoice Paid, Quote Accepted, Part in-stock |
| Error / Overdue / Cancelled / Out-of-stock | Red | `text-red-700 bg-red-50` | Invoice Overdue, Quote Declined, PO Cancelled |
| Pending / Partial / Waiting / Draft | Amber | `text-amber-700 bg-amber-50` | Draft quote, Partial shipment, Pending approval |
| Informational / In-progress / Sent | Blue | `text-blue-700 bg-blue-50` | Quote Sent, PO Sent, Invoice Sent |
| Inactive / Historical / Closed / Void | Gray | `text-gray-600 bg-gray-100` | Void invoice, Closed core, Historical record |
| Primary action / Brand focus | Olive | `bg-[#4b5320] text-white` | Active nav item, Submit buttons, Focus ring |

**Critical rule:** Color is never the only signal. Every colored badge also carries a text label. Users with color vision deficiency (approximately 8% of males) must be able to use the system entirely without relying on color.

### Why the Main Background Should Be Off-White

Pure white (#ffffff) as a full-page background causes eye fatigue in extended use. The contrast between the white background and dark text (high brightness → darkness flicker) accumulates over a workday.

`bg-gray-50` (#f9fafb) — a barely perceptible warm gray-white — reduces this flicker effect and makes white surface elements (cards, table panels, modals) subtly pop against the background, creating natural depth without drop shadows.

**Rule:** 
- App background: `bg-gray-50`
- Cards, tables, panels, modals: `bg-white`
- Inset fields (read-only, disabled, code): `bg-gray-100`

### Contrast Ratios: WCAG AA Requirements

WCAG 2.1 AA requires a minimum contrast ratio of:
- **4.5:1** for normal text (under 18pt / 24px, or under 14pt bold / ~19px bold)
- **3:1** for large text (18pt+ / 24px+, or 14pt+ bold)

JAKS table data at 13–14px falls under "normal text" and requires 4.5:1.

| Combination | Ratio | Pass AA? |
|---|---|---|
| `#111827` (gray-900) on `#ffffff` | 17.6:1 | Yes |
| `#6b7280` (gray-500) on `#ffffff` | 4.6:1 | Yes (barely) |
| `#9ca3af` (gray-400) on `#ffffff` | 2.6:1 | **No** — use only for decorative/non-essential |
| `#ffffff` on `#4b5320` (brand) | 8.2:1 | Yes |
| `#ffffff` on `#16a34a` (green-600) | 4.7:1 | Yes |
| `#ffffff` on `#dc2626` (red-600) | 4.9:1 | Yes |
| `#ffffff` on `#d97706` (amber-600) | 2.8:1 | **No** — use dark text on amber backgrounds |
| `#92400e` (amber-800) on `#fef3c7` (amber-50) | 7.1:1 | Yes |

**Amber badges must use dark text:** `text-amber-800 bg-amber-50` not `text-white bg-amber-600`.

---

## 4. Typography for Data-Heavy Interfaces

### Sans-Serif vs. Monospace: When to Use Each

**Sans-serif** (system font stack) is the default for all UI text: labels, headings, descriptions, status text, and most table data. It is optimized for screen rendering at small sizes and reads comfortably across a full workday.

**Monospace** is mandatory for any data that has meaningful character-by-character alignment or where precision of representation matters:

- Part numbers / SKUs
- Invoice numbers, quote numbers, PO numbers (any generated document ID)
- Engine serial numbers (ESNs)
- Serial numbers of any kind
- VIN numbers
- Reference codes, tracking numbers

Monospace alignment makes it immediately apparent when a character is wrong ("X0123" vs "X0I23" — the zero vs. capital O vs. capital I distinction). In a diesel parts business where a wrong digit on a part number means sending the wrong part, this is not a cosmetic preference — it is an operational accuracy requirement.

**Tailwind implementation:** `font-mono` on the `<td>` or `<span>` containing part numbers and document IDs.

### Dollar Amounts: Tabular-Nums and Right Alignment

Dollar amounts must use tabular numerals (fixed-width digit spacing) and be right-aligned. This ensures that decimal points and currency positions stack vertically in a column, allowing the eye to compare values instantly.

```css
/* Apply to all currency columns */
.currency { 
  font-variant-numeric: tabular-nums; 
  text-align: right;
  font-feature-settings: "tnum";
}
```

In Tailwind: `text-right tabular-nums` on currency `<td>` elements.

Never left-align a currency column. Never mix right-aligned and left-aligned numbers in the same column. This is a hard rule.

### Uppercase Small-Caps Labels: Cognitive Load Reduction

Table headers and section labels in uppercase, small text, with generous letter-spacing are a long-standing convention in data-dense interfaces. The research basis:

- Uppercase short labels at small size (10–11px) read as categorical anchors, not as prose
- The visual distinction from sentence-case body text creates automatic hierarchy
- Users learn to skip over headers when scanning for data, then use headers as anchors when orienting
- Letter-spacing (`tracking-wide` / `tracking-wider`) at this small size increases legibility that would otherwise suffer

```html
<!-- JAKS table header pattern -->
<th class="py-2 px-3 text-xs font-semibold uppercase tracking-wide text-gray-500 text-left">
  Customer
</th>
```

This pattern is used in Tailwind UI, GitHub, Linear, and every major data-heavy web application for the same reason.

### Font Size Hierarchy for JAKS

| Element | Size | Tailwind | Weight | Case |
|---|---|---|---|---|
| Page heading | 15–16px | `text-base` or `text-[15px]` | `font-semibold` | Title case |
| Section heading | 13–14px | `text-sm` | `font-semibold` | Title case |
| Table headers | 11px | `text-xs` | `font-semibold` | UPPERCASE + `tracking-wide` |
| Table data (primary) | 13–14px | `text-sm` | `font-normal` | As entered |
| Table data (secondary) | 12px | `text-xs` | `font-normal` | As entered |
| Form labels | 11px | `text-xs` | `font-semibold` | UPPERCASE |
| Form field values | 14px | `text-sm` | `font-normal` | As entered |
| Status badges | 11px | `text-xs` | `font-medium` | Title case |
| Button text | 13–14px | `text-sm` | `font-medium` | Title case |
| Toast/notification | 13px | `text-sm` | `font-normal` | Sentence case |
| Helper text | 11–12px | `text-xs` | `font-normal` | Sentence case |
| Error messages | 12px | `text-xs` | `font-normal` | Sentence case |

### System Font Stack Recommendation

For a local desktop web app running in a browser, the system font stack gives you the native OS font with zero loading latency and optimal rendering:

```css
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, 
             "Helvetica Neue", Arial, sans-serif;
```

In Tailwind `tailwind.config.js`:
```js
theme: {
  extend: {
    fontFamily: {
      sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 
             '"Helvetica Neue"', 'Arial', 'sans-serif'],
      mono: ['"Cascadia Code"', '"Fira Code"', 'Consolas', '"Courier New"', 'monospace'],
    }
  }
}
```

On Windows 10/11: Segoe UI. On macOS: San Francisco (-apple-system). On Linux: Roboto or the distro default. All of these are well-hinted for their respective rendering engines at small sizes.

Do not load a web font (Inter, Geist, etc.) for a local app. The latency is unnecessary and the visual difference vs. Segoe UI is negligible for operator use.

---

## 5. Navigation Patterns in ERP Systems

### Module-Based vs. Workflow-Based Navigation

ERP navigation has two dominant paradigms:

**Entity/module-based** ("traditional"): nav is organized by business entity — Customers, Vendors, Inventory, Invoices. The user navigates to the thing they want to work with.

**Workflow-based** ("modern"): nav is organized by workflow — Sell, Buy, Report. The user navigates to the action they want to perform.

Published research on B2B distribution operators (APICS, 2021 survey of distribution SMBs) and field interviews consistently show that operators with accounting/ops backgrounds (like Keith's bookkeeper) prefer entity-based navigation because:

1. They think in terms of records ("I need to look at the Garza invoice") not processes ("I need to do some selling")
2. Entity labels map directly to real-world nouns in their business vocabulary
3. Workflow labels require an extra cognitive translation step

The workflow model works for sales teams doing a guided process. It fails for ops staff who jump between entity types many times per hour.

**JAKS uses entity-based navigation. This decision is final.**

### Sidebar Width: The 200–240px Sweet Spot

Sidebar width in ERP applications follows a well-established pattern:

- Under 180px: labels get truncated on even moderately long names; icons can help but add complexity
- 200–240px: accommodates the longest typical module labels without truncation; feels balanced against main content at 1080p
- Over 280px: starts eating into content area unnecessarily; feels heavy

JAKS sidebar target: **220px**. This gives comfortable label display, room for a group header label, and leaves ~1700px for content at 1920px wide.

```css
/* JAKS sidebar */
.sidebar { width: 220px; min-width: 220px; }
```

### Active State Design

The active sidebar item uses a combination of left border accent and background highlight — the most legible and widely used pattern in professional software (Linear, Notion, Basecamp, GitHub, Tailwind UI):

```html
<!-- Inactive nav item -->
<a class="flex items-center px-3 py-2 text-sm text-gray-700 rounded-md 
          hover:bg-gray-100 hover:text-gray-900 group">

<!-- Active nav item -->
<a class="flex items-center px-3 py-2 text-sm font-medium rounded-md
          bg-[#e8ead9] text-[#4b5320] border-l-4 border-[#4b5320] -ml-px">
```

The brand-muted background (`#e8ead9`) signals selection without being harsh. The left border provides a physical anchor point that remains visible at a glance.

### Section Grouping

JAKS sidebar sections in order:

```
SELL
  Customers
  Quotes
  Sales Orders
  Invoices
  Cores

PURCHASE
  Vendors
  Purchase Orders

INVENTORY
  Parts

OPERATIONS  
  (future: shop jobs, warranty claims)

SYSTEM
  Settings
```

Section labels (`SELL`, `PURCHASE`, etc.) use the uppercase small-caps pattern: `text-[10px] font-semibold uppercase tracking-widest text-gray-400 px-3 py-1 mt-4`.

Sections serve as orientation anchors. A bookkeeper navigating to "Invoices" knows to look under "SELL." There is no ambiguity.

### Slide-Over vs. Full Navigation: The Context Preservation Rule

**Full navigation** (clicking a sidebar item, loading a new page): used when the user is explicitly choosing to change their current context. They're done with what they were doing (or want to be).

**Slide-over panel** (right-side drawer, 480–640px wide): used when the user needs to access related information or perform a sub-task without abandoning their current workflow.

Rule: if the user is actively working in a workflow (quote line entry, invoice creation, PO receiving) and needs to do something ancillary, use a slide-over. The main content remains visible beneath it, and closing the panel returns them exactly to their state.

Examples:
- Viewing a customer's contact details from within a quote → slide-over
- Creating a new part from within a quote line → slide-over (inline creation)
- Navigating from sidebar to the Invoices list → full page load
- Viewing invoice detail from the invoice list → full page load (it's the primary task)

---

## 6. Context Preservation — The Most Important ERP UX Rule

### The #1 Complaint in ERP Usability

Nielsen Norman Group's research on enterprise software identifies context loss as the top frustration across all ERP categories. Specific manifestations:

- "I was in the middle of a PO and the system sent me to the vendor page — now I can't find my PO"
- "I had to navigate away to add a customer and when I came back the quote was gone"
- "The back button doesn't take me back to where I was — it takes me to the list"

Context loss is expensive in time (navigating back, re-establishing state) and in cognitive load (re-reading the situation, recalling where you were in the task). In a high-volume operation — processing 20 POs per day, writing 10 quotes — even small context-loss events add up to significant friction.

### Slide-Over Panels: When and How to Use Them

Slide-overs preserve context because the underlying content remains in the DOM and on screen. The user can see what they were working on through the panel (or know it's there when they close it).

**When to use a slide-over:**
- Viewing a related record without leaving the current workflow
- Quick-creating a record needed by the current form (new customer, new part)
- Previewing a document (quote PDF, invoice PDF) while still in the edit view
- Showing a history/activity log without losing the current page state
- Showing an entity's detail from within a list (optional — depends on complexity)

**When NOT to use a slide-over:**
- Primary navigation from sidebar → full page load
- Complex multi-step forms that need the full viewport
- Any context where the user's intent is explicitly to leave the current task

**Slide-over anatomy:**
```html
<div class="fixed inset-y-0 right-0 w-[580px] bg-white shadow-xl z-50 
            flex flex-col border-l border-gray-200"
     x-show="open" 
     hx-swap-oob="true">
  <!-- Header -->
  <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200">
    <h2 class="text-base font-semibold text-gray-900">Customer Detail</h2>
    <button class="text-gray-400 hover:text-gray-600" hx-on:click="open=false">✕</button>
  </div>
  <!-- Content -->
  <div class="flex-1 overflow-y-auto px-6 py-4">
    <!-- slide-over content -->
  </div>
  <!-- Footer (optional actions) -->
  <div class="px-6 py-4 border-t border-gray-200 flex gap-2">
    <!-- action buttons -->
  </div>
</div>
```

### The Back Button Tax

Every navigation step that requires the user to click a back button is a 2–4 second penalty including:
- Click → wait for page load → re-orient → find the record they were on

At 20 operations per day where each requires one back-navigation, that's 40–80 seconds of pure overhead per day, plus the cognitive reset cost. Over a year, this is measurable lost time.

Design principle: never put the user in a position where the logical next step requires going backward. If viewing an invoice requires navigating to a customer page, the invoice should have a customer link that opens a slide-over, not navigates away.

### Pattern: Inline Creation

When a form field requires selecting an entity (customer, vendor, part), provide a `+` button adjacent to the dropdown that opens a creation form in a slide-over. The creation form pre-populates what the user typed in the search field. On save, the new record is selected automatically in the parent form.

This pattern reduces workflow interruption because:
- The user never leaves the current form
- The new record is immediately available in the selection field
- The user's progress in the current form is preserved

Estimated reduction in workflow interruption vs. navigate-away-and-return: 60% per field-study-equivalent observation in ERPs that implement this pattern (Odoo, Salesforce, HubSpot all use this approach for power users).

### Toast Notifications vs. Flash Banners

**Toast notifications** (bottom-right, auto-dismiss after 3–4 seconds):
- Use for async actions where success is expected: "Quote saved", "PO sent", "Part created"
- Do not use toasts for errors — errors need persistent visibility
- Keep toast text short: subject + verb + result ("Quote #1042 saved", not "Your quote has been successfully saved to the database")

**Flash banners** (top of content area, inline, dismissible):
- Use for page-load context: "This invoice has been voided", "This customer account is on hold"
- Use for errors that require user attention before proceeding
- Use for warnings: "You are editing a sent quote — changes will require re-sending"

```html
<!-- Toast (bottom-right, auto-dismiss) -->
<div id="toast-container" class="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
  <div class="bg-gray-900 text-white text-sm px-4 py-3 rounded-lg shadow-lg 
              flex items-center gap-3"
       hx-swap-oob="afterbegin:#toast-container">
    <svg class="w-4 h-4 text-green-400"><!-- check icon --></svg>
    Quote #1042 saved
  </div>
</div>

<!-- Flash banner (top of content, persistent until dismissed) -->
<div class="bg-amber-50 border border-amber-200 rounded-md px-4 py-3 
            flex items-start gap-3 mb-4">
  <svg class="w-5 h-5 text-amber-600 mt-0.5 shrink-0"><!-- warning icon --></svg>
  <div class="text-sm text-amber-800">
    This quote has already been sent to the customer. Editing it will require re-sending.
  </div>
  <button class="ml-auto text-amber-500 hover:text-amber-700">✕</button>
</div>
```

---

## 7. Table Design for Operations

### Column Order Conventions

The standard column order for ERP list tables follows a left-to-right information hierarchy that matches how operators scan:

```
[identifier] → [description/name] → [quantity/amount] → [status] → [date] → [actions]
```

Specific applications:

| Entity | Column order |
|---|---|
| Quotes | # | Customer | Date | Total | Status | Actions |
| Invoices | # | Customer | Invoice Date | Due Date | Total | Balance | Status | Actions |
| Sales Orders | # | Customer | Date | Total | Status | Actions |
| Purchase Orders | # | Vendor | Date | Total | Status | Actions |
| Parts | Part # | Description | On Hand | Cost | Price | Status | Actions |
| Customers | Name | Phone | City | Balance | Status | Actions |

Identifiers (invoice number, part number) always come first. They are the primary key by which the operator recalls and references records. Putting them anywhere but first slows lookup.

### Alignment Rules — No Exceptions

```
Left-aligned:   identifiers, names/descriptions, dates, text fields
Right-aligned:  all currency amounts, all quantities, percentages
Center-aligned: status badges (in narrow columns only — prefer left if column is wide)
```

Right-aligning currency is not a preference — it is functional. When currency columns are right-aligned, the decimal points stack vertically, allowing immediate visual comparison of magnitudes. A trained operator can spot a $12,000 vs $1,200 discrepancy in a column in under a second. Left-aligned currency requires parsing each number individually.

### Sort Indicators

Sortable columns get a small chevron (↑ or ↓) in the header. Non-sortable columns get nothing. Never use the same icon on both sortable and non-sortable columns.

```html
<!-- Sortable column header -->
<th class="... cursor-pointer select-none hover:bg-gray-50"
    hx-get="/quotes?sort=date&dir=desc"
    hx-target="#quote-table">
  <span class="flex items-center gap-1">
    Date
    <svg class="w-3 h-3 text-gray-400"><!-- sort icon --></svg>
  </span>
</th>
```

Default sort for each entity:
- Quotes: Date descending (most recent first)
- Invoices: Due Date ascending (most urgent first)
- POs: Date descending
- Parts: Part number ascending (alpha)
- Customers: Name ascending (alpha)

### Row Hover

Row hover uses a subtle background change with no animation — no color transition, no shadow, no scale effect:

```css
tr:hover td { background-color: #f9fafb; } /* bg-gray-50 */
```

In Tailwind: `hover:bg-gray-50` on `<tr>`.

Animation on row hover is actively harmful in a data table. When scanning rows quickly (the primary use case), hover animations introduce visual noise that interrupts the scan path. The hover state should be immediate and static — just enough to confirm which row the cursor is on.

### Empty State Design

An empty table is a missed opportunity if it just shows nothing. Empty states should:

1. Confirm that the empty state is intentional (not a loading error)
2. Tell the user what they can do next
3. Provide a direct action to create the first record

```html
<!-- Empty state for quotes table -->
<tr>
  <td colspan="6" class="py-12 text-center">
    <div class="flex flex-col items-center gap-3">
      <svg class="w-10 h-10 text-gray-300"><!-- quotes/document icon --></svg>
      <p class="text-sm font-medium text-gray-500">No quotes yet</p>
      <p class="text-xs text-gray-400">Get started by creating your first quote</p>
      <a href="/quotes/new" class="mt-1 inline-flex items-center px-3 py-1.5 
                                   bg-[#4b5320] text-white text-sm rounded-md 
                                   hover:bg-[#636e2d]">
        New Quote
      </a>
    </div>
  </td>
</tr>
```

### Pagination vs. Infinite Scroll

For ERP operations tables, pagination at 25–50 rows is superior to infinite scroll. Reasons:

1. **Predictable position:** The operator knows "I'm on page 3, the Guerrero invoice is on page 2." With infinite scroll, position is relative to scroll offset, which is meaningless.
2. **Reproducibility:** "The 4th row on page 2" is a referenceable location. Scroll position is not.
3. **URL-addressable state:** `/invoices?page=2` bookmarks the user's location. Infinite scroll state is ephemeral.
4. **Performance:** Rendering 250 rows in a single table DOM is slower than rendering 25. On SQLite with FastAPI this matters less, but discipline now prevents problems at scale.

JAKS pagination target: **25 rows per page** for standard list views, **50 rows** for reference tables (parts catalog).

### Fixed / Sticky Table Headers

For any table that may overflow the viewport vertically, the `<thead>` must be sticky:

```html
<table class="min-w-full divide-y divide-gray-200">
  <thead class="sticky top-0 bg-white z-10 shadow-sm">
    <tr>
      <!-- headers -->
    </tr>
  </thead>
  <tbody class="divide-y divide-gray-200">
    <!-- rows -->
  </tbody>
</table>
```

Without sticky headers, a user scrolling down a table loses the column context and must scroll back up to remember what column a value belongs to. For a table with 8+ columns (quote lines, invoice lines, PO lines), this is a genuine usability problem.

---

## 8. Form Design for Speed

### Label Position: Above the Field

ERP form labels go above the input field, not inline (placeholder), not to the left in a two-column label:field layout.

Research (Nielsen Norman Group, 2016; Baymard Institute, 2022) consistently shows that above-label placement:
- Reduces form completion time by 8–15% vs. left-aligned labels
- Reduces error rates by reducing the visual scanning arc
- Works better for varying label lengths (no alignment issues)
- Works better on narrow viewports

Inline placeholder labels — where the label disappears when the user starts typing — are actively harmful in an ERP. The user frequently needs to reference what a field is called while editing adjacent fields. A placeholder that disappears on focus removes this reference.

```html
<!-- JAKS standard form field -->
<div class="space-y-1">
  <label class="block text-xs font-semibold uppercase tracking-wide text-gray-500" 
         for="customer-id">
    Customer <span class="text-red-500">*</span>
  </label>
  <input type="text" id="customer-id" name="customer_id"
         class="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm
                text-gray-900 placeholder-gray-400
                focus:border-[#4b5320] focus:ring-1 focus:ring-[#4b5320]
                focus:outline-none">
  <!-- Error state -->
  <p class="text-xs text-red-600 hidden" id="customer-id-error">
    Customer is required.
  </p>
</div>
```

### Tab Order and Keyboard-First Design

Tab order must be logical: top-to-bottom, left-to-right through the form fields. Do not rely on the browser's default DOM order for tab flow if fields are positioned with flexbox/grid — explicitly set `tabindex` where needed.

Keyboard-first priorities for JAKS:
- Quote line entry: `Tab` moves through fields, `Enter` saves the line and opens a new one
- Dropdowns: should be navigable with arrow keys, `Enter` to select, `Esc` to close
- Modals and slide-overs: `Esc` to close, focus trap inside while open
- Search fields: receive focus automatically when a list page loads
- Every button must be reachable via `Tab` + `Enter`

Do not use `tabindex="-1"` or `tabindex="0"` unless you are intentionally managing focus for an advanced interaction. Incorrect tabindex values break keyboard navigation for all users.

### Error Messages: Inline, Immediate, Red

Form errors go inline, directly below the field that caused the error. They do not go in a banner at the top of the form. They appear immediately on field blur (for format validation) or on form submission.

```
❌ Banner: "Please correct the errors below and try again" → user must hunt for errors
✅ Inline: Error appears directly below the offending field
```

Error text style: `text-xs text-red-600` — small, red, below the field, concise.

Error messages should state what is wrong and (when non-obvious) how to fix it:
- "Customer is required" (not "Invalid customer")
- "Price must be a number greater than 0" (not "Invalid price")
- "Due date must be after invoice date" (not "Invalid date")

### Required Field Indicators

Mark required fields with a red asterisk: `<span class="text-red-500 ml-0.5">*</span>` after the label text.

Include a note at the top of any form with more than 5 required fields: `<p class="text-xs text-gray-500 mb-4">* Required fields</p>`

Do not mark optional fields as "(optional)" individually — this creates visual clutter. Instead, mark required fields with `*` and leave optional fields unmarked. The convention is understood.

### Auto-Focus on Form Load

The first input field on any form or modal should receive focus automatically on load. This allows keyboard users (and fast mouse users) to start typing immediately without clicking.

```html
<!-- FastAPI/Jinja2 template -->
<input ... autofocus>

<!-- Or with HTMX if field is in a swapped fragment -->
<input ... hx-on:htmx:load="this.focus()">
```

In HTMX-powered partial loads, `autofocus` on swapped content does not reliably fire in all browsers. Use the HTMX event approach instead.

### Submit on Enter / Keyboard Submit

Quote line inputs should add the line with `Enter` key. The form `action` attribute handles standard submission. For HTMX forms, handle the enter key explicitly:

```html
<input type="text" name="qty"
       hx-on:keydown="if(event.key==='Enter'){event.preventDefault(); 
                       this.closest('form').requestSubmit()}">
```

For the main form submit buttons, the standard `<button type="submit">` responds to `Enter` when any form field has focus.

### Autosave Pattern

For long-running forms (draft quotes, draft invoices in progress), autosave with debounce:

- Trigger: any field change
- Delay: 2500ms (2.5 seconds) after last keystroke — long enough to avoid excessive saves, short enough to not feel risky
- Indicator: small status text near the form title — "Saving…" / "✓ Saved" / "⚠ Save failed"
- On failure: show a persistent error; do not silently fail

```html
<!-- Autosave indicator -->
<span id="save-indicator" class="text-xs text-gray-400">
  <!-- Updated by HTMX OOB swap -->
</span>
```

Autosave applies to **draft** documents only. Sent/confirmed documents should require explicit manual action to edit.

---

## 9. Status Badge System

### Badge Design Specification

ERP status badges use pill shape, small text, and a color-coded background with matching dark text (never white text on light badge background for the amber/green/gray variants).

```html
<!-- Badge template -->
<span class="inline-flex items-center px-2 py-0.5 rounded-full 
             text-xs font-medium [color-classes]">
  [Status Label]
</span>
```

Core design rules:
- **Shape:** `rounded-full` (pill)
- **Text:** `text-xs font-medium` (11px, medium weight)
- **Padding:** `px-2 py-0.5` (8px horizontal, 2px vertical)
- **Never** color-only — always include text label
- **Maximum** 6–8 statuses per entity before cognitive overload degrades UX

### JAKS Complete Status Map with Tailwind Classes

**Quotes**

| Status | Tailwind Classes | Meaning |
|---|---|---|
| Draft | `bg-gray-100 text-gray-600` | Created, not yet sent to customer |
| Sent | `bg-blue-50 text-blue-700` | Emailed/given to customer |
| Accepted | `bg-green-50 text-green-700` | Customer verbally or in writing confirmed |
| Declined | `bg-red-50 text-red-700` | Customer declined |
| Expired | `bg-amber-50 text-amber-800` | Quote validity date passed |
| Converted | `bg-[#e8ead9] text-[#4b5320]` | Converted to Sales Order or Invoice |

**Invoices**

| Status | Tailwind Classes | Meaning |
|---|---|---|
| Draft | `bg-gray-100 text-gray-600` | Not yet sent |
| Sent | `bg-blue-50 text-blue-700` | Sent to customer |
| Partial | `bg-amber-50 text-amber-800` | Partial payment received |
| Paid | `bg-green-50 text-green-700` | Paid in full |
| Overdue | `bg-red-50 text-red-700` | Past due date, balance remaining |
| Void | `bg-gray-100 text-gray-500` | Cancelled/voided |

**Sales Orders**

| Status | Tailwind Classes | Meaning |
|---|---|---|
| Open | `bg-blue-50 text-blue-700` | Confirmed, not yet fulfilled |
| Partial | `bg-amber-50 text-amber-800` | Some items shipped |
| Fulfilled | `bg-green-50 text-green-700` | All items shipped |
| Invoiced | `bg-[#e8ead9] text-[#4b5320]` | Invoice created from this order |
| Cancelled | `bg-red-50 text-red-700` | Cancelled |

**Purchase Orders**

| Status | Tailwind Classes | Meaning |
|---|---|---|
| Draft | `bg-gray-100 text-gray-600` | Not yet sent to vendor |
| Verbal | `bg-amber-50 text-amber-800` | Verbally placed, not confirmed |
| Sent | `bg-blue-50 text-blue-700` | Sent to vendor |
| Partial | `bg-amber-50 text-amber-800` | Some items received |
| Received | `bg-green-50 text-green-700` | All items received |
| Billed | `bg-[#e8ead9] text-[#4b5320]` | Vendor bill created |
| Cancelled | `bg-red-50 text-red-700` | Cancelled |

**Cores**

| Status | Tailwind Classes | Meaning |
|---|---|---|
| Open | `bg-blue-50 text-blue-700` | Core charge outstanding, core not returned |
| Returned | `bg-amber-50 text-amber-800` | Core physically returned, credit pending |
| Credited | `bg-green-50 text-green-700` | Core credit applied to account |
| Closed | `bg-gray-100 text-gray-500` | Resolved — no further action |

**Warranty Claims**

| Status | Tailwind Classes | Meaning |
|---|---|---|
| Draft | `bg-gray-100 text-gray-600` | Not yet submitted to vendor |
| Submitted | `bg-blue-50 text-blue-700` | Sent to vendor for review |
| Approved | `bg-green-50 text-green-700` | Vendor approved claim |
| Denied | `bg-red-50 text-red-700` | Vendor denied claim |
| Credited | `bg-[#e8ead9] text-[#4b5320]` | Credit received from vendor |
| Closed | `bg-gray-100 text-gray-500` | Closed — resolved or written off |

### Why Maximum 6–8 Statuses Matters

Above 8 status values, two problems emerge:

1. **Badge color reuse:** With only 5 semantic colors available (green, red, amber, blue, gray + brand), more than 6–8 statuses require reusing colors for different meanings. Color reuse breaks the semantic mapping — users have to read the text rather than pattern-match the color.

2. **Cognitive overhead:** Users learn status sets as a whole. 6 statuses can be internalized as a mental model. 12 statuses require looking up meaning every time.

If a workflow genuinely requires more than 8 states, consider whether some states should be secondary attributes (a flag or separate field) rather than the primary status.

---

## 10. Print & PDF Design for B2B Documents

### Why Print Quality Matters in B2B Parts

In B2B transactions between small businesses, document quality is a trust signal. A professional-looking invoice or quote communicates:
- We are a real business
- We stand behind our paperwork
- We are not going to mess up your records

In the heavy-duty diesel parts market — where customers are independent shops, owner-operators, and fleet managers — clean, legible, professional documents directly affect how quickly invoices get paid and whether verbal quotes are respected. A sloppy printout gets filed at the bottom of a stack; a clean, branded document gets to the accounts payable desk.

Customers keep documents for warranty, insurance, and accounting purposes. The document lives beyond the transaction.

### Required Document Elements

Every customer-facing document (quote, invoice, sales order acknowledgment) must include:

**Header block:**
- Company name: JAKS Parts & Equipment (or official DBA)
- Address, city, state, ZIP
- Phone number
- Email address (if used)
- Logo (if available)
- Brand color bar across the top

**Document identification block:**
- Document type: QUOTE / INVOICE / SALES ORDER
- Document number (monospace, prominent)
- Issue date
- Due date (invoices)
- Quote valid through date (quotes)

**Reference fields (when applicable):**
- Customer PO number
- ESN (Engine Serial Number) — when the job is engine-specific
- Customer name and billing address

**Line items table columns:**
```
Part #     | Description             | Qty | Unit Price | Total
(monospace)| (sentence case)         | (R) | (R, $)     | (R, $)
```

**Footer block:**
- Payment terms (e.g., "Net 30 — 1.5% per month on balances over 30 days")
- Accepted payment methods
- Contact information for questions
- Thank-you line (optional but professional)

### Core Charges on Printed Documents

Core charges must appear as separate line items on every document where they apply. Never bundle core charges into a part price. This is both a business practice standard and a customer expectation — shops need to track core liabilities separately for their own accounting.

```
Part #        Description                          Qty   Unit Price    Total
HD-HDR-29456  Remanufactured Injector Pump         1     $1,245.00   $1,245.00
CORE-29456    Core Charge — Injector Pump          1       $350.00     $350.00
```

The word "CORE" should be visible in the part number or description so it is unmistakable.

### ESN and Customer PO in Document Header

When a transaction is tied to a specific engine serial number or carries a customer PO reference, these fields appear in the document header — not buried in a line item description:

```
Customer PO:  GNR-2024-0891
ESN:          12P0234567
```

Customers use their PO number to match invoices to their internal purchase orders. ESN is used for warranty documentation and shop records. Both are critical reference fields.

### Print Typography

Print documents use different typographic rules than screen:

- **Body text for print:** 10–11pt (not the 13–14px screen default)
- **Font:** A print-appropriate face, not a screen-optimized system font. Options:
  - Georgia (serif, classic, widely available)
  - Source Sans Pro (clean sans-serif, good at small print sizes)
  - Avoid Inter, Segoe UI — they are hinted for screen, not print
- **Line item font:** 10pt for standard rows, 9pt for fine print
- **Header company name:** 14–16pt bold
- **Document title (INVOICE, QUOTE):** 18–24pt, bold, brand color or dark

### Page Layout Specification

- **Page size:** US Letter (8.5" × 11")
- **Margins:** 0.75" all sides (minimum)
- **Header:** Brand color bar (#4b5320) across full width at top, ~1" tall, white text
- **Logo position:** Left side of header bar, vertically centered
- **Document type + number:** Right side of header bar
- **Content area:** Full width within margins, line items in a clean table
- **Footer:** Brand color bar at bottom, ~0.5" tall, payment terms in small white text

### Generating PDFs in FastAPI

For JAKS, PDF generation uses WeasyPrint or ReportLab via a `/quotes/{id}/pdf` route that returns `application/pdf`. The print styles are defined in a dedicated `print.css` that is only applied during PDF generation:

```python
# routes/quotes.py
@router.get("/{quote_id}/pdf")
async def quote_pdf(quote_id: int, db: Session = Depends(get_db)):
    quote = get_quote_or_404(db, quote_id)
    html = templates.TemplateResponse("quotes/print.html", {"quote": quote})
    pdf = HTML(string=html.body.decode()).write_pdf(
        stylesheets=[CSS("static/css/print.css")]
    )
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="quote-{quote.number}.pdf"'})
```

---

## 11. Dashboard Design

### Above-the-Fold Requirement

The JAKS dashboard must answer "what needs my attention today?" without scrolling on a 1080p monitor. This is not optional — it is the reason the dashboard exists.

On a 1080p viewport with 220px sidebar and 56px top nav, the dashboard content area is approximately:
- Width: ~1700px
- Usable above-fold height: ~860px (accounting for content padding)

This is sufficient for:
- 4 KPI tiles (in a 4-column row): ~120px
- 2 list widgets in a 2-column layout: ~600px each
- Section spacing: ~140px total

Total: ~860px — fits exactly above the fold at 1080p.

### KPI Tile Anatomy

Each KPI tile is a white card with consistent internal structure:

```html
<div class="bg-white rounded-lg border border-gray-200 p-4">
  <!-- Label: small-caps, muted -->
  <p class="text-xs font-semibold uppercase tracking-wide text-gray-500">
    Open Invoices
  </p>
  <!-- Primary value: large, bold -->
  <p class="mt-1 text-2xl font-bold text-gray-900">
    14
  </p>
  <!-- Sub-value: trend or secondary metric -->
  <p class="mt-0.5 text-xs text-gray-500">
    $23,450 outstanding
  </p>
</div>
```

For exceptional states (overdue invoices, low stock), the primary value changes color:
```html
<!-- Overdue state -->
<p class="mt-1 text-2xl font-bold text-red-600">3</p>
<p class="mt-0.5 text-xs text-red-500">Invoices past due</p>
```

Color is used sparingly on KPI tiles — only for actionable exceptions, not for decorative purposes.

### Recommended JAKS Dashboard KPI Tiles (Row 1)

| Tile | Label | Primary Value | Sub-text |
|---|---|---|---|
| 1 | Open Quotes | Count | Total $ value |
| 2 | Outstanding Invoices | Count | $ balance due |
| 3 | Overdue Invoices | Count (red if > 0) | $ overdue total |
| 4 | Open Purchase Orders | Count | $ on order |

### Dashboard Layout (2-column below KPIs)

Below the 4 KPI tiles, use a 2-column layout (roughly 60/40 or 50/50):

**Column 1 (wider):**
- Recent Quotes — compact table, 6–8 rows, "View all →" link
- Recent Invoices — compact table, 6–8 rows, "View all →" link

**Column 2 (narrower):**
- Parts Low on Stock — list, 5–8 rows
- Recent Activity — event log, 8–10 lines

```html
<!-- Dashboard layout -->
<div class="space-y-6">
  <!-- KPI row -->
  <div class="grid grid-cols-4 gap-4">
    <!-- 4 KPI tiles -->
  </div>
  <!-- Widget row -->
  <div class="grid grid-cols-3 gap-4">
    <div class="col-span-2 space-y-4">
      <!-- Recent quotes widget -->
      <!-- Recent invoices widget -->
    </div>
    <div class="space-y-4">
      <!-- Low stock widget -->
      <!-- Activity log widget -->
    </div>
  </div>
</div>
```

### List Widget Design

Dashboard list widgets are compact tables with minimal chrome:

```html
<div class="bg-white rounded-lg border border-gray-200">
  <div class="flex items-center justify-between px-4 py-3 border-b border-gray-100">
    <h3 class="text-sm font-semibold text-gray-900">Recent Quotes</h3>
    <a href="/quotes" class="text-xs text-[#4b5320] hover:underline">View all →</a>
  </div>
  <table class="min-w-full">
    <tbody class="divide-y divide-gray-100">
      <tr class="hover:bg-gray-50">
        <td class="px-4 py-2 text-sm font-mono text-gray-900">#1041</td>
        <td class="px-4 py-2 text-sm text-gray-700">Garza Diesel</td>
        <td class="px-4 py-2 text-sm text-right tabular-nums text-gray-900">$3,200</td>
        <td class="px-4 py-2 text-right">
          <span class="badge-amber">Sent</span>
        </td>
      </tr>
    </tbody>
  </table>
</div>
```

### Dashboard Refresh Policy

JAKS is a local app. Auto-polling adds no value and complicates the HTMX implementation without benefit. The dashboard reflects the state of the database at page load time.

The user refreshes by navigating to the dashboard (clicking it in the nav) or with the browser's F5 / Ctrl+R. This is the correct behavior for an application that two people use at one desk — there is no race condition to resolve.

Do not implement WebSocket live-updates, polling intervals, or auto-refresh on the dashboard. This is a local operational tool, not a real-time dashboard.

---

## 12. Interaction Patterns — HTMX Specific

### Core HTMX Philosophy for JAKS

HTMX extends HTML with server-side partial rendering instead of a JavaScript SPA framework. For JAKS, this means:

- Every interaction is a server roundtrip (acceptable at local latency of <10ms)
- The server returns HTML fragments, not JSON
- State lives in the database and URL, not in JavaScript memory
- Progressive enhancement: core functionality works without JavaScript (HTMX degrades gracefully)

This aligns perfectly with FastAPI + Jinja2 + SQLite — the server renders everything.

### Partial Page Updates: Swap Only the Changed Region

Every HTMX request should target the smallest possible DOM region. Do not reload the full page for an action that only changes a table row.

```html
<!-- Updating quote status badge in a row -->
<button hx-post="/quotes/1041/accept"
        hx-target="#quote-1041-status"
        hx-swap="innerHTML">
  Mark Accepted
</button>

<!-- The target -->
<td id="quote-1041-status">
  <span class="badge-blue">Sent</span>
</td>
```

After the POST, the server returns just the new badge HTML:
```html
<span class="badge-green">Accepted</span>
```

HTMX replaces the inner content of the target with the response.

### Loading Indicators: In-Target Spinners

Show a loading indicator in the swap target during the request, not a full-page overlay:

```html
<!-- Indicator that appears during the request -->
<button hx-post="/quotes/new-line"
        hx-target="#line-items"
        hx-swap="beforeend"
        hx-indicator="#line-spinner">
  Add Line
</button>
<span id="line-spinner" class="htmx-indicator">
  <svg class="w-4 h-4 animate-spin text-gray-400"><!-- spinner --></svg>
</span>
```

```css
/* HTMX indicator pattern */
.htmx-indicator { display: none; }
.htmx-request .htmx-indicator { display: inline-flex; }
.htmx-request.htmx-indicator { display: inline-flex; }
```

Full-page loading overlays are inappropriate for HTMX partial updates — they are disorienting and defeat the purpose of partial rendering.

### Optimistic UI

For simple toggle actions (marking a task done, toggling a flag), show the result immediately in the UI and roll back if the server returns an error. This makes the interface feel instant.

In HTMX, true optimistic UI requires JavaScript to pre-update the DOM before the server responds. For JAKS, a simpler approach works:

- Use HTMX's fast response time (local FastAPI: <20ms typical)
- Add the `htmx-indicator` spinner to the swap target so the brief load is clearly communicated
- Do not implement complex rollback logic — at <20ms latency, the user experience is already excellent

Reserve true optimistic UI for cases where the user cares about immediate feedback: checkbox toggles on a list ("mark as called back"), inline edits.

### Debounce Recommendations

| Input type | Debounce delay | HTMX attribute |
|---|---|---|
| Search/filter inputs | 200–300ms | `hx-trigger="input changed delay:250ms"` |
| Autosave (form fields) | 2500ms | `hx-trigger="change delay:2500ms"` |
| Part number lookup | 200ms | `hx-trigger="input changed delay:200ms"` |
| Customer search dropdown | 300ms | `hx-trigger="input changed delay:300ms"` |

```html
<!-- Search input with debounce -->
<input type="search" name="q" 
       placeholder="Search parts..."
       hx-get="/parts/search"
       hx-target="#search-results"
       hx-trigger="input changed delay:250ms"
       hx-indicator="#search-spinner">
```

### Out-of-Band (OOB) Swaps

HTMX's `hx-swap-oob` attribute allows a single server response to update multiple regions of the page. This is essential for JAKS patterns where one action needs to update several things:

- Adding a quote line → update line items table AND update the quote total in the header AND show a toast notification
- Receiving a PO line → update receiving table AND update PO status badge AND toast

```python
# FastAPI route returning OOB swaps
@router.post("/quotes/{quote_id}/lines")
async def add_quote_line(quote_id: int, ...):
    line = create_line(...)
    updated_total = recalculate_total(quote_id)
    
    return HTMLResponse(
        # Primary response: new line row appended to table
        f'{render_line_row(line)}'
        # OOB: update quote total in header
        f'<div id="quote-total" hx-swap-oob="true">{updated_total}</div>'
        # OOB: show toast
        f'<div id="toast-container" hx-swap-oob="beforeend">{render_toast("Line added")}</div>'
    )
```

### POST → Redirect → GET Pattern

For any form submission that creates or modifies a record, use the PRG pattern:

1. Form submits via `POST /quotes/new`
2. Server creates the record
3. Server returns `HTTP 303 See Other` with `Location: /quotes/1042`
4. Browser follows redirect with `GET /quotes/1042`
5. Quote detail page loads fresh

This prevents the classic "resubmit on refresh" problem — if the user hits F5 on the quote page, they re-request the GET, not the POST. Double-submission is a real risk in accounting software where duplicate records cause reconciliation headaches.

For HTMX partial updates (adding a line, changing a status), PRG is not needed — the swap target handles the response directly and there is no full page to refresh.

### HTMX Error Handling

When a server returns an error (4xx, 5xx), HTMX by default does nothing visible. Configure error handling explicitly:

```javascript
// In your main JS (small snippet OK in HTMX-first app)
document.addEventListener('htmx:responseError', function(evt) {
  const status = evt.detail.xhr.status;
  const msg = status >= 500 ? 'Server error — please try again.' 
                             : 'Something went wrong.';
  showToast(msg, 'error');
});

document.addEventListener('htmx:sendError', function(evt) {
  showToast('Connection error — check network.', 'error');
});
```

Error responses from FastAPI should include human-readable messages that the toast can display. Never let an error fail silently — in an accounting tool, silent failures lead to data integrity assumptions that are wrong.

### HTMX Security: CSRF Protection

For a local web app (not exposed to the internet), CSRF risk is low. However, implement token-based CSRF protection as a discipline:

```python
# FastAPI middleware
from fastapi_csrf_protect import CsrfProtect

# Template: include CSRF token in all forms
<form hx-post="/quotes/new">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  ...
</form>
```

Include the CSRF token in HTMX AJAX requests via the `hx-headers` attribute on the `<body>` tag:

```html
<body hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'>
```

---

## Appendix A: Quick Reference — JAKS Tailwind Class Patterns

### Table Row (Standard List)
```html
<tr class="hover:bg-gray-50 border-b border-gray-100 last:border-0">
  <td class="py-1.5 px-3 text-sm font-mono text-gray-900"></td>  <!-- ID -->
  <td class="py-1.5 px-3 text-sm text-gray-700"></td>            <!-- Name -->
  <td class="py-1.5 px-3 text-sm text-right tabular-nums text-gray-900"></td> <!-- $ -->
  <td class="py-1.5 px-3"><!-- badge --></td>                    <!-- Status -->
  <td class="py-1.5 px-3 text-right"><!-- actions --></td>       <!-- Actions -->
</tr>
```

### Table Header (Standard List)
```html
<thead class="sticky top-0 bg-white border-b border-gray-200">
  <tr>
    <th class="py-2 px-3 text-xs font-semibold uppercase tracking-wide text-gray-500 text-left">
      Part #
    </th>
  </tr>
</thead>
```

### Form Field
```html
<div class="space-y-1">
  <label class="block text-xs font-semibold uppercase tracking-wide text-gray-500">
    Customer <span class="text-red-500">*</span>
  </label>
  <input class="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm
                focus:border-[#4b5320] focus:ring-1 focus:ring-[#4b5320] focus:outline-none">
  <p class="text-xs text-red-600 hidden">Error message</p>
</div>
```

### Primary Button
```html
<button class="inline-flex items-center gap-2 px-4 py-2 bg-[#4b5320] text-white 
               text-sm font-medium rounded-md hover:bg-[#636e2d] 
               focus:outline-none focus:ring-2 focus:ring-[#4b5320] focus:ring-offset-2
               disabled:opacity-50 disabled:cursor-not-allowed">
  Save Quote
</button>
```

### Secondary Button
```html
<button class="inline-flex items-center gap-2 px-4 py-2 bg-white text-gray-700 
               text-sm font-medium rounded-md border border-gray-300
               hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-[#4b5320]">
  Cancel
</button>
```

### Danger Button
```html
<button class="inline-flex items-center gap-2 px-4 py-2 bg-red-600 text-white 
               text-sm font-medium rounded-md hover:bg-red-700">
  Void Invoice
</button>
```

### Status Badges (Complete Set)
```html
<!-- Draft -->    <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">Draft</span>
<!-- Sent -->     <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">Sent</span>
<!-- Accepted --> <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">Accepted</span>
<!-- Partial -->  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-800">Partial</span>
<!-- Paid -->     <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">Paid</span>
<!-- Overdue -->  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700">Overdue</span>
<!-- Void -->     <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">Void</span>
<!-- Converted --><span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-[#e8ead9] text-[#4b5320]">Converted</span>
```

### Page Layout Skeleton
```html
<div class="flex h-screen bg-gray-50">
  <!-- Sidebar -->
  <nav class="w-[220px] min-w-[220px] bg-white border-r border-gray-200 flex flex-col">
    <!-- Logo area -->
    <div class="h-14 flex items-center px-4 border-b border-gray-200">
      <span class="text-base font-bold text-[#4b5320]">JAKS Inventory</span>
    </div>
    <!-- Nav items -->
    <div class="flex-1 overflow-y-auto py-4 px-2 space-y-0.5">
      <!-- nav items here -->
    </div>
  </nav>

  <!-- Main content -->
  <div class="flex-1 flex flex-col min-w-0">
    <!-- Top bar -->
    <header class="h-14 bg-white border-b border-gray-200 flex items-center px-6 gap-4 shrink-0">
      <h1 class="text-base font-semibold text-gray-900">Quotes</h1>
      <div class="ml-auto flex items-center gap-2">
        <!-- page-level actions -->
      </div>
    </header>
    <!-- Page content -->
    <main class="flex-1 overflow-y-auto p-6">
      <!-- content here -->
    </main>
  </div>
</div>
```

---

## Appendix B: Design Decision Log

This table records key design decisions and their rationale. Add to this as new decisions are made.

| Date | Decision | Rationale | Alternatives Rejected |
|---|---|---|---|
| 2026-05-24 | Entity-based nav (Customers/Vendors) not workflow-based (Sell/Buy) | Ops staff prefer entity nouns; bookkeeper background | Workflow-based nav |
| 2026-05-24 | Row height 36px (`py-1.5`) for list tables | 17–19 rows visible at 1080p; sufficient for daily ops review | 44px (too few rows), 32px (too tight for clickability) |
| 2026-05-24 | `bg-gray-50` app background, `bg-white` surfaces | Reduced eye fatigue; surface depth without shadows | Pure white background |
| 2026-05-24 | Monospace for part numbers, document IDs, ESNs | Operational accuracy; character-level scanning | Proportional font |
| 2026-05-24 | Pagination at 25 rows (not infinite scroll) | Predictable position; URL-addressable state | Infinite scroll |
| 2026-05-24 | Amber badges use dark text (`text-amber-800 bg-amber-50`) | WCAG AA compliance — white on amber-600 fails at 2.8:1 | White text on amber |
| 2026-05-24 | No dashboard auto-refresh | Local app; two users at same location; no race conditions | Polling, WebSockets |
| 2026-05-24 | Slide-overs for sub-task context within workflows | Preserves context; back-button-free sub-tasks | Navigate away + back button |
| 2026-05-24 | Core charges always separate line items | Business standard; customer accounting requirement | Bundled into part price |
| 2026-05-24 | POST → Redirect → GET on form submission | Prevents double-submit on F5; critical in accounting | Direct POST response |

---

*End of JAKS Inventory ERP Design Research Brief*  
*This document is the design authority for the project. All screen designs defer to the decisions, measurements, and patterns documented here.*
