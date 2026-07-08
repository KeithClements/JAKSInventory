"""Scraper service package — wraps external scrapers for inventory app use."""

from .scraper_worker import ScraperWorker
from .scraper_service import (
    scrape_xrefs_for_product,
    scrape_competitor_prices_for_product,
    scrape_pai_images_for_product,
)
from .hhp_bridge import (
    hhp_scan,
    hhp_scrape_category,
    hhp_scrape_single,
    hhp_sync_product,
    hhp_sync_products_batch,
    HHP_CATEGORIES,
    HHP_MANUFACTURERS,
)
from .hhp_workers import (
    HHPScanWorker,
    HHPScrapeWorker,
    HHPSingleScrapeWorker,
)

__all__ = [
    "ScraperWorker",
    "scrape_xrefs_for_product",
    "scrape_competitor_prices_for_product",
    "scrape_pai_images_for_product",
    # HHP pipeline
    "hhp_scan",
    "hhp_scrape_category",
    "hhp_scrape_single",
    "hhp_sync_product",
    "hhp_sync_products_batch",
    "HHP_CATEGORIES",
    "HHP_MANUFACTURERS",
    "HHPScanWorker",
    "HHPScrapeWorker",
    "HHPSingleScrapeWorker",
]
