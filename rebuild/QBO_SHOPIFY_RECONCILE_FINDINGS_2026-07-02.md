# QBO + Shopify Reconciliation — Findings & Fix Lists

**Date:** 2026-07-02 · Companion to [QBO_SANDBOX_GO_LIVE_PLAN.md](QBO_SANDBOX_GO_LIVE_PLAN.md)
Ground-truthed against live `data/jaks.db` (30,935 products, 43 customers), live Shopify (jaksdiesel.com), and live QBO (JAK's Diesel, LLC).

---

## Deliverable 1 — Shopify / ERP catalog reconcile

### Good news: ERP identifier data is clean
- **Null/empty SKUs in ERP: 0.**
- **Duplicate SKUs in ERP: 0** — the global unique constraint on `Product.sku` is holding.
- The "null SKU" seen earlier is **Shopify-side**: a listing whose linked ERP product *has* a SKU that was never pushed down to the Shopify variant. Fix = re-run inventory/SKU push for those listings, not a data repair. (The `JAKS-4101420S3`-titled `5658283` head is the same class — verify SKU/title in the house-brand set, below.)

### The 6 unlinked ERP products (only 6 of 30,935)
| id | SKU | Title | Action |
|----|-----|-------|--------|
| 27509 | `JAKS-JAKS-10R1273` | JAK's ACERT Fuel Injectors | **Real house-brand** — link to Shopify listing (or publish) + add OEM xref `10R1273`. |
| 29659 | `JAKS-JAKS-2239250S3` | Stage 3 All Inconel Valve Head | **Real house-brand** — link/publish + add OEM xref `2239250`. |
| 30932 | `JAKS-PAI-HBKC15T2` | Test Bolt Kit 2 | **Delete** — QA junk. |
| 30933 | `JAKS-IMB-NEGCOST1` | Negative Cost Test | **Delete** — QA junk. |
| 30934 | `JAKS-PAI-311148C15` | CAT C15 Cylinder Head Reman | **Review** — generic title + `311148` looks real; either finish it (title/price/xref) or delete if it's a QA row. |
| 30935 | `JAKS-PAI-QASTEPFIX1` | QA Step-Fix Test Head | **Delete** — QA junk. |

### House-brand SKUs (is_house_brand=1): only 2, both need the same 2 fixes
Both id 27509 and 29659 are `UNLINKED` and their OEM core (`10R1273`, `2239250`) isn't in the title. For house-brand that's **expected** — the SKU carries the OEM ref and the title is descriptive (per locked rule D-A). No data defect; they just need (a) a Shopify link/publish and (b) the OEM number registered as an OEM `CrossReference` so search/interchange works.

### Discard: the 25,023-row "SKU/title mismatch" heuristic = false positives
PAI part numbers (the SKU core, e.g. `040000`) legitimately never appear in human titles (`855 HEAD BOLT`). That check only makes sense for house-brand listings that *lead* with an OEM number — a set of 2, both already covered above. Ignore the 25k list.

### Reverse-link audit (Shopify → ERP) — how to run it right
30,929 of 30,935 ERP products already carry a `shopify_product_id`, and Shopify shows ~19k active + 8,799 draft listings (2,686 are `vendor:"JAK's Diesel"` house-brand). To find Shopify listings that *no* ERP product points to, do **not** page the Admin API 50-at-a-time (~560 calls). Instead:
1. Run the ERP's own **`/shopify/link-products`** endpoint first — it's read-only and matches unlinked listings to ERP products by SKU/handle, then stores the GIDs.
2. Feed whatever it *can't* match into the existing **import review queue** for disposition.
This reuses code that already exists rather than building a new sweep.

---

## Deliverable 2 — QBO customer cutover blocker (the important find)

**Headline:** QBO has **no internal duplicate DisplayNames** among ~30 active customers — so the multi-match *refusal* won't fire. The real risk is the opposite: **ERP company names don't match QBO DisplayNames**, and **none of the 43 ERP customers has `qbo_customer_id` set**, so first push resolves everyone by name. Four accounts will **silently create duplicate QBO customers**.

### B. DUPLICATE-CREATE RISK — fix before any live push (4)
ERP is named by *company*; QBO is named by the *person* (the ERP contact). First push finds no DisplayName match → creates a second QBO record.

| ERP id | ERP company_name | ERP contact | Existing **production** QBO customer | Prod QBO id |
|--------|------------------|-------------|-----------------------|-----|
| 29 | CROFUTT TRUCK AND TRAILER REPAIR | Ron Crofutt | Ron Crofutt (CrofuttTruck@gmail.com) | **62** |
| 10 | Denver Truck and Trailer Repair | David | David *(bare first name — rename in QBO to the company)* | **39** |
| 21 | KYB - JAE Trucking Services | Manuel Colin | Manuel Colin | **18** |
| 38 | Williams Diesel | William Flores | William Flores | **20** |

**Cleanest fix:** set each ERP customer's `qbo_customer_id` to the correct QBO id (sticky binding — the push honors it and never re-matches by name).

**⚠ Sandbox vs production caveat (critical):** the IDs above (62/39/18/20) are from **live/production** QBO. A fresh *sandbox* company has none of these customers, so during the 30-day trial the ERP simply **creates** them in the sandbox — no duplicate risk there, and you should NOT paste production IDs into `qbo_customer_id` during sandbox. Apply this binding **at live cutover only**, right before the first production push:
```sql
UPDATE customers SET qbo_customer_id='62' WHERE id=29;  -- CROFUTT → Ron Crofutt
UPDATE customers SET qbo_customer_id='39' WHERE id=10;  -- Denver Truck → David
UPDATE customers SET qbo_customer_id='18' WHERE id=21;  -- KYB-JAE → Manuel Colin
UPDATE customers SET qbo_customer_id='20' WHERE id=38;  -- Williams Diesel → William Flores
```
(If you also test the binding mechanism in sandbox, use the *sandbox* customer ids, then clear/re-seed on the flip.)

### C. Will CREATE-NEW on first push — verify each name is intentional (12)
These have no QBO counterpart, so a new QBO customer gets created (fine if the name is right). Flag the non-customers: **Mitchell 1** (that's your shop-software vendor), **G2 DIESEL PRODUCTS (CORE)** (a core-supplier, not a sell-to customer), and **JAK's Diesel House** (looks like your own house account) probably should *not* become QBO customers. The rest (2 Strong Truck Parts, Colorado Truck Repair, Cornerstone Construction, Ivans Diesel, JM Diesel Repair, MHC TRP, Rush Truck Centers, Triple B Transport, Victor Agapiev) are legit new accounts.

### D. TEST/JUNK ERP customers — must NOT reach live QBO (7)
Purge or mark inactive before cutover: **Test** (Keith), **NEW** (MIKE), **New Customer Quote**, **Bad Email Co**, **Hanks Other Shop**, **QA Fleet Diesel LLC** (Hank Fleet), **Mountain County Fleet**. (QBO also carries its own junk: **Sample Customer** $5, and the bare **David** covered in B.)

### A. Clean binders (20) — no action
AP DIESEL SERVICES LLC, Abraham Mechanics, Bigfoot Diesel Parts, Cornerstone Truck Repair LLC, Diesel Performance of Grand Junction, Elite Diesel, Family Machine LLC, Front Range Diesel Repair, Inline 6 Diesel Repair, Kar Tech LLC, Mile High Truck and Trailer Repair LLC, Morgan County Diesel Repair, Myer Brothers Truck & Tractor, Rampart Equipment Inc., State Express Truck Repair, Steve Schmidt Repair Inc, The Mechanics LLC, Titan Logistics, UZB TRUCK TRAILER REPAIR INC, Wize Trucking LLC — all match a QBO DisplayName exactly and bind on first push.

> Note: the Shopify-connector customers (`Shopify - jaks-diesel-3 Customer`, `Shopify customer - USD`) stay in QBO untouched — they belong to the web-order connector per decision D-C.

---

## Deliverable 3 — Sandbox smoke script

**Files:** `scripts/qbo_sandbox_smoke.py` (the script) + `tests/test_qbo_sandbox_smoke_gate.py` (12 gate tests). Built + verified 2026-07-02; the full QBO suite (67 tests incl. the new gate) passes.

### Surprise finding: the ERP is ALREADY sandbox-connected and preflight-green
Running `--check-only` against the current `data/jaks.db` returned all-PASS:
- QBO **connected**, realm `9341456051662686` — a **different realm from live** (`9341455493585007`), so the ERP sandbox is properly isolated from production.
- Environment = **sandbox**, API host = `sandbox-quickbooks.api.intuit.com` (both safety checks pass).
- **138 accounts** present and **all 5 generic JAKS items already resolve.**
- Verdict printed: *"Preconditions OK — safe to run the full smoke test."*

So plan steps §3.1–3.6 (Intuit app, sandbox company, connect, "Set up QBO items") are effectively **already done**. One open item confirmed: `JAKS_FERNET_KEY` is **not set**, so QBO tokens sit in plaintext in the settings table — fine for sandbox, **must be set before production** (already on the cutover checklist).

### What the script proves (per-phase PASS/FAIL, non-zero exit on any fail)
Drives one of each document through the **real ERP service layer** and pushes with the **same `QBOSyncService` methods the app uses**, then reads each back from QBO to assert the account/item mapping:
1. **Invoice** — product + core + freight lines → asserts parts=`JAKS Parts Sales`, core=`JAKS Core Charge`, freight=`JAKS Freight & Delivery`, plus local `SYNCED` + `qbo_invoice_id`.
2. **Payment** — card payment w/ surcharge → asserts QBO Payment `LinkedTxn`→invoice and the separate `JAKS-SC-{id}` surcharge SalesReceipt.
3. **Vendor bill** — real PO → send → receive → bill+freight → approve → push → asserts COGS line + freight-in account.
4. **Credit memo** — line back-linked to the invoice's core line + a parts line → asserts both mappings.

### Safety gate (the point of the script)
`assert_sandbox()` is a single choke point run first in preflight — refuses to proceed unless `environment == "sandbox"` **and** the API host contains `sandbox`. No override flag exists; production exits code 2 with an actionable message.

### How to run (repo root, server stopped)
```
.venv\Scripts\python.exe -m scripts.qbo_sandbox_smoke --self-check   # gate only, no network  ✓ verified
.venv\Scripts\python.exe -m scripts.qbo_sandbox_smoke --check-only   # preconditions          ✓ verified: all PASS
.venv\Scripts\python.exe -m scripts.qbo_sandbox_smoke                # full run (writes ZZ SMOKE TEST docs to jaks.db + sandbox)
.venv\Scripts\python.exe -m scripts.qbo_sandbox_smoke --cleanup      # void sandbox docs + remove local ZZ SMOKE TEST rows
```
All records are prefixed `ZZ SMOKE TEST`; `--cleanup` reverses them. **Note the full run mutates the live ERP DB** (creates the smoke customer/vendor/products/docs there), which is why it's gated on your go-ahead rather than run automatically.

### Two accounting nuances the script surfaced (not bugs — verification limits)
1. **Core-charge liability binding isn't self-proving.** If `qbo_core_charge_liability_account` is unset, cores post to **income** with only a log warning (`qbo_service.py:618-631`); the `JAKS Core Charge` item still resolves, so the item-mapping check passes even when the core is mis-accounted. To make the sandbox prove the *liability* landing, map that account in Settings → QBO Accounts and re-run "Set up QBO items" first. **Check this during trial week 1.**
2. **Surcharge item self-heals but the 5 generic items don't** (`_resolve_surcharge_item` creates on the fly; `_resolve_items` requires pre-existing). Intended split — the preflight catches missing generics up front regardless.

