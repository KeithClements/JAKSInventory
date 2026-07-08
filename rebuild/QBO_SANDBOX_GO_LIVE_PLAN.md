# QBO Sandbox → Live Plan + Shopify Data Reconciliation

**Date:** 2026-07-02 · **Status:** Decisions locked with owner (interview 2026-07-02)
**Live QBO:** JAK's Diesel, LLC · **Shopify:** jaksdiesel.com (Basic plan)

---

## 0. Locked owner decisions (2026-07-02)

| # | Decision | Answer |
|---|----------|--------|
| D-A | Part-number rule | JAKS SKU stays the one unique identifier. OEM numbers → OEM cross-references (shared across products is normal). Vendor part numbers stay on ProductVendorSource. Fix only null/duplicate/irregular SKUs in place — **no mass re-mint** of live listings. |
| D-B | QBO per-part items | At live cutover, the 225 legacy QBO inventory items are **retired (made inactive)** after final reconciliation. ERP is the inventory system of record. All new sales post to the 5 generic JAKS summary items (SKU rides in line description). |
| D-C | Web-order flow | Shopify→QBO connector **keeps booking website orders** exactly as today. ERP pushes only counter/phone/fleet documents. Web orders are never entered as ERP invoices → no double-booking possible. |
| D-D | Sandbox trial | **30 days**, one full month-end close against sandbox QBO. Exit criteria in §4. |

---

## 1. Where the data actually stands (ground-truthed 2026-07-02)

### Shopify → ERP: already done, needs a reconcile pass — not a migration
- ERP `jaks.db`: **30,935 products, 30,929 already linked** to Shopify (`shopify_product_id` set). 6 unlinked.
- Shopify: ≥10,000 active products (count API caps at 10k; real ≈19k active) + 8,799 drafts.
- Shopify has only **13 customers and 7 orders** — the storefront is young. No order-history migration is worth building (and per D-C, web orders stay out of the ERP anyway).
- Remaining Shopify-side work (see §2): reverse-link audit, irregular house-brand SKUs (found at least one variant with **no SKU**, stage-suffix SKUs like `JAKS-2239250S1` vs `JAKS-S22392503`, and one SKU/title mismatch `JAKS-4101420S3` on a 5658283-titled head), and OEM-number reclassification.

### Live QBO: 291 active items (225 inventory / 47 non-inventory / 19 service)
Duplicate part numbers fall into three classes — **the education**:

1. **Same OEM number, genuinely different products** (most of them). E.g. OEM `2239250` appears on 5 items (Stage 2/3/5/6 C15 heads), `4298234` on Stage 2 + Stage 3, `5406187` on PAI-brand vs McBee-brand kits, `2881757` SOHC vs DOHC oil pumps. → **Not duplicates.** In the ERP each is its own SKU; the shared OEM number becomes an OEM cross-reference. In QBO these items retire per D-B.
2. **True duplicates** — `DD1501-145` twice, `2113023` twice (`10R8501 2113023` vs `10R8501 / 2113023`), typo twins (`Intake/Exh aust`), `Credit Card Fee` under both `Fees:` and `MISC:`, `Cummins N14 Accessory Drive - 3078307` under two categories, `A4720162120` head gasket in two hierarchies. → Retire with everything else at cutover; if any carry qty-on-hand, merge/zero first (§5 step 4).
3. **Shopify-connector auto items** — `Shopify sales item`, `Shopify Sales Tax Item` (x2 case variants), refund/tips/shipping/gift-card/discount items. → **Leave alone**; the connector owns them (D-C).

Data-quality flags in live QBO: negative qty on hand on `CAT 3406C Exhaust Manifold Kit (PAI)` (−3) and `Miscellaneous:Cummins N14 Accessory Drive` (−1); inconsistent category trees (~40 `JAKS-G2-*` injector items sit at top level, others nested 2–3 deep).

