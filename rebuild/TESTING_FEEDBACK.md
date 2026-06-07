# JAKS Inventory — Functional Testing Feedback

**Purpose:** Screen-by-screen functional test pass. Focus is **does it work / save / calculate correctly** —
not UI polish (no major UI changes this round). Fill in the Result + Notes columns as you go.

**How to use:** Replace the `⬜` in the **Result** column with one of:

| Mark | Meaning |
|---|---|
| ✅ | Works as expected |
| ⚠️ | Works but has a quirk / partial / confusing |
| ❌ | Broken — doesn't work |
| ⬜ | Not tested yet |
| N/A | Doesn't apply / skipped |

Put specifics in **Notes** (what you clicked, what you expected, what happened). The more concrete, the
faster a lane can fix it. When done, hand this back and I'll triage every ❌/⚠️ into lane tickets.

**Tester:** Keith   **Date:** ______   **Build/commit:** ______

---

## 0. Global (test once, applies everywhere)

| # | What to test (functional) | Result | Notes |
|---|---|---|---|
| 0.1 | App loads, no broken styling, no flash of unstyled page | ⬜ | |
| 0.2 | Sidebar nav — every link opens the right screen | ⬜ | |
| 0.3 | Ctrl+K global search opens; returns Customers/Products/Quotes/Invoices/Vendors/SOs/POs | ⬜ | |
| 0.4 | Ctrl+K — ↑↓ navigate, Enter opens result, Esc closes | ⬜ | |
| 0.5 | Notifications bell — count shows, panel opens, acknowledge clears | ⬜ | |
| 0.6 | Inline quick-create (customer/product/vendor) from a dropdown adds + selects it | ⬜ | |
| 0.7 | Toasts appear on save actions and auto-dismiss | ⬜ | |

---

## 1. SALES

### 1.1 Customers List
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Search by company name (partial) returns matches | ⬜ | |
| b | Search by phone (with/without dashes) returns matches | ⬜ | |
| c | Search by email / contact returns matches | ⬜ | |
| d | Filter tabs switch the list; counts match reality | ⬜ | |
| e | Click row → preview dock loads correct customer (balance, open docs, cores, last sale, terms) | ⬜ | |
| f | "New Customer" / quick-create saves | ⬜ | |
| g | Creating a near-duplicate name triggers the duplicate warning (View Existing / Create Anyway) | ⬜ | |

### 1.2 Customer Detail
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Tabs load: Account / Invoices / Quotes / Call Log / Sales Orders | ⬜ | |
| b | Edit Customer → change a field → Save persists after refresh | ⬜ | |
| c | Log a call → appears in Call Log | ⬜ | |
| d | Balance mini-panel numbers match the customer's actual open invoices | ⬜ | |
| e | "Open quotes" figure links to that customer's quotes | ⬜ | |
| f | New Quote / New Invoice from header opens with customer pre-filled | ⬜ | |
| g | Generate Statement → form → print/PDF shows correct activity + aging | ⬜ | |
| h | Customer Excel/CSV import → review → confirm creates customers | ⬜ | |

### 1.3 Quotes List
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Tabs + counts correct (All/Draft/Sent/Converted/Declined/Expired) | ⬜ | |
| b | Search by quote # or customer works | ⬜ | |
| c | Row preview dock loads | ⬜ | |
| d | Margin % column shows a sensible value | ⬜ | |
| e | AR warning chip appears for customers with an open balance | ⬜ | |
| f | "→ Invoice" on a row converts the quote to an invoice | ⬜ | |
| g | New Quote modal → pick customer → opens the workspace | ⬜ | |

### 1.4 Quote Workspace  *(the 45-second-quote screen — test hard)*
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Select customer; header shows terms / open AR / tax-exempt / note | ⬜ | |
| b | Search a part → select → **+ Add Line** adds it (note: 2-step staging) | ⬜ | |
| c | Edit Qty / Sell / Disc % → totals + Margin % recompute correctly | ⬜ | |
| d | QOH dot color matches stock (green ≥2 / amber 1 / red 0) | ⬜ | |
| e | Autosave fires (indicator updates); reload keeps changes | ⬜ | |
| f | Follow-up bar buttons set follow-up state | ⬜ | |
| g | Warranty tier picker adds a warranty line at the right price | ⬜ | |
| h | Upgrade options (Economy/Recommended/Premium) select + include/exclude | ⬜ | |
| i | Research status on a line saves | ⬜ | |
| j | Convert → SO (out of stock) and Convert → Invoice (in stock) both work | ⬜ | |
| k | Mark Lost / Reactivate / Duplicate each behave correctly | ⬜ | |
| l | Print / PDF renders the quote correctly | ⬜ | |
| m | Send quote logs/records the send | ⬜ | |

