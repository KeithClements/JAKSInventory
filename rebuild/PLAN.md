# JAKS Inventory — Rebuild Plan
*Drafted: 2026-05-21 | Status: Draft*

---

## 1. Business Context

**JAKS** is a small heavy-duty diesel parts dealership — currently local, actively growing toward online sales via Shopify and eBay. Two users: **Keith** (day-to-day operations, sales, purchasing) and **his wife** (bookkeeping). The existing application became unstable after a large UI overhaul; the goal is a clean rebuild that is reliable first, feature-rich second.

---

## 2. What Went Wrong With the Current App

- PySide6 desktop app grew to 100+ screens with no stable core
- Fix one screen → break another (no integration tests, no clear data layer)
- Many screens were "someday" builds that were never functional
- UI complexity outpaced actual business needs
- QBO inventory sync was attempted but is broken and should not be rebuilt

---

## 3. Architecture Decision

### Chosen: Local Web App (FastAPI + Browser UI)

**Rationale:**
- Database stays on Keith's local machine (SQLite — single file, easy backup)
- Any browser (desktop or mobile) connects over the local WiFi network
- Wife can open it from her machine; Keith can log a call from his phone at a job site
- No Qt install, no executable distribution, no DLL conflicts
- Python backend keeps all the scraper code already written
- Future: add Tailscale or a VPN for remote access without exposing to internet

**Stack:**
| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | Async, fast, keeps existing Python code |
| Database | SQLite (local file) | Zero config, single-file backup, reliable |
| Frontend | HTMX + Alpine.js + Jinja2 | No build step, mobile-friendly, easy to maintain |
| Styling | Tailwind CSS (CDN) | Clean UI, responsive, no toolchain |
| Scrapers | Existing Python scrapers | Reuse HHP, PAI, ATL code |
| QBO | qbo-python SDK | Push invoices, payments, vendor bills only |
| Shopify | shopify-python-api | Push products, sync sold inventory |
| eBay | eBay REST API | Phase 2 |
| Tax | TaxJar API | Phase 2 (when shipping starts) |

---

## 4. What We Are Building (Modules)

### Module 1 — Products & Inventory *(Phase 1 — Foundation)*
The heart of the system. Everything else depends on this being right.

- Product entry form: SKU, title, description, brand, manufacturer, category, cost, selling price, weight
- Per-customer discount % override (simple, not tiered — tiers can come later)
- Stock quantity tracking (on-hand, committed, available)
- Cost sources: PAI cost preferred → HHP cost fallback
- Markup % setting (global, overridable per product)
- Supplier/vendor linkage (who we buy this from)
- Core charge flag per product (yes/no + dollar amount)
- Image management (local storage, link to Shopify/eBay)
- **Scraper integration:** pull data from PAI, HHP, ATL in-app with one click — populates cost, images, cross-references, description

### Module 2 — Customers & CRM *(Phase 1)*
Lightweight but complete enough to be genuinely useful.

- Customer list: name, company, phone, email, address, tax-exempt flag
- Per-customer discount % (feeds into quoting automatically)
- **Call log:** date + freeform notes under each customer — accessible from phone
- Purchase history (auto-populated from invoices)
- Customer 360 view: contact info + log + open invoices + core balance at a glance

### Module 3 — Quotes → Invoices *(Phase 1 — Core Revenue)*
The daily driver. Must be bulletproof.

- Quote builder: search product by part number, description, or cross-reference
- Add line items: product, qty, unit price (pre-filled from product, editable), core charge line (auto-added if product has core)
- Per-customer discount auto-applied, always overridable
- Convert quote → invoice (preserves history)
- Invoice states: Draft → Sent → Partial → Paid → Void
- Tax line: simple taxable/non-taxable flag per customer for now (TaxJar later)
- **3% credit card surcharge toggle** on invoice — one click adds the fee line
- Print/PDF invoice
- Push invoice to QBO (on demand, not automatic)

### Module 4 — Payments *(Phase 1)*
- Record payment against invoice: cash, check, card
- Partial payments supported
- 3% surcharge line on card payments
- Push payment to QBO (marks QBO invoice as paid)
- No card terminal integration in Phase 1 — record manually, process via QBO Payments externally

### Module 5 — Cores *(Phase 1)*
The most unique and business-critical workflow.

**Inbound (buying from vendor):**
- PO line item carries a core charge amount
- Core charge logged as a vendor core liability

