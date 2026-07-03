# Axle ERP — Technical Brief for the Design Team

*What the engineering stack can and cannot do — read this before designing.*
*The visual identity (colors, logo, name: **Axle**) is owned by the design team; this
document only covers the technical platform your designs will be built on.*

---

## 1. The one-paragraph technical summary

Axle is a **server-rendered web application**. Every screen is an HTML page generated on
the server and styled with **Tailwind CSS**. Small pieces of interactivity (menus, modals,
inline edits, live totals) are added with two lightweight libraries (**Alpine.js** and
**HTMX**). There is **no React, no Vue, no native mobile app** — it runs in a normal web
browser on desktop, laptop, tablet, and phone. Think "extremely polished website," not
"app-store app."

---

## 2. What this means for your designs — the capability table

### ✅ Cheap and fully supported (design freely)

| Capability | Notes |
|---|---|
| Any color scheme | Colors live in ONE config file. Hand over hex values and the whole app re-themes. Your Axle palette is a drop-in. |
| Any web font | Currently Inter; any Google/licensed web font can be swapped in globally. |
| Responsive layouts | Standard CSS breakpoints: 640 / 768 / 1024 / 1280px. Stacking, hiding, reflowing per breakpoint is normal work. |
| Tables, cards, lists, grids | The bread and butter. Dense data tables included. |
| Tabs, dropdowns, accordions, tooltips | Standard. |
| Modals, slide-in panels, bottom sheets | Standard. Esc-to-close, backdrop dimming all exist. |
| Inline editing | Click a value, edit it in place, totals update live — already used heavily. |
| Search-as-you-type | Type 2+ characters, results appear — already used for part/customer search. |
| Toast notifications | Pop-up confirmations top-right, auto-dismiss. |
| Status chips, badges, colored indicators | Everywhere in the app today. |
| Empty states, loading spinners | Standard. |
| Charts | Chart.js is loaded — line, bar, doughnut etc. Static/refresh-on-load, not streaming. |
| Icons | Inline SVG (Heroicons-style). Any SVG icon set works. |
| Print layouts | Invoices/quotes/statements print via the browser. Print-specific design (logo header, footer, terms) is supported and matters to this business. |
| Tap-to-call / tap-to-email | `tel:` and `mailto:` links — cheap, great for mobile. |
| Keyboard shortcuts | Ctrl+K global search exists; more are possible. |

### ⚠️ Possible but costs real engineering time (ask before designing around it)

| Capability | Why it's expensive |
|---|---|
| Drag-and-drop (kanban boards, reorder by dragging) | Needs a new library + careful server sync. Doable, not free. |
| Animated page-to-page transitions | Pages are full browser navigations; smooth cross-page animation fights the architecture. Within-page animation is fine. |
| Infinite scroll | Lists paginate (100 rows/page) server-side. Infinite scroll is possible via HTMX but pagination is the native pattern. |
| Camera / barcode scanning | Possible via browser APIs, planned for a later phase — don't block core flows on it. |
| Photo-heavy interfaces | Product images exist, but this is a parts catalog with sparse imagery — don't design layouts that fall apart without photos. |
| Highly custom form controls (fancy sliders, dial inputs) | Each one is hand-built. Native-ish controls are instant. |

### ❌ Not supported by the architecture (do not design these)

| Pattern | Why |
|---|---|
| Native app patterns | No app store, no swipe gestures (swipe-to-delete/archive), no haptics, no native share sheets. |
| Offline mode | The app needs a connection to its server. No offline-first design. |
| Push notifications to a locked phone | In-app toasts/badges yes; OS-level push no. |
| Real-time multi-user presence | No live cursors, no "Jane is typing…", no live-updating dashboards without refresh. |
| Optimistic UI | Changes save to the server then reflect back. Don't design flows that assume instant local state. |
| Heavy scroll-driven animation / parallax | Wrong tool for an operational ERP, and the stack doesn't support it well. |
| Client-side mega-tables | Don't design a view that requires all 13,000 products rendered at once — data comes in pages. |

---

## 3. Platform facts to design around

