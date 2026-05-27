# JAK's Diesel PRO — Project Tracker

> Living document. Tick boxes as work completes. Updated 2026‑05‑19.
> **MVP North Star:** enter products → quote → sell → PO → QBO sync — without leaving the app or touching a legacy dialog.

---

## 0. How to use this tracker

1. **Section 1 — MVP Critical Path.** This is the only thing that matters right now. Walk every step end-to-end in the running app and tick each checkbox. Anything ❌ becomes the *next* ticket.
2. **Section 2 — Tab Inventory.** One row per sidebar item. Theme status, wiring status, what dialogs it still opens, and what's blocking sign‑off.
3. **Section 3 — UI Revitalization Order.** Tab-by-tab rollout plan. Don't refactor anything that isn't in the current waveband.
4. **Section 4 — QBO Sync.** Separate scoreboard — sync is its own beast.
5. **Section 5 — Cross-cutting Cleanup.** `.bak` files, duplicate screens, legacy palettes — chip away between feature waves.
6. **Section 6 — Working Agreements.** Rules to keep the refactor from sliding sideways.

Legend used throughout:
`✅ done` · `🟡 partial / works but not refreshed` · `🔴 legacy / broken` · `⚫ stub / placeholder` · `🧪 needs manual verification`

---

## 1. MVP Critical Path — Verification Checklist

Run each scenario in this order. Stop and file an issue the moment a step doesn't behave as described.

### 1.1 Enter a product (Inventory → Products)
- [ ] **+ New Product** (header button) opens the Add Product dialog (not the old light-theme one)
- [ ] Required fields: SKU, Item, Type (NEW/REMAN/USED/CORE/KIT), Vendor, Cost, List — all save
- [ ] New row appears in table without a manual refresh (cross-window `product_changed` signal)
- [ ] **Quick Entry** (More ▾) keyboard-only flow works for ≥5 products in a row
- [ ] **AI Catalog Import** (header) — confirm whether real or stub; mark ⚫ if stub
- [ ] **Import CSV** (header) → Bulk Import screen → mapping → commit → rows appear in Products
- [ ] Edit cell inline: Cost, List, Reorder, Vendor, Category — saves on focus-out, persists across refresh
- [ ] Type/Status/Flags columns render correctly (pill colors, emoji)
- [ ] On Order column shows >0 for any SKU that has an open PO line ← *new wiring, must verify*

### 1.2 Quote a product (Sales → Quotes)
- [ ] **New Quote** opens dark `quote_dialog`, not legacy
- [ ] Customer picker finds & creates customers
- [ ] Part picker finds products by SKU / OEM xref / vendor SKU / ESN
- [ ] Tier pricing applies automatically when a tiered customer is selected
- [ ] Core charges appear on eligible items and total correctly
- [ ] Save Draft → quote shows in list with status `Draft`
- [ ] Send Quote → SMS/email goes out (or queues if mock); status flips to `Sent`
- [ ] Convert to Sales Order — see 1.3

### 1.3 Sell (Sales → Sales Orders → Invoice)
- [ ] Convert Quote → SO (new_so_dialog) carries lines, customer, pricing
- [ ] Allocate stock — qty reservations show on Products screen
- [ ] Print Pick Ticket
- [ ] Convert SO → Invoice
- [ ] Record Payment (cash / card / check) — payment_dialog
- [ ] Invoice sent (SMS / email) and marked Paid
- [ ] AR aging reflects the new invoice immediately

### 1.4 PO flow (Purchasing → POs)
- [ ] Low Stock tab lists items below reorder
- [ ] **Create PO from Tagged** (Products screen) builds a PO grouped by vendor
- [ ] PO dialog: add/edit lines, discounts, ETA, notes
- [ ] Send PO to vendor (PDF / email)
- [ ] Receive PO (receive_dialog) — partial + full
- [ ] Qty on Hand increments, On Order decrements on Products screen
- [ ] AP bill created and visible in Accounting

