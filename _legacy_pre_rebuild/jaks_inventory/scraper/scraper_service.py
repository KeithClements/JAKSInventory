"""Scraper service — orchestrates DPD, ATL, FleetPride and HHP searches.

This is the sync/async bridge layer. It wraps the existing source scrapers
and funnels their output into the ``db`` layer
(``save_scraped_xref``, ``save_competitor_price``, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sources.dpd_scraper import DPDScraper
from sources.atl_diesel import search_atl_diesel
from sources.fleetpride_scraper import FleetPrideScraper
from sources.hhp_scraper import HHPScraper

from db import (
    create_scrape_run,
    update_scrape_run,
    save_scraped_xref,
    save_competitor_price,
    get_product_by_sku,
    get_product_by_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL-based direct scraping
# ---------------------------------------------------------------------------

def _scrape_url_for_xrefs(
    url: str,
    *,
    product_id: int | None = None,
    on_progress: Any = None,
) -> dict:
    """Scrape a single product page URL for cross-reference part numbers.

    Works with HHP, DPD, FleetPride, or any product page that lists
    related/replacement part numbers.
    """
    import re
    import httpx

    errors: list[str] = []
    run_id = create_scrape_run("xref", "direct_url", notes=f"URL: {url}")
    found = 0

    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(0, 1, msg)

    _progress(f"Fetching {url}…")

    try:
        from bs4 import BeautifulSoup

        client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        try:
            resp = client.get(url)
            resp.raise_for_status()
        finally:
            client.close()

        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        # Determine the source from URL domain
        source = "URL"
        if "highwayandheavyparts" in url:
            source = "HHP"
        elif "dieselpartsdirect" in url:
            source = "DPD"
        elif "fleetpride" in url:
            source = "FleetPride"
        elif "atldiesel" in url:
            source = "ATL"

        # --- Extract cross-reference numbers ---
        xrefs: set[str] = set()

        # 1) HHP custom tab with comma-separated cross-reference numbers
        xref_tab = soup.select_one("#tab-hhp_product_custom_tab")
        if xref_tab:
            for p_elem in xref_tab.select("p"):
                nums = re.findall(r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]+)*\b", p_elem.get_text())
                xrefs.update(nums)

        # 2) Explicit cross-ref / replaces / OEM patterns in page text
        for pattern in (
            r"(?:Replaces?|OEM|Cross[\s-]*Ref(?:erence)?s?|Interchange|Also\s+known\s+as)\s*[:#]?\s*([\d\-A-Z,\s]{5,})",
        ):
            for m in re.finditer(pattern, page_text, re.IGNORECASE):
                nums = re.findall(r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]+)*\b", m.group(1))
                xrefs.update(nums)

        # 3) Part numbers from structured HTML elements
        for sel in (".cross-references", ".xrefs", ".alternates",
                     "[data-cross-ref]", ".replaces"):
            section = soup.select_one(sel)
            if section:
                nums = re.findall(r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]+)*\b", section.get_text())
                xrefs.update(nums)

        # 3) Extract the page's own primary part number (to exclude it)
        own_pn = ""
        title_elem = soup.select_one("h1")
        if title_elem:
            title_text = title_elem.get_text(strip=True)
            pn_match = re.search(r"\b(\d{5,})\b", title_text)
            if pn_match:
                own_pn = pn_match.group(1)

        # 4) Also try to scrape HHP product detail specifically
        if "highwayandheavyparts" in url:
            try:
                hhp = HHPScraper()
                detail = hhp.get_product_detail(url)
                if detail:
                    if detail.part_number:
                        own_pn = own_pn or detail.part_number
                    for ref in detail.oem_refs:
                        xrefs.add(ref)
                    for ref in detail.aftermarket_refs:
                        xrefs.add(ref)
                hhp.close()
            except Exception:
                pass

        # Remove the page's own PN and noise
        xrefs.discard(own_pn)
        xrefs = {
            x for x in xrefs
            if len(x) >= 5
            and any(c.isdigit() for c in x)          # must contain at least one digit
            and not x.startswith(("HTTP", "HTTPS"))
        }

        # Deduplicate dash variants: keep only the non-dashed form
        # e.g. if both "222-6727" and "2226727" are present, keep "2226727"
        normalized = {}
        for x in xrefs:
            key = x.replace("-", "")
            # Prefer the version without dashes
            if key not in normalized or "-" not in x:
                normalized[key] = x
        xrefs = set(normalized.values())

        _progress(f"Found {len(xrefs)} cross-references on page")

        for pn in sorted(xrefs):
            saved = save_scraped_xref(
                run_id,
                source_part_number=own_pn or url,
                found_alternate_number=pn,
                source=source,
                product_id=product_id,
                confidence="medium",
                match_type="Cross-Reference",
            )
            if saved:
                found += 1

    except Exception as exc:
        errors.append(f"URL scrape: {exc}")
        logger.exception("URL-based xref scrape failed for %s", url)

    update_scrape_run(run_id, status="completed", total_searched=1, total_found=found)
    return {"searched": 1, "found": found, "run_id": run_id, "errors": errors}


# ---------------------------------------------------------------------------
# Cross-reference scraping
# ---------------------------------------------------------------------------


def scrape_xrefs_for_product(
    sku: str,
    *,
    product_id: int | None = None,
    on_progress: Any = None,
    alt_terms: list[str] | None = None,
) -> dict:
    """Search DPD + ATL + FleetPride + HHP for cross-references related to *sku*.

    If *sku* is a URL (starts with http), scrape that page directly for
    cross-reference part numbers instead of running the 4-source search.

    *alt_terms* — additional part numbers to try if the primary *sku* returns
    no results for a given source (e.g. OEM part # alongside vendor SKU).

    Returns a summary dict:
        {"searched": int, "found": int, "run_id": int, "errors": list[str]}

    ``on_progress`` — optional callable(current, total, message) for UI updates.
    """
    # URL-based direct scrape
    if sku.startswith(("http://", "https://")):
        return _scrape_url_for_xrefs(sku, product_id=product_id, on_progress=on_progress)

    errors: list[str] = []

    # Build ordered list of search terms to try per source
    _terms = [sku]
    if alt_terms:
        for t in alt_terms:
            t = t.strip()
            if t and t not in _terms:
                _terms.append(t)

    # Resolve product_id if not given
    if product_id is None:
        prod = get_product_by_sku(sku)
        if prod:
            product_id = prod["id"]

    run_id = create_scrape_run("xref", "dpd+atl+fp+hhp", notes=f"SKU: {sku}")
    searched = 0
    found = 0
    total_sources = 4

    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(searched, total_sources, msg)

    # --- DPD ---
    try:
        dpd = DPDScraper()
        try:
            dpd_found = 0
            for term in _terms:
                _progress(f"Searching DPD for {term}…")
                result = dpd.search(term)
                searched += 1
                for prod in result.products:
                    pn = prod.part_number.strip()
                    if not pn or pn.upper() in {t.upper() for t in _terms}:
                        continue
                    saved = save_scraped_xref(
                        run_id,
                        source_part_number=term,
                        found_alternate_number=pn,
                        source="DPD",
                        product_id=product_id,
                        confidence="medium",
                        match_type="Cross-Reference" if prod.manufacturer else "Unknown",
                    )
                    if saved:
                        found += 1
                        dpd_found += 1
                    # Also save OEM refs from detail page
                    for oem in prod.oem_refs:
                        s = save_scraped_xref(
                            run_id,
                            source_part_number=term,
                            found_alternate_number=oem,
                            source="DPD",
                            product_id=product_id,
                            confidence="medium",
                            match_type="OEM",
                        )
                        if s:
                            found += 1
                            dpd_found += 1
                    for am in prod.aftermarket_refs:
                        s = save_scraped_xref(
                            run_id,
                            source_part_number=term,
                            found_alternate_number=am,
                            source="DPD",
                            product_id=product_id,
                            confidence="medium",
                            match_type="Aftermarket",
                        )
                        if s:
                            found += 1
                            dpd_found += 1
                if dpd_found > 0:
                    break  # Got results, skip remaining alt terms for DPD
        finally:
            dpd.close()
    except Exception as exc:
        errors.append(f"DPD: {exc}")
        logger.exception("DPD xref scrape failed for %s", sku)

    # --- ATL (async → sync bridge) ---
    try:
        atl_found = 0
        atl_result = None
        for term in _terms:
            _progress(f"Searching ATL Diesel for {term}…")
            atl_result = asyncio.run(search_atl_diesel(term))
            searched += 1
            # Save ATL's own SKU as a cross-reference (exact or close match)
            if atl_result.atl_sku:
                atl_pn = atl_result.atl_sku.strip()
                if atl_pn.upper() not in {t.upper() for t in _terms}:
                    saved = save_scraped_xref(
                        run_id,
                        source_part_number=term,
                        found_alternate_number=atl_pn,
                        source="ATL",
                        product_id=product_id,
                        confidence="high" if not atl_result.is_cross_reference else "medium",
                        match_type="Cross-Reference",
                    )
                    if saved:
                        found += 1
                        atl_found += 1
            # Save cross-references from product page "Cross-Referenced Parts" tab only
            for xref_pn in atl_result.cross_references:
                xref_pn = xref_pn.strip()
                if not xref_pn or xref_pn.upper() in {t.upper() for t in _terms}:
                    continue
                # Filter out short fragments (truncated part numbers)
                if len(xref_pn) < 5:
                    continue
                saved = save_scraped_xref(
                    run_id,
                    source_part_number=term,
                    found_alternate_number=xref_pn,
                    source="ATL",
                    product_id=product_id,
                    confidence="high",
                    match_type="OEM",
                )
                if saved:
                    found += 1
                    atl_found += 1
                    logger.info("ATL product page xref: %s → %s", term, xref_pn)
            if atl_found > 0 or atl_result.cross_references:
                break  # Got results, skip remaining alt terms for ATL
    except Exception as exc:
        errors.append(f"ATL: {exc}")
        logger.exception("ATL xref scrape failed for %s", sku)

    # --- FleetPride ---
    try:
        fp = FleetPrideScraper()
        try:
            fp_found = 0
            skip_terms = {t.upper() for t in _terms}
            for term in _terms:
                _progress(f"Searching FleetPride for {term}\u2026")
                fp_result = fp.search(term)
                searched += 1
                for prod in fp_result.products:
                    pn = prod.part_number.strip()
                    if not pn or pn.upper() in skip_terms:
                        continue
                    saved = save_scraped_xref(
                        run_id,
                        source_part_number=term,
                        found_alternate_number=pn,
                        source="FleetPride",
                        product_id=product_id,
                        confidence="medium",
                        match_type="Cross-Reference" if prod.manufacturer else "Unknown",
                    )
                    if saved:
                        found += 1
                        fp_found += 1
                    for oem in prod.oem_refs:
                        s = save_scraped_xref(
                            run_id,
                            source_part_number=term,
                            found_alternate_number=oem,
                            source="FleetPride",
                            product_id=product_id,
                            confidence="medium",
                            match_type="OEM",
                        )
                        if s:
                            found += 1
                            fp_found += 1
                # Navigate into first product page and extract cross-references
                if fp_result.products and fp_result.products[0].url:
                    first_url = fp_result.products[0].url
                    _progress(f"Checking FleetPride product page for xrefs\u2026")
                    page_xrefs = fp.get_product_detail(first_url)
                    for xref_pn in page_xrefs:
                        xref_pn = xref_pn.strip()
                        if not xref_pn or xref_pn.upper() in skip_terms:
                            continue
                        s = save_scraped_xref(
                            run_id,
                            source_part_number=term,
                            found_alternate_number=xref_pn,
                            source="FleetPride",
                            product_id=product_id,
                            confidence="high",
                            match_type="OEM",
                        )
                        if s:
                            found += 1
                            fp_found += 1
                            logger.info("FleetPride product page xref: %s \u2192 %s", term, xref_pn)
                if fp_found > 0:
                    break  # Got results, skip remaining alt terms for FleetPride
        finally:
            fp.close()
    except Exception as exc:
        errors.append(f"FleetPride: {exc}")
        logger.exception("FleetPride xref scrape failed for %s", sku)

    # --- HHP ---
    try:
        hhp = HHPScraper()
        try:
            hhp_found = 0
            skip_terms = {t.upper() for t in _terms}
            for term in _terms:
                _progress(f"Searching HHP for {term}\u2026")
                hhp_result = hhp.search(term)
                searched += 1
                for prod in hhp_result.products[:3]:
                    pn = prod.part_number.strip()
                    if not pn or pn.upper() in skip_terms:
                        continue
                    saved = save_scraped_xref(
                        run_id,
                        source_part_number=term,
                        found_alternate_number=pn,
                        source="HHP",
                        product_id=product_id,
                        confidence="medium",
                        match_type="Cross-Reference" if prod.manufacturer else "Unknown",
                    )
                    if saved:
                        found += 1
                        hhp_found += 1

                # Fetch detail pages to extract OEM cross-references
                for prod in hhp_result.products[:3]:
                    if not prod.url:
                        continue
                    _progress(f"Fetching HHP detail for xrefs\u2026")
                    detail = hhp.get_product_detail(prod.url)
                    if not detail:
                        continue
                    for oem in detail.oem_refs:
                        oem = oem.strip()
                        if not oem or len(oem) < 4 or oem.upper() in skip_terms:
                            continue
                        s = save_scraped_xref(
                            run_id,
                            source_part_number=term,
                            found_alternate_number=oem,
                            source="HHP",
                            product_id=product_id,
                            confidence="medium",
                            match_type="OEM",
                        )
                        if s:
                            found += 1
                            hhp_found += 1
                if hhp_found > 0:
                    break  # Got results, skip remaining alt terms for HHP
        finally:
            hhp.close()
    except Exception as exc:
        errors.append(f"HHP: {exc}")
        logger.exception("HHP xref scrape failed for %s", sku)

    update_scrape_run(run_id, status="completed", total_searched=searched, total_found=found)
    return {"searched": searched, "found": found, "run_id": run_id, "errors": errors}


# ---------------------------------------------------------------------------
# Competitor-pricing scraping
# ---------------------------------------------------------------------------


def scrape_competitor_prices_for_product(
    sku: str,
    *,
    product_id: int | None = None,
    on_progress: Any = None,
    alt_terms: list[str] | None = None,
) -> dict:
    """Search DPD + ATL + FleetPride + HHP for competitor pricing on *sku*.

    *alt_terms* — additional part numbers to try if the primary *sku* returns
    no results for a given source (e.g. OEM part # alongside vendor SKU).

    Returns a summary dict:
        {"searched": int, "found": int, "run_id": int, "errors": list[str]}
    """
    errors: list[str] = []

    # Build ordered list of search terms to try per source
    _terms = [sku]
    if alt_terms:
        for t in alt_terms:
            t = t.strip()
            if t and t not in _terms:
                _terms.append(t)

    if product_id is None:
        prod = get_product_by_sku(sku)
        if prod:
            product_id = prod["id"]

    run_id = create_scrape_run("pricing", "dpd+atl+fp+hhp", notes=f"SKU: {sku}")
    searched = 0
    found = 0
    total_sources = 4

    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(searched, total_sources, msg)

    # --- DPD ---
    try:
        dpd = DPDScraper()
        try:
            for term in _terms:
                _progress(f"Checking DPD pricing for {term}…")
                result = dpd.search(term)
                searched += 1
                dpd_found = 0
                for prod in result.products:
                    if prod.price is not None and prod.price > 0:
                        saved = save_competitor_price(
                            run_id,
                            sku_searched=term,
                            competitor_name="Diesel Parts Direct",
                            product_id=product_id,
                            competitor_part_number=prod.part_number,
                            competitor_price=prod.price,
                            availability="In Stock" if prod.in_stock else "Unknown",
                            source_url=prod.url,
                        )
                        if saved:
                            found += 1
                            dpd_found += 1
                if dpd_found > 0:
                    break
        finally:
            dpd.close()
    except Exception as exc:
        errors.append(f"DPD: {exc}")
        logger.exception("DPD pricing scrape failed for %s", sku)

    # --- ATL ---
    try:
        for term in _terms:
            _progress(f"Checking ATL Diesel pricing for {term}…")
            atl_result = asyncio.run(search_atl_diesel(term))
            searched += 1
            # Save pricing even for close matches (related part numbers)
            if atl_result.atl_price and atl_result.atl_price > 0:
                saved = save_competitor_price(
                    run_id,
                    sku_searched=term,
                    competitor_name="ATL Diesel",
                    product_id=product_id,
                    competitor_part_number=atl_result.atl_sku,
                    competitor_price=atl_result.atl_price,
                    availability=atl_result.atl_stock or "Unknown",
                    source_url=atl_result.atl_url,
                )
                if saved:
                    found += 1
                break  # Got a price, skip remaining alt terms for ATL
    except Exception as exc:
        errors.append(f"ATL: {exc}")
        logger.exception("ATL pricing scrape failed for %s", sku)

    # --- FleetPride ---
    try:
        fp = FleetPrideScraper()
        try:
            for term in _terms:
                _progress(f"Checking FleetPride pricing for {term}\u2026")
                fp_result = fp.search(term)
                searched += 1
                fp_found = 0
                for prod in fp_result.products:
                    if prod.price is not None and prod.price > 0:
                        saved = save_competitor_price(
                            run_id,
                            sku_searched=term,
                            competitor_name="FleetPride",
                            product_id=product_id,
                            competitor_part_number=prod.part_number,
                            competitor_price=prod.price,
                            availability=prod.availability or ("In Stock" if prod.in_stock else "Unknown"),
                            source_url=prod.url,
                        )
                        if saved:
                            found += 1
                            fp_found += 1
                if fp_found > 0:
                    break
        finally:
            fp.close()
    except Exception as exc:
        errors.append(f"FleetPride: {exc}")
        logger.exception("FleetPride pricing scrape failed for %s", sku)

    # --- HHP ---
    try:
        hhp = HHPScraper()
        try:
            for term in _terms:
                _progress(f"Checking HHP pricing for {term}\u2026")
                hhp_result = hhp.search(term)
                searched += 1
                hhp_found = 0
                if hhp_result.error and not hhp_result.products:
                    logger.warning("HHP pricing: %s (query=%s)", hhp_result.error, term)
                # If search returned products without prices, do a deterministic
                # detail-page fetch for the first product to recover the price.
                if hhp_result.products and not any(
                    p.price is not None and p.price > 0 for p in hhp_result.products
                ):
                    first = hhp_result.products[0]
                    if first.url:
                        try:
                            detail = hhp.get_product_detail(first.url)
                            if detail and detail.price is not None and detail.price > 0:
                                first.price = detail.price
                                if not first.part_number and detail.part_number:
                                    first.part_number = detail.part_number
                        except Exception as detail_exc:
                            logger.warning(
                                "HHP detail retry failed for %s: %s", first.url, detail_exc
                            )
                for prod in hhp_result.products:
                    if prod.price is not None and prod.price > 0:
                        saved = save_competitor_price(
                            run_id,
                            sku_searched=term,
                            competitor_name="Highway & Heavy Parts",
                            product_id=product_id,
                            competitor_part_number=prod.part_number,
                            competitor_price=prod.price,
                            availability=prod.availability or ("In Stock" if prod.in_stock else "Unknown"),
                            source_url=prod.url,
                        )
                        if saved:
                            found += 1
                            hhp_found += 1
                # Anti-false-negative: log when products exist but none had valid prices
                if hhp_result.products and not any(
                    p.price is not None and p.price > 0 for p in hhp_result.products
                ):
                    errors.append(
                        f"HHP: {len(hhp_result.products)} product(s) found but none had prices"
                    )
                    logger.warning(
                        "HHP pricing false-negative risk: %d products returned for '%s' "
                        "but none had extractable prices",
                        len(hhp_result.products), term,
                    )
                if hhp_found > 0:
                    break
        finally:
            hhp.close()
    except Exception as exc:
        errors.append(f"HHP: {exc}")
        logger.exception("HHP pricing scrape failed for %s", sku)

    update_scrape_run(run_id, status="completed", total_searched=searched, total_found=found)
    return {"searched": searched, "found": found, "run_id": run_id, "errors": errors}


# ---------------------------------------------------------------------------
# PAI image scrape (per-product, runs from Edit Product dialog)
# ---------------------------------------------------------------------------

def _resolve_product_images_dir(product_id: int) -> str:
    """Return `<db_dir>/images/<product_id>/` to match edit_product_dialog layout."""
    import os
    try:
        from db.database import get_database_url
        db_url = get_database_url() or ""
    except Exception:
        db_url = os.environ.get("DATABASE_URL", "") or ""
    db_path = db_url.replace("sqlite:///", "") if db_url.startswith("sqlite") else ""
    base = os.path.dirname(db_path) if db_path else os.getcwd()
    if not base:
        base = os.getcwd()
    return os.path.join(base, "images", str(product_id))


def scrape_pai_images_for_product(
    sku: str,
    *,
    product_id: int | None = None,
    on_progress: Any = None,
    alt_terms: list[str] | None = None,
    max_images: int = 6,
) -> dict:
    """Look up *sku* (and *alt_terms*) on PAI, download product images, and
    register them with the product so they appear in the Edit Product IMAGES
    section.

    PAI must be configured via env (``PAI_ENABLE=1`` + ``PAI_USER`` /
    ``PAI_PASS``).  If PAI is disabled or login fails, this function returns
    a no-op summary with an explanatory error rather than raising.

    Returns:
        {"searched": int, "found": int, "downloaded": int,
         "run_id": int, "errors": list[str]}
    """
    import os
    import re
    from io import BytesIO

    errors: list[str] = []

    if product_id is None:
        prod = get_product_by_sku(sku)
        if prod:
            product_id = prod["id"]

    run_id = create_scrape_run("images", "pai", notes=f"SKU: {sku}")

    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(0, 1, msg)

    if not product_id:
        errors.append("PAI images: no product_id resolved for SKU")
        update_scrape_run(run_id, status="failed", total_searched=0, total_found=0)
        return {"searched": 0, "found": 0, "downloaded": 0, "run_id": run_id, "errors": errors}

    # Build candidate part numbers in priority order: explicit alt_terms first
    # so caller-supplied vendor SKU / OEM are tried; then sku itself.
    candidates: list[str] = []
    for c in (alt_terms or []):
        c = (c or "").strip()
        if c and c not in candidates:
            candidates.append(c)
    if sku and sku.strip() and sku.strip() not in candidates:
        # Skip JAKS- internal SKU — PAI never indexes those
        if not sku.strip().upper().startswith("JAKS-"):
            candidates.append(sku.strip())
    if not candidates:
        errors.append("PAI images: no searchable part numbers")
        update_scrape_run(run_id, status="failed", total_searched=0, total_found=0)
        return {"searched": 0, "found": 0, "downloaded": 0, "run_id": run_id, "errors": errors}

    # Connect to PAI
    try:
        from sources.pai_builder import build_pai_client_for_run
        from sources.pai_client import remove_pai_watermark
    except Exception as exc:
        errors.append(f"PAI images: import failed: {exc}")
        update_scrape_run(run_id, status="failed", total_searched=0, total_found=0)
        return {"searched": 0, "found": 0, "downloaded": 0, "run_id": run_id, "errors": errors}

    _progress("Connecting to PAI portal…")
    client = build_pai_client_for_run(None)
    if client is None:
        errors.append("PAI images: PAI not configured (set PAI_ENABLE=1 + PAI_USER/PAI_PASS)")
        update_scrape_run(run_id, status="failed", total_searched=0, total_found=0)
        return {"searched": 0, "found": 0, "downloaded": 0, "run_id": run_id, "errors": errors}

    # Determine destination directory and existing image filenames
    images_dir = _resolve_product_images_dir(product_id)
    try:
        os.makedirs(images_dir, exist_ok=True)
    except Exception as exc:
        errors.append(f"PAI images: cannot create images dir: {exc}")
        update_scrape_run(run_id, status="failed", total_searched=0, total_found=0)
        return {"searched": 0, "found": 0, "downloaded": 0, "run_id": run_id, "errors": errors}

    searched = 0
    found_hits = 0
    downloaded = 0
    image_urls: list[str] = []
    matched_pn: str | None = None

    for pn in candidates:
        _progress(f"Searching PAI for {pn}…")
        searched += 1
        urls: list[str] = []
        try:
            result = client.lookup_cost(pn)
        except Exception as exc:
            errors.append(f"PAI lookup '{pn}': {exc}")
            logger.warning("PAI lookup_cost('%s') failed: %s", pn, exc)
            result = None
        if result:
            urls = list(result.get("image_urls") or [])
        # Fallback: lookup_cost returns None when the detail page has no
        # parseable cost (cost == 0 or missing). Try the images-only walk
        # so we still pick up product photos for free / no-quote items.
        if not urls:
            try:
                urls = list(client.lookup_images(pn) or [])
            except Exception as exc:
                errors.append(f"PAI images lookup '{pn}': {exc}")
                logger.warning("PAI lookup_images('%s') failed: %s", pn, exc)
                urls = []
        if urls:
            found_hits += 1
            image_urls = urls
            matched_pn = pn
            break

    if not image_urls:
        errors.append("PAI images: no product images returned")
        update_scrape_run(run_id, status="completed", total_searched=searched, total_found=0)
        return {"searched": searched, "found": 0, "downloaded": 0, "run_id": run_id, "errors": errors}

    # Download + validate + watermark-crop + save into product images folder.
    try:
        import requests
        from PIL import Image
    except Exception as exc:
        errors.append(f"PAI images: deps missing: {exc}")
        update_scrape_run(run_id, status="failed", total_searched=searched, total_found=0)
        return {"searched": searched, "found": found_hits, "downloaded": 0, "run_id": run_id, "errors": errors}

    session = getattr(client, "session", None)
    if not isinstance(session, requests.Session):
        session = requests.Session()

    handle = re.sub(r"[^A-Za-z0-9._-]+", "-", str(sku or matched_pn or "image"))
    # Position offset: existing images on this product
    try:
        existing = [f for f in os.listdir(images_dir) if not f.startswith(".")]
    except Exception:
        existing = []
    position_offset = len(existing)

    for i, img_url in enumerate(image_urls[:max_images], start=1):
        if not img_url:
            continue
        _progress(f"Downloading PAI image {i}/{min(len(image_urls), max_images)}…")
        try:
            resp = session.get(img_url, timeout=30)
            if resp.status_code != 200:
                continue
            data = resp.content
            if len(data) < 5000:
                continue
            try:
                with Image.open(BytesIO(data)) as img:
                    w, h = img.size
                    if min(w, h) < 300:
                        continue
            except Exception:
                continue
            # Remove watermark
            processed = remove_pai_watermark(data)
            if processed:
                data = processed
            # Pick unique filename
            base_name = f"pai_{handle}_{position_offset + i:02d}.jpg"
            dest_path = os.path.join(images_dir, base_name)
            n = 1
            while os.path.exists(dest_path):
                base_name = f"pai_{handle}_{position_offset + i:02d}_{n}.jpg"
                dest_path = os.path.join(images_dir, base_name)
                n += 1
            try:
                with Image.open(BytesIO(data)) as img:
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(dest_path, format="JPEG", quality=92, optimize=True)
                    width, height = img.size
            except Exception as exc:
                errors.append(f"PAI image save failed: {exc}")
                continue
            try:
                file_size = os.path.getsize(dest_path)
            except Exception:
                file_size = None
            try:
                from db import add_product_image
                add_product_image(
                    product_id,
                    filename=base_name,
                    path=dest_path,
                    position=position_offset + i,
                    width=width,
                    height=height,
                    file_size=file_size,
                    source="pai",
                )
            except Exception as exc:
                errors.append(f"PAI image DB register failed: {exc}")
                # Keep the file even if DB registration failed
            downloaded += 1
        except Exception as exc:
            errors.append(f"PAI image '{img_url[:60]}': {exc}")
            continue

    update_scrape_run(
        run_id,
        status="completed",
        total_searched=searched,
        total_found=downloaded,
    )
    return {
        "searched": searched,
        "found": downloaded,
        "downloaded": downloaded,
        "matched_part_number": matched_pn,
        "run_id": run_id,
        "errors": errors,
    }

