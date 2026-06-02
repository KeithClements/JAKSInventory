# JAKS UI Change Plan
*Living document — updated as screens are built.*

**Status:** Products ✅ · PO ✅ · Invoices ✅ · Customers ✅ · Invoice Workspace ✅ L3 · Quotes ✅ · Sales Orders ✅ · Vendors ✅ · Returns ✅ · **Payments ✅ L2** (all lists complete 2026-05-29) · **#10/#11 HOLD — functional-test mode** · FIX 1-4 + bcda974 landed · "Can't receive" = NameError@po_service.py:448 FIXED @3a700fd (pending owner re-test) · Compiled Tailwind ✅ LIVE · §8B + Save-button + method-chip-colors ✅ RULED · **§2C Line-Item Workspace Standard ✅ RATIFIED 2026-05-30** · **§9 Functional Gate ✅ RATIFIED 2026-05-30** · **⚠️ P0 core-path failures active — §9 re-sequencing in effect** · **⚠️ Quotes/Returns/Quote-WS "complete" marks gated on post-b514196 owner re-test (§8G)**.

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
| Quote Workspace | L3 | L3 | Autosave, inline editing done. **⚠️ "done" recorded in the 500-era build — gated on post-b514196 owner re-test (§8G).** **Shared one-click line-adder (§8H) migrated @797a407 — immediate-add replaces 2-step staging; governance PASS 2026-05-31.** |
| Customers List | ✅ L2 | L2 | Governance pass done 2026-05-29. §2B operational intelligence complete (Balance Due, Open Invoices/Quotes/SOs, Cores, Last Sale, Terms). M1/M2 cosmetic deferred. **Inactive-tab + Reactivate gap fixed 2026-05-31 — deactivated customers reachable/reactivatable; locked by `tests/test_customer_list_tabs.py` (see As-Built "Fix").** |
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

