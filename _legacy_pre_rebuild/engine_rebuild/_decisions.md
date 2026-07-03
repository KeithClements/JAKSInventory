# Decisions Log

User-confirmed choices from `01_open_questions.md`. Append-only.

---

## Wave 1 (foundation)

| ID | Decision |
|----|----------|
| A1 | **Python 3.12 + FastAPI** |
| A2 | **PostgreSQL** |
| A3 | **On-prem single box at the shop** (outbound internet only — no inbound) |
| A4 | **Fresh start** — no migration of existing data |
| D1 | **QBO mode = Mock** at launch (queue records, no live calls until flipped) |
| E1 | **SMTP** (Gmail / O365 / own server) — no third-party provider |
| B6 | **HHP + ATL** only for competitor tracking |
| F  | **Keep:** SMS, Kits, AI features (suggested sells, part assistant) |
| F  | **Drop:** Shopify sync, Marketing automation rules, Multi-warehouse locations, ESN history |
| C0 | **3-concept core engine** (see `03_cores_engine.md`) — supersedes the 5-step model |

---

## Implications

- **On-prem + outbound-only** → no webhook receivers from QBO or SMS; we poll
  instead. Outbound calls (PAI/HHP/ATL scrape, QBO push, SMTP send) all fine.
- **Fresh start** → no migration scripts needed. We do need a **seed
  loader** for: company info, users, tax rates, default email templates,
  initial vendors, initial price categories / tiers.
- **Drop Shopify + Marketing + Locations + ESN** → schema simplification.
  All `location_*`, `automation_*`, `campaign_*`, `shopify_*`, and the ESN
  history columns / tables are out of scope.
- **Keep AI** → reserve a `services/ai/` namespace; design API surface to
  accept AI-suggested patches (suggested sells, descriptions) as queued
  proposals for human approval rather than direct writes.
- **Mock QBO at launch** → every QBO push path must work end-to-end against
  the mock so the real flip is a config change, not a code change.
- **SMTP** → bounce handling is limited; we rely on SMTP-level error codes
  + manual cleanup, not provider webhooks.

---

## Wave 2 (products / pricing / QBO / email)

| ID | Decision |
|----|----------|
| B1  | **Cost truth = hybrid**: PAI is authoritative for PAI-sourced SKUs; manual cost for everything else. Tracked via `products.cost_source` (`pai` / `manual`). |
| B2  | **Pending cost-change queue**: when a re-scrape detects a >threshold delta on a PAI SKU, do NOT auto-apply. Insert into `pending_cost_changes` for human approval. |
| B3  | **No scheduled full re-scans.** Pricing refresh is event-driven (see `02_products_engine.md` and `06_scrapers_engine.md`). Triggers: product opened, quote line added, manual refresh, PO review, low-margin warning, stale-cost threshold. Large vendor cost shifts emit `market_change_event` which fans out a *related-SKU* refresh (same engine family / category / vendor group). |
| B4  | **All six new-product paths enabled**: manual, from PAI SKU, from OEM number, from CSV bulk import, from HHP scrape match, from competitor URL paste. |
| B7  | **Weekly** competitor (HHP/ATL) price re-check for SKUs with a known competitor URL. |
| B8  | **Log only** when competitor undercuts. Surface in `/alerts/competitive` endpoint; no auto-match, no email. |
| B9  | **Tier grid is default, per-product override allowed.** Pricing resolver order: per-product override > qty-tier > tier-grid > flat selling_price > cost×markup fallback. |
| B10 | **MAP = soft warn.** Engine accepts the price; flags the line so an approver can intervene. |
| B11 | **Per-product qty tiers gated by `products.qty_tiers_enabled = 1`.** Resolver skips the tier-table lookup otherwise. |
| D2  | **QBO push:** Customers, Vendors, Items, Invoices, Invoice payments, Sales receipts, Credit memos, Bills, Vendor credits. **NOT** pushed: Estimates, POs, inventory adjustment JEs, core-liability JEs. |
| D3  | **QBO pull:** customer updates + payments entered directly in QBO. Poll-based (no inbound webhooks). |
| D5  | QBO sees products as **non-inventory items**. Engine is sole owner of `qty_on_hand`. COGS lands in QBO only at invoice time (Invoice line carries item + cost). |
| E2  | **Outbound emails:** Quote, Shipment, Invoice, Payment receipt, Statement, Core reminder, Past-due reminder, RGA notice to vendor, PO to vendor, Internal alerts. **NOT** sent: order-confirmation, daily-summary. |
| E3  | **Outbound only** — no inbound parsing in this build. |
| E7  | **In-process PDF rendering** (wkhtmltopdf or Puppeteer). |
| F8  | **Session cookies** for API auth (single-tenant local). |

