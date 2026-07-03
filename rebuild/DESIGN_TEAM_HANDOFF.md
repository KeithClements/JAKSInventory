# JAKS Inventory ERP — Design Team Handoff

*Prepared 2026-06-11 for the design team. Everything a designer needs to know before
producing mockups for this system — especially the mobile-friendly pass.*

---

## 1. What this product is

JAKS Inventory is a **diesel-parts ERP** used daily by a 2-person shop (owner + bookkeeper)
to run the whole business: quoting, sales orders, invoicing, payments, purchasing,
receiving, core charges, returns, warranty claims, and QuickBooks sync. It manages a
catalog of ~13,000 parts.

It is a **work tool, not a marketing site**. Users live in it for hours: speed of reading,
scannability of tables, and "can I make the next decision without opening the record" beat
visual flourish every time. Information density is a feature.

**Users:** the owner (on the road much of the day — hence the mobile pass) and a
bookkeeper. Both are experts in the business, not in software.

---

## 2. The technical reality you are designing for

This is the most important section. The implementation stack constrains what designs can
be built cheaply vs. expensively.

| Layer | Technology | What it means for design |
|---|---|---|
| Pages | **Server-rendered HTML** (FastAPI + Jinja2 templates) | Every screen is a full page from the server. No client-side app, no routing transitions, no skeleton-loader patterns. Page-to-page navigation is a real browser navigation. |
| Styling | **Tailwind CSS** (compiled, utility classes) | All styling maps to Tailwind's spacing/type/color scale. Designs that snap to that scale (4px spacing grid, standard type sizes) implement 1:1. Arbitrary one-off values are possible but discouraged. |
| Interactivity | **Alpine.js** (small inline behaviors) | Dropdowns, modals, slide-overs, row selection, tabs. Lightweight — think "sprinkles of behavior", not rich app interactions. |
| Partial updates | **HTMX** (swap fragments without reload) | Line-item edits, preview panels, totals refresh in place. Inline editing patterns ARE supported. |
| NOT in the stack | React / Vue / native apps | Do not design patterns that assume an SPA: no global client state, no optimistic UI, no animated route transitions, no offline mode, no native gestures (swipe-to-delete etc.). |

**Mobile is the same web app, responsive — not a separate native app.** The phone opens
the same URL over a VPN. Design responsive layouts of the existing screens, not a new product.

**Browser targets:** modern Chrome/Edge/Safari. Desktop is tested at **1280px and 1920px**;
the mobile pass targets roughly **390px (phone)** and **768px (tablet)** widths.

---

## 3. Design tokens (already locked in code)

### 3.1 Brand color — army olive

The single primary hue. **Do not introduce a new primary color.**

| Token | Hex | Use |
|---|---|---|
| brand-50 | `#f4f5e9` | Tinted backgrounds (selected rows, bulk toolbar) |
| brand-100 | `#e6e9cc` | Tinted borders |
| brand-200 | `#cdd399` | — |
| brand-300 | `#b0be71` | Selected-row ring |
| brand-400 | `#94a84e` | Focus rings |
| brand-500 | `#788436` | — |
| brand-600 | `#5e6928` | Button hover |
| **brand-700** | **`#4b5320`** | **PRIMARY — buttons, active tabs, brand identity, links on identifiers** |
| brand-800 | `#363c18` | Pressed states |
| brand-900 | `#232710` | — |

### 3.2 Semantic colors — meanings are LOCKED

Color carries operational meaning everywhere in the app. A user has learned that red = money
problem. **Never use these hues decoratively, and never use a different hue for these meanings.**