### Live QBO chart of accounts the ERP touches (from live balance sheet + P&L)
| ERP mapping key (Settings → QBO Accounts) | Live QBO account (exists today) |
|---|---|
| `qbo_income_account` | **Sales of Product Income** ✓ |
| `qbo_bill_expense_account` | **COGS - Diesel Parts** ✓ |
| `qbo_freight_in_account` | **COGS - Freight In** ✓ |
| `qbo_freight_out_account` | **Freight Out** ✓ |
| `qbo_inventory_asset_account` | **Inventory Asset** ✓ (reserved; not posted yet) |
| `qbo_core_charge_liability_account` | **Customer Core Deposits** ✓ ($6,900 balance) |
| `qbo_surcharge_income_account` | *(none — create `Card Surcharge Income` (Income) or leave unset → falls back to Sales Income)* |

Other live facts that shape testing: QBO **Automated Sales Tax** with Colorado agencies (Broomfield, Commerce City, CO Dept of Revenue payables); `Vendor Core Charges` liability at **−$11,802.79** (bookkeeper review candidate); `COG - SKU Reclassification Clearing` and `Cost of Goods - Inventory Adjustment` used actively; QuickBooks Payments in use; Shopify connector accounts (`Channel sales`, `Shopify - jaks-diesel-3 Clearing Account`, `Channel Sales Tax Payable`).

**Good news:** live QBO already has every account the ERP needs. The sandbox is what needs building — live needs almost nothing (§5).

---

## 2. Shopify reconciliation punch list (pre-trial, ERP side)

1. **Reverse-link audit** — sweep all Shopify listings (active + draft) and report any with no ERP product pointing at them; feed genuinely-new ones through the existing import review queue (`/shopify/link-products` first — it matches by SKU/handle and is read-only).
2. **Resolve the 6 unlinked ERP products** (link, retire, or publish).
3. **Irregular-SKU fix list** (house-brand listings, mostly created Dec 2025–Jan 2026): fill the null-SKU variant, normalize stage-suffix collisions, verify `JAKS-4101420S3`-style SKU/title mismatches. Fix in place per D-A; update Shopify variant SKU where changed.
4. **OEM reclassification pass** — for house-brand listings whose titles lead with an OEM number, ensure that number exists as an OEM CrossReference on the ERP product (importer already has cross-vendor OEM dedup + hygiene filters).
5. **Customers:** enter the 13 Shopify customers manually only if they're real repeat trade accounts. The ERP customer master for go-live should mirror **QBO's customer list** instead — enter/verify ERP `company_name` **exactly equal** to the QBO customer DisplayName, because the QBO push binds by DisplayName and *refuses to sync on ambiguous multi-matches*. Action: dedupe customer DisplayNames in live QBO before cutover.

---

## 3. Build the sandbox to mimic live QBO — exact steps

The ERP stores account mappings **by account name**, so if sandbox account names exactly match live, the mapping config survives the sandbox→production switch with zero changes. That is the mirroring principle.

1. **Intuit Developer app** — developer.intuit.com (same Intuit login) → Create app → QuickBooks Online. Copy **Development** Client ID/Secret. Under Development → Redirect URIs add exactly: `http://localhost:8000/qbo/callback`.
2. **Sandbox company** — Dashboard → Sandbox (a US sandbox company is auto-provisioned).
3. **Set sandbox company address** to the real Commerce City, CO address (Settings → Company), then **enable Sales Tax** (Taxes → Set up) so Automated Sales Tax computes Colorado agencies like live.
4. **Create/verify these accounts in the sandbox, named exactly:**
   - Income: `Sales of Product Income`, `Services`, `Shipping Income`, `Discounts given`, *(optional)* `Card Surcharge Income`
   - COGS: `COGS - Diesel Parts`, `COGS - Freight In`
   - Expense: `Freight Out`
   - Other Current Asset: `Inventory Asset`
   - Other Current Liability: `Customer Core Deposits`, `Vendor Core Charges`
   - (Skip banks/credit cards/Channel accounts — the ERP never posts to them; payments land in Undeposited Funds.)
