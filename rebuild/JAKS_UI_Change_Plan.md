# JAKS UI Change Plan
*Living document — updated as screens are built.*

**Status:** Products List ✅ L2 ref · PO List ✅ L2 · Invoice List ✅ L2 · Customer List ✅ L2 (governance 2026-05-29) · Invoice Workspace ✅ L3 (governance PASS 2026-05-29) · Primitives extraction ✅ · **Build quality pass ✅ 2026-05-29** (Tailwind infra, class-token spec §17, motion macros) · **Phase 2 pending:** npm install → build:css → remove CDN block (high priority — gates QA visual snapshots) · Quotes List ⏳ awaiting Builder submission for governance pass · §8B workspace action-header standard ⏳ drafting next.

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
When reviewing a submitted screen, the UI Architect checks all 11 elements from Section 2, the
Operational Intelligence fields from Section 2B, plus:

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

Current target mapping (last audited 2026-05-29):

| Screen | Current | Target | Notes |
|---|---|---|---|
| Products List | ✅ L2 ref | L2 ref | Official reference. Governance pass done. |
| PO List | ✅ L2 | L2 | Governance pass done. Overdue bug fixed. |
| Invoice List | ✅ L2 | L2 | Governance pass done 2026-05-28. Red stripe for financial overdue (intentional domain distinction). |
| Quotes List | L2 | L2 | Has tabs+divide-y but no preview dock, no border-l-4 stripe. Pending final alignment pass. |
| Quote Workspace | L3 | L3 | Autosave, inline editing done. |
| Customers List | ✅ L2 | L2 | Governance pass done 2026-05-29. §2B operational intelligence complete (Balance Due, Open Invoices/Quotes/SOs, Cores, Last Sale, Terms). M1/M2 cosmetic deferred. |
| Product Detail | L1 | L2 | Raw form, no card sections. |
| Sales Orders List | L1 | L2 | Old tbl-* table, no L2 elements. |
| Vendors List | L1 | L2 | Old tbl-* table, no L2 elements. |
| Returns List | L1 | L2 | Old tbl-* table, no L2 elements. |
| Payments List | L1 | L2 | Old tbl-* table, no L2 elements. |
| Warranty List | L1 | L2 | Old tbl-* table, no L2 elements. |
| Cores List | L1 | L2 | Old tbl-* table, no L2 elements. |
| PO Receiving Queue | ✅ QB2 | QB2 | Queue Board archetype ratified 2026-05-28. Official QB2 reference implementation. |
| PO Match Queue | ✅ QB2 | QB2 | Queue Board archetype ratified 2026-05-28. No metrics strip — non-blocking, entry via Receiving Queue provides context. |

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

### 2A. Queue Board Standard

**Ratified 2026-05-28.** Queue Boards are a distinct UI archetype. They are **not** Operational Lists and must not be required to implement filter tabs, bulk toolbars, or preview docks. Those are list-specific patterns that do not map onto queue workflows.

**What a Queue Board is:** A surface that groups work items by operational context (vendor, PO, date, document) so a user can process them in sequence. Examples: PO Receiving Queue (items to receive, grouped by PO), 3-Way Match Queue (invoices to match, grouped by vendor or PO). The user acts on items one at a time or group by group — not across an arbitrary multi-select selection.

**What a Queue Board is not:** A filterable, sortable, bulk-actionable list of records. If a screen primarily browses and filters records, it is an Operational List (§2). If it presents work to be done in operational context groups, it is a Queue Board (§2A).

#### Queue Board — Required Elements

1. **Metrics strip** — area at the top showing aggregate counts and urgency numbers. Not tabs, not filters. At-a-glance snapshot only. Two acceptable formats:
   - **Horizontal strip (3 metrics or fewer):** `px-5 py-4 border-b border-gray-100 bg-white flex items-center gap-4 flex-wrap` with `h-4 w-px bg-gray-200` separators between items.
   - **Card grid (4+ metrics):** `grid grid-cols-2 md:grid-cols-4 gap-3` with each metric as a `.card` containing an icon + `text-2xl font-bold tabular-nums` count + `text-[11px] font-medium text-gray-500 uppercase tracking-wide` label. Metric cards that link somewhere should use `hover:ring-1 hover:ring-*-100 transition`. Cards with non-zero urgent counts should use `ring-1 ring-*-100` always-on.
   - Color: zero counts → `text-gray-400`; non-zero → appropriate semantic color (`text-red-600`, `text-amber-600`, etc.)

2. **Queue grouping headers** — each logical group has a header row that separates it from the next group. The header shows the group title (vendor name, PO number, date, etc.) and an item count.
   - Format: `px-4 py-2.5 bg-gray-50 border-y border-gray-100 flex items-center justify-between`
   - Title: `text-sm font-semibold text-gray-800`
   - Count badge: `text-xs text-gray-400 font-medium tabular-nums ml-2`
   - Optional: a group-level action button (e.g., "Receive All") right-aligned

3. **Grouped item rows** — within each group, items are listed. Tighter row padding than Operational List is permitted: `px-4 py-2.5 align-middle`. Must still use `divide-y divide-gray-100` within a group.

4. **Left-edge status stripe** — `border-l-4` on the first `<td>` of each item row. Same color semantics as §2:
   - `border-l-red-400` — blocked, error, critical mismatch
   - `border-l-amber-400` — needs attention, partial, overdue
   - `border-l-blue-300` — in-progress, pending review
   - `border-l-transparent` — ready, clean

5. **Status chips** — same dot + label format as §2. Always visible on each item row. Never hover-to-reveal.
   - `inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold`
   - Dot: `w-1.5 h-1.5 rounded-full shrink-0`

