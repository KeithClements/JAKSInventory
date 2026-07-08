# Module: Tools

Collection of single-purpose utilities. Each is its own screen accessible from
the sidebar's Tools section.

---

## Part Finder

**Existing code:** `jaks_inventory/ui/part_finder_screen.py`,
`part_finder_dialog.py`, services: `part_search_service.py`, `match_engine.py`

The fastest way to find a part across every source.

### Search box
One input. As you type, parallel queries against:
- Local `products` (SKU + title + OEM + vendor SKU)
- `product_interchanges` / xref table
- Vendor cross-references
- ESN history (engine serial → parts previously sold)
- PAI live (debounced; only after 800 ms idle)
- HHP live (debounced)

### Results pane
Columns: source badge (LOCAL / PAI / HHP / ATL), SKU, title, price, qty (local
only), warranty, image thumbnail.

Selecting a row enables: **Add to current Quote/SO/PO**, **Open product**,
**Create from this** (drafts a new product pre-filled from the source).

---

## Barcodes

**Existing code:** `jaks_inventory/ui/barcode_screen.py`

- Pick products → choose label format (Avery 5160, 4×6 thermal, etc.) → preview → print.
- Generates Code 128 by default. QR optional.
- Sequential serial-number labels for receiving lots.

---

## Import

**Existing code:** `jaks_inventory/ui/import_screen.py`

General-purpose CSV import wizard for any major entity:
- Products (full schema)
- Customers
- Vendors
- Pricing tiers
- Open POs / SOs (historical migration)

Steps mirror Bulk Import in Inventory: upload → map → validate → preview → commit.

---

## HHP Scraper

**Existing code:** `jaks_inventory/ui/hhp_scraper_screen.py`,
backend `jaks_inventory/scraper/hhp_bridge.py`, phases under `phases/`

UI front-end for the 5-phase HHP catalog ingest. See
`08_integrations/02_hhp_scraper.md` for backend detail.

### Layout
- Phase strip across top showing current phase (Scan / Scrape / PAI Enrich /
  Review / Upload) with progress per phase.
- Live log panel.
- Review phase: human-in-the-loop table where new products are accepted /
  edited / rejected before upload.

### Controls
`[ Start Scan ] [ Pause ] [ Resume ] [ Cancel ] [ View History ]`

---

## Scraper Admin

**Existing code:** `jaks_inventory/ui/scraper_screen.py`

Across-source monitor.

### KPI strip
- PAI runs today
- HHP runs today
- ATL runs today
- Total scraped items (24h)
- Failures (24h)

### Run history table
Columns: started_at, source, type (single/category/full), items_found,
status, duration, error.

### Actions
- **Run PAI search** (one-off)
- **Run HHP scan**
- **Run ATL scan**
- **Clear PAI session** (when login is stale)
- **Open scraper logs** (folder)

---

## ESN Lookup

**Existing code:** `jaks_inventory/ui/esn_lookup_screen.py`,
`esn_lookup_dialog.py`

Quick lookup: paste an engine serial number → see manufacturer, model, and
every quote/SO/invoice line that referenced it (across all customers).

Also used embedded inside Quote / SO dialogs.

### Filters
`[Manufacturer ▾] [Model ▾] [Customer ▾] [Date range ▾]`

---

## Other tools (often present)

| Tool | Purpose |
|------|---------|
| **Test Center** | Run smoke tests against this install (DB, QBO, SMS, scrapers). |
| **Diagnostics** | Show DB path, version, migration count, env. |
| **Backup** | One-click DB backup → zipped file to disk. |
| **Restore** | Restore from a backup zip (admin only, requires re-auth). |
