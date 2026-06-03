# JAKS Inventory — Phase 2 Plan

*Created: 2026-06-02 · Owner: Keith · Status: **LOCKED 2026-06-02 — scope finalized via interview (owner: "lock it"); ready to build.***
*Source: Phase 1A signed off 2026-06-02 (see [`PHASE_1_TEST_PLAN.md`](PHASE_1_TEST_PLAN.md) §13). This doc organizes the owner's post-cert wishlist into an executable plan.*

> **How to read:** §1 = carry-over from Phase 1 (do first). §2 = locked decisions from the interview. §3 = open questions (I applied recommended defaults — flip any). §4–§7 = the modules. §8 = priority order. §9 = complementary ideas not in the original notes.

---

## 1. Phase-1 carry-over (do before / alongside Phase 2)

**Data-safety cutover (before real production data — from §13 sign-off):**
- [ ] Perform one real **backup → restore** (X6).
- [ ] Set a **strong admin password** at `/account`.
- [ ] Confirm the **CSRF LAN waiver**.

**Phase 1.1 quick fixes (small, found during acceptance):**
- [ ] **Quote-count badge mismatch** — Customer tab shows "Quotes (1)" while the panel shows "No Open Quotes." Badge counts *all* quotes; panel filters to *open*. Reconcile so badge counts and displayed data always match (decide: badge = open-only, or label it "Quotes (total)"). *This is a Phase-1 bug, not a feature.*
- [x] ~~Per-customer card-surcharge **%** default field~~ — done in Phase-1 (D-7).
- [x] ~~Core-return flow keeps invoice open + slip in new window~~ — done in Phase-1 (After-Sale card).

---

## 2. Locked decisions (interview Rounds 1–4, locked 2026-06-02)

| # | Decision | Choice | Implication |
|---|---|---|---|
| P2-D1 | **Customer defaults model** | **Type-driven default profiles** | Each Customer Type owns a defaults profile (terms, pricing tier, tax-exempt, surcharge %, surcharge-on/off, credit limit, status). New-customer form: pick type → auto-fill → user can override. Global fallback for "Other". |
| P2-D2 | **Must-see customer info** | **Structured flags + freeform notes** | Build a flag taxonomy (chips) shown everywhere transactions happen, PLUS notes surfaced in a panel. Flags can drive rules later (e.g., Requires-PO). |
| P2-D3 | **Profit/margin visibility** | **Show/hide toggle (no role gate)** | Margin/profit on quote/invoice/intelligence panels sits behind a per-user "Show margin" toggle, **off by default**. No role plumbing. |
| P2-D4 | **Credit enforcement** | **Display + warn only** | Show limit/available/hold; warn when a doc would exceed available credit or customer is on hold. **Never block** (revisit hard-block in a later phase). |
| P2-D5 | **Credit-hold trigger** *(R3)* | **Manual flag only** | Staff toggle a "Credit Hold" flag; system warns on docs. No auto-hold. |
| P2-D6 | **Customer Type** *(R3)* | **Single type, fixed list** | One type per customer: Fleet · Owner-Operator · Repair Shop · Dealer · Municipality · Internal · Other. This is the key that maps to the P2-D1 default profiles. |
| P2-D7 | **Won/Lost quote tracking** *(R3)* | **Yes — structured reasons** | Mark-Lost prompts a reason (Price · Lead time · Competitor · No longer needed · No response · Other) + optional note → activates `lost_sales_log`; win-rate / lost-$ reporting. |
| P2-D8 | **Job templates** *(R3)* | **Deferred** | Not in Phase 2 (existing "Save Standard" covers part). |
| P2-D9 | **Vendor catalog** *(R4 → REMOVED from ERP scope 2026-06-02)* | **No PAI/HHP/ATL integration in the ERP** | The standalone **PAI Info** tool (PAI catalog + HHP/ATL competitive pricing + OEM cross-refs) stays **fully external** — the ERP neither scrapes nor imports it. The full PAI catalog flows to **Shopify** (separate track); the ERP holds only the parts JAKS actually stocks. No importer, no scrapers, no competitor-pricing field in Phase 2. |
| P2-D10 | **Truck-Down depth** *(R4)* | **Queue + dashboard + color** | Visibility-first; expedited-PO suggestions + escalation report come later. |
| P2-D11 | **Vendor contacts** *(R4)* | **Fixed roles** | Sales Rep · Warranty · Returns/RMA · Core Dept · Accounting (+ a primary). |
| P2-D12 | **Deposit refund** *(R4)* | **Convert to account credit** | Refund/cancel a deposit → customer account credit for the next invoice. |

