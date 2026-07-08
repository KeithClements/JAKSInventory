# Open Questions (Multi-Choice)

The engine's behavior depends on these answers. Each section lists the
options; pick one (or several where noted). User answers will be appended
under each question as they come in.

---

## A. Stack & runtime

**A1. Primary language / runtime for the new engine?**
- [ ] (a) Python 3.12 + FastAPI (closest to existing code, fastest port)
- [ ] (b) Node.js + Fastify/Nest
- [ ] (c) Go (highest perf, biggest rewrite)
- [ ] (d) Don't care — recommend (a)

**A2. Database?**
- [ ] (a) SQLite (single-file, simplest)
- [ ] (b) PostgreSQL (production-grade, recommended)
- [ ] (c) Start SQLite, migrate to Postgres later

**A3. Deployment target?**
- [ ] (a) Single VM / bare-metal box at the shop
- [ ] (b) Docker on a small cloud VM
- [ ] (c) Managed PaaS (Fly.io, Render, Railway)
- [ ] (d) Stays on-prem; no internet exposure except outbound

**A4. Migration of existing data?**
- [ ] (a) Full import — bring every product, customer, invoice, PO, core obligation
- [ ] (b) Catalog + customers + open balances only (drop closed history)
- [ ] (c) Fresh start — re-enter what matters
- [ ] (d) Hybrid: full catalog, fresh transactions

---

## B. Products engine

**B1. Source of truth for a product's cost?**
- [ ] (a) PAI cost (last scraped `your_price`) — authoritative, override only with reason
- [ ] (b) Manual cost — PAI is reference only
- [ ] (c) Whichever was updated most recently
- [ ] (d) PAI for PAI-sourced SKUs; manual for everything else

**B2. When PAI cost changes by >X% on a re-scrape, what should the engine do?**
- [ ] (a) Auto-update silently
- [ ] (b) Auto-update + flag the product for review
- [ ] (c) Hold the change in a "pending cost change" queue; require approval
- [ ] (d) Auto-update + auto-bump selling price by same %

What's the X threshold?  ___ % (default 5)

**B3. Stale-cost refresh cadence (background re-scrape of PAI for known SKUs)?**
- [ ] (a) Nightly
- [ ] (b) Weekly
- [ ] (c) Only on demand
- [ ] (d) Nightly for SKUs with sales in last 90d; weekly for the rest

**B4. New-product creation paths to support?** (multi-select)
- [ ] (a) Manual create (API call with fields)
- [ ] (b) From a PAI SKU (engine fetches PAI, creates the product)
- [ ] (c) From OEM number (engine searches PAI → match → create)
- [ ] (d) From an HHP scrape match
- [ ] (e) From CSV bulk import
- [ ] (f) From a competitor URL paste

**B5. Cross-reference (`product_interchanges`) sources?** (multi-select)
- [ ] (a) PAI OEM numbers
- [ ] (b) HHP product page cross-refs
- [ ] (c) ATL product page cross-refs
- [ ] (d) Manual entry only
- [ ] (e) All of the above, merged + de-duped

**B6. Competitor price tracking — which competitors to watch?** (multi-select)
- [ ] (a) HHP (Highway & Heavy Parts)
- [ ] (b) ATL Diesel
- [ ] (c) FleetPride
- [ ] (d) DPD / Diesel Pro Power
- [ ] (e) Others (list: __________)

**B7. How often to re-check competitor prices for SKUs with a known competitor URL?**
- [ ] (a) Daily
- [ ] (b) Weekly
- [ ] (c) Only when running a full scan
- [ ] (d) Daily for top-100 SKUs; weekly for the rest

**B8. What to do when competitor price drops below ours?**
- [ ] (a) Log only; surface in a "competitive alerts" endpoint
- [ ] (b) Log + email the owner
- [ ] (c) Auto-match (lower our price within a configured floor)
- [ ] (d) Auto-match only inside a per-category margin floor

**B9. Selling-price model?**
- [ ] (a) Single flat selling_price per product
- [ ] (b) Customer-tier × category-discount grid (the existing v2 model)
- [ ] (c) Both: tier grid is default, per-product override allowed
- [ ] (d) Cost × markup % (single markup per category)

**B10. MAP (minimum advertised price) enforcement?**
- [ ] (a) Hard block — never allow a price below MAP, even with overrides
- [ ] (b) Soft warn — allow with manager confirmation
- [ ] (c) Ignore MAP entirely
- [ ] (d) Hard block for retail; soft for dealer/wholesale tiers

