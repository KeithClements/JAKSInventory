# 01 — Glossary

Domain terms used throughout this spec. If your reader doesn't know diesel
parts, **read this first**.

| Term | Meaning |
|------|---------|
| **SKU** | Internal stock keeping unit. Primary key for products. Format: free-form text, often `JAK-12345`. |
| **OEM part number** | Original equipment manufacturer's number (Cummins, Detroit, Caterpillar, etc.). Used as a crossref to look up the matching JAK SKU. |
| **PAI** | A major aftermarket parts distributor (portal.pai.com). Primary supplier for many SKUs. The app has a Playwright scraper that pulls cost, list price, OEM number, stock by warehouse, and images. |
| **HHP** | "Highway and Heavy Parts" — competitor reseller. Used for competitive price intelligence. Scraped via WooCommerce AJAX endpoint. |
| **ATL** | "ATL Diesel" — another competitor reseller (Shopify storefront). Scraped for prices + related products + frequently-bought-together. |
| **REMAN** | Remanufactured. A core-bearing condition. Carries a core charge. |
| **NEW** | Brand-new part, no core charge. |
| **USED** | As-is part, no warranty, no core charge. |
| **CORE** | An empty/spent unit awaiting credit. Either *customer core* (we owe customer credit) or *vendor core* (we owe vendor a return). |
| **KIT** | A pseudo-SKU that explodes into multiple line items on sale. |
| **Core charge** | Refundable deposit added to a REMAN part. Tracked per unit, often serialized. Refunded when the customer returns the old part within the return window (typically 30–90 days). |
| **Conditional Exchange** | A core that may or may not be refundable based on vendor inspection. See `db/core_handling.py`. |
| **Vendor core** | Our obligation to return an accumulated customer-returned core to the vendor for credit. Tracked in `vendor_core_obligations`. |
| **RGA / RMA** | Return Goods Authorization / Return Merchandise Authorization. Vendor RGA = us returning to vendor. Customer RMA = customer returning to us. |
| **ESN** | Engine Serial Number. Customers often look up parts by ESN. |
| **Reorder point** | Min qty triggering a low-stock alert. |
| **Min Qty / Max Qty** | Optional safety stock floor and target ceiling. |
| **On Order** | Sum of open PO line quantities for a SKU. |
| **Reserved** | Sum of open SO line quantities. |
| **Available** | `qty_on_hand - reserved`. |
| **Customer tier** | A pricing class (`Retail`, `Dealer`, `Fleet`, `Wholesale`). Drives the discount grid. |
| **Price category** | A product classification (`Filters`, `Turbos`, `Engine Parts`, etc.) used as one axis of the tier discount grid. |
| **Cost band** | A cost range (e.g. `$0–50`, `$50–250`). The other axis of the tier discount grid. |
| **MAP price** | Minimum Advertised Price set by manufacturer. App warns if a quote line goes below it. |
| **Landed cost** | Unit cost + allocated freight + duty. Used for accurate margin reporting. |
| **Pick ticket** | Warehouse document listing what to pull for an SO. |
| **Packing slip** | Customer-facing copy of what shipped. No prices shown. |
| **QBO** | QuickBooks Online. The accounting system this app syncs to. |
| **Fitment** | The relationship "this SKU fits these engines / trucks / years". |
| **Supersession** | OEM has replaced part A with part B. App stores the chain so a search for either resolves to the current replacement. |
| **Vendor cutoff** | Daily time after which orders ship next-day. Drives PO send timing. |
| **Restocking fee** | Percentage withheld on a customer return for inventory restock cost. Usually 15–25 %. |
| **Stage** | A product lifecycle marker (`scraped`, `enriched`, `reviewed`, `published`, `archived`). |
