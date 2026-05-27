# JAK's Diesel PRO — Base44 Assembly Specification

This folder is a **complete, self-contained brief** that another AI app-builder
(Base44 or equivalent) can read end-to-end and reproduce the JAK's Diesel PRO
inventory + ERP system.

It mirrors the existing PySide6 desktop app that lives in this repo. Where the
spec mentions a screen, dialog, or process, the corresponding source code path
is given so a builder can cross-check behavior.

---

## How to read this folder

Read the files **in order**. Each one builds on the previous.

| # | File | What it covers |
|---|------|----------------|
| 00 | [00_overview.md](00_overview.md) | What the product is, who uses it, what problem it solves |
| 01 | [01_glossary.md](01_glossary.md) | Domain terms (core charge, REMAN, ESN, PAI, RGA, etc.) |
| 02 | [02_data_model.md](02_data_model.md) | All entities, columns, relationships, lifecycles |
| 03 | [03_business_rules.md](03_business_rules.md) | Pricing tiers, cores, warranties, taxes, returns logic |
| 04 | [04_core_processes.md](04_core_processes.md) | Quote→SO→Invoice→Payment, PO→Receive→Bill, Core flow, Returns |
| 05 | [05_design_system.md](05_design_system.md) | Palette, components, layout patterns, KPI strips |
| 06 | [06_navigation.md](06_navigation.md) | Sidebar tree, screen list, keyboard shortcuts |
| 07 | [07_modules/](07_modules/) | One spec per module (11 files: Dashboard, Inventory, Sales, Purchasing, Core Processing, Pricing, Customers, Marketing, Accounting, Tools, Settings) |
| 08 | [08_integrations/](08_integrations/) | PAI scraper, HHP scraper, ATL scraper, Shopify, QBO, SMS |
| 09 | [09_workflows.md](09_workflows.md) | Step-by-step user flows (clickstreams) |
| 10 | [10_build_phases.md](10_build_phases.md) | Recommended Base44 build order (8 phases) |
| 11 | [11_mockups.md](11_mockups.md) | Index of HTML mockups in `../mockups/` to use as visual reference |

---

## Source-of-truth file map (existing PySide6 app)

When a spec refers to "the existing app", these are the canonical files:

```
db/inventory.py            ← single largest module, all data access
db/migrations/*.py         ← 115 schema migrations (read in order to derive schema)
jaks_inventory/ui/*.py     ← every screen + dialog (100+ files)
jaks_inventory/scraper/    ← PAI scraper, HHP bridge/workers, Cummins scraper
sources/                   ← ATL Diesel, HHP, FleetPride, DPD competitor scrapers
phases/                    ← 5-phase HHP scrape pipeline (scan→scrape→PAI→review→upload)
jaks_inventory/services/   ← AI assistants, match engine, part search
jaks_inventory/shopify/    ← Shopify sync
qbo/                       ← QuickBooks Online sync
mockups/*.html             ← Visual reference (open in browser)
```

---

## Non-negotiable principles for any rebuild

1. **Dark theme only.** Background `#0c1116`, panel `#131a22`, text `#e6edf3`.
2. **Single filter row per list screen.** No nested filter panels.
3. **KPI strip at the top of every list screen.** 3–6 tiles, no charts in the strip.
4. **Attention chips** call out problems ("3 below reorder", "2 stale costs").
5. **Cross-window signals** on every CRUD save (`product_changed`,
   `customer_changed`, `quote_changed`, `invoice_changed`, `po_changed`).
6. **Auto-refresh** any open list when its underlying entity changes.
7. **Real money never silently rounds.** All currency stored as `REAL` USD.
   Display with 2-decimal formatting; never truncate.
8. **Every action that mutates inventory must be auditable.** See
   [02_data_model.md](02_data_model.md) → `inventory_audit` table.
9. **QBO is the accounting source of truth for AR/AP.** The inventory app
   owns SKUs, on-hand, costs, customers; QBO owns ledger and tax filings.
10. **Cores are first-class.** A core charge is not a fee — it is a tracked
    obligation tied to a serialized refundable part. See
    [03_business_rules.md#cores](03_business_rules.md).

---

## Tech-stack guidance (if Base44 has discretion)

The existing app is desktop (PySide6 / SQLite). For a Base44 web rebuild:

- **Frontend:** React-style components, dark theme baked in.
- **Backend:** Node/Python — irrelevant as long as the data model matches.
- **DB:** PostgreSQL preferred for new builds (existing app already supports it,
  see [db/database.py](../db/database.py) `PostgreSQLConnection`).
- **Async jobs:** scrapers and QBO sync must run as background workers, not
  in the request thread.
- **Auth:** Single-tenant (one company). User roles: `admin`, `sales`,
  `purchasing`, `warehouse`, `viewer`. See [03_business_rules.md#permissions](03_business_rules.md).
- **File storage:** Product images, invoice PDFs, vendor docs — object storage.

---

## Out of scope for the rebuild

These features exist in the desktop app but are explicitly **optional** for a
Base44 first cut:

- Multi-warehouse locations (single warehouse OK for MVP)
- Marketing automation rules engine
- Cummins-specific scraper (PAI + HHP + ATL is sufficient)
- QBO webhook replay UI (manual reconciliation is acceptable)
- Period close wizard (annual operation; can be deferred)
- ESN (Engine Serial Number) lookup (nice-to-have)
- Daily delivery routes / map view

Everything else in this spec is **required** for parity.
