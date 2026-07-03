"""Async scraping functions for HHP product pages."""

from __future__ import annotations

import asyncio
import datetime
import re
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from playwright.async_api import async_playwright

from core.constants import REQUEST_TIMEOUT, DEFAULT_LOGO_PATH
from core.paths import get_sku_images_folder
from core.stage import get_stage, set_stage
from engine.product_engine import ProductEngine
from phases.scraper import scrape_product, normalize_product_url, extract_primary_part_from_url
from sources.pai_builder import build_pai_client_for_run
from sources.pai_enrichment import enrich_pai, apply_pai_supplier_gate
from utils.helpers import watermark_folder
from utils.part_normalizer import normalize_part_number

if TYPE_CHECKING:
    from workers.signals import Signals

__all__ = [
    "scrape_single_url_async",
    "scrape_category_async",
    "collect_first_page_links",
    "estimate_category_listing",
    "build_category_url",
    "check_url_against_existing_xrefs",
]


def check_url_against_existing_xrefs(url: str, db_path: str | None = None) -> dict | None:
    """Check if URL's part numbers match any existing product's cross-references.
    
    Args:
        url: Product URL to check
        db_path: Optional path to SQLite database (defaults to output/inventory.db)
        
    Returns:
        dict with match info if found: {product_id, sku, title, matched_part}
        None if no match found
    """
    import sqlite3
    from pathlib import Path
    
    primary = extract_primary_part_from_url(url) or ""
    if not primary or len(primary) < 4:
        return None
    
    # Normalize for matching
    primary_norm = normalize_part_number(primary)
    if not primary_norm:
        return None
    
    # Default DB path
    if not db_path:
        db_path = Path("output") / "inventory.db"
    
    if not Path(db_path).exists():
        return None
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Check part_numbers table for match
        cursor.execute("""
            SELECT DISTINCT p.id, p.sku, p.title, pn.number, pn.type
            FROM products p
            JOIN part_numbers pn ON p.id = pn.product_id
            WHERE pn.number_normalized = ?
            LIMIT 1
        """, (primary_norm,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "product_id": row[0],
                "sku": row[1],
                "title": row[2],
                "matched_part": row[3],
                "match_type": row[4],
            }
        
        return None
        
    except Exception:
        return None


def build_category_url(main_category: str, manufacturer: str) -> tuple[str, str]:
    """Build HHP category URL from category and manufacturer names."""
    main_cat_slug = "all-" + main_category.lower().replace(" & ", "-").replace(" ", "-")
    if manufacturer == "All":
        start_url = f"https://highwayandheavyparts.com/product-category/{main_cat_slug[4:]}/"
    else:
        if manufacturer == "International":
            man_slug = "international-navistar-heavy-duty-diesel-engine-parts"
        else:
            man_slug = manufacturer.lower().replace(" ", "-") + "-heavy-duty-diesel-engine-parts"
        start_url = f"https://highwayandheavyparts.com/shop/filter/category/{man_slug}/part/{main_cat_slug}/"
    return start_url, main_cat_slug


def estimate_category_listing(
    *,
    main_category: str,
    manufacturer: str,
    new_only: bool,
    cancel_event: threading.Event | None = None,
    signals: "Signals",
) -> dict:
    """Scan category pages to estimate product count and gather listing data."""
    import requests
    from bs4 import BeautifulSoup

    start_url, _category_slug = build_category_url(main_category, manufacturer)
    scanned_at = datetime.datetime.now().isoformat(timespec="seconds")
    signals.log.emit(f"Scanning from: {start_url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    items_by_part: dict[str, dict] = {}
    seen_urls: set[str] = set()

    url: str | None = start_url
    page_num = 1
    while url:
        if cancel_event is not None and cancel_event.is_set():
            signals.log.emit("Estimate cancelled")
            items = list(items_by_part.values())
            return {
                "main_category": main_category,
                "manufacturer": manufacturer,
                "start_url": start_url,
                "new_only": bool(new_only),
                "count": len(items),
                "items": items,
                "stopped": True,
            }
        signals.log.emit(f"Fetching page {page_num}: {url}")
        resp = requests.get(url, headers=headers, timeout=30)
        signals.log.emit(f"Response status: {resp.status_code}")
        if resp.status_code != 200:
            raise RuntimeError(f"Bad response: {resp.status_code}")

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try to keep link+price associated by iterating product tiles when available.
        product_nodes = soup.select("li.product") or []
        if not product_nodes:
            # Fallback: best-effort parse via links only.
            product_nodes = soup.select("a[href*='/product/']")

        page_added = 0
        for node in product_nodes:
            href = ""
            price_val: float | None = None

            try:
                if getattr(node, "name", "") == "a":
                    href = (node.get("href") or "").strip()
                else:
                    a = node.select_one("a[href*='/product/']")
                    href = (a.get("href") or "").strip() if a else ""
            except Exception:
                href = ""

            if not href or "/product/" not in href:
                continue

            href = normalize_product_url(href)
            if not href:
                continue

            if new_only and ("reman" in href.lower() or "remanufactured" in href.lower()):
                continue

            if href in seen_urls:
                continue
            seen_urls.add(href)

            part = str(extract_primary_part_from_url(href) or "").strip()
            if not part:
                continue

            # Best-effort listing price.
            price_val = None
            try:
                if getattr(node, "name", "") != "a":
                    price_el = (
                        node.select_one(".price ins .amount")
                        or node.select_one(".price .amount")
                        or node.select_one(".woocommerce-Price-amount")
                    )
                else:
                    price_el = None
                price_txt = str(price_el.get_text(" ", strip=True) if price_el else "").strip()
                if price_txt:
                    m = re.search(r"(\d[\d,]*\.?\d{0,2})", price_txt)
                    if m:
                        price_val = float(m.group(1).replace(",", ""))
            except Exception:
                price_val = None

            # Best-effort title extraction from listing tile.
            title_val = ""
            try:
                if getattr(node, "name", "") != "a":
                    title_el = (
                        node.select_one(".woocommerce-loop-product__title")
                        or node.select_one("h2")
                        or node.select_one(".product-title")
                        or node.select_one("a[href*='/product/']")
                    )
                    if title_el:
                        title_val = str(title_el.get_text(" ", strip=True) or "").strip()
            except Exception:
                title_val = ""

            # Best-effort thumbnail URL.
            thumbnail_val = ""
            try:
                if getattr(node, "name", "") != "a":
                    img_el = node.select_one("img")
                    if img_el:
                        thumbnail_val = (
                            str(img_el.get("data-src") or img_el.get("src") or "").strip()
                        )
            except Exception:
                thumbnail_val = ""

            part_key = part.upper()
            items_by_part[part_key] = {
                "part": part,
                "url": href,
                "price": price_val,
                "title": title_val,
                "thumbnail": thumbnail_val,
            }
            page_added += 1

        signals.log.emit(
            f"Found {page_added} product(s) on page {page_num} (unique parts: {len(items_by_part)})"
        )
        
        # Emit stats for progress bar update during scan
        try:
            signals.stats.emit({
                "phase": "Phase 1: Scan",
                "attempted": len(items_by_part),
                "added": len(items_by_part),
                "skipped": 0,
                "failed": 0,
                "total": None,  # Unknown total during pagination
                "current_url": url,
            })
        except Exception:
            pass

        next_link = soup.select_one(".woocommerce-pagination a.next")
        if next_link and next_link.get("href"):
            url = next_link.get("href")
            page_num += 1
        else:
            url = None

    items = list(items_by_part.values())
    count = len(items)

    return {
        "main_category": main_category,
        "manufacturer": manufacturer,
        "start_url": start_url,
        "new_only": bool(new_only),
        "scanned_at": scanned_at,
        "count": count,
        "items": items,
    }


async def scrape_single_url_async(
    url: str,
    images_dir: Path,
    temp_dir: Path,
    signals: "Signals",
    *,
    site: str = "hhp",
    pai_username: str | None = None,
    pai_password: str | None = None,
):
    """Scrape a single product URL asynchronously. Always downloads images."""
    from ui.product_entry import normalize_product_for_ui

    pai_client = build_pai_client_for_run(signals)

    site = str(site or "hhp").strip().lower()
    async with async_playwright() as p:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            browser = None
            try:
                signals.log.emit(f"Scraping: {url}")
                signals.stats.emit(
                    {
                        "attempted": 0,
                        "added": 0,
                        "skipped": 0,
                        "failed": 0,
                        "total": 1,
                        "current_url": url,
                        "phase": "Scraping product",
                    }
                )

                gen_desc = bool(getattr(signals, "_generate_description", True))

                if site == "pai":
                    result = await scrape_product(
                        None,
                        session,
                        url,
                        temp_dir,
                        "",
                        site="pai",
                        username=pai_username,
                        password=pai_password,
                        playwright=p,
                        download_images=True,  # Always download images
                        generate_description=bool(gen_desc),
                    )
                else:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()
                    result = await scrape_product(
                        page,
                        session,
                        url,
                        temp_dir,
                        "",
                        download_images=True,  # Always download images
                        generate_description=bool(gen_desc),
                    )
                    await page.close()

                if result.warnings:
                    signals.log.emit("Warnings: " + "; ".join(result.warnings))
                if result.errors:
                    signals.log.emit("Errors: " + "; ".join(result.errors))

                prod = result.product
                if not prod:
                    raise RuntimeError("; ".join(result.errors) if result.errors else "No product returned")

                if pai_client is not None:
                    try:
                        st_pai = int(get_stage(prod) or 1)
                    except Exception:
                        st_pai = 1

                    if st_pai != 3:
                        try:
                            signals.log.emit(f"Skipped PAI enrichment (Stage {st_pai}): requires Stage 3")
                        except Exception:
                            pass
                    else:
                        try:
                            before = float(getattr(prod, "cost", 0) or 0)
                        except Exception:
                            before = 0.0
                        try:
                            await asyncio.to_thread(enrich_pai, prod, pai_client)
                        except Exception:
                            pass
                        try:
                            apply_pai_supplier_gate(prod)
                        except Exception:
                            pass
                        try:
                            after = float(getattr(prod, "cost", 0) or 0)
                        except Exception:
                            after = before
                        if after > 0 and (before <= 0):
                            try:
                                match = str(getattr(prod, "pai_sku", "") or "").strip()
                                signals.log.emit(
                                    f"PAI cost applied: {str(getattr(prod, 'sku', '') or '').strip()} -> ${after:.2f}"
                                    + (f" (match: {match})" if match else "")
                                )
                            except Exception:
                                pass

                # Always watermark images (validate_only removed)
                signals.stats.emit(
                    {
                        "attempted": 1,
                        "added": 1,
                        "skipped": 0,
                        "failed": 0,
                        "total": 1,
                        "current_url": url,
                        "phase": "Watermarking",
                    }
                )
                # Use new organized folder structure: output/<Category>/<Manufacturer>/<SKU>/images/
                prod_cat = str(getattr(prod, "category", "") or "").strip() or "uncategorized"
                prod_man = str(getattr(prod, "manufacturer", "") or "").strip() or "unknown"
                prod_sku = str(getattr(prod, "sku", "") or "").strip() or str(getattr(prod, "handle", "") or "")
                out_dir = get_sku_images_folder(prod_cat, prod_man, prod_sku)
                out_dir.mkdir(parents=True, exist_ok=True)
                raw_dir = temp_dir / prod.handle
                count = watermark_folder(raw_dir, out_dir, DEFAULT_LOGO_PATH) if raw_dir.exists() else 0
                try:
                    setattr(prod, "image_count_watermarked", int(count))
                except Exception:
                    pass
                signals.log.emit(f"Watermarked {count} image(s) -> {out_dir}")

                try:
                    engine = getattr(signals, "_engine", None) or ProductEngine()
                    flags = engine.validate_product(prod).flags
                except Exception:
                    flags = []
                if flags:
                    display = str(getattr(prod, "sku", "") or "").strip() or str(getattr(prod, "handle", "") or "").strip() or url
                    signals.log.emit(f"ERROR (validate): {display} — " + ", ".join(flags))
                signals.stats.emit(
                    {
                        "attempted": 1,
                        "added": 1,
                        "skipped": 0,
                        "failed": 0,
                        "total": 1,
                        "current_url": url,
                        "phase": "Watermarking",
                    }
                )
                signals.done.emit(f"Scraped {prod.sku} ({prod.handle})")
                return prod
            finally:
                if browser is not None:
                    await browser.close()


async def collect_first_page_links(browser, start_url: str, signals: "Signals") -> list[str]:
    """Collect product links from the first page of a category."""
    page = await browser.new_page()
    try:
        signals.log.emit(f"Loading category page: {start_url}")
        await page.goto(start_url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3000)

        page_links = await page.eval_on_selector_all(
            "a",
            "els => els.map(e => e.href).filter(href => href && href.includes('/product/'))",
        )

        # Dedupe while preserving order
        links = list(dict.fromkeys(page_links))
        signals.log.emit(f"Found {len(links)} product link(s) on first page")
        return links
    finally:
        await page.close()


async def scrape_category_async(
    main_category: str,
    manufacturer_filter: str,
    new_only: bool,
    limit: int | None,
    images_dir: Path,
    temp_dir: Path,
    signals: "Signals",
    cancel_event: threading.Event | None = None,
) -> dict:
    """Scrape all products from a category asynchronously. Always downloads images."""
    from ui.product_entry import normalize_product_for_ui

    attempted = 0
    added = 0
    skipped = 0
    failed = 0
    products: dict[str, object] = {}

    pai_client = build_pai_client_for_run(signals)

    # Safe parallelism: scrape up to 3 pages concurrently.
    # Image downloads remain sequential via a shared semaphore passed to scraper.scrape_product.
    page_semaphore = asyncio.Semaphore(3)
    image_download_semaphore = asyncio.Semaphore(1)
    counters_lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            try:
                start_url, category_slug = build_category_url(main_category, manufacturer_filter)
                links = await collect_first_page_links(browser, start_url, signals)

                scan_mode = str(getattr(signals, "_scan_mode", "") or "").strip()
                existing_urls = getattr(signals, "_existing_urls_for_diff", None)
                if scan_mode == "New Only (diff)" and isinstance(existing_urls, (set, frozenset)):
                    try:
                        before_n = len(links)
                        links = [u for u in links if normalize_product_url(u) not in set(existing_urls)]
                        signals.log.emit(f"Diff mode: {len(links)} new of {before_n} link(s)")
                    except Exception:
                        pass

                if limit is not None:
                    links = links[:limit]
                signals.log.emit(f"Scraping {len(links)} product(s)...")

                baseline_skus = getattr(signals, "_baseline_skus", set())
                baseline_urls = getattr(signals, "_baseline_urls", set())
                if not isinstance(baseline_skus, (set, frozenset)):
                    baseline_skus = set()
                if not isinstance(baseline_urls, (set, frozenset)):
                    baseline_urls = set()

                # Initial stats so UI can show 0/N progress immediately (even when Products = 0)
                signals.stats.emit(
                    {
                        "attempted": 0,
                        "added": 0,
                        "skipped": 0,
                        "failed": 0,
                        "total": len(links),
                        "current_url": "",
                    }
                )

                async def _process_one(idx: int, url: str) -> None:
                    nonlocal attempted, added, skipped, failed

                    if cancel_event is not None and cancel_event.is_set():
                        return

                    async with counters_lock:
                        attempted += 1
                        signals.log.emit(f"Scraping {idx}/{len(links)}: {url}")
                        signals.stats.emit(
                            {
                                "attempted": attempted,
                                "added": added,
                                "skipped": skipped,
                                "failed": failed,
                                "total": len(links),
                                "current_url": url,
                                "phase": "Scraping product",
                            }
                        )

                    did_skip = False
                    did_fail = False
                    raw_dir = None
                    prod = None

                    # Phase 1: Playwright work (concurrency-limited)
                    result = None
                    page = None
                    try:
                        async with page_semaphore:
                            if cancel_event is not None and cancel_event.is_set():
                                return
                            page = await browser.new_page()
                            try:
                                result = await scrape_product(
                                    page,
                                    session,
                                    url,
                                    temp_dir,
                                    category_slug,
                                    download_images=True,  # Always download images
                                    generate_description=bool(getattr(signals, "_generate_description", True)),
                                    image_download_semaphore=image_download_semaphore,
                                )
                            finally:
                                try:
                                    await page.close()
                                except Exception:
                                    pass
                                page = None

                        if result is None:
                            raise RuntimeError("Scrape failed")

                        if result.warnings:
                            signals.log.emit("   Warnings: " + "; ".join(result.warnings))
                        if result.errors:
                            signals.log.emit("   Errors: " + "; ".join(result.errors))

                        prod = result.product
                        if not prod:
                            raise RuntimeError("; ".join(result.errors) if result.errors else "Scrape failed")

                        # Normalize product for UI
                        try:
                            engine = getattr(signals, "_engine", None) or ProductEngine()
                            prod, _info = normalize_product_for_ui(engine, prod)
                        except Exception:
                            pass

                        # Phase 2: PAI enrichment (NOT part of Playwright concurrency)
                        if pai_client is not None:
                            try:
                                st_pai = int(get_stage(prod) or 1)
                            except Exception:
                                st_pai = 1

                            if st_pai != 3:
                                try:
                                    signals.log.emit(
                                        f"   Skipped PAI enrichment (Stage {st_pai}): requires Stage 3"
                                    )
                                except Exception:
                                    pass
                            else:
                                try:
                                    before = float(getattr(prod, "cost", 0) or 0)
                                except Exception:
                                    before = 0.0
                                try:
                                    await asyncio.to_thread(enrich_pai, prod, pai_client)
                                except Exception:
                                    pass
                                try:
                                    after = float(getattr(prod, "cost", 0) or 0)
                                except Exception:
                                    after = before
                                if after > 0 and (before <= 0):
                                    try:
                                        match = str(getattr(prod, "pai_sku", "") or "").strip()
                                        signals.log.emit(
                                            f"PAI cost applied: {str(getattr(prod, 'sku', '') or '').strip()} -> ${after:.2f}"
                                            + (f" (match: {match})" if match else "")
                                        )
                                    except Exception:
                                        pass

                                # Apply supplier gating AFTER all PAI signals are present
                                try:
                                    apply_pai_supplier_gate(prod)
                                except Exception:
                                    pass

                                # PAI options are sourced from Product.pai_alternates

                        raw_dir = temp_dir / prod.handle

                        # Manufacturer filter - use lenient matching for combined brands
                        if manufacturer_filter != "All":
                            prod_man = str(getattr(prod, "manufacturer", "") or "").strip().lower()
                            filter_man = str(manufacturer_filter or "").strip().lower()
                            
                            # Lenient manufacturer matching
                            man_match = False
                            
                            # Direct match or substring match
                            if filter_man == prod_man:
                                man_match = True
                            elif filter_man in prod_man:
                                man_match = True
                            elif prod_man in filter_man:
                                man_match = True
                            # Volvo/Mack are often combined in HHP listings
                            elif filter_man == "volvo" and any(x in prod_man for x in ["volvo", "mack", "d13", "mp8"]):
                                man_match = True
                            elif filter_man == "mack" and any(x in prod_man for x in ["mack", "volvo", "mp7", "mp8"]):
                                man_match = True
                            # Cummins variants
                            elif filter_man == "cummins" and any(x in prod_man for x in ["cummins", "isx", "isb", "isc", "isf", "n14", "m11"]):
                                man_match = True
                            # Caterpillar variants  
                            elif filter_man == "caterpillar" and any(x in prod_man for x in ["caterpillar", "cat", "c15", "c13", "c12", "3406"]):
                                man_match = True
                            # Detroit Diesel variants
                            elif filter_man == "detroit diesel" and any(x in prod_man for x in ["detroit", "dd13", "dd15", "dd16", "series 60"]):
                                man_match = True
                            # International/Navistar variants
                            elif filter_man in ["international", "navistar"] and any(x in prod_man for x in ["international", "navistar", "maxxforce", "dt466"]):
                                man_match = True
                            # Empty manufacturer from scrape should pass (will be categorized later)
                            elif not prod_man:
                                man_match = True
                                signals.log.emit(f"   Warning: Empty manufacturer for {prod.sku}, allowing through filter")
                            
                            if not man_match:
                                async with counters_lock:
                                    skipped += 1
                                did_skip = True
                                signals.log.emit(f"   Skipped (manufacturer filter '{manufacturer_filter}' != '{prod.manufacturer}'): {prod.sku}")
                                try:
                                    signals.row_ready.emit(
                                        {
                                            "kind": "skipped",
                                            "url": url,
                                            "sku": str(getattr(prod, "sku", "") or "").strip(),
                                            "title": str(getattr(prod, "title", "") or ""),
                                            "manufacturer": str(getattr(prod, "manufacturer", "") or ""),
                                            "handle": str(getattr(prod, "handle", "") or ""),
                                            "reason": f"manufacturer filter: {manufacturer_filter} vs {prod.manufacturer}",
                                        }
                                    )
                                except Exception:
                                    pass
                                return

                        if new_only and (
                            "remanufactured" in prod.title.lower() or "reman" in prod.title.lower()
                        ):
                            async with counters_lock:
                                skipped += 1
                            did_skip = True
                            signals.log.emit(f"   Skipped (reman item): {prod.sku}")
                            try:
                                signals.row_ready.emit(
                                    {
                                        "kind": "skipped",
                                        "url": url,
                                        "sku": str(getattr(prod, "sku", "") or "").strip(),
                                        "title": str(getattr(prod, "title", "") or ""),
                                        "manufacturer": str(getattr(prod, "manufacturer", "") or ""),
                                        "handle": str(getattr(prod, "handle", "") or ""),
                                        "reason": "reman item",
                                    }
                                )
                            except Exception:
                                pass
                            return

                        # Always watermark images (validate_only removed)
                        signals.stats.emit(
                            {
                                "attempted": attempted,
                                "added": added,
                                "skipped": skipped,
                                "failed": failed,
                                "total": len(links),
                                "current_url": url,
                                "phase": "Watermarking",
                            }
                        )
                        # Use new organized folder structure: output/<Category>/<Manufacturer>/<SKU>/images/
                        prod_cat = str(getattr(prod, "category", "") or "").strip() or main_category or "uncategorized"
                        prod_man = str(getattr(prod, "manufacturer", "") or "").strip() or manufacturer_filter or "unknown"
                        prod_sku = str(getattr(prod, "sku", "") or "").strip() or str(getattr(prod, "handle", "") or "")
                        out_dir = get_sku_images_folder(prod_cat, prod_man, prod_sku)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        count = watermark_folder(raw_dir, out_dir, DEFAULT_LOGO_PATH) if raw_dir.exists() else 0
                        try:
                            setattr(prod, "image_count_watermarked", int(count))
                        except Exception:
                            pass
                        signals.log.emit(
                            f"   Success: {prod.sku} | {prod.manufacturer} | images: {count}"
                        )

                        try:
                            engine = getattr(signals, "_engine", None) or ProductEngine()
                            v = engine.validate_product(prod)
                            flags = list(v.flags or [])
                            try:
                                setattr(prod, "flags", flags)
                            except Exception:
                                pass
                        except Exception:
                            v = None
                            flags = []

                        if flags:
                            display = (
                                str(getattr(prod, "sku", "") or "").strip()
                                or str(getattr(prod, "handle", "") or "").strip()
                                or url
                            )
                            status = str(getattr(v, "status", "") or "").strip() if v is not None else ""
                            prefix = "VALIDATION" + (f"({status})" if status else "")
                            signals.log.emit(f"{prefix}: {display} — " + ", ".join([str(x) for x in flags]))

                        try:
                            is_unknown = bool(getattr(v, "is_unknown_part", False)) if v is not None else False
                        except Exception:
                            is_unknown = False
                        if is_unknown:
                            key = url
                        else:
                            key = (
                                str(getattr(prod, "sku", "") or "").strip()
                                or str(getattr(prod, "handle", "") or "").strip()
                                or url
                            )

                        # UI-only update status (New/Updated)
                        try:
                            is_updated = False
                            try:
                                if normalize_product_url(url) in baseline_urls:
                                    is_updated = True
                            except Exception:
                                pass
                            try:
                                if str(getattr(prod, "sku", "") or "").strip() in baseline_skus:
                                    is_updated = True
                            except Exception:
                                pass
                            setattr(prod, "_ui_update_status", "Updated" if is_updated else "New")
                        except Exception:
                            pass

                        # Set stage to 2 (SCRAPED) - this enables PAI enrichment
                        try:
                            set_stage(prod, 2)
                        except Exception:
                            pass

                        async with counters_lock:
                            products[key] = prod
                            added += 1

                        try:
                            signals.row_ready.emit(
                                {
                                    "kind": "scraped",
                                    "url": url,
                                    "key": key,
                                    "product": prod,
                                }
                            )
                        except Exception:
                            pass
                    except Exception as e:
                        async with counters_lock:
                            failed += 1
                        did_fail = True
                        signals.log.emit(f"   Failed {url}: {e}")
                        try:
                            signals.row_ready.emit({"kind": "failed", "url": url, "error": str(e)})
                        except Exception:
                            pass
                    finally:
                        if raw_dir and raw_dir.exists() and (did_fail or did_skip):
                            shutil.rmtree(raw_dir, ignore_errors=True)
                        async with counters_lock:
                            signals.stats.emit(
                                {
                                    "attempted": attempted,
                                    "added": added,
                                    "skipped": skipped,
                                    "failed": failed,
                                    "total": len(links),
                                    "current_url": url,
                                }
                            )

                tasks: list[asyncio.Task] = []
                for idx, url in enumerate(links, 1):
                    if cancel_event is not None and cancel_event.is_set():
                        signals.log.emit("Scrape cancelled")
                        break
                    tasks.append(asyncio.create_task(_process_one(idx, url)))

                if tasks:
                    await asyncio.gather(*tasks)
            finally:
                await browser.close()

    signals.log.emit(
        f"Scrape summary — Attempted: {attempted} | Added: {len(products)} | Skipped: {skipped} | Failed: {failed}"
    )
    # Ensure final stats are consistent with the actual table payload.
    try:
        added = int(len(products))
    except Exception:
        pass
    try:
        signals.stats.emit(
            {
                "attempted": attempted,
                "added": added,
                "skipped": skipped,
                "failed": failed,
                "total": len(links),
                "current_url": "",
                "phase": "Done",
            }
        )
    except Exception:
        pass
    if cancel_event is not None and cancel_event.is_set():
        signals.done.emit(f"Category scrape stopped: {added} added")
    else:
        signals.done.emit(f"Category scrape complete: {added} added")
    return products