### 1.5 Sales Orders List
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Tabs + counts correct | ⬜ | |
| b | Row preview dock loads (fulfillment/payment/invoice status) | ⬜ | |

### 1.6 Sales Order Workspace
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Add line → fulfillment source set correctly (stock vs backorder vs linked PO) | ⬜ | |
| b | Collect deposit (Full/Deposit/None) records payment | ⬜ | |
| c | Hold / Release hold change status | ⬜ | |
| d | Fulfill → creates an invoice from fulfilled lines; deposit carries over | ⬜ | |
| e | Cancel SO releases committed qty | ⬜ | |
| f | Print / PDF renders | ⬜ | |

### 1.7 Invoices List
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Tabs + counts (All/Draft/Open/Partial/Overdue/Paid/Void) correct | ⬜ | |
| b | Overdue rows flagged; balance due correct | ⬜ | |
| c | Lock badge shows on locked invoices | ⬜ | |
| d | Row preview dock loads | ⬜ | |

### 1.8 Invoice Workspace
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Header autosave; add / edit / remove lines persist | ⬜ | |
| b | Change customer works | ⬜ | |
| c | **Finalize** → locks invoice, decrements inventory, creates core charges for core items | ⬜ | |
| d | Record Payment (modal) → balance due updates | ⬜ | |
| e | Apply customer credit → balance updates | ⬜ | |
| f | Void → reverses inventory; blocked if paid/QBO-pushed | ⬜ | |
| g | Locked invoice rejects edits | ⬜ | |
| h | Print / PDF renders | ⬜ | |

### 1.9 Payments List / New / Detail
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Record payment → allocate across one or more invoices | ⬜ | |
| b | Overpayment prompts to credit customer (never silent) | ⬜ | |
| c | Reverse payment → reopens invoice, restores credit if applicable | ⬜ | |
| d | NSF → marks payment, creates NSF fee | ⬜ | |
| e | Card payment computes surcharge on the card portion | ⬜ | |

---

## 2. PURCHASING

### 2.1 Vendors List
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Tabs + counts correct | ⬜ | |
| b | Row preview dock loads (open POs / bills / credits / last PO) | ⬜ | |
| c | Quick-create vendor saves | ⬜ | |

### 2.2 Vendor Detail
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Edit → Save persists | ⬜ | |
| b | Add contact | ⬜ | |
| c | Deactivate vendor | ⬜ | |

### 2.3 Purchase Orders List
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Tabs + counts correct | ⬜ | |
| b | Overdue/ETA stripe correct | ⬜ | |
| c | Row preview dock loads | ⬜ | |

### 2.4 PO Workspace  *(inventory integrity — test carefully)*
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Header autosave; add lines persist | ⬜ | |
| b | Send to vendor sets status | ⬜ | |
| c | **Receive** (partial + full) → inventory qty goes UP by received amount | ⬜ | |
| d | Receiving updates moving-average cost + last cost | ⬜ | |
| e | Create Vendor Bill → 3-way match; auto-approves when qty/cost match | ⬜ | |
| f | Mismatched bill → flagged as discrepancy (not auto-approved) | ⬜ | |
| g | Resolve discrepancy line works | ⬜ | |
| h | Cancel PO / cancel line works | ⬜ | |
| i | Print / PDF renders | ⬜ | |

### 2.5 PO Receiving Queue
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Metrics strip numbers correct (open / due / partial / flagged) | ⬜ | |
| b | Grouped by vendor; urgent first | ⬜ | |
| c | Receive / Match / Open / Print actions go to the right place | ⬜ | |

### 2.6 PO 3-Way Match Queue
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Flagged bills listed with qty Δ / cost Δ | ⬜ | |
| b | Each row links back to the PO's match panel | ⬜ | |
| c | Resolving a flag clears it from the queue | ⬜ | |

---

## 3. INVENTORY