### 1.5 QBO sync
- [ ] Settings → QBO tab: mode set to **read_write**, creds present, status pill green
- [ ] Sync Center: row counts non-zero, **Last Sync** timestamps recent
- [ ] Create a new local product → it appears in QBO Items within 60 s (or after manual push)
- [ ] Create a new local invoice → QBO Invoice mirrors it; payment posts back
- [ ] Vendor bill from a received PO syncs to QBO Bill
- [ ] Webhook worker drained queue (no rows stuck `pending` > 5 min)
- [ ] Status pill shows `0 failed` after a full cycle

---

## 2. Tab Inventory & Status

Columns:
- **Theme**: ✅ dark mockup · 🟡 partial · 🔴 legacy
- **Wired**: ✅ end-to-end · 🟡 partial · ⚫ stub
- **Blockers**: what stops this tab from being "done"

### Dashboard
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Dashboard | [dashboard_screen.py](jaks_inventory/ui/dashboard_screen.py) | ✅ | 🟡 | Chart cards depend on `PySide6.Charts`; verify install. Some KPI tiles point at deprecated counters. |

### Sales
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Quotes | [quotes_screen.py](jaks_inventory/ui/quotes_screen.py) + [quote_dialog.py](jaks_inventory/ui/quote_dialog.py) | ✅ | ✅ | None known. Verify ESN/xref finder still routes through Part Finder. |
| Lost Sales | [lost_sales_screen.py](jaks_inventory/ui/lost_sales_screen.py) | 🟡 | 🟡 | Header bar + filter row not yet on mockup. |
| Sales Orders | [sales_orders_screen.py](jaks_inventory/ui/sales_orders_screen.py) + [new_so_dialog.py](jaks_inventory/ui/new_so_dialog.py) | ✅ | ✅ | Pick-ticket PDF template needs a refresh. |
| Invoices | [invoices_screen.py](jaks_inventory/ui/invoices_screen.py) + [invoice_dialog.py](jaks_inventory/ui/invoice_dialog.py) | ✅ | ✅ | Payment-method icons inconsistent; SMS template uses old branding. |
| Deliveries | [deliveries_screen.py](jaks_inventory/ui/deliveries_screen.py) | 🟡 | 🟡 | Map/route view is placeholder text. |
| CRM | [crm_screen.py](jaks_inventory/ui/crm_screen.py) | 🟡 | 🟡 | Activity feed lacks filtering; not aligned with Customers Hub. |
| Returns | [returns_screen.py](jaks_inventory/ui/returns_screen.py) + [invoice_return_dialog.py](jaks_inventory/ui/invoice_return_dialog.py) | 🟡 | ✅ | Needs dark refresh; RGA → vendor return handoff already lives in Vendor Returns. |

### Core Processing
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Processing Dashboard | [processing_center_screen.py](jaks_inventory/ui/processing_center_screen.py) | 🟡 | 🟡 | Cards still legacy; needs the same KPI/chip pattern as Products. |
| Customer Cores | [customer_cores_screen.py](jaks_inventory/ui/customer_cores_screen.py) | 🟡 | 🟡 | Credit-apply flow needs end-to-end test post core return. |
| Vendor Returns | [vendor_returns_combined_screen.py](jaks_inventory/ui/vendor_returns_combined_screen.py) | ✅ | ✅ | None — recent rebuild. Retire old [vendor_returns_screen.py](jaks_inventory/ui/vendor_returns_screen.py). |

### Inventory
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Products | [products_screen.py](jaks_inventory/ui/products_screen.py) | ✅ | 🧪 | Just refactored. Manually verify: inline edits, type pill, on-order column, flags emoji, footer summary. |
| Bulk Import | [bulk_import_screen.py](jaks_inventory/ui/bulk_import_screen.py) | 🟡 | ✅ | Legacy stepper UI. |
| Adjustments | [adjustments_screen.py](jaks_inventory/ui/adjustments_screen.py) | 🟡 | ✅ | Should match Products header pattern. |
| Locations | [locations_screen.py](jaks_inventory/ui/locations_screen.py) | 🟡 | 🟡 | Multi-warehouse model partially in schema; UI shows single warehouse only. |
| Kits | [kits_screen.py](jaks_inventory/ui/kits_screen.py) | 🟡 | 🟡 | Explode-on-sale path needs verification. |
| Audit | [audit_screen.py](jaks_inventory/ui/audit_screen.py) | 🟡 | 🟡 | Will absorb the Recent Activity panel that was removed from Products. |

