"""Highway and Heavy Parts (HHP) competitor pricing scraper.

HHP (highwayandheavyparts.com) uses Advanced Woo Search Pro with a
WordPress AJAX endpoint that returns structured JSON.

URL: https://www.highwayandheavyparts.com/
"""

from __future__ import annotations

import re
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE_URL = "https://www.highwayandheavyparts.com"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"


@dataclass
class HHPProduct:
    """Product data from Highway and Heavy Parts."""
    part_number: str
    title: str
    manufacturer: Optional[str] = None
    price: Optional[float] = None
    oem_refs: list[str] = field(default_factory=list)
    aftermarket_refs: list[str] = field(default_factory=list)
    url: Optional[str] = None
    in_stock: bool = False
    availability: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    category: Optional[str] = None


@dataclass
class HHPSearchResult:
    """Results from HHP search."""
    query: str
    products: list[HHPProduct] = field(default_factory=list)
    total_results: int = 0
    error: Optional[str] = None


class HHPScraper:
    """Scraper for Highway and Heavy Parts website."""

    def __init__(self):
        self.client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.5",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        self._last_request_time = 0.0

    def _respect_rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()

    def search(self, query: str) -> HHPSearchResult:
        """Search HHP via AJAX first, then fall back to WP HTML search."""
        result = self._search_ajax(query)

        if not result.products:
            logger.info(
                "HHP AJAX returned 0 results for '%s', trying WP HTML fallback", query
            )
            fallback = self._search_wp_html(query)
            if fallback.products:
                logger.info(
                    "HHP WP HTML fallback found %d results for '%s'",
                    len(fallback.products), query,
                )
                result = fallback
            else:
                # Both empty – return original (preserves any AJAX error info)
                if not result.error:
                    result.error = "No results from AJAX or HTML search"
                logger.warning(
                    "HHP: both AJAX and HTML search returned 0 results for '%s'", query
                )

        # Anti-false-negative: if products found but none have prices,
        # try fetching detail pages for the first 3 to recover pricing.
        priceless = [p for p in result.products if p.price is None and p.url]
        if priceless and not any(p.price for p in result.products):
            logger.info(
                "HHP: %d products found but no prices — fetching detail pages",
                len(priceless),
            )
            for prod in priceless[:3]:
                detail = self.get_product_detail(prod.url)
                if detail and detail.price is not None and detail.price > 0:
                    prod.price = detail.price
                    prod.oem_refs = detail.oem_refs or prod.oem_refs
                    logger.info(
                        "HHP: recovered price $%.2f from detail page for '%s'",
                        detail.price, prod.part_number,
                    )

        return result

    def _search_ajax(self, query: str) -> HHPSearchResult:
        """Search HHP via Advanced Woo Search AJAX API."""
        self._respect_rate_limit()
        result = HHPSearchResult(query=query)

        try:
            response = self.client.get(
                AJAX_URL, params={"action": "aws_action", "keyword": query}
            )

            if response.status_code >= 500:
                result.error = f"HTTP {response.status_code}"
                logger.warning("HHP AJAX error for '%s': %s", query, response.status_code)
                return result

            if response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
                return result

            data = response.json()
            products_data = data.get("products", [])

            for item in products_data[:20]:
                prod = self._parse_ajax_product(item)
                if prod:
                    result.products.append(prod)

            result.total_results = len(result.products)
            logger.info("HHP AJAX found %d results for '%s'", result.total_results, query)

        except httpx.HTTPError as e:
            result.error = f"HTTP error: {e}"
            logger.warning("HHP AJAX search failed for '%s': %s", query, e)
        except Exception as e:
            result.error = f"Parse error: {e}"
            logger.warning("HHP AJAX parse failed for '%s': %s", query, e)

        return result

    def _search_wp_html(self, query: str) -> HHPSearchResult:
        """Fallback: scrape the standard WP product search results page."""
        self._respect_rate_limit()
        result = HHPSearchResult(query=query)

        try:
            response = self.client.get(
                BASE_URL,
                params={"s": query, "post_type": "product"},
                headers={
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                    "X-Requested-With": "",  # override XHR header
                },
            )

            if response.status_code != 200:
                result.error = f"HTML search HTTP {response.status_code}"
                return result

            soup = BeautifulSoup(response.text, "html.parser")
            product_cards = soup.select("li.product")

            for card in product_cards[:20]:
                prod = self._parse_html_product(card)
                if prod:
                    result.products.append(prod)

            result.total_results = len(result.products)

        except httpx.HTTPError as e:
            result.error = f"HTML search HTTP error: {e}"
            logger.warning("HHP HTML search failed for '%s': %s", query, e)
        except Exception as e:
            result.error = f"HTML search parse error: {e}"
            logger.warning("HHP HTML search parse failed for '%s': %s", query, e)

        return result

    def _parse_html_product(self, card) -> Optional[HHPProduct]:
        """Parse a single <li class='product'> card from WP search results."""
        # URL + title
        link_el = card.select_one("a.woocommerce-LoopProduct-link")
        url = link_el["href"] if link_el and link_el.get("href") else ""

        h2 = card.select_one("h2.woocommerce-loop-product__title")
        title = h2.get_text(strip=True) if h2 else ""
        if not title:
            return None

        part_number = self._extract_part_number(url, title)

        # Price
        price = None
        price_el = card.select_one(".woocommerce-Price-amount bdi")
        if price_el:
            pm = re.search(r"[\d,]+\.?\d*", price_el.get_text())
            if pm:
                try:
                    price = float(pm.group().replace(",", ""))
                except ValueError:
                    pass

        # Stock status from CSS class
        in_stock = "instock" in card.get("class", [])

        # Image
        image_url = None
        img = card.select_one("img")
        if img:
            image_url = img.get("src", "")

        return HHPProduct(
            part_number=part_number,
            title=title,
            price=price,
            url=url,
            in_stock=in_stock,
            image_url=image_url,
        )

    def _parse_ajax_product(self, item: dict) -> Optional[HHPProduct]:
        """Parse a product from the AWS AJAX JSON response."""
        title_html = item.get("title", "")
        # Strip HTML tags from title
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        if not title:
            return None

        url = item.get("link", "")

        # Extract part number from URL slug or title
        part_number = self._extract_part_number(url, title)

        # Price: item has 'f_price' (float) and 'price' (HTML)
        price = None
        f_price = item.get("f_price")
        if f_price is not None:
            try:
                price = float(f_price)
                if price <= 0:
                    price = None
            except (ValueError, TypeError):
                pass

        # Fallback: parse price from HTML
        if price is None and item.get("price"):
            price_match = re.search(r"[\d,]+\.?\d*", re.sub(r"<[^>]+>", "", item["price"]))
            if price_match:
                try:
                    price = float(price_match.group().replace(",", ""))
                except ValueError:
                    pass

        # Stock status
        in_stock = bool(item.get("f_stock"))

        # Image
        image_url = item.get("image")

        return HHPProduct(
            part_number=part_number,
            title=title,
            price=price,
            url=url,
            in_stock=in_stock,
            image_url=image_url,
        )

    @staticmethod
    def _extract_part_number(url: str, title: str) -> str:
        """Extract a part number from HHP URL or title."""
        # HHP URLs: /product/mcif3466615-caterpillar-c15-... → "MCIF3466615"
        if url:
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            first_token = slug.split("-")[0].upper()
            # Must contain at least one digit to be a part number
            if first_token and len(first_token) >= 4 and any(c.isdigit() for c in first_token):
                return first_token

        # Try title patterns: "Title C15107010 | ..." or "2486744 | Title"
        # Must contain at least one digit to be a part number
        for m in re.finditer(r"\b([A-Z0-9]{5,}(?:-[A-Z0-9]+)*)\b", title.upper()):
            candidate = m.group(1)
            if any(c.isdigit() for c in candidate):
                return candidate
        return title[:30]

    def get_product_detail(self, url: str) -> Optional[HHPProduct]:
        """Fetch an HHP product page and extract cross-references."""
        self._respect_rate_limit()
        try:
            # Override session AJAX/XHR headers — product detail pages are HTML.
            # WordPress can serve different content when X-Requested-With=XMLHttpRequest
            # and Accept=application/json, which makes the price selector miss.
            resp = self.client.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "X-Requested-With": "",
                },
            )
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, "html.parser")

            # Title / part number from h1
            title = ""
            h1 = soup.select_one("h1")
            if h1:
                title = h1.get_text(strip=True)
            part_number = self._extract_part_number(url, title)

            # Price — try several selectors to tolerate theme variation
            price = None
            price_el = soup.select_one(
                ".summary .woocommerce-Price-amount bdi, "
                ".woocommerce-Price-amount bdi, "
                "p.price .amount, "
                ".summary .price, "
                ".price .amount"
            )
            if price_el:
                pm = re.search(r"[\d,]+\.?\d*", price_el.get_text())
                if pm:
                    try:
                        price = float(pm.group().replace(",", ""))
                    except ValueError:
                        pass
            # Last-resort fallback: og:price:amount meta tag
            if price is None:
                meta = soup.select_one('meta[property="og:price:amount"], meta[property="product:price:amount"]')
                if meta and meta.get("content"):
                    try:
                        price = float(str(meta.get("content")).replace(",", "").strip())
                    except ValueError:
                        pass

            # Cross-references from custom tab
            oem_refs: list[str] = []
            xref_tab = soup.select_one("#tab-hhp_product_custom_tab")
            if xref_tab:
                for p_elem in xref_tab.select("p"):
                    nums = re.findall(
                        r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]+)*\b", p_elem.get_text()
                    )
                    oem_refs.extend(nums)

            return HHPProduct(
                part_number=part_number,
                title=title,
                price=price,
                url=url,
                oem_refs=oem_refs,
            )
        except Exception as e:
            logger.warning("HHP detail fetch failed for %s: %s", url, e)
            return None

    def close(self):
        self.client.close()
