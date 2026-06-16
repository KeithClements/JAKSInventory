# JAKS Customer-Facing SKU Scheme — Spec & Build Record

> ## ⚠️ REVERSED 2026-06-16 — this opaque scheme is no longer the direction.
> After an owner interview the masking scheme below was judged too confusing (10% of SKUs were
> meaningless `JAKS-GEN-#####`, 2,730 kits under a junk `INFO` code, cryptic derived codes, and it hid
> the vendor part number staff need to order/cross-ref/test). **The customer-facing SKU is now simply the
> vendor's real part number** for standard products; only **private-label** parts (`products.is_house_brand`)
> carry a separate owner-typed **JAKS Product #** (e.g. `2239250S3`) while the vendor part # still prints
> on the PO. The revert was applied to the live catalog (29,659 rows, 0 collisions); the create path and
> importer were de-masked; this `sku_service.py` + `scripts/backfill_sku_scheme.py` are **shelved (dormant,
> not deleted)** as a possible future feature. **Authoritative spec: MASTER_PLAN.md §20.** Everything below
> is retained for historical context only.

*Owner-locked 2026-06-06 (interview) · BUILT the same night on `backend/workflow-series-3`.*
*Supersedes the 2026-05-22 `JAKS-[VENDOR_CODE]-[PART#]` decision (that format leaked the vendor).*

---

## 1. The scheme

```
JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]

JAKS-ISX-INJ-90001     ISX / injector / vendor 9 (PAI) / part 0001   (engine known)
JAKS-FUE-90001         fuel part fitting many engines / PAI / 0001    (engine omitted)
JAKS-ISX-INJ-30001     HHP's version of that SAME injector            (shares 0001, digit → 3)
```

| Segment | Meaning | Source |
|---|---|---|
| **ENGINE** | Engine platform code (ISX, DT466, C15…). **Omitted** for multi-fit / engine-agnostic parts. | `product.engine_model` (§18 classification), normalized by `SkuService.engine_code` |
| **CATEGORY** | Short code of the **deepest** category a product is tagged to. | `ProductCategory.code`, or auto-derived from the name when blank |
| **V** | **1-digit opaque vendor number** (PAI = 9, …). A customer can't tell which supplier "9" is. | `Vendor.vendor_number` (owner-set) |
| **NNNN** | 4-digit zero-padded part sequence, unique per (engine, category). | allocated by `SkuService` |

**Owner-locked rules**
- **Vendor-independent / no leak:** the SKU never contains the vendor name or the vendor's real part number — only the opaque digit. The vendor's real part number lives on `ProductVendorSource.vendor_part_number` and stays **off customer documents**.
- **Frozen:** `engine_code` / `category_code` / `part_seq` are stamped at mint time, so the SKU never drifts if a category is later renamed/re-coded.
- **One SKU = one vendor:** a part sourced from a 2nd vendor gets its **own** SKU that **shares the sequence** with a different vendor digit (readable equivalence `90001 ↔ 30001`).
- **Cores** stay on `CoreCharge` — there are **no** `-CORE` SKUs.
- **Customer documents** show the JAKS SKU **only**.
- **Customer Part Number** (a fleet's own number) — **deferred** (not built).

---

## 2. What was built (this session)

| # | Change | Files |
|---|---|---|
| 1 | **Live-leak fix** — quote print showed the *vendor* part number; now JAKS SKU only. | `app/templates/quotes/print.html` (`line_part_no`) |
| 2 | **Schema** — `product_categories.code`, `vendors.vendor_number`, `products.engine_code/category_code/part_seq` (+ inline migrations). | `app/database.py`, `app/models/product.py`, `app/models/vendor.py` |
| 3 | **SKU generator** — engine/category code normalization, 4-digit sequence allocation, assembly, vendor-twin sharing, catalog backfill. | `app/services/sku_service.py` (new) |
| 4 | **Backfill CLI** — `--dry-run`/`--apply`, auto-backs-up jaks.db, parks old `JAKS-PAI-…` on `vendor_sku`. | `scripts/backfill_sku_scheme.py` (new) |
| 5 | **Owner input** — "SKU #" (1-digit vendor number) field on the vendor new/edit forms + routes. | `app/templates/vendors/new.html`, `…/detail.html`, `app/routers/vendors.py` |
| 6 | **Tests** — 28 new (normalization, assembly, sequence, vendor-twin, backfill dry-run/apply/skip). | `tests/test_sku_service.py` (new) |

**Not changed (deliberately):** the importer `full_import` still mints the CSV `Variant SKU` — the backfill is the regeneration mechanism (mirrors the §18 `backfill_s18_classification.py` pattern; the catalog is throwaway/re-importable). The money path, inventory, cores, and §18 classification are untouched.

---

## 3. How to roll it out (owner steps, in order)

1. **Set each vendor's SKU #** — Inventory → Vendors → edit → "SKU #" (e.g. PAI = 9). Products whose vendor has no number are skipped and keep their old SKU.
2. *(Optional)* **Refine category codes** — blank codes auto-derive from the name (coarse but valid: `ENGINE PARTS → ENG`). *Editing codes on the Category Maintenance screen is a small follow-up — see §5.*
3. **Preview** — `.venv/Scripts/python.exe scripts/backfill_sku_scheme.py` (dry-run, writes nothing). Review the old→new samples.
4. **Apply** — `.venv/Scripts/python.exe scripts/backfill_sku_scheme.py --apply` (backs up jaks.db first).

---

## 4. Verification (this session)

- **Unit tests:** 28/28 green (`tests/test_sku_service.py`).
- **Regression:** full suite **1079 passed**, only the 6 pre-existing cosmetic `test_ui_lint`/W-4 fails (was 1051; +28 mine). **Zero regressions.**
- **Live dry/apply on a throwaway COPY of the real 13,154-part catalog:** 13,153 regenerated, **0 SKU collisions**, 33% got an engine segment. Real output samples:
  `JAKS-ISX-HPFP → JAKS-FUE-90001`, `JAKS-ISX-TURBO → JAKS-TURB-90001`, `JAKS-ISX-STARTMOTOR → JAKS-ELEC-90001`.
  Top auto-derived category codes: ENG (8059), CAB (1218), TRAN (1014), DIFF (754), AIRB (490), SUSP, STEE, DRIV, ELEC. The live `data/jaks.db` was **not** touched.

---

## 5. Follow-ups (small, documented — not blockers)

- **Category-code editing UI** on the Category Maintenance screen (the `ProductCategory.code` column exists; wire an input + `CategoryService.update_category` branch + router binding). Until then, codes auto-derive.
- **Product-detail lock label** — a "🔒 Hidden from customer documents" tag beside the vendor/mfg part numbers on `products/detail.html` (presentation only; those screens are already internal-only).
- **Importer wiring (optional)** — have `full_import` mint the new SKU directly so a fresh import needs no backfill pass.
- **Multi-vendor twins UI** — a one-click "this is another vendor's version of <SKU>" action calling `SkuService.assign_twin_sku` (logic + tests already exist; no UI yet).
- **Engine/category code normalization** is heuristic — the owner may want to tune a few engine codes (e.g. `855`, `743`) or category codes via the maintenance screen.

---

*Single source of truth for the SKU scheme. Update as decisions change.*