5. **ERP config** — Settings → QuickBooks: Environment = **Sandbox** (default), paste Development Client ID/Secret, Connect → authorize the sandbox company.
6. **One-time item setup** — click **“Set up QBO items”** (creates the 5 generic JAKS items and binds JAKS Core Charge to the core-liability account).
7. **Map accounts** — Settings → QBO Accounts: fill all seven keys with the names in §1's table.
8. **Tax setting** — leave `qbo_push_tax` ON initially. The sandbox is an AST company; the ERP already soft-retries without the tax override if AST rejects it. During week 1, compare ERP invoice tax vs sandbox-computed tax; if they diverge, that's a finding to resolve before live.
9. Customers/vendors need **no seeding** — the ERP creates them by DisplayName on first push.

---

## 4. 30-day sandbox trial protocol (locked D-D)

**Operate for real:** every counter/phone/fleet quote → SO → invoice → payment, and every PO → receive → vendor bill, entered in the ERP; each push lands in sandbox QBO (auto + 30-min retry worker).

**Weekly checkpoint (15 min):**
- Invoice list QBO column: zero red `✗ QBO Error` chips left unresolved (Settings → QuickBooks shows per-entity pending/failed/synced counts).
- Spot-check 2–3 documents in sandbox QBO: parts → Sales of Product Income, freight → freight accounts, core charges → Customer Core Deposits, surcharge as separate SalesReceipt, SKU visible in line descriptions.
- Payments correctly applied against their invoices.

**Month-end close (the gate):**
- ERP open-invoice total = sandbox QBO A/R **to the penny**.
- ERP vendor-bill total for the month = sandbox QBO A/P entries pushed.
- Tax: ERP invoice tax vs sandbox AST tax reconciled (or `qbo_push_tax` decision documented).
- Zero documents stuck in `error` after retries; retry ceiling (5) never silently exhausted without review.

**Pass = all of the above for the full 30 days → schedule cutover. Any miss → fix, restart the failing check for 2 more weeks.**

---

## 4b. How to run the trial in parallel with live QBO (no risk to live books)

**The sandbox is a physically separate QBO company** (realm `9341456051662686`) from live (`9341455493585007`). The ERP pushing to sandbox **cannot touch live QBO**. So the trial runs *alongside* your current setup with zero risk:

- **Live QBO keeps running exactly as today** — bookkeeper entries + the Shopify→QBO connector for web orders. It stays your source of truth for the whole trial.
- **In parallel ("shadow month"),** enter the same real counter/phone/fleet transactions into the ERP, which pushes to the *sandbox*.
- **At month-end, reconcile sandbox QBO against live QBO.** If A/R, sales, and COGS match, the ERP is proven to produce the same books as your current process — and only then do you cut over.

Nothing is "pushed over" from live to sandbox — you re-enter (or the ERP re-drives) the period's activity. The only true one-time data move happens at the **cutover** (§5), and inventory is the heart of it.

## 4c. Inventory reality (ground-truthed 2026-07-03) — the #1 transition task

The ERP→QBO push **never carries inventory quantities or on-hand.** Vendor bills post via `AccountBasedExpenseLine` straight to **COGS - Diesel Parts** (`qbo_service.py:697-726`); the Inventory-Asset field is explicitly *"Reserved — used when PO/bill inventory posting is enabled"* (not enabled). So this is a **periodic** inventory model in QBO (expense on purchase), whereas your live QBO today runs **perpetual** inventory via 225 items (Inventory Asset $68,623.81, COGS on sale). **That accounting-model change needs your bookkeeper's sign-off.**

Second, the ERP isn't holding your real stock yet:

| Metric | ERP `jaks.db` today | Live QBO |
|---|---|---|
| SKUs with on-hand > 0 | **4** | 225 inventory items |
| Total units on hand | **6** | — |
| Inventory valuation | **$13,593** (all `cost_source=receipt`, test receipts) | **$68,623.81** (Inventory Asset) |

