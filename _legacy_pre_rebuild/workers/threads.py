"""Background thread functions for scraping, uploading, and enrichment operations."""

from __future__ import annotations

import asyncio
import datetime
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from models import Product
from utils.helpers import ensure_dir, ACCESS_TOKEN
from engine.product_engine import ProductEngine
from phases.uploader import upload_to_shopify, export_shopify_to_qbo_csv
from phases.description import generate_engaging_description
from sources.pai_enrichment import enrich_pai_many_parallel, enrich_pai_advisory_many_parallel, download_pai_images_for_product
from sources.pai_builder import build_pai_client_for_run

if TYPE_CHECKING:
    from workers.signals import Signals

__all__ = [
    "run_single_scrape_thread",
    "run_category_scrape_thread",
    "run_estimate_thread",
    "run_family_analyzer_thread",
    "run_upload_thread",
    "run_sku_conflict_check_thread",
    "run_preupload_diff_thread",
    "run_simulate_upload_thread",
    "run_export_qbo_thread",
    "run_regenerate_descriptions_thread",
    "run_refresh_selected_thread",
    "run_pai_advisory_enrichment_thread",
    "run_atl_enrichment_thread",
    "run_shopify_sync_thread",
    "sync_products_to_database",
]


def sync_products_to_database(products: dict[str, Product], signals: "Signals" = None) -> tuple[int, int]:
    """Sync products to SQLite database.
    
    Returns (synced_count, error_count).
    """
    try:
        from db import sync_products_batch
        
        def progress_cb(current, total):
            if signals:
                try:
                    signals.log.emit(f"DB sync: {current}/{total}")
                except Exception:
                    pass
        
        synced, errors = sync_products_batch(products, progress_callback=progress_cb)
        
        if signals:
            try:
                signals.log.emit(f"Database sync complete: {synced} synced, {errors} errors")
            except Exception:
                pass
        
        return synced, errors
    except Exception as e:
        if signals:
            try:
                signals.log.emit(f"Database sync failed: {e}")
            except Exception:
                pass
        return 0, len(products)


def run_single_scrape_thread(url: str, signals: "Signals") -> None:
    """Scrape a single URL in a background thread."""
    # Import here to avoid circular imports
    from phases.async_scraper import scrape_single_url_async
    from workers.signals import redirect_stdout_to_signal

    try:
        output = Path.cwd() / "output"
        images_dir = output / "images"
        temp_dir = output / "_temp_raw"

        validate_only = bool(getattr(signals, "_validate_only", False))
        if not validate_only:
            ensure_dir(images_dir)
            ensure_dir(temp_dir)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        site = str(getattr(signals, "_single_site", "hhp") or "hhp").strip().lower()
        pai_username = getattr(signals, "_pai_username", None)
        pai_password = getattr(signals, "_pai_password", None)
        prod = loop.run_until_complete(
            scrape_single_url_async(
                url,
                images_dir,
                temp_dir,
                signals,
                validate_only=validate_only,
                site=site,
                pai_username=pai_username,
                pai_password=pai_password,
            )
        )
        loop.close()

        if prod is not None:
            # Normalize product for UI
            try:
                from ui.product_entry import normalize_product_for_ui
                engine = getattr(signals, "_engine", None) or ProductEngine()
                prod, _info = normalize_product_for_ui(engine, prod)
            except Exception:
                pass
            try:
                engine = getattr(signals, "_engine", None) or ProductEngine()
                is_unknown = bool(engine.validate_product(prod).is_unknown_part)
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
            
            # Sync single product to database
            if not validate_only:
                try:
                    sync_products_to_database({key: prod}, signals)
                except Exception:
                    pass
            
            signals.products_ready.emit({key: prod})

        # Match GUI cleanup behavior
        if not validate_only:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:
        signals.failed.emit(str(e))


