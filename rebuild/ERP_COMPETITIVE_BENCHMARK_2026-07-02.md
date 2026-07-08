# Axle ERP vs. the Field — Competitive Benchmark (2026-07-02)

**Scope:** Run JAK's best-in-class (internal ops focus, not productization). Benchmarked against four tiers:
auto/diesel-parts-specific systems, SMB distribution ERPs, enterprise ERPs, and the QBO+Shopify+spreadsheets status quo.

**Method:** (1) Ground-truth capability inventory of the actual codebase (70+ domain models, 33 routers, 269 test
modules, 11 Alembic migrations). (2) Multi-agent web research with adversarial 3-vote verification per claim
(104 agents, 22 sources, 109 claims extracted, 25 verified). Claims that failed verification are labeled
**[unverified]** — notably ALL pricing/TCO and user-review claims, because the verification wave hit a usage-credit
limit mid-run. Two claims were refuted outright and are excluded from this report.

---

## 1. The market in one picture

The verified evidence shows a **two-camp structure**:

- **Niche auto-parts ERPs** (MAM/Klipboard Autopart, Epicor Eagle, Epicor Vision) win on parts-specific DNA —
  above all **interchange/cross-reference catalogs with VIN/fitment lookup**, which generalists lack natively
  (verified 3-0: NetSuite has no native ACES/PIES; Acumatica needs the AutoFitmentPlus ISV add-on; Eagle bundles
  the Epicor PartExpert eCatalog, ~22M OEM-to-aftermarket cross-references).
