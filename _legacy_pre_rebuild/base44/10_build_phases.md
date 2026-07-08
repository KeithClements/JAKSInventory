# Build Phases (Recommended Order)

Building everything at once is a recipe for a broken release. Below is the
recommended phasing for a Base44 rebuild. Each phase ships *something
usable* on its own.

---

## Phase 1 — Foundation (Catalog + Customers + Auth)

**Outcome:** Operator can sign in, add customers, add products by hand.

- Authentication / users / roles / PIN overrides.
- Settings → Company, Users, Documents (numbering prefixes).
- Customers Hub + Customer 360 (Profile, Contacts, Addresses tabs).
- Products screen + Product Workbench (all 13 sections).
- Inventory → Audit (read-only, populated from any qty change).
- Bulk Import (Products, Customers).
- Dashboard (basic KPIs).

Deliverable: catalog of products + customer file, with audit trail.

---

## Phase 2 — Quotes

**Outcome:** Sales can take quotes end-to-end.

- Quotes screen + quote dialog (lines, totals, multi-options).
- Part Finder (local only — no live scraping yet).
- Suggested sells data (manual; xref tab in Workbench).
- Lost Sales screen.
- Print/email/SMS quote PDFs.
- Settings → Tax, Shipping.

Deliverable: quoting + lost-sale capture.

---

## Phase 3 — Sales Orders, Invoices, Payments

**Outcome:** Quote → SO → Invoice → Payment cycle works.

- Sales Orders screen + SO dialog + pick / pack / ship.
- Invoices screen + invoice dialog + payments.
- AR Aging.
- Customer credits ledger.
- Customer 360 → Orders / AR / Credits tabs.
- Dashboard tiles for today's revenue / payments.

Deliverable: full sales cycle without purchasing.

---

## Phase 4 — Purchasing & Receiving

**Outcome:** POs to vendors and stocking inventory.

- Vendors screen + vendor dialog.
- Purchase Orders screen + PO dialog.
- PO Receipts (single + partial).
- Low Stock screen + Create POs wizard.
- Drop-ship linkage (PO ←→ SO).
- Adjustments screen + bulk adjust.
- Settings → Documents → PO templates.

Deliverable: full inventory loop. App can now run as a closed system.

---

## Phase 5 — Cores

**Outcome:** Core deposits, returns, and vendor RGAs.

- Core charge on products (Workbench section already exists).
- Customer cores tracking (auto-created on invoice).
- Customer Cores screen + return flow.
- Vendor Cores Board.
- Vendor Returns + RGA dialog.
- Processing Dashboard.
- Aging buckets + reminders.

Deliverable: cores work end-to-end. Often the make-or-break feature for diesel.

---

## Phase 6 — Pricing v2 & Tiers

**Outcome:** Customer-tier × cost-band × category pricing.

- Tiered Pricing grid editor.
- Price Lists generation.
- Pricing Maintenance (mass markup, MAP audit).
- Per-product qty tiers (already in Workbench Pricing section).
- Manufacturer / vendor → category mapping.

Deliverable: tier-driven prices instead of flat list prices.

---

## Phase 7 — Scrapers

**Outcome:** Live competitor and supplier price intel.

- PAI sidecar service + integration in Part Finder + Workbench Refresh.
- HHP sidecar + 5-phase pipeline + HHP Scraper screen.
- ATL sidecar.
- Scraper Admin screen.
- Nightly stale-cost / price-watch jobs.

Deliverable: data-driven pricing decisions.

---

## Phase 8 — Accounting & Channel & Marketing

**Outcome:** Integrations + outreach.

- **QBO** sync queue + worker + Sync Center + reconciliation. Run in **mock**
  for a week before switching to **read_write**.
- **Shopify** product publish + inventory + order pull.
- **SMS** queue + inbox + templates.
- Campaigns + Automation Rules.
- Reports library.
- Margin screen.

Deliverable: full ERP behavior with external systems.

---

## Cross-cutting (every phase)

- Backups
- Permissions matrix per screen
- Cross-window signals
- Print/PDF templates
- Telemetry / error reporting

---

## Anti-patterns to avoid

- Don't build all screens at 70% completeness. Ship one workflow to 100%
  before starting the next.
- Don't postpone the audit table. Add `inventory_audit` from Phase 1.
- Don't enable QBO write mode without one full week in mock + reconciliation.
- Don't enable Shopify auto-publish without testing the unpublish path
  (zero-qty → draft).