def run_category_scrape_thread(
    main_category: str,
    manufacturer: str,
    new_only: bool,
    limit_text: str,
    signals: "Signals",
    cancel_event: threading.Event | None = None,
) -> None:
    """Scrape a category of products in a background thread."""
    # Import here to avoid circular imports
    from phases.async_scraper import scrape_category_async

    try:
        limit: int | None
        t = (limit_text or "").strip()
        limit = int(t) if t else None
        if limit is not None and limit <= 0:
            limit = None

        output = Path.cwd() / "output"
        images_dir = output / "images"
        temp_dir = output / "_temp_raw"

        validate_only = bool(getattr(signals, "_validate_only", False))
        if not validate_only:
            ensure_dir(images_dir)
            ensure_dir(temp_dir)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        products = loop.run_until_complete(
            scrape_category_async(
                main_category=main_category,
                manufacturer_filter=manufacturer,
                new_only=new_only,
                limit=limit,
                images_dir=images_dir,
                temp_dir=temp_dir,
                signals=signals,
                cancel_event=cancel_event,
                validate_only=validate_only,
            )
        )
        loop.close()

        # Sync to SQLite database (non-blocking, errors logged but don't fail scrape)
        if products and not validate_only:
            try:
                sync_products_to_database(products, signals)
            except Exception as db_err:
                try:
                    signals.log.emit(f"DB sync warning: {db_err}")
                except Exception:
                    pass

        signals.products_ready.emit(products)

        # Match GUI cleanup behavior
        if not validate_only:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:
        signals.failed.emit(str(e))


def run_estimate_thread(
    main_category: str,
    manufacturer: str,
    new_only: bool,
    signals: "Signals",
    cancel_event: threading.Event | None = None,
) -> None:
    """Estimate category listing count in a background thread."""
    # Import here to avoid circular imports
    from phases.async_scraper import estimate_category_listing

    try:
        result = estimate_category_listing(
            main_category=main_category,
            manufacturer=manufacturer,
            new_only=new_only,
            cancel_event=cancel_event,
            signals=signals,
        )
        signals.estimate_ready.emit(result)
    except Exception as e:
        signals.failed.emit(str(e))


def run_family_analyzer_thread(
    items: list[dict],
    signals: "Signals",
    cancel_event: threading.Event | None = None,
) -> None:
    """Analyze product families via cross-references in a background thread.
    
    This is Phase 1.5 - a lightweight HTTP-only analysis that groups products
    by shared cross-reference numbers without doing full scraping.
    """
    from phases.family_analyzer import analyze_product_families

    try:
        result = analyze_product_families(
            items=items,
            signals=signals,
            cancel_event=cancel_event,
        )
        signals.family_analysis_ready.emit(result)
    except Exception as e:
        signals.failed.emit(str(e))


def run_upload_thread(products: dict, publish: bool, include_core: bool, signals: "Signals") -> None:
    """Upload products to Shopify in a background thread."""
    from workers.signals import redirect_stdout_to_signal

    try:
        if not ACCESS_TOKEN:
            raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not set (helpers.ACCESS_TOKEN is empty)")

        images_dir = Path.cwd() / "output" / "images"
        
        def progress_callback(current, total, sku):
            """Report upload progress to UI."""
            try:
                pct = int((current / total) * 100)
                signals.progress.emit(pct, f"Uploading {current}/{total}: {sku}")
            except Exception:
                pass
        
        with redirect_stdout_to_signal(signals):
            upload_to_shopify(
                products_dict=products,
                images_dir=images_dir,
                update_mode=True,
                publish=publish,
                include_core=include_core,
                markup_pct=float(getattr(signals, "_markup_pct", 30.0)),
                check_sku_conflicts=bool(getattr(signals, "_check_sku_conflicts", True)),
                allow_sku_conflicts=bool(getattr(signals, "_allow_sku_conflicts", False)),
                progress_callback=progress_callback,
            )
        signals.done.emit(f"Upload complete: {len(products)} product(s)")
    except Exception as e:
        signals.failed.emit(str(e))


def run_sku_conflict_check_thread(products: dict, signals: "Signals") -> None:
    """Check for SKU conflicts in a background thread."""
    try:
        from phases.uploader import detect_sku_conflicts

        conflicts = detect_sku_conflicts(products, check_shopify=True)
        signals.sku_conflicts_ready.emit(conflicts)
    except Exception as e:
        signals.failed.emit(str(e))