6. **Always-visible inline quick actions** — action buttons on each item row, **always visible** (not hover-only). This differs from the Operational List, where the action icon column uses `group-hover:opacity-100`. Queue actions are the primary workflow mechanism and must be immediately accessible.
   - Primary action: `px-2.5 py-1.5 text-xs font-semibold rounded-lg bg-brand-700 text-white hover:bg-brand-600`
   - Secondary action: `px-2.5 py-1.5 text-xs font-medium rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50`
   - Max 2–3 actions per item row. If more are needed, use a `···` overflow button.

7. **Empty states** — two levels:
   - **Group-level empty state:** when a group has no items to display (e.g., all items in a PO already received). Shown inline within the group using a compact message: `px-4 py-3 text-xs text-gray-400 italic`.
   - **Page-level empty state:** when the entire queue has no items. Use the standard centered `.card` empty state block from §2 with icon, heading, body. No CTA needed — an empty queue is a good state.

8. **Search** *(optional)* — if the queue has enough volume to need filtering, use the standard icon-prefixed search input. Not required. If omitted, do not add a placeholder or stub.

#### Queue Board — NOT Required

These §2 Operational List elements are **explicitly exempt** for Queue Boards:

| Element | Why exempt |
|---|---|
| Filter tabs with counts | Queue items are grouped by context, not filtered by status. Tab navigation is wrong for queues. |
| Bulk action toolbar | Queue processing is sequential per item or per group, not arbitrary multi-select. |
| Preview dock | Inline quick actions handle the workflow. A dock would duplicate the inline context. |
| Row selection checkboxes | No bulk actions means no selection. |
| `pb-52` on wrapper | No preview dock means no dock clearance needed. |
| `previewId` Alpine state | No dock means no dock state. |

#### Queue Board — Shared with Operational List

These elements apply to both archetypes:

- Color semantics (§4) — red/amber/green/blue meaning is identical
- Status chip format — identical dot + label classes
- Left-edge stripe (`border-l-4`) — identical color logic, on first `<td>`
- Hover state — `hover:bg-gray-50/80 transition-colors` on item rows
- `divide-y divide-gray-100` — within item rows inside a group
- Card container — `.card` or equivalent
- Monospace identifiers — PO #, invoice #, SKU use `font-mono text-sm font-bold text-brand-700`
- No `tbl-*` classes — explicit Tailwind padding only

#### Queue Board — Reference Implementations

**Status: ✅ BOTH QB2 COMPLETE — governance pass 2026-05-28**

- `app/templates/purchase_orders/receiving_queue.html` — **Official QB2 reference.** Demonstrates metrics card-grid, vendor group-divider rows, always-visible Receive/Match/Open/Print actions, fill progress bar, disabled placeholder for future route. UI Builder A owned.
- `app/templates/purchase_orders/match_queue.html` — QB2 complete. Variance count chips use `badge-*` (correct for count badges vs status chips). No metrics strip — entry via Receiving Queue "Flagged Bills" card provides equivalent context. Non-blocking.

**When building Warranty, Cores, or Returns queues:** copy `receiving_queue.html` structure. Adapt the state_meta mapping, group-by key, and metrics cards. Keep everything else identical.

#### Queue Board Maturity Levels

| Level | Name | What it means |
|---|---|---|
| **QB1** | Basic queue view | Items listed without grouping or metrics. Functional only. May use `tbl-*` classes. |
| **QB2** | Full Queue Board Standard | All 8 required elements present. No `tbl-*`. Follows §2A exactly. |

---

### 2B. Operational Intelligence Requirement

Every L2 Operational List must answer this question:

> **"Can the user make the next decision from this screen without opening the record?"**

If the answer is no, the screen is not L2 complete yet.

**Rule:** Do not add new schema just for this pass. Use existing relationships, cheap aggregate
queries, or deferred placeholders. If an expensive calculation is needed, add a TODO comment and
do not block the screen. No new schema may be introduced without UI Architect approval.

**Implementation priority:**
1. This addendum applies starting with Customer List L2 (current in-flight screen).
2. Continue existing rollout order — do not reorder or freeze.
3. Do not redesign the dashboard, create new modules, or add analytics.
4. Do not interrupt backend workflow work.

#### Required decision fields per screen

| Screen | Must expose |
|---|---|
| **Customers List** | Balance Due · Open Quotes count · Open Sales Orders count · Open Invoices count · Outstanding Cores count · Last Sale date · Terms / credit status · *(Optional: Lifetime Sales if already available cheaply)* |
| **Quotes List** | Quote total · Margin % · Follow-up due date/status · Customer terms / AR warning chip · Line count · Valid-until date · Conversion status |
| **Sales Orders List** | SO # · Customer · Fulfillment status · Payment/deposit status · PO/backorder status · Invoice status · Ship/tracking status · Total |
| **Invoices List** | Invoice # · Customer · Balance due · Due date · Days late · Payment status · Lock badge · Source quote/SO reference |
| **Payments List** | Payment # · Customer · Amount · Method · Applied / unapplied amount · Related invoices · Reversed / NSF status |
| **Vendors List** | Open POs · Open Bills · Credits pending · Last PO date · Lead time (if stored) · Primary contact |

#### Governance Checklist additions (append to §0 pass checklist)

- [ ] Screen exposes next-decision fields, not just record identity fields
- [ ] User can identify urgent/actionable rows without opening the detail page
- [ ] Financial risk is visible where relevant (balance due, overdue, AR exposure)
- [ ] Core/warranty/receiving obligations are visible where relevant
- [ ] No new schema introduced without UI Architect approval

---

