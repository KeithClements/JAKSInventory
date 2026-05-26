# JAKS Inventory — UX & Navigation Requirements
*Compiled from owner interview — 2026-05-22*
*Status: ACTIVE — Review before touching navigation, modals, or any screen that creates records*

---

## ⚠️ Critical UX Directive

> **The single biggest workflow problem identified in testing:**
> Having to leave an active screen to create a missing supporting record.

This must be solved with **inline creation modals** before any other UI work proceeds.

---

## 1. The Workflow Interruption Problem

### What Happens Today (Broken)

```
Start building a quote
  → Realize customer doesn't exist
  → Leave quote screen
  → Go to Customers → Create customer
  → Return to quote
  → Realize product doesn't exist
  → Leave quote again
  → Go to Products → Create product
  → Realize vendor doesn't exist
  → Leave product screen
  → Go to Vendors → Create vendor
  → Return through multiple screens
  → Finally back at the quote — cold, interrupted, frustrated
```

**Result:** Friction, lost speed, broken workflow, too many context switches.
The app feels like disconnected modules, not one operational system.

---

## 2. Required Solution — Inline Creation (Modal / Slide-over)

### Rule
**The user must never be forced to leave an active workflow to create supporting data.**

### Required Inline Creations

| Active Screen | Can Create Inline |
|---|---|
| Quote (new or edit) | Customer, Product, Vendor |
| Invoice (new or edit) | Customer, Product |
| Product (new or edit) | Vendor |
| Purchase Order (new or edit) | Vendor, Product |
| Any screen with a dropdown | The record that dropdown draws from |

### Behavior After Inline Save
1. Modal closes
2. The newly created record is **auto-selected** in the originating field
3. User is back in context, exactly where they left off
4. No page reload. No lost data.

### Implementation Pattern
- Use a slide-over panel (right-side drawer) for create forms — keeps context visible
- Quick-create forms are **minimal** (required fields only, not the full detail form)
- Full edit available later from the record's own page
- Triggered by a `+` button next to any dropdown that selects a related record

### Example
```
Quote screen → Customer field → [ Select Customer ▼ ] [ + ]
                                                           ↑
                                         Click → Slide-over opens
                                                 Quick customer form
                                                 (name, phone, email only)
                                                 [Save] → closes, auto-selects
```

### Minimum Quick-Create Forms Required

**Quick Customer** (from Quote or Invoice screen):
- Company name *(required)*
- Contact name
- Phone
- Email
- [Save & Select]

**Quick Product** (from Quote, Invoice, or PO screen):
- SKU *(required)*
- Title
- Vendor (with its own + to create inline)
- Cost
- Markup % or Price Override
- Has core charge (toggle + amount)
- [Save & Select]

**Quick Vendor** (from Product or PO screen):
- Name *(required)*
- Phone
- Account number
- [Save & Select]

---

## 3. Navigation — Current State & Problem

### What Keith Said
> "Catalog and Products seem to be the same thing."

### Current Sidebar Structure (Built)
```
[Section] Catalog
  → Products
  → Vendors         ← Vendors do not belong under "Catalog"

[Section] Sales
  → Customers
  → Quotes
  → Invoices

[Section] Purchasing
  → Purchase Orders  ← Vendors should be here, not Catalog

[Section] Cores
  → Core Charges

[Section] System
  → Settings
```

### Problems Identified
1. **"Catalog" + "Products" is redundant.** Section header and link feel like the same thing.
2. **Vendors are under Catalog** — wrong. Vendors are a purchasing concept, not a catalog concept.
3. **Customers are under Sales** — functionally fine but separates them from their CRM context.

---

## 4. Navigation — Research Summary

Based on research into QuickBooks Online, Odoo, Zoho Books/Inventory, Fishbowl, and Epicor Eagle (aftermarket parts):

### Best Practice for B2B Parts Dealers
**Organize by transaction type, not by entity.**

The pattern used by Zoho (proven for this business size) and Epicor (industry-specific):
- **SELL** side: Customers → Quotes → Invoices
- **BUY** side: Vendors → Purchase Orders
- **INVENTORY**: Products (standalone, not under Catalog)
- **SPECIAL PROCESS**: Cores (unique to this business)
- **SYSTEM**: Settings

Customers and Vendors are **supporting reference data** — they belong as entry points to their respective sides of the business, not buried under unrelated sections.

### Key Insight from Research
> "Catalog" as a section name implies a browsable product directory — an eCommerce concept.
> For an operational ERP, "Inventory" or simply labeling the section clearly wins.

---

## 5. Navigation — LOCKED Structure (Session 4, 2026-05-23)

```
Dashboard
[Recently Viewed — last 5–10 records, collapsible]

─── SALES ──────────────────
  Customers
  Quotes
  Sales Orders              ← own item (active work queue)
  Invoices

─── PURCHASING ──────────────
  Vendors
  Purchase Orders

─── INVENTORY ──────────────
  Products

─── CORES ──────────────────
  Core Charges

─── REPORTS ─────────────────
  Reports

─── SYSTEM ─────────────────
  Settings
```

### Locked Decisions

| Decision | Locked Value | Reason |
|---|---|---|
| Section label — sell side | SALES | Traditional, matches how Keith thinks about his business |
| Section label — buy side | PURCHASING | Traditional, clear |
| Sales Orders placement | Own nav item under SALES | It is an active work queue, not just a child of quotes/customers |
| Cores | Own CORES section | Unique lifecycle; not an inventory concept |
| Reports | Own REPORTS section AND embedded in screens | Sidebar = management view; embedded = workflow context |
| Recently Viewed | In sidebar below Dashboard | Yes — Keith confirmed he will use this |
| "Sell / Buy" phrasing | Rejected | Keith confirmed traditional naming |