def run_preupload_diff_thread(products: dict, publish: bool, include_core: bool, signals: "Signals") -> None:
    """Build pre-upload diffs in a background thread."""
    try:
        from phases.uploader import build_preupload_diffs

        diffs = build_preupload_diffs(
            products,
            publish=publish,
            include_core=include_core,
            markup_pct=float(getattr(signals, "_markup_pct", 30.0)),
        )
        signals.preupload_diff_ready.emit(diffs)
    except Exception as e:
        signals.failed.emit(str(e))


def run_simulate_upload_thread(products: dict, signals: "Signals") -> None:
    """Simulate upload (preview diffs) in a background thread."""
    from workers.signals import redirect_stdout_to_signal

    try:
        if not ACCESS_TOKEN:
            raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not set (helpers.ACCESS_TOKEN is empty)")

        from phases.uploader import build_preupload_diffs

        with redirect_stdout_to_signal(signals):
            print("PHASE: Upload preview")
            diffs = build_preupload_diffs(
                products,
                publish=bool(getattr(signals, "_publish", True)),
                include_core=bool(getattr(signals, "_include_core", False)),
                markup_pct=float(getattr(signals, "_markup_pct", 30.0)),
            )

            # UI preview (no writes) — show diffs in a dialog.
            try:
                signals.simulate_diff_ready.emit(diffs)
            except Exception:
                pass

            would_create = sum(1 for d in diffs if not d.get("exists"))
            would_update = sum(1 for d in diffs if d.get("exists"))
            print(f"Would create {would_create}, update {would_update} (no writes performed)")

            for d in diffs:
                sku = d.get("sku") or d.get("key")
                print(f"\nSKU: {sku}")
                print(f"Price: {d.get('price_old')} → {d.get('price_new')}")
                print(f"Cost: {d.get('cost_old')} → {d.get('cost_new')}")

                tmpl_old = d.get("template_old")
                tmpl_new = d.get("template_new")
                print(f"Template: {tmpl_old or '—'} → {tmpl_new or '—'}")

                added = d.get("tags_added") or []
                removed = d.get("tags_removed") or []
                if added:
                    print("Tags added: " + ", ".join([str(x) for x in added]))
                if removed:
                    print("Tags removed: " + ", ".join([str(x) for x in removed]))

        signals.done.emit(f"Simulated upload: create {would_create}, update {would_update}")
    except Exception as e:
        signals.failed.emit(str(e))


def run_export_qbo_thread(save_path: str, signals: "Signals") -> None:
    """Export Shopify products to QBO CSV in a background thread."""
    from workers.signals import redirect_stdout_to_signal

    try:
        if not ACCESS_TOKEN:
            raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not set (helpers.ACCESS_TOKEN is empty)")
        with redirect_stdout_to_signal(signals):
            csv_content = export_shopify_to_qbo_csv()
        if not csv_content:
            raise RuntimeError("No CSV content returned")
        Path(save_path).write_text(csv_content, encoding="utf-8")
        signals.exported.emit(save_path)
    except Exception as e:
        signals.failed.emit(str(e))


def run_regenerate_descriptions_thread(products: dict, signals: "Signals") -> None:
    """Regenerate descriptions for products in a background thread."""
    try:
        total = len(products)
        if total <= 0:
            signals.done.emit("No products to regenerate")
            return

        signals.log.emit(f"Regenerating descriptions for {total} product(s)...")
        for idx, (sku, p) in enumerate(products.items(), 1):
            cancel_event = getattr(signals, "_cancel_event", None)
            if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
                signals.done.emit(f"Description regeneration stopped: {idx-1}/{total} processed")
                return

            title = getattr(p, "title", "")
            brand = getattr(p, "brand", "")
            engine = getattr(p, "engine", "")
            kit_contents = getattr(p, "kit_contents", []) or []
            dimensions = getattr(p, "dimensions", "") or ""

            signals.log.emit(f"{idx}/{total}: {sku} — regenerating")
            try:
                html = generate_engaging_description(
                    brand,
                    engine,
                    kit_contents,
                    dimensions,
                    title,
                    xrefs=getattr(p, "xrefs", None),
                    cpl_list=getattr(p, "cpl_list", None),
                )
                setattr(p, "description_jaks_html", html)
            except Exception as e:
                signals.log.emit(f"   ERROR regenerating {sku}: {e}")

        signals.done.emit(f"Descriptions regenerated: {total} product(s)")
    except Exception as e:
        signals.failed.emit(str(e))