### 3. Shared Interaction Rules

These rules apply to every screen in the app. Do not deviate without updating this document.

**Row behavior:**
- `cursor-pointer` on clickable rows
- Entire row click = open preview dock (not navigate — **Operational List only**; Queue Boards use inline actions instead)
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
| Red | Financial overdue (invoices), problem, out-of-stock, error, discontinued | `red-*` |
| Amber | Operational overdue (POs, follow-up), low stock, warning, superseded | `amber-*` |
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
| Header status badge | `badge-green` / `badge-amber` / `badge-red` / `badge-gray` / `badge-blue` (design system classes) |
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
| 2 | PO List | ✅ L2 complete | L1 → L2 | Governance pass done; overdue bug fixed |
| 3 | Invoice List | ✅ L2 complete | L1 → L2 | Governance pass 2026-05-28. Red stripe for financial overdue — accepted + codified in §4. |
| — | **Primitives extraction** | ✅ Complete (2026-05-29) | — | All 6 macros extracted + governance-approved. Products/PO/Invoice ported. See §7 as-built. |
| 4 | Customer List | ✅ L2 complete | L1.5 → L2 | Governance pass approved 2026-05-29. All §2 + §2B fields satisfied (Balance Due, Open Invoices/Quotes/SOs, Cores, Last Sale, Terms). M1/M2 cosmetic deferred. |
| 5 | Quotes List | 🟡 L2 conditional | L2 → L2 | **Governance pass 2026-05-29 — CONDITIONAL PASS.** All 11 §2 elements + §2B satisfied. One blocker held: AR warning chip (line 387, `# TODO` — needs open-balance from list route). Becomes ✅ L2 the moment the AR chip lands; no re-review needed. 5 non-blocking cosmetics in §8 backlog. |
| 6 | Sales Orders List | ⏳ Pending | L1 → L2 | Old tbl-* table. Full L2 upgrade needed. |
| 7 | Vendors List | ⏳ Pending | L1 → L2 | Old tbl-* table. Full L2 upgrade needed. |
| 8 | Returns List | ⏳ Pending | L1 → L2 | Old tbl-* table. Full L2 upgrade needed. |
| 9 | Payments List | ⏳ Pending | L1 → L2 | Old tbl-* table. Full L2 upgrade needed. |
| 10 | Product Detail | ⏳ Pending | L1 → L2 | Section-based card layout. |
| 11 | PO Workspace | ⏳ Pending | L1 → L3 | Autosave, line editor, receive flow. |
| 12 | Invoice Workspace | ✅ L3 complete | L3 candidate → L3 | Governance pass 2026-05-29. **PASS confirmed 2026-05-29** — window.confirm already cleared prior to pass. No blocking defects remain. A11y follow-up (role=dialog/aria-modal/focus-trap on payment/void/change-customer modals) tracked under §8 #4 a11y sweep — non-blocking for L3. |
| 13 | PO Receiving Queue | ✅ QB2 complete | QB1 → QB2 | Queue Board archetype — §2A. Governance pass 2026-05-28. Official QB2 reference. |
| 14 | PO Match Queue | ✅ QB2 complete | QB1 → QB2 | Queue Board archetype — §2A. Governance pass 2026-05-28. |
| 15 | Warranty Queue | ⏳ Pending | QB1 → QB2 | Queue Board archetype. Copy `receiving_queue.html`. UI Builder A owns. |
| 16 | Cores Queue | ⏳ Pending | QB1 → QB2 | Queue Board archetype. Copy `receiving_queue.html`. UI Builder A owns. |
| 17 | Returns Queue | ⏳ Pending | QB1 → QB2 | Queue Board archetype (or L2 list if returns are browsed not worked). Confirm with builder. |

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

#### Invoice List — Builder Brief (UI Builder B) — ✅ COMPLETE

**Status:** Governance pass approved 2026-05-28. L2 complete. See rollout order item #3.
**Governance note:** Builder used `border-l-red-400` for overdue invoices (per brief). Accepted — codified in §4 color semantics as "financial overdue = red". Empty state has 7 tab-specific messages, exceeding the 3-case minimum. No blocking defects found.

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

#### Quotes List — As-Built Record — 🟡 L2 conditional (governance 2026-05-29)

**Governance pass: CONDITIONAL PASS — L2 complete once AR chip lands. No re-review needed.**

All 11 §2 structural elements verified present:
- `pb-52` wrapper · `divide-y divide-gray-100` tbody · `overflow-x-auto` + `min-w-[1040px]` ·
  `border-l-4` stripe on first `<td>` (checkbox cell) · `filter_tabs` macro (7 tabs incl. Follow-up Due) ·
  `bulk_toolbar` macro · `status_chip` macro · `operationalListData` factory · `preview_dock_shell` macro ·
  Row click → `togglePreview()` · `@click.stop` on checkbox + action `<td>` ✅

§2B fields verified: Total · Margin % (computed inline from loaded lines, cost×qty vs subtotal) ·
Follow-up date / status with overdue flag · Customer terms chip (`terms_map`) · Line count ·
Valid-until date · Conversion status chip ✅

**One conditional item (self-documented by builder):**
- AR warning chip at `app/templates/quotes/list.html:387` — comment `# TODO: AR warning chip (needs
  open-balance data from route)`. The preview route already computes `ar_overdue_count`; the list route
  needs to pass customer balance data. **Flip to ✅ when the chip lands — no re-review.**

**Accepted decisions:**
- `emerald-*` color for Converted status chip — accepted (green family, semantically correct for
  completed conversion). Logged as cosmetic for future unification to `green-*`.