### Wave 2 implications

- **Event-driven pricing**: build a `pricing_refresh_queue` instead of cron. Workers consume the queue; rate-limited per-source. No nightly full re-scans.
- **`market_change_event`** is a first-class table: a >X% cost shift on one PAI SKU enqueues refresh for sibling SKUs (same `engine_family` / `category` / `vendor_id`).
- **Pending cost changes** require an approval endpoint + audit. UI-agnostic: any client can list pending changes and approve/reject.
- **No inbound HTTP**: QBO pull is poll-based; email is outbound-only. Drops the need for any public webhook receiver on the shop box.
- **Inventory adjustments NOT pushed to QBO** — the engine carries the full audit trail; QBO sees COGS only at invoice time. Reconciliation reports compare the two at month-end.
- **In-process PDF**: pick **wkhtmltopdf** for the lowest install footprint; Puppeteer if richer rendering needed later.
- **Session cookies**: standard server-side session store (Postgres-backed). Login via username/password + PIN-protected manager overrides for risky actions.

---

## Wave 3 (policy + ops detail)

| ID | Decision |
|----|----------|
| C1 | **Default customer core return window: 30 days.** Per-product override allowed via products.core_return_days. |
| C3 | **Expired customer cores stay in liability.** No auto-revenue conversion. Operator must manually write off via a `Write-off expired cores` action that posts the liability→income JE and closes the `customer_core_event`. |
| C5 | **Vendor RGAs auto-group when N cores accumulate per vendor.** N is per-vendor setting (default 10). An RGA-draft is auto-created and emitted as `rga.draft_ready`; operator finalizes before send. |
| C6 | **Vendor credit auto-accept tolerance: ±$1 per unit.** Within tolerance auto-applies; outside tolerance opens a conflict row for review. |
| C7 | **Vendor-rejected cores enter `rejected_review` status.** Operator then picks disposition: return_to_shelf, scrap, dispute_resubmit, send_to_alt_vendor, salvage_teardown, customer_chargeback, warranty_hold. Each is its own audit transition. |
| D4 | **QBO conflicts lock both sides** until operator manually resolves via the conflict queue. No automatic merge. |
| D7 | **QBO push retry: 5 attempts with 1m→5m→15m→1h→6h backoff,** then dead-letter for operator review. |
| D8 | **Engine respects QBO closed-period lock.** Backdated entries with txn_date before `qbo_closed_through` are blocked at the engine layer and surfaced to the operator. |
| E5 | **Per-operator From (their name), single shop Reply-To.** Operator name+email pulled from `users` table; Reply-To from `email_smtp_config.reply_to`. |
| E6 | **Quiet hours 6pm–8am local, daily cap 200 messages.** External-recipient emails respect both; internal alerts bypass quiet hours. |
| F1 | **SMS provider: Twilio.** |
| F2 | **Kits support both modes via per-kit flag** `assembly_mode = 'virtual' | 'stocked'`. Virtual: components deducted at sale. Stocked: build/unbuild transactions deduct components and increment the kit SKU. |
| F3 | **AI features kept at MVP (all queued, operator-approves):** part-finder fuzzy match, suggested-sell from competitor graph, email draft assistance. |
| F5 | **Audit log retention: 7 years**, then archive to cold storage (compressed S3 prefix). |
| F6 | **Backups: nightly pg_dump to local NAS + offsite (S3/Backblaze)** with 30-day retention local, 1-year offsite. |
| F7 | **Two roles: Admin and Operator.** Admin = settings, pricing tiers, QBO config, user mgmt. Operator = quotes/orders/POs/receiving/cores/email. PIN-override (F8) escalates an Operator for a single risky action. |