### Purchasing
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Purchase Orders | [po_screen.py](jaks_inventory/ui/po_screen.py) + [po_dialog.py](jaks_inventory/ui/po_dialog.py) | 🟡 | ✅ | Header & KPIs not aligned with mockup. |
| PO Receipts | [po_screen.py](jaks_inventory/ui/po_screen.py) (tab) + [receive_dialog.py](jaks_inventory/ui/receive_dialog.py) | 🟡 | ✅ | Same screen as POs; split or label-up clearly. |
| Vendors | [vendors_screen.py](jaks_inventory/ui/vendors_screen.py) + [vendor_dialog.py](jaks_inventory/ui/vendor_dialog.py) | 🟡 | ✅ | Needs dark refresh; payment terms field underused. |
| Low Stock & Reorder | [po_screen.py](jaks_inventory/ui/po_screen.py) (tab) + [restock_wizard_dialog.py](jaks_inventory/ui/restock_wizard_dialog.py) | 🟡 | ✅ | Wizard layout legacy. |

### Pricing
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Price Lists | [price_lists_screen.py](jaks_inventory/ui/price_lists_screen.py) | 🟡 | 🟡 | |
| Pricing Maintenance | [pricing_maintenance_screen.py](jaks_inventory/ui/pricing_maintenance_screen.py) | 🟡 | 🟡 | Mass-reprice preview missing. |
| Tiered Pricing | [tiered_pricing_screen.py](jaks_inventory/ui/tiered_pricing_screen.py) | 🟡 | 🟡 | |

### Customers
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Customers Hub | [customers_hub_screen.py](jaks_inventory/ui/customers_hub_screen.py) | 🟡 | ✅ | Internal tab styling mixed; ESN history tab is the strongest. |

### Marketing
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Text Messaging | [messaging_screen.py](jaks_inventory/ui/messaging_screen.py) | 🟡 | 🟡 | Inbound queue limited. |
| SMS Campaigns | [marketing_screen.py](jaks_inventory/ui/marketing_screen.py) | 🟡 | 🟡 | Audience builder works; analytics shallow. |
| Automation | [automation_screen.py](jaks_inventory/ui/automation_screen.py) | 🟡 | 🟡 | Rule executor needs test runner. |

### Accounting
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Margins | [margin_screen.py](jaks_inventory/ui/margin_screen.py) | 🟡 | 🟡 | |
| QBO Sync Center | [sync_center.py](jaks_inventory/ui/sync_center.py) | ✅ | 🟡 | Manual replay of failed webhook events still missing — see Section 4. |
| QBO Reconciliation | [qbo_screen.py](jaks_inventory/ui/qbo_screen.py) | 🟡 | ⚫ | Read-only inspector; no repair tooling. |
| Aging AR | [aging_screen.py](jaks_inventory/ui/aging_screen.py) | 🟡 | ✅ | |
| Reports | [reports_screen.py](jaks_inventory/ui/reports_screen.py) | 🟡 | 🟡 | Builder works; saved-report library thin. |

### Tools
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Part Finder | [part_finder_screen.py](jaks_inventory/ui/part_finder_screen.py) | 🟡 | ✅ | |
| Barcodes | [barcode_screen.py](jaks_inventory/ui/barcode_screen.py) | 🟡 | 🟡 | Print Labels button on Products screen should land here. |
| Import | [import_screen.py](jaks_inventory/ui/import_screen.py) | 🟡 | 🟡 | Overlaps with Bulk Import — clarify scope. |
| HHP Scraper | [hhp_scraper_screen.py](jaks_inventory/ui/hhp_scraper_screen.py) | 🟡 | 🟡 | |
| Scraper Admin | [scraper_screen.py](jaks_inventory/ui/scraper_screen.py) | 🟡 | 🟡 | Cummins / Holset scrapers still pending. |

