# Pricing · Warranty · Add-ons — Design Decisions
**Status:** DRAFT from owner brainstorm 2026-06-07 (Keith). Captures locked decisions + open items so the lanes can build to one picture. Supersedes scattered behavior in the current code where noted.

---

## 1. PRICING

### Locked
- **Keep the imported sell prices as-is.** The ~13k PAI parts keep their imported Shopify prices (`product.price_override`). The system does **not** recompute or overwrite them.
- **Cost-bracket tiers = TARGET MARGIN, not a price-setter.** The cost-bracket grid (today `MarkupTier`, currently disabled) is repurposed as **target margin by cost bracket**. It is a guardrail/health indicator and the default for parts with no price — never an override of a real price.
- **Margin = % of sell price** (not markup on cost). Sell = `cost ÷ (1 − margin%)`. Example: $100 cost at 40% margin → $166.67.
- **Margin awareness:** flag a part when its actual margin (sell vs moving-avg COGS) is **under** its bracket's target margin.
- **Competitor awareness:** flag a part when a **competitor's** price (the `CompetitorPrice` data the importer already collects) is **below** our sell price. *(Owner clarified: competitors, not suppliers. Supplier cost still feeds COGS/margin math underneath, but it is not the alert.)*
- **Per-line override always allowed** — the counter can set any line price.

### Margin brackets — CONFIRMED (target margin = % of sell price)
| Cost bracket | Target margin |
|---|---|
| $0 – 25 | 50% |
| $25 – 100 | 45% |
| $100 – 500 | 40% |
| $500 – 2,000 | 35% |
| $2,000 – 3,000 | 30% |
| $3,000+ | 25% |

Owner-approved 2026-06-07. Sell price stays the imported price; these drive only the **margin-vs-target badge** and the default price for parts that have none.

### Build implications
- The audit's "30% hardcode" problem is solved by this model **differently**: since prices are kept, the fix is to **stop rendering `Product.selling_price` (the 30% guess) as the price** on list/preview, and instead show the **imported price + a margin badge vs the bracket target**. (`product.py:300`, `products/list.html:432`, `_preview_panel.html`.)
- Repurpose `MarkupTier` rows + the pricing-grid UI as **margin targets**; the cost-bracket grid stays informational (not flipped on to overwrite prices).
- Surface two badges on a part/line: **margin-vs-target** and **competitor-undercut**. Both are read-only signals; neither changes the price automatically.

---

## 2. ADD-ONS (suggested parts) — the model the owner wants

### Locked
- **One concept: "suggested parts."** They populate **right below the product line**, exactly like the current chip behavior the owner likes. Keep that behavior.
- **The `+` offers two actions:**
  1. **Add to quote** → firm line, **counted in the total** ("customer said yes").
  2. **Add as option** → shows on the customer's quote as an add-on they *could* take, **NOT in the total** ("here's the option").
- **Drop the required / recommended / optional taxonomy** (`SuggestedSellType`) and the dead/confusing roles. Collapse to: *suggested parts* + the two add modes. No auto-add, no locked kits.
- **Goal = fast upsell:** easy to add onto the quote, or easy to show the customer the option on the quote.

### Source of suggestions — "the RIGHT way" (3-layer; owner asked for AI help)
1. **AI-generated companion rules + kit sets (backbone).** An agent reads the catalog — category tree + `engine_manufacturer`/`engine_model` + `ProductApplication` fit — and **proposes** companion links ("cylinder heads → head-bolt kits + gasket sets in the same engine family"). Owner **reviews/approves**; nothing auto-commits. Reuses the empty `ProductKit`/`ProductKitLine` scaffold.
2. **Per-product manual override** for exceptions + top sellers.
3. **Bought-together history** layered in later, once real invoices accumulate.
- **Start narrow:** generate one part family first (e.g. cylinder heads) to validate quality before turning it loose on 13k parts.

### Print
- "Add as option" lines render in a **customer-facing "Options / Add-ons" section**, clearly *not* in the total.

