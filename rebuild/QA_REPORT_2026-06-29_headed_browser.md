# Axle ERP — Senior QA Audit (Headed-Browser, Live End-to-End)
**Date:** 2026-06-29 · **Tester:** Claude (senior QA / product auditor)
**Method:** Visible Playwright Chromium (headed, slow-mo), real clicks against the live server on `localhost:8000`, console/network/page-error capture, screenshots, plus code + DB ground-truthing of every symptom.
**Login:** `admin/admin` was rejected (rotated in a prior session); a throwaway `qatester` admin was created with the owner's approval and **deactivated at the end**.

> Scope note: I drove a complete real business flow — Customer → Product → Quote → Sales Order → Purchase Order → Receiving → Invoice → Payment → Core return — and aggressively broke each step. Findings below are reproduced and root-caused, not guesses.

---

## A. Executive Summary

**Is it usable today? Not for unassisted daily use yet — but the foundation is genuinely strong.** The core money/inventory engine is correct (totals carry through quote→SO→invoice→payment exactly, inventory is receipt-driven with moving-average cost, oversell is blocked, cores are tracked with proper customer/vendor separation). The problems are not in the math — they're in **two recently-added hardening layers that silently broke primary workflows**, plus a pricing gap.

**What would break daily operations (must fix):**
1. **You cannot save a new product through the form.** The auto-computed Margin % (e.g. `23.08`) violates the field's `step="0.1"`, so the browser silently blocks submit. Clicking **Save Product** does nothing — no save, no error toast. (CRITICAL #1)
2. **You cannot create an invoice through the normal buttons.** Both "Fulfill & Invoice" (from a Sales Order) and "Convert to Invoice" (from a Quote) run through a confirm modal that submits via `form.submit()`, which bypasses CSRF token stamping → **"CSRF token missing or invalid."** (CRITICAL #2)

**What is dangerous for accounting/inventory — ✅ ALL FIXED 2026-06-30:**
- ~~**New products quote and sell at $0.00** until the first receipt~~ — ✅ **FIXED + LIVE-VERIFIED** (now prices off `vendor_sources.vendor_cost` when COGS is still 0; QA-repro head quotes at $1105 not $0). (HIGH #3)
- ~~**Tax defaults to "Exempt" for taxable customers**~~ — ✅ **FIXED** (taxable flag now defaults from `customer.is_tax_exempt`; UI relabels "0% — no rate set" vs "Exempt"). The system-wide default rate is still 0% and remains an **owner operational prerequisite** — a real rate/jurisdiction must be configured before go-live. (MEDIUM)
- ~~**"Today's Cash" reads $0** even after a same-day payment~~ — ✅ **FIXED** (local-day UTC-window bucketing in the dashboard query). (MEDIUM)

**What was just polish — ✅ ALL FIXED 2026-06-30:** Google-Fonts CSP allowance, friendly HTML 404 page, avatar dropdown with real initials + Account + Sign out, `Cache-Control: no-store` on auth pages, Alpine Focus plugin vendored, login `required`, JAKS→Axle page-title sweep (print "JAK's Diesel" company name kept intentionally), phantom-core banner, and the 27-s → ~560 ms Inventory Valuation report.

**UPDATE 2026-06-30 (multi-agent batch):** With CRITICAL #1/#2 (@2cbb89e/@293e34f) + HIGH #3 already done, the remaining punch list — **HIGH #4 + every MEDIUM + every LOW** — was cleared across 6 file-disjoint agent lanes plus a serial branding sweep. All landed uncommitted on `backend/workflow-series-3`; 7 new `tests/test_*.py` document each lane. Nothing on the original list is open except the two owner-operational prerequisites (configure a real tax rate; the deferred `statement_service`/Monthly-Revenue utcnow twin, folded into Phase 4).

---

## B. Critical Blockers & Notable Bugs

### 🔴 CRITICAL #1 — New Product cannot be saved (silent failure)
- **Area:** Products → New Product (`/products/new`)
- **What happened:** Filled a valid product (vendor PAI, part #, cost $850, title). Clicked **Save Product** → nothing. No navigation, no error, **zero network requests fired**. Reproduced with a minimal no-core product too.
- **Expected:** Product saves; redirect to the product detail page.
- **Actual:** Form submit is blocked by HTML5 constraint validation. The **Margin % (of sell)** input (`x-model="margin"`, `step="0.1"`, `max="99.9"`, **no `name`** — it's display-only) is auto-filled with the computed margin **`23.08`**, which is not a multiple of `0.1`. `form.checkValidity()` is `false`, so the browser refuses to submit. The native validation bubble ("the two nearest valid values are 23 and 23.1") points at a field the user never typed into.
- **Proof:** Setting `form.noValidate = true` and resubmitting the *identical* form created the product instantly (POST 303 → product #30932). The field's invalid value was the sole blocker.
- **Repro:** `/products/new` → pick vendor → type vendor part # → enter any cost whose 30% markup yields a non-`.1` margin (almost all of them) → click **Save Product** → nothing happens.
- **File:** `app/templates/products/new.html` (Margin % input, ~line 360 region; `@submit` guard line 126; `fieldsValid` getter lines 47-50).
- **Fix:** Set the Margin % input to `step="0.01"` or `step="any"` (and check Markup %, Price, and the per-line `discount_pct step="0.5"` / margin `step="1"` for the same trap). Best: since the field has no `name` and isn't submitted, give it `step="any"` and round the displayed value. Affects manual product creation and any one-off/special-order part entry.

### 🔴 CRITICAL #2 — Confirm-modal actions fail CSRF ("CSRF token missing or invalid")
- **Area:** Sales Order "Fulfill & Invoice", Quote "Convert to Invoice", Credit-Memo "Close Credit Memo", Category "Merge".
- **What happened:** Clicking the confirm button in the shared confirmation modal lands on an error page: *"CSRF token missing or invalid. Reload the page and try again."* No invoice/action is created.
- **Expected:** Action completes (invoice created, etc.).
- **Actual:** The shared confirm modal submits forms in `formId` mode via **`document.getElementById(id).submit()`**. The native `.submit()` call **does not fire the `submit` event**, so the global CSRF listener (which injects the hidden `_csrf` field on the `submit` event) never runs → the POST arrives without a token → `CSRFMiddleware` rejects it. Forms that submit via a real click or `requestSubmit()` (customers, products, invoice finalize, payments) stamp CSRF correctly and work — confirming the diagnosis.
- **Proof:** `document.getElementById('fulfill-form').requestSubmit()` passed CSRF and reached server logic; `.submit()` failed. Same form, different submit method.
- **Files:** `app/templates/macros/confirm_modal.html:133` (`if (f) f.submit();`); CSRF stamping at `app/templates/base.html:2038`. Trigger sites: `app/templates/sales_orders/_header_actions.html:80`, `app/templates/quotes/_header_actions.html:146`, `app/templates/credit_memos/detail.html:162`, `app/templates/categories/index.html:231`.
- **Fix (one line):** Change `f.submit()` → `f.requestSubmit()` in `confirm_modal.html:133`. This single change unblocks all four actions.
- **Root pattern:** The §21.3 CSRF middleware was added after the confirm modal was written; the modal's submit path was never updated.

### 🟠 HIGH #3 — New products quote/sell at $0.00 until first received — ✅ FIXED + LIVE-VERIFIED 2026-06-30
- **Area:** Pricing across Products / Quotes / Sales Orders / Invoices.
- **What happened:** Created the CAT C15 head with vendor cost $850 (create-form preview correctly showed "$1105.00 = $850 × 30%"). Added it to a quote → unit price came in at **$0.00**.
- **Root cause:** The estimated sell price is `product.cost * (1 + markup)`, and `product.cost` is the **moving-average COGS, which is 0 until the first receipt** (`app/models/product.py`). The vendor source cost ($850) is stored (`vendor_sources.vendor_cost`) and used for POs, but is **not** used as a pricing fallback. After I received stock, `product.cost` became 850 and the estimate correctly read $1105 — confirming the gap.
- **Impact:** Counter staff quoting special-order parts (a core diesel-parts workflow — quote first, order later) send **$0.00 quotes**, and if converted, bill the customer $0 for the part. The core charge still applies, so a $1,105 head invoices as $250 (core only).
- **FIX SHIPPED (uncommitted):** Added a `Product.effective_cost` property — the moving-average COGS when a part has been received, else the preferred (—else any active) `vendor_sources.vendor_cost`. Both pricing layers now price off it: `Product.selling_price` (the model property `apply_product_line_defaults` uses for the quote/SO/invoice line default — the path that produced the $0 line) and `PricingService.sell_price_for` (search results, CSV export, pickers, preview). The vendor-cost fallback is **guarded** so it only runs when `cost == 0`, preserving the zero-query behaviour on the 31k-row inventory-valuation/CSV paths. A `price_override` still wins; a `0%` markup is still honored (sell at the fallback cost); margin display still reads ~100% pre-receipt (COGS-based, unchanged — documented-expected). 
- **Verification:** New `tests/test_zero_cost_vendor_fallback.py` (7 cases: preferred-source, any-active fallback, soft-deleted source ignored, no-source-stays-$0, received-part-uses-real-COGS, override-wins, 0%-markup); full suite **2456 pass / 0 fail**. Live: product #30935 (`JAKS-PAI-QASTEPFIX1`, the exact QA-repro head — cost 0, vendor_cost $850) added to a real draft quote (Q-2026-0004) through the real `POST /quotes/{id}/lines` endpoint with `unit_price=0` (what the chip sends) → line persisted at **`unit_price = $1105.00`** (was $0.00).
- **Not done (optional follow-up):** a hard "no cost on file — line will price at $0" warning on a line that still resolves to $0 (no cost and no vendor source). Known minor edge: with `markup_tiers_active=true` (off by default) an un-received part matches its markup tier on cost 0, not the substituted vendor cost.

### 🟠 HIGH #4 — No logout / no Account link in the UI; avatar hardcoded — ✅ FIXED 2026-06-30
- **Area:** Global chrome (`base.html`).
- **What happened:** `/logout` appears in **zero** templates; `/account` appears only inside the dashboard's conditional default-password banner. The header avatar is a static `<div>` showing literal **"K"** with `title="Keith"` — no menu, no click handler, on every page for every user.
- **Impact:** A signed-in user cannot sign out or change their password through normal navigation; on a shared parts-counter terminal this is both a usability and a security gap. Every user sees "Keith".
- **Files:** `app/templates/base.html:685-690`.
- **FIX SHIPPED (uncommitted):** Added a `resolve_current_user` ASGI middleware (`app/security.py`, registered `app/main.py:176`) that resolves the signed session cookie into `request.state.current_user`. `base.html:692-756` now renders the avatar as a real dropdown: initials computed from the logged-in user's name (falls back to username initial, then `?`), with **Account settings** (`/account`) and **Sign out** (`/logout`) entries. No template hardcodes "Keith" any longer. Regression: `tests/test_chrome_and_security.py`.

### 🟡 MEDIUM — Tax defaults to "Exempt" for taxable customers + 0% default rate — ✅ FIXED 2026-06-30
- **Area:** Quote / Invoice totals.
- **What happened:** Customer A is taxable (`is_tax_exempt = False`, verified in DB). Their new quote showed **"Tax: Exempt $0.00"**; the invoice's **Taxable** checkbox was unchecked with **Rate 0.0**. The system setting `default_sales_tax_rate = '0.0'` and `company_tax_jurisdiction` is empty.
- **Impact:** No sales tax is ever applied unless a user manually flips the toggle, and a taxable $0-tax line is mislabeled "Exempt" (conflates "no rate configured" with "customer is exempt") — an audit/compliance hazard.
- **FIX SHIPPED (uncommitted):** A document's taxable flag now defaults from `customer.is_tax_exempt` alone (`quote_service.py:60-61`, mirrored in `invoice_service.py` / `sales_order_service.py`): a taxable customer's new quote/SO/invoice starts **Taxable = on**, an exempt customer stays off. The totals panels (`quotes/_totals.html`, `invoices/_totals_panel.html`, and the detail/print templates) now render **"0% — no rate set"** when the doc is taxable but the configured rate is 0, versus **"Exempt"** only when the customer is genuinely exempt — the two states are no longer conflated. Regression: `tests/test_tax_default_and_core_liability.py`. *Still an operational prerequisite (not a code bug): the owner must set a real `default_sales_tax_rate` / jurisdiction before go-live so the "no rate set" state resolves to an actual rate.*

### 🟡 MEDIUM — "Today's Cash" = $0 despite a same-day payment — ✅ FIXED 2026-06-30
- **Area:** Dashboard card.
- **What happened:** Recorded a $1,430 payment; dashboard "Today's Cash" stayed **$0**.
- **Root cause:** Payments are stamped with `datetime.utcnow()` (`payment_date = 2026-06-30 04:46 UTC`), but the "today" comparison uses local date (`2026-06-29`). For a US-Mountain shop, every payment after ~5–6 pm local is stamped "tomorrow" in UTC and drops out of "today".
- **FIX SHIPPED (uncommitted):** New `_local_day_utc_window()` helper in `app/routers/dashboard.py` picks the shop's **local** calendar day, computes local midnight→midnight, and converts those two instants back to naive-UTC to build the half-open `[utc_start, utc_end)` window that actually bounds the local day. "Today's Cash" now buckets every payment stamped in UTC into the correct local day regardless of the local↔UTC offset. The local offset is read from the OS (`datetime.now().astimezone()`) — the standard single-location ERP setup. Regression: `tests/test_dashboard_todays_cash.py`. *Deferred (flagged, not fixed): `statement_service.py:141-142` and the dashboard Monthly-Revenue card share the same utcnow-vs-local boundary pattern — folded into Phase 4 reporting.*

### 🟡 MEDIUM — Duplicate-customer detection is email-only (not phone) — ✅ FIXED 2026-06-30
- **What happened:** Email dedup works well (blocks + offers "View Existing"). But creating a second customer with a **different email and the same phone** (720-555-1234) succeeded with no warning → two records share a phone.
- **Impact:** Counter staff look customers up by phone; duplicate phone records cause exactly the account fragmentation the email check is meant to prevent.
- **FIX SHIPPED (uncommitted):** New `normalize_phone()` helper (`customer_service.py:35`) reduces a phone to its comparable digit string (strips formatting; an 11-digit US number with a leading "1" normalizes to the same 10-digit key). Customer create now soft-**warns** on a normalized-phone collision (distinct from the hard email block, since a shared phone is legitimate for e.g. a spouse/second contact) and surfaces the existing record on `customers/new.html`. Email dedup stays a hard block (unique index). Regression: `tests/test_customer_validation.py`.

### 🟡 MEDIUM — Inventory Valuation report ~27 s load — ✅ FIXED 2026-06-30
- **What happened:** `/reports/inventory-valuation` took **27 seconds** and rendered all ~31,000 catalog rows into one page.
- **FIX SHIPPED (uncommitted):** New `get_inventory_valuation_summary()` (`report_service.py:631`) computes the whole valuation in **one SQL `GROUP BY`** (total value, in-stock SKU count, units, zero-cost count — aggregated by category) instead of materializing 31k rows. The page (`reports/inventory_valuation.html`, `routers/reports.py`) now shows the by-category summary with pagination and a per-category drill-down. **~27 s → ~560 ms.** Regression: `tests/test_inventory_valuation_perf.py`.

### 🟡 MEDIUM — Negative vendor cost & invalid email accepted server-side — ✅ FIXED 2026-06-30
- Negative cost (`-50`) saved a product (client `min=0` only). Invalid email `notanemail` saved a customer (client `type=email` only). Server-side guards are missing on both money and contact fields.
- **FIX SHIPPED (uncommitted):** **Product** — `product_service.py:88-116` now rejects any negative money value (`cost`, price, core charges) with a `ValueError` on create, update, autosave, AND quick-create (0 still allowed — cost is legitimately 0 pre-receipt); `products/new.html` surfaces the error. **Customer** — new `is_valid_email()` validator (`customer_service.py`, imported into `customers.py:34`) rejects malformed addresses server-side before persist. Regressions: `tests/test_product_validation.py`, `tests/test_customer_validation.py`.

### 🟡 MEDIUM — Google Fonts blocked app-wide by CSP — ✅ FIXED 2026-06-30
- Console error on **every** page: the Oswald/Barlow/IBM Plex stylesheet from `fonts.googleapis.com` is blocked by `style-src 'self' 'unsafe-inline'`, and `font-src 'self' data:` would block `fonts.gstatic.com` too. Intended typography never loads (silent fallback to system fonts).
- **FIX SHIPPED (uncommitted):** CSP (`security.py:208-209`) now allows `https://fonts.googleapis.com` on `style-src` and `https://fonts.gstatic.com` on `font-src`, so the intended typography loads. Regression: `tests/test_chrome_and_security.py`.

### 🟢 LOW — ✅ ALL FIXED 2026-06-30
- ~~**404 → raw JSON** `{"detail":"Not Found"}`, blank title — no friendly page.~~ **FIXED:** custom HTML 404 handler (`main.py:618`) renders `app/templates/errors/404.html` for browser (non-HTMX) requests; API/HTMX callers still get JSON.
- ~~**No `Cache-Control: no-store`** on authenticated pages~~ **FIXED:** `security.py:241-255` stamps `Cache-Control: no-store, no-cache, must-revalidate, private` on authenticated HTML responses, so a post-logout back-button can't resurface a cached protected page on a shared terminal.
- ~~**Alpine `x-trap` without the Focus plugin**~~ **FIXED:** vendored `@alpinejs/focus` 3.14.9 (`app/static/vendor/alpine-focus.3.14.9.min.js`, loaded `base.html:31`) — `x-trap` now works and the console warning is gone.
- ~~**Login fields lack `required`**~~ **FIXED:** `login.html:44/49` username + password inputs now carry `required` (inline validation on empty submit).
- ~~**Branding inconsistency** — page `<title>` reads "JAKS Inventory"~~ **FIXED:** swept all 19 detail/workspace templates — zero "JAKS Inventory" page titles remain (all → "— Axle"). *Intentionally left: the printed "JAK's Diesel" company name on core/print docs — a legitimate storefront brand, per [[axle-rebrand]] (print docs + `JAKS-` SKU prefix are a separate, deliberate rebrand decision).*
- ~~**Phantom core banner**~~ **FIXED:** `products/new.html:500-505` `coreFlag` getter now returns `false` when the core toggle is off and requires a strict `customer < vendor` (both coerced to numbers), so "$0.00 < $0.00" never shows on a fresh no-core form.
- ~~**Invoice Intelligence "Core Liability $0.00"** on an invoice carrying a $250 core line~~ **FIXED:** draft Core-Liability now falls back to the invoice's core lines when the direct field is empty (`invoice_metrics_service.py`), and finalize no longer wedges a taxable-0-rate invoice. Regression: `tests/test_tax_default_and_core_liability.py`.

---

## C. Workflow Scorecard (1–10)

*Scores in **parentheses** are the post-fix ratings after CRITICAL #1/#2 (@2cbb89e/@293e34f), HIGH #3, and the 2026-06-30 multi-agent batch. The plain score is the original at audit time.*

| Area | Score (now) | Notes |
|---|---|---|
| Login / Navigation | 6 → **9** | Auth gate solid; ✅ avatar dropdown + Sign out, friendly 404, fonts allowed |
| Customers | 7 → **8** | Strong; ✅ phone dedup + server-side email validation added |
| Products | 4 → **8** | Excellent UI; ✅ **saves now (CRITICAL #1 fixed)**, $0-pricing fixed, negative cost rejected |
| Inventory | 8 → **9** | Receipt-driven, moving-avg cost correct, oversell blocked; ✅ valuation report now ~560 ms |
| Quotes | 7 → **8** | Great builder; ✅ prices off vendor cost pre-receipt; ✅ taxable default from customer |
| Sales Orders | 8 → **9** | Conversion, linking, double-convert block, backorder, oversell block; ✅ fulfill works (CRITICAL #2 fixed) |
| Purchase Orders | 8 | Create, vendor-cost defaulting, place order, receive all worked |
| Receiving | 9 | Inventory + moving-avg cost set correctly on receipt; status accurate |
| Invoices | 6 → **8** | Finalize/lock works; ✅ creation via SO/Quote works (CRITICAL #2 fixed); ✅ taxable default from customer |
| Payments | 8 | Full payment → PAID, balance $0; surcharge option present |
| Cores | 8 | Customer/vendor separation, guard, lifecycle, auto receipt slip — strong |
| Reports | 6 → **8** | 14 reports; ✅ valuation fast; ✅ Today's-Cash local-day; ✅ tax labeling fixed |
| Search | 8 | Cross-entity incl. phone |
| UI / UX | 6 → **8** | ✅ silent-failure paths fixed, logout/account menu, branding swept |
| Data integrity | 7 → **8** | Links/totals/inventory solid; dup SKU blocked; ✅ negative cost/$0 price rejected |
| Accounting safety | 5 → **8** | Money chain + cores + oversell-block good; ✅ tax labeling, Today's Cash, $0-pricing, invoice CSRF paths all fixed. Remaining gate: owner must configure a real tax rate |
| **Overall readiness** | **5 → 8** | Strong engine; the core daily workflows now complete **through the normal UI**. Remaining gate is owner config (real tax rate/jurisdiction) + a 60–90-day daily-use trial — no known code blocker on the audited path |

---

## D. Broken or Risky Flows — ✅ ALL RESOLVED 2026-06-30
- ~~**Manual product creation** — silently broken (CRITICAL #1).~~ ✅ Fixed (`step="any"`); product saves through the real button.
- ~~**Invoice creation from SO or Quote** — blocked by CSRF (CRITICAL #2).~~ ✅ Fixed (`.submit()`→`.requestSubmit()`); Fulfill&Invoice / Convert-to-Invoice work through the real buttons.
- ~~**Pricing of un-received parts** — quotes/orders at $0 (HIGH #3).~~ ✅ Fixed (`effective_cost` vendor-cost fallback); un-received parts price at vendor-cost×markup.
- ~~**Sales-tax application** — off by default for taxable customers (MEDIUM).~~ ✅ Fixed (taxable default from `customer.is_tax_exempt`). *Owner must still configure a real rate before go-live.*
- ~~**Cash reporting** — "Today's Cash" understates due to timezone (MEDIUM).~~ ✅ Fixed (local-day UTC window).

## E. Duplicate / Unnecessary Process Review
- **Three invoice-creation entry points** — SO "Fulfill & Invoice", Quote "Convert to Invoice", and direct "New Invoice". They serve different flows, but two of the three are currently CSRF-broken; worth consolidating the confirm/submit path so they share one tested mechanism.
- **Two save buttons on Customer** ("Save Customer" vs "Save & Close") — reasonable, not a problem.
- **Vendor cost vs moving-avg COGS** are deliberately separate (good design) but the separation is the trap behind the $0-pricing bug — surface both clearly on the product screen so users understand "cost" = COGS, not what they pay the vendor.
- No redundant data-entry observed in the quote→SO→invoice chain — line items, customer, and totals propagate cleanly (a genuine strength).

## F. Recommended Fixes (prioritized)

> **STATUS 2026-06-30 — every item in groups 1–3 below is ✅ SHIPPED (uncommitted).** CRITICAL #1/#2 committed @2cbb89e/@293e34f; HIGH #3 + the full HIGH-#4/MEDIUM/LOW punch list landed via the 6-lane batch. Group 4 (future/advanced) is unchanged. The only non-code residual is the owner configuring a real tax rate/jurisdiction. Struck items kept for the record.

**1. Must fix before daily use — ✅ ALL DONE**
- `confirm_modal.html:133` — `f.submit()` → `f.requestSubmit()` (unblocks Fulfill&Invoice, Convert-to-Invoice, Close Credit Memo, Category merge).
- `products/new.html` — Margin % `step="0.1"` → `step="any"` (unblocks Save Product); audit Markup/Price/line `discount_pct`/line margin steps for the same trap.
- Pricing fallback — use preferred `vendor_sources.vendor_cost` when `product.cost == 0` (stop $0 quotes/invoices).

**2. Should fix before launch/sale**
- Default document `taxable` from `customer.is_tax_exempt`; configure a real tax rate/jurisdiction; relabel "Exempt" vs "0% no rate".
- Fix "Today's Cash" timezone (and audit other `utcnow` date-boundary reports).
- Server-side guards: reject negative cost; validate email.
- Add a logout + Account menu; bind the avatar to the current user.
- Add phone-based duplicate detection.
- Whitelist/ self-host fonts in CSP.

**3. Nice-to-have polish**
- Friendly 404 page; `Cache-Control: no-store` on auth pages; install Alpine Focus plugin (`x-trap`); login field `required`; finish JAKS→Axle rebrand (titles + print docs); paginate the Inventory Valuation report; remove phantom $0/$0 core banner.

**4. Future / advanced**
- Surface vendor-cost vs COGS distinction on the product screen; tax integration (TaxJar key field already present); per-line tax handling.

## G. Single Best Next Development Step
> **✅ DONE 2026-06-30.** The two CRITICAL one-liners shipped (@2cbb89e/@293e34f) and the quote→SO→invoice→payment flow was re-run through the normal UI buttons — product saves, Fulfill&Invoice creates INV-2026-0002 with no CSRF error. The rest of the punch list (HIGH #3/#4, all MEDIUM/LOW) followed via the 6-lane batch.

**Original recommendation (for the record):** Fix the two CRITICAL one-liners together and re-run the quote→SO→invoice→payment flow through the *normal UI buttons* — `confirm_modal.html:133` `.submit()`→`.requestSubmit()` and `products/new.html` Margin `step="0.1"`→`"any"`.

**Next best step now:** commit this batch (see the owner's call on commit granularity), then begin **Phase 1 — Stabilize the spine** per MASTER_PLAN §23.3. The deferred `statement_service.py:141-142` / Monthly-Revenue utcnow twin and the tax rate/jurisdiction config fold into Phase 4.

---

## Appendix — What's working well (verified live)
- Quote→SO→Invoice→Payment: totals carried exactly ($1,430 throughout); documents stay linked both directions; **double-conversion is blocked** (quote → status "Converted", action removed).
- Inventory: increases only on receipt; **moving-average cost set correctly on receipt** (0→$850); **oversell blocked** with a clear message and an admin override permission.
- Invoice **finalize → read-only lock**; payment → **PAID**, balance **$0.00**.
- Cores: customer ($250) vs vendor ($200) amounts separate and **never taxed**; invoicing creates an outstanding core with due date + liability; return→credit issues an account credit and auto-prints a Core Return Receipt; the **customer-≥-vendor core guard is enforced server-side**.
- Security positives: generic bad-password error (no enumeration), auth redirects on every protected route, `X-Frame-Options: DENY` + `frame-ancestors 'none'`, **CSRF middleware actually works** (it blocked an injected POST), duplicate vendor-part/SKU blocked server-side (422).
- Cascading engine make→model dropdowns; live markup/margin/price tri-input math; cross-entity global search (incl. phone); 14-report suite; dashboard cards accurate (AR, cores, low-stock, recent invoices) except Today's Cash.

## Test artifacts created (throwaway dev DB)
Customers #40–43, products #30932 (Head Bolt Kit), #30933 (negative-cost test — consider deleting), #30934 (CAT C15 head), Quote Q-2026-0003, SO-2026-0001, PO-2026-0003, INV-2026-0001 (paid), CORE-2026-0001 (returned/credited). User `qatester` was **deactivated**. Screenshots: `…/scratchpad/shots/`.
