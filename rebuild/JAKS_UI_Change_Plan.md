# JAKS UI Change Plan
*Living document — updated as screens are built.*

**Status:** Governance pass completed. Products List = official L2 reference. PO List = L2 complete. Invoice List submitted by UI Builder B — awaiting governance review. Customer List enriched to L1.5 — full L2 upgrade pending primitives decision.

**Scope:** All list and workspace screens in the JAKS Inventory ERP system.

---

This document defines the shared UI system for every list and workspace screen in JAKS Inventory. It exists because the app was being polished one screen at a time with no shared reference, producing inconsistent patterns that would require rework. All future screen builds and redesigns must follow this document. Update it when decisions change — do not diverge silently.

---

## UI Governance Model

### Roles

**UI Architect (pattern owner)**
- Owns this document. All pattern decisions go through here first.
- Reviews every L2/L3 screen before it is marked complete.
- Approves or rejects new UI primitives, patterns, color uses, and interaction systems.
- Intervenes when a builder diverges from the standard — punches the specific issue, does not redesign the screen.
- Decides when to extract shared primitives (Section 7) into macros.
- Does **not** build primary screens unless: (a) shared primitives need extraction, (b) a builder's output requires architectural correction, or (c) no builder is available.

**UI Builder**
- Owns screen implementation for their assigned screens.
- Reads this document before writing a line of template code.
- Uses Products List (`app/templates/products/list.html`) and PO List (`app/templates/purchase_orders/list.html`) as the direct reference — copy structure, adapt content.
- Does not invent new badge styles, modal patterns, table structures, interaction systems, or color uses.
- Submits each screen to the UI Architect for a governance pass before it is marked complete in the Rollout Order.
- Does not mark their own screen as "L2 complete" — that call belongs to the UI Architect.

### What Requires UI Architect Approval
These actions are **blocked** until the UI Architect approves:
- Any new CSS class that doesn't exist in the design system
- Any new modal or slide-over width/layout variant
- Any new badge or chip color not in Section 4
- Any new table structure (column pattern, row height, header style)
- Any new preview dock variant
- Any new interaction (keyboard shortcut, hover behavior, click behavior)
- Marking a screen as L2/L3 complete in the Rollout Order
- Extracting a shared primitive (Section 7)

### Governance Pass Checklist
When reviewing a submitted screen, the UI Architect checks all 11 elements from Section 2 plus:

**Structure**
- [ ] All 11 Operational List Screen Standard elements present
- [ ] No `tbl-td`, `tbl-th`, `tbl-row`, `tbl-head` classes used
- [ ] `divide-y divide-gray-100` on tbody
- [ ] `px-4 py-4 align-middle` on all td cells
- [ ] `overflow-x-auto` wrapper with `min-w-[...]` on table
- [ ] `pb-52` on outer x-data wrapper

**Tabs**
- [ ] `bg-gray-100 rounded-xl p-1 shrink-0` container
- [ ] Active tab: `bg-brand-700 text-white shadow-sm`
- [ ] Inactive tab: `text-gray-600 hover:text-gray-900 hover:bg-gray-200/70`
- [ ] Count badges: `bg-white/20 text-white` (active) / `bg-gray-200 text-gray-500` (inactive)
- [ ] Tab links preserve q param: `?tab={{ slug }}&q={{ q }}`
- [ ] Search form has `<input type="hidden" name="tab" value="{{ tab }}" />`
- [ ] Counts come from full unfiltered dataset (not the filtered result)

**Stripe**
- [ ] `border-l-4` on first `<td>`, not on `<tr>`
- [ ] Color matches plan semantics: red=critical, amber=warning, blue=informational, transparent=normal

**Chips**
- [ ] Status chips always visible — not hover-reveal
- [ ] Chip format: `inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold`
- [ ] Dot: `w-1.5 h-1.5 rounded-full shrink-0`

