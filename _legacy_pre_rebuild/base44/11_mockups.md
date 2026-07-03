# Mockups Index

All HTML mockups live in the `mockups/` folder at the repo root. They are
standalone HTML files (no build step) — open in any browser. Use them as the
visual source of truth alongside the textual specs in this folder.

| File | Demonstrates |
|------|--------------|
| `main_window_dashboard_redesign.html` | App shell, sidebar nav, dashboard layout (KPI tiles, attention chips, activity feed). Reference for `07_modules/01_dashboard.md` and `05_design_system.md`. |
| `add_product_redesign.html` | NEW-mode of the Product Workbench: section nav, required checklist meta panel, progress bar. |
| `edit_product_redesign.html` | EDIT-mode of the Product Workbench: same shell, quick-stats meta panel with margin %. |
| `product_workbench_plan.html` | Annotated layout plan for the workbench dialog. Use this to align section order and field placement. |
| `product_detail_drawer_v2.html` | Slide-in drawer for viewing a product from the Products list without opening the full dialog. Useful for quick edits in Base44. |
| `products_screen_fixes_v2.html` | Updated Products screen with attention chips, inline editing, and bulk action bar. |
| `inventory_mockup.html` | Top-level Inventory tab with sub-tab layout. |
| `inventory_products_mockup.html` | Earlier Products screen mockup; superseded by `products_screen_fixes_v2.html`. |
| `inventory_products_redesign.html` | Color/spacing redesign of the Products list. |
| `products_and_inventory_tabs_redesign.html` | The relationship between top-level Inventory and the Products tab. |
| `inventory_adjustments_mockup.html` | Adjustments screen layout + Add Adjustment dialog. |
| `inventory_locations_mockup.html` | Multi-location warehouse + per-location stock view. |
| `inventory_kits_mockup.html` | Kits screen: parent SKU + components grid + build availability. |
| `new_quote_modal_mockup.html` | New Quote dialog: header, lines, Part Finder integration, options/totals, comments/follow-up. |
| `sales_orders_mockup.html` | Sales Orders list + detail dialog with pick/pack/ship. |
| `invoices_mockup.html` | Invoices list, detail dialog, payment dialog, statements. |
| `core_processing_mockup.html` | Processing Dashboard, Customer Cores aging buckets, Vendor Cores board, RGA dialog. |

---

## How to use these in a rebuild

1. **Layout grammar** comes from `main_window_dashboard_redesign.html` —
   sidebar + header + content area with the standard list-screen grammar
   (title bar / KPI strip / attention chips / filter row / table / footer).
2. **Color palette and spacing** are in the inline CSS of every mockup;
   the canonical tokens are documented in `05_design_system.md`.
3. **Dialog/modal sizes**: take measurements directly from the mockup HTML.
4. **Mock data**: the mockups contain plausible sample data (SKUs, customer
   names, prices) — use these as fixtures when seeding a Base44 dev DB.
5. **Cross-references**: every module spec under `07_modules/` and process
   spec under `04_core_processes.md` references the relevant mockup file
   by name. Open them side-by-side while building.

---

## Mockups still to create (optional for Base44 work)

These screens don't have a current HTML mockup and would benefit from one:
- Vendor Returns combined screen (the RGA list/board pair).
- Tiered Pricing grid editor.
- Customer Hub / Customer 360 dialog.
- Settings tabs (QBO mapping is the most complex).
- Part Finder dialog with all sources surfaced.
- HHP Scraper phase-progress view.
- Sync Center.

The textual specs are authoritative when no mockup exists.