---

## 3. Decisions locked from recommended defaults (2026-06-02)

Owner said **"lock it"** — the Round 2 + Round 4 recommended defaults are now **final decisions**, not assumptions.

| # | Question | Decision (locked) |
|---|---|---|
| P2-Q1 | "Lifetime / YTD Sales" definition | **Net invoiced incl. open, minus returns/credits; YTD = calendar year.** (Best "relationship value" number.) |
| P2-Q2 | Category hierarchy depth + attach point | **Exactly 2 levels (Category → Subcategory); products attach to the leaf subcategory.** Existing flat categories migrate into the tree. |
| P2-Q3 | Activity timeline capture | **Auto-capture system events (quote/SO/invoice/payment/return/warranty) + manual calls/notes.** |
| P2-Q4 | PDF branding scope | **Logo + company header block + configurable footer (terms/return policy/thank-you).** No full template engine yet. |

**All Round 2 + Round 4 items are LOCKED** (2026-06-02) — §2 (P2-D9..D12) and the table above (P2-Q1..Q4) are final.

**Deferred to build-time (not blocking — genuinely premature to lock now):** backorder customer-notification *channel* (email vs SMS — needs the Phase-2 messaging layer); core-inspection template depth; whether "Assigned Salesperson" should start attributing now (ties to the 2-user login); fiscal vs calendar year if you don't use calendar-year for YTD.

---

## 4. Customer Module

*Builds on: customers table, `card_surcharge_pct` (D-7), activity-log foundation (`customer_call_logs` + `communication_log`, CRMService.get_timeline), `lost_sales_log`/`quote_followups` scaffolds.*

### 4.1 Customer Type + Type-driven Defaults *(P2-D1)*
- `customer_type` field (single-select; fixed list — Fleet · Owner-Operator · Repair Shop · Dealer · Municipality · Internal · Other, per P2-D6). Show on list, detail, preview.
- **Type-defaults profiles** (new `customer_type_defaults` table or settings blob): per type → payment terms, pricing tier, discount %, credit limit, tax-exempt, card-surcharge % + apply-by-default, status, "same as billing" default.
- New-customer form auto-fills from the selected type; every field overridable; **existing customers unaffected.**
- Global fallback defaults in **Settings → Customer Defaults**.

### 4.2 Card-surcharge **apply-by-default** *(extends D-7)*
- Add customer boolean **"Apply card surcharge by default"** (separate from the % already built). New invoices inherit it (pre-check the surcharge box); override per invoice. Wires into the type-defaults profile.

### 4.3 Customer Flags + Notes Visibility *(P2-D2)*
- **Flags (chips):** Requires PO #, Credit Hold, Tax Exempt, Call-first, Text-preferred, Warranty-escalation, + extensible. Render on: Customer list (icon), Preview panel, Customer detail, **Quote / SO / Invoice workspaces**.
- **Notes:** surface existing customer notes (internal vs customer-facing kept distinct — internal never on customer PDFs) in a panel on the same surfaces. Note **indicator/chip** on the customer list with hover/click preview.
- *Rule hook (later):* Requires-PO flag → warn/require a PO # before finalizing.

### 4.4 Customer Intelligence Panel *(P2-Q1 metrics)*
On Account tab + condensed in the Preview panel + list columns:
- Lifetime Sales · YTD Sales · Average Order Value · Last Quote / Invoice / Payment date.
- Open AR Balance · Available Credit (Credit Limit − Open AR) · **Total Outstanding Core Credits**.
- *Engine:* one `CustomerMetricsService` (cache/materialize for list performance; live on detail). Single definition per P2-Q1 so list/preview/detail/intelligence all agree.

### 4.5 Credit Visibility + Warn *(P2-D4)*
- Show Credit Limit / Current Balance / Available Credit / Credit-Hold status on customer list, preview, **SO + Invoice** screens.
- **Warn** (non-blocking) when a quote/invoice would exceed available credit or the customer is on Credit Hold.

