# JAKS Inventory — §8 Owner Acceptance Checklist

**Purpose:** Hand-run gate before go-live. Three core business spines; each must
pass end-to-end. Run against the live app (`http://localhost:8000`) with a FRESH
test-data set created below. Check every box. Any ❌ is a blocker.

**Before you start:**
1. Start the app: `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
2. Log in as `admin` / `admin` at `http://localhost:8000/login`
3. Complete the **Seed Data** section once. Re-use the same records for all three flows.

---

## Seed Data  (do once)

### Customer
- Customers → **New Customer** (top-right button)
- Company name: `ACCEPT TEST CO`
- Save

### Vendor
- Vendors → **New Vendor**
- Name: `ACCEPT TEST VENDOR`
- Save

### Products — create two
**In-stock product (for Flows A and C)**
- Products → **New Product**
- SKU: `ACC-INSTOCK`, Title: `Acceptance In-Stock Part`
- Cost: `$20.00`, Markup: `50%` → sell price shows `$30.00`
- QOH: `20` (set via Receive or direct on new product form)
- `Has Core`: ☐ off
- Save — note the **Product ID** from the URL: `/products/___`

**Core product (for Flow C)**
- Products → **New Product**
- SKU: `ACC-CORE`, Title: `Acceptance Core Part`
- Cost: `$80.00`, Markup: `25%` → sell ~`$100.00`
- QOH: `5`
- `Has Core`: ☑ on
- Vendor core charge: `$25.00`
- Customer core charge: `$35.00`
- Save

---

## Flow A — In-Stock Sale → Paid
> Spine: Quote → Sales Order → Fulfill & Invoice → Payment → PAID
> Automated equivalent: `pytest -m acceptance -k instock_sale`

### Setup note
Record current **QOH** for `ACC-INSTOCK` before starting: ______

### Steps

- [ ] **A-1.** Go to **Customers** → find `ACCEPT TEST CO` → click **+ Quote** (in the row)
- [ ] **A-2.** Quote workspace opens. In the search bar type `ACC-INSTOCK`, wait for the
      dropdown, click the result → line is added automatically.
      *Verify:* one line appears; SELL $ shows `$30.00`; Total = `$30.00`.
- [ ] **A-3.** Header: click **→ Sales Order ▾** → **Create Sales Order →**.
      *Verify:* redirected to a Sales Order workspace; status = **OPEN**.
- [ ] **A-4.** SO workspace: click **Fulfill & Invoice →** (green button in the lines section).
      Accept the confirm dialog.
      *Verify:* redirected to an Invoice workspace; status = **OPEN**.
- [ ] **A-5.** Invoice workspace: click **Take Payment** (top-right header area).
      In the payment form:
      - Amount: `30.00` (pre-filled; leave it)
      - Method: `Cash`
      - Click **Record Payment**
- [ ] **A-6.** *Verify — invoice status:* chip shows **PAID**.
- [ ] **A-7.** *Verify — balance due:* `$0.00` on the invoice workspace.
- [ ] **A-8.** Go to **Products** → find `ACC-INSTOCK` → open detail.
      *Verify — QOH:* decreased by 1 from the value you recorded in Setup.
      _(QOH before: ______ → QOH now: ______)_

**Flow A result:** ☐ PASS  ☐ FAIL — notes: ___________________________

---

## Flow B — Out-of-Stock → Deposit → PO Receive → Fulfill → Invoice
> Spine: SO (OOS product) + deposit → PO receive allocates → Fulfill & Invoice → deposit applied
> Automated equivalent: `pytest -m acceptance -k oos_linked`

### Setup note
Go to **Products** → `ACC-INSTOCK` and temporarily set QOH to `0` (or use a different
product that is naturally OOS). Then proceed.

**Product used for this flow:** `ACC-INSTOCK` with QOH = 0
**Deposit amount you'll collect:** `$15.00`

### Steps

- [ ] **B-1.** Customers → `ACCEPT TEST CO` → **+ Quote**.
- [ ] **B-2.** Add `ACC-INSTOCK` to the quote (same as A-2).
      *Verify:* line appears; fulfillment source shows **BACKORDER** badge on the line.
- [ ] **B-3.** Click **→ Sales Order ▾** → **Create Sales Order →**.
      In the "Payment Mode" field on the SO header, select **Deposit Required**.
      _(This step may be optional if your default is already Deposit Required.)_
      *Verify:* SO status = **OPEN**.
- [ ] **B-4.** SO workspace: click **$ Deposit** (header action area).
      - Amount: `15.00`
      - Method: `Cash`
      - Click **Record Deposit**
      *Verify:* "Deposit Collected: $15.00" appears in the SO header strip.
- [ ] **B-5.** Create a PO: **Purchase Orders** → **New PO** → select `ACCEPT TEST VENDOR`.
      Add a line for `ACC-INSTOCK`, qty=1, cost=`$20.00`. Status must be **SENT** to receive
      (open the PO → send/mark it as Sent if still DRAFT).
- [ ] **B-6.** PO workspace → **Receive Goods** section → enter qty `1` for the
      `ACC-INSTOCK` line → click **Receive & Update Inventory**.
      *Verify:* PO status → **RECEIVED**; a green success banner appears.
- [ ] **B-7.** Go to **Products** → `ACC-INSTOCK`.
      *Verify:* QOH increased by 1 (now = 1).
