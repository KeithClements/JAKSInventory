"""Bridge between the root-level HHP scraper pipeline and jaks_inventory.

Wraps the 5-phase scraper workflow (scan → scrape → PAI → review → upload)
and persists results into the inventory database (``db.sync_product_to_db``).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from core.constants import HHP_MAIN_CATEGORIES, MANUFACTURERS, DEFAULT_LOGO_PATH
from core.paths import get_sku_images_folder
from core.stage import get_stage, set_stage
from engine.product_engine import ProductEngine
from phases.async_scraper import (
    build_category_url,
    estimate_category_listing,
    scrape_category_async,
    scrape_single_url_async,
)
from phases.scraper import extract_primary_part_from_url
from sources.pai_builder import build_pai_client_for_run
from sources.pai_enrichment import enrich_pai
from utils.helpers import ensure_dir

from db import (
    create_scrape_run,
    update_scrape_run,
    sync_product_to_db,
    sync_products_batch,
    get_product_by_sku,
)

logger = logging.getLogger(__name__)

__all__ = [
    "hhp_scan",
    "hhp_scrape_category",
    "hhp_scrape_single",
    "hhp_sync_product",
    "hhp_sync_products_batch",
    "HHP_CATEGORIES",
    "HHP_MANUFACTURERS",
]

# Re-export constants for use from inventory UI
HHP_CATEGORIES: list[str] = list(HHP_MAIN_CATEGORIES)
HHP_MANUFACTURERS: list[str] = ["All"] + list(MANUFACTURERS)


# ---------------------------------------------------------------------------
# Phase 1 — Scan (HTTP-only, no Playwright)
# ---------------------------------------------------------------------------

def hhp_scan(
    *,
    category: str,
    manufacturer: str,
    new_only: bool = False,
    cancel_event: threading.Event | None = None,
    signals: Any,
) -> dict:
    """Run Phase 1 scan and record the run in inventory.db.

    Returns the dict produced by ``estimate_category_listing``:
        {main_category, manufacturer, start_url, new_only, scanned_at,
         count, items: [{part, url, price, title, thumbnail}]}
    """
    run_id = create_scrape_run("scan", "HHP", notes=f"{category} / {manufacturer}")
    try:
        result = estimate_category_listing(
            main_category=category,
            manufacturer=manufacturer,
            new_only=new_only,
            cancel_event=cancel_event,
            signals=signals,
        )
        count = result.get("count", 0)
        update_scrape_run(run_id, status="completed", total_searched=1, total_found=count)
        return result
    except Exception as exc:
        update_scrape_run(run_id, status="failed")
        raise


# ---------------------------------------------------------------------------
# Phase 2 — Scrape (Playwright + images + description)
# ---------------------------------------------------------------------------

def hhp_scrape_category(
    *,
    category: str,
    manufacturer: str,
    new_only: bool = False,
    limit: int | None = None,
    cancel_event: threading.Event | None = None,
    signals: Any,
) -> dict:
    """Run Phase 2 full scrape of a category and persist to inventory.db.

    Returns ``{key: Product}`` dict.
    """
    run_id = create_scrape_run("scrape", "HHP", notes=f"{category} / {manufacturer}")
    output = Path.cwd() / "output"
    images_dir = output / "images"
    temp_dir = output / "_temp_raw"
    ensure_dir(images_dir)
    ensure_dir(temp_dir)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        products = loop.run_until_complete(
            scrape_category_async(
                main_category=category,
                manufacturer_filter=manufacturer,
                new_only=new_only,
                limit=limit,
                images_dir=images_dir,
                temp_dir=temp_dir,
                signals=signals,
                cancel_event=cancel_event,
            )
        )
        loop.close()

        # Persist to inventory.db
        if products:
            hhp_sync_products_batch(products, signals=signals)

        update_scrape_run(
            run_id,
            status="completed",
            total_searched=len(products) if products else 0,
            total_found=len(products) if products else 0,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        return products or {}
    except Exception as exc:
        update_scrape_run(run_id, status="failed")
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def hhp_scrape_single(
    *,
    url: str,
    signals: Any,
) -> Any:
    """Scrape a single HHP product URL and persist to inventory.db.

    Returns the ``Product`` object (or raises on failure).
    """
    run_id = create_scrape_run("scrape", "HHP", notes=f"single: {url}")
    output = Path.cwd() / "output"
    images_dir = output / "images"
    temp_dir = output / "_temp_raw"
    ensure_dir(images_dir)
    ensure_dir(temp_dir)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        prod = loop.run_until_complete(
            scrape_single_url_async(url, images_dir, temp_dir, signals)
        )
        loop.close()

        if prod:
            hhp_sync_product(prod)
            update_scrape_run(run_id, status="completed", total_searched=1, total_found=1)
        else:
            update_scrape_run(run_id, status="completed", total_searched=1, total_found=0)

        shutil.rmtree(temp_dir, ignore_errors=True)
        return prod
    except Exception as exc:
        update_scrape_run(run_id, status="failed")
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Database persistence helpers
# ---------------------------------------------------------------------------

def hhp_sync_product(product: Any) -> int:
    """Sync a single scraped Product to inventory.db. Returns DB id."""
    return sync_product_to_db(product)


def hhp_sync_products_batch(
    products: dict,
    *,
    signals: Any | None = None,
) -> tuple[int, int]:
    """Sync a batch of products to inventory.db.

    Returns ``(synced_count, error_count)``.
    """
    def progress_cb(current: int, total: int) -> None:
        if signals:
            try:
                signals.log.emit(f"DB sync: {current}/{total}")
            except Exception:
                pass

    return sync_products_batch(products, progress_callback=progress_cb)