### 4.6 Activity Timeline (auto-capture) *(P2-Q3)*
- Expand the call log into a unified timeline that **auto-appends**: quotes created/sent, SOs, invoices, payments, returns, warranty claims, notes — plus manual calls/notes.

### 4.7 Enhanced Call Logging
- Add: Subject · Follow-up date · Follow-up user · linked Quote/SO # · Priority · Duration (optional). Turn calls into actionable, linked follow-ups (builds on existing `follow_up_date`).

### 4.8 Communications Center
- Build out the Communications tab into one per-customer timeline: email/SMS history, quote-delivery tracking, acknowledgements, warranty comms, internal notes. *(Depends on Phase-2 real messaging — see §6 below.)*

### 4.9 CRM foundation fields (data model only)
- Reserved columns, no UI behavior yet: Lead Source · Sales Territory · Assigned Salesperson (FK to users) · Industry Segment · Fleet Size · Last Contact Date · Last Marketing Touch · Customer Ranking (A/B/C). Start collecting where free (e.g., Last Contact Date from the timeline) so history builds for the future Lead Finder / CRM.

### 4.10 Customer Entry Form re-order
- Reorder per real usage: Company → Type → Contact → Phone → Email → Terms → Credit Limit → Pricing Tier → Discount % → Tax Exempt → Notes. Move Billing/Shipping address + secondary contacts **below the fold** (most quotes start before full address is known).

---

## 5. Sales / Sales-Order / Core / Invoice Module

*Builds on: SO line `ship_qty` + `linked_po_line_id` (backorder→PO already wired), core lifecycle + VCR, invoice_totals, deposits (SO Full/Deposit/None).*

### 5.1 Sales-Order Dashboard Metrics
- Strip above the SO list: Open SO Value · Backordered Value · Waiting-on-Inventory · Ready-to-Ship · On-Hold · Fulfilled-Today · **Open Core Liability**.

### 5.2 Backorder Management
- Expand partial fulfillment: backordered qty, **ETA tracking**, vendor-PO linkage (surface existing link), customer notification, **Ready-to-Ship queue**.

### 5.3 Truck-Down Priority Workflow
- Promote the existing Truck-Down tag into a workflow: dedicated queue, dashboard alerts, priority color-coding, escalation reporting, expedited-PO suggestions.

### 5.4 Core Dashboard
- Dedicated core-management screen: Outstanding Core Liability · Core Credits Issued · Returns Pending Inspection · Vendor Core Recoveries · **Aging core returns**.

### 5.5 Vendor Core Recovery Workflow
- Track money owed back to JAK's: Vendor RMA · Return Date · Tracking # · Vendor Credit Expected · Vendor Credit Received. (Extends the VCR built in Phase 1.)

### 5.6 Core Return Batch Processing
- Receive multiple cores under one return receipt (e.g., 6 injectors + 1 turbo + 2 heads in one pass). Reduces counter time.

### 5.7 Core Inspection Standards
- Inspection templates/checklists per core type (injector: body/solenoid/connector; turbo: housing/wheel/actuator) to standardize accept/reject credit decisions.

### 5.8 Invoice Intelligence Panel *(P2-D3 — margin behind toggle)*
- Profit $ · Margin % (behind the **Show-margin** toggle) · Core Liability · Warranty Exposure · Customer Lifetime Sales. Context while invoicing.

### 5.9 Deposit Management
- Deposit balance · history · application tracking · **refund workflow** for large jobs.

### 5.10 SO ↔ PO Linking (visibility)
- Surface the existing SO↔PO link with live status (Ordered → Vendor-Confirmed → Shipped → Received) on the SO so staff can see customer orders waiting on inbound stock.

### 5.11 Warranty Intelligence
- Connect warranty claims to Invoice / Customer / Vehicle / ESN / Product; auto-display warranty history to spot repeat failures + high-risk products.

### 5.12 PDF Branding *(P2-Q4)*
- Settings: upload **company logo** (top-left), company header block, configurable footer (terms / return policy / thank-you). Applies to Quote / SO / Invoice PDFs.

---

## 6. Vendor Module

*Builds on: vendors, `vendor_contacts` (O4), PO/receive/3-way-match, VCR; relates to the standalone **PAI Info** tool.*