**Rows**
- [ ] `cursor-pointer hover:bg-gray-50/80 transition-colors group` on `<tr>`
- [ ] Row click = `togglePreview()` only — not navigate
- [ ] `@click.stop` on checkbox `<td>` and action `<td>`
- [ ] Selected/active ring: `ring-inset ring-1 ring-brand-300 bg-brand-50/40`

**Alpine x-data**
- [ ] `selected` plain object with spread-replace pattern
- [ ] `previewId` for dock state
- [ ] `allIds` array from Jinja2 loop
- [ ] `selectedCount`, `isSelected()`, `toggleSelect()`, `allSelected`, `toggleAll()`, `clearSelected()`, `togglePreview()` all present

**Bulk toolbar**
- [ ] `x-show="selectedCount > 0" x-cloak`
- [ ] `bg-brand-50 border border-brand-100 rounded-xl` container

**Preview dock**
- [ ] `fixed bottom-0 left-64 right-0 bg-white border-t-2 border-gray-200 shadow-2xl z-30`
- [ ] `max-height: 260px; overflow-y: auto`
- [ ] Sticky header: `px-6 py-3 border-b border-gray-100 bg-gray-50/80 sticky top-0`
- [ ] Content: `px-6 py-4` with 3–4 column grid
- [ ] Section labels: `text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-2`
- [ ] htmx.ajax() loading with spinner injected before call
- [ ] Preview route: `GET /{resource}/preview/{id}` (string prefix avoids int routing conflict)

**Identifiers**
- [ ] Invoice #, PO #, SKU: `font-mono text-sm font-bold text-brand-700 hover:text-brand-900 hover:underline`

**Colors**
- [ ] No color used outside Section 4 semantics
- [ ] No new badge color introduced

**Empty state**
- [ ] 3 cases: search miss / tab filter / no records
- [ ] Each case has distinct heading and body text
- [ ] No-records case has CTA

**Route**
- [ ] Tab counts computed from unfiltered dataset (group_by or separate counts)
- [ ] `now = datetime.now()` passed when overdue detection needed
- [ ] Preview route registered before `/{id}` route in router file

---

## Operational Workspace UI System

**Purpose:** We are done with one-off UI polishing. Going forward, all list/workspace screens must follow one shared design system so the app feels cohesive.

---

### 1. Screen Maturity Levels

Define four levels:

- **L1 — Basic CRUD/admin screen** — raw table or form, minimal polish, functional only
- **L2 — Polished operational list** — filter tabs with counts, search, status chips, hover state, always-visible actions, proper empty states
- **L3 — Workspace-grade workflow screen** — autosave, inline editing, live totals, slide-over integration, keyboard support, real-time state feedback
- **L4 — Power-user optimized screen** — all of L3 plus bulk operations, keyboard-driven navigation, Ctrl+K integration, side-by-side or docked panels

Current target mapping (as of this sprint):

| Screen | Current Level | Target | Notes |
|---|---|---|---|
| Products List | L2.5 | L3 | Reference pattern after final polish |
| Quotes List | L2 | L2 | Complete |
| Quote Workspace | L3 candidate | L3 | Autosave, inline editing done |
| Customers List | L1.5 | L2 | Quick-create slide-over (full fields, 4 footer buttons), detail card redesign, list enriched (phone/email/tier pills/last-sale). Full L2 upgrade (tabs, preview dock, stripe, bulk toolbar) blocked on Invoice List governance + primitives extraction decision. |
| PO List | L2 | L2 | ✅ Governance pass done |
| Invoice List | Submitted — pending governance | L2 | Full L2 rewrite done by UI Builder B; awaiting UI Architect governance pass |
| Product Detail | L1 | L2 | Needs upgrade |
| Warranty/Core/Returns queues | L1 | L2 | Scheduled for later |

---

### 2. Operational List Screen Standard

Every major list screen must include these elements:

