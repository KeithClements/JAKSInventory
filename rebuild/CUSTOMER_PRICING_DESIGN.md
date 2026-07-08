# Customer-Specific Product Pricing — Design Decisions

**Status:** SPEC from owner interview 2026-06-18 (Keith). All key decisions locked via interview; ready for tickets. Nothing built yet.

This is the "set a price on certain products for a specific customer" feature — the per-customer, per-product (and per-category/brand) pricing layer the ERP currently lacks.

---

## 0. The gap today

The pricing engine works but has **no customer×product layer**. Current line-price resolution order:

1. `product.price_override` (hard sell price)
2. tier discount (`standard / wholesale / fleet / dealer`, each a configurable % off)
3. per-product `markup_pct`
4. cost-bracket `MarkupTier` (if `markup_tiers_active`)
5. `default_markup_pct` (30%)

Customer-level levers are blunt: `Customer.pricing_tier` and `Customer.discount_pct` (flat % off everything). Competitor prices are collected but only used as a signal, never to set price. **No table exists for a per-customer, per-product/category price.** This feature fills that gap.

---

## 1. Locked decisions (from interview)

| Topic | Decision |
|---|---|
| **Price method** | Cost-based only. Enter as **markup%** OR **margin%** per rule (interconvertible; stored canonically). **No** fixed-dollar, **no** %-off-list. Every price rides the moving-average `cost` and self-corrects on receipt. |
| **Margin guard** | Resolved price's margin% is compared to the existing cost-bracket targets (50/45/40/35/30/25). Below target → **warn (red badge), proceed allowed**. Below cost → stronger warn. **Never blocks.** |
| **Scope levels** | Three: **specific PRODUCT (SKU)** → **CATEGORY / BRAND line** → **whole CUSTOMER**. (Upgrades `Customer.discount_pct`: a whole-customer deal can now be cost-plus, not just %-off.) |
| **Precedence** | **Most-specific wins, but show both.** SKU beats brand/category beats whole-customer. The overridden runner-up is surfaced on the line ("SKU rule $420 overrides All-Turbos $445"). |
| **Expiry** | **Open-ended by default, optional** `effective_from` / `effective_to`. Dated rules auto-expire. |
| **Quantity breaks** | **Build it in** — optional `qty_min` per rule. |
| **Last price** | When no rule matches, **show "Last: $X · margin% · Inv# · date" as a click-to-apply hint. Never auto-fill.** |
| **Entry workflow** | Customer-page rule editing + bulk "Quick Deal" category rules + last-price hint. **No CSV import.** |
| **Applies to** | Quotes, Sales Orders, Invoices (the document waterfall). Shopify storefront out of scope. |

---

## 2. Architecture — one new table, slotted as "Step 0"

The existing waterfall is untouched. A new lookup is inserted at the top:

```
0. CustomerPriceRule   ← NEW (this feature)
1. product.price_override
2. tier discount
3. product.markup_pct
4. MarkupTier
5. default_markup_pct
```

If a rule matches → it sets the price. If not → current behavior is unchanged. Fully backward-compatible. Existing `tier` + `discount_pct` remain as the general fallback when no rule matches.

---

## 3. Data model — `customer_price_rule`

```
id
customer_id        FK customers (indexed)
scope_type         PRODUCT | CATEGORY | BRAND | CUSTOMER
scope_ref          product_id / category id|path / vendor id — NULL for whole-customer
price_method       'markup' | 'margin'        (user enters either)
price_value        float   (e.g. 12 = cost+12%, or 30 = 30% margin)
qty_min            float NULL  (volume break threshold; NULL = any qty)
effective_from     date NULL   (NULL = open start)
effective_to       date NULL   (NULL = open end; auto-expires when past)
note               str
is_active          bool
created_by         str
created_at         datetime
updated_at         datetime
```

New table → auto-created by `create_all()`. **Verify before build:** whether the ERP now uses Alembic ("inline list frozen" per memory @8527d79) or still the inline `_PENDING_COLUMN_ADDITIONS` in `database.py` — current code shows the latter. New table works either way; any new columns on *existing* tables must follow whichever is canonical.

---

## 4. Resolution engine — `PricingService.resolve_customer_price(product, customer, qty=1, as_of=today)`

