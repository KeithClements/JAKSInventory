# 00 — Overview

## What it is

**JAK's Diesel PRO** is a single-tenant ERP + inventory system for a heavy-duty
diesel parts dealer. It sits between three external systems:

```
     ┌──────────────┐        ┌──────────────────┐        ┌──────────────┐
     │  Suppliers   │        │   JAK's Diesel   │        │   Customers  │
     │  (PAI, HHP,  │ ─────► │       PRO        │ ─────► │  (fleets,    │
     │   ATL, ...)  │        │   (this app)     │        │   shops,     │
     └──────────────┘        └──────────────────┘        │   walk-ins)  │
                                     │                   └──────────────┘
                                     ▼
                              ┌──────────────┐
                              │  QuickBooks  │
                              │     Online   │
                              └──────────────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │   Shopify    │
                              │  (web store) │
                              └──────────────┘
```

## Users

- **Counter sales** — takes phone/walk-in orders, builds quotes, converts to
  invoices, takes payment.
- **Inside sales / outside sales** — works open quotes, manages customer
  relationships, hits weekly campaigns.
- **Purchasing** — reviews low-stock, builds POs, receives shipments.
- **Warehouse** — picks/packs/ships SOs, receives POs, runs cycle counts.
- **Owner / admin** — pricing, vendor terms, QBO mappings, reports.

A typical install has 3–10 concurrent users.

## What problem it solves

Diesel parts retail has these awkward facts:

1. **Cores.** Many remanufactured parts (turbos, injectors, water pumps) carry
   a refundable deposit ("core charge"). The customer pays it on the invoice
   and gets it back when they return the old unit. Cores must be tracked
   per-unit, often serialized, with aging.
2. **Multiple supplier sources for one part.** A single SKU might exist at
   PAI (primary), HHP (competitor pricing reference), ATL (competitor), and
   a private-label vendor — at different prices, MOQs, lead times.
3. **Crossreferences are everything.** Customers call with OEM part numbers,
   ESNs, engine model, or "what the truck guy said". The app must resolve
   any of these to a SKU in <2 seconds.
4. **Tiered pricing.** Walk-in retail pays list. Dealers, fleets, wholesale
   accounts each get their own discount grid by part category.
5. **QBO is mandatory but slow.** Accounting needs QBO synced; the app must
   keep working when QBO is offline.

## Why this app is different from generic inventory tools

| Feature | Generic inventory (e.g. Sortly) | JAK's Diesel PRO |
|---------|--------------------------------|-------------------|
| Core charges | ❌ | ✅ tracked per unit + aging |
| Multi-supplier matrix | Vendor field | Full cost-comparison panel per SKU |
| Competitor pricing scrape | ❌ | PAI + HHP + ATL automated |
| Tier-pricing grid | Flat discount | Category × Tier × Band matrix |
| OEM crossrefs | Manual | Auto-resolved from any number |
| Part lookup speed | Form submit | Live, ≤300 ms |
| QBO Items + Invoices sync | Optional | Required, bidirectional |
| Shopify product publish | ❌ / paid add-on | Built-in, per-SKU |

## High-level capability list

The app provides, at minimum:

- Product catalog with multi-source costs and competitor prices
- Tiered pricing (categories × customer tiers × bands × discount %)
- Quotes → Sales Orders → Invoices → Payments
- Purchase Orders → Receipts → Vendor Bills
- Core charges + customer core returns + vendor RGA flow
- Returns / RMAs with restocking fee logic
- Cycle counts + manual adjustments + audit trail
- Customer hub: AR aging, ESN history, credit limits, employees
- Vendor hub: terms, cutoff times, price categories
- Tools: Part Finder, Barcodes, Bulk Import, Scrapers
- Integrations: PAI scrape, HHP scrape, ATL scrape, Shopify sync, QBO sync, SMS

Each capability is fleshed out in its own file under [07_modules/](07_modules/).
