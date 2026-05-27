"""FleetPride competitor pricing scraper.

FleetPride is one of the largest independent heavy-duty truck parts
distributors. Their website (fleetpride.com) allows searching by part number.
Search is powered by Coveo (JS-rendered), so we use Playwright.

URL: https://www.fleetpride.com/
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.fleetpride.com"
SEARCH_URL = "https://www.fleetpride.com/gs/%40uri#q={query}"
SEARCH_TIMEOUT = 25_000  # ms to wait for Coveo results


@dataclass
class FleetPrideProduct:
    """Product data from FleetPride."""
    part_number: str
    title: str
    manufacturer: Optional[str] = None
    price: Optional[float] = None
    oem_refs: list[str] = field(default_factory=list)
    url: Optional[str] = None
    in_stock: bool = False
    availability: Optional[str] = None
    category: Optional[str] = None


@dataclass
class FleetPrideSearchResult:
    """Results from FleetPride search."""
    query: str
    products: list[FleetPrideProduct] = field(default_factory=list)
    total_results: int = 0
    error: Optional[str] = None


class FleetPrideScraper:
    """Scraper for FleetPride website (Playwright-based)."""

    def __init__(self):
        self._pw = None
        self._browser = None

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)

    def search(self, query: str) -> FleetPrideSearchResult:
        """Search FleetPride for a part number using Playwright."""
        result = FleetPrideSearchResult(query=query)

        try:
            self._ensure_browser()
            page = self._browser.new_page()
            try:
                search_url = SEARCH_URL.format(query=quote_plus(query))
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

                # Wait for Coveo to render search results
                try:
                    page.wait_for_selector(".product-title", timeout=SEARCH_TIMEOUT)
                except Exception:
                    logger.debug("FleetPride: no .product-title appeared for '%s'", query)
                    return result

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                result.products = self._parse_search_results(soup, query)
                result.total_results = len(result.products)
                logger.info("FleetPride found %d results for '%s'", result.total_results, query)
            finally:
                page.close()

        except Exception as e:
            result.error = f"FleetPride search error: {e}"
            logger.warning("FleetPride search failed for '%s': %s", query, e)

        return result

    def _parse_search_results(
        self, soup: BeautifulSoup, query: str
    ) -> list[FleetPrideProduct]:
        """Parse rendered Coveo search results from BeautifulSoup."""
        products: list[FleetPrideProduct] = []
        seen_urls: set[str] = set()

        # .product-title links are actual search results (not recommendations)
        title_links = soup.select(".product-title a[href*='/parts/']")

        for link in title_links:
            href = link.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)

            title = link.get_text(strip=True) or ""
            url = href if href.startswith("http") else BASE_URL + href

            # Extract part number from URL slug
            # e.g. /parts/afa-industries-engine-parts-2486744 → 2486744
            # e.g. /parts/interstate-mcbee-engine-parts-m2486744 → M2486744
            slug = href.rsplit("/", 1)[-1] if "/" in href else href
            part_number = slug.rsplit("-", 1)[-1].upper() if slug else ""

            # Walk up DOM to find card container and extract Part #, Brand
            brand = None
            card = link
            for _ in range(15):
                if card.parent is None:
                    break
                card = card.parent
                card_text = card.get_text(" ", strip=True)

                # Check for actual Part # (digits/letters, not "Part # Match")
                pn_match = re.search(
                    r'Part\s*#\s*([A-Z0-9][A-Z0-9-]{2,})', card_text, re.I
                )
                brand_match = re.search(
                    r'Brand:\s*([A-Z][A-Z0-9 -]+?)(?:\s+Part|\s+$|\s*\|)', card_text, re.I
                )
                if pn_match:
                    part_number = pn_match.group(1).upper()
                if brand_match:
                    brand = brand_match.group(1).strip()
                if pn_match or brand_match:
                    break

            if not part_number:
                continue

            products.append(FleetPrideProduct(
                part_number=part_number,
                title=title,
                manufacturer=brand,
                url=url,
            ))
            logger.debug("FleetPride result: %s (%s) — %s", part_number, brand, title[:50])

        return products

    def get_product_detail(self, url: str) -> list[str]:
        """Navigate to a FleetPride product page and extract cross-references.

        Returns a list of cross-reference part numbers found on the page.
        """
        xrefs: list[str] = []
        try:
            self._ensure_browser()
            page = self._browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait for product content to render
                try:
                    page.wait_for_selector(
                        ".product-detail, [class*='product'], [class*='Part']",
                        timeout=15000,
                    )
                except Exception:
                    pass

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text(" ", strip=True)

                # Look for cross-reference / interchange / replaces sections
                for pattern in (
                    r"(?:Cross[\s-]*Ref(?:erence)?s?|Interchange|Replaces?|OEM)\s*[:#]?\s*([\dA-Z,\s\-]{5,})",
                ):
                    for m in re.finditer(pattern, text, re.IGNORECASE):
                        nums = re.findall(
                            r"\b([A-Z0-9]{4,}(?:-[A-Z0-9]+)*)\b", m.group(1).upper()
                        )
                        for n in nums:
                            if any(c.isdigit() for c in n) and n not in xrefs:
                                xrefs.append(n)

                if xrefs:
                    logger.info("FleetPride product page xrefs from %s: %s", url, xrefs)
            finally:
                page.close()
        except Exception as e:
            logger.warning("FleetPride product detail failed for %s: %s", url, e)

        return xrefs

    def close(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