1. **Page header** — title + count, with primary action button (Add / New) in the top-right via `header_actions` block
2. **Search field** — icon-prefixed input, left-aligned, preserves other query params on submit
3. **Filter tabs with counts** — pill-style nav (`bg-brand-700` active, `bg-gray-100` container), tab counts always reflect full unfiltered dataset
4. **Operational grid/table** — `divide-y divide-gray-100` rows, explicit Tailwind padding (not `tbl-td`), `overflow-x-auto` wrapper
5. **Left-edge status stripe** — `border-l-4` on first cell: red = critical/out-of-stock, amber = warning/low/overdue, blue = informational/on-order, transparent = normal
6. **Always-visible status chips** — never hover-to-reveal, inline dot + label format
7. **Clear hover state** — `hover:bg-gray-50/80 transition-colors` on every row
8. **Selected row state** — `ring-inset ring-1 ring-brand-300 bg-brand-50/40` when preview is open or row is active
9. **Bulk action toolbar** — appears above table when `selectedCount > 0`, `x-show` driven by Alpine, shows count + relevant actions
10. **Empty state** — centered card with icon, distinguishes: no records yet (CTA to create) vs no filter match (CTA to clear filter) vs no search match (CTA to clear search)
11. **Preview dock** — fixed bottom panel (`fixed bottom-0 left-64`) loaded via `htmx.ajax()` on row click, shows key fields in 3-4 columns, includes action buttons, dismisses on X or second row click

---

### 3. Shared Interaction Rules

These rules apply to every screen in the app. Do not deviate without updating this document.

**Row behavior:**
- `cursor-pointer` on clickable rows
- Entire row click = open preview dock (not navigate)
- Ctrl+click or action button = navigate to detail page
- Checkbox click must `@click.stop` to prevent row click

**Overlays (Esc to close):**
- Slide-over panel: backdrop `z-40`, panel `z-50`
- Modals / confirmation dialogs: backdrop `z-[60]`, panel `z-[60]` (centered)
- Ctrl+K overlay: backdrop `z-[60]`, panel `z-[61]`
- Preview dock: `z-30` (below all overlays)

**Keyboard support:**
- `Esc` closes the topmost open overlay (preview dock, modal, slide-over, Ctrl+K — in that priority order)
- `Ctrl+K` / `⌘K` opens global search (wired in base.html)
- `Enter` on search input submits the form
- Tab order must be logical inside modals and slide-overs

**Filter tabs:**
- Preserve all other query params when switching tabs (hidden `<input type="hidden" name="tab">` inside search form)
- Active tab: `bg-brand-700 text-white shadow-sm`
- Inactive tab: `text-gray-600 hover:text-gray-900 hover:bg-gray-200/70`
- Count badge inside tab: `bg-white/20 text-white` (active) or `bg-gray-200 text-gray-500` (inactive)

**Modal standard:**
- Max width: `max-w-lg` (standard), `max-w-2xl` (data-heavy)
- Rounded: `rounded-2xl`
- Shadow: `shadow-2xl`
- Header: `px-6 py-4 border-b border-gray-100` with title (`font-semibold text-sm text-gray-900`) + X close button
- Body: `px-6 py-6`
- Footer: `px-6 py-4 border-t border-gray-100 bg-gray-50/60 flex items-center justify-between`
- Primary action right-aligned, cancel left or second from right

**Slide-over standard:**
- Width: `max-w-lg` (standard), `max-w-2xl` (workspace-grade)
- Backdrop: `fixed inset-0 z-40 bg-black/40`
- Panel: `fixed inset-y-0 right-0 z-50 w-full max-w-lg bg-white shadow-2xl`
- Header: `px-6 py-4 border-b border-gray-100` with title + X close
- Footer: `px-6 py-4 border-t border-gray-100 bg-gray-50 flex gap-3 justify-end`

