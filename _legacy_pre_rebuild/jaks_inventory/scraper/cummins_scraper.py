"""Cummins parts.cummins.com scraper — Playwright-based ESN + keyword search.

Flow:
  1. Navigate to parts.cummins.com ESN entry
  2. Submit ESN → establishes "Current ESN" session
  3. Search by keyword (e.g. "Overhaul Kit") within ESN context
  4. Parse result cards: part number, description, kit number, kit description, image URL
  5. Paginate through all result pages
  6. Optionally scrape part-details page for a specific part

Uses sync Playwright so it can run in a QThread worker.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Persistent browser profile so cookies/session survive between runs
_PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "cummins_session"

_BASE = "https://parts.cummins.com"


@dataclass
class CumminsPartResult:
    part_number: str = ""
    description: str = ""
    kit_number: str = ""
    kit_description: str = ""
    image_url: str = ""
    detail_url: str = ""


def search_cummins_esn(
    esn: str,
    keyword: str,
    *,
    headless: bool = True,
    on_status: Optional[Callable[[str], None]] = None,
    max_pages: int = 5,
) -> list[CumminsPartResult]:
    """Search parts.cummins.com for parts matching *keyword* under *esn*.

    Returns a list of CumminsPartResult.  Raises on hard failure.
    """
    from playwright.sync_api import sync_playwright

    results: list[CumminsPartResult] = []

    def _status(msg: str):
        logger.info(msg)
        if on_status:
            on_status(msg)

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    _status("Launching browser…")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            # ── Step 1: Navigate to ESN entry ──
            _status(f"Navigating to Cummins ESN entry…")
            page.goto(f"{_BASE}/esn-entry", wait_until="domcontentloaded", timeout=30_000)
            time.sleep(2)

            # Dismiss cookie banner if present
            _dismiss_cookies(page)

            # ── Step 2: Enter ESN ──
            _status(f"Entering ESN {esn}…")
            esn_input = page.locator(
                "input[placeholder*='Search ESN'], "
                "input[placeholder*='Search ESN/ATSN/GSN'], "
                "input[type='search'], "
                "input.search-input"
            ).first
            esn_input.click()
            esn_input.fill(esn)
            esn_input.press("Enter")

            # Wait for ESN to be accepted — look for "Current ESN" banner
            _status("Waiting for ESN confirmation…")
            try:
                page.wait_for_selector(
                    "text=Current ESN", timeout=15_000
                )
            except Exception:
                # Try alternative — the page URL might change
                page.wait_for_url("**/esn-entry/main/**", timeout=10_000)

            time.sleep(1)
            _dismiss_cookies(page)

            # ── Step 3: Search by keyword within ESN context ──
            _status(f"Searching for '{keyword}'…")

            # After ESN is accepted the input[name='esn'] becomes the
            # keyword search ("Search <ESN> For:").  Clear it and type
            # the keyword, then click the red search button.
            search_input = page.locator("input[name='esn']:visible").first
            search_input.click()
            search_input.fill("")
            time.sleep(0.3)
            search_input.fill(keyword)
            time.sleep(0.3)

            # Click the red search button (btn-danger with fa-search)
            search_btn = page.locator("button.btn-danger").first
            if search_btn.count() and search_btn.is_visible():
                search_btn.click()
            else:
                search_input.press("Enter")

            # Wait for results to load
            _status("Waiting for results…")
            try:
                page.wait_for_selector(
                    "text=Showing Search Results, "
                    "text=No Information available",
                    timeout=20_000,
                )
            except Exception:
                pass
            time.sleep(2)

            # Dismiss "Too Many Results Found" popup
            try:
                ok_btn = page.locator("button:has-text('OK')").first
                if ok_btn.count() and ok_btn.is_visible():
                    ok_btn.click()
                    time.sleep(0.5)
            except Exception:
                pass

            _dismiss_cookies(page)

            # Check for "No Information available"
            no_info = page.locator("text=No Information available").count()
            if no_info > 0:
                _status("No results found on Cummins.")
                return []

            # ── Step 4: Parse result cards ──
            _status("Parsing results…")
            results = _parse_result_page(page)

            _status(f"Found {len(results)} part(s) on Cummins.")

        finally:
            context.close()

    return results


def scrape_part_details(
    part_number: str,
    esn: str = "",
    *,
    headless: bool = True,
    on_status: Optional[Callable[[str], None]] = None,
) -> dict:
    """Scrape the detail page for a specific Cummins part number.

    Returns dict with keys: part_number, description, kit_number,
    kit_description, image_url, superseded_by, service_kits, etc.
    """
    from playwright.sync_api import sync_playwright

    def _status(msg: str):
        logger.info(msg)
        if on_status:
            on_status(msg)

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    detail = {"part_number": part_number}

    _status(f"Fetching details for {part_number}…")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            url = f"{_BASE}/esn-entry/main/search/inner/part-details/{part_number}/esn"
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(3)
            _dismiss_cookies(page)

            # Parse detail page
            body_text = page.inner_text("body")

            # Extract description
            desc_el = page.locator(".part-description, .product-title, h2").first
            if desc_el.count():
                detail["description"] = desc_el.inner_text().strip()

            # Extract image
            img_el = page.locator(
                ".part-image img, .product-image img, img[alt*='part']"
            ).first
            if img_el.count():
                detail["image_url"] = img_el.get_attribute("src") or ""

            # Look for supersession info
            if "superseded" in body_text.lower():
                sup_el = page.locator("text=Superseded").locator("..").locator("a, span")
                if sup_el.count():
                    detail["superseded_by"] = sup_el.first.inner_text().strip()

            # Look for service kits
            kit_els = page.locator(".kit-info, .service-kit, [class*='kit']")
            if kit_els.count():
                detail["service_kits"] = kit_els.all_inner_texts()

            _status(f"Got details for {part_number}")

        finally:
            context.close()

    return detail


# ── Helpers ──────────────────────────────────────────────────────────


def _dismiss_cookies(page):
    """Click any cookie consent / accept buttons."""
    try:
        accept_btn = page.locator(
            "button:has-text('Accept'), "
            "a:has-text('Accept'), "
            "button:has-text('Accept & Close')"
        ).first
        if accept_btn.count() and accept_btn.is_visible():
            accept_btn.click()
            time.sleep(0.5)
    except Exception:
        pass


def _parse_result_page(page) -> list[CumminsPartResult]:
    """Parse the visible search results on the current page.

    Cummins renders each part as a card (div.col-md-3.col-sm-6)
    containing a part-number link in red, a description line, and
    optionally an image.
    """
    results = []

    # Each result card is a col-md-3 col-sm-6 with an ng-star-inserted
    cards = page.locator("div.col-md-3.col-sm-6.ng-star-inserted").all()

    if not cards:
        # Fallback: extract from body text using regex
        return _parse_results_from_text(page)

    seen = set()
    for card in cards:
        try:
            text = card.inner_text().strip()
            if not text or len(text) < 3:
                continue

            # Part number is typically the first line (a link element)
            link = card.locator("a").first
            part_number = ""
            detail_url = ""
            if link.count():
                part_number = link.inner_text().strip()
                href = link.get_attribute("href") or ""
                if href:
                    detail_url = href if href.startswith("http") else f"{_BASE}{href}"

            if not part_number:
                # Try first line of text
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if lines:
                    part_number = lines[0]

            if not part_number or part_number in seen:
                continue
            seen.add(part_number)

            # Description is the text after the part number
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            description = ""
            for line in lines:
                if line != part_number and "IMAGE NOT" not in line.upper():
                    description = line
                    break

            # Try to get image
            image_url = ""
            img = card.locator("img").first
            if img.count():
                src = img.get_attribute("src") or ""
                if src and "not" not in src.lower() and "placeholder" not in src.lower():
                    image_url = src if src.startswith("http") else f"{_BASE}{src}"

            r = CumminsPartResult(
                part_number=part_number,
                description=description,
                detail_url=detail_url,
                image_url=image_url,
            )
            results.append(r)
        except Exception:
            continue

    return results


def _parse_results_from_text(page) -> list[CumminsPartResult]:
    """Fallback parser — extract part info from page body text."""
    import re

    results = []
    body = page.inner_text("body")

    # Pattern: "Part Number: XXXX"
    pn_matches = re.findall(
        r"Part\s+Number:\s*(\S+)", body, re.IGNORECASE
    )
    desc_matches = re.findall(
        r"Part\s+Description:\s*([^\n]+)", body, re.IGNORECASE
    )
    kit_num_matches = re.findall(
        r"Kit\s+Number:\s*(\S+)", body, re.IGNORECASE
    )
    kit_desc_matches = re.findall(
        r"Kit\s+Description:\s*([^\n]+)", body, re.IGNORECASE
    )

    for i, pn in enumerate(pn_matches):
        r = CumminsPartResult(part_number=pn.strip())
        if i < len(desc_matches):
            r.description = desc_matches[i].strip()
        if i < len(kit_num_matches):
            r.kit_number = kit_num_matches[i].strip()
        if i < len(kit_desc_matches):
            r.kit_description = kit_desc_matches[i].strip()
        r.detail_url = f"{_BASE}/esn-entry/main/search/inner/part-details/{r.part_number}/esn"
        results.append(r)

    # Deduplicate by part number
    seen = set()
    deduped = []
    for r in results:
        if r.part_number not in seen:
            seen.add(r.part_number)
            deduped.append(r)

    return deduped


def _extract_part_from_text(text: str) -> CumminsPartResult:
    """Extract part info from a card's inner text."""
    import re

    r = CumminsPartResult()

    pn = re.search(r"Part\s+Number:\s*(\S+)", text, re.IGNORECASE)
    if pn:
        r.part_number = pn.group(1).strip()

    desc = re.search(r"Part\s+Description:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if desc:
        r.description = desc.group(1).strip()

    kn = re.search(r"Kit\s+Number:\s*(\S+)", text, re.IGNORECASE)
    if kn:
        r.kit_number = kn.group(1).strip()

    kd = re.search(r"Kit\s+Description:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if kd:
        r.kit_description = kd.group(1).strip()

    if r.part_number:
        r.detail_url = f"{_BASE}/esn-entry/main/search/inner/part-details/{r.part_number}/esn"

    return r