- **Vendor Intelligence:** Purchases YTD · Lifetime · Open PO Value · Open Bills · Outstanding Vendor Credits · Last Order Date · AOV.
- **Vendor Lead Time (days):** feeds customer ETA, backorder forecasting, purchasing recs, Truck-Down prioritization.
- **Typed vendor contacts:** roles — Sales Rep · Warranty · Returns/RMA · Core Dept · Accounting (name/phone/email/department/notes). Extends `vendor_contacts`.
- **Vendor performance metrics:** Fill Rate % · Avg Lead Time · On-Time % · Return Rate % · Warranty Rate % → vendor scorecards.
- **Vendor catalog integration — REMOVED from ERP scope (2026-06-02).** All PAI/HHP/ATL fetching lives in the standalone **PAI Info** tool (owner adds HHP/ATL competitive pricing + OEM cross-refs to *that* tool); its enriched output feeds **Shopify**, not the JAKS ERP. The ERP does **not** scrape or import vendor catalogs in Phase 2 — no importer, no scrapers, no competitor-pricing field. *(If an in-ERP "look up a PAI part / competitor price" reference is ever wanted, revisit later as a small read-only lookup — never a bulk catalog load.)*
- **Vendor notes by context:** show vendor notes when creating POs, receiving, creating RMAs, processing core returns (e.g., "reference account #12345", "core returns go to Dallas").

---

## 7. Inventory / Catalog Module

### 7.1 Category hierarchy restructure *(P2-Q2)*
- Move flat categories → **2-level tree** (Category → Subcategory); products attach to the leaf subcategory. Migrate existing categories. Example tree: Engine Parts → Cylinder Heads / Inframe Kits / Bearings / Pistons / Liners; Fuel System → Injectors / Fuel Pumps / Transfer Pumps; Cooling → Water Pumps / EGR Coolers / Thermostats; Air & Exhaust → Turbos / Exhaust Manifolds / Charge Air; Suspension → Air Springs / Torque Rods / Bushings; Driveline → Clutches / Yokes / U-Joints.

### 7.2 ~~PAI Info importer~~ — REMOVED from ERP scope (2026-06-02)
The vendor-catalog importer (and the PAI/HHP/ATL scrapers) are **out of Phase-2 ERP scope** — see §6 (P2-D9). The standalone PAI Info tool stays external and feeds Shopify, not the ERP.

---

## 8. Suggested build order

Merges the owner's rankings + technical dependencies. **Foundations first** (they unblock the rest):

1. **Customer Notes + Flags visibility** (P2-D2) — highest owner priority; surfaces everywhere.
2. **Customer Metrics engine** (P2-Q1) — one service powering Intelligence Panel + list + preview + invoice panel.
3. **Type-driven Customer Defaults** (P2-D1) — Customer Type + defaults profiles + form re-order.
4. **Credit visibility + warn** (P2-D4).
5. **SO ↔ PO linking + Backorder/ETA** (data link exists; surface + extend).
6. **Core Dashboard + Vendor Core Recovery** (core exposure $).
7. **Invoice Intelligence Panel** (margin toggle) + **PDF branding (logo/footer)**.
8. **Activity Timeline auto-capture + Enhanced Call Logging.**
9. **Truck-Down workflow · Deposit management · Core batch/inspection.**
10. **Vendor Intelligence + typed contacts + performance.**
11. **Category hierarchy restructure.**
12. **CRM foundation fields** (model only) · **Communications Center** (needs real messaging). *(Vendor-catalog integration removed from ERP scope — §6.)*

---

## 9. Complementary ideas (not in the original notes — candidates)

- **Won/Lost quote tracking — ✅ APPROVED for Phase 2 (P2-D7).** Activate the dormant `lost_sales_log`: structured lost-reason on declined quotes → win-rate + lost-revenue reporting.
- **Saved job templates — deferred (P2-D8).** One-click add a common build (e.g., an ISX15 inframe kit's full line set) to a quote; revisit after Phase 2.
- **Core-aging proactive alerts** — auto-flag customers with cores outstanding past the grace window (dashboard + customer flag), tying into the Core Dashboard.
- **Counter quick-entry** — SKU scan / type-ahead to add a line in one keystroke (counter speed).
- **Customer ranking automation** — derive A/B/C ranking from the metrics engine instead of manual entry.

---

*This plan is shaped from the owner's Phase-1 acceptance notes (2026-06-02) and interview. Confirm/adjust the §3 assumptions and answer the "still to interview" items to finalize scope.*