| Color family | Always means | Never used for |
|---|---|---|
| Red | Financial overdue, critical, error, out-of-stock, discontinued | Decoration, success |
| Amber | Warning, operational overdue (POs/follow-ups), low stock, partial | Errors |
| Green | Healthy, paid, success, in-stock, active | Anything not-positive |
| Blue / Sky | Informational, on-order, activity, links | Errors, warnings |
| Purple | Vendor-related, waiting, serialized, special workflow | General accents |
| Orange | Core charges and special-cost items ONLY (domain-specific) | General warnings (that's amber) |
| Gray / Slate | Inactive, archived, neutral, metadata | Primary actions |

All colors are standard Tailwind palette values (e.g. red-400 = `#f87171`, amber-400 =
`#fbbf24`, green-500 = `#22c55e`) — use Tailwind's published palette in your design tool.

### 3.3 Typography

| Use | Spec |
|---|---|
| UI font | **Inter** (fallback: system-ui, sans-serif) — the only UI typeface |
| Body/table text | 14px (`text-sm`) |
| Metadata / captions | 12px (`text-xs`), gray-400 |
| Section labels inside panels | 10px, bold, uppercase, wide tracking, gray-400 |
| Identifiers (SKU, Invoice #, PO #, Quote #) | **Monospace**, 14px bold, brand-700, underline on hover — these are always links. This mono-identifier convention is a core visual signature of the app. |
| Numbers in metrics | Bold, tabular figures (`tabular-nums`) so columns align |

### 3.4 Shape, depth, spacing

| Token | Value |
|---|---|
| Cards | White, 1px gray-100 border, 12px radius (`rounded-xl`), subtle shadow |
| Modals / slide-overs | 16px radius (`rounded-2xl`), heavy shadow (`shadow-2xl`) |
| Buttons & chips | 8px radius (`rounded-lg`) |
| Table cell padding | 16px × 16px (`px-4 py-4`); queue rows may tighten to 10px vertical |
| Spacing grid | Tailwind 4px scale — design on a 4px grid |
| Page background | Light gray; content sits in white cards |
| Sidebar | Fixed left, **256px wide**, dark (stone family), full-height — desktop only behavior; the mobile pass must propose what happens to it below 1024px (see §6) |

---

## 4. The three screen archetypes

Every screen in the app belongs to one of three ratified archetypes. Designs must respect
the archetype — a list is not a dashboard, a queue is not a list.

### A. Operational List (e.g. Products, Invoices, Customers)

Anatomy, top to bottom:
1. **Page header** — title + record count, primary "New X" button top-right
2. **Search field** (icon-prefixed) + **filter tabs** — pill tabs with count badges
   (active = brand-700 fill, white text)
3. **Bulk-action toolbar** — appears only when rows are checkbox-selected
4. **The table** — dense rows with:
   - **Left-edge status stripe** (4px colored border on each row: red/amber/blue/transparent) —
     ⚠️ **LOCKED feature: no redesign may remove these stripes or change their color meanings**
   - Always-visible **status chips** (small colored dot + label) — never hover-to-reveal
   - Monospace identifier links
   - Hover highlight, row-click opens the preview dock (does NOT navigate)
5. **Preview dock** — a panel fixed to the bottom of the viewport (~260px tall, 3–4 column
   summary grid + action buttons). Clicking a row peeks at the record without leaving the list.
6. **Empty states** — three distinct cases: no records yet (with CTA) / no tab match / no
   search match

### B. Queue Board (e.g. Receiving Queue, Cores, Warranty)

Work-to-be-done surfaces. Items grouped by context (vendor, lifecycle stage), processed
top-to-bottom:
1. **Metrics strip** — count cards at top (e.g. "4 awaiting return", red/amber when non-zero)
2. **Group divider rows** — vendor or stage headers separating chunks of work
3. **Always-visible inline action buttons** on every row (max 2–3) — queues never hide actions
4. No tabs, no bulk select, no preview dock — by design

### C. Line-Item Workspace (Quote / SO / Invoice / PO editing)

Where money is made. One document open for editing:
1. **Document header card** — customer, dates, status chip, document-level fields
2. **Add-line search** — type 2+ chars, pick a part, it's added instantly to the grid
3. **Editable line grid** — qty/price/discount edited inline, totals refresh live
4. **Totals bar** — pinned at the bottom of the line section
5. **Sticky save bar** — manual Save button + an honest state pill
   (amber "Unsaved changes" → pulsing "Saving…" → green "Saved" → red "Save failed").
   A permanently-green "saved" indicator is banned — the pill must reflect true state.
6. **Customer-context strip** — terms, AR balance, cores owed (sales-side docs)
7. **Workflow action bar** — status-appropriate actions; primary action rightmost,
   destructive actions are ghost-red and always confirm via a styled modal
   (never a browser `confirm()` popup)

### Shared overlay system

| Overlay | Behavior |
|---|---|
| Slide-over (right edge) | Quick-create forms (new customer/product/vendor mid-flow), ~512px wide |
| Modal (centered) | Confirmations and small data entry, max ~512–672px |
| Preview dock (bottom) | List-row peek, desktop-anchored to the right of the sidebar |
| Toasts | Top-right stack, past-tense copy ("Quote created"), auto-dismiss 4s |
| Esc | Closes the topmost overlay; Ctrl+K opens global search |

---

## 5. Decisions that are LOCKED (do not redesign)

These survived owner testing and governance rulings. Proposals that change them will be rejected:

1. **Left-edge status stripes** on list rows + their color semantics (red/amber/blue) —
   explicitly locked against "modernization."
2. **Semantic color table** (§3.2) — meanings cannot shift; no new badge colors without approval.
3. **One primary hue** — army olive brand-700. No second accent color.
4. **Status chips always visible** — nothing operational may be hover-revealed.
5. **Save Standard v2** — manual Save + honest dirty-state pill + sticky save bar on every
   editing workspace.
6. **Density** — these are operator tables, not consumer cards. Do not air them out into
   spacious card lists on desktop.
7. **Monospace identifiers as links** — SKUs and document numbers keep the mono/bold/brand look.
8. **CC-surcharge display** — shown as an informational note below the invoice total, never
   added into the total. (Legal/accounting decision, not a style one.)

---

## 6. The actual job: the mobile-friendly pass

Designs are needed for **~390px phone width** (primary) and **768px tablet** (secondary).
Desktop (1280px+) must remain visually unchanged — the implementation adds responsive
behavior below those widths only.

### Usage model on mobile

The owner is **on the road**, on a phone, doing **lookups and light actions** — not heavy
data entry. Phone = read + quick act; laptop/desktop = build documents.

Typical roadside tasks, in priority order:
1. Look up a part — price, quantity on hand, vendor cost
2. Look up a customer — balance, overdue status, phone number (tap to call)
3. Check an invoice / quote / SO status
4. Dashboard glance — today's numbers
5. Light actions: record a simple payment, add a customer note, mark a follow-up

### Screens in scope (in order)

1. **Dashboard** — KPI tiles, revenue chart, top customers, follow-ups
2. **Customers list + customer detail** — detail page has a Timeline-first tab layout
3. **Products list + product detail** — the 13k-part catalog; search is the entry point
4. **Invoices list + invoice view** (read-oriented)
5. **Quotes list** (read-oriented)
6. **Global navigation + global search (Ctrl+K equivalent on mobile)**

Out of scope for this pass: the editing workspaces (quote/SO/invoice/PO builders), Smart
Import, receiving, three-way match, settings. They stay desktop.

### Design problems that need YOUR answers

These are the known desktop-only patterns with no mobile answer yet — this is the heart
of the brief:

1. **The sidebar.** Fixed 256px left rail on desktop. Propose the small-screen pattern
   (hamburger + drawer? bottom tab bar for the top 4–5 destinations? both?). Note the app
   has ~15 nav destinations; roadside use needs ~6.
2. **Dense tables on a 390px screen.** Products list has 8+ columns. Options to weigh:
   priority columns + horizontal scroll, collapsing each row into a 2-line card on phones,
   or a hybrid. The status stripe and status chip must survive whatever you choose (locked).
3. **The preview dock.** Bottom-anchored panel positioned to the right of the sidebar —
   meaningless on phones. Propose its mobile equivalent (full-width bottom sheet is the
   natural candidate) or replace row-tap behavior with direct navigation on phones.
4. **Filter tabs + search.** Tab pills + search currently share one toolbar row. They won't
   fit at 390px — propose stacking/scrolling behavior.
5. **Touch targets.** Current rows/buttons are mouse-scaled. Propose minimum hit areas
   (44px) where they matter; row height may grow on mobile (density rule §5.6 applies to
   desktop, not phone).
6. **Phone-native affordances.** Customer phone numbers should be tap-to-call links;
   consider where `tel:` / `mailto:` links elevate the roadside experience.

### What to deliver

- **Figma (or equivalent) frames at 390px and 768px** for the in-scope screens, plus one
  1280px frame per screen confirming "desktop unchanged."
- **Use the tokens in §3** — Tailwind palette, Inter, 4px grid, the radii table. A design
  that uses the Tailwind scale ships fast; arbitrary values create friction.
- **A short pattern sheet** for the new responsive primitives you introduce (nav drawer,
  bottom sheet, card-collapsed row) so they can be ratified once and reused everywhere.
- **Annotations** for anything interactive (what taps do, what scrolls, breakpoint behavior).

### Process / governance

The app has a UI governance rule: **new patterns, colors, and components are proposed,
ratified once, then reused** — never invented per-screen. Your pattern sheet will go
through that review. Expect a punch-list style response (specific items, not vague
feedback). Once ratified, your patterns become the standard for the rest of the app.

---

## 7. Reference materials

| What | Where |
|---|---|
| Live app | Provided URL + login (ask the owner) — the best reference is clicking around the real thing |
| Full design-system spec | `JAKS_UI_Change_Plan.md` (repo root) — the canonical, exhaustive document |
| Exact CSS class tokens | `.claude/skills/jaks-ui-governance/references/class-tokens.md` |
| Reference list screen | Products List (`/products`) — the official archetype implementation |
| Reference queue screen | Receiving Queue (`/purchase-orders/receiving`) |
| Reference workspace | Quote Workspace (`/quotes/{id}`) |
| Brand palette source | `tailwind.config.js` (repo root) |

**Questions:** anything not covered here or in the change plan — ask before designing
around it. A 5-minute question beats a rejected mockup.
