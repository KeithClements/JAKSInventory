# 06 — Navigation

## Sidebar tree (top to bottom)

Match the existing app's `main_window.py` sidebar. Sections are headers (not
clickable); items below them are screens.

```
DASHBOARD
  Dashboard

SALES
  Quotes
  Lost Sales
  Sales Orders
  Invoices
  Deliveries
  CRM
  Returns

CORE PROCESSING
  Processing Dashboard
  Customer Cores
  Vendor Returns

INVENTORY
  Products              ← canonical hub
  Bulk Import
  Adjustments
  Locations
  Kits
  Audit

PURCHASING
  Purchase Orders
  PO Receipts
  Vendors
  Low Stock & Reorder

PRICING
  Price Lists
  Pricing Maintenance
  Tiered Pricing

CUSTOMERS
  Customers Hub

MARKETING
  Text Messaging
  SMS Campaigns
  Automation

ACCOUNTING
  Margins
  QBO Sync Center
  QBO Reconciliation
  Aging AR
  Reports

TOOLS
  Part Finder
  Barcodes
  Import
  HHP Scraper
  Scraper Admin
  ESN Lookup

SETTINGS
  Settings (8 sub-tabs: Company, Users, Tax, Shipping, Documents, QBO, Shopify, SMS)
```

## Header (top bar)

```
[ ☰ ]   JAK's Diesel PRO   [ global part search ]      [ QBO ● ] [ User ▼ ] [ ⓘ ]
```

- `☰` toggles sidebar (collapsed shows icons only).
- Global part search: queries SKU, OEM, vendor SKU, ESN. Live, top-5 results
  with click-to-open.
- `QBO ●` status pill: green=connected/synced, amber=pending>0, red=failures.
  Click → Sync Center.
- User menu: profile, logout.

## Keyboard quick-nav

`Ctrl + K` opens a command palette listing every screen, all primary actions
(`New Quote`, `New Product`, `New PO`), and the last 10 entities viewed.

## Breadcrumb / context strip

Inside detail screens (e.g. customer detail, PO detail) show:

```
Customers Hub  /  Acme Trucking  /  Invoices  /  INV-2026-00831
```

Clickable segments. Last segment is plain text.