def run_refresh_selected_thread(products: dict[str, Product], keys: list[str], signals: "Signals") -> None:
    """Re-enrich selected products (PAI) without rescraping."""

    try:
        if not products or not keys:
            signals.done.emit("No selected products to refresh")
            return

        client = build_pai_client_for_run(signals)
        if client is None:
            raise RuntimeError("PAI refresh requires valid PAI credentials and PAI enrichment enabled")

        regen_desc = bool(getattr(signals, "_regen_desc_on_refresh", False))

        # Phase 2.97 lockdown: do not apply PAI cost/pricing unless Stage == 3.
        try:
            blocked_keys: list[str] = []
            for sku_k in list(keys):
                prod_k = products.get(sku_k)
                if prod_k is None:
                    continue
                try:
                    pr = getattr(prod_k, "pricing", None)
                except Exception:
                    pr = None
                try:
                    st = pr.get("stage") if isinstance(pr, dict) else None
                except Exception:
                    st = None
                try:
                    st_i = int(st) if st is not None and str(st).strip() != "" else 1
                except Exception:
                    st_i = 1
                if st_i <= 0:
                    st_i = 1
                if int(st_i) != 3:
                    blocked_keys.append(str(sku_k))
            if blocked_keys:
                signals.log.emit(
                    f"Blocked Refresh Selected (requires Stage 3): {len(blocked_keys)} item(s) not Stage 3"
                )
                signals.done.emit("Refresh skipped: requires Stage 3 selection")
                return
        except Exception:
            # If we can't verify stages safely, do not run mutating PAI refresh.
            try:
                signals.log.emit("Blocked Refresh Selected: could not verify Stage==3")
                signals.done.emit("Refresh skipped: requires Stage 3 selection")
            except Exception:
                pass
            return

        total = len(keys)
        signals.log.emit(f"Refreshing {total} selected product(s) via PAI...")

        cancel_event = getattr(signals, "_cancel_event", None)
        if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
            signals.done.emit("Refresh stopped: 0 processed")
            return

        # Set up image directory for PAI image downloads (products with 0 images)
        output = Path.cwd() / "output"
        image_dir = output / "_temp_raw"

        counts = enrich_pai_many_parallel(
            products,
            client,
            keys=list(keys),
            image_dir=image_dir,
            download_images_if_zero=True,
        )
        ok = int(counts.get("ok", 0) or 0)
        multi = int(counts.get("multi", 0) or 0)
        no_match = int(counts.get("no_match", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)

        # Phase 2 Checkout Protocols: Stage 3 represents "PAI Checked".
        # Promote all processed products to Stage 3 (do not downgrade), regardless
        # of match outcome.
        try:
            now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        except Exception:
            now_iso = ""
        try:
            for sku in list(keys):
                prod = products.get(sku)
                if prod is None:
                    continue
                try:
                    pr = getattr(prod, "pricing", None)
                except Exception:
                    pr = None
                if not isinstance(pr, dict):
                    pr = {}
                    try:
                        setattr(prod, "pricing", pr)
                    except Exception:
                        pr = {}
                try:
                    st_old = int(pr.get("stage")) if str(pr.get("stage") or "").strip() != "" else 1
                except Exception:
                    st_old = 1
                if st_old <= 0:
                    st_old = 1
                try:
                    pr["stage"] = max(int(st_old), 3)
                except Exception:
                    pass
                if now_iso:
                    try:
                        pr["pai_checked_at"] = now_iso
                    except Exception:
                        pass
        except Exception:
            pass

        if regen_desc:
            for idx, sku in enumerate(list(keys), 1):
                cancel_event = getattr(signals, "_cancel_event", None)
                if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
                    signals.done.emit(f"Refresh stopped: descriptions {idx-1}/{total} processed")
                    return

                prod = products.get(sku)
                if prod is None:
                    continue

                try:
                    title = getattr(prod, "title", "")
                    brand = getattr(prod, "brand", "")
                    engine = getattr(prod, "engine", "")
                    kit_contents = getattr(prod, "kit_contents", []) or []
                    dimensions = getattr(prod, "dimensions", "") or ""
                    html = generate_engaging_description(
                        brand,
                        engine,
                        kit_contents,
                        dimensions,
                        title,
                        xrefs=getattr(prod, "xrefs", None),
                        cpl_list=getattr(prod, "cpl_list", None),
                    )
                    setattr(prod, "description_jaks_html", html)
                except Exception as e:
                    signals.log.emit(f"ERROR regenerating {sku}: {e}")

        try:
            signals.products_ready.emit(products)
        except Exception:
            pass

        signals.done.emit(
            f"Refresh complete: ok {ok} | multi {multi} | no_match {no_match} | failed {failed}"
            + (" | desc regenerated" if regen_desc else "")
        )
    except Exception as e:
        signals.failed.emit(str(e))


def run_pai_advisory_enrichment_thread(
    products: dict[str, Product],
    keys: list[str],
    signals: "Signals",
    scope_category: str = "",
    scope_manufacturer: str = "",
) -> None:
    """Phase 3: PAI enrichment (advisory-only).

    Writes Product.pai_advisory and promotes products to Stage 3.
    Also downloads PAI images for products that have no images.
    
    Args:
        scope_category: Current UI category selection (for image folder path)
        scope_manufacturer: Current UI manufacturer selection (for image folder path)
    """
    import time
    
    try:
        if not products or not keys:
            signals.done.emit("Phase 3: no selected products")
            return

        client = build_pai_client_for_run(signals)
        if client is None:
            raise RuntimeError("PAI enrichment requires valid PAI credentials and PAI enrichment enabled")

        total = len(keys)
        signals.log.emit(f"Phase 3: running advisory PAI enrichment for {total} product(s)...")

        cancel_event = getattr(signals, "_cancel_event", None)
        if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
            signals.done.emit("Phase 3 stopped: 0 processed")
            return

        # Progress tracking with ETA
        start_time = time.time()
        
        # Progress callback to update UI with ETA
        def _on_progress(completed: int, total_count: int) -> None:
            try:
                elapsed = time.time() - start_time
                if completed > 0:
                    avg_per_item = elapsed / completed
                    remaining = (total_count - completed) * avg_per_item
                    eta_min = int(remaining // 60)
                    eta_sec = int(remaining % 60)
                    eta_str = f"ETA: {eta_min}m {eta_sec}s" if eta_min > 0 else f"ETA: {eta_sec}s"
                else:
                    eta_str = "calculating..."
                
                pct = int((completed / total_count) * 100) if total_count > 0 else 0
                signals.progress.emit(pct, f"Phase 3: {completed}/{total_count} ({eta_str})")
            except Exception:
                pass

        counts = enrich_pai_advisory_many_parallel(products, client, keys=list(keys), on_progress=_on_progress)
        ok = int(counts.get("ok", 0) or 0)
        multi = int(counts.get("multi", 0) or 0)
        no_match = int(counts.get("no_match", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)

        # Download PAI images for products that have no images but have a PAI match
        images_downloaded = 0
        signals.log.emit(f"Checking {len(keys)} products for PAI image downloads...")
        for k in keys:
            prod = products.get(k)
            if prod is None:
                continue
            try:
                # Check if product has no images
                img_count = int(getattr(prod, "image_count_raw", 0) or 0)
                if img_count > 0:
                    continue
                
                # Get PAI SKU from product (set by enrich_pai_advisory)
                pai_sku = str(getattr(prod, "pai_sku", "") or "").strip()
                if not pai_sku:
                    continue
                
                # Use lookup_cost to get image URLs for this SKU
                lookup = None
                try:
                    lookup = client.lookup_cost(pai_sku)
                except Exception:
                    lookup = None
                
                if not isinstance(lookup, dict):
                    continue
                
                image_urls = lookup.get("image_urls", [])
                if not image_urls:
                    continue
                
                # Get product identifiers for folder path
                handle = str(getattr(prod, "handle", "") or "").strip()
                sku = str(getattr(prod, "sku", "") or "").strip()
                
                # Get category/manufacturer - use scope if specific, otherwise use product attributes
                prod_category = str(getattr(prod, "category", "") or "").strip()
                prod_manufacturer = str(getattr(prod, "manufacturer", "") or "").strip()
                
                # Use scope only if it's a specific value (not "All" or empty)
                category = prod_category
                manufacturer = prod_manufacturer
                if scope_category and scope_category.lower() not in ("all", ""):
                    category = scope_category
                if scope_manufacturer and scope_manufacturer.lower() not in ("all", ""):
                    manufacturer = scope_manufacturer
                
                if not handle:
                    continue
                
                # Follow normal flow: download to _temp_raw, then watermark to final folder
                # Step 1: Download to temp folder
                temp_raw_path = Path.cwd() / "output" / "_temp_raw" / handle
                
                downloaded = download_pai_images_for_product(
                    prod,
                    image_urls,
                    temp_raw_path,
                    handle=handle,
                    remove_watermark=True,
                    max_images=6,
                )
                
                if downloaded > 0:
                    images_downloaded += downloaded
                    signals.log.emit(f"[PAI Images] Downloaded {downloaded} image(s) for {handle}")
                    
                    # PAI images are NOT watermarked - move directly to final folder
                    if category and manufacturer:
                        from phases.scraper import get_sku_images_folder
                        import shutil
                        
                        final_path = get_sku_images_folder(category, manufacturer, sku or handle)
                        final_path.mkdir(parents=True, exist_ok=True)
                        
                        # Move raw PAI images to final folder (no watermark)
                        try:
                            moved = 0
                            for img_file in temp_raw_path.glob("*.jpg"):
                                dest = final_path / img_file.name
                                shutil.copy2(img_file, dest)
                                moved += 1
                            if moved > 0:
                                signals.log.emit(f"[PAI Images] Moved {moved} image(s) -> {final_path} (no watermark)")
                                # Update product image count
                                try:
                                    setattr(prod, "image_count", moved)
                                except Exception:
                                    pass
                        except Exception as mv_err:
                            signals.log.emit(f"[PAI Images] Move failed: {mv_err}")
                    
            except Exception as e:
                signals.log.emit(f"[PAI Images] Error downloading for {k}: {e}")

        # Promote all processed products to Stage 3 (regardless of match outcome)
        promoted = 0
        for k in keys:
            prod = products.get(k)
            if prod is None:
                continue
            try:
                pr = getattr(prod, "pricing", None)
                if not isinstance(pr, dict):
                    pr = {}
                    setattr(prod, "pricing", pr)
                st_old = int(pr.get("stage", 2) or 2)
                if st_old < 3:
                    pr["stage"] = 3
                    promoted += 1
            except Exception:
                pass

        if promoted > 0:
            signals.log.emit(f"Phase 3: promoted {promoted} product(s) to Stage 3")

        try:
            signals.products_ready.emit(products)
        except Exception:
            pass

        msg = f"Phase 3 complete: ok {ok} | multi {multi} | no_match {no_match} | failed {failed}"
        if images_downloaded > 0:
            msg += f" | {images_downloaded} PAI images downloaded"
        signals.done.emit(msg)
    except Exception as e:
        signals.failed.emit(str(e))


def run_atl_enrichment_thread(
    products: dict[str, Product],
    keys: list[str],
    signals: "Signals",
    download_images: bool = True,
) -> None:
    """Phase 2.5: ATL Diesel enrichment (competitor pricing & images).

    Searches atldiesel.com for matching products and extracts:
    - Competitor pricing (regular + sale)
    - Product images (optional download)
    - Vendor info (PAI vs ATL Diesel)
    - Stock availability
    - Related product recommendations
    
    Args:
        products: Dict of SKU -> Product
        keys: List of SKUs to process
        signals: Worker signals for progress/logging
        download_images: Whether to download ATL images
    """
    import time
    import asyncio
    from sources.atl_diesel import (
        search_atl_diesel,
        download_atl_images,
        update_product_with_atl_result,
        ATL_RATE_LIMIT,
    )
    
    try:
        if not products or not keys:
            signals.done.emit("Phase 2.5: no selected products")
            return

        total = len(keys)
        signals.log.emit(f"Phase 2.5: running ATL Diesel enrichment for {total} product(s)...")

        cancel_event = getattr(signals, "_cancel_event", None)
        if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
            signals.done.emit("Phase 2.5 stopped: 0 processed")
            return

        # Progress tracking with ETA
        start_time = time.time()
        
        # Counters
        found = 0
        not_found = 0
        failed = 0
        images_downloaded = 0
        
        # Create async event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            import aiohttp
            
            async def process_all():
                nonlocal found, not_found, failed, images_downloaded
                
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
                ) as session:
                    for idx, sku in enumerate(keys, 1):
                        # Check for cancellation
                        if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
                            return
                        
                        prod = products.get(sku)
                        if prod is None:
                            continue
                        
                        # Get part number to search for
                        primary_pn = (
                            getattr(prod, "primary_pn", "") or 
                            getattr(prod, "oem_part_number", "") or
                            getattr(prod, "pai_part_number", "") or
                            sku.replace("JAKS-", "")
                        )
                        
                        if not primary_pn or primary_pn == "UNKNOWN_PART":
                            signals.log.emit(f"  {idx}/{total}: {sku} - skipped (no part number)")
                            not_found += 1
                            continue
                        
                        # Update progress with ETA
                        try:
                            elapsed = time.time() - start_time
                            if idx > 1:
                                avg_per_item = elapsed / (idx - 1)
                                remaining = (total - idx + 1) * avg_per_item
                                eta_min = int(remaining // 60)
                                eta_sec = int(remaining % 60)
                                eta_str = f"ETA: {eta_min}m {eta_sec}s" if eta_min > 0 else f"ETA: {eta_sec}s"
                            else:
                                eta_str = "calculating..."
                            
                            pct = int((idx / total) * 100) if total > 0 else 0
                            signals.progress.emit(pct, f"Phase 2.5: {idx}/{total} ({eta_str})")
                        except Exception:
                            pass
                        
                        signals.log.emit(f"  {idx}/{total}: {sku} - searching ATL for '{primary_pn}'...")
                        
                        try:
                            result = await search_atl_diesel(primary_pn, session)
                            
                            # If not found, try cross-references
                            if not result.found:
                                xrefs = getattr(prod, "xrefs", []) or []
                                pai_sku = str(getattr(prod, "pai_sku", "") or "").strip()
                                oem_pn = str(getattr(prod, "oem_pn", "") or getattr(prod, "oem_part_number", "") or "").strip()
                                
                                # Build list of alternate SKUs to try
                                alt_skus = []
                                if pai_sku and pai_sku != primary_pn:
                                    alt_skus.append(pai_sku)
                                if oem_pn and oem_pn not in [primary_pn, pai_sku]:
                                    alt_skus.append(oem_pn)
                                for xref in xrefs[:5]:  # Limit to first 5 xrefs
                                    xref_clean = str(xref or "").strip()
                                    if xref_clean and xref_clean not in [primary_pn, pai_sku, oem_pn] and xref_clean not in alt_skus:
                                        alt_skus.append(xref_clean)
                                
                                # Try each alternate
                                for alt_sku in alt_skus:
                                    if isinstance(cancel_event, threading.Event) and cancel_event.is_set():
                                        break
                                    signals.log.emit(f"    → Trying cross-ref: '{alt_sku}'...")
                                    await asyncio.sleep(ATL_RATE_LIMIT)
                                    result = await search_atl_diesel(alt_sku, session)
                                    if result.found:
                                        # Mark as cross-reference match
                                        result.is_cross_reference = True
                                        result.matched_query = alt_sku
                                        break
                            
                            if result.found:
                                found += 1
                                
                                # Update product with ATL data
                                update_product_with_atl_result(prod, result)
                                
                                price_str = f"${result.atl_price:.2f}" if result.atl_price else "N/A"
                                xref_note = f" (via {result.matched_query})" if result.is_cross_reference else ""
                                signals.log.emit(f"    ✓ Found: {result.atl_sku or result.atl_title[:40]} - {price_str}{xref_note}")
                                
                                # Download images if requested
                                if download_images and result.image_urls:
                                    category = getattr(prod, "category", "uncategorized") or "uncategorized"
                                    manufacturer = getattr(prod, "manufacturer", "unknown") or "unknown"
                                    
                                    # Normalize folder names (lowercase, underscores) - consistent with upload_screen.py
                                    cat_folder = category.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
                                    mfr_folder = manufacturer.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
                                    
                                    # Get handle for folder name
                                    handle = getattr(prod, "handle", "") or sku.replace("JAKS-", "").lower().replace("-", "_")
                                    handle_underscore = handle.lower().replace("-", "_")
                                    
                                    images_dir = Path("output") / cat_folder / mfr_folder / f"jaks_{handle_underscore}" / "images" / "atl"
                                    
                                    downloaded = await download_atl_images(result, images_dir, watermark=True)
                                    if downloaded:
                                        images_downloaded += len(downloaded)
                                        # Store ATL image paths on product
                                        setattr(prod, "images_atl", [str(p) for p in downloaded])
                                        signals.log.emit(f"    ✓ Downloaded {len(downloaded)} ATL images")
                                
                                # Log related products found
                                if result.frequently_bought:
                                    signals.log.emit(f"    ↳ Frequently Bought Together: {len(result.frequently_bought)} items")
                                if result.related_products:
                                    signals.log.emit(f"    ↳ Related Products: {len(result.related_products)} items")
                            
                            elif result.error:
                                failed += 1
                                signals.log.emit(f"    ✗ Error: {result.error}")
                            else:
                                not_found += 1
                                signals.log.emit(f"    - Not found on ATL Diesel")
                        
                        except Exception as e:
                            failed += 1
                            signals.log.emit(f"    ✗ Exception: {e}")
                        
                        # Rate limiting between requests
                        await asyncio.sleep(ATL_RATE_LIMIT)
            
            loop.run_until_complete(process_all())
            
        finally:
            loop.close()

        # Emit updated products
        try:
            signals.products_ready.emit(products)
        except Exception:
            pass

        msg = f"Phase 2.5 complete: found {found} | not_found {not_found} | failed {failed}"
        if images_downloaded > 0:
            msg += f" | {images_downloaded} ATL images downloaded"
        signals.done.emit(msg)
        
    except Exception as e:
        signals.failed.emit(str(e))


def run_shopify_sync_thread(
    products: dict[str, Product],
    signals: "Signals",
) -> None:
    """
    Run Shopify sync check in a background thread.
    
    Compares local products to Shopify and emits sync_status_ready signal
    with comparison results.
    """
    from phases.uploader import build_sync_status
    from utils.helpers import ACCESS_TOKEN
    
    try:
        if not ACCESS_TOKEN:
            signals.log.emit("No Shopify access token configured")
            signals.sync_status_ready.emit({
                'error': 'No Shopify access token configured',
                'local_count': len(products or {}),
                'shopify_count': 0,
                'synced': [],
                'modified': [],
                'not_uploaded': list((products or {}).keys()),
                'orphaned': [],
                'details': {},
            })
            signals.done.emit("Sync check complete (no token)")
            return
        
        signals.log.emit(f"Starting Shopify sync check for {len(products or {})} local products...")
        
        def progress_cb(pct_or_page, msg):
            try:
                if isinstance(pct_or_page, int) and pct_or_page <= 100:
                    signals.progress.emit(pct_or_page, msg)
                signals.log.emit(msg)
            except Exception:
                pass
        
        # Build sync status
        sync_result = build_sync_status(products, progress_callback=progress_cb)
        
        # Log summary
        signals.log.emit(f"Sync check complete:")
        signals.log.emit(f"  Local products: {sync_result.get('local_count', 0)}")
        signals.log.emit(f"  Shopify products: {sync_result.get('shopify_count', 0)}")
        signals.log.emit(f"  ✓ Synced: {len(sync_result.get('synced', []))}")
        signals.log.emit(f"  ⚠ Modified: {len(sync_result.get('modified', []))}")
        signals.log.emit(f"  ⊕ Not uploaded: {len(sync_result.get('not_uploaded', []))}")
        signals.log.emit(f"  ⊗ Orphaned on Shopify: {len(sync_result.get('orphaned', []))}")
        
        signals.sync_status_ready.emit(sync_result)
        signals.done.emit("Shopify sync check complete")
        
    except Exception as e:
        signals.log.emit(f"Sync error: {e}")
        signals.failed.emit(str(e))
