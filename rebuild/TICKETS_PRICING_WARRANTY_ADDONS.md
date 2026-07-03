# Lane Tickets — Pricing · Warranty · Add-ons
**From** `PRICING_WARRANTY_ADDONS_DESIGN.md` (decided 2026-06-07). Code-grounded against the current tree. **Nothing built yet.**

---

## ⚠️ Build order & collision coordination (read first)

**Three tickets (#2 add-ons, #3 good/better/best, #4 warranty) all edit `app/templates/quotes/_line_row.html`, which is already DIRTY** with another lane's in-flight work. Do **not** run them in parallel on that file. Sequence it:

**Phase A — start now, in parallel (no `_line_row.html` contention):**
- **#1 Pricing** — fully independent (pricing_service + products routes/templates + settings seed). Go.
- **#5 AI sourcing pilot** — all new files, zero collision. Go.
- **Backend seams of #2 + #4** — the non-template parts: #2's `get_inline_chips` filter + the new `/option-add` route; #4's `WarrantyTier` table + service + warranty router/templates + the `convert_to_sales_order` warranty carry (rebase onto the dirty `quote_service.py`). Go.

**Phase B — after the in-flight `_line_row.html` work lands, do ONE coordinated `_line_row.html` pass** that implements all three template changes together:
- #2 unified two-mode chips · #3 Good/Better/Best relabels · #4 DB-sourced warranty tier picker.
Doing it as one edit avoids three lanes fighting over one file.

**Phase C** — QA across all + UI-Architect governance on the new macros/pickers.

**Global do-not-touch (in-flight):** `app/services/report_service.py` (dirty) · `app/routers/credit_memos.py`, `app/routers/import_review.py`, `app/services/import_review_service.py`, `app/services/ar_aging_utils.py` (untracked, being built).

---

## Ticket 1 — Pricing: margin-vs-target + competitor-undercut signals (read-only)
**Lanes:** Backend → UI-Builder → UI-Architect → QA. **Prices are NOT changed; these are display signals only.**

**Backend**
- `app/models/pricing.py` — add `target_margin_pct: float|None` to `MarkupTier`; add `target_margin()` (returns `target_margin_pct`, else converts legacy `markup_pct` via `markup/(100+markup)`).
- `app/routers/settings.py` — replace `_DEFAULT_TIERS` (lines ~228-234) with the **6 confirmed brackets** (margin %): `(0,25,50) (25,100,45) (100,500,40) (500,2000,35) (2000,3000,30) (3000,None,25)`. Equivalent legacy markups for the dormant markup path: 100 / 81.82 / 66.67 / 53.85 / 42.86 / 33.33. Add `target_margin_pct` to `/settings/pricing/preview`. Seed stays idempotent.
- `app/services/pricing_service.py` — add `target_margin_pct_for_cost(cost)` (bracket lookup) and `competitor_undercut(product)` (True if any active `CompetitorPrice.price < sell_price_for(product)`). Import `CompetitorPrice`. Both read-only.
- `app/routers/products.py` — list route (~220) build `target_margin_map` + `undercut_set`; preview (~257) + detail (~640) pass scalar `target_margin`, `competitor_undercut`, and `sell_price` (from `PricingService.sell_price_for`).

**UI-Builder** — stop rendering `p.selling_price` (the 30% guess) for price display in `products/list.html` (line ~300, ~433), `_preview_panel.html` (line ~2, ~68-78), `detail.html` (line ~278). Show the imported price + a **margin badge** (green ≥ bracket target, amber within 5 pts below, red > 5 pts below, em-dash when cost=0) + a **competitor-undercut icon** when `p.id ∈ undercut_set`. Complete the existing STAGED `resolved_price_map` seam (rename → `sell_price_map`, drop the comment).

**UI-Architect** — extract `margin_vs_target_badge(actual, target)` + `competitor_undercut_icon()` macros (`macros/margin_signals.html`); governance pass.

**QA** — `tests/test_pricing_margin_signals.py`: 6-bracket margin math, `sell = cost/(1−m)`, competitor-undercut true/false/no-rows/inactive, list+preview route context keys, idempotent 6-row seed. **Update** `test_pricing_grid.py:72-75` (old 4-tier → new 6-tier).

**Acceptance:** all 13k PAI rows show their imported price (never the 30% estimate); margin badge bracket-aware; competitor icon only when a real lower competitor row exists; per-line quote/SO/invoice pricing math untouched; `grep` shows no `p.selling_price` in those 3 price cells.

---

## Ticket 2 — Add-ons: unified "suggested parts" with a two-mode "+"
**Lanes:** Backend → UI-Builder (Phase B) → QA. **Good/Better/Best is Ticket 3 — don't touch `upgrade_option` here.**

**Backend (Phase A)**
- `app/services/suggested_sell_service.py` — `get_inline_chips` (line ~54): replace the `.in_([...])` whitelist with `relationship_type != WARRANTY` so **all** non-warranty suggestions surface as chips (the dead `optional` type now shows).
- `app/routers/quotes.py` (~825) — add `POST /{quote_id}/lines/{line_id}/option-add` → `svc.add_optional_line(parent_line_id=line_id, product_id=...)`, returns the tbody (HX-Retarget `#quote-lines-tbody`) so the Options panel refreshes.
- `app/constants.py` — comment that the 3 non-warranty `SuggestedSellType` values are collapsed to one "suggested" concept at the UI layer; **keep the enum** (existing DB rows use the strings).

**UI-Builder (Phase B — `_line_row.html`, rebase first)** — collapse the recommended/required chip branches (lines ~477-521) into one neutral-gray chip loop; each chip is a **split button**: left = **Add to quote** (`hx-post /lines`, `line_role=suggested`, firm/in-total); right = **Add as option** (`hx-post /lines/{id}/option-add`, `is_included=False`/`line_role=optional`/not-in-total). Relabel the workspace + print "Optional Add-ons" → "Options / Add-ons" (cosmetic). No structural change to `workspace.html`/`print.html` — they already branch on `line_role=optional` + `is_included`.

**QA** — `tests/test_suggested_sell_two_mode.py`: chips include all non-warranty; option-add creates `optional`/`is_included=False`/parented line excluded from total; firm add is in total.

**Note:** the "Add as option" path is pure reuse of the existing `is_included=False` mechanism — **no model migration**.

---

## Ticket 3 — Good / Better / Best (relabel the existing alternatives mechanism)
**Lanes:** UI-Builder (Phase B) → UI-Architect. **100% presentational — zero route/service/model change.**

- `_line_row.html` (rebase) — badge (lines ~89-93): `ALT` → the `option_label` (`Good`/`Better`/`Best`, fallback `ALT`); context-menu "Add Upgrade Option" (~347) → "Add Good / Better / Best"; "Make This Active" (~375) → "Select This Option".
- `workspace.html` — the upgrade-option add slide-over: title → "Add Good / Better / Best Alternative"; replace the free-text `option_label` input with a `<select>` (Good / Better / Best + blank). Submits `option_label` unchanged.
- `print.html` (~421/424) — heading → "▲ Good / Better / Best Alternatives" + updated subhead.
- `constants.py` — one-line docstring update on `LineRole.UPGRADE_OPTION`.
- **UI-Architect** — governance: badge color semantics (amber=not-selected, green=selected), `option_label` field name, print render.

`select_upgrade_option` replace/exclude-parent behavior is already correct — **do not modify it**.

**QA** — `tests/test_upgrade_option_labels.py`: render `_line_row`/`print` with `option_label='Good'` → shows "Good"; blank → "ALT"; selected → green.

---

## Ticket 4 — Extended Warranty: all-tiers Parts+Labor, admin tiers, Mitchell 1 $45/hr, claim linkage
**Lanes:** Backend (heavy) → UI-Builder (Phase B) → UI-Architect → QA. Closes the 5 warranty build-gaps in the spec.

**Backend (Phase A)**
- `app/constants.py` — `WarrantyTierCoverage` enum (`parts_and_labor` default, `parts_only` legacy guard); `MITCHELL1_LABOR_RATE_DEFAULT = 45.0`.
- `app/models/warranty.py` — new **`WarrantyTier`** table (`code`, `label`, `months`, `coverage` default `parts_and_labor`, `cost_reserve_pct` default 0.0, `sort_order`, `is_active`). Add to `WarrantyClaim`: `esn`, `quote_line_id` FK, `so_line_id` FK (`invoice_line_id` already exists). Add to `WarrantyClaimLine`: `mitchell1_book_hours`, `labor_rate` (default 45.0), `labor_amount` (stored). Export `WarrantyTier` in `models/__init__.py`.
- `app/models/quote.py` — add `warranty_tier_code` to **both** `QuoteLine` and `SOLine` (so the sold tier is queryable).
- `app/services/warranty_service.py` — tier CRUD + **auto-seed 5 tiers** (the current hardcoded codes, **all `parts_and_labor`**, `cost_reserve_pct=0` to start); `compute_labor_amount(hours, rate=45)`; `create_claim` accepts `esn`/`quote_line_id`/`so_line_id`; claim-line stores labor fields.
- `app/services/quote_service.py` (rebase — dirty) — `convert_to_sales_order` (line ~381): expand the filter from `PRODUCT` to `PRODUCT, WARRANTY`; build PRODUCT lines first (collect quote-line→SO-line id map), then append WARRANTY lines with `parent_line_id` remapped + `warranty_tier_code`. **(Fixes the warranty-drops-on-SO bug.)**
- `app/routers/warranty.py` — tier admin endpoints (`GET/POST /warranty/tiers`, `PATCH/DELETE /tiers/{id}`, `GET /tiers/active`); claim-create accepts `esn`/`sold_line_id`; labor-calc helper.

**UI-Builder (Phase B)** — `_line_row.html` tier picker (lines ~531-537): replace the hardcoded 5 tiers with DB-sourced active tiers (server-injected JSON tag, house pattern); add `warranty_tier_code` to `hx-vals`; keep the `× pct × months/12` formula. `warranty/_new_picker.html`: add ESN input + `warranty_type` (vendor/jaks_extended) + hidden `sold_line_id`. `warranty/workspace.html`: ESN row + sold-line linkage chip + per-line labor inputs (book hours, $45/hr badge, computed `labor_amount`). New `warranty/_tier_admin.html` table wired into Settings.

**UI-Architect** — governance on the tier-picker (no tojson-in-attr), the picker additions, the admin table.

**QA** — `tests/test_warranty_service.py`: auto-seed 5 parts+labor tiers; claim ESN + sold-line persist; labor fields (`2.5h×$45=112.50`); `compute_labor_amount`; **quote→SO carries warranty lines**; tier CRUD; no lifecycle regression.

**Open inputs (owner, not blocking the build):** real `cost_reserve_pct` per tier; whether any part class needs a `warranty_percentage` above the 10% default (overridable at quote time already).

---

## Ticket 5 — AI Sourcing Pass (cylinder-head pilot, staged for review)
**Lanes:** Backend + QA. **One-off script; nothing auto-writes to the live catalog.**

- `app/models/ai_sourcing.py` (new) — `AiSourcingBatch` + `AiSourcingProposal` (mirror `ImportBatch`/`ImportCandidate`): proposal_type `companion|good_better_best`, anchor/suggested product FKs, `gbb_label`, `confidence_score`, `review_status pending|accepted|rejected`, `applied_ss_id`. Register in `models/__init__.py`.
- `app/constants.py` — `AiSourcingStatus`, `AiProposalType`, `AiProposalReviewStatus`, **`GoodBetterBestLabel`** (the seam Ticket 3 consumes).
- `app/services/ai_sourcing_service.py` (new) — `stage_proposals`, `set_review_status`, `list_proposals`, `apply_accepted` (calls `SuggestedSellService.add_suggestion` for companions; **idempotent** — catch the `ix_suggested_sells_pair` ValueError).
- `scripts/ai_sourcing_pass.py` (new) — `--category 'cylinder heads'` (default), `--dry-run`, `--apply <batch> --confirm`. Reads heads → proposes companions (bolt/gasket/kit categories sharing a `ProductApplication` engine pair, scored by overlap) → clusters Good/Better/Best by engine+category (price/`is_performance_part` heuristic) → stages into a batch.
- **QA** — `tests/test_ai_sourcing_pass.py`: companion proposal on shared engine pair; no-overlap excluded; GBB labels distinct; dry-run writes nothing; apply review-gate; apply idempotent.

**Acceptance:** runs on the 13k catalog < 60s, scoped to heads; nothing written until `--apply --confirm` on accepted proposals; ≥80% of companions join a head to a bolt/gasket/kit sharing an engine pair; owner accepts ≥5 and sees them as chips on a test quote before expanding to other categories.
