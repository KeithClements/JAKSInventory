# JAKS Inventory — Go-Live Trial Run Test Plan (Round 4)

**Purpose:** one final, end-to-end functional verification of the whole ERP before going live with real data.
**Date run:** ____________   **Tester:** ____________   **Build / HEAD commit:** ____________ (`git log --oneline -1`)

---

## How to use this document

1. **Start clean.** This plan assumes a freshly reset DB (Admin → Reset & Reseed, or the clean reset already done — logins `admin/admin` and `bookkeeper/bookkeeping`). The demo/throwaway DB is safe to break.
2. **Run Part 1 (the 5 lifecycles) FIRST** — these are the real go-live gate. If any of A–E fails, stop and fix before continuing.
3. Then work the **screen-by-screen** sections (Part 2). Re-confirm even the screens you "know" work — a backend edit can break a screen that worked yesterday.
4. **Mark every row.** Hard-refresh (Ctrl+Shift+R) each screen so you're testing the latest build.

### Marks legend

| Mark | Meaning |
|---|---|
| ✅ | Works as expected |
| ⚠️ | Works but a quirk / partial / confusing |
| ❌ | Broken — can't complete the task, or data ends up wrong |
| — | Not tested yet |
| N/A | Doesn't apply / skipped |

> The single question for every test: **"Can I finish the business task, and is the data (money + inventory) correct afterward?"** — not "does the screen look right."

---

## PART 0 — Pre-flight

| # | Check | Expected | Mark | Notes |
|---|-------|----------|------|-------|
| 0.1 | `git log --oneline -1` then restart app | HEAD is current; app boots with no errors | | |
| 0.2 | Open `/` (not logged in) | Redirected to login | | |
| 0.3 | Log in `admin / admin` | Lands on Dashboard | | |
| 0.4 | Default-password warning visible | Warning to change admin password is shown | | |
| 0.5 | Sidebar stays on the LEFT at a narrow window width | Nav never jumps to the right | | |
| 0.6 | Log in as `bookkeeper / bookkeeping` (separate browser) | Bookkeeper role works; attribution is per-user (not always "admin") | | |

---

## PART 1 — The 5 Go-Live Lifecycles (END-TO-END — run these first)

Run each as a FULL lifecycle with REAL-looking data. These prove the two money/inventory spines.

### Lifecycle A — Purchasing spine (PO → Receive → Inventory)
| # | Step | Expected | Mark | Notes |
|---|------|----------|------|-------|
| A.1 | Create a NEW vendor | Saves; appears in Vendors list | | |
| A.2 | Create a NEW product (with a cost), assign that vendor as a source | Product saves; vendor source shows vendor cost | | |
| A.3 | Create a PO to that vendor for, say, 10 units | PO drafts with the line; totals correct | | |
| A.4 | Receive a PARTIAL qty (e.g. 4 of 10) | Inventory **+4**; PO shows partial; remaining 6 open | | |
| A.5 | Receive the REST (6) | Inventory **+6** (total 10); PO rolls to fully received | | |
| A.6 | Check product cost after receipt | Moving-average cost updated correctly | | |
| A.7 | Confirm an inventory transaction exists for each receipt | Audit trail present; QOH = 10 | | |

### Lifecycle B — Revenue spine, in-stock (Quote → Invoice → Finalize → Pay)
| # | Step | Expected | Mark | Notes |
|---|------|----------|------|-------|
| B.1 | Create a Quote for the in-stock part (from A) | Quote drafts; line added one-click from search | | |
| B.2 | Convert Quote → Invoice | Invoice created from quote lines | | |
| B.3 | Finalize the Invoice | Status locks; **inventory goes DOWN** by qty sold | | |
| B.4 | Record a full Payment | Invoice → **PAID**; balance 0 | | |
| B.5 | Re-open the invoice | Totals, tax, payment all correct and consistent | | |

### Lifecycle C — Revenue spine, backorder + deposit (Quote → SO → deposit → receive → invoice)
| # | Step | Expected | Mark | Notes |
|---|------|----------|------|-------|
| C.1 | Quote for an OUT-of-stock part | Quote drafts | | |
| C.2 | Convert Quote → Sales Order, choosing **Deposit** | SO created; deposit collected/recorded | | |
| C.3 | From the backordered SO line, create a PO to the vendor | Draft PO created + **linked** to the SO line | | |
| C.4 | Receive that linked PO | Inventory ↑; SO line shows now-available | | |
| C.5 | Fulfill the SO → Invoice | Invoice built from fulfilled lines; **deposit pre-applied** | | |
| C.6 | Record the remaining balance as Payment | Invoice PAID; deposit + payment = total | | |