### 3.1 Products List
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Tabs + counts correct | ⬜ | |
| b | Search by SKU / OEM / description | ⬜ | |
| c | Stock health stripe + QOH chip correct | ⬜ | |
| d | Margin % badge correct | ⬜ | |
| e | Row preview dock loads | ⬜ | |
| f | Bulk select + bulk action works | ⬜ | |

### 3.2 Product Detail
| # | What to test | Result | Notes |
|---|---|---|---|
| a | All 6 tabs load (Info / Sources / Cross-Refs / Images / Suggested Sells / History) | ⬜ | |
| b | Edit → Save persists | ⬜ | |
| c | Add vendor source; set preferred | ⬜ | |
| d | Add cross-ref; change confidence status | ⬜ | |
| e | Upload image → appears in grid | ⬜ | |
| f | Add suggested-sell | ⬜ | |
| g | Adjust inventory → qty changes + logs a transaction | ⬜ | |

---

## 4. CORES

### 4.1 Cores List / lifecycle
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Core charge auto-appears after invoicing a core item | ⬜ | |
| b | Record customer return → inspection outcome (Accepted/Hold/Rejected) routes to right location | ⬜ | |
| c | Complete inspection (Hold case) resolves | ⬜ | |
| d | Submit to vendor → status changes | ⬜ | |
| e | Vendor accepted / denied / credit-difference each behave | ⬜ | |
| f | Issue credit (Account credit vs Check) updates balance correctly | ⬜ | |
| g | Core slip print + Vendor Core Return (VCR) print render | ⬜ | |
| h | Overdue core flagged after grace window | ⬜ | |

---

## 5. RETURNS & WARRANTY

### 5.1 Returns (RA)
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Create RA with lines + restocking fee | ⬜ | |
| b | Approve → Receive → Close lifecycle | ⬜ | |
| c | Closing credits the customer correctly | ⬜ | |
| d | Print / PDF renders | ⬜ | |

### 5.2 Warranty Claims
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Create claim with product lines + credit amounts | ⬜ | |
| b | Submit to vendor → vendor decision (approve/deny) | ⬜ | |
| c | Credit customer on approval | ⬜ | |
| d | Notify denial / Close | ⬜ | |
| e | Print / PDF renders | ⬜ | |

### 5.3 Vendor Returns
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Create → Ship → vendor Decision → Close | ⬜ | |

---

## 6. REPORTS  *(check each loads + numbers look right)*

| # | Report | Loads? | Numbers correct? | Notes |
|---|---|---|---|---|
| a | AR Aging | ⬜ | ⬜ | |
| b | Sales by Customer | ⬜ | ⬜ | |
| c | Sales by Product | ⬜ | ⬜ | |
| d | Inventory Valuation | ⬜ | ⬜ | |
| e | Open POs | ⬜ | ⬜ | |
| f | Outstanding Cores | ⬜ | ⬜ | |
| g | Overdue Invoices | ⬜ | ⬜ | |
| h | Sales Tax | ⬜ | ⬜ | |
| i | Lost Sales | ⬜ | ⬜ | |

---

## 7. SYSTEM

### 7.1 Dashboard
| # | What to test | Result | Notes |
|---|---|---|---|
| a | KPI tiles show real numbers (today's payments, AR, overdue) | ⬜ | |
| b | Widgets (open SOs, follow-ups due, open POs, outstanding cores) match reality | ⬜ | |
| c | Tile/widget links navigate to the right filtered screen | ⬜ | |

### 7.2 Settings
| # | What to test | Result | Notes |
|---|---|---|---|
| a | Edit a setting → Save persists after refresh | ⬜ | |
| b | Number sequences / company info reflected on documents | ⬜ | |

---

## 8. Cross-workflow end-to-end (the real proof)

Run a full lifecycle and confirm the data stays consistent at each hop.

| # | Flow | Result | Notes |
|---|---|---|---|
| a | New vendor + product → PO → receive → inventory ↑ + cost updated | ⬜ | |
| b | Quote (in stock) → Invoice → finalize → inventory ↓ → record payment → paid | ⬜ | |
| c | Quote (out of stock) → SO → deposit → linked PO receive → fulfill → invoice | ⬜ | |
| d | Invoice with core item → core charge → customer return → vendor return → credit | ⬜ | |
| e | Overdue invoice → statement shows it in the right aging bucket | ⬜ | |

---

## Overall notes / top issues

> Free-form: anything that blocked you, felt wrong, or you want prioritized.

1.
2.
3.