- Truck-down urgency dot (`animate-pulse`) — approved pattern, aligns with operational urgency intent.
- Legend row for urgency indicators — non-standard but genuinely useful; accepted.
- `trapFocus` inline `<script>` — builder added focus trap to New Quote modal (pre-dates §8E sweep);
  acceptable; can be moved to a shared primitive in the a11y sweep.

**Non-blocking cosmetics (see §8F):**
1. Search hidden input: `name="status"` → should be `name="tab"` for spec compliance
2. Row padding: `py-3.5` → spec is `py-4`
3. `emerald-*` chip → align to `green-*` family
4. Empty state: 2-case logic → spec prefers 3-case
5. New Quote modal inline `x-transition:*` → should use motion macros (rule introduced 2026-05-29)

---

#### Invoice List — As-Built Record (UI Builder B) — ✅ L2 (governance 2026-05-28)

*Templates owned by UI Builder B; list route + preview endpoint owned by the backend agent
(parallel). Governance pass **passed** 2026-05-28 — 7-tab set and red-overdue stripe both accepted
(see §1 maturity table + Rollout #3). Detail below is kept as the as-built record.*

**Backend — landed** (`app/routers/invoices.py` — backend agent's lane):
- `GET /invoices/` (`invoice_list`) rewritten to the L2 pattern (mirrors `purchase_orders.py`):
  - `tab` param with back-compat `?status=` alias via `_INV_STATUS_TO_TAB`.
  - Tab groupings in `INV_TAB_GROUPS` / labels in `INV_LIST_TABS`. **As-built tab set:**
    `All · Draft · Open · Partial · Overdue · Paid · Void` (7 tabs).
    Note: as-built uses 7 tabs vs brief's 6 (brief folded Draft into "open"). **Governance
    decision: 7 tabs accepted.** Draft (not yet sent to customer) is operationally distinct from
    Open (sent, awaiting payment). Collapsing them would mislead A/R staff. No change needed.
  - `open` = OPEN only; `partial` = PARTIAL only; `overdue` is **virtual** (OPEN/PARTIAL with
    `due_date < now`). `void` is its own tab; `all` excludes VOID.
  - `counts` computed from the **full unfiltered dataset** (`group_by` + dedicated overdue query).
  - Search spans invoice #, customer company, customer PO #, ESN.
- `GET /invoices/preview/{invoice_id}` (`invoice_preview_panel`) added, **registered before**
  `GET /invoices/{invoice_id}` (verified by route-order check) so "preview" isn't captured by the
  dynamic workspace route. AST/syntax verified.

**Templates — landed (L2-approved):**
- `app/templates/invoices/list.html` — full L2 rewrite (no `tbl-*` classes, `pb-52` wrapper,
  `divide-y divide-gray-100`, `border-l-4` stripe on first `<td>`, always-visible status chips,
  bulk toolbar, 3-case empty state, bottom preview dock → `#invoice-preview-body` via
  `htmx.ajax('/invoices/preview/'+id)`).
- `app/templates/invoices/_preview_panel.html` — 4-column dock body (Identity / Financials /
  Dates & Refs / Actions); core subtotal computed in-template by summing `CORE_CHARGE` lines.

**Stripe semantics (accepted + codified in §4):** `border-l-red-400` overdue ·
`border-l-amber-400` partial · `border-l-blue-300` open · transparent draft/paid/void. Red for
*financial* overdue is the intentional domain distinction the architect accepted. ✔

---

#### Invoice Workspace (#8) — Current State (verified 2026-05-28)

*Builder B's original note claimed "raw onclick+hidden modals, L1." Architect governance review found this to be **inaccurate**. Actual state documented below.*

- `app/templates/invoices/workspace.html` — **substantially L3** based on code audit:
  - ✅ Autosave: `hx-trigger="change from:input,select,textarea,checkbox delay:300ms"` on header form
  - ✅ Inline line editing, live totals panel (HTMX)
  - ✅ **Payment modal** — Alpine `x-show`, `z-[60]`, `max-w-2xl rounded-2xl bg-white shadow-2xl`, Esc closes, `@click.stop`, `x-transition` — **fully §3 compliant**
  - ✅ **Void modal** — Alpine `x-show`, `z-[60]`, `max-w-lg rounded-2xl bg-white shadow-2xl`, Esc closes — **fully §3 compliant**
  - ✅ **Change-Customer overlay** — Alpine-driven (`changeCustomerOpen`), Esc closes
  - ✅ All three closed by `@keydown.escape.window` on root wrapper
- **Status:** Needs formal workspace governance pass (L3 review) to confirm complete. The workspace *looks* L3 but has not been formally reviewed against the §3 Workspace-grade checklist.

---

#### Receiving Queue & 3-Way Match — As-Built Record (UI Builder A)

*Submitted for governance — not self-marked complete. These two screens did not exist when the
plan was first written; they were built to a product brief from the owner this sprint.*

**Scope (UI Builder A):** `app/templates/purchase_orders/receiving_queue.html`,
`match_queue.html`, `_match_panel.html`, and the `/receiving` + `/match` routes in
`app/routers/purchase_orders.py` (both registered **before** `/{po_id}`). The per-PO match panel
is included in `workspace.html` behind `match.has_activity`. **No** service-layer, QBO, or PDF
changes — the existing `POService` / `_match_summary` helper is the source of truth.

**Receiving Queue** — standalone operational board (not a tab-filtered list):
- Metrics strip: **Open POs · Due/Overdue · Partially Received · Flagged Bills** (last two cards
  link to the relevant tab / the match queue).
- Covers the full receiving lifecycle (sent, verbal, partial, received, billed).
- **Grouped by vendor**, most-urgent vendor first; row order by urgency then ETA.
- Status `border-l-4` stripe **on the first `<td>`**: blue=open, amber=partial,
  red=overdue/discrepancy, green=received, gray=billed.
- Row actions link **into** the PO workspace (`Receive` → `#receive`, `Match` → `#match`, Open,
  Print). Receiving-slip print is a **disabled placeholder + TODO** — no `/receiving-slip` route
  exists yet, so it never 404s.

**3-Way Match** — per-PO panel (`_match_panel.html`) showing Ordered/Received/Billed + Qty Δ /
Cost Δ + match-state chip (matched / awaiting receipt / awaiting bill / over-billed / cost
variance), plus the cross-PO flagged-bill queue (`match_queue.html`). Each flagged row references
the exact vendor bill (number + ⚠) and links back to that PO's `#match` panel.

**Builder self-check done (before submission):**
- ✅ Fixed `border-l-4` that was on `<tr>` → moved to first `<td>` in `match_queue.html` and
  `_match_panel.html` (was a §Stripe red-flag).
- ✅ Converted status chips from `badge-*` to the §Chips dot+label format
  (`inline-flex … rounded-lg … ` + `w-1.5 h-1.5 rounded-full` dot) in both queues.
- ✅ `divide-y divide-gray-100`, explicit `px-4 py-4` padding (no `tbl-*`), `overflow-x-auto` +
  `min-w-[…]`, 3-case empty states, color use within §4 semantics.

**Architect rulings (2026-05-28):**
1. ✅ **Queue Board archetype ratified.** §2A added to the plan. Filter tabs, bulk toolbar, and preview dock are explicitly exempt for queue boards. Both screens are QB2 complete.
2. ✅ **Metrics card-grid and vendor group-divider rows approved** as QB2 primitives. `receiving_queue.html` is the official QB2 reference. Warranty, Cores, and Returns queues must copy this pattern.
3. ✅ **`badge-blue` added to §4** header status badge list. Approved.

---

#### PO Workspace (#7) — As-Built Progress (UI Builder A)

*Partial L3 pass — submitted for governance, not self-marked L3 complete.*

**Scope:** `app/templates/purchase_orders/workspace.html`, `_lines_section.html`. No service-layer,
QBO, or PDF changes — all POST actions and field names preserved; the Receive and Create-Bill
**primary submits were not touched**.

**Landed this pass:**
- **De-`tbl-*`:** the line-items table (`_lines_section.html`), the Create-Vendor-Bill table, and
  the Vendor-Bills table (`workspace.html`) now use `divide-y divide-gray-100` + explicit
  `px-4 py-4 align-middle` (0 `tbl-*` remain in either file; verified in rendered markup). All
  inline-edit `hx-*`/`name`/Alpine bindings preserved.
- **Modal standard:** all 5 confirmations (Cancel PO ×2 in header, cancel-line, approve-bill, and
  the line-items **delete**) replaced with one Alpine-driven confirm modal per §3 (centered,
  `z-[60]`, Esc-close, `rounded-2xl shadow-2xl`). Header triggers (outside the content x-data scope)
  `$dispatch('po-confirm', …)`; the content-scope modal listens via `@po-confirm.window`. Empty
  `<form>`s hold POST actions submitted on confirm; the delete (an HTMX action, not a form) uses
  `askConfirmHx()` which the modal runs via `htmx.ajax('POST', …)`.
  - *Governance 2026-05-28:* the line-delete originally kept native `hx-confirm` — caught as the
    sole confirmed blocker in the architect pass and now converted. **0 native confirm dialogs
    remain** in the workspace (verified in rendered markup).

**Still open for full L3 (flagging, not done):**
- Whether the inline Receive / Create-Bill **cards** should become slide-overs (§3) — held for
  Architect direction; same open question as the Invoice Workspace drawers (Builder B).
- `app/templates/purchase_orders/detail.html` still uses `tbl-*` but the `/{po_id}` route renders
  `workspace.html` — `detail.html` appears to be a **dead template**; recommend confirming + deleting.

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

**✅ EXTRACTION APPROVED — 2026-05-28**

All three gate criteria passed:
1. Products List, PO List, and Invoice List all use every primitive identically — no special-casing required. Invoice List governance confirmed full pattern match.
2. Customer List (next in rollout) will need all 6 primitives. Building it from macros is cleaner than copy-paste + later extraction.
3. 3–4+ screens: extraction cost is justified. 16 screens total in rollout order — macros will pay off across every future screen.

**Extraction sequence (do in this order):**
1. **Primitive 5 — `preview_dock_shell`** first. Highest-value. 30 lines of identical HTML across 3 screens. Easiest to validate.
2. **Primitive 4 — `bulk_toolbar`** second. Pixel-identical across all 3 screens.
3. **Primitive 1 — `filter_tabs`** third. Normalise the triple-tuple format while porting.
4. **Primitive 6 — `empty_state`** fourth. 3-case structure is identical; only text/icon differs.
5. **Primitive 3 — `status_chip`** fifth. Small but used many times per screen.
6. **Primitive 2 — `operational_list_xdata`** last. Most complex — affects Alpine wiring. Validate thoroughly.

For each primitive:
- Write macro
- Port Products List → smoke test (`GET /products/`)
- Port PO List → smoke test (`GET /purchase-orders/`)
- Port Invoice List → smoke test (`GET /invoices/`)
- Then proceed to next primitive

After all 6 extracted: begin Customer List using macros from the start.

---

#### Primitives Extraction — As-Built Record (UI Builder B) — 🟡 submitted, pending governance

*Extraction executed per the approved §7 sequence. **Not self-marked complete** — awaiting an
architect pass on the macro library + the 3 ported screens.*

**Macro library created** (`app/templates/macros/`):
| File | Macro | Primitive |
|---|---|---|
| `preview_dock.html` | `preview_dock_shell(title, body_id)` | P5 |
| `bulk_toolbar.html` | `bulk_toolbar()` (call-block for action buttons) | P4 |
| `filter_tabs.html` | `filter_tabs(tabs, active_tab, q)` — triple-tuple `(slug,label,count)` | P1 |
| `empty_state.html` | `empty_state(icon_path, q, active_tab, …, cta_html='')` | P6 |
| `chips.html` | `status_chip(bg_cls, dot_cls, label)` | P3 |
| `list_behavior.html` | `operational_list_script()` → emits `operationalListData(bodyId, urlPrefix, allIds)` | P2 |

**Ports landed** (each imports macros + uses the factory `x-data`):
- `products/list.html` — 449→368 lines. dock/bulk/tabs/xdata. Empty state kept **inline** (tab-specific copy).
- `purchase_orders/list.html` — 413→335 lines. Same set; builds triples from route pairs+counts.
- `invoices/list.html` — 408→327 lines. Same set **+ `status_chip`** in the row; 7-tab empty state kept inline.

**Decisions / deviations (flag for governance):**
1. **P2 delivered as an inline-`<script>` macro**, not a `static/js/list.js` file — keeps the
   primitive in the template layer (owner-approved). `{{ operational_list_script() }}` emits the
   factory once per page.
2. **`empty_state` macro built but not adopted** by the 3 ported screens — each has richer
   tab-specific copy than the generic 3-case macro. Macro is ready for Customer List. Confirm whether
   to force the 3 onto it or keep their richer copy.
3. **`filter_tabs` signature** is `(tabs, active_tab, q)` — dropped the backlog's `preserve_q=true`
   param (q always preserved when truthy). Confirm.

**Verification:** all 6 macros + 3 ported lists **compile** under Jinja 3.1 (`.venv`); all 3 lists
**render** with mock context — Products (4 tabs), PO (6 tabs), Invoice (7 tabs), each with dock
body-id + factory call present, Invoice status-chip present. No `tbl-*`, no leftover inline x-data,
no orphaned dock markup. Browser smoke test (`GET /products/`, `/purchase-orders/`, `/invoices/`)
pending a running server.

---

### Build — Tailwind Compiled CSS

**Status: ✅ Infra complete (2026-05-29) — Phase 2 (CDN removal) pending one `npm install` + build run.**

The Tailwind play-CDN has been replaced by a proper compiled build pipeline. Source files are
in place; the CDN remains active as a dev fallback until the first build runs.

#### Files created

| File | Purpose |
|---|---|
| `tailwind.config.js` | Single source of design tokens: brand color palette, font family, content scan paths, §4 color semantics documented |
| `app/static/css/input.css` | CSS entry point: `@tailwind` directives + `@layer components` block defining every design-system class (`.card`, `.btn-*`, `.form-*`, `.badge-*`, `.toast-*`, etc.) |
| `app/static/css/app.css` | Compiled output (placeholder — overwritten by build). **Gitignore this file in production.** |
| `package.json` | `build:css` and `watch:css` scripts |

#### Build commands

```bash
# One-time install (first time only)
npm install

# Production build (minified output → app/static/css/app.css)
npm run build:css

# Development watch mode (rebuilds on every template/JS change)
npm run watch:css
```

**Alternative — Tailwind standalone CLI (no Node.js needed):**
Download the self-contained binary from https://github.com/tailwindlabs/tailwindcss/releases
and run:
```
tailwindcss.exe -i app/static/css/input.css -o app/static/css/app.css --minify
```

#### Phase 2 — CDN removal (do after first build)

Once `app/static/css/app.css` is compiled (not a placeholder):
1. Remove the `window.tailwind = {...}` config block from `base.html` (marked with `{# DEV FALLBACK #}` comment)
2. Remove the `<script src="https://cdn.tailwindcss.com">` line immediately below it
3. Verify every L2/QB2 screen renders identically — the compiled CSS covers all component classes

**What the compiled build fixes:**
- No more FOUC (CDN processes @apply at runtime, causing a flash on slow connections)
- Tree-shaking: only used classes are included (CDN bundles ~3MB; compiled output is ~10–30KB)
- Lint-able: class-tokens.md §§1–17 can now be enforced in CI against the source CSS
- Stable artifact: deployed app.css doesn't depend on CDN availability

#### Motion primitives — `macros/motion.html`

All Alpine `x-transition:*` attributes in `base.html` and `macros/preview_dock.html` have been
extracted into five named macros (2026-05-29):

| Macro | Primitive | Used by |
|---|---|---|
| `slide_right()` | Slide-over panels | Log Call, Create Customer |
| `backdrop_fade()` | Backdrop overlays | All slide-overs, Ctrl+K, future modals |
| `slide_up()` | Preview dock | `preview_dock_shell` macro (all L2 lists) |
| `dropdown_scale()` | Notification dropdown | Header notification bell |
| `modal_scale()` | Ctrl+K / centered modals | Ctrl+K overlay, future confirm dialogs |

**Rule:** All new x-transition usage must use one of these macros. Hard-coding `x-transition:*`
inline is blocked. If a new motion pattern is needed, add it to `motion.html` + `class-tokens.md §10`.

#### Class-token allowlist — `class-tokens.md`

`.claude/skills/jaks-ui-governance/references/class-tokens.md` is now the authoritative lint spec.
QA lane: enforce §§1–17 in CI as the token allowlist gate. Key sections for the lint gate:

- **§1** — Forbidden classes (`tbl-td`, `tbl-th`, `tbl-row`, `tbl-head`)
- **§2** — Required markers (`pb-52`, `divide-y divide-gray-100`, `overflow-x-auto`, dock markers)
- **§3** — Stripe placement (`border-l-4` on first `<td>` only, never `<tr>`)
- **§4** — Permitted color families (only red/amber/green/blue/purple/orange/gray/brand)
- **§10** — Motion macro requirement (no inline `x-transition:*`)

---

### 8. Deferred Backlog

Items confirmed as real, scoped, and worth doing — but explicitly deferred. Do not schedule without
an Architect instruction. Each item includes the file targets and enough context to act without
this conversation.

#### 8A. Post-Extraction Cosmetic Cleanup (low priority)

1. **Macro adoption — fold Products/PO inline chips onto `status_chip`.**
   Products and PO lists still inline their health/status chips; Invoice uses `status_chip`.
   A single PR should replace the inline `<span>` blocks with `{{ status_chip(...) }}` calls,
   retroactively satisfying the §7 "3 screens" gate.
   Targets: `app/templates/products/list.html`, `app/templates/purchase_orders/list.html`.

2. **`Invoice.is_overdue` scope mismatch for DRAFT invoices.**
   `Invoice.is_overdue` (`app/models/invoice.py:144`) returns `True` for any non-PAID status,
   so a DRAFT with a past `due_date` shows the overdue stripe in the **all** tab but is excluded
   from the Overdue count (count = OPEN/PARTIAL only). Fix: gate template overdue styling to
   `status in (OPEN, PARTIAL)`, or narrow the model property. Coordinate with Backend lane.

3. **Customer List M1 — blue-stripe row tint missing.**
   Amber-stripe rows get `bg-amber-50/20`; blue-stripe (open-invoice) rows have no tint. Add
   `'bg-blue-50/10': '{{ stripe }}' === 'blue' && previewId !== {{ c.id }}` to the row `:class`
   binding. `app/templates/customers/list.html`.

4. **Customer List M2 — activity badge rounding.**
   Activity pills use `rounded-full`; system standard is `rounded-lg`. Cosmetic only.
   `app/templates/customers/list.html:286,294,303,312`.

#### 8D. Compiled CSS Cut-over — HIGH PRIORITY (gates QA visual snapshots)

**Owner:** UI Architect lane. **Blocked on:** Node.js installation.

Once Node.js is available on this machine:
1. `npm install` (one-time, ~30s)
2. `npm run build:css` (produces `app/static/css/app.css`, ~5s)
3. Delete the `{# DEV FALLBACK #}` block in `app/templates/base.html` — the `window.tailwind`
   config script and the `<script src="https://cdn.tailwindcss.com">` line immediately below it.
4. Smoke-test: `GET /products/`, `/purchase-orders/`, `/invoices/`, `/customers/` — confirm
   styling identical, no FOUC, screenshot tool no longer times out.
5. Commit as "Phase 2: activate compiled Tailwind CSS, remove CDN dev fallback".

**Why high priority:** The CDN adds ~8s page-weight before Alpine can initialise and before
screenshot tools can capture a stable frame. QA's visual regression snapshots block on this.
The compiled file also enables the §1–17 class-token lint gate in CI.

**Standalone CLI alternative (no Node.js):** Download `tailwindcss-windows-x64.exe` from
https://github.com/tailwindlabs/tailwindcss/releases and run:
```
tailwindcss-windows-x64.exe -i app/static/css/input.css -o app/static/css/app.css --minify
```

---

#### 8E. A11y Sweep — Invoice Workspace Modals (low priority, track separately)

**Not blocking L3.** The three Alpine modals in `app/templates/invoices/workspace.html`
(payment, void, change-customer) are §3-compliant for visual behavior but are missing
ARIA accessibility attributes. Track as part of a future cross-workspace a11y pass (#4).

**Files:** `app/templates/invoices/workspace.html`

**Required on each modal panel element:**
- `role="dialog"`
- `aria-modal="true"`
- `aria-labelledby="<heading-id>"` pointing to the modal's `<h2>` or `<h3>`

**Focus trap:** Alpine does not provide a built-in focus trap. Options when this sweep
is scheduled: (a) add a small focus-trap directive (30 lines of JS), or (b) adopt the
`@alpinejs/focus` plugin (`$focus.trap()` magic). Confirm approach with Architect before
implementing — it affects all modals app-wide.

**Do not start this sweep without an explicit Architect instruction.** It is non-blocking
and should not interrupt the list-port rollout.

---

#### 8B. Global Workspace Action Header Review — NEXT ARCHITECT DELIVERABLE

**Priority elevated 2026-05-29.** This standard must be drafted before Sales Orders,
Warranty, and Returns workspaces are built — those screens will copy whatever pattern is
established here. Getting it wrong now means rework across 3+ screens.

**Architect action:** Draft the standard (button order, labels, back-link behavior, New X
visibility rules) as a new subsection of §3. Submit for owner review before any builder
implements it. The draft does not require running the app — it's a written spec based on
auditing the existing workspace templates.

**Scope:** The action button strip and back-link area that appears in the `header_actions` block
across Customer, Quote, Invoice, Sales Order, and PO workspaces.

**Problem observed:** Owner testing surfaced inconsistency in visibility, ordering, and behavior
of New Quote / New Invoice / Statement / Back link across workspace screens. The area needs a
cross-workspace audit and a consistent standard before further workspace screens are built.

**Do not redesign the dashboard or any list screen as part of this work.**
**Draft spec first — no template changes until the standard is approved by the owner.**

Screens in scope:
- `app/templates/customers/*.html` (customer workspace header)
- `app/templates/quotes/workspace.html`
- `app/templates/invoices/workspace.html`
- `app/templates/sales_orders/workspace.html`
- `app/templates/purchase_orders/workspace.html`

Deliverable: a punched list of specific inconsistencies + a proposed standard for the header strip,
submitted to the Architect for approval before any template changes.

#### 8F. Quotes List — Post-Governance Cosmetic Cleanup (low priority)

Non-blocking items from the 2026-05-29 governance pass. Do not start without an Architect instruction.
All five can be done in one small PR after the AR chip lands.

1. **Search hidden input** — `app/templates/quotes/list.html:245`. Change `name="status"` →
   `name="tab"`, `value="{{ status_filter }}"` → `value="{{ active_tab }}"`. The router back-compat
   still works during the transition; this just makes the form consistent with the L2 standard.

2. **Row padding** — `app/templates/quotes/list.html` — all `<td>` use `py-3.5`; spec is `py-4`.
   One-pass find-replace.

3. **`emerald-*` chip color** — `app/templates/quotes/list.html:272`. `bg-emerald-100 text-emerald-800`
   / `bg-emerald-500` for Converted. Replace with `bg-green-50 text-green-700` / `bg-green-500`.
   Aligns to §4 `green-*` = complete/success.

4. **Empty state** — `app/templates/quotes/list.html:525-541`. Add third case: when both `q` and
   `status_filter` are set, emit "No results for '{q}' in this filter" with separate clear-search
   and clear-filter CTAs.

5. **New Quote modal inline `x-transition`** — `app/templates/quotes/list.html:86-89, 98-101`.
   Replace the six `x-transition:*` attributes on backdrop and panel with `{{ backdrop_fade() }}`
   and `{{ modal_scale() }}` from `macros/motion.html`.

---

**Quote Workspace partial pass — Architect-approved & implemented 2026-05-29.** Owner testing of
`quotes/workspace.html` surfaced three issues; fixes 2 & 3 approved and applied (verified live):
1. *"Can't add a part"* — **NOT a bug** (add-line works end-to-end; verified by running the app).
   Root cause is a hidden two-step "staging" flow: clicking a search result only stages the part
   (`selectProduct()`), and **+ Add Line** sits greyed-out/`disabled` until then, so it reads as
   broken. **Left as-is pending Architect decision** between one-click-add vs. a prominent-staged-
   button + "↵ Enter to add" hint. Still an open discoverability gap.
2. *Account link lost work* — the Account / customer-name / "← All quotes" links navigated away
   in-tab, discarding staged (uncommitted) search input. Now `target="_blank" rel="noopener"`.
3. *No visible Save* — the grey **Save** button was **redundant** (its `POST /quotes/{id}` updates
   the same 4 fields as the 2.5s autosave). Removed it, added `onsubmit="return false"` to block
   accidental Enter-submit, and made the autosave indicator always-visible ("✓ Saved automatically").
This is a targeted fix, **not** the full cross-workspace header standard — §8B proper is still open.

#### 8C. Customer Profile UX Fixes — IMPLEMENTED (UI Builder, 2026-05-29)

Owner testing identified the following UX issues. Status below reflects the 2026-05-29 UI Builder
pass — **pending Architect governance sign-off** (Builder does not self-mark complete).

1. **Phone/email search behavior** — ✅ Done. `customer_list` search now normalizes phone to
   digits-only on both sides and matches company / contact / email / phone partials.
   `app/routers/customers.py` (`_digits`, search predicate). *Note:* the global typeahead
   `/customers/search-json` was intentionally left as-is (flagged as a separate follow-up).
2. **Duplicate customer warning** — ✅ Done. `_find_duplicate_customers` (normalized name match)
   gates both `POST /customers/quick-create` and `POST /customers/new`; on a hit the form
   re-renders with a "Possible duplicate customer found." banner offering **View Existing** and
   **Create Anyway** (`confirm_duplicate=1`), preserving entered values. Never silently creates.
   `app/routers/customers.py`, `app/templates/customers/_quick_create.html`, `.../new.html`.
3. **Drawer dirty-state protection** — ✅ Done (prior pass). `createSlideDirty` + `closeCreateSlide()`
   guard backdrop / Escape / X / Cancel with a native confirm. `app/templates/base.html`,
   `.../customers/_quick_create.html`.
4. **Edit / Save button visibility** — ✅ Done. Header toggle relabeled **Edit Customer** (primary
   button + pencil icon in view mode); Save Changes / Cancel moved into a right-aligned, full-width
   footer bar at the bottom of the edit card. `app/templates/customers/detail.html`.
5. **Quote workspace customer context** — ✅ Done. Header already showed terms + open/overdue AR;
   added a **Tax Exempt** chip and a read-only **Customer note** strip (compact, no broad redesign).
   `app/templates/quotes/workspace.html`.
6. **New Invoice visibility** — ✅ Verified present. Customer detail `header_actions` and the
   customer preview-dock Actions column both expose New Invoice. No change required.
7. **Open quote count behavior** — ✅ Done (prior pass). Detail "Open quotes" figure links to
   `/quotes/?customer_id={id}`; `list_quotes` honors the `customer_id` filter.
8. **Statement screen — FLAGGED FOR LATER POLISH (not started).** Loads and functions correctly;
   owner asked to defer cosmetic/UX polish. Do not redesign without an Architect instruction.
   Targets: `app/templates/customers/statement_form.html`, `.../statement_print.html`,
   and `customer_statement_*` routes in `app/routers/customers.py`.
