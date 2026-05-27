# JAKS Inventory

Internal inventory management system for JAKS — a B2B diesel parts distributor.
Handles quotes, sales orders, invoices, purchase orders, customer accounts, vendor tracking, core charges, warranty claims, and payments.

---

## Quick Start

Double-click **`rebuild/START JAKS.bat`**, then open **http://localhost:8000** in your browser.

Or from a terminal:

```bash
cd rebuild
pip install -r requirements.txt   # first time only
python run.py
```

---

## Tech Stack (Active App)

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python) |
| Frontend | HTMX + Alpine.js + Jinja2 templates |
| Styling | Tailwind CSS CDN + custom utility classes |
| Database | SQLite via SQLAlchemy ORM |
| PDF output | WeasyPrint |
| Platform | Windows — local, single-machine |

---

## Project Layout

```
Inventory Program/
│
├── rebuild/                    ← ACTIVE APP — work here
│   ├── app/
│   │   ├── main.py             ← FastAPI entry point + router registration
│   │   ├── database.py         ← SQLAlchemy session + init_db()
│   │   ├── models/             ← ORM models (one file per domain)
│   │   ├── routers/            ← Route handlers (thin — call service layer)
│   │   ├── services/           ← Business logic (all rules live here)
│   │   ├── templates/          ← Jinja2 HTML templates
│   │   │   └── base.html       ← Design system utility classes defined here
│   │   └── static/             ← CSS, JS, images
│   ├── requirements.txt
│   ├── run.py                  ← Uvicorn launcher script
│   └── START JAKS.bat          ← Double-click launcher (Windows)
│
├── jaks_inventory/             ← Legacy desktop app (tkinter) — reference only
├── db/                         ← Legacy database layer — reference only
├── engine/                     ← Legacy business logic — reference only
├── qbo/                        ← QuickBooks Online integration module
├── sources/                    ← Vendor web scrapers (PAI, HHP, ATL, etc.)
├── mockups/                    ← HTML design mockups
├── base44/                     ← Architecture and domain documentation
├── engine_rebuild/             ← Design specs for future engine work
│
├── run.bat                     ← Root launcher (runs the rebuild app)
└── run.ps1                     ← PowerShell launcher
```

---

## Modules (Phase 1)

| Module | Route | Notes |
|---|---|---|
| Dashboard | `/` | KPI tiles, recent activity |
| Customers | `/customers/` | Net terms, tax-exempt, discount % |
| Vendors | `/vendors/` | Vendor codes used in SKUs |
| Products | `/products/` | Cost/markup/price, core charges |
| Quotes | `/quotes/` | Live ops console — keyboard-first |
| Sales Orders | `/sales-orders/` | Quote → SO conversion flow |
| Invoices | `/invoices/` | CC surcharge, tax, ESN/customer PO |
| Payments | `/payments/` | Applied against invoices |
| Purchase Orders | `/purchase-orders/` | Receiving, freight, core tracking |
| Core Charges | `/cores/` | Customer and vendor core lifecycle |
| Returns | `/returns/` | RMA / return merchandise |
| Warranty Claims | `/warranty/` | Parts claimed, vendor credit tracking |
| Reports | `/reports/` | Sales, aging, inventory |
| Settings | `/settings/` | Company info, markup %, tax, QBO |
| Global Search | `Ctrl+K` | Searches customers, products, invoices |

---

## Design System

All reusable UI classes are defined with `@apply` in `rebuild/app/templates/base.html`.
Use these in every template for consistency:

```
Forms:   .form-input  .form-select  .form-textarea  .form-label  .form-checkbox
Cards:   .card  .card-header  .card-body  .card-title  .card-footer
Buttons: .btn-primary  .btn-secondary  .btn-danger  .btn-sm  .btn-xs
Tables:  .tbl  .tbl-head  .tbl-th  .tbl-th-r  .tbl-td  .tbl-td-r  .tbl-row  .tbl-empty
Badges:  .badge-green  .badge-red  .badge-amber  .badge-blue  .badge-gray
Links:   .link  .link-subtle
```

Brand color is **`brand-700`** — `#4b5320` (dark olive/army green).

> **Note:** Tailwind CDN loads after the `@apply` block, so CDN utility classes
> can be added directly to elements to override specifics without breaking the system.

---

## Database

The SQLite file lives at `rebuild/data/jaks.db` — **gitignored, never committed.**
It is auto-created on first run via `init_db()`.

To seed demo data after a fresh install:

```bash
cd rebuild
python seed_demo.py
python seed_invoice.py
```

---

## Phase Status

| Phase | Scope | Status |
|---|---|---|
| Phase 1 | Core CRUD, quotes, invoices, POs, payments, cores, warranty | In progress |
| Phase 2 | Credit memos, refund checks, QBO sync | Backlog |
| Phase 3 | Shopify sync, TaxJar, SMS notifications | Backlog |

Full Phase 1 checklist: [`rebuild/PHASE_1_PLAN.md`](rebuild/PHASE_1_PLAN.md)