### Lifecycle D — Core lifecycle (Invoice with core → return → vendor return → credit)
| # | Step | Expected | Mark | Notes |
|---|------|----------|------|-------|
| D.1 | Invoice a product that **has a core charge** | Core charge line appears automatically (child line) | | |
| D.2 | Finalize + the core charge is on the customer's balance | Core liability tracked | | |
| D.3 | Customer returns the core (After-Sale Service / core return from invoice) | Core return started; core marked returned | | |
| D.4 | Send the core back to the vendor (Vendor Return) | Vendor return created | | |
| D.5 | Issue the core credit to the customer | Credit issued **once** (no double-credit); balance reduced | | |

### Lifecycle E — A/R + statements (overdue → statement → aging)
| # | Step | Expected | Mark | Notes |
|---|------|----------|------|-------|
| E.1 | Let an invoice go past its due date (or set terms so it's overdue) | Invoice shows overdue (red) | | |
| E.2 | Generate a customer Statement | Statement renders; shows the invoice | | |
| E.3 | Check the aging bucket | Invoice lands in the correct bucket (current / 1–30 / 31–60 / 61–90 / 90+) | | |
| E.4 | Customer list / detail balance + aging bar | Balance Due and the 5-segment aging bar reflect reality | | |

> **NOTE:** AR-aging bucket consolidation was in active development at last status — confirm E.3/E.4 numbers carefully and flag any mismatch.

---

## PART 2 — Screen-by-screen functional coverage

### 1 — Dashboard
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 1.1 | Dashboard loads with KPI tiles | Numbers populate (not blank/zero everywhere) | | |
| 1.2 | Revenue chart renders | Chart draws at a sensible size (not oversized) | | |
| 1.3 | **Top Customers** widget | Lists real top customers by lifetime sales | | |
| 1.4 | **Open Follow-Ups** widget | Shows quotes with pending/overdue follow-ups | | |
| 1.5 | Recent invoices / recent calls | Reflect actual recent activity | | |
| 1.6 | Click-through from a widget | Navigates to the right record | | |

### 2 — Customers
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 2.1 | Customers list loads; tabs + counts | Active/Inactive tabs, counts correct | | |
| 2.2 | Account # subline shows on rows | Acct # visible where set | | |
| 2.3 | Search (company / contact / phone) | Finds the right customer | | |
| 2.4 | Create a new customer | Saves; required fields enforced | | |
| 2.5 | **Customer Type** pre-fills defaults | Picking a type seeds the 5 default fields | | |
| 2.6 | **Pricing tier** (wholesale/fleet/dealer) | Saved; see 5.x for price effect | | |
| 2.7 | Credit limit + terms + card surcharge | Saved and shown | | |
| 2.8 | Customer flags (chips) | Flags render on list/detail/preview | | |
| 2.9 | **Credit hold** stripe | On-hold customers show red left stripe on the list | | |
| 2.10 | Dynamic preview dock (click a row) | Preview panel updates without full reload | | |
| 2.11 | Detail → **Timeline tab first** | Unified timeline (quotes/SOs/invoices/payments/calls) renders | | |
| 2.12 | Customer Status (Active/Inactive/On Hold/Credit Hold) | Setting it behaves correctly | | |
| 2.13 | Deactivate then Reactivate a customer | Inactive tab + Reactivate works | | |
| 2.14 | Statement button (detail header) | Generates a statement | | |
| 2.15 | Intelligence panel (Lifetime / Open AR / Last sale / Cores / Warranty) | Numbers are net-of-credits and correct | | |

### 3 — Products & Catalog
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 3.1 | Products list loads + **paginates** (100/pg) | Large catalog doesn't render all rows at once | | |
| 3.2 | Filter tabs (In Stock / Out-of-Stock / Special-Order) | Out-of-Stock excludes special-order correctly | | |
| 3.3 | Search by SKU / part # / title | Finds the product | | |
| 3.4 | Create a product (manual) | Saves; **customer core charge ≥ vendor core** enforced | | |
| 3.5 | **F2** shortcut | Opens the expected action | | |
| 3.6 | Sell price / **margin** display | Margin honest; cost 2-dp; sell price reflects markup grid | | |
| 3.7 | Product detail — 6-tab layout | Info / Sources / Cross-Refs / Images / Suggested Sells / History all load | | |
| 3.8 | Product images | Display correctly (no broken `/static` src) | | |
| 3.9 | Vendor sources + preferred source | Add/remove source; cost from preferred reflects | | |
| 3.10 | Cross-reference confidence states | Inline status change works | | |
| 3.11 | **JAKS SKU scheme** on a generated SKU | `JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]` format | | |

### 4 — Smart Import / Review Queue  *(recently rebuilt — test hard)*
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 4.1 | Upload a SMALL CSV (a few rows) | Stages to a review batch; candidates listed | | |
| 4.2 | Upload a **LARGE PAI CSV** (thousands of rows) | **App does NOT freeze**; you're redirected immediately | | |
| 4.3 | Progress banner | "Analyzing X / total" with spinner; auto-refreshes; you can leave the page | | |
| 4.4 | Queue fills in live | Candidate count climbs each refresh until done | | |
| 4.5 | Candidate flags | New / Update / Cross-Ref / Duplicate / price Δ / needs-review / category tagged correctly | | |
| 4.6 | Review tabs + counts | Pending / Needs Review / New / Updates / Cross-Ref / Accepted / Rejected | | |
| 4.7 | Approve / Reject / Ignore selected | Bulk action updates candidates + batch tally | | |
| 4.8 | Candidate preview dock | Shows raw row + matched product | | |
| 4.9 | Absurdly large file (>100k rows) | Rejected with a clear message (not a freeze/500) | | |
| 4.10 | **Apply** approved candidates → catalog (admin-gated) | Approved rows create/update products; non-admin blocked; nothing applied without approval | | |
| 4.11 | JAKS-native export format (not just Shopify CSV) | Auto-detected + parsed correctly | | |

### 5 — Quotes
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 5.1 | Quotes list — tabs, follow-up colors, Open/Print/Email actions | All present and working | | |
| 5.2 | Create a quote; add a line one-click from search | Line adds immediately (no 2-step staging) | | |
| 5.3 | Vehicle / ESN / engine make+model block | Captured; engine picker cascades | | |
| 5.4 | **Margin %** editable → back-calculates Sell price | Typing margin recomputes price | | |
| 5.5 | Add an **Optional** item | Renders in the table, listed under "Optional Add-ons", does **NOT** raise the Total | | |
| 5.6 | Warranty tier picker + upgrade options (Economy/Recommended/Premium) | Selecting/toggling works | | |
| 5.7 | **Tier pricing**: quote for a WHOLESALE customer vs a STANDARD customer, same part | Wholesale line is **cheaper** (discount auto-applies) | | |
| 5.8 | Core charge child line on a core part | Appears automatically | | |
| 5.9 | Quote workspace actions always visible (Print/PDF, Send, Convert) | Buttons present on every status | | |
| 5.10 | Intelligence chips (Last sale / Lifetime) | Render | | |
| 5.11 | Save as Standard / duplicate / reactivate | Works | | |
| 5.12 | **Print / PDF** | Opens print view; ESN/engine/PO/Prepared-By/Terms all print; phone formatted `(XXX) XXX-XXXX` | | |
| 5.13 | Convert Quote → SO and Quote → Invoice | Both paths work | | |

### 6 — Sales Orders
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 6.1 | SO list + dashboard metric strip | 8 tiles populate | | |
| 6.2 | Per-row PO status chip (backorder / earliest ETA) | Reflects linked PO state | | |
| 6.3 | SO workspace — add line | Adds; core child line derived for core parts | | |
| 6.4 | Per-line "Order" on a backordered line | Creates a draft PO to the preferred vendor + links it | | |
| 6.5 | Set line ETA | Saves; surfaces on list rollup | | |
| 6.6 | Deposit collection (Full / Deposit / None) | Recorded | | |
| 6.7 | SO → Invoice | Builds from fulfilled lines; deposit pre-applied | | |
| 6.8 | "Invoiced" badge + Invoiced tab | Badge derives from status; void rolls SO back | | |
| 6.9 | SO print | Prints with line details + vehicle/PO/ESN | | |

### 7 — Invoices
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 7.1 | Invoice list — tabs, search (part#/phone), QBO status column | All present | | |
| 7.2 | Create / convert to invoice | Lines, totals correct | | |
| 7.3 | **Taxable toggle is authoritative** | Unchecking "Taxable" zeroes tax on every surface + through finalize; re-check restores | | |
| 7.4 | Discounts (line + header) | Compute correctly | | |
| 7.5 | Finalize | Locks; inventory down; lock badge; edit disabled | | |
| 7.6 | $0 invoice cannot finalize | Blocked | | |
| 7.7 | Void a finalized invoice | Reverses inventory + rolls SO back; audit recorded | | |
| 7.8 | CC surcharge note | Shows as informational note below Total (NOT added to math) | | |
| 7.9 | Intelligence panel (profit/margin/core liability/warranty exposure/lifetime) | Correct; margin gated by showMargin | | |
| 7.10 | After-Sale Service card (finalized invoice) | Start core-return / warranty / RA from the invoice | | |
| 7.11 | Apply credit memo to an invoice | Reduces balance correctly | | |
| 7.12 | Invoice print / PDF | Branding (logo/footer/terms), Prepared-By, phone formatting | | |

### 8 — Payments
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 8.1 | Payments list loads | Rows + tabs | | |
| 8.2 | Record a payment, allocate to invoice(s) | Allocation correct; invoice balance updates | | |
| 8.3 | **Overpayment** | Parks as unallocated (not silently lost / not a fake credit) | | |
| 8.4 | Reverse / NSF a payment | Invoice balance restored | | |
| 8.5 | Payment notes show in detail | Visible | | |
| 8.6 | Credit memo issue + list/detail | OPEN / PARTIAL / PAID states correct | | |

### 9 — Vendors
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 9.1 | Vendors list loads (L2) | Rows, search, status | | |
| 9.2 | Create / edit a vendor | Saves | | |
| 9.3 | Vendor detail | Sources / linked POs / history | | |
| 9.4 | Quick-create vendor from a product/PO | Returns option + selects it | | |

### 10 — Purchase Orders
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 10.1 | PO list loads; overdue handling | Correct | | |
| 10.2 | Create a PO; add lines one-click | Lines + totals | | |
| 10.3 | Per-line condition input | Saved | | |
| 10.4 | PO from a backordered SO line | Linked correctly | | |
| 10.5 | PO print | Prints | | |

### 11 — Receiving
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 11.1 | Receiving Queue (QB2) loads | Metrics + vendor group dividers + actions | | |
| 11.2 | Receive partial then full | **Inventory moves UP each time** (the canonical trap — verify it actually POSTs) | | |
| 11.3 | Multi-PO receipt session (same vendor) | Accepts across POs | | |
| 11.4 | Moving-average cost on receipt | Cost updates correctly | | |
| 11.5 | PO status rolls up from line fulfillment | Partial → received | | |

### 12 — Three-Way Match
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 12.1 | 3-Way Match Queue loads | Receipts → bills → variance | | |
| 12.2 | Enter a vendor bill | Saved | | |
| 12.3 | Cost variance flagged | Over-billed lines flagged for AP review | | |
| 12.4 | Accept a discrepancy | Recorded | | |
| 12.5 | **Correct a match line** | Edits PO/bill so they genuinely reconcile (→ PENDING, audited) | | |
| 12.6 | Issue a vendor credit from a variance | Vendor credit created | | |

### 13 — Cores
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 13.1 | Cores Queue (QB2) loads | Triage + navigate | | |
| 13.2 | Core charge created on sale of a core part | Tracked as liability | | |
| 13.3 | Core return from customer | State machine advances | | |
| 13.4 | Core credit issued ONCE | No double-credit (idempotent) | | |
| 13.5 | Core dashboard / metrics | Numbers correct | | |

### 14 — Returns (RA)
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 14.1 | Returns Queue (QB2) loads | Triage + navigate to /returns/{id} | | |
| 14.2 | Create an RA against an invoice | Links to the right invoice (shows invoice #) | | |
| 14.3 | Approve + per-line receive | Inventory adjusts | | |
| 14.4 | Close the RA | Lifecycle draft→open→received→closed | | |

### 15 — Warranty
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 15.1 | Warranty Queue (QB2) loads | Rows + actions | | |
| 15.2 | Create a warranty claim | Saves; state machine | | |
| 15.3 | Warranty parts + labor | Captured | | |
| 15.4 | Resolve / close claim | Works | | |

### 16 — Vendor Returns
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 16.1 | Vendor Returns list (L2) loads | Rows navigate to detail | | |
| 16.2 | Create a vendor return (incl. from a core) | Saves | | |
| 16.3 | Receive vendor credit | Recorded | | |

### 17 — Reports
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 17.1 | Each of the 9 reports loads | No errors | | |
| 17.2 | AR Aging | Buckets + totals correct (cross-check with Lifecycle E) | | |
| 17.3 | Statements | Render correctly | | |
| 17.4 | Sales / inventory / margin reports | Numbers look right | | |

### 18 — QBO (QuickBooks Online)
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 18.1 | Settings → Connect to QuickBooks | OAuth flow completes; connected status shows | | |
| 18.2 | One-time QBO item setup | Default income items created | | |
| 18.3 | Push a finalized invoice | Succeeds; invoice marked synced + locked | | |
| 18.4 | Invoice-list QBO status column + filter tabs | Synced/unsynced reflect reality | | |
| 18.5 | Bulk "Sync Selected / Sync All Unsynced" | Pushes a batch | | |
| 18.6 | Push failure is fail-soft | On error: marked sync-failed, **money path untouched** | | |
| 18.7 | cc_surcharge + tax excluded from push | Pushed totals match the accounting-summary strategy | | |

### 19 — Settings
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 19.1 | Settings tabs (Company / Pricing / QuickBooks / Shopify / Tax / System) | All load | | |
| 19.2 | Company logo upload + footer + terms | Saved; appears on printed docs | | |
| 19.3 | Markup tier grid (cost brackets) | Editable; drives product markup | | |
| 19.4 | Per-tier customer discount config | Drives tier pricing (test with 5.7) | | |
| 19.5 | Save doesn't blank other fields | No accidental wipe of unrelated settings | | |

### 20 — UI / Cross-cutting / Security
| # | Test | Expected | Mark | Notes |
|---|------|----------|------|-------|
| 20.1 | Global Ctrl+K search | Grouped results (Customers/Products/Quotes/Invoices/Vendors); ↑↓/Enter/Esc | | |
| 20.2 | Quick-create slide-overs (customer/product/vendor) | Return option + select into the originating dropdown | | |
| 20.3 | Status chips / badges consistent | Colors semantic and consistent | | |
| 20.4 | Modals (Esc to close, focus) | No raw `window.confirm`; proper dialogs | | |
| 20.5 | Login required everywhere | No unauthenticated access to data screens | | |
| 20.6 | Admin-only actions gated | Reset/reseed + logo upload require admin | | |
| 20.7 | Demo reset blocked in production | `JAKS_ENV=production` → 403 | | |
| 20.8 | Compiled Tailwind (no FOUC) | Styles load cleanly | | |
| 20.9 | Toasts / notifications | Appear and dismiss | | |

---

## PART 3 — Data-integrity spot checks (do these after the lifecycles)

| # | Check | Expected | Mark | Notes |
|---|-------|----------|------|-------|
| DI.1 | Inventory QOH for the test product = receipts − sales − returns | Exact | | |
| DI.2 | A customer's balance = Σ finalized invoices − Σ payments − Σ credits | Exact | | |
| DI.3 | A finalized invoice's total = lines + tax (+ no hidden surcharge) | Exact | | |
| DI.4 | Moving-average cost recomputed correctly after mixed-cost receipts | Exact | | |
| DI.5 | A voided invoice fully reversed inventory + SO state | No orphan stock movement | | |
| DI.6 | Core credit issued exactly once per returned core | No double-credit | | |

---

## PART 4 — Top issues / sign-off

**Blocking issues found (must fix before go-live):**
1. ____________________________________________________________________
2. ____________________________________________________________________
3. ____________________________________________________________________

**Non-blocking / polish:**
1. ____________________________________________________________________
2. ____________________________________________________________________

**Operational cutover (do before real data — not code):**
- [ ] Set a strong admin password at `/account`
- [ ] One real backup → restore drill (prove you can recover)
- [ ] Reconnect QuickBooks (live, not sandbox) when ready
- [ ] Import the real product catalog (Full Import / Smart Import)

**Go-live decision:** ☐ GO   ☐ NO-GO   — Signed: ____________  Date: ____________