**Preview dock standard:**
- `fixed bottom-0 left-64 right-0 bg-white border-t-2 border-gray-200 shadow-2xl z-30`
- Max height: 260px with `overflow-y: auto`
- Sticky header bar inside dock: `px-6 py-3 border-b border-gray-100 bg-gray-50/80 sticky top-0`
- Content: 3 or 4 column grid, `px-6 py-4`
- Section labels: `text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-2`
- Action buttons: right column of the grid

**Toast notifications:**
- Position: top-right, stacked
- Language: past tense ("Quote created", "Product saved", not "Success!")
- Success: green dot/icon
- Error: red dot/icon
- Auto-dismiss at 4000ms

---

### 4. Shared Visual Rules

**Brand color semantics:**

| Color | Meaning | Tailwind family |
|---|---|---|
| Army olive/green (`brand-*`) | Primary actions, active states, brand identity | `bg-brand-700`, `text-brand-700` |
| Red | Overdue, problem, out-of-stock, error, discontinued | `red-*` |
| Amber | Warning, low stock, follow-up needed, superseded | `amber-*` |
| Green | Healthy, active, in-stock, success, paid | `green-*` |
| Blue | Informational, on-order, activity, links | `blue-*` / `sky-*` |
| Purple | Vendor, waiting, special workflow, serialized | `purple-*` |
| Orange | Core charge, special cost items | `orange-*` |
| Gray | Inactive, archived, neutral, metadata | `gray-*` / `slate-*` |

**Badge/chip sizes:**

| Use | Classes |
|---|---|
| Status badge (list rows) | `inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold` |
| Manufacturer badge | `px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border` |
| Micro flag (Core, S/N, SO) | `px-1 py-0 rounded text-[10px] font-semibold border` |
| Header status badge | `badge-green` / `badge-amber` / `badge-red` / `badge-gray` (design system classes) |
| Margin/score badge | `px-2 py-0.5 rounded-full text-xs font-semibold border` |

**Row padding:** `px-4 py-4` on all cells, `align-middle`

**Card:** `rounded-xl bg-white border border-gray-100 shadow-sm` (via `.card` utility class)

**Shadow levels:**
- Cards: `shadow-sm`
- Dropdowns/popovers: `shadow-lg`
- Modals: `shadow-2xl`
- Slide-overs: `shadow-2xl`
- Preview dock: `shadow-2xl`

**Section header style (inside cards):** `text-sm font-semibold text-gray-700 border-b border-gray-100 pb-3`

**Muted metadata style:** `text-xs text-gray-400`

