# rebuild/ — JAKS Inventory (Phase 1 Active App)

This is the active FastAPI + HTMX rebuild of JAKS Inventory.
All day-to-day development happens in this folder.

## Run it

```bat
START JAKS.bat
```

or:

```bash
python run.py
```

Then open **http://localhost:8000**.

## Folder structure

```
rebuild/
├── app/
│   ├── main.py             ← FastAPI app, router registration, startup
│   ├── database.py         ← SQLAlchemy engine, SessionLocal, init_db()
│   ├── constants.py        ← Enums, shared constants (PaymentTerms, etc.)
│   ├── deps.py             ← FastAPI dependency injection (get_db)
│   ├── seeds.py            ← Seed data helpers
│   ├── settings_utils.py   ← Settings key/value helpers
│   ├── utils.py            ← Misc utilities
│   │
│   ├── models/             ← SQLAlchemy ORM models
│   │   ├── customer.py
│   │   ├── vendor.py
│   │   ├── product.py
│   │   ├── quote.py
│   │   ├── invoice.py
│   │   ├── purchase_order.py
│   │   ├── core.py
│   │   ├── warranty.py
│   │   ├── returns.py
│   │   └── ...
│   │
│   ├── routers/            ← FastAPI route handlers (thin — delegate to services)
│   │   ├── customers.py
│   │   ├── vendors.py
│   │   ├── products.py
│   │   ├── quotes.py
│   │   ├── sales_orders.py
│   │   ├── invoices.py
│   │   ├── purchase_orders.py
│   │   ├── cores.py
│   │   ├── warranty.py
│   │   └── ...
│   │
│   ├── services/           ← Business logic (all rules live here)
│   │   ├── quote_service.py
│   │   ├── invoice_service.py
│   │   ├── po_service.py
│   │   ├── core_service.py
│   │   ├── pricing_service.py
│   │   ├── payment_service.py
│   │   └── ...
│   │
│   ├── templates/          ← Jinja2 HTML (HTMX partials + full pages)
│   │   ├── base.html       ← Layout, sidebar, design system @apply classes
│   │   ├── customers/
│   │   ├── vendors/
│   │   ├── products/
│   │   ├── quotes/
│   │   ├── invoices/
│   │   └── ...
│   │
│   └── static/             ← CSS, JS, images
│
├── data/                   ← SQLite DB lives here (gitignored)
├── requirements.txt
├── run.py
└── START JAKS.bat
```

## Key conventions

- **Routers are thin** — validate input, call a service method, return a template response.
- **Services own all business logic** — pricing, tax, status transitions, core lifecycle.
- **Design system** — use `.form-input`, `.btn-primary`, `.card`, etc. (defined in `base.html`). Never re-inline those styles.
- **HTMX partials** — files prefixed with `_` (e.g. `_row.html`) are fragment responses, not full pages.
- **No nested forms** — if a sub-form is needed, move it outside the parent `<form>` and wire with `form="id"` on the submit button.

## Planning docs (this folder)

| File | Contents |
|---|---|
| `PHASE_1_PLAN.md` | Phase 1 feature checklist and status |
| `MASTER_PLAN.md` | Full multi-phase architecture plan |
| `QUOTING_REQUIREMENTS.md` | Quote screen spec (keyboard-first, live ops) |
| `UX_NAVIGATION_REQUIREMENTS.md` | Navigation and inline-creation rules |
| `DESIGN.md` | Visual design system reference |
| `INTERVIEW_NOTES.md` | Requirements gathered from stakeholder interviews |