The 4 stocked SKUs are development test receipts. Your real ~$68.6k of stock is **not in the ERP**. (And `jaks.db`'s catalog is rebuilt from scraper CSVs, which zeroes `qty_on_hand` — so on-hand needs a *durable* load, done after the catalog is stable.) The ERP has the right machinery to load it — `InventoryService.receive_without_po()` (sets qty + moving-avg cost → feeds valuation) and `adjust_inventory()` (MANUAL_ADJUSTMENT ledger) — but a **bulk physical-count → on-hand importer** is the missing piece and the gating task for go-live.

## 5. Cutover to live QBO (after the trial passes)

1. **Production keys** — in the Intuit app, complete the production questionnaire (privacy/EULA links), copy **Production** Client ID/Secret; add the redirect URI under Production settings too.
2. **Server prep** — set `JAKS_FERNET_KEY` env var (32-byte urlsafe-base64) **before** connecting live, so tokens are encrypted at rest. Run the backup → restore drill (owner decision O3).
3. **Live QBO prep (minimal, per §1):** optionally create `Card Surcharge Income`; dedupe customer DisplayNames; confirm bookkeeper is aware of the plan for `Vendor Core Charges` (−$11.8k) and inventory treatment going forward (periodic journal from ERP valuation vs live item tracking).
4. **Load real on-hand + retire per-part items (D-B) — careful order:**
   a. **Physical count → load into the ERP** (bulk importer or `receive_without_po`), so the ERP's on-hand + valuation reflect real stock. This is the gating task (§4c). Re-sync those levels to Shopify so all three systems agree.
   b. Record the ERP inventory **valuation = X** (Reports → Inventory Valuation).
   c. In live QBO, **zero out qty-on-hand** on the 225 inventory items via a qty adjustment **with the bookkeeper choosing the adjustment account** (deactivating items with nonzero QOH otherwise auto-posts an adjustment you didn't choose).
   d. Bookkeeper posts an adjusting **journal so Inventory Asset = X** going forward, and confirms the switch to a **periodic** model (new vendor bills expense to COGS; Inventory Asset maintained by periodic journal from ERP valuation, not by QBO items).
   e. Mark the legacy items **inactive** (they keep all transaction history). Leave the Shopify-connector items active (D-C).
5. **Flip the ERP** — Settings → QuickBooks: Environment = **Production**, paste production keys, Connect → authorize JAK's Diesel, LLC. Click **“Set up QBO items”** again (creates the 5 JAKS items in live). Account mappings need no edits (names match by design).
6. **First-week watch** — same weekly checkpoint as §4, daily for the first 3 days. Historical open A/R stays QBO-native; only new ERP documents flow.
7. **Rollback** — flip Environment back to Sandbox and reconnect; live QBO keeps whatever was pushed (void manually if needed); restore DB backup only for ERP-side issues.

---

## 6. Part-number education (the one-pager, per D-A)

| Number type | Example | Where it lives | Unique? |
|---|---|---|---|
| **JAKS SKU** | `JAKS-23525566`, `JAKS-G2-1266W` | `Product.sku` (and Shopify variant SKU) | **Yes — globally unique, one per sellable configuration.** Stage 1 vs Stage 3 head = two SKUs. |
| **OEM number** | `2239250`, `DD1501-145`, `A4720162120` | `CrossReference` (type OEM) | No — shared across many products by design; drives search + interchange. |
| **Vendor part number** | PAI `132057`, McBee `MCB…` | `ProductVendorSource.vendor_part_number` | Unique per (product, vendor). |
| **Competitor number** | HHP/ATL listings | `CompetitorPrice` / COMPETITOR cross-ref | No — market intel only, never cost, never SKU. |
| **QBO going forward** | — | SKU in the invoice-line *Description* only | QBO holds **no** part numbers as items → duplicates structurally impossible. |