1. Gather customer's rules matching the product: by `product_id`, by its category, by its brand/vendor, or whole-customer.
2. Filter: `is_active`, date window (`effective_from ≤ as_of ≤ effective_to`, NULLs open), `qty_min ≤ qty` (NULL = any).
3. Rank by **specificity: PRODUCT > BRAND > CATEGORY > CUSTOMER** (a fixed ladder). Within a scope: higher qualifying `qty_min` wins, then newest.
4. Winner → compute price from `price_method`/`price_value` against current `cost`.
5. Return the **runner-up** (next scope down) too, for "show both."
6. Compute resolved margin% vs the bracket target → `below_target` / `below_cost` flags.

**Returns:** `{ price, source_rule, overridden_rule, margin_pct, below_target, below_cost }`

**Brand-vs-category order (DECIDED 2026-06-19):** when both a BRAND and CATEGORY rule hit the same part, the **BRAND deal wins** — a fixed `PRODUCT > BRAND > CATEGORY > CUSTOMER` ladder (predictable beats "narrowest scope wins"); the category rule is surfaced as the overridden runner-up. (Implemented in `PricingService._SCOPE_RANK`; the earlier fewest-SKUs tiebreak was removed.)

### Last-price hint — `PricingService.last_price_for(customer, product)`
Most-recent finalized invoice line for this customer+product → `{ unit_price, margin_pct, doc_ref, date }`. Display-only, click-to-apply. Never auto-fills.

---

## 5. UI surfaces

1. **Customer detail → "Pricing & Deals" panel:** lists the account's rules with a **live margin-at-current-cost** preview (red if under target); inline add/edit (scope picker, markup/margin toggle, value, optional qty_min, optional dates, note). The "as I go" path.
2. **Bulk "Quick Deal" form:** "[Customer] gets [30% margin | cost+12%] on [category/brand], effective [dates]." One rule blankets hundreds of SKUs — the maintainable backbone. Reuses category-tree / engine-picker macros.
3. **Quote / SO / Invoice line:** `unit_price` auto-resolves (still editable per existing pattern) + a chip naming the source deal + margin-warn badge + overridden-runner-up note. When no rule: the last-price hint. Reuses `_line_row.html` + existing margin badge + quote-chip machinery.
4. **Print / customer-facing docs:** resolved price as the line price. Default **silent** (just the number — cost-plus shops don't reveal markup). Optional setting to label it "Your account price."

---

## 6. Edge cases

- **cost = 0 / no cost yet:** cost-plus can't compute → fall through to normal waterfall + "no cost" flag. No divide-by-zero, no $0 sale.
- **Rule vs `price_override`:** customer rule (Step 0) wins (more specific intent). Confirm at build.
- **Cores:** rule applies to the part line only; core charge stays its own line, untouched.
- **Expired/inactive rules:** ignored in pricing, kept for history/audit.
- **Qty changes after line added:** re-resolve on qty change (and on add).

---

## 7. Phased build plan (mapped to lanes)

**Phase 1 — backbone (MVP):**
- Backend: `customer_price_rule` model + migration; `resolve_customer_price`; wire into `add_line` for quote/SO/invoice; rule CRUD routes; tests.
- UI-Architect: rule chip + margin-warn badge (reuse) + customer "Pricing & Deals" panel layout macros; governance.
- UI-Builder: customer-detail panel + line-row chip wiring.
- QA: precedence matrix (SKU>brand>category>customer, qty breaks, date windows, below-target, below-cost, cost=0), override-of-price_override.

**Phase 2 — workflow polish:**
- Bulk "Quick Deal" form; `last_price_for` hint; "show both / overridden" chip.

**Phase 3 — visibility:**
- Deals report (active rules, margin-at-cost, expiring-soon); margin-leakage report; optional print-label setting.

---

## 8. Why this shape (research basis)

Mature distribution ERPs (NetSuite, Epicor, Acumatica, Cetec) resolve price via a **most-specific-to-most-general precedence ladder** and warn against hand-maintaining per-SKU prices across a large catalog — do the heavy lifting at the **customer-class and category level**, per-SKU only for exceptions. Date-effective pricing and an audit trail are table stakes. This design follows that: category/brand rules are the backbone, SKU rules the scalpel, cost-based math keeps every price honest as costs move, and the margin badge is the "don't lose your margin" guardrail.

Sources: NetSuite Pricing Management; Bizowie "Managing Customer-Specific Pricing"; ElevatIQ "Top Practices for Pricing and Discounts in ERP".