- **But the flagship niche system forces a rip-and-replace:** MAM/Klipboard Autopart ships its own GL/AR/AP and
  offers **no QuickBooks integration at all** (verified 3-0 against the vendor's own third-party integration list).
  Adopting it means abandoning the QBO workflow, not syncing with it. (Note: the same claim about Epicor Eagle was
  *refuted* 0-3 — this pattern is confirmed only for Autopart.)
- **Distribution ERPs** (Acumatica verified; Fishbowl/Cin7/Prophet 21 unverified) cover generalist table stakes
  well. Acumatica's first-party Shopify connector is bidirectional, **but product/inventory sync runs on scheduled
  prepare/process cycles — only orders are webhook-driven** (verified, medium confidence), and its B2B
  customer-specific-pricing-to-storefront feature **requires Shopify Plus (~$2,300+/mo)** (verified 3-0).
- **Enterprise ERPs** (SAP B1, NetSuite, D365 BC, Odoo): no claims survived verification this run. Directionally
  [unverified]: entry costs in the tens of thousands per year with implementations from $20K to $500K.
- **Status-quo stack (QBO+Shopify+sheets):** evidenced indirectly — standard Shopify has no native B2B
  customer-specific pricing, and practitioner forums document chronic QBO↔Shopify inventory drift.

**Confirmed table stakes for the niche tier** (from Autopart's verified feature set): automated reorder
suggestions, multi-vendor sourcing with best-buy analysis, lead-time calculations, inter-branch transfers,
multi-branch inventory, 3-way match, integrated eCommerce.

**Unverified pricing signals** (all failed verification — treat as directional only):
Prophet 21 ~$200/user/mo with 10-user minimum (~$24K/yr floor); Acumatica ~$6,400/yr entry, $20K–$500K
implementation, $75K–$350K typical TCO; Cin7 Core $349–$999/mo; Epicor Eagle 3.0/5 on Capterra (3 reviews) with
"overpriced legacy software" complaints; Fishbowl freeze/sync-error and weak-reporting complaints.

---

## 2. Where Axle already fits well (at or above parity)

Grounded in code, not aspiration:

| Area | What we have | Benchmark position |
|---|---|---|
| **Purchase-to-pay** | Full PO lifecycle, multi-PO receiving, over-receipt detection, vendor bills with freight-in, **3-way match with 1¢ cost-variance tolerance** and a reconciliation workflow (`POService.compute_match_line`, match resolutions), vendor volume discounts snapshotted per-PO | Matches what Vision markets as its purchasing/AP pillar; ahead of the QBO stack entirely |
| **Order-to-cash** | Quote→SO→Invoice→Payment with option groups, payment allocation across invoices, NSF reversal, credit memos + credit netting, invoice locking, margin snapshots, unified totals engine | Full parity with distribution-tier ERPs |
| **Core / reman lifecycle** | Bidirectional core charges (customer-owes / vendor-owes), partial returns, inspection outcomes, core locations + movement history, vendor core returns (VCR), denial handling, core margin math, ESN/serial gating | **No benchmarked system evidenced this on any verified page.** Absence-of-evidence, not verified absence — but it's the strongest signal in the whole study |
| **Warranty/RMA** | Claim lifecycle with vendor submission, **labor reimbursement (hours × rate) tracked separately from parts credit**, replacement-line chaining, RA with policy snapshots and restocking fees | Diesel-real in a way generalists aren't |
| **Pricing** | Markup tiers (cost brackets), per-product overrides with scraper-proof locks, **cost-plus customer rules with volume breaks and date windows** (self-correcting on receipt), card surcharge at payment time | Parity with niche-tier "warehouse pricing tiers"; more disciplined than most (no stale fixed prices) |
| **QBO integration** | OAuth2, Fernet-encrypted tokens, invoice/bill/payment/credit-memo sync, **off the money path by design**, retry + error surfacing | **Verified differentiator**: sync-not-replace, vs. Autopart's forced accounting replacement |
| **Shopify integration** | Direct API sync on standard (non-Plus) Shopify, auto-hide on vendor OOS / auto-relist, oversell guards, sell-pack enforcement, vendor brand masking | Beats Acumatica's scheduled sync on freshness; does it without a $2,300/mo Plus dependency |
| **Inventory core** | Moving-average costing applied only on receipt, immutable inventory ledger, cost history audit trail, reorder points, dead-stock report | Textbook-correct for a distributor this size |
| **Security/audit** | PBKDF2 (240K iter), signed timed sessions, 4-role RBAC, optimistic locking on money paths, append-only audit log with per-field tracking + IP | Better than most SMB-tier tools; below enterprise (no 2FA/SSO) |

---

## 3. What needs refinement (built, but not at benchmark depth)

1. **Cross-reference / interchange depth — the #1 verified gap-shaped risk.** The *machinery* exists
   (`CrossReference` with normalized indexed search, `ProductApplication` fitment, engine picker), but PartExpert
   ships ~22M cross-references; ours holds only what we've imported from PAI/IMB/scraper data. The moat of the
   niche tier is *data licensing*, not software. Fitment picker is partially wired; `ESNLookup`/`EngineConfig`
   are Phase-3 stubs.
2. **Reporting/BI.** Solid point-in-time reports (AR aging w/ interest, sales by customer/product, valuation,
   dead stock, lost sales) + CSV — but no charts, no scheduled delivery, no trend views. Enterprise tier's clearest
   edge over us. [Fishbowl's weak reporting is its most-complained-about flaw — unverified.]
3. **Messaging is log-only.** The entire Communication model, consent/A2P compliance fields, templates, and
   public doc links exist — nothing actually sends until SMTP/Twilio are configured. One config step from parity
   with everyone.
4. **Replenishment is reorder-point-only.** We have reorder→PO and multi-vendor sources with preferred-vendor
   costs; Autopart's verified table stakes add automated *suggestions*, best-buy analysis across vendors, and
   lead-time math. The data to build this is already in `ProductVendorSource`.
5. **Multi-location is schema-ready, single-location in practice.** Fine for now; inter-branch transfer workflows
   would need finishing if a second location ever happens.
6. **Serials are product-level, not ledgered.** Good enough for ESN warranty gating; not lot/batch tracking.
7. **Known open hygiene** (carried from the fix-before-phase-1 sprint): bookkeeper default password, QBO account
   configuration, owner-run crossref purge.

---

## 4. What's missing to compete (not built at all)

Ranked by how much it actually matters for running JAK's:

| Gap | Who has it | Does JAK's need it? |
|---|---|---|
| **Interchange catalog data at scale** (ACES/PIES feed, PartExpert/LaserCat3 license, or equivalent) | Whole niche tier | **Yes — highest-value gap.** Open question from research: can it be licensed standalone into a custom ERP, at what cost? Worth a vendor inquiry |
| **Barcode receiving + scanning** | All commercial tiers | Yes, eventually — receiving and core intake are the natural first lanes; `core_tracking_number` is already barcode-ready |
| **Automated replenishment suggestions / best-buy** | Autopart (verified) | Yes — cheap to build on existing vendor-source data |
| **Scheduled report delivery + dashboards with trends** | Enterprise tier | Medium — unlocks itself once messaging turns on |
| **EDI (GCommerce VIC, Corcentric)** | Autopart integrates both | **No, for now** — PAI/IMB ordering already works via scraper/portal lanes |
| **Mobile picking app / WMS** | Prophet 21, Indago | No — single warehouse, small crew |
| **Demand forecasting** | Enterprise tier | No — dead-stock + reorder reports cover the current scale |
| **REST API for third parties** | All commercial tiers | No — internal integrations are in-process |
| **Lot tracking, multi-entity, multi-currency, 2FA/SSO** | Enterprise tier | No — explicitly out of scope for a single-entity USD shop |

---

## 5. Where we win (defensible advantages)

1. **Core/reman lifecycle depth.** The single most business-critical workflow for a diesel reman shop, and no
   benchmarked system evidenced anything comparable. Bidirectional tracking, partial returns, inspection, VCR,
   denial resolution, core margin — this is the crown jewel.
2. **QBO sync-not-replace** (verified 3-0 against Autopart). Every niche-tier evaluation for a shop like ours
   starts with "you must abandon QuickBooks." We never ask that.
3. **Real-time Shopify on standard Shopify.** Verified: Acumatica's product/inventory sync is scheduled, and its
   B2B storefront pricing needs Shopify Plus (~$2,300+/mo). Our ERP-driven hide/relist + oversell guards + pack
   enforcement run on the plan we already pay for.
4. **The combination no one offers:** QBO-sync accounting + real-time non-Plus Shopify + diesel core/warranty
   lifecycle. Each tier has one; none has all three. That's the verified strategic conclusion of the research.
5. **Pricing intelligence moat.** AxleForge competitor scraping, vendor availability sync, cost-plus rules that
   self-correct on receipt — commercial ERPs treat competitor pricing as a third-party BI add-on, if at all.
6. **Diesel-real details:** ESN gating on warranty, labor reimbursement, replacement-line chaining, drop-ship POs,
   vendor brand masking, core slips. These come from running the business, not from a requirements doc.
7. **Cost and speed.** Zero license fees vs. [unverified] ~$24K/yr floors and $20K–$500K implementations; feature
   turnaround in days with a 2,900+ test regression gate.

---

## 6. Recommended moves (priority order)

1. **Price the interchange-data gap** — contact Epicor (PartExpert/LaserCat3) or an ACES/PIES data provider about
   standalone licensing. This is the one niche-tier capability we can't code our way to; everything else on their
   feature lists we already match or beat.
2. **Turn on messaging** (SMTP first, Twilio later). Everything is built and compliance-ready; it's config, not code.
3. **Build replenishment suggestions** on top of `ProductVendorSource` (best-buy across vendors + lead-time-aware
   suggested qty). Small build, verified table-stakes feature.
4. **Barcode lane 1: receiving + core intake.** Cheap hardware, biggest error-reduction per dollar.
5. **Close the open hygiene items:** bookkeeper password, QBO account config, crossref purge.
6. **Defer without guilt:** EDI, mobile WMS, forecasting, API, lot tracking, multi-entity, 2FA — no current trigger.

---

## Appendix — verification ledger

- **Confirmed (3-0 unless noted):** Autopart positioning + purchasing table stakes; Autopart accounting
  rip-and-replace; Epicor Eagle/Vision segmentation; interchange as the defining niche capability (PartExpert
  ~22M xrefs, NetSuite/Acumatica lacking native ACES/PIES); Acumatica Shopify B2B pricing requires Shopify Plus.
  Acumatica scheduled-vs-webhook sync confirmed at medium confidence (1-1 split).
- **Refuted (excluded):** "Eagle replaces the QBO+Shopify stack" (0-3); "Acumatica connector requires no
  additional purchase" (0-3).
- **Unverified (verifier outage — usage credits):** all pricing/TCO figures (Prophet 21, Acumatica, Cin7), all
  user-review claims (Eagle Capterra, Fishbowl complaints), Fishbowl/Vision feature lists. Re-verify before using
  in any external-facing material.
- **Coverage caveat:** no confirmed evidence for WHI/Nexpart, PartsTech, Autologue, Fullbay, inFlow, SAP B1,
  NetSuite, D365 BC, or Odoo this run. Core-tracking absence across tiers is absence-of-evidence, not proof.

**Key sources:** klipboard.com/en-us/products/autopart · epicor.com (Eagle for the Aftermarket, Vision,
PartExpert) · acumatica.com (Shopify connector) · Capterra (Eagle, Fishbowl) · top10erp.org (Prophet 21) ·
erpresearch.com (Acumatica costs) · softwareconnect.com (Cin7) · QBO + Shopify community forums.