**B11. Per-product quantity tiers (3 @ $X, 5 @ $Y) — keep?**
- [ ] (a) Yes, keep as in current schema
- [ ] (b) Drop — too rarely used
- [ ] (c) Keep but only for products explicitly flagged

---

## C. Cores — the 5-step engine

The user mentioned **"our core 5-step process engine"**. Confirm the steps:

**C0. Are these the 5 steps?**
1. **Sell** — customer buys a REMAN part; core charge added to invoice.
2. **Receive customer core** — customer returns the used core; we credit them.
3. **Build vendor obligation** — that returned core becomes an obligation we
   owe a vendor (or stays as our stock to refurb).
4. **Ship to vendor (RGA)** — we batch obligations, request RGA, ship.
5. **Vendor credit applied** — vendor issues credit memo; we reconcile and
   apply to AP / future bills.

- [ ] (a) Yes, exactly those 5
- [ ] (b) Mostly — corrections: __________
- [ ] (c) No — the 5 steps are: __________

**C1. Default core charge model?**
- [ ] (a) Always refundable (full credit on return)
- [ ] (b) Conditional exchange (credit only if returned core is rebuildable)
- [ ] (c) No-charge swap (1-for-1 exchange, no money)
- [ ] (d) Per-product setting (already in schema) — keep that

**C2. Customer core return window default?**
- [ ] (a) 30 days
- [ ] (b) 60 days
- [ ] (c) 90 days
- [ ] (d) 180 days
- [ ] (e) Per-vendor / per-product

**C3. What happens to a core past its return window?**
- [ ] (a) Auto-forfeit (status flips, no credit possible)
- [ ] (b) Auto-forfeit + notify customer 7 days before
- [ ] (c) Stay open forever; manual forfeit only
- [ ] (d) Auto-forfeit at 2× window unless manually extended

**C4. Customer core credit — default form?**
- [ ] (a) Store credit (applies to next invoice)
- [ ] (b) Cash refund
- [ ] (c) Refund to original payment method
- [ ] (d) Customer's choice each time

**C5. Vendor RGA grouping?**
- [ ] (a) Auto-create when a vendor's obligation count hits N (default 10)
- [ ] (b) Auto-create when total obligation $ hits a threshold
- [ ] (c) Time-based: every N days roll up open obligations into a draft RGA
- [ ] (d) Manual only

**C6. Variance handling (vendor credits less than expected)?**
- [ ] (a) Auto-accept any variance, log it
- [ ] (b) Auto-accept under $X variance; flag above
- [ ] (c) Always require human acceptance
- [ ] (d) Auto-reject and re-open the obligation

**C7. Serialized cores?**
- [ ] (a) Track every core by serial (turbos, injectors, ECMs)
- [ ] (b) Track serial only for SKUs flagged `requires_serial=1`
- [ ] (c) Don't track serials at all

---

## D. QBO sync

**D1. QBO mode the engine ships in by default?**
- [ ] (a) Mock (no real calls, queue still records)
- [ ] (b) Read-only (pulls only)
- [ ] (c) Read-write (full bidirectional)
- [ ] (d) Off (disable entirely until user enables)

**D2. Which entities should the engine push to QBO?** (multi-select)
- [ ] (a) Customers
- [ ] (b) Vendors
- [ ] (c) Items (products)
- [ ] (d) Invoices
- [ ] (e) Invoice payments
- [ ] (f) Sales receipts (cash sales)
- [ ] (g) Credit memos / refunds
- [ ] (h) Estimates (from quotes)
- [ ] (i) Purchase orders
- [ ] (j) Bills (from PO receipts)
- [ ] (k) Vendor credits (from RGAs)
- [ ] (l) Inventory adjustments (as journal entries)
- [ ] (m) Core liability journal entries

**D3. Which entities should the engine pull from QBO?** (multi-select)
- [ ] (a) Customer updates (terms / balance / address)
- [ ] (b) Vendor updates
- [ ] (c) Payments entered directly in QBO
- [ ] (d) Item changes (rare; for reconciliation)
- [ ] (e) Nothing — push-only

**D4. Conflict policy when both sides changed an entity?**
- [ ] (a) Engine wins (overwrite QBO)
- [ ] (b) QBO wins (overwrite engine)
- [ ] (c) Field-by-field three-way merge; conflicts go to a review queue
- [ ] (d) Lock — block writes until human resolves

**D5. Inventory accounting in QBO?**
- [ ] (a) Inventory tracked in QBO too (mirror) — push every qty change as JE
- [ ] (b) Inventory only in engine; QBO sees only COGS at invoice time
- [ ] (c) Non-inventory items in QBO; track everything in engine