**Outbound (selling to customer):**
- Invoice line item carries core charge (auto from product)
- Core charge logged as a customer core liability (we owe customer a credit when they return)

**Customer core return:**
- Select customer → see open core charges
- Issue credit memo for returned cores (reduces their core liability)
- Credit applied to their account or refunded

**Vendor core return:**
- Select vendor → see cores we've accumulated and owe back
- Log cores shipped back to vendor
- Record vendor credit when received
- Closes the liability loop

**Core Ledger:** simple view of all open core charges by customer and by vendor

### Module 6 — Purchase Orders & Vendor Bills *(Phase 1)*
- Create PO: vendor, line items (product + qty + cost + core charge)
- Receive against PO: marks items as received, updates inventory
- PO → Vendor Bill: push to QBO as a bill (so wife sees expenses in QBO P&L)
- Vendor list: PAI, HHP, ATL, others as needed

### Module 7 — Shopify Sync *(Phase 2)*
- Push a product from the app to Shopify with one click
- Fields mapped: title, description, price, images, SKU, inventory quantity
- When a Shopify order comes in → create invoice in app → deduct inventory
- Manual trigger first; webhook automation later

### Module 8 — Scraper Tools *(Phase 2 — already partially built)*
- PAI portal: cost lookup, stock check, cross-references
- HHP: pricing, images, product description
- ATL Diesel: cross-references, pricing
- Run from product entry screen — "Enrich from PAI/HHP/ATL" button
- Results shown for review before saving (user always confirms)

### Module 9 — eBay Listings *(Phase 3)*
- Push product to eBay listing
- Sync sold → create invoice → deduct inventory

### Module 10 — TaxJar *(Phase 3 — when shipping starts)*
- Calculate sales tax on invoices based on ship-to address
- Collect and record for remittance

---

## 5. What We Are NOT Building (Yet)

| Feature | Why cut |
|---|---|
| Fleet management | Not core to the business now |
| ESN lookup scraper | Useful someday — design the data model to accommodate it later |
| Barcode scanning | Not needed at current scale |
| Period close wizard | QBO handles this |
| Work orders / field service | Out of scope |
| Daily delivery routes | Out of scope |
| SMS / marketing automation | Out of scope |
| Tiered pricing | Per-customer discount % covers the need now |
| Aging reports | QBO handles AR aging; revisit when volume grows |
| Complex QBO inventory sync | Cut entirely — was broken and not needed |

---

## 6. Phased Build Plan

### Phase 1 — Operational Core *(Build this first; must be 100% reliable)*
1. Database schema + migrations (SQLite)
2. Products — entry, edit, list, search
3. Customers — entry, edit, list, call log
4. Vendors — entry, edit, list
5. Purchase Orders — create, receive, bill to QBO
6. Quotes — build, edit, convert to invoice
7. Invoices — create, payment, PDF, push to QBO
8. Cores — full cycle (buy → sell → customer return → vendor return)
9. Basic dashboard: today's sales, open quotes, low stock, open cores

### Phase 2 — Market Expansion
1. Shopify product push + order sync
2. Scraper tools surfaced inside product entry
3. Simple TaxJar for taxable local customers

### Phase 3 — Growth
1. eBay listings
2. Full TaxJar (shipping / multi-state)
3. ESN lookup scraper
4. Mobile-first improvements based on real usage patterns

---

## 7. Design Principles for the Rebuild

1. **Reliable over feature-rich.** Every screen in Phase 1 must work completely before Phase 2 starts.
2. **One source of truth.** Inventory lives here, not in QBO. QBO gets pushed financial events only.
3. **Scraper data is advisory.** User always reviews before it saves to a product.
4. **Price is always overridable.** No locked-in price logic that the user cannot bypass.
5. **Mobile-first for CRM.** The call log and customer view must work on a phone browser.
6. **Cores are first-class.** Not an afterthought — designed into the data model from day one.
7. **No dead screens.** If a screen is in the nav, it works. If it doesn't work, it's not in the nav.

---

## 8. Decisions — Locked

| # | Question | Decision |
|---|---|---|
| A | QBO connection method | **OAuth flow** in the app |
| B | Shopify API keys | **Ready** — wire up in Phase 2 |
| C | 3% CC surcharge rate | **Configurable in Settings** (not hardcoded) |
| D | App startup | **Windows Service** — auto-starts on boot, always available |
| E | Database migration path | **SQLAlchemy ORM** — SQLite now, PostgreSQL-ready later |

---

*Next step: Database schema design → then scaffold the FastAPI project.*
