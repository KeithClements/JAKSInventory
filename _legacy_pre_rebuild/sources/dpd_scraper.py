"""Diesel Parts Direct scraper for manufacturer verification.

DPD is a secondary verification source with 0.3 weight in the confidence scoring.
Useful for:
- Cross-validating PAI manufacturer identification
- Finding McBee, AFA, and other aftermarket options
- Getting OEM cross-references

URL: https://www.dieselpartsdirect.com/
"""

from __future__ import annotations
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from engine.verification_pipeline import VerificationResult, VerificationSource
from utils.part_normalizer import normalize_part_number


logger = logging.getLogger(__name__)

# Respectful scraping settings
REQUEST_DELAY = 1.5  # seconds between requests
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE_URL = "https://www.dieselpartsdirect.com"


@dataclass
class DPDProduct:
    """Product data from Diesel Parts Direct."""
    part_number: str
    title: str
    manufacturer: Optional[str] = None
    price: Optional[float] = None
    oem_refs: list[str] = field(default_factory=list)
    aftermarket_refs: list[str] = field(default_factory=list)
    url: Optional[str] = None
    in_stock: bool = False


@dataclass
class DPDSearchResult:
    """Results from DPD search."""
    query: str
    products: list[DPDProduct] = field(default_factory=list)
    total_results: int = 0
    error: Optional[str] = None