- **Screen targets:** desktop 1280px and 1920px (primary work environment), tablet 768px,
  phone ~390px. Phone use is **read-and-act-light** (lookups on the road), desktop is where
  documents get built.
- **Navigation model:** a persistent left sidebar (currently 256px, ~15 destinations) on
  desktop. The mobile navigation pattern is yours to propose (drawer, bottom bar, etc.).
- **Page loads are real:** moving between screens is a normal browser navigation
  (fast — local server — but it is a page load, not an in-app transition).
- **Partial updates are real too:** within a screen, line edits / totals / panels refresh
  in place without a full reload. Design inline workflows with confidence.
- **One overlay system:** centered modals (confirmations), right slide-overs (quick-create
  forms), and a bottom preview panel (peek at a row). New overlay types are possible but
  should be proposed once and reused everywhere.
- **Forms post to the server:** validation errors come back from the server and render on
  the page. Inline as-you-type validation exists only where hand-built — design it where it
  matters (not on every field).
- **Authentication:** simple login page; two roles (admin, bookkeeper). Role can hide/show
  features — e.g. margin data is admin-visible only. You can use "role" in your designs.
- **Printing is a feature:** customers receive printed/PDF quotes, invoices, statements.
  These print views are plain, brandable documents — design them as part of the identity.

---

## 4. How to hand designs over so they build fast

1. **Design on a 4px spacing grid.** The styling system (Tailwind) thinks in 4px steps
   (4, 8, 12, 16, 24, 32…). Designs that snap to it translate 1:1; arbitrary values
   (e.g. 13px padding) create friction.
2. **Use a small type scale.** Tailwind's defaults: 10, 12, 14, 16, 18, 20, 24, 30px.
   Body/table text in the app today is 14px.
3. **Deliver colors as hex + role.** e.g. "Primary action: #1F4FD8 · Success: #16A34A".
   If you provide a 10-step ramp (50→900) per color, theming is literally copy-paste.
   If you only provide the main hexes, engineering will generate the ramps — fine too.
4. **Standard radii and shadows.** Pick from: 8px / 12px / 16px radius; subtle / medium /
   heavy shadow. One choice per component type, used consistently.
5. **Annotate interactions.** For anything clickable: what happens (navigate? open panel?
   inline edit?). For responsive frames: what collapses, what hides, what scrolls.
6. **Frames per screen:** 390px and 768px (the new responsive work) + 1280px (desktop).
7. **Component sheet over per-screen invention.** Define each primitive once (button set,
   chip set, table row, card, modal, nav) and reuse. The engineering side builds primitives
   once and stamps them everywhere — per-screen one-offs are what make builds slow.

### The rebrand specifically (JAKS → Axle)

| Change | Effort |
|---|---|
| New color palette | Trivial — one config file. Provide hexes. |
| New font | Trivial — global swap. |
| New logo | Easy — appears in sidebar, login page, and printed documents. Provide SVG. |
| Name change to "Axle" | Easy — page titles, sidebar, login, printed docs, emails. |
| New component look (buttons, chips, tables restyled) | Moderate — restyling shared primitives propagates app-wide; brand-new component *behaviors* cost more (see §2). |
| Information architecture changes (merging/splitting screens) | Real engineering work — propose, don't assume. |

---

## 5. One business-critical constraint that survives any rebrand

Color in this app **carries operational meaning** for daily users: the current system uses
red = overdue/critical money issues, amber = warnings, green = healthy/paid. Whatever
palette you choose, **preserve a clear three-way semantic distinction** (problem / warning /
good) that is instantly scannable in a table of 100 rows — and don't assign those alarm
roles to brand/decorative colors. The exact hues are yours; the *system* of meaning must
survive.

Similarly: status indicators on rows (colored edge stripes + labeled chips) are functional
equipment the owner relies on, not decoration. Restyle them freely; don't remove them.

---

## 6. Questions welcome

If a design idea isn't clearly covered by §2's tables, ask **before** building mockups
around it. "Can the platform do X?" is a five-minute answer; a mockup designed around an
unsupported pattern is a week of rework.