**Monospace identifiers (SKU, Quote #, Invoice #, PO #):**
- Primary display: `font-mono text-sm font-bold text-brand-700`
- Sub/secondary display: `font-mono text-xs text-gray-500`
- Always link to detail page: `hover:text-brand-900 hover:underline`

---

### 5. Reference Implementation

**Status: ✅ OFFICIAL L2 REFERENCE — governance pass completed.**

**Products List** (`app/templates/products/list.html`) is the official reference implementation of the Operational List Screen Standard (Section 2). All 11 elements are present and correct. Audited against this plan on the governance pass.

It demonstrates:
- Filter tabs with backend-computed counts (defined inline in template via `{% set tabs %}`)
- Manufacturer color-coded badges (Brand-specific color map in template)
- Left-edge health stripe (`border-l-4` on first cell, not `<tr>`)
- Inventory health chips (dot + label: `inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold`)
- Margin % badge with green/amber/red thresholds
- Row selection with Alpine `selected` plain-object pattern (spread-replace for reactivity)
- Bulk action toolbar (`x-show="selectedCount > 0" x-cloak`, `bg-brand-50 border border-brand-100 rounded-xl`)
- HTMX preview dock (`htmx.ajax()` on row click, loading spinner injected first, `pb-52` on wrapper)
- Preview route at `GET /products/preview/{id}` (string prefix avoids int-param routing conflict)
- 4-column preview grid: Identity / Pricing / Inventory / Source & Actions
- Empty state: 3 cases distinguished (no records / tab filter / search miss)

**Cosmetic polish items (non-blocking — L2 is complete):**
- Hover state: `hover:bg-gray-50/80` is subtle; `hover:bg-gray-100/80` would be slightly crisper
- Preview dock top border: `border-t-2 border-gray-200`; `border-brand-200` would tie it to brand color
- Bulk action "Export CSV" is a stub; needs wiring when backend supports it
- Product Detail page upgrade scheduled as item #6 in Rollout Order

**Copy this screen when building Invoice List and Customer List. Adapt columns and tab data only. Keep everything else identical.**

---

### 6. Rollout Order

Apply the Operational Workspace UI System to screens in this order. Do not skip ahead — each screen informs the next.

| # | Screen | Status | Current → Target | Notes |
|---|---|---|---|---|
| 1 | Products List | ✅ L2 complete — official reference | L2.5 → L2 ref | Governance pass done |
| 2 | PO List | ✅ L2 complete | L1 → L2 | Governance pass done; overdue logic bug fixed |
| 3 | Invoice List | 🟡 Submitted — governance pending | L1 → L2 | Full L2 rewrite complete; UI Architect governance review next |
| 4 | Customer List | ⏳ L1.5 enriched — L2 pending | L1.5 → L2 | Quick-create/detail/list enriched this sprint. Full L2 upgrade (tabs, dock, stripe, bulk toolbar) blocked on Invoice List governance pass + primitives extraction decision |
| 5 | Quotes List | ⏳ Pending | L2 → L2 | Final alignment pass |
| 6 | Product Detail | ⏳ Pending | L1 → L2 | Section-based card layout |
| 7 | PO Workspace | ⏳ Pending | L1 → L3 | Autosave, line editor, receive flow |
| 8 | Invoice Workspace | ⏳ Pending | L1 → L3 | Autosave, payment flow, PDF |
| 9 | Warranty/Core/Returns queues | ⏳ Pending | L1 → L2 | Queue-style layout — UI Builder A owns |

**Constraint:** Do not redesign every screen differently. Do not create new modal, table, or badge patterns without updating this plan. Use shared UI primitives wherever possible:

- `.card` — operational list wrapper
- Filter tabs nav — pill-style with count badges
- `badge-*` classes — status badges
- Preview dock — `fixed bottom-0 left-64` + `htmx.ajax()` pattern
- Slide-over shell — `#create-slide` in base.html
- Modal shell — centered, `max-w-lg rounded-2xl shadow-2xl`
- Bulk action toolbar — `x-show="selectedCount > 0"` Alpine pattern
- Empty state block — icon + heading + body + optional CTA

**Goal:** The app should feel like one polished operational ERP system built by one team — not separate pages built by different coders in different sessions.

---

#### Invoice List — Builder Brief (UI Builder B)

**Owner:** UI Builder B
**Scope:** `app/templates/invoices/list.html` and `app/routers/invoices.py` list route only.
**Out of scope:** Invoice workspace, payment logic, QBO sync, PDF/print. Do not touch those.

**Read first:**
1. This document (all sections)
2. `app/templates/products/list.html` — copy this structure exactly
3. `app/templates/purchase_orders/list.html` — second reference

**What the current screen has (L1 baseline):**
- Old tab nav with `bg-white shadow text-brand-700` active state — wrong, must be `bg-brand-700 text-white shadow-sm`
- No tab counts
- Old `tbl` / `tbl-th` / `tbl-td` / `tbl-row` table classes — remove all
- No `divide-y divide-gray-100`
- No left-edge stripe
- No row selection or bulk toolbar
- No preview dock
- Bare empty state with no 3-case logic
- Search uses `?status=` not `?tab=` — align to tab param pattern

**What to build:**

*Router changes (`app/routers/invoices.py` list route only):*
- Change `status` param to `tab` param (keep `status` as backward-compat alias)
- Add tab groupings:
  - `all` → all non-void invoices
  - `open` → draft + open (awaiting payment)
  - `overdue` → open/partial where `due_date < now` (highest urgency)
  - `partial` → partial payment received
  - `paid` → paid
  - `void` → void (separate so it doesn't pollute "all")
- Compute counts from full unfiltered dataset using `group_by` or individual queries
- Pass `now = datetime.now()` for overdue detection
- Add `GET /invoices/preview/{invoice_id}` route before `GET /invoices/{id}` route
- Create `app/templates/invoices/_preview_panel.html` partial

*Template (`app/templates/invoices/list.html`):*
- Full rewrite following Products List structure exactly
- Tab slugs and labels: All / Open / Overdue / Partial / Paid / Void
- Left-edge stripe:
  - `border-l-red-400` → overdue (due_date < now, not paid)
  - `border-l-amber-400` → partial (some payment, not complete)
  - `border-l-blue-300` → open (finalized, awaiting payment)
  - `border-l-transparent` → draft, paid, void
- Status chip colors:
  - draft → `bg-gray-100 text-gray-600` / `bg-gray-400` dot
  - open → `bg-blue-50 text-blue-700` / `bg-blue-500` dot
  - partial → `bg-amber-100 text-amber-700` / `bg-amber-500` dot
  - paid → `bg-green-50 text-green-700` / `bg-green-500` dot
  - void → `bg-red-100 text-red-700` / `bg-red-400` dot
- Overdue indicator: amber date + "overdue" sub-label (same pattern as PO list)
- Row tinting: `bg-rose-50/30` for overdue rows, `bg-amber-50/20` for partial rows
- Columns: Invoice # / Customer / Ref (PO#/ESN) / Total / Balance Due / Status / Due Date / Actions
- Invoice # must use `font-mono text-sm font-bold text-brand-700 hover:text-brand-900 hover:underline`
- Customer name: prominent, `text-sm font-medium text-gray-900`
- Balance Due: `text-sm font-bold text-red-600` when > 0, `text-gray-400 text-xs` (—) when zero
- Preview dock body ID: `invoice-preview-body`
- Preview dock title: "Invoice Preview"

*Preview panel (`app/templates/invoices/_preview_panel.html`):*
4-column grid:
  1. **Identity** — invoice #, customer name, status badge, source (quote/SO if linked), created date
  2. **Financials** — subtotal, tax (if taxable), total, amount paid, balance due (bold red if > 0)
  3. **Dates & Refs** — due date (amber if overdue), customer PO #, ESN, job #
  4. **Actions** — "Open Invoice" (btn-primary), "Print" (btn-secondary)

**Locked behavior (is_locked field):** If `inv.is_locked` and status is not paid/void, show a lock micro-badge next to the invoice # in the row. Use the same micro-flag format: `px-1 py-0 rounded text-[10px] font-semibold bg-amber-50 text-amber-600 border border-amber-100`.

**Do not invent anything.** If a situation arises that isn't covered by this brief or the reference screens, ask before building.

**Submit for governance review** when complete — do not self-mark as L2.

---

### 7. Reusable Primitives Backlog

These patterns exist as near-identical copy-paste HTML across Products List and PO List. They should be extracted into Jinja2 macros when building the third list screen (Invoice List). Do not extract prematurely — the pattern needs two real implementations before the right abstraction is obvious.

**Do not build these yet. Define them here so the extraction decision is intentional, not reactive.**

#### Primitive 1 — `filter_tabs` macro
**File:** `app/templates/macros/filter_tabs.html`
**Signature:** `{% macro filter_tabs(tabs, active_tab, q, preserve_q=true) %}`
**What it renders:** The full `<nav class="flex items-center gap-0.5 bg-gray-100 rounded-xl p-1 shrink-0">` block.
**Current duplication:** Products List (inline `{% set tabs %}` + manual loop) vs PO List (route-provided `tabs` + loop). Tab data sourcing differs — macro accepts tabs as parameter, normalizes both.
**Tab data format:** `[(slug, label, count), ...]` — Products List already uses this; PO List uses `[(slug, label)]` + separate `counts` dict. Align to the triple-tuple format on extraction.

#### Primitive 2 — `operational_list_xdata` JS function
**File:** `app/static/js/list.js` or inline `<script>` in a macro
**Signature:** `operationalListData(previewBodyId, previewUrlPrefix)`
**What it replaces:** The 40-line Alpine `x-data` block in both list pages. Currently identical except for `'product-preview-body'` vs `'po-preview-body'` and `'/products/preview/'` vs `'/purchase-orders/preview/'`.
**Usage in template:** `x-data="operationalListData('po-preview-body', '/purchase-orders/preview/')"`

#### Primitive 3 — `status_chip` macro
**File:** `app/templates/macros/chips.html`
**Signature:** `{% macro status_chip(bg_cls, dot_cls, label) %}`
**What it renders:** `<span class="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold {{ bg_cls }}"><span class="w-1.5 h-1.5 rounded-full {{ dot_cls }} shrink-0"></span>{{ label }}</span>`
**Note:** Each screen defines its own color mapping in template-level `{% set %}` blocks. The macro just renders; callers compute the classes.

#### Primitive 4 — `bulk_toolbar` macro
**File:** `app/templates/macros/bulk_toolbar.html`
**Signature:** `{% macro bulk_toolbar() %}...{% endmacro %}` with `caller()` for action buttons
**What it renders:** The `x-show="selectedCount > 0" x-cloak` bar with count, separator, action slot, clear button.
**Currently:** Pixel-identical HTML in both Products and PO lists except for the "Export CSV" label.

#### Primitive 5 — `preview_dock_shell` macro
**File:** `app/templates/macros/preview_dock.html`
**Signature:** `{% macro preview_dock_shell(title, body_id) %}`
**What it renders:** The entire `fixed bottom-0 left-64...` container, sticky header with title + X close, and the empty body div.
**Currently:** 30 lines of near-identical HTML. Only `"Product Preview"` vs `"Purchase Order Preview"` and `product-preview-body` vs `po-preview-body` differ.

#### Primitive 6 — `empty_state` macro
**File:** `app/templates/macros/empty_state.html`
**Signature:** `{% macro empty_state(icon_path, q, tab, no_records_heading, no_records_body, cta_html='') %}`
**What it renders:** The `.card` empty state block with 3-case logic (search / tab filter / no records).
**Currently:** Each screen hardcodes its own copy. The structure is identical; only the icon SVG path, headings, body text, and CTA differ.

#### When to extract

**Gate:** UI Architect governs this decision. Do not extract without approval.

The extraction window opens **after Invoice List passes its governance review** and before Customer List begins. At that point there are 3 complete implementations — enough to validate the abstraction without guessing.

Decision criteria the UI Architect will apply:
1. Do all 3 screens use the primitive identically enough that one interface covers them? (If Invoice List required special-casing, that's a signal the abstraction is wrong.)
2. Is the fourth screen (Customer List) confirmed to need the same primitive? If not, wait.
3. Is the extraction cost (porting 2 existing screens + writing macro) worth the maintenance benefit? For 3–4 screens, yes. For 2, no.

If extraction is approved, sequence:
1. Write the macro (Primitive 5 — `preview_dock_shell` first, it's the highest-value / most identical)
2. Port Products List to use it — smoke test
3. Port PO List to use it — smoke test
4. Port Invoice List to use it — smoke test
5. Repeat for each additional primitive
6. Then begin Customer List using the macros from the start

**Do not extract early.** Two implementations of a pattern is not enough to know the right abstraction. Premature extraction creates wrong interfaces that then need rework. The cost of wrong abstraction is higher than the cost of one more copy-paste screen.