class DPDScraper:
    """Scraper for Diesel Parts Direct website."""
    
    def __init__(self):
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
        self._last_request_time = 0
    
    def _respect_rate_limit(self):
        """Ensure respectful scraping delays."""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()
    
    def search(self, query: str) -> DPDSearchResult:
        """Search DPD for a part number.
        
        Args:
            query: Part number or search term
        
        Returns:
            DPDSearchResult with matching products
        """
        self._respect_rate_limit()
        
        result = DPDSearchResult(query=query)
        
        try:
            # DPD search URL pattern
            search_url = f"{BASE_URL}/search?q={quote_plus(query)}"
            
            response = self.client.get(search_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Parse search results
            result.products = self._parse_search_results(soup)
            result.total_results = len(result.products)
            
        except httpx.HTTPError as e:
            result.error = f"HTTP error: {e}"
            logger.warning(f"DPD search failed for '{query}': {e}")
        except Exception as e:
            result.error = f"Parse error: {e}"
            logger.warning(f"DPD parse failed for '{query}': {e}")
        
        return result
    
    def _parse_search_results(self, soup: BeautifulSoup) -> list[DPDProduct]:
        """Parse search results page."""
        products = []
        
        # Try multiple possible selectors for product cards
        # DPD may use different layouts
        product_cards = (
            soup.select(".product-card") or
            soup.select(".product-item") or
            soup.select("[data-product-id]") or
            soup.select(".search-result-item")
        )
        
        for card in product_cards[:20]:  # Limit to first 20
            try:
                product = self._parse_product_card(card)
                if product:
                    products.append(product)
            except Exception as e:
                logger.debug(f"Failed to parse product card: {e}")
                continue
        
        return products
    
    def _parse_product_card(self, card: BeautifulSoup) -> Optional[DPDProduct]:
        """Parse a single product card."""
        # Extract part number
        part_elem = (
            card.select_one(".part-number") or
            card.select_one("[data-part-number]") or
            card.select_one(".sku")
        )
        
        if not part_elem:
            return None
        
        part_number = part_elem.get_text(strip=True)
        if part_elem.has_attr("data-part-number"):
            part_number = part_elem["data-part-number"]
        
        # Clean up part number
        part_number = re.sub(r"^(Part|SKU|#):?\s*", "", part_number, flags=re.I)
        
        if not part_number:
            return None
        
        # Extract title
        title_elem = (
            card.select_one(".product-title") or
            card.select_one("h2") or
            card.select_one("h3") or
            card.select_one(".title")
        )
        title = title_elem.get_text(strip=True) if title_elem else part_number
        
        # Extract manufacturer
        mfr_elem = (
            card.select_one(".manufacturer") or
            card.select_one(".brand") or
            card.select_one("[data-manufacturer]")
        )
        manufacturer = None
        if mfr_elem:
            manufacturer = mfr_elem.get_text(strip=True)
            if mfr_elem.has_attr("data-manufacturer"):
                manufacturer = mfr_elem["data-manufacturer"]
        
        # Try to detect manufacturer from title if not explicit
        if not manufacturer:
            manufacturer = self._detect_manufacturer_from_text(title)
        
        # Extract price
        price = None
        price_elem = (
            card.select_one(".price") or
            card.select_one("[data-price]") or
            card.select_one(".product-price")
        )
        if price_elem:
            price_text = price_elem.get_text(strip=True)
            price_match = re.search(r"\$?([\d,]+\.?\d*)", price_text)
            if price_match:
                price = float(price_match.group(1).replace(",", ""))
        
        # Extract URL
        url = None
        link_elem = card.select_one("a[href]")
        if link_elem:
            url = link_elem["href"]
            if not url.startswith("http"):
                url = f"{BASE_URL}{url}"
        
        # Check stock status
        in_stock = False
        stock_elem = card.select_one(".stock-status, .availability")
        if stock_elem:
            stock_text = stock_elem.get_text(strip=True).lower()
            in_stock = "in stock" in stock_text or "available" in stock_text
        
        return DPDProduct(
            part_number=part_number,
            title=title,
            manufacturer=manufacturer,
            price=price,
            url=url,
            in_stock=in_stock,
        )
    
    def _detect_manufacturer_from_text(self, text: str) -> Optional[str]:
        """Detect manufacturer from product title/description."""
        text_upper = text.upper()
        
        manufacturers = {
            "PAI": ["PAI ", "PAI-", "P.A.I", "PAI INDUSTRIES"],
            "Interstate McBee": ["MCBEE", "INTERSTATE", "MCB-"],
            "AFA": ["AFA ", "AFA-", "AFA INDUSTRIES"],
            "IPD": ["IPD ", "IPD-"],
            "Reliance": ["RELIANCE"],
            "FP Diesel": ["FP DIESEL", "FP-"],
            "Cummins": ["CUMMINS", "CUM-"],
            "Caterpillar": ["CATERPILLAR", "CAT ", "CAT-"],
            "Detroit Diesel": ["DETROIT", "DD13", "DD15", "SERIES 60"],
        }
        
        for mfr, patterns in manufacturers.items():
            for pattern in patterns:
                if pattern in text_upper:
                    return mfr
        
        return None
    
    def get_product_detail(self, url: str) -> Optional[DPDProduct]:
        """Get detailed product info from product page.
        
        This provides more detail than search results, including
        cross-references and alternates.
        """
        self._respect_rate_limit()
        
        try:
            response = self.client.get(url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            return self._parse_product_detail(soup, url)
            
        except Exception as e:
            logger.warning(f"DPD product detail failed for '{url}': {e}")
            return None
    
    def _parse_product_detail(self, soup: BeautifulSoup, url: str) -> Optional[DPDProduct]:
        """Parse product detail page for cross-references."""
        # Get basic info
        title_elem = soup.select_one("h1, .product-title")
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"
        
        part_elem = soup.select_one(".part-number, .sku, [data-part-number]")
        part_number = part_elem.get_text(strip=True) if part_elem else ""
        
        product = DPDProduct(
            part_number=part_number,
            title=title,
            url=url,
        )
        
        # Look for cross-references section
        xref_section = soup.select_one(".cross-references, .xrefs, .alternates")
        if xref_section:
            # OEM references
            oem_refs = xref_section.select(".oem-ref, .oem-number")
            for ref in oem_refs:
                product.oem_refs.append(ref.get_text(strip=True))
            
            # Aftermarket references
            am_refs = xref_section.select(".aftermarket-ref, .alternate-number")
            for ref in am_refs:
                product.aftermarket_refs.append(ref.get_text(strip=True))
        
        # Fallback: look for "Replaces" text
        desc = soup.select_one(".description, .product-description")
        if desc:
            desc_text = desc.get_text()
            
            # Pattern: "Replaces OEM 1234567, 2345678"
            oem_match = re.search(r"Replaces?\s+(?:OEM\s+)?([\d\-,\s]+)", desc_text, re.I)
            if oem_match:
                nums = re.findall(r"\d{6,8}", oem_match.group(1))
                product.oem_refs.extend(nums)
        
        return product
    
    def close(self):
        """Close the HTTP client."""
        self.client.close()


def verify_from_dpd(part_number: str) -> VerificationResult:
    """Verify manufacturer using DPD lookup.
    
    This is the main integration point with the verification pipeline.
    Returns VerificationResult with DPD_SCRAPE weight (0.3).
    
    Args:
        part_number: Part number to look up
    
    Returns:
        VerificationResult with manufacturer info if found
    """
    result = VerificationResult(source=VerificationSource.DPD_SCRAPE)
    
    scraper = DPDScraper()
    try:
        # Normalize for search
        normalized = normalize_part_number(part_number)
        
        # Search DPD
        search_result = scraper.search(part_number)
        
        if search_result.error:
            result.error = search_result.error
            return result
        
        if not search_result.products:
            # No results - try normalized version
            search_result = scraper.search(normalized)
        
        if not search_result.products:
            result.error = "No products found"
            return result
        
        # Find best match
        best_match = None
        for product in search_result.products:
            product_normalized = normalize_part_number(product.part_number)
            if product_normalized == normalized:
                best_match = product
                break
        
        # If no exact match, use first result with caution
        if not best_match and search_result.products:
            best_match = search_result.products[0]
            result.confidence = 0.15  # Lower confidence for non-exact
        else:
            result.confidence = VerificationSource.DPD_SCRAPE.weight
        
        if best_match and best_match.manufacturer:
            # Map to standard codes
            mfr_map = {
                "PAI": ("PAI", "PAI Industries", "aftermarket"),
                "Interstate McBee": ("MCBEE", "Interstate McBee", "aftermarket"),
                "McBee": ("MCBEE", "Interstate McBee", "aftermarket"),
                "AFA": ("AFA", "AFA Industries", "aftermarket"),
                "IPD": ("IPD", "IPD Parts", "aftermarket"),
                "Reliance": ("RELIANCE", "Reliance Power Parts", "aftermarket"),
                "Cummins": ("CUMMINS", "Cummins Inc.", "oem"),
                "Caterpillar": ("CAT", "Caterpillar Inc.", "oem"),
                "Detroit Diesel": ("DETROIT", "Detroit Diesel", "oem"),
            }
            
            mfr_key = best_match.manufacturer
            if mfr_key in mfr_map:
                code, name, mfr_type = mfr_map[mfr_key]
                result.manufacturer_code = code
                result.manufacturer_name = name
                result.manufacturer_type = mfr_type
            else:
                result.manufacturer_code = best_match.manufacturer.upper()[:6]
                result.manufacturer_name = best_match.manufacturer
                result.manufacturer_type = "unknown"
            
            result.raw_data = {
                "title": best_match.title,
                "price": best_match.price,
                "url": best_match.url,
                "in_stock": best_match.in_stock,
            }
        
    except Exception as e:
        result.error = str(e)
        logger.exception(f"DPD verification failed for '{part_number}'")
    finally:
        scraper.close()
    
    return result


def search_dpd_xrefs(oem_part: str) -> list[DPDProduct]:
    """Find all aftermarket options on DPD for an OEM part.
    
    Useful for building cross-reference chains.
    
    Args:
        oem_part: OEM part number to search for
    
    Returns:
        List of DPDProduct with aftermarket alternatives
    """
    scraper = DPDScraper()
    try:
        result = scraper.search(oem_part)
        
        if result.error:
            logger.warning(f"DPD xref search failed: {result.error}")
            return []
        
        return result.products
        
    finally:
        scraper.close()


def batch_verify_dpd(part_numbers: list[str]) -> dict[str, VerificationResult]:
    """Batch verify multiple part numbers via DPD.
    
    Respects rate limits between requests.
    
    Args:
        part_numbers: List of part numbers to verify
    
    Returns:
        Dict mapping part number to VerificationResult
    """
    results = {}
    
    for pn in part_numbers:
        results[pn] = verify_from_dpd(pn)
    
    return results
