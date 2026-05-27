# JAK's Diesel — Engine Rebuild Notes

This folder is the brief for rebuilding **only the backend engine**. No UI,
no screens, no mockups. Pure services, data, jobs, and APIs.

## What "engine" means here

- **Data model** — tables / collections + invariants.
- **Domain services** — pure functions over the data (pricing resolver,
  core ledger, inventory mutator, document numberer).
- **Workflows / state machines** — quote → SO → invoice; PO → receipt → bill;
  core in → vendor RGA → credit.
- **Jobs** — scheduled or event-driven (PAI scrape, competitor watchers,
  QBO queue worker, email sender).
- **Integrations** — PAI, HHP, ATL, QBO, email (SMTP/API), Shopify (optional).
- **Public API surface** — the endpoints any future UI (or AI agent) will
  call to drive the system.

## Files in this folder

| File | Purpose |
|------|---------|
| `00_principles.md` | Non-negotiables for the engine |
| `01_open_questions.md` | The decisions the user must make (multi-choice) |
| `02_products_engine.md` | Products + cross-reference + pricing logic |
| `03_cores_engine.md` | The 5-step core process |
| `04_qbo_sync_engine.md` | QBO sync queue + workers |
| `05_email_engine.md` | Outbound + inbound email |
| `06_scrapers_engine.md` | PAI + HHP + ATL scrape services |
| `07_api_surface.md` | REST/RPC endpoints the engine exposes |

Start by reading `01_open_questions.md` — the user's answers there decide
what gets implemented in the other files.