**D6. Webhooks from QBO?**
- [ ] (a) Yes — set up the receiver endpoint
- [ ] (b) No — poll-only (simpler, slower)
- [ ] (c) Yes for payments + customers; poll for the rest

**D7. Retry policy for failed QBO pushes?**
- [ ] (a) 5 attempts with backoff 1m → 5m → 15m → 1h → 6h, then human queue
- [ ] (b) 3 attempts with backoff, then human queue
- [ ] (c) Unlimited backoff, never give up
- [ ] (d) Custom: __________

**D8. Period-close enforcement?**
- [ ] (a) Engine respects closed periods (refuse to push, queue a reversing entry)
- [ ] (b) Engine ignores QBO close (admin pushes through)
- [ ] (c) Closed periods block all related mutations in the engine too

---

## E. Email

**E1. Email provider?**
- [ ] (a) SMTP (Gmail/O365/own server)
- [ ] (b) SendGrid / Postmark / Mailgun API
- [ ] (c) AWS SES
- [ ] (d) Don't care — recommend Postmark for transactional

**E2. Which outbound emails should the engine send?** (multi-select)
- [ ] (a) Quote to customer (PDF attached)
- [ ] (b) Order confirmation (SO created)
- [ ] (c) Shipment notification (with tracking)
- [ ] (d) Invoice (PDF attached)
- [ ] (e) Payment receipt
- [ ] (f) Statement of account (monthly)
- [ ] (g) Core return reminder (30/60/90 day)
- [ ] (h) Past-due invoice reminder
- [ ] (i) RGA approved / shipped notice to vendor
- [ ] (j) PO sent to vendor (PDF attached)
- [ ] (k) Internal alerts (low stock, QBO failures, scrape failures)
- [ ] (l) Daily summary email to owner

**E3. Inbound email handling?**
- [ ] (a) None — outbound only
- [ ] (b) Parse vendor RGA confirmations and update RGA status automatically
- [ ] (c) Parse customer replies into a CRM thread
- [ ] (d) Both (b) and (c)

**E4. Templates: where stored?**
- [ ] (a) In DB (`email_templates` table), editable via API
- [ ] (b) Files on disk (Jinja2/Handlebars), versioned in git
- [ ] (c) Both — DB overrides file defaults

**E5. Sending domain / reply-to?**
- [ ] (a) Send from `quotes@jaksdiesel.com`, replies to same
- [ ] (b) Per-user from address (rep@jaksdiesel.com)
- [ ] (c) Single shared address for everything
- [ ] (d) Configurable per template

**E6. Rate / volume controls?**
- [ ] (a) None — provider handles it
- [ ] (b) Daily cap, queue overflow to next day
- [ ] (c) Quiet hours (no automated email between 8pm–7am local)
- [ ] (d) Both cap + quiet hours

**E7. Attachments / PDF generation?**
- [ ] (a) Engine renders PDFs in-process (wkhtmltopdf / Puppeteer)
- [ ] (b) Engine calls a separate PDF service
- [ ] (c) No PDFs — text/HTML body only
- [ ] (d) PDFs for invoices/POs only; HTML body for the rest

**E8. Bounce / complaint handling?**
- [ ] (a) Capture via provider webhook, mark contact as bounced, suppress future sends
- [ ] (b) Log only; manual cleanup
- [ ] (c) Auto-disable customer email on hard bounce + alert internal

---

## F. Other engine surfaces (quick yes/no)

**F1. Keep SMS in the engine?** (a) Yes (b) No (c) Email-only for now
**F2. Keep Shopify sync?** (a) Yes (b) No (c) Later
**F3. Keep marketing automation rules?** (a) Yes (b) Drop (c) Defer
**F4. Keep multi-warehouse / locations?** (a) Yes (b) Single warehouse only
**F5. Keep ESN (engine serial) history?** (a) Yes (b) Drop
**F6. Keep kits (parent SKU explodes to children)?** (a) Yes (b) Drop
**F7. AI features (suggested sells, AI part assistant)?** (a) Yes (b) Drop (c) Stub for later
**F8. API auth model?**
- [ ] (a) API keys per user
- [ ] (b) OAuth2 / JWT
- [ ] (c) Session cookies (single-tenant, local)
- [ ] (d) mTLS (on-prem only)

---

Answer with section letter + question number + choice, e.g. "B1 = a, B2 = b
with 7%, C0 = a".