### Settings
| Tab | File | Theme | Wired | Blockers |
|-----|------|-------|-------|----------|
| Settings (all 8 sub-tabs) | [settings_screen.py](jaks_inventory/ui/settings_screen.py) | 🔴 | ✅ | **Highest theme-debt** screen. QBO tab is functionally critical but visually legacy. |

---

## 3. UI Revitalization Order

Each wave is one focused work session. Don't skip ahead — each wave depends on the prior wave's pattern (KPI strip, attention chips, single filter row, footer summary, dark palette tokens).

### Wave 1 — Inventory baseline (in progress)
- ✅ Products screen — done this session
- [ ] **Bulk Import** — dark theme, header bar, drag-drop drop zone, mapping table styling
- [ ] **Adjustments** — KPI strip (today/week/month qty deltas), attention chip "needs reason code"
- [ ] **Locations** — single-warehouse list now; placeholder card for multi-warehouse upgrade
- [ ] **Kits** — explode preview pane
- [ ] **Audit** — absorb Recent Activity (now hidden on Products); filter by entity

### Wave 2 — Purchasing
- [ ] **POs** — header bar (New PO / Receive / Print / Email Vendor), KPI strip (open / overdue / value / received this week), filter row pattern
- [ ] **PO Receipts** — split visually from POs tab (still same file, different sub-tab UI)
- [ ] **Vendors** — dark refresh, terms / contact / on-time % chips
- [ ] **Low Stock & Reorder** — wizard rebuild with same chip pattern

### Wave 3 — Sales polish
- [ ] **Lost Sales** — header + filter pattern
- [ ] **Deliveries** — route view stub → real map widget (or table-only if map deferred)
- [ ] **CRM** — fold into Customers Hub OR rebuild as activity feed
- [ ] **Returns** — dark refresh, link to Vendor Returns
- [ ] **Quotes / SOs / Invoices** — already ✅; only touch for shared component upgrades

### Wave 4 — Core Processing
- [ ] **Processing Dashboard** — KPIs (cores in / cores out / awaiting credit), attention chips
- [ ] **Customer Cores** — credit-apply E2E test + dark refresh
- [ ] **Vendor Returns** — already ✅

### Wave 5 — Pricing
- [ ] All three pricing screens get the standard pattern; add **preview** to Pricing Maintenance

### Wave 6 — Customers / Marketing / Reports
- [ ] **Customers Hub** — internal tab styling pass
- [ ] **Messaging / Campaigns / Automation** — dark refresh + analytics cards
- [ ] **Margins / Aging / Reports** — KPI strip pattern, exportable

### Wave 7 — Tools
- [ ] **Part Finder**, **Barcodes**, **Import**, **Scrapers** — dark refresh
- [ ] Decide fate of **Import** vs **Bulk Import** (merge?)

### Wave 8 — Settings & QBO UX
- [ ] **Settings** — top-to-bottom dark rebuild (highest visibility legacy screen)
- [ ] **QBO Reconciliation** — design real repair tooling (push button per drifted row)
- [ ] Decide: Settings → QBO tab vs Sync Center → consolidate or formally split

### Wave 9 — Cross-cutting polish
- [ ] Delete `.bak` files after git review
- [ ] Retire deprecated `vendor_returns_screen.py`
- [ ] Adopt `theme.py` tokens everywhere — eliminate hard-coded `#0f1419` / `#1a2128`
- [ ] Adopt `auto_refresh_mixin` on every list screen
- [ ] Adopt cross-window signals (`customer_changed`, `product_changed`, `quote_changed`, `invoice_changed`) on every CRUD action

---

## 4. QBO Sync Scoreboard

Treat this as its own product. Track separately from the UI refresh.

### Foundation
- ✅ OAuth2 connect / token refresh
- ✅ Mock / read-only / read-write mode toggle
- ✅ Sync queue + retry with exponential backoff
- ✅ Webhook receiver + 60 s drain worker
- ✅ Status pill in main window (mode + pending + failed)
- ✅ Sync Center screen — entity grid, counts, manual actions