### Alternatives — KEEP (owner confirmed: he quotes good / better / best)
There are **two distinct axes**, do not merge them:
- **Additive (suggested parts):** "add this *too*" → the `+` two-mode behavior above.
- **Alternative (good / better / best):** "pick *one* of these *instead*" → the existing `upgrade_option` mechanism (Economy/Recommended/Premium) maps directly to good/better/best. Selecting one makes it the active line and drops the others. Keep this; relabel to **Good / Better / Best** for clarity.
- The AI sourcing pass (below) should also propose the good/better/best **sibling groups** per part (e.g. economy reman ↔ standard reman ↔ new), the same way it proposes companion parts.

---

## 3. WARRANTY (extended, the paid upsell)

### Locked
- **Two warranties stay distinct:** supplier/vendor (free, what the vendor includes) vs **JAKS extended** (paid upsell child line).
- **Price = part line price × `warranty_percentage` × (months ÷ 12)** — keep the current formula.
- **Coverage = Parts + Labor.**

### Recommended (owner to confirm)
- **`warranty_percentage` — CONFIRMED: default 10%, counter can override higher at quote time** (e.g. on heads/long blocks where a Mitchell 1 R&R at $45/hr may exceed what 10% covers). Keep it editable on the line.
- **Tiering: ALL tiers = Parts + Labor** (owner confirmed) — coverage no longer varies by term; longer terms just extend the window.
- **Labor mechanic — CONFIRMED: Mitchell 1 book hours × $45/hr.** Reimburse the Mitchell 1 standard repair time at a flat $45/hr JAKS rate. **Book time is the cap** — you pay standard time, never the shop's padded actual invoice. No separate per-part dollar cap needed; the SRT bounds every claim.
- **Non-negotiable guardrails** for parts+labor:
  1. **Pre-authorization** before the work (verify ESN, original invoice, in-window, failure type).
  2. **Failed part returned + inspected** — routes through the existing RA/core flow; how you reject install-error / no-fault-found.
  3. **Written exclusions** (misinstallation, abuse, consequential damage, no-fault-found); coverage **keyed to part class**.

### Labor = REIMBURSE THE OUTSIDE SHOP — Mitchell 1 book hours × $45/hr (confirmed)
The customer's shop does the R&R; JAKS reimburses **Mitchell 1 standard repair time × $45/hr**. Real cash out on **every** parts+labor claim (all tiers now include labor), so:
- Pay on **Mitchell 1 book hours**, never the shop's raw invoice — book time is the cap.
- **Pre-authorization + failed-part-return (via the RA flow) + written exclusions** (install error, abuse, no-fault-found) are mandatory on every claim.
- The **`% × term` price must cover expected labor** — 10% default is overridable upward at quote time on big-labor parts.

### Build gaps to close (from the audit, regardless of the above)
- **Store the tier sold** structurally (not just a description string) so "how many 24-mo parts+labor did we sell?" is queryable. Tiers should be **admin-configurable**, not hardcoded in `_line_row.html:531-537`.
- **Carry warranty lines through quote → SO** (currently dropped at `quote_service.py:381`; only survives quote → invoice).
- **At claim time:** capture **ESN** + **`warranty_type` (vendor vs jaks_extended)** on `WarrantyClaim`, and **link the claim back to the sold warranty line**, so staff know it's covered and on what terms.
- **Model a warranty cost/reserve** — today `unit_cost = 0` on every warranty line (shows 100% margin). Parts+labor makes the real expected cost material; set a reserve so the `% × term` is sanity-checked against expected payouts.

---

## 4. Spec COMPLETE — all inputs decided (2026-06-07)
- **Margin brackets:** confirmed (§1) — 50 / 45 / 40 / 35 / 30 / 25% across the six cost brackets.
- **Warranty tiering:** all tiers parts + labor.
- **Warranty labor:** reimburse outside shop at **Mitchell 1 book hours × $45/hr** (book time = the cap).
- **Warranty %:** 10% default; counter can override higher at quote time.
- **Good / Better / Best:** kept as a distinct "pick one instead" axis, separate from additive suggestions.

*Nothing built yet — agreed direction, ready for tickets: pricing margin-vs-target + competitor-undercut badges; unified suggested-parts `+` two-mode add; Good/Better/Best relabel + AI sibling groups; warranty all-tiers parts+labor + admin-config tiers + carry quote→SO + claim ESN/link + Mitchell 1 $45/hr reimbursement.*