- [ ] **B-8.** Return to the Sales Order (Sales Orders menu → find your SO).
      *Verify:* the line's fulfillment source / status changed from **BACKORDER** toward
      **RESERVED_STOCK** or the line qty-committed increased.
      _(This step confirms PO receive allocated to the waiting SO.)_
- [ ] **B-9.** SO workspace → **Fulfill & Invoice →** (accept confirm).
      *Verify:* redirected to an Invoice workspace.
- [ ] **B-10.** *Verify — invoice total:* `$30.00`.
- [ ] **B-11.** *Verify — deposit applied:* balance due shows `$15.00` (total minus deposit),
       OR invoice status = **PARTIAL** if deposit was auto-applied.
       _(balance_due = total − deposit: $30.00 − $15.00 = $15.00)_
- [ ] **B-12.** Click **Take Payment** → enter `15.00` → Record Payment.
       *Verify:* status → **PAID**, balance due = `$0.00`.

**Flow B result:** ☐ PASS  ☐ FAIL — notes: ___________________________

---

## Flow C — Core Charge → Customer Return → Vendor Credit
> Spine: Invoice (core product) → finalize → CoreCharge created → customer returns core →
>        credit issued → submit to vendor → vendor credit memo
> Automated equivalent: `pytest -m acceptance -k core_charge`

### Steps

- [ ] **C-1.** Customers → `ACCEPT TEST CO` → **+ Quote**.
- [ ] **C-2.** Add `ACC-CORE` to the quote.
      *Verify:* **two** lines appear — the product line ($100) and a core charge line ($35).
      Total = `$135.00`.
- [ ] **C-3.** Click **→ Sales Order ▾** → **Create Sales Order →** → **Fulfill & Invoice →**
       (accept confirm).
       *Verify:* redirected to Invoice workspace, status = **OPEN**.
- [ ] **C-4.** Invoice workspace → click **Finalize** (top-right; opens a confirm modal).
       In the modal click the **Finalize** submit button.
       *Verify:* invoice status → **OPEN** (already open from fulfillment); if it was DRAFT,
       status should now show **OPEN**.
       *Key:* finalization creates the **CoreCharge** record in the background.
- [ ] **C-5.** Go to **Core Charges** (left sidebar, under CORES).
       *Verify:* a new row appears for `ACCEPT TEST CO` / `ACC-CORE` in the
       **"Awaiting Customer Return"** stage. Core charge = `$35.00`.
       Record the **Core #**: ______
- [ ] **C-6.** On the Core Charges board, find the row → click **Record Return** (action button).
       - Qty returned: `1`
       - Inspection outcome: `Accepted`
       - Click the submit button
       *Verify:* core moves to **"Pending Inspection"** or **"Ready to Ship"** stage.
- [ ] **C-7.** *Verify — customer credit:* go to **Customers** → `ACCEPT TEST CO` → Account tab.
       Credit balance increased by `$35.00` (the customer core charge).
       _(Credit balance before: $______  →  after: $______)_
- [ ] **C-8.** Return to **Core Charges** → find your core (now in **"Ready to Ship"** stage).
       Click **Mark Shipped** → confirm.
       *Verify:* core moves to **"Awaiting Vendor Decision"** stage.
- [ ] **C-9.** On the **"Awaiting Vendor Decision"** row, click **Accepted** (vendor accepted
       the core return).
       - Credit amount: `$25.00` (the vendor core charge)
       - Confirm
       *Verify:* core is removed from the active board (status = VENDOR_ACCEPTED).
- [ ] **C-10.** Go to **Vendors** → `ACCEPT TEST VENDOR` → open detail.
       *Verify:* a **Vendor Credit** line of `$25.00` appears in the vendor's credit memo
       or credits section.

**Flow C result:** ☐ PASS  ☐ FAIL — notes: ___________________________

---

## Sign-off

| Flow | Result | Date | Tester |
|---|---|---|---|
| A — In-stock sale → paid | ☐ PASS  ☐ FAIL | | |
| B — OOS → deposit → receive → invoice | ☐ PASS  ☐ FAIL | | |
| C — Core → customer return → vendor credit | ☐ PASS  ☐ FAIL | | |

**Build / commit tested against:** ______________________

**Overall verdict:** ☐ GO  ☐ NO-GO

**Blocking issues (any ❌):**

1.
2.
3.

---

## Running the automated equivalent

The same three flows run as isolated, in-memory pytest tests.  No live server needed.

```
# All three acceptance flows:
.venv\Scripts\python.exe -m pytest tests/test_e2e_flows.py -m acceptance -v

# Individual flows:
.venv\Scripts\python.exe -m pytest tests/test_e2e_flows.py -m acceptance -k instock_sale   # Flow A
.venv\Scripts\python.exe -m pytest tests/test_e2e_flows.py -m acceptance -k oos_linked     # Flow B
.venv\Scripts\python.exe -m pytest tests/test_e2e_flows.py -m acceptance -k core_charge    # Flow C
```

A passing automated run does NOT replace this checklist — it proves the service layer is
correct, not the UI.  Run both before go-live.

---

## Schema drift gate

```
.venv\Scripts\python.exe -m pytest tests/test_schema_drift.py -v
```

Must stay green after every Backend model change. If it fails: add the missing column
to `_PENDING_COLUMN_ADDITIONS` in `app/database.py` (see test output for the exact entry).