**Optional — Sort control (Builder-introduced 2026-06-01, pending Architect ratification):** lists that
need user-controlled ordering add a compact sort dropdown to the toolbar, right-aligned alongside the
search — a `<form method="get">` carrying hidden `tab`/`q`, a `<select name="sort"
onchange="this.form.submit()">` styled like the search field, and a `Sort by` label. The route reads
`sort` (default = the list's natural key) and normalizes unknown values back to the default; the search
form and Clear link must carry `sort` so it survives a search. Products list (`sku` / `vendor` /
`category`) is the reference. **Ordering that depends on a self-referential hierarchy (category
`full_path` = Major Group → Category → Sub-category) or a relationship's display name (preferred
vendor) is computed in Python after `.all()` with the relevant `joinedload`s — not raw SQL — with
no-value rows sorted last and the natural key as the tiebreaker.**

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
   - **As-built (ratified `receiving_queue.html` — the QB2 source of truth, 2026-05-28):** because queues render as a `<table>`, the divider is a full-width `<tr><td colspan="N">` using `bg-gray-50/70 px-4 py-2 text-xs font-bold text-gray-600 tracking-wide` with the group title; the **item-count badge is OPTIONAL** (neither receiving nor warranty render it). A queue that copies the reference verbatim is **conformant — do NOT punch it for the missing count or for `text-xs`/`py-2`.** The flex+count format above applies to non-table queue layouts; reconcile any future QB2 pass (Cores #16, Returns #17) against the reference, not this idealized prose.

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

**Inline-action-form variant — ratified 2026-05-31 via Cores #16 (`cores/list.html` @c6468af).** When a queue's items have **no per-item workspace** to link to, the always-visible action (§2A.6) may **expand a stage-specific inline `<form>` row** instead of linking out. Implement with **ONE board-level `x-data`** (e.g. `{ openId, … }`) on the wrapper `<div>` and `x-show="openId === {{ id }}"` on the form `<tr>`s — the form rows **must be descendants** of that div. **Do NOT** put `x-data` on a main `<tr>` and `x-show` on a *sibling* `<tr>`: Alpine scopes don't bridge siblings, so the toggle silently fails. `cores/list.html` is the reference for this variant; the group-by key may be a **lifecycle stage** (not just vendor).

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

### 2C. Line-Item Workspace Standard

**Ratified 2026-05-30.**

The app has two ratified screen archetypes (§2 Operational List, §2A Queue Board). Neither covers
the third major screen type — the workspace where the business actually makes money: a document
header + an editable line grid where the user finds parts, sets prices, and commits records.

Quote / Sales Order / Invoice / PO all implement this pattern. Before this standard existed, each
grew its own "add a part" implementation (different transport, different wiring, different search
quality), producing four divergent code paths — three of which were confirmed broken in the
2026-05-30 functional test pass. This section defines the standard so the shared implementation
replaces all four.

#### What a Line-Item Workspace is

A screen that combines a **document header** (customer, dates, status, autosave/save) with an
**editable line grid** (products, quantities, prices, totals) and **workflow actions** (Finalize,
Fulfill, Receive, Convert). The user finds parts, stages them, and commits.

Examples: Quote Workspace, Sales Order Workspace, Invoice Workspace, PO Workspace.

This is **not** an Operational List (it is not filtered/browsed — it is one record open for editing)
and **not** a Queue Board (it does not group work items for sequential processing).

#### Required Elements

1. **Document header card** — customer name, document number, status chip, date fields, discount,
   any screen-specific header fields (ESN, PO #, etc.). Autosave wired per §3. Back link always
   visible per §8B standard.

2. **Add-line panel** — full-width search input (icon-prefixed, 2+ character trigger) that returns
   product results. Selection **immediately adds the line** at qty 1 / suggested sell — no separate
   "Add" button step. Qty, price, and disc % are editable inline in the resulting row. Misc/free-text
   lines supported (search ≥2 chars, no product selected → adds a free-text line on Enter).

3. **One shared search endpoint** — `GET /search/products?q=` returns JSON:
   ```json
   [{"product_id": 1, "part_number": "OK-1", "description": "...", "qty_on_hand": 3,
     "suggested_sell": 45.00, "current_cost": 22.00, "last_sold_price": 44.00}]
   ```
   Search normalizes: strip punctuation/spaces, case-fold, match on SKU **and** OEM **and**
   cross-ref SKU. "ok1", "OK-1", "ok 1" must all resolve to the same result. All four workspace
   types call this one endpoint — no per-screen product-search routes.

4. **Line grid** — `divide-y divide-gray-100`, explicit `px-4 py-4 align-middle` (no `tbl-*`),
   `overflow-x-auto` wrapper with `min-w-[...]`. Each row editable inline with HTMX
   (`hx-trigger="change"`, `hx-target="#lines-section"`, `hx-swap="outerHTML"`). Delete via
   Alpine confirm modal (§3) — never `window.confirm()`.

5. **Totals bar** — HTMX-refreshed after every line action. Shows subtotal, tax, total,
   balance-due or amount-paid as applicable. Stays pinned at the bottom of the line section.
   **R1 — CC convenience fee (documented design decision; do NOT re-flag as a math bug):** the card
   surcharge is applied *at payment time* on the card portion only. The totals bar shows an
   **informational estimate** ("~$X.XX if paid by card") with a helper note — it is NOT added to the
   invoice total. Source: `app/invoice_totals.py:117-120` ("R1 — CC surcharge is applied AT PAYMENT TIME
   on the card portion only; this is an INFORMATIONAL estimate … and is NOT added to the total.").
   QA rule: any test that expects the surcharge in `invoice.total` or `balance_due` is testing the wrong
   thing — the total intentionally excludes the fee. Relabelling the estimate is UI polish; changing the
   math would violate R1.
   **Tax gate — `invoice.is_taxable` is AUTHORITATIVE (do NOT re-introduce the `or tax_rate_display > 0`
   fallback).** The totals engine (`app/invoice_totals.py:83-110`) has two paths: the finalized path uses
   per-line `is_taxable` (correct); the draft/legacy fallback used `invoice.is_taxable or tax_rate_display > 0`,
   meaning an invoice could appear taxable even when `is_taxable=False` if the customer had a tax rate on file
   — wrong for tax-exempt customers. The fallback is being removed: `invoice.is_taxable` is the only gate.
   At finalize, `invoice_service.py:589` reconciles it: `invoice.is_taxable = any(ln.is_taxable for ln in
   invoice.lines)`. Any future draft-path calc must also key off `invoice.is_taxable` only — the rate-based
   fallback has caused silent tax errors and is permanently banned.

6. **Workflow action bar** — reflects current status. Primary action rightmost (`btn-primary btn-sm`).
   Destructive action uses `btn-ghost btn-sm text-red-500` + Alpine confirm modal. Follows §8B
   workspace header zone order.

7. **Save affordance** — per **Save Standard v2 (2026-05-31)**. Autosave (L3) screens show **all three**:
   manual `btn-primary` Save + honest dirty-state pill + sticky save bar (the old "indicator-only, no Save
   button" rule is **superseded**). L1/L2 non-autosaved screens: real `btn-primary` Save in the footer.
   No ambiguity about whether work is persisted.

8. **Customer-context bar** — on sales-side workspaces (Quote, SO, Invoice): a compact strip
   showing terms, tax-exempt status, open AR balance, overdue balance, credit balance, cores owed.
   Collapsed by default to a single line; does not obscure the line grid.

#### Reference Implementation

`app/templates/quotes/workspace.html` is the official L3 reference for Line-Item Workspaces.

It demonstrates:
- Full-width part search with Alpine `lineAdder` component (JSON endpoint, keyboard-navigable results)
- Immediate qty/price/disc staging after product selection, then `+ Add Line` commit
- Child-line mode (warranty, upgrade options, optional lines)
- Autosave indicator with `setInterval` staleness label
- HTMX line mutations with chips-row lifecycle management
- Keyboard navigation across Qty → Price → Disc → search (Enter-driven)
- Misc/free-text line support (no product required)

**Note on one-click vs. two-step add:** The reference currently uses a two-step flow (select
product → + Add Line). Owner testing confirmed this reads as broken. **Decision pending Architect
ruling:** either (a) selecting a product immediately adds the line with editable defaults, or
(b) the "Add Line" button becomes much more prominent (large, brand-colored, keyboard-focused)
and shows "↵ Enter to add" hint. Resolve before adopting for SO/Invoice/PO.

#### What Is NOT Required

| Element | Why exempt |
|---|---|
| Filter tabs with counts | One record open — not a browsable list |
| Bulk action toolbar | Line-level actions only; no multi-select across lines |
| Preview dock | The record IS open — the workspace is the "preview" |
| Row selection checkboxes | No bulk actions |
| `pb-52` on wrapper | No preview dock |

#### Shared with Operational List and Queue Board

- Color semantics (§4) — identical meaning for all status chips and stripes
- Status chip format — `inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold`
- Left-edge stripe on first `<td>` — same color logic as §2 (when used for line-level urgency)
- Alpine confirm modal for all destructive actions (§3) — never `window.confirm()`
- Motion macros for any overlays (§ Build → Motion primitives)
- No `tbl-*` classes — explicit Tailwind padding only
- Monospace identifiers — SKU, PO #, Quote #, Invoice # use `font-mono text-sm font-bold text-brand-700`

#### Governance Checklist (Line-Item Workspace screens)

- [ ] One shared `/search/products?q=` JSON endpoint used — no per-screen product-search route
- [ ] Search normalizes punctuation and case (ok1 → OK-1; q2026 → Q-2026)
- [ ] Line add is discoverable — result selection either adds immediately or reveals a clear, prominent Add button
- [ ] No `window.confirm()` anywhere — all destructive actions use Alpine modal per §3
- [ ] Save Standard v2: autosave (L3) screens show manual Save + honest dirty-state pill + sticky save bar; L1/L2 have a real footer Save — never ambiguous; pill green ONLY when truly saved
- [ ] Customer-context bar (AR, terms, cores) present on sales-side workspaces
- [ ] Totals bar HTMX-refreshed after every line mutation
- [ ] `divide-y divide-gray-100` on line tbody; no `tbl-*` classes
- [ ] Workspace header follows §8B zone order (chip → back → secondaries → destructive → primary)
- [ ] Functional gate (§9) passed — end-to-end smoke completed, not just visual checklist

---

### 3. Shared Interaction Rules

These rules apply to every screen in the app. Do not deviate without updating this document.

**Row behavior:**
- `cursor-pointer` on clickable rows
- Entire row click = open preview dock (not navigate — **Operational List only**; Queue Boards use inline actions instead)
- Ctrl+click or action button = navigate to detail page
- Checkbox click must `@click.stop` to prevent row click

**Action buttons inside injected content (preview dock, htmx fragments):**
- A button rendered inside content loaded via `htmx.ajax()` (e.g. the preview dock partial) that needs
  to open a slide-over must trigger the load with an **explicit `htmx.ajax('GET', url,
  {target:'#create-slide-content', swap:'innerHTML'})` from `@click`**. Declarative `hx-get`/`hx-target`
  are **not** reliably wired on htmx-injected descendants, so a declarative button silently no-ops — the
  slide-over opens but stays on the loading skeleton. Alpine `@click` itself *does* run on injected
  content, so combine `@click` (open the slide + fire `htmx.ajax`) and stash the URL in a `data-` attribute
  read via `$el.dataset` to keep quoting sane. Reference: products `_preview_panel.html` "New PO" (2026-06-01).
- A plain `<a href="/…/new?…">` to a GET route that gates on the `HX-Request` header will redirect to that
  resource's list for a normal click — never use one for an action meant to open a slide-over.

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

**Save Standard v2 — RATIFIED 2026-05-31 (SUPERSEDES the 2026-05-29 rule below; the owner asked repeatedly).**
Every **autosave workspace** (Quote / SO / Invoice / PO / Product Detail) now shows **all three**:
1. **Manual `btn-primary` Save** — fires the same persist POST as autosave. Reverses the 2026-05-29 "remove
   redundant Save"; generalizes the 2026-05-30 PO + Product-Detail override to **all** autosave workspaces.
2. **Honest dirty-state pill** — reflects the **actual** state, never a permanent "Saved" lie:
   `dirty` (amber · "Unsaved changes") → `saving` (gray pulse · "Saving…") → `clean` (green · "Saved") →
   `error` (red · "Save failed — retry"). Driven by a real Alpine `saveState`: set `dirty` on `@input`,
   `saving` on POST, `clean` only on a 2xx, `error` on failure. Format under "Dirty-state pill format" below.
3. **Sticky save bar** — a `sticky bottom-0` bar within the workspace holding the pill (left) + Save (right),
   always reachable without scrolling.
The Invoice **Save Draft** exception and the L1/L2 (no-autosave) real-Save rows below still stand.

---

**Save button standard — RULED 2026-05-29 — ⚠️ SUPERSEDED 2026-05-31 by Save Standard v2 above (kept as history):**

| Screen grade | Standard | Rationale |
|---|---|---|
| **L3 workspace with autosave wired** | **Remove Save button. Add always-visible autosave indicator.** | The button is redundant and owner-confusing when autosave is running. |
| **Invoice workspace "Save Draft" exception** | **Keep it.** | "Save Draft" signals workflow state (you need to Finalize), not just persistence. Removing it would confuse the Draft→Finalize progression. |
| **L1/L2 form screens (no autosave)** | **Keep a real `btn-primary` Save in the footer.** | No autosave = user's only save mechanism. |
| **Owner override — PO Workspace + Product Detail (RULED 2026-05-30)** | **Show BOTH — explicit `btn-primary` Save AND the autosave/dirty indicator.** | Owner explicitly wants a visible Save on these two screens; overrides the "remove redundant button" row above **for them only.** Quote / SO / Invoice workspaces unchanged (indicator-only stands). |

**Dirty-state pill format (v2 — replaces the old always-green "Saved automatically" indicator):**
```html
<!-- Alpine saveState ∈ 'clean'|'dirty'|'saving'|'error': set 'dirty' on @input, 'saving' on POST,
     'clean' on a 2xx response, 'error' on failure. Green ONLY when truly persisted. -->
<span class="text-xs flex items-center gap-1.5"
      :class="{ 'text-amber-600': saveState==='dirty', 'text-gray-400': saveState==='clean'||saveState==='saving', 'text-red-600': saveState==='error' }">
  <span class="w-1.5 h-1.5 rounded-full"
        :class="{ 'bg-amber-400': saveState==='dirty', 'bg-green-400': saveState==='clean', 'bg-gray-300 animate-pulse': saveState==='saving', 'bg-red-500': saveState==='error' }"></span>
  <span x-text="({clean:'Saved', dirty:'Unsaved changes', saving:'Saving…', error:'Save failed — retry'})[saveState]"></span>
</span>
```
Lives in the **sticky save bar** beside the manual Save — not in `header_actions`. A permanent green "Saved
automatically" is **banned**: it lied while mid-edit or when autosave failed (the owner's recurring complaint).
The pill shows green **only** after a confirmed successful save.

**Screens that need this fix applied:**
- `purchase_orders/workspace.html` — autosave indicator is already wired (lines 204-218). **Per the
  2026-05-30 owner override: ADD an explicit `btn-primary btn-sm` Save submit alongside the indicator —
  do NOT remove the indicator.** The header form already `hx-post`s (`workspace.html:104`); add `submit`
  to its `hx-trigger` (line 106 → `…delay:600ms, submit`) so the button fires the same POST. Mirrors the
  pattern already live at `invoices/workspace.html:155`.
- `products/detail.html` — **already compliant, no rework:** visible `Save Changes` submit at
  `detail.html:314` + dirty/clean indicator at line 316. Owner re-test only.
- (Quote workspace: indicator-only, already fixed 2026-05-29 ✅ — **not** in the override scope.)

**The PO Save-button add is approved (2026-05-30 override); it is a surgical add only and does NOT lift
the #11 PO-Workspace L3 HOLD. All other screens still require explicit per-screen instruction.**

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

**Payment method chip colors — RULED 2026-05-29:**

| Method | `bg` / `dot` | Rationale |
|---|---|---|
| Cash | `bg-green-50 text-green-700` / `bg-green-500` | Immediate, certain — §4 "success/paid" |
| Check | `bg-blue-50 text-blue-700` / `bg-blue-500` | Standard AR transaction — §4 "informational" |
| Card (credit/debit) | `bg-purple-50 text-purple-700` / `bg-purple-500` | External processor workflow — §4 "special workflow" |
| ACH | `bg-blue-50 text-blue-700` / `bg-blue-500` | Electronic transfer — §4 "informational/activity" |
| Wire | `bg-sky-50 text-sky-700` / `bg-sky-500` | Builder used `sky-*` (not initial `gray-*` recommendation) — **ratified 2026-05-29.** `sky-*` is §4-permitted (`blue-*/sky-*` co-listed); better semantic than gray for an active transfer. |
| Account Credit | `bg-gray-100 text-gray-700` / `bg-gray-400` | Internal balance — §4 "neutral/metadata" |

Applied and verified in committed code (`payments/list.html:38-45`). Replaces `indigo-*` (ACH) and `cyan-*` (Wire) — both §4 violations. QA: no lint advisory on any of these families.

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

**⚠️ Priority re-sequence in effect (2026-05-30):** Core money-path flows (Quote → SO/Invoice →
PO receive) must be functionally verified before any new screen enters L2 work. See §9 Functional
Gate — Re-sequencing Rule and the P0/P1 issue table there. The Rollout Order below reflects the
visual governance history; §9 governs what gets worked next.

Apply the Operational Workspace UI System to screens in this order. Do not skip ahead — each screen informs the next.

| # | Screen | Status | Current → Target | Notes |
|---|---|---|---|---|
| 1 | Products List | ✅ L2 complete — official reference | L2.5 → L2 ref | Governance pass done |
| 2 | PO List | ✅ L2 complete | L1 → L2 | Governance pass done; overdue bug fixed |
| 3 | Invoice List | ✅ L2 complete | L1 → L2 | Governance pass 2026-05-28. Red stripe for financial overdue — accepted + codified in §4. |
| — | **Primitives extraction** | ✅ Complete (2026-05-29) | — | All 6 macros extracted + governance-approved. Products/PO/Invoice ported. See §7 as-built. |
| — | **Line-item builder (§8H) — 4 workspaces** | ✅ **Governance PASS (2026-05-31)** | — | Shared `line_items/_line_adder.html` migrated to Quote/SO/Invoice/PO (`797a407`/`2d76b83`/`5bb0bc0`/`1fb562c`). Immediate-add (no staging), sibling-above placement, match-type/orange-core/red-at-0/cost-vs-sell-by-mode visual contract all verified **in-tree**. §7 3-screen gate MET (macro promotion optional). Full record + punch list in §8H. |
| 4 | Customer List | ✅ L2 complete | L1.5 → L2 | Governance pass approved 2026-05-29. All §2 + §2B fields satisfied (Balance Due, Open Invoices/Quotes/SOs, Cores, Last Sale, Terms). M1/M2 cosmetic deferred. **Inactive customers now reachable + reactivatable (2026-05-31, see As-Built "Fix"); locked by `tests/test_customer_list_tabs.py`.** |
| 5 | Quotes List | ⏳ **L2 — pending post-b514196 owner re-test** | L2 → L2 | **⚠️ The 2026-05-29 "FULL PASS" was recorded against a 500-ing build — the committed visual baseline `quotes_list@1280px.png` is an "Internal Server Error" screenshot (see §8G re-test gate). NOT re-affirmed until the owner re-tests a post-`b514196` pull + §9 passes.** Original record (unverified): all 11 §2 + §2B; AR chip `ar_map` bulk-computed (zero N+1); 5 non-blocking cosmetics in §8F. |
| 6 | Sales Orders List | ✅ L2 complete | L1 → L2 | **Governance pass 2026-05-29 — FULL PASS.** Dock confirmed: import line 25, call line 346 (verified in committed code). All 11 §2 elements + §2B. `py-4` correct. Backend-contract guard accepted. |
| 7 | Vendors List | ✅ L2 complete | L1 → L2 | **Governance pass 2026-05-29 — FULL PASS.** Dock wired (import line 21 + call line 333). All 11 §2 elements + §2B (Open POs/Bills/Credits/LastPO/Contact). **Tab slugs realigned to the router contract (active/inactive/all) — RESOLVED 2026-05-31 (see "Fix 2" below); inactive vendors are now reachable and the detail page gained a Reactivate button. Locked by `tests/test_vendor_list_tabs.py`.** Lead time deferred per "if stored" qualifier. |
| 8 | Returns List | ⏳ **L2 — pending post-b514196 owner re-test** | L1 → L2 | **⚠️ The 2026-05-29 "FULL PASS" was recorded in the same 500-era build as Quotes (see §8G re-test gate); not re-affirmed until the owner re-tests a post-`b514196` pull + §9 passes.** Original record (unverified): all 11 §2 + §2B; real tab counts from router group_by (no stub guard); dock wired at line 339; stripe amber=received/blue=open/transparent=draft-closed. |
| 9 | Payments List | ✅ L2 complete | L1 → L2 | **Governance pass 2026-05-29 — FULL PASS (re-pass).** Dock: import line 16, call line 314 (live, not commented). Method chips: Cash=green, Check=blue, Card=purple, ACH=blue, Wire=sky (§4-permitted; sky co-listed with blue). All §2 + §2B previously verified. |
| 10 | Product Detail | ⏳ **HOLD — functional-test mode** | L1 → L2 | Section-based card layout. Do not start until hold lifted. **When lifted, the owner's UX review IS the spec** (not a generic L2 card upgrade) — build to the owner's review; Architect governs against it. Save Standard v2 applies (manual Save + honest pill + sticky bar). |
| 11 | PO Workspace | ⏳ **HOLD — functional-test mode** | L1 → L3 | Autosave, line editor, receive flow. Do not start until hold lifted. **Line-adder swap (§8H) applied during hold — line-adder ONLY; full L3 build remains HELD pending owner.** |
| 12 | Invoice Workspace | ✅ L3 complete | L3 candidate → L3 | Governance pass 2026-05-29. **PASS confirmed 2026-05-29** — window.confirm already cleared prior to pass. No blocking defects remain. A11y follow-up (role=dialog/aria-modal/focus-trap on payment/void/change-customer modals) tracked under §8 #4 a11y sweep — non-blocking for L3. |
| 13 | PO Receiving Queue | ✅ QB2 complete | QB1 → QB2 | Queue Board archetype — §2A. Governance pass 2026-05-28. Official QB2 reference. |
| 14 | PO Match Queue | ✅ QB2 complete | QB1 → QB2 | Queue Board archetype — §2A. Governance pass 2026-05-28. |
| 15 | Warranty Queue | ✅ **QB2 — Governance PASS 2026-05-31** | QB1 → QB2 | **PASS @e93cef9** — verified faithful copy of the ratified `receiving_queue.html`: metrics card-grid (Drafts/Awaiting Vendor/To Credit/To Notify), vendor group dividers, always-visible inline next-action+open+print, `border-l-4` on first `<td>`, `divide-y`, **no `tbl-*`**. Stripe/chip palette §4-consistent (purple=awaiting-vendor, green=approved/credited, amber=notified, red=denied, gray=draft). **1 required cosmetic (non-blocking):** `JAKS Ext` type chip uses orange (`list.html:156`) — §4 reserves orange for **core charges**; recolor to neutral gray/slate, and move the `Vendor` type chip off purple (collides with the 'Awaiting Vendor' status purple on the same row) — **fix before Cores Queue #16 ships.** Filename `list.html` kept (rename to `queue.html` declined — needless churn). UI Builder A owns. |
| 16 | Cores Queue | ✅ **QB2 — Governance PASS 2026-05-31** | QB1 → QB2 | **PASS @c6468af** — owner-ruled variant: ONE **stage-grouped** board (Awaiting Return → Pending Inspection → Ready to Ship → Awaiting Vendor) that **keeps inline action forms** (cores have no per-item workspace to link to). All §2A QB2 elements present: metrics card-grid (4 stages + overdue sub-count), colspan-td stage dividers, `border-l-4` on first `<td>` (red override for overdue), always-visible primary buttons expanding inline forms, status chips, `divide-y`, **no `tbl-*`**. Orange at `list.html:176` is the **legitimate** §4 core-charge (`customer_unit_charge`) — correctly NOT punished. Alpine expandable rows verified: ONE board-level `x-data`, form `<tr>`s as descendants (fixes the old sibling-scope toggle bug). **No punch items.** UI Builder A owns. |
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

#### Quotes List — As-Built Record — ✅ L2 complete (governance 2026-05-29)

**Governance pass: FULL PASS. AR chip landed; all §2 + §2B verified.**

All 11 §2 structural elements verified present:
- `pb-52` wrapper · `divide-y divide-gray-100` tbody · `overflow-x-auto` + `min-w-[1040px]` ·
  `border-l-4` stripe on first `<td>` (checkbox cell) · `filter_tabs` macro (7 tabs incl. Follow-up Due) ·
  `bulk_toolbar` macro · `status_chip` macro · `operationalListData` factory · `preview_dock_shell` macro ·
  Row click → `togglePreview()` · `@click.stop` on checkbox + action `<td>` ✅

§2B fields verified: Total · Margin % (computed inline from loaded lines, cost×qty vs subtotal) ·
Follow-up date / status with overdue flag · Customer terms chip (`terms_map`) · Line count ·
Valid-until date · Conversion status chip ✅

**AR chip — LANDED (cleared 2026-05-29):**
`ar_map` bulk-computed in `list_quotes` route using `defaultdict` over open invoices grouped by
`customer_id` — zero N+1 queries. Passes `open_balance` (float, sum of `balance_due`) and
`is_overdue` (bool). Template renders chip with two variants: `bg-red-100` (overdue) or
`bg-amber-50` (open, not yet overdue). Amount displayed as `Overdue $NNN` / `AR $NNN`.

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

#### Sales Orders List — As-Built Record — 🟡 ONE BLOCKER (governance 2026-05-29)

**Governance pass: SEND BACK — one functional blocker. Everything else passes. No full re-review needed after fix.**

**Blocker — preview dock commented out (Builder fix required, 2 lines):**
- `app/templates/sales_orders/list.html` line 19-25 — add this import after line 24:
  `{% from "macros/preview_dock.html" import preview_dock_shell %}`
- Lines 345-349 — replace the entire comment block with:
  `{{ preview_dock_shell('Sales Order Preview', 'so-preview-body') }}`

**Why this matters:** Without the dock, `togglePreview()` calls `document.getElementById('so-preview-body').innerHTML = ...` on a null element — runtime `TypeError` on every row click. The template already has `operationalListData('so-preview-body', '/sales-orders/preview/', [...])` wired correctly. The backend route `GET /sales-orders/preview/{so_id}` is registered and returns `sales_orders/_preview_panel.html`. The builder commented it out assuming the route wasn't live yet; it is.

**What passes:**
- `pb-52` wrapper ✅ · `divide-y divide-gray-100` tbody ✅ · `overflow-x-auto` + `min-w-[1100px]` ✅
- `border-l-4` on first `<td>` (checkbox cell) ✅ · stripe semantics: red=cancelled, amber=hold, blue=open/partial, transparent=fulfilled/invoiced ✅
- `filter_tabs` macro (7 tabs: All/Open/Partial/On Hold/Fulfilled/Invoiced/Cancelled) ✅
- **Tab counts from full unfiltered dataset** ✅ (group_by before any filter applied)
- **Search hidden input: `name="tab"` `value="{{ active_tab }}"`** ✅ (spec-clean, unlike Quotes)
- `bulk_toolbar` macro ✅ · `status_chip` macro ✅ · `operationalListData` factory ✅
- `@click.stop` on checkbox + action `<td>` ✅ · `cursor-pointer hover:bg-gray-50/80 transition-colors group` ✅
- `empty_state` macro with 3-case logic (passes `q` + `active_tab` to macro) ✅
- Preview route `/preview/{so_id}` registered before `/{so_id}` ✅ (lines 184 vs 220 in router)
- No `tbl-*` classes ✅ · All `<td>` use `px-4 py-4 align-middle` ✅ (correct `py-4`, not `py-3.5`)

**§2B fields all present:** SO # + line count + B/O flag · Customer + terms chip · PO # / ESN ·
Fulfillment status + qty filled/ordered progress · Payment mode + deposit amount · Invoice status ·
Total. Ship/tracking column absent — no tracking data in model yet (non-blocking, flag for later).

**Non-blocking cosmetics:**
- `emerald-*` chip for Invoiced status — same as Quotes; should align to `green-*` family (§4)
- `sky-*` chip for Deposit payment mode — not in §4 permitted families; replace with `blue-*`
- Ship/tracking column absent — add when `SalesOrder.tracking_number` or equivalent exists in model

**Accepted:** Backend-contract guard at lines 31-37 (stub `_counts` if route doesn't pass `counts`) — pragmatic template resilience; accepted.

---

#### Vendors List — As-Built Record — ✅ L2 (governance 2026-05-29)

**Governance pass: FULL PASS.**

All 11 §2 structural elements verified:
- `pb-52` wrapper · `divide-y divide-gray-100` tbody · `overflow-x-auto` + `min-w-[1020px]` ·
  `border-l-4` on first `<td>` (checkbox cell) · `filter_tabs` macro (3 tabs: All/Active POs/Open Credits) ·
  `bulk_toolbar` macro · `status_chip` macro · `operationalListData` factory ·
  `preview_dock_shell` macro (import line 21 + call line 333) · `empty_state` macro ·
  `px-4 py-4 align-middle` on all `<td>` ✅

§2B fields verified: Open POs count (chip) · Open Bills count (billed-status POs) · Credits pending
(chip + total dollar amount) · Last PO date (relative + PO# in monospace) · Primary contact
(name + phone). Lead time deferred — no `lead_time` field on Vendor model; explicitly deferred
per §2B "if stored" qualifier ✅

**Stripe semantics accepted:** amber=open credits (vendor owes us money), blue=active open POs,
transparent=no active relationship. Colour semantics are correct per §4 (amber=attention,
blue=informational).

**Backend-contract guard accepted** (lines 27-33): `active_tab` and `_counts` degrade gracefully
when backend hasn't yet passed the `open_pos`/`credits` keys. Tabs show stub zeros; no error.
Non-blocking. Backend update needed to pass real counts keyed by `open_pos`/`credits`.

**Non-blocking cosmetics (none filed — screen is clean).**

---

#### Vendors List — Builder Brief (#7) — now superseded by as-built above

**Backend is done.** Do not wait for backend — everything needed is live.

**Two fixes needed before submitting for governance:**

**Fix 1 — Preview dock (import + uncomment).**
At the bottom of `app/templates/vendors/list.html` there is a comment block (lines 332-337).
Replace the entire comment block with these two items — the import goes at the TOP with the other
imports (after `empty_state`), the macro call stays at the bottom:

```jinja2
{# Add to imports block (after empty_state import): #}
{% from "macros/preview_dock.html" import preview_dock_shell %}

{# Replace the comment block with: #}
{{ preview_dock_shell('Vendor Preview', 'vendor-preview-body') }}
```

The backend route `GET /vendors/preview/{vendor_id}` is already registered before `GET /vendors/{vendor_id}`
and returns `vendors/_preview_panel.html`. No backend changes needed.

**Fix 2 — Tab slug mismatch.**
The template defines tabs with slugs `open_pos` and `credits`:
```python
vendor_tabs = [('', 'All', ...), ('open_pos', 'Active POs', ...), ('credits', 'Open Credits', ...)]
```
But the router (`app/routers/vendors.py`) only handles `active`/`inactive`/`all` as `active_tab` values,
and the `counts` dict uses keys `""`, `"active"`, `"inactive"` — not `"open_pos"` or `"credits"`.

**✅ RESOLVED 2026-05-31 (template-side, the plan's preferred option).** Tabs now match what the
router computes:
```python
vendor_tabs = [
  ('active',   'Active',   _counts.get('active',   0)),
  ('inactive', 'Inactive', _counts.get('inactive', 0)),
  ('all',      'All',      _counts.get('all',      0)),
]
```
**Correction to the original snippet:** the All tab uses slug **`all`**, not `''`. The router
defaults any unrecognized slug to the `active` filter (`active_tab = tab if tab in
("active","inactive","all") else "active"`), so an All tab keyed `''` would have shown active-only
rows with the active count — a silent lie. Default view stays `active` (deactivated vendors hidden,
matching the deactivate copy "they will no longer appear in vendor lists"); the **Inactive** tab
surfaces them and **All** shows both. Footer "· filtered" is now suppressed on the All tab (matches
the products reference convention `{% if tab != 'all' or q %}`).

**Paired fix — `vendors/detail.html` Reactivate button.** The `/vendors/{id}/reactivate` route
existed but had no UI, so a deactivated vendor was a dead end. Added a **Reactivate vendor** button
(form-outside-form, mirroring the deactivate pattern) shown when `not vendor.is_active`.

Both locked by `tests/test_vendor_list_tabs.py` (7 tests, green). The `open_pos`/`credits` "richer
tabs" idea is dropped; if wanted later it is a separate backend-lane change.

---

#### Customers List — Inactive-tab + Reactivate Fix — ✅ (2026-05-31)

Same "deactivated records unreachable" gap the Vendors List had, fixed the same day the same way.
`customer_list` hard-filtered `is_active == True` on every tab and shipped no `/reactivate` route,
so a customer deactivated via `POST /customers/{id}/deactivate` was unreachable from the UI and
could never be turned back on.

**Decision (owner-confirmed):** unlike Vendors — whose `all` tab is a true active+inactive union —
the Customer List is the busy counter screen, so its four operational tabs (**including `all`**)
stay scoped to **active** customers (`all` = all *active*). A dedicated **`inactive`** lifecycle tab
(`is_active == False` only) surfaces deactivated accounts; the default view is unchanged.

- **Backend (`app/routers/customers.py`):** added the `inactive` tab branch (dedicated query — it
  lives outside `all_active` and its activity maps); an `inactive` count in `counts` from the full
  dataset; per-row invoice/quote count lookups for inactive rows (so a deactivated account with
  lingering open items still reads true); and `POST /customers/{id}/reactivate` (mirrors
  `/vendors/{id}/reactivate`; 303 → detail). Factored shared `_OPEN_INVOICE_STATUSES` /
  `_CLOSED_QUOTE_STATUSES` constants and dropped the dead `tab_ids` assignments.
- **Template (`customers/list.html`):** added the **Inactive** filter tab (5th pill, count-badged).
- **Template (`customers/detail.html`):** state-aware status forms (deactivate-form when active /
  reactivate-form when inactive — form-outside-form, no nesting); a gray **Inactive** banner with a
  one-click **Reactivate** button (visible in view *and* edit mode); the edit-footer Deactivate
  button is now active-only. An inactive customer's page carries no deactivate path, and vice-versa.
- **Locked by `tests/test_customer_list_tabs.py`** (12 tests, green — part of the 513-passed
  functional suite). The only red in the full run is the known run-to-run-unstable
  `test_visual_regression` baseline set (diffs vs the live mutable `jaks.db`); it now includes an
  *expected* `customers_list` diff from the new tab — do **not** re-baseline. Verified live on
  :8000: deactivate → row appears only under Inactive (count 1, hidden from default) → detail shows
  the Reactivate banner → reactivate → back in the active list (count 0).

---

#### Returns List — As-Built Record — ✅ L2 (governance 2026-05-29)

**Governance pass: FULL PASS.**

All 11 §2 structural elements verified in template + router:
- `pb-52` wrapper · `divide-y divide-gray-100` tbody · `overflow-x-auto` + `min-w-[1000px]` ·
  `border-l-4` on first `<td>` (checkbox, line 190) · `filter_tabs` macro (5 tabs: All Open /
  Draft / Authorized / Received / Closed) · `bulk_toolbar` macro · `status_chip` macro ·
  `operationalListData` factory · `preview_dock_shell` wired (import line 22, call line 339) ·
  `empty_state` macro (3-case, `q` + `active_tab` passed) · `px-4 py-4 align-middle` ✅

Router verified: tab counts from `group_by` over full dataset — **no stub guard needed** (router
provides real counts immediately). "All Open" tab correctly excludes CLOSED records
(`sum(v for k, v in _raw.items() if k != RAStatus.CLOSED)`). Preview route `/preview/{ra_id}`
at line 279 registered before `/{ra_id}` at line 338 ✅

**Stripe semantics accepted:** amber=received (goods in, needs processing — §4 attention),
blue=open/authorized (waiting for goods — §4 informational), transparent=draft/closed. ✅

**§2B fields verified:** RA # (monospace) · Customer + reason snippet · Status chip ·
Disposition chips (return_to_stock/quarantine/vendor_return) · Linked invoice # + credit memo # ·
Total credit + restocking fee · Requested date · Line count ✅

**Domain note:** §2B brief listed "Vendor" — this is a customer-facing RA screen, so Customer
is the correct entity (not vendor). Accepted; no fix needed. Received date not shown as column —
covered by Received status chip; acceptable.

**Non-blocking:** none filed — screen is clean.

---

#### Payments List — As-Built Record — 🟡 SEND BACK (governance 2026-05-29)

**Governance pass: SEND BACK — one blocker (dock), two advisory color violations.**

**Blocker — preview dock commented out (same fix as SO List ×2, Payments is the third):**

`app/templates/payments/list.html` lines 312-317 contain the dock in a Jinja2 comment block.
The `preview_dock_shell` import is also inside the comment. Fix (2 lines, builder must do):

1. Add to imports block (after line 15, with the other macros):
   `{% from "macros/preview_dock.html" import preview_dock_shell %}`
2. Replace lines 312-317 (the entire comment block) with:
   `{{ preview_dock_shell('Payment Preview', 'payment-preview-body') }}`

Backend route `GET /payments/preview/{payment_id}` is at line 234, registered before
`GET /payments/{payment_id}` at line 296 — **backend is done**. Template fix only.

**Advisory color violations (warn, do not block — §0 ruling 2026-05-29):**
- `'ach': ('bg-indigo-50 text-indigo-700', 'bg-indigo-500', 'ACH')` — `indigo-*` not in §4.
  Replace with `bg-blue-50 text-blue-700` / `bg-blue-500` (ACH = financial activity = blue ✅).
- `'wire': ('bg-cyan-50 text-cyan-700', 'bg-cyan-500', 'Wire')` — `cyan-*` not in §4.
  Replace with `bg-blue-50 text-blue-700` / `bg-blue-500` (Wire = financial activity = blue ✅).

File: `app/templates/payments/list.html` lines 40-43 (`method_chip` dict).

**Everything else passes:**
- `pb-52` · `divide-y` · `border-l-4` on first `<td>` (line 171) · `overflow-x-auto` +
  `min-w-[980px]` · no `tbl-*` · `px-4 py-4 align-middle` ✅
- Router: real counts (group_by, no stub guard) · `active_tab` ✅ ·
  preview route line 234 before `/{payment_id}` line 296 ✅
- Stripe: red=reversed/NSF (§4 problem ✅), amber=unapplied balance (§4 attention ✅),
  transparent=fully applied ✅
- §2B: Payment # (monospace) · Customer · Amount + applied/unapplied · Method + check# ·
  Related invoices (up to 3 shown + overflow count) · Status chip + reversal reason ·
  NSF fee · Footer total-received + total-unapplied ✅
- `name="tab"` hidden input ✅ · 3-case empty state ✅ · `@click.stop` on both cells ✅

**Self-certify after dock fix + optional color cleanup.** No re-review needed.

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

**↪ Line-item builder primitive (§8H) — 3-SCREEN GATE MET (2026-05-31).** The shared
`line_items/_line_adder.html` include now runs in **4** workspaces (Quote/SO/Invoice/PO) identically, so
the §7 "3 real implementations" gate is satisfied. Promoting the include to a `macros/line_adder.html`
macro is therefore **PERMITTED but optional** — the include is already single-source DRY. **5th-consumer
ruling:** `products/detail.html:663` is a product *picker* (selects a SKU into a hidden field for a
suggested-sell relationship), **not** a line-adder — when migrated off the now-dead PO search route it will
consume the `/line-items/product-search` **JSON** endpoint via a small Alpine dropdown (**NOT** a naive
`hx-get` repoint — that endpoint returns JSON, not an HTML partial), so it does **not** consume
`_line_adder.html` and does **not** change the macro calculus. Promote only if a genuine 4th+ *line-adder*
consumer or a config-signature need arises.

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

**⚠️ Rule: any new Tailwind utility class added to a template requires `npm run build:css` + commit of `app/static/css/app.css`. The compiled file is the sole CSS source — the CDN is gone. A class that exists only in a template but not in app.css will be invisible in the browser.**

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

5. **PO List — `rose-*` row tint (QA Rule-5 advisory).**
   `app/templates/purchase_orders/list.html:165` uses `bg-rose-50/30` for cancelled row tint.
   `rose-*` is not in §4 permitted families; §4 only permits `red-*` for the red/problem semantic.
   Fix: `bg-rose-50/30` → `bg-red-50/30`. One-character change.
   **Ruling (Architect, 2026-05-29):** `rose-*` remains outside §4. This is a valid QA lint
   advisory (not a block). The screen is ✅ L2 — this is a cosmetic fix only.
   **QA note:** Keep the `rose-*` lint advisory as-is. Do not add `rose-*` to the allowlist.

   *(The other 3 QA-flagged violations — `sky-*` in Drop Ship badge on PO list, preview panel, and
   receiving queue — are **not violations**. §4 explicitly permits `blue-* / sky-*` together.
   Update the lint pattern to accept `sky-*` wherever `blue-*` is accepted.)*

#### 8D. Compiled CSS Cut-over — ✅ COMPLETE (2026-05-29)

**Node v24.16.0 + npm 11.13.0. Build ran 2026-05-29. CDN removed. FOUC eliminated.**

- `app.css` 77.7 KB compiled output, serving 200 OK, no `cdn.tailwindcss.com` request confirmed.
- Two `@apply` violations fixed during build: `@apply group` and `@apply group-hover:opacity-100`
  are not permitted by the CLI. Fixed by removing `group` from `.tbl-row @apply` (add in HTML)
  and replacing `group-hover:opacity-100` with `.group:hover .row-actions { opacity: 1; }`.
- `app.css` committed for dev parity (other lanes without Node can still run the app).
  **Gitignore app.css in CI/CD pipelines** — rebuild from source on deploy.
- To rebuild: `npm run build:css` (or `npm run watch:css` during active template work).
- Node is at `C:\Program Files\nodejs\` — add to system PATH to use `npm` without full path.

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

#### 8B. Workspace Action-Header Standard — ✅ DRAFTED 2026-05-29

**Audit complete. Standard defined below. Apply to new workspace screens (SO, Warranty, Returns).
Existing screens get conformance fixes as a low-priority sweep — non-blocking for their current maturity level.**

---

##### As-audited state (5 workspaces, verified in templates)

| Screen | Status chip | Back link | Button order (L→R) |
|---|---|---|---|
| **Customer detail** | None (not a workflow record) | `← Customers` always ✅ | New Quote (primary) · New Invoice (secondary) · Statement (secondary) |
| **Quote workspace** | `badge-*` first ✅ | `← All quotes` — `hidden lg:inline` ❌ | chip · customer name · primary-CTA · More▾ · back-link |
| **Invoice workspace** | **None in header** ❌ | **None** ❌ | (DRAFT) Save Draft · Finalize · (OPEN) Print · PDF · Take Payment · Void |
| **SO workspace** | `badge-*` first ✅ | **None** ❌ | chip · Print · PDF · $ Deposit · Hold · Cancel |
| **PO workspace** | `badge-*` first ✅ | `← Purchase Orders` always ✅ | chip · Print · PDF · primary-CTA · Cancel PO · back-link |

**Deviations punched:**
1. **Invoice** — no status chip in header (status visible only in page body)
2. **Invoice** — no back link to `← Invoices`
3. **SO** — no back link to `← Sales Orders`
4. **Quote** — back link hidden on mobile (`hidden lg:inline`); should always be visible
5. **Back link classes split:** PO/Customer use `text-sm text-gray-500 hover:text-gray-700`; Quote uses `text-sm link-subtle` — unify to former
6. **Quote** — convert-to-invoice form uses `return confirm()` in `onsubmit` — §3 requires Alpine modal

---

##### The Standard — workspace header zones (left → right)

```
[status chip]  [back link]  ···  [secondary…]  [destructive]  [primary]
```

**Zone 1 — Status chip** (always present on workflow-record workspaces; absent on Customer detail which has no workflow state). Use the screen's `status_pill` dict defined above the block:
```html
<span class="{{ status_pill[record.status][0] }}">{{ status_pill[record.status][1] }}</span>
```

**Zone 2 — Back link** (always present, always visible — no `hidden lg:*`). Points to the list screen, same tab (no `target="_blank"` — navigating away is intentional). Placed second so the user can escape quickly.
```html
<a href="/{list}/" class="text-sm text-gray-500 hover:text-gray-700 transition-colors">← {List name}</a>
```
Exception: Customer detail keeps back link last because New Quote is the dominant CTA.

**Zone 3 — Secondary utilities** (Print, PDF, Statement — things that don't change state). Class: `btn-secondary btn-sm`. Print before PDF when both present.

**Zone 4 — Destructive action** (Cancel, Void — when the record isn't already terminal). Class: `btn-ghost btn-sm text-red-500` — not `btn-danger`, which is too visually heavy for a header. Must dispatch to an Alpine confirm modal per §3; never `window.confirm()` or `return confirm()`.

**Zone 5 — Primary action** (the next workflow step: Finalize, Send, Take Payment, → SO). Class: `btn-primary btn-sm`. Rightmost. Only one primary at a time. Empty when the record is terminal (PAID, CANCELLED, CONVERTED, etc.).

**Button cap:** max 4 buttons (excluding chip and back link). If more actions are needed for a given status, use a `More ▾` secondary dropdown (see Quote workspace `_header_actions.html` pattern).

---

##### Shell template for new workspace screens

```html
{% block header_actions %}
  {# Zone 1 — status chip #}
  <span class="{{ status_pill[record.status][0] }}">{{ status_pill[record.status][1] }}</span>

  {# Zone 2 — back link (always visible) #}
  <a href="/{list}/" class="text-sm text-gray-500 hover:text-gray-700 transition-colors">← {List name}</a>

  {# Zone 3 — secondary utilities #}
  <a href="/{record}/{{ record.id }}/print" target="_blank" class="btn-secondary btn-sm">Print</a>
  <a href="/{record}/{{ record.id }}/pdf"                   class="btn-secondary btn-sm">PDF</a>

  {# Zone 4 — destructive (when not terminal) #}
  {% if record.status not in terminal_statuses %}
  <form id="cancel-form" method="post" action="/{record}/{{ record.id }}/cancel" class="hidden"></form>
  <button type="button" class="btn-ghost btn-sm text-red-500"
          @click="$dispatch('confirm', {formId:'cancel-form', title:'Cancel?', body:'...', cta:'Cancel', danger:true})">
    Cancel
  </button>
  {% endif %}

  {# Zone 5 — primary action (next workflow step; absent when terminal) #}
  {% if record.status == 'open' %}
  <button type="submit" form="submit-form" class="btn-primary btn-sm">Next Step →</button>
  {% endif %}
{% endblock %}
```

---

##### Conformance fixes for existing screens (low-priority sweep — do not start without instruction)

| Screen | File | Fix |
|---|---|---|
| Invoice workspace | `app/templates/invoices/workspace.html` | Add `badge-{status}` chip as first element; add `← Invoices` back link |
| SO workspace | `app/templates/sales_orders/workspace.html` | Add `← Sales Orders` back link |
| Quote workspace | `app/templates/quotes/_header_actions.html` | Remove `hidden lg:inline` from back link; replace `return confirm()` in convert form with Alpine modal dispatch |

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

#### 8G. Owner-Test Triage — Functional-Test Phase (2026-05-29)

**Context:** Owner entered functional-test mode after list rollout. The following fixes landed
during triage. Recorded here as the authoritative change log; not pending further action unless
re-test surfaces new defects.

**FIX 1-4 + commit `bcda974` — landed:**
Details of the specific fixes are tracked in the backend/feature commit log. The Architect's
record is that FIX 1-4 were verified by the backend/builder lane and committed at `bcda974`.
No UI macro or base.html changes were involved in this set.

**"Can't receive" cluster — ❌ RETRACTED 2026-05-30: this WAS a code defect.**
The earlier conclusion ("the receiving flow was reviewed… no code defect was identified… believed to
be a workflow/UX discoverability issue") is **withdrawn.** Ground-truth of the receive path found a
hard crash: `app/services/po_service.py` raised **`NameError` at line 448** — `SOLineStatus` was
referenced but never imported from `constants`. Receiving 500'd before any "discoverability" question
could apply; the code review that cleared it read past a missing import. **Fixed at commit `3a700fd`**
(B2: "add `SOLineStatus` to constants import (NameError at line 448)"). Status: **automated-green @9d0ced2** — `tests/test_e2e_flows.py::test_e2e_a_po_receive_updates_inventory_and_cost`
runs PO → Receive and asserts on-hand 0→10 + moving-avg cost 10→12 (re-verified passing 2026-05-31),
so the §9 P1 re-test gate is **cleared** (owner spot-check optional, non-blocking). The §9 functional
gate exists precisely because of this: "no defect" is not a valid finding until the flow has been *run*,
not just read.

**Post-b514196 re-test gate — Quotes / Quote Workspace / Returns (RULED 2026-05-30):**
The L2/L3 "complete" marks for **Quotes List (#5), Quote Workspace, and Returns List (#8)** were
recorded while the app was returning **HTTP 500** — pre-`b514196`, the deprecated Starlette
`TemplateResponse(name, {"request": …})` signature broke template rendering app-wide. **Proof:** the
committed visual baseline `tests/visual/baselines/pixels/quotes_list@1280px.png` is a screenshot of the
**"Internal Server Error"** page, not the Quotes list. `b514196` (TemplateResponse migration) has since
landed, with 9 commits on top of it. **Ruling:** these three statuses are **not re-affirmed on the
Architect's word.** They stay gated until the **owner re-tests a post-`b514196` pull** and the §9
functional gate passes end-to-end. The poisoned baseline must be re-captured by QA after that re-test.
Until then §6 #5 / #8 read "L2 — pending re-test," not "FULL PASS."

**Change-customer (TESTING_FEEDBACK §1.8.b) — ✅ close-out RULED 2026-05-30 (re-test, not rework):**
`bcda974`'s **pencil-badge** is the **correct fix** and is **in-tree** at
`app/templates/invoices/workspace.html:173` — edit-pencil icon + "Change" label, `type="button"`,
`@click="changeCustomerOpen = true"`, DRAFT-gated via `{% if editable %}`. No rework needed; this is
**closed pending owner re-test only.** Optional defensive hardening (low priority, not a blocker): add
`@click.stop` to the button at `workspace.html:173` so the open-overlay click can never bubble to a
future parent click handler.

**#10 Product Detail / #11 PO Workspace — on HOLD:**
Both screens are explicitly deferred during functional-test phase. Do not start either until the
owner lifts the hold. The Save-button ruling (§3) and §8B workspace header standard are the
pre-work that will apply when these screens resume.

---

**Quote Workspace partial pass — Architect-approved & implemented 2026-05-29.** Owner testing of
`quotes/workspace.html` surfaced three issues; fixes 2 & 3 approved and applied (verified live):
1. *"Can't add a part"* — **NOT a bug** (add-line works end-to-end; verified by running the app).
   Root cause is a hidden two-step "staging" flow: clicking a search result only stages the part
   (`selectProduct()`), and **+ Add Line** sits greyed-out/`disabled` until then, so it reads as
   broken. **Left as-is pending Architect decision** between one-click-add vs. a prominent-staged-
   button + "↵ Enter to add" hint. **RULED 2026-05-31 (§8H): one-click immediate-add adopted** (ratifies Contract §3.2); the staging step is
retired. This discoverability gap is **closed by design — pending UI-Builder implementation** of the
generalized shared line-adder.
2. *Account link lost work* — the Account / customer-name / "← All quotes" links navigated away
   in-tab, discarding staged (uncommitted) search input. Now `target="_blank" rel="noopener"`.
3. *No visible Save* — the grey **Save** button was **redundant** (its `POST /quotes/{id}` updates
   the same 4 fields as the 2.5s autosave). Removed it, added `onsubmit="return false"` to block
   accidental Enter-submit, and made the autosave indicator always-visible ("✓ Saved automatically").
This is a targeted fix, **not** the full cross-workspace header standard — §8B proper is still open.

#### 8H. Line-Item Builder — Shared-Primitive Governance Ruling (Architect, 2026-05-31)

Backend shipped the data contract (`LINE_ITEM_BUILDER_CONTRACT.md`: one `GET /line-items/product-search`
+ one unified add-line POST per doc). This ruling sets the **UI** pattern **before** UI-Builder
generalizes the quote `lineAdder` into a shared component — per Contract §4 ("Style passes are the
Architect lane's call") and the standing rule that design deviations are the Architect's call.

**1. Interaction model — RULED: one-click immediate add-on-select.** Ratifies Contract §3.2.
Click a result → immediately `POST {product_id, qty}` (qty from a small stepper, default 1) → swap the
returned partial → reset the search box. This **retires the 2-step staging flow** (`selectProduct()` →
disabled **+ Add Line**) currently in `quotes/workspace.html:435-478`, and **resolves the §8G #1 "can't
add a part" discoverability gap** (the greyed-out + Add Line button that read as broken) **by design**.
Reversibility requirement: one-click inline line-delete must stay (an accidental add is trivially undone).
Fallback if accidental-adds surface in test: Enter-to-add on the keyboard-highlighted result — **not** a
return to the disabled staging button.

**2. Where it lives — RULED: ONE shared `{% include %}` partial now, NOT a `macros/` macro yet.**
Governance §7 forbids macro extraction until **three** screens use the pattern identically (premature
abstraction = wrong interface). Today only Quote has a proven adder; SO/Invoice/PO diverge (Contract §2:
different qty/money fields, returned partials, status guards; PO is cost-only + core). A frozen macro
signature now would be premature.
- **Location:** new shared `app/templates/line_items/_line_adder.html` (+ co-located JS), namespaced to
  the backend `/line-items` route prefix. Each workspace `{% include %}`s it with a per-doc config (below)
  — single source of truth (satisfies Contract §4) **without** a frozen macro interface.
- **Promotion gate:** convert include → `app/templates/macros/line_adder.html` **only after 3 of the 4
  workspaces run it identically** (the §7 three-screen gate). PO is the expected permanent config-variant
  (cost-mode + core), so Quote/SO/Invoice are the three that will prove the interface.

**3. Config surface — RATIFIED (Contract §3).** The include's **only** divergence surface; everything
visual stays identical across all four:

    { docType, searchUrl:"/line-items/product-search", postUrl:"/<doc>/{id}/lines",
      mode:"sell"|"cost", showDiscount:bool, target:"<lines region>", swap:"beforeend"|...,
      childMode:{parentLineId,lineRole}|null  /* Quote only */ }

Extraction map from `quotes/workspace.html`: hardcoded `/quotes/product-search` (L423) → `searchUrl`;
`/quotes/{id}/lines` (L471) → `postUrl`; the child-vs-top-level target/swap branch (L468-478) →
`target`/`swap` + `childMode`. Keep the legacy result aliases (`part_number`/`current_cost`/
`suggested_sell`/`description`) working at first; prefer canonical (`sku`/`title`/`unit_cost`) in new code.

**4. Visual contract — SET (Architect's call). Existing tokens only — no new colors/classes.**
All four adders look identical; `mode`/`showDiscount` only toggle field visibility.
- Search input: icon-prefixed (list-search convention), `rounded-xl border-gray-200`.
- Results dropdown: `bg-white border border-gray-200 rounded-xl shadow-lg`; rows `divide-y
  divide-gray-100`, hover `hover:bg-gray-50/80 transition-colors`, keyboard focus `focus:bg-brand-50/40
  ring-inset ring-1 ring-brand-300`.
- `match_type` chip per result (existing chip tokens): part_number → gray, cross_ref → blue (`badge-blue` — RULED, see §8H PASS),
  vendor_sku → purple, description → gray.
- Core indicator (`has_core`) → **orange** (palette: orange = core charge). `qty_available` → red at 0
  (out-of-stock), gray otherwise. `last_sold` → gray metadata, `text-[11px]`.
- Qty stepper: small inline numeric, default 1.
This dropdown is a **new workspace visual primitive — defined here = approved**; UI-Builder copies it
verbatim, no improvisation. It is independent of **§8B** (workspace-header standard, still OPEN); §8B,
when it lands, governs the header above the adder, not the adder itself.

**5. Rollout — incremental, core-money-path first (NOT big-bang).** The Contract keeps the old per-doc
endpoints alive for one-at-a-time migration. Order anchored to the Re-sequencing Rule:
1. Extract the include from Quote's working `lineAdder` (Quote → immediate-add; closes §8G #1).
2. Wire **SO** — clears Re-sequence **P0** "Can't add parts to Sales Order."
3. Wire **Invoice** — clears Re-sequence **P0** "Can't add parts to Invoice."
4. **§7 gate** → promote include to `macros/line_adder.html`.
5. Wire **PO** (cost-mode + core) last (P1).
Each step deletes that screen's old search partial + endpoint per Contract §4, and verifies line-add
(plus, for SO/Invoice, the core money path) before moving to the next.

**Lane ownership:** the data contract is Backend's (`LINE_ITEM_BUILDER_CONTRACT.md`); this UI pattern +
visual contract is the Architect's, recorded here. UI-Builder implements per the §6 rollout; QA gates
each screen's line-add against the functional suite.

**✅ MIGRATION COMPLETE + GOVERNANCE PASS — 2026-05-31 (Architect).** All four workspaces now run the
shared `line_items/_line_adder.html` include; verified **in-tree**, not on report:
- **Commits (branch backend/workflow-series-3):** Quote + partial `797a407`; SO `2d76b83` (clears Re-seq
  P0 "can't add parts to SO"); Invoice `5bb0bc0` (clears Re-seq P0); PO cost+core `1fb562c`.
- **Immediate-add (§8H.1): PASS** — `selectProduct()` → `_post({product_id, qty})`; no staging, no disabled
  +Add Line (`_line_adder.html:240-242`). Enter fast-path adds the top result / a misc line.
- **Placement (the "gotcha"): PASS** — SO/Invoice/PO adders are preceding **siblings ABOVE** their
  `outerHTML`-swapped sections (`#so-lines-section` L353<359 · `#lines-and-totals` L429<435 ·
  `#po-lines-section` L254<258), so the adder survives re-render and keeps focus; Quote is `beforeend`
  into `#quote-lines-tbody` (adder above the tbody). Configs verified: SO/Invoice `mode=sell` +
  `show_discount=true`; **PO `mode=cost` + `show_discount=false`** (cost + core, orange); `child_mode=true`
  Quote-only.
- **Visual contract (§8H.4): PASS** — dropdown, hover/focus tokens, core=orange, qty_avail red-at-0 all
  verbatim. `new_product` slide-over host is global in `base.html` (L300/L898) → + New Product wired on all four.
- **§3/§4: clean** — no `window.confirm`; safe single-quoted `x-data` injection (cf. alpine injection rule).
- **§7 three-screen gate: SATISFIED** (4/4 run it identically). Macro promotion to `macros/line_adder.html`
  is therefore **PERMITTED but DEFERRED as optional** — the shared include is already single-source DRY;
  promote only if a 5th consumer or a config-signature need arises. **Do not treat the gate as still pending.**

*Non-blocking punch list (cosmetic — do NOT hold the migration):*
1. `cross_ref` chip uses `badge-blue` not a literal "sky" token (`_line_adder.html:116`) — compliant
   **RULED 2026-05-31 — `badge-blue` ACCEPTED.** No `badge-sky` token exists; blue/sky are one §4
   informational bucket (cf. Payments "Wire=sky co-listed with blue"). §8H.4 wording updated "sky" →
   `badge-blue`; **no `badge-sky` token will be added.** Item closed.
2. Core chip uses inline `bg-orange-100 text-orange-700` (`_line_adder.html:140`) not a tokenized
   `badge-orange` — add the token to `class-tokens.md` if the core chip recurs.
3. + New Product create→add-back: confirm in-browser the quick-create slide-over dispatches
   `record-created {type:'product', id}` so the new product auto-adds (search→click→add core path already
   in-browser verified). QA item.

**Do NOT re-flag the §8H migration as open** — it is in-tree and PASSED. Remaining follow-ups are QA's
(stale smoke add-part selectors) and Backend's (delete the now-dead per-doc search routes/partials per
Contract §4).

---

#### 8I. Phase-1A O-UI Visual Contracts — SET (Architect, 2026-05-31)

Set **before** UI-Builder reaches them (pairs with Backend's O4/O5/O6 route contracts) so there is no
rework. **Copy the referenced existing pattern — do not invent.** Grounded in `settings/index.html` and
`vendors/detail.html`.

**O4 — Vendor Contacts card** (`vendor_contacts` CRUD + `is_primary`). A `card` on `vendors/detail.html`:
- Container: existing `card` + `card-header`/`card-title` ("Contacts"); header-right **`+ Add contact`**.
- Table: **explicit Tailwind padding + `divide-y divide-gray-100`** — **NOT `tbl-*`** (banned; the legacy
  `tbl` table at `vendors/detail.html:138` is the anti-pattern, do not copy it). Columns: Name · Role ·
  Phone · Email · Primary · row-actions.
- **`is_primary` chip:** a `badge-green` **"Primary"** pill on the primary row; backend guarantees **at most one
  primary** per vendor. **"Make primary" is a dedicated per-row action** (`POST …/contacts/{cid}/make-primary`) —
  the per-row **Edit** route deliberately ignores `is_primary` (no checkbox ambiguity), so the `is_primary`
  checkbox appears **only on the Add form**.
- **Per-row actions:** Edit (→ update route), Make primary (only when not primary), Delete (soft). Optional: a
  small chip per function flag (`is_sales/warranty/returns/accounting_contact`) beside `role`.
- **Submit mechanism — ✅ VERIFIED against shipped `VENDOR_CONTACTS_CONTRACT.md` @c17f2b6 (2026-05-31):** the
  routes are **plain form-POSTs that 303-redirect** to `/vendors/{id}?saved=1#contacts` (or `?error=…#contacts`)
  — **NOT** HTMX-partial swaps (there is no Backend-owned partial). Build the Add form + per-row forms as standard
  POSTs; show the `saved`/`error` flash near the card; the `#contacts` anchor returns focus. Iterate
  `vendor.contacts if c.is_active`; primary via `vendor.primary_contact`. Inline, no modal. **UI-Builder is
  cleared to build the card to this — the O4 seam is consistent.**

**O5 — Markup rules in Settings** (replace the hardcoded 30% at `product.py:202`). Copy the
`settings/index.html` **percent-field** verbatim (`settings/index.html:28-35`): `label` +
`flex items-center gap-2` + `<input type="number" step="0.1" min="0" class="w-32 rounded-lg border
border-gray-200 …">` + `<span class="text-sm text-gray-400">%</span>`.
- **Global default markup %** already IS this field (`default_markup_pct`) — keep as-is.
- **Per-category override:** a "Pricing Defaults" card sub-table, one row per category (name + the same
  percent-field + clear-to-inherit). Empty = inherit the global default.

**O6 — Card surcharge** (replace the read-only banner at `invoices/workspace.html:566`). Two surfaces, same
percent-field token as O5:
- **Per-customer default %:** a percent-field on the customer create/edit form, labeled "Card surcharge
  default %".
- **Per-invoice override:** a small editable percent-field by the invoice totals, **DRAFT-gated**
  (`{% if editable %}`), seeded from the customer default; blank = fall back to customer default → global
  `cc_surcharge_pct`. The existing fee-estimate text stays as helper copy.
- **Two-way** (can be cleared to 0) — fixes owner-test 1.9.e (the one-way 3% toggle). Writes the same field
  the totals math already reads (`invoice.cc_surcharge_pct`).

**Shared tokens (all three):** field = `rounded-lg border border-gray-200 px-3 py-2 text-sm focus:ring-2
focus:ring-brand-400`; section card = `rounded-xl bg-white border border-gray-100 shadow-sm p-6`; save =
`bg-brand-700 … hover:bg-brand-600`. **No new colors/classes.** §3/§4 apply; any modal/slide-over follows §3.

---

#### 8M. Chart.js Init Pattern — RATIFIED 2026-06-01

**The rule: `defer` the lib at `base.html`; guard every `new Chart(...)` call with `DOMContentLoaded`.**

`base.html` loads Chart.js with `defer` (L18). A deferred script executes after HTML parsing completes but
before `DOMContentLoaded` fires — so any inline `<script>` that calls `new Chart(...)` at parse time will
reference an undefined `Chart` constructor and silently no-op (or throw). The correct guard:

```html
<script>
  document.addEventListener('DOMContentLoaded', function () {
    var ctx = document.getElementById('my-chart').getContext('2d');
    new Chart(ctx, { /* config */ });
  });
</script>
```

Reference: `app/templates/dashboard.html:152` — "B-3: defer chart init until DOMContentLoaded — Chart.js
loads with `defer`". This is the only approved Chart.js init pattern; any bare `new Chart(...)` outside a
`DOMContentLoaded` listener is a bug.

**Offline / CDN risk gate.** Alpine, HTMX, and Chart.js all load from CDN (`cdn.jsdelivr.net` /
`unpkg.com`). A network outage or CDN failure renders the app non-functional. This is an acceptable risk for
a local-network ERP (owner's laptop + local WiFi), but must be resolved before any cloud/SaaS deployment:
self-host the three scripts under `/static/js/` and update the three `<script>` tags in `base.html`. Tag as
a Phase 1B go-live prerequisite for cloud hosting; for local 1A use, CDN remains acceptable.

**Print / totals parity (follow-up, NOT this round).** Ideally `quotes/print.html` and invoice print
templates render from the same `totals` engine dict (`app/invoice_totals.compute_invoice_totals(...)`) that
the workspace already uses — this guarantees identical subtotal / discount / tax / total across the screen,
print, and PDF, and means cores/fees parity is automatic rather than duplicated logic. Current state: print
templates recompute totals via Jinja2 arithmetic (e.g. `line.unit_price * line.qty`), which can drift from
the service layer when edge-cases (cores, NSF lines, restocking fees) are involved. Flag for the next print
pass — do not address in the current round.

---

#### 8N. D-10 — `product.cost` Semantic Decision (owner ruling required)

**The collision.** Two features write `product.cost` with incompatible intent, and they run back-to-back
on every PO receipt:

| Write site | When | Intent | Source |
|---|---|---|---|
| `inv_svc._apply_moving_average_cost(product, qty, unit_cost)` | PO receipt, R11 | **Moving weighted-average cost** (COGS for existing inventory) | `po_service.py:359-361` |
| `product_svc.compare_and_record_cost_change(…, new_cost=po_line.unit_cost)` → `_sync_cost_from_preferred()` → `product.cost = new_cost` | Same receipt, when PO cost ≠ stored vendor_source.vendor_cost | **Vendor quote price mirror** | `product_service.py:504-505`, `318-319` |

When these differ, the vendor-sync fires second and **overwrites the moving average**. The product model's own comments are contradictory: L71-72 says "mirrors preferred vendor source cost for quick access"; L73 says "R11 — moving weighted average cost; updated on every PO receipt." The D-10 test case that prompted R11 fails because of this overwrite.

**Options (owner must choose one):**

**Option A — `product.cost` = moving-average COGS (RECOMMENDED).** The receipt path writes the moving
average and owns `product.cost`. `_sync_cost_from_preferred` is narrowed to write only
`ProductVendorSource.vendor_cost` — it stops touching `product.cost`. When a user updates a vendor source
price manually (not from a receipt), the cached `product.cost` is deliberately NOT updated — the on-hand
inventory was bought at the old price. `selling_price` and margin displays continue reading `product.cost`
(they already do; behavior unchanged). Margin = `(sell_price - product.cost) / sell_price` = **COGS-based
margin on current inventory** — the financially correct number for a diesel parts shop. When stock = 0,
`product.cost` is the last receipt cost (standard moving-average convention).
*Fix scope:* remove the `product.cost = new_cost` write from `_sync_cost_from_preferred` and
`compare_and_record_cost_change` (keep `ProductVendorSource.vendor_cost` writes and cost-history rows).
Zero migration needed — the column is unchanged.

**Option A′ — add `product.avg_cost`, keep `product.cost` = vendor mirror.**
Add a dedicated column (`avg_cost`) for the moving average; leave `product.cost` as the vendor-price mirror
(the old intent from L71-72). Repoint margin display, `invoice_service.py:597`, and
`search_service.py:118/247` to read `avg_cost`. *Cost:* small migration + repoint all ~5 read-sites.
Cleanest long-term semantics (two distinct columns, unambiguous names).

**Option B — reject R11; `product.cost` = vendor mirror only.**
Remove `_apply_moving_average_cost` from the receipt path entirely. No moving average is maintained.
`product.cost` is always the vendor quote price. *Only valid if the owner explicitly overrides the D-10
design decision* ("product cost should update on receipt to reflect what we actually paid") — if D-10 was
intentional, Option B directly contradicts it.

**Architect recommendation: Option A.** Rationale: (1) D-10 was an explicit owner-tested behaviour —
respecting it rules out Option B. (2) Option A′ is cleaner but costs 5 read-site rewrites + a migration for
a net-zero behaviour change from the owner's perspective. (3) Option A is a two-line fix in
`_sync_cost_from_preferred` / `compare_and_record_cost_change`. (4) Vendor quote prices belong on
`ProductVendorSource.vendor_cost` — that column already exists and is the right home for them.

**Ruling: PENDING OWNER CONFIRMATION.** Document the chosen option here once decided so the receipt path
(`po_service.py`) and `_sync_cost_from_preferred` (`product_service.py`) stop fighting on every receipt.
Update the `product.py:71-73` model comment to match. Until ruled, **do not add any new code that reads
`product.cost` and assumes either semantic** — the column is ambiguous.

---

#### 8J. Activity Timeline — New UI Primitive (Timeline/Feed archetype) — RATIFIED 2026-05-31

Governs the customer **Activity** timeline per `ACTIVITY_LOG_CONTRACT.md` §4 (Backend owns the data + merge;
this is the UI pattern, ratified **before** UI-Builder builds it). The Timeline/Feed is a **third read
archetype** — **not** a §2 Operational List, **not** a §2A Queue Board.

**Archetype rules (what it is NOT):** no filter tabs, no bulk toolbar, no preview dock, no per-item queue
actions. It is a **chronological, newest-first, read-only feed** merging manual activities + system-sent
comms (`get_timeline`). The only write affordance is the shared **"+ Log Activity" modal** (Contract §4.1);
the feed never edits in place (both stores are append-only).

**Row format (one entry):** a left **rail** (type dot + vertical connector) then the body —
- **Timestamp** — relative ("2h ago"), absolute on hover; optional **date sub-headers** (Today / Yesterday /
  Mon DD) between days.
- **Type chip** — `activity_type` as icon + label: call · text · email (informational → `badge-blue`),
  counter_visit (in-person → `badge-gray` + store icon), note (`badge-gray`). The **icon** carries the type;
  the chip color stays in the §4 informational/neutral family so it never competes with the outcome.
- **Outcome chip** *(optional; activities only)* — `reached` → `badge-green`, `voicemail` → `badge-amber`,
  `no_answer` → `badge-gray`. A NOTE has no outcome.
- **Note** — the free text (`text-sm text-gray-700`).
- **Linked-doc chip** *(when `related_entity_type` set)* — `font-mono` doc number in a chip linking to the
  doc, e.g. `→ Q2026-0001` (`text-brand-700`), via the doc-identifier token.
- **Logged-by** — the signed-in user who logged it (`text-[11px] text-gray-400`).
- **System-sent comms** (`kind='comm'`) render in the SAME feed but **muted** (lighter rail dot + a small
  "sent" tag) to mark them read-only audit, distinct from manual activity.

**Empty state:** centered `.card` empty state — "No activity yet" + an explicit **"+ Log Activity"** CTA
(unlike a queue, logging IS the action here, so a CTA is correct).

**Reuse:** the doc-side activity panel (Contract §4.4, `activities_for_entity`) uses a **compact** variant of
this same row (no rail / no date headers) — same chips, same linked-doc + logged-by. Build the row as one
`{% include %}`-able partial so the Activity tab and the doc panel share it (don't fork two row layouts).

**Tokens:** existing `badge-*` chips, `divide-y divide-gray-100` between entries, `.card` container. **No new
colors/classes** — type stays informational/neutral, outcome carries green/amber. §3/§4 apply; the
"+ Log Activity" modal follows §3 (role=dialog, z-[60], rounded-2xl, @keydown.escape).

**This replaces** the old "Call Log" tab and the `/customers/{id}/communications` page (Contract §4). Ratified
**before** the build — UI-Builder copies this; no improvisation.

---

#### 8K. Linked-Documents Strip — New UI Primitive (workspace cross-doc nav) — BUILT 2026-06-01

A compact **"Linked"** chip strip rendered at the top of every Line-Item Workspace (Quote / SO / Invoice /
PO), directly under the §8B action bar. It surfaces the document graph — source quote, sales order, related
invoices, the PO sourcing a backorder — as clickable, status-toned chips so a user hops between linked
records without leaving the workspace. Owner-requested 2026-06-01 ("link these together… the PO should show
it's linked up; bills / invoices / quotes linked").

**Why a primitive (not per-screen):** the same need recurred on all four workspaces, and three prior one-off
treatments already existed (SO "Source Quote" field, SO "Related Invoices" card, Invoice "Sales Order" row).
The strip unifies them. The legacy field/card were left in place (additive); the strip is the glance layer.

- **Resolver (single source of truth):** `app/services/document_links.py` →
  `related_documents(db, entity) -> list[DocLink]`. Reads only relationships/columns that already exist —
  **no new schema**. The one link never stored on the PO side (PO → the SO it sources) is recovered by
  walking `SOLine.linked_po_line_id` backwards. `DocLink` carries `(relation, kind, number, url, tone,
  status_label, group)`; the resolver returns a coarse semantic **tone**, never CSS — colour stays in the
  template per §5.
- **Macro:** `app/templates/macros/linked_docs.html` → `linked_strip(links)`. Chip uses the §5 format
  (`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold` + dot). The tone→class map
  lives in the macro (one place → identical on every workspace). **All colours are §4-permitted families**
  (gray/blue/amber/green/red) — no new colour. Renders nothing when `links` is empty.
- **Wiring:** each workspace route sets `ctx["linked_documents"] = related_documents(db, entity)`; each
  `workspace.html` calls `{{ linked_strip(linked_documents|default([])) }}` at the top of `content`.
- **Tests:** `tests/test_document_links.py` (every direction + the PO→SO reverse + re-order-after-cancel).

**Companion (SO line lifecycle).** The backordered-line chip in `sales_orders/_lines_section.html` now tracks
the sourcing PO's progress — Backorder → **Ordering** (draft PO) → **On order · ETA** (sent) → **Receiving**
(partial) → **Ready** (reserved_stock) — and falls back to **Backorder + Re-order** if that PO is cancelled.
Owner ruling 2026-06-01: a line reads "On order" only once the PO is actually **sent** (a draft still reads
"Ordering"). `SalesOrderService.create_po_for_line` now treats a cancelled linked PO as re-orderable (drops
the stale link) so Re-order doesn't hit the "already linked" guard.

**Status:** BUILT 2026-06-01 (owner-directed). **✅ Architect governance PASS 2026-06-01.** Ruling:
- **Pattern:** PASS. A clickable navigation chip distinct from the read-only §5 `status_chip` span — correct
  to define as a new primitive rather than overloading the status chip.
- **Condition (previously noted):** `related_documents()` must be defensively safe (never raise; return `[]`
  on any error). The template's `|default([])` is a guard against `None` only — the service itself must
  be wrapped. Confirm before committing the full §8K router/service diff.
- **§7 list:** Add `linked_strip(links)` to the §7 primitives backlog as Primitive 7. Three-screen gate
  satisfied once Quote/SO/Invoice/PO all call it (currently 4 workspaces via the wired workspace diffs).
- **Do NOT re-pass or rebuild.**

---

#### 8L. After-Sale Service Section — start cores / warranty / RA from an invoice — BUILT 2026-06-01

Owner-requested 2026-06-01 ("start the core return process from the invoice screen as well start a warranty or
return from this screen"). The natural counterpart to §8K: where the Linked strip *navigates* to documents
already tied to the invoice, this *creates* the downstream service documents from it.

A new **"After-Sale Service" card** renders on the Invoice workspace for **finalized** invoices
(OPEN / PARTIAL / PAID; hidden on DRAFT/VOID), placed after the Payments panel. Two parts:
- **Core Returns** — one row per open `CoreCharge` on this invoice (resolved in `invoices.py`
  `_workspace_context` by joining `CoreCharge.invoice_line_id → InvoiceLine.invoice_id`), each with the inline
  qty/inspection/condition form copied from `cores/list.html` (the `awaiting_return` block) posting to the
  unchanged `POST /cores/{id}/return`. Copied, not extracted — §7 needs 3 identical users (this is the 2nd).
- **Warranty & Returns** — two buttons that open the existing global create slide-over (`createSlideOpen` /
  `#create-slide-content` in base.html) loading `/warranty/new?invoice_id=` and `/returns/new?invoice_id=`.

The picker GET routes now seed from the invoice: customer pre-selected, invoice number carried (warranty got a
hidden field — it had none before, so the POST's invoice link was previously unreachable from the UI), and the
invoice's PRODUCT lines pre-loaded as editable lines (seeded via a `<script type="application/json">` block +
`x-init` JSON.parse — never tojson-in-attribute, per the product-new footgun). Seeded products are unioned into
the `<select>` options so an inactive part still resolves. The create/money paths
(`WarrantyService.create_claim`, `RAService.create_ra`, `CoreService.record_customer_return`) are **unchanged** —
the POST handlers already accepted `invoice_number` + line arrays.

- **Files:** `app/routers/invoices.py` (`_workspace_context` → `invoice_cores`), `app/routers/warranty.py` +
  `app/routers/returns.py` (GET `…/new` invoice seeding), `app/templates/invoices/workspace.html` (the card),
  `app/templates/warranty/_new_picker.html` + `app/templates/returns/_new_picker.html` (seed + invoice field).
- **Tests:** `tests/test_invoice_after_sale_actions.py` (9 — GET seeding, `invoice_cores` join, card visibility
  by status, POST invoice-link).
- **Governance:** composes existing primitives only — no new CSS class, no new colour (orange = core is
  §4-permitted). The `bg-orange-50/30` opacity variant was added → `npm run build:css` re-run.

**Status:** BUILT 2026-06-01 (owner-directed). **✅ Architect governance PASS 2026-06-01.** Ruling:
- **Pattern:** PASS. Composes existing primitives only: the global create slide-over (already in base.html),
  the §5 status chip, the cores inline form (copied from `cores/list.html` — copying is correct at this
  stage; §7 extraction gate fires when a 3rd consumer appears, not before). No new CSS class; no new colour.
- **Placement:** After the Payments panel, finalized-only — correct. The card is invisible on DRAFT/VOID,
  preventing action on documents that aren't settled.
- **`bg-orange-50/30` opacity variant:** Permitted under §4 `orange-*` (core-charge semantic); opacity
  modifier does not change the colour family. `npm run build:css` re-run required before shipping.
- **Do NOT re-pass or rebuild.**

---

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

---

### 9. Functional Gate — Definition of Done

**Ratified 2026-05-30.**

**Problem this addresses:** The maturity table (§1) uses visual checklist conformance as the
definition of L2/L3 complete. The 2026-05-30 owner test pass showed that screens marked
"✅ L2/L3 complete" had broken core workflows (quotes not opening, can't add parts to SO/Invoice,
PO receiving failing). Visual conformance and functional correctness drifted apart because the
governance pass never required end-to-end proof.

**New rule:** A screen is not L2/L3 complete until it passes **both** the visual governance
checklist (§0) **and** the functional gate below. The governance pass confirms the checklist.
The functional gate confirms the work.

---

#### Functional Gate Checklist

Every screen must be verified by actually running the app and completing the real user job, not
just rendering the template. These tests must be run against a live server with real or realistic
data — not mocked, not inferred from code review.

**For all List screens (L2 gate):**
- [ ] Clicking every filter tab changes the list and shows the correct count in the tab
- [ ] Search returns correct results with partial input, dashes, mixed case (e.g., "ok1" finds "OK-1")
- [ ] Clicking a row opens the preview dock and loads correct data for that record
- [ ] Preview dock closes on X or second row click; does not interfere with tab/search
- [ ] Empty state renders correctly for: no records, filtered-tab-miss, and search-miss
- [ ] Bulk select: selecting rows shows toolbar; bulk action executes without error

**For Workspace screens (L3 gate):**
- [ ] **Add a line:** search for a real product (by SKU, partial name, and a variant with dashes/spaces), select it, confirm it appears in the line grid with correct price, qty, and line total
- [ ] **Edit inline:** change qty and price on an existing line; confirm line total and document total both update
- [ ] **Remove a line:** delete a line via the confirm modal; confirm it disappears and totals update
- [ ] **Save/autosave:** edit a header field, leave the screen, return; confirm the change persisted
- [ ] **Primary workflow action:** for Quote → Convert to Invoice or SO; for SO → Fulfill; for Invoice → Finalize; for PO → Receive items; confirm the action completes without error and status updates
- [ ] **No window.confirm():** all destructive actions use the Alpine modal — no browser native dialogs appear anywhere in the workflow

**For Queue Board screens (QB2 gate):**
- [ ] Metrics strip shows real numbers matching actual database state
- [ ] Group headers and item rows display for all active groups
- [ ] Each inline action (Receive, Match, Open, Print) navigates to the correct target
- [ ] Page-level empty state renders correctly when queue is empty

---

#### Smoke Test Reference

The owner has an end-to-end smoke test document that covers the full cross-workflow lifecycle
(TESTING_FEEDBACK.md in the repo root). The eight flows in Section 8 of that document are the
definitive proof:

| Flow | Screens touched |
|---|---|
| a | Vendor → Product → PO → Receive → inventory updated |
| b | Quote (in stock) → Invoice → Finalize → inventory decremented → payment recorded |
| c | Quote (out of stock) → SO → deposit → linked PO receive → fulfill → invoice |
| d | Invoice with core item → core charge → customer return → vendor return → credit |
| e | Overdue invoice → statement shows correct aging bucket |

A screen that participates in one of these flows is not L2/L3 complete until that flow runs
end-to-end without error.

---

#### Who Runs the Gate

The **owner** runs the functional gate against a live server. The **UI Architect** runs the
visual governance checklist. Both must pass before the Rollout Order entry is marked complete.
If the owner's test reveals a functional defect after a governance pass, the screen reverts to
"in progress" until re-tested — the governance check-mark does not survive a failing functional test.

---

#### Re-sequencing Rule (added 2026-05-30)

The core money path — **Quote → SO/Invoice → PO receive → Payment** — takes priority over
cosmetic L2 re-passes, §8 backlog items, and new screen rollouts. If any screen in that path
fails the functional gate, it becomes the highest-priority ticket for all lanes. No new screen
enters L2 work while the core path is broken.

**Current status (as of 2026-05-30 test pass):**

| Issue | Screen | Priority | Functional gate item |
|---|---|---|---|
| Quotes nav/workspace not opening | Quote List + Workspace | **P0** | b: Quote → Invoice flow |
| Can't add parts to Sales Order | SO Workspace | **P0** | b/c: SO line add |
| Can't add parts to Invoice | Invoice Workspace | **P0** | b: Invoice finalize flow |
| PO "can't receive" — ✅ automated-green @9d0ced2 | PO Workspace + Receiving Queue | ~~P1~~ resolved | a: PO → receive flow |
| Ctrl+K: "q2026" doesn't find quote | Global search | **P1** | Search normalization |
| New Quote from Customer detail empty | Customer Detail → Quote | **P1** | b: Quote creation |
| Card surcharge can't be unselected | Invoice Workspace | **P2** | d: payment edge case |
| Vendor: only one contact visible | Vendor Detail | **P2** | UX gap, not blocking |
| Product search: exact-SKU required | All workspaces | **P1** | §2C search normalization |

P0 items must be resolved and owner-re-tested before any §8 backlog work or new screen rollout.
P1 items are resolved in parallel with the P0 fix lane. P2 items are deferred until P0/P1 clear.