### Reports — Dual Access Pattern (LOCKED)
Reports appear in TWO places:

**Sidebar REPORTS section** (management/bookkeeping view):
- AR Aging
- Sales by Customer
- Sales by Product
- Inventory Valuation
- Open POs
- Core Charges Outstanding
- Overdue Invoices + Interest

**Embedded in screens** (workflow context):
- Customers screen → AR Aging, Customer Sales, Open Balance
- Products screen → Sales by Product, Inventory Valuation, Low Stock
- Vendors screen → Vendor Spend, Open POs, Vendor Credits

### Recently Viewed — Behavior
- Last 5–10 records opened (any type: customer, quote, invoice, product, PO)
- Shows: record type icon + record number/name + relative time ("2h ago")
- One click to reopen
- Persists across page loads (stored server-side per user session)
- Collapsible if sidebar space is tight

---

## 6. Dashboard — Current State

### Keith's Assessment
> "At the moment I think this is a good starting point."

### Current Widgets
- Today's Payments
- Open Quotes
- Open POs
- Open Cores
- Recent Invoices table
- Low Stock alerts
- Recent Call Logs

### Status: ✅ Acceptable for now. Revisit after core workflows are solid.

Future additions to consider (do not build yet):
- Quotes needing follow-up
- Overdue invoices / aged receivables
- Core charges past X days with no return
- Top customers this month

---

## 7. Implementation Priority

### Do These First (Blockers to Daily Use)

1. **Rebuild sidebar navigation** — use LOCKED SALES/PURCHASING/INVENTORY/CORES/REPORTS/SYSTEM structure
2. **Inline creation slide-over for Customer** — from Quote and Invoice screens
3. **Inline creation slide-over for Product** — from Quote, Invoice, and PO screens
4. **Inline creation slide-over for Vendor** — from Product and PO screens
5. **Global "Quick Log Call" slide-over** — accessible from every screen (new — Session 4)
6. **Quote pop-out window** — V1 priority (new — Session 4)
7. **Quote auto-save** — continuous background save with visible indicator (new — Session 4)

### Do These After Core Workflows Are Proven

8. Recently Viewed sidebar list
9. Inline category/subcategory creation
10. Global search bar (see `QUOTING_REQUIREMENTS.md`)
11. Dashboard additions
12. Sales Order pop-out and Invoice pop-out (Phase 2)

---

## 8. Inline Creation — Technical Notes

### Framework
- Use Alpine.js `x-data` + a `<div>` slide-over panel already rendered in `base.html`
- HTMX `hx-get` loads the quick-create form fragment into the panel
- On save, HTMX response updates the originating dropdown and closes the panel
- No full page reload at any point

### Slide-over Shell (add to base.html)
```html
<!-- Global slide-over panel — populated by HTMX -->
<div id="slideover-backdrop" class="fixed inset-0 z-50 bg-black/40 hidden" onclick="closeSlideover()"></div>
<div id="slideover-panel" class="fixed right-0 top-0 h-full w-96 bg-white shadow-2xl z-50 translate-x-full transition-transform duration-200 overflow-y-auto">
  <div id="slideover-content"></div>
</div>
```

### Route Pattern
```
GET  /quick-create/customer     → returns form fragment HTML
POST /quick-create/customer     → saves, returns <option> tag to inject into dropdown
GET  /quick-create/product      → returns form fragment HTML
POST /quick-create/product      → saves, returns <option> tag
GET  /quick-create/vendor       → returns form fragment HTML
POST /quick-create/vendor       → saves, returns <option> tag
```

---

---

## 9. Global "Quick Log Call" Slide-over (LOCKED — Session 4)

### Problem
While on the Vendors screen, Products screen, PO screen, or anywhere in the app — a customer calls.
The user must be able to log the call WITHOUT leaving their current workflow.

### Behavior
- Available from every screen via a persistent action (header button or keyboard shortcut)
- Opens a right-side slide-over panel
- User does NOT navigate away from their current screen

### Slide-over Contents
1. Customer search (type to search by name or phone)
2. Select customer
3. Call notes (free text)
4. Optional links: quote, invoice, research item, product
5. Call type: Inbound / Outbound
6. Call outcome: Quoted / Order Placed / Follow-Up Needed / No Answer / Info Only / Other
7. [Save & Close]

### After Save
- Slide-over closes
- User is exactly where they were
- Call log entry appears in customer's call history

### Implementation
- Persistent header button: "📞 Log Call" (or keyboard shortcut)
- HTMX loads slide-over fragment
- POST to `/quick-create/call-log`
- Returns success toast: "Call logged for Mike's Diesel"

---

## 10. Recently Viewed (LOCKED — Session 4)

### Behavior
- Shows last 5–10 records opened by the current user
- Record types: Customer, Quote, Invoice, Sales Order, Purchase Order, Product, Vendor
- Display: [icon] [record type] — [record ID / name] — [relative time]
- Example: 🧾 Quote — Q-2026-0051 Mike's Diesel — 2h ago
- One click to reopen
- Persisted server-side in user session (survives page reload)
- Positioned in sidebar below Dashboard link
- Collapsible

---

*This document governs all UX navigation and inline creation decisions.*
*Update it as new friction points are identified.*