### Entity coverage
| Entity | Push | Readback | Webhook | Notes |
|--------|------|----------|---------|-------|
| Items (Products) | ✅ | 🟡 | ✅ | Verify cost vs price field mapping |
| Customers | ✅ | ✅ | ✅ | |
| Vendors | ✅ | ✅ | ✅ | |
| Invoices | ✅ | 🟡 | ✅ | |
| Payments | ✅ | 🟡 | ✅ | |
| Sales Receipts | 🟡 | 🟡 | 🟡 | |
| Estimates (Quotes) | 🟡 | ⚫ | ⚫ | Mapping defined; flow untested |
| Purchase Orders | ✅ | 🟡 | ✅ | |
| Bills (from received POs) | ✅ | 🟡 | ✅ | |
| Credit Memos | 🟡 | ⚫ | ⚫ | |
| Journal Entries (adjustments) | 🟡 | n/a | n/a | |
| Core charges | 🟡 | ⚫ | ⚫ | Charge mapping unique to JAK |
| Period close | 🟡 | n/a | n/a | |
| Taxes | ⚫ | ⚫ | ⚫ | Schema partial; no UI |

### Outstanding work
- [ ] Webhook event **manual replay** UI (Sync Center)
- [ ] QBO Reconciliation: repair / push actions per drifted row
- [ ] Tax sync end-to-end
- [ ] Settings QBO tab dark refresh
- [ ] Health check page (last error, last success, queue depth, throttle status)
- [ ] Auto-throttle visualization (current backoff window)

---

## 5. Cross-Cutting Cleanup

Small chores. Knock these out between waves.

- [ ] Verify and delete: `invoices_screen.py.bak`, `main_window.py.bak`, `products_screen.py.bak*`, `quotes_screen.py.bak`, `sales_orders_screen.py.bak`, `new_so_dialog.py.bak`
- [ ] Delete deprecated `vendor_returns_screen.py` once nothing imports it
- [ ] Centralize palette: replace literal hex colors in screens with `theme.py` getters
- [ ] Confirm `shopify_orders_screen.py` is wanted; either wire into nav or remove
- [ ] Audit `auto_refresh_mixin` adoption — list which screens use it
- [ ] Audit cross-window signal emission — every CRUD save should fire one
- [ ] Settings screen: rip out light-theme stylesheet
- [ ] Decide canonical SMS branding template; remove the old one

---

## 6. Working Agreements

To keep this from going sideways:

1. **One wave at a time.** Don't start Wave 3 while Wave 1 has open items.
2. **Pattern parity.** Every list screen follows: header bar → KPI strip → attention chips → single filter row → table → footer summary. No exceptions.
3. **Theme tokens only.** All new code reads palette from `theme.py`. No `#hex` literals in widgets.
4. **Cross-window signals.** Every CRUD save in a dialog must emit the matching signal so other open screens refresh without F5.
5. **Compile + smoke test after each edit.** `py_compile` then relaunch; click through the touched flow.
6. **Don't delete data flows you don't understand.** Hide widgets first, prove nothing breaks, then remove on a follow-up pass.
7. **Mockups are the source of truth** for layout decisions. If the mockup is stale, update the mockup *first*, then the code.
8. **Backup files are not version control.** Use git; delete `.bak` once reviewed.

---

## 7. Sprint Recommendation (next 1–2 sessions)

Given the user's stated needs (enter products → quote → sell → PO → QBO sync):

**Session A — Lock down Products + QBO Items**
1. Walk Section 1.1 checklist live in the app; file any ❌ as immediate fixes.
2. Push a fresh product end-to-end into QBO (mock then live).
3. Verify the new `get_on_order_qty_batch` query returns expected numbers against a known PO.

**Session B — Purchasing baseline (Wave 2 partial)**
1. POs screen header + KPI strip pass.
2. Walk Section 1.4 checklist end-to-end.
3. Confirm received PO → AP bill → QBO Bill sync.

**Session C — Settings + QBO UX**
1. Dark-refresh Settings (Wave 8 first item).
2. Add webhook replay button to Sync Center.
3. Walk Section 1.5 checklist end-to-end.

After those three, you'll be able to *run the business* in the new UI; everything else becomes polish.
