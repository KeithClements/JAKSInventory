"""ATL Diesel enrichment scraper.

Searches atldiesel.com (Shopify store) for matching part numbers and extracts:
- Competitor pricing (regular + sale)
- Product images
- Vendor info (PAI vs ATL Diesel branded)
- Stock availability
- Frequently Bought Together recommendations
- Related Products (You May Also Like)
"""

from __future__ import annotations

import re
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from models import Product

logger = logging.getLogger(__name__)

# ATL Diesel is a Shopify store front. A bare User-Agent gets 403'd by their
# bot-mitigation layer — send a full browser-like header set. ``br`` is
# deliberately excluded from Accept-Encoding because aiohttp doesn't decode
# brotli without an optional dependency.
ATL_BASE_URL = "https://atldiesel.com"
ATL_SEARCH_URL = "https://atldiesel.com/search?q={query}"
ATL_RATE_LIMIT = 1.5  # seconds between requests (conservative)
ATL_TIMEOUT = 30
ATL_MAX_IMAGES = 10

ATL_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class ATLRelatedProduct:
    """A related product from ATL Diesel."""
    sku: str | None = None
    title: str | None = None
    price: float | None = None
    url: str | None = None


@dataclass
class ATLDieselResult:
    """Result from ATL Diesel search."""
    search_query: str
    atl_sku: str | None = None
    atl_price: float | None = None
    atl_sale_price: float | None = None  # If on sale
    atl_compare_price: float | None = None  # Original price if on sale
    atl_vendor: str | None = None  # "PAI" or "ATL Diesel"
    atl_stock: str | None = None  # "In Stock", "Out of Stock", etc.
    atl_url: str | None = None
    atl_title: str | None = None
    image_urls: list[str] = field(default_factory=list)
    frequently_bought: list[ATLRelatedProduct] = field(default_factory=list)
    related_products: list[ATLRelatedProduct] = field(default_factory=list)
    found: bool = False
    error: str | None = None
    # Cross-reference tracking: did we find via alternate SKU?
    matched_query: str | None = None  # The actual query that found the product
    is_cross_reference: bool = False  # True if found via alternate SKU (not primary)
    # All search result products (for xref scraping — includes non-exact matches)
    all_search_results: list[ATLRelatedProduct] = field(default_factory=list)
    # Cross-reference part numbers from the product page "Cross-Referenced Parts" tab
    cross_references: list[str] = field(default_factory=list)


def _generate_dash_variants(pn: str) -> list[str]:
    """Generate dash insertion patterns for a part number without dashes.
    
    PAI part number patterns:
    - C-prefix (CAT): C15607010HP → C15607-010HP (dash 3 from end, before suffix)
    - ISX-prefix (Cummins): ISX108945 → ISX-108945 (dash after ISX)
    - ISX inframe kits: Often ISX-108XXX pattern
    
    Strategy:
    1. For C/ISX prefixes: try dash 3 from end first, then 5 from end
    2. For pure digits: try dash before last 3
    3. Generic fallbacks for other patterns
    
    Args:
        pn: Part number without dashes (e.g., "C15607010HP", "ISX108945")
        
    Returns:
        List of possible dash-inserted variants to try (ordered by likelihood)
    """
    variants = []
    pn_upper = pn.upper().strip()
    
    # Remove any existing dashes for analysis
    pn_clean = pn_upper.replace("-", "")
    
    # === PAI-specific patterns (C and ISX prefixes) ===
    
    # ISX pattern: ISX followed by digits -> ISX-XXXXXX
    # e.g., ISX108945 -> ISX-108945 (Cummins inframe kits)
    if pn_clean.startswith("ISX") and len(pn_clean) > 3:
        # Primary: ISX-rest (most common for inframe kits)
        variants.append(f"ISX-{pn_clean[3:]}")
        # Secondary: try 3 from end if there's enough length
        if len(pn_clean) >= 7:
            variants.append(f"{pn_clean[:-3]}-{pn_clean[-3:]}")
        # Tertiary: try 5 from end for longer part numbers
        if len(pn_clean) >= 9:
            variant = f"{pn_clean[:-5]}-{pn_clean[-5:]}"
            if variant not in variants:
                variants.append(variant)
    
    # C pattern: C + digits -> try dash 3 from end first, then 5
    # e.g., C15103010 -> C15103-010, C15607010HP -> C15607-010HP
    elif pn_clean.startswith("C") and len(pn_clean) >= 6:
        # Check if there's a letter suffix (HP, STD, etc.)
        suffix_match = re.match(r'^(C\d+)([A-Z]{2,})$', pn_clean)
        if suffix_match:
            base = suffix_match.group(1)
            suffix = suffix_match.group(2)
            # Try dash 3 from end of base: C15607010HP -> C15607-010HP
            if len(base) >= 6:
                variants.append(f"{base[:-3]}-{base[-3:]}{suffix}")
            # Try dash 5 from end of base: C15607010HP -> C156-07010HP
            if len(base) >= 8:
                variant = f"{base[:-5]}-{base[-5:]}{suffix}"
                if variant not in variants:
                    variants.append(variant)
        else:
            # No suffix - pure C + digits
            # Try dash 3 from end: C15103010 -> C15103-010
            variants.append(f"{pn_clean[:-3]}-{pn_clean[-3:]}")
            # Try dash 5 from end: C15103010 -> C1510-3010
            if len(pn_clean) >= 8:
                variant = f"{pn_clean[:-5]}-{pn_clean[-5:]}"
                if variant not in variants:
                    variants.append(variant)
    
    # === Generic patterns for other part numbers ===
    
    # Pattern: Letter(s) + 5 digits + rest -> insert dash after 6 chars
    # e.g., A12345678 -> A12345-678
    match = re.match(r'^([A-Z]+\d{5})(\d.*)$', pn_clean)
    if match:
        variant = f"{match.group(1)}-{match.group(2)}"
        if variant not in variants:
            variants.append(variant)
    
    # Pattern: Pure digits - try dash before last 3
    # e.g., 23536348 -> 23536-348
    if pn_clean.isdigit() and len(pn_clean) >= 6:
        variant = f"{pn_clean[:-3]}-{pn_clean[-3:]}"
        if variant not in variants:
            variants.append(variant)
    
    # Fallback: Generic dash 3 from end (if not already tried)
    if len(pn_clean) >= 6:
        variant = f"{pn_clean[:-3]}-{pn_clean[-3:]}"
        if variant not in variants:
            variants.append(variant)
    
    # Try dash before suffix letters (HP, STD, etc.) - generic
    match3 = re.match(r'^(.+\d)([A-Z]{2,})$', pn_clean)
    if match3:
        variant = f"{match3.group(1)}-{match3.group(2)}"
        if variant not in variants:
            variants.append(variant)
    
    return variants


async def search_atl_diesel(
    part_number: str,
    session: aiohttp.ClientSession | None = None
) -> ATLDieselResult:
    """
    Search ATL Diesel for a part number.
    
    Searches atldiesel.com/search?q={part_number} and parses results.
    If match found, fetches product page for full details.
    
    ATL Diesel search works BETTER with dashes included, so we try:
    1. Original part number (with dashes if present)
    2. Fall back to dash-stripped version only if no results
    
    Args:
        part_number: Part number to search for
        session: Optional aiohttp session (creates new one if not provided)
        
    Returns:
        ATLDieselResult with product data or error
    """
    result = ATLDieselResult(search_query=part_number)
    
    # Keep original for comparison - ATL search works better WITH dashes
    original_pn = part_number.strip().upper()
    # Also create clean version for matching (no dashes/spaces)
    clean_pn = part_number.replace("-", "").replace(" ", "").upper()
    
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=ATL_TIMEOUT),
            headers=dict(ATL_BROWSER_HEADERS),
        )
    
    try:
        # Try search with original part number first (dashes preserved)
        # ATL Diesel search returns better results WITH dashes (e.g., "c15103-010" vs "c15103010")
        search_queries = [part_number]
        
        # If original has dashes, also try without (fallback)
        if "-" in part_number:
            search_queries.append(part_number.replace("-", ""))
        # If original has NO dashes, try multiple common dash patterns
        elif len(part_number) >= 6:
            # Generate multiple dash insertion patterns for CAT-style part numbers
            # Examples: C15607010HP -> C15607-010HP, C15103010 -> C15103-010
            dash_variants = _generate_dash_variants(part_number)
            search_queries.extend(dash_variants)
            logger.info(f"ATL dash variants for {part_number}: {dash_variants}")
        
        matches = []
        all_results = []  # All search result products (for xref saving)
        used_query = part_number
        
        for query in search_queries:
            search_url = ATL_SEARCH_URL.format(query=query)
            logger.info(f"ATL trying: {query}")
            
            async with session.get(search_url) as resp:
                if resp.status != 200:
                    logger.warning(
                        "ATL search HTTP %s for query '%s'", resp.status, query
                    )
                    result.error = f"ATL search returned HTTP {resp.status}"
                    continue
                
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # Find product links in search results
                product_links = soup.select('a[href*="/products/"]')
                
                # Filter to EXACT matches only - prevent wrong products
                for link in product_links:
                    href = link.get("href", "")
                    text = link.get_text(strip=True)
                    text_clean = text.replace("-", "").replace(" ", "").upper()
                    href_clean = href.replace("-", "").replace("_", "").upper()
                    
                    # Extract SKU from URL (usually last part after /products/)
                    url_sku = ""
                    if "/products/" in href.lower():
                        url_sku = href.split("/products/")[-1].split("?")[0]
                    url_sku_clean = url_sku.replace("-", "").upper()

                    # Extract leading part number from URL slug
                    # e.g. "2486740-caterpillar-c15-6nz-head-gasket-set-new" → "2486740"
                    # but "caterpillar-c15-acert-...-c15103-010" → "C15103010" (from end)
                    url_pn = ""
                    if url_sku:
                        first_tok = url_sku.split("-")[0].upper()
                        # First token is a part number if it contains a digit
                        if any(c.isdigit() for c in first_tok) and len(first_tok) >= 4:
                            url_pn = first_tok
                        else:
                            # Fallback: extract part number from title (before | pipe)
                            title_part = text.split("|")[0].strip() if "|" in text else ""
                            pn_m = re.search(r'\b([A-Z0-9]{4,}(?:-[A-Z0-9]+)*)\b', title_part.upper())
                            if pn_m and any(c.isdigit() for c in pn_m.group(1)):
                                url_pn = pn_m.group(1).replace("-", "")

                    # Collect ALL search results for xref scraping
                    if url_pn and url_pn != clean_pn:
                        # Extract price
                        price = None
                        price_match = re.search(r'\$([0-9,]+\.?\d*)', text)
                        if price_match:
                            try:
                                price = float(price_match.group(1).replace(",", ""))
                            except ValueError:
                                pass
                        full_url = href if href.startswith("http") else ATL_BASE_URL + href
                        full_url = full_url.split("?")[0]
                        all_results.append(ATLRelatedProduct(
                            sku=url_pn, title=text[:100], price=price, url=full_url,
                        ))
                    
                    # STRICT MATCH: Part number must match exactly (ignoring dashes)
                    # Compare: clean_pn (our search) vs url_sku or text_clean
                    is_exact_match = False
                    
                    # Check if exact PN match in URL SKU
                    if url_sku_clean and clean_pn == url_sku_clean:
                        is_exact_match = True
                    # Check if URL slug ends with the PN (common for ATL URLs)
                    # e.g. "caterpillar-c15-acert-...-c15103-010" → clean ends with "C15103010"
                    elif url_sku_clean and url_sku_clean.endswith(clean_pn):
                        is_exact_match = True
                    # Check if exact PN match at start of text (SKU usually first)
                    elif text_clean.startswith(clean_pn):
                        is_exact_match = True
                    # Check if text contains exact SKU with boundaries
                    elif f"|{clean_pn}|" in f"|{text_clean}|" or f"|{clean_pn}" in f"|{text_clean}":
                        is_exact_match = True
                    # Check if URL contains exact match with word boundary
                    elif f"/{clean_pn.lower()}" in href.lower().replace("-", "") or f"-{clean_pn.lower()}" in href.lower().replace("-", ""):
                        is_exact_match = True
                    
                    if not is_exact_match:
                        # Log near-misses for debugging
                        if len(clean_pn) >= 6 and clean_pn[:6] in text_clean:
                            logger.debug(f"ATL near-miss: searched '{clean_pn}', found '{text_clean[:30]}...' - SKIPPED (not exact)")
                        continue
                    
                    # Extract price from surrounding text if available
                    price = None
                    price_match = re.search(r'\$([0-9,]+\.?\d*)', text)
                    if price_match:
                        try:
                            price = float(price_match.group(1).replace(",", ""))
                        except ValueError:
                            pass
                    matches.append((href, text, price))
                    logger.debug(f"ATL EXACT match: '{clean_pn}' -> '{text[:50]}...'")
                
                # If we found results (exact or not), stop searching
                if matches or all_results:
                    used_query = query
                    result.error = None  # clear any earlier-query HTTP errors
                    logger.debug(f"ATL found {len(matches)} exact + {len(all_results)} total with query: {query}")
                    break
                
                # Rate limit between search attempts
                if query != search_queries[-1]:
                    await asyncio.sleep(0.5)
        
        # Save all search results for xref scraping (deduplicated)
        seen_skus = set()
        for rp in all_results:
            if rp.sku not in seen_skus:
                seen_skus.add(rp.sku)
                result.all_search_results.append(rp)
        if all_results:
            logger.info(f"ATL collected {len(result.all_search_results)} search results for xref: {[r.sku for r in result.all_search_results]}")

        # After trying all queries, check if we have matches
        if not matches:
            # No exact match — but if we have search results, navigate to
            # the first one to extract cross-references from its product page
            if result.all_search_results and result.all_search_results[0].url:
                first_url = result.all_search_results[0].url
                logger.info("ATL no exact match for '%s', fetching first result for xrefs: %s",
                            part_number, first_url)
                await asyncio.sleep(ATL_RATE_LIMIT)
                result = await _fetch_product_page(first_url, session, result)
                # Mark as not an exact find (pricing etc. may not be for our part)
                result.found = False
            return result
        
        # Track which query matched and if it was a cross-reference
        result.matched_query = used_query
        result.is_cross_reference = (used_query.upper().replace("-", "") != part_number.upper().replace("-", ""))
        
        # Take best match (first one typically most relevant)
        best_href, best_text, _ = matches[0]
        if best_href.startswith("/"):
            product_url = ATL_BASE_URL + best_href
        elif best_href.startswith("http"):
            product_url = best_href
        else:
            product_url = ATL_BASE_URL + "/" + best_href
        
        # Remove query params for cleaner URL
        product_url = product_url.split("?")[0]
        
        # Fetch full product page
        await asyncio.sleep(ATL_RATE_LIMIT)
        result = await _fetch_product_page(product_url, session, result)
            
    except asyncio.TimeoutError:
        result.error = "Request timed out"
    except aiohttp.ClientError as e:
        result.error = f"Network error: {e}"
    except Exception as e:
        result.error = f"Unexpected error: {e}"
        logger.exception(f"ATL search error for {part_number}")
    finally:
        if own_session and session:
            await session.close()
    
    return result


async def _fetch_product_page(
    url: str,
    session: aiohttp.ClientSession,
    result: ATLDieselResult
) -> ATLDieselResult:
    """Fetch and parse ATL Diesel product page."""
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                result.error = f"Product page returned {resp.status}"
                return result
            
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            
            result.atl_url = url
            result.found = True
            
            # Extract title from h1
            h1 = soup.find("h1")
            if h1:
                result.atl_title = h1.get_text(strip=True)
            
            # Extract SKU - look for "SKU: XXXX" pattern
            sku_match = re.search(r'SKU:\s*([A-Z0-9\-]+)', html, re.IGNORECASE)
            if sku_match:
                result.atl_sku = sku_match.group(1).strip()
            
            # Extract prices - look for "$X,XXX.XX" patterns
            # ATL shows prices in format: $4,650.00 (compare) and $3,875.42 (sale)
            price_patterns = re.findall(r'\$([0-9,]+\.[0-9]{2})', html)
            unique_prices = []
            for p in price_patterns:
                try:
                    val = float(p.replace(",", ""))
                    if val not in unique_prices and val > 0:
                        unique_prices.append(val)
                except ValueError:
                    continue
            
            # Sort prices descending - highest is usually compare/original price
            unique_prices.sort(reverse=True)
            
            # Check for sale indicator
            is_sale = "Sale Price:" in html or "sale" in html.lower()
            
            if unique_prices:
                if is_sale and len(unique_prices) >= 2:
                    # Higher price is compare-at, lower is sale price
                    result.atl_compare_price = unique_prices[0]
                    result.atl_sale_price = unique_prices[1] if len(unique_prices) > 1 else unique_prices[0]
                    result.atl_price = result.atl_sale_price  # Current price is sale price
                else:
                    result.atl_price = unique_prices[0]
            
            # Extract vendor
            vendor_match = re.search(r'Vendor:\s*([A-Za-z\s]+?)(?:\s*Price|\s*$|\n)', html)
            if vendor_match:
                result.atl_vendor = vendor_match.group(1).strip()
            else:
                # Try alternative pattern
                if "PAI" in html[:5000]:  # Check near top of page
                    result.atl_vendor = "PAI"
                elif "ATL Diesel" in html[:5000]:
                    result.atl_vendor = "ATL Diesel"
            
            # Extract availability
            if "Available" in html and "ships within" in html.lower():
                result.atl_stock = "In Stock"
            elif "Out of Stock" in html or "out of stock" in html.lower():
                result.atl_stock = "Out of Stock"
            elif "Available" in html:
                result.atl_stock = "In Stock"
            
            # Extract cross-references + images from Shopify JSON
            all_image_urls = []
            json_url = url.rstrip('/') + '.json'
            try:
                async with session.get(json_url) as json_resp:
                    if json_resp.status == 200:
                        data = await json_resp.json()
                        product_data = data.get('product', {})

                        # Cross-references from body_html
                        body_html = product_data.get('body_html', '')
                        if body_html:
                            result.cross_references = _extract_cross_refs_from_body(body_html)
                            if result.cross_references:
                                logger.info("ATL found %d cross-refs on product page: %s",
                                            len(result.cross_references), result.cross_references)

                        images = product_data.get('images', [])
                        for img in images:
                            src = img.get('src', '')
                            if src and 'cdn.shopify.com' in src:
                                all_image_urls.append(src)
                        logger.info(f"ATL JSON endpoint found {len(all_image_urls)} images")
            except Exception as e:
                logger.debug(f"ATL JSON endpoint failed: {e}")
            
            # FALLBACK: Parse HTML if JSON didn't return images
            if not all_image_urls:
                logger.debug("ATL JSON had no images, trying HTML fallback methods")
                
                # Method 1: Parse JSON-LD structured data (most reliable for Shopify)
            json_ld_scripts = soup.find_all('script', type='application/ld+json')
            for script in json_ld_scripts:
                try:
                    import json
                    data = json.loads(script.string or '{}')
                    # Product schema has 'image' field (can be string or list)
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        images = data.get('image', [])
                        if isinstance(images, str):
                            images = [images]
                        all_image_urls.extend(images)
                        logger.debug(f"JSON-LD found {len(images)} images")
                except Exception as e:
                    logger.debug(f"JSON-LD parse error: {e}")
            
            # Method 2: Parse product gallery data-attributes (Shopify themes)
            # Look for data-media-id, data-image, or similar attributes
            gallery_images = soup.find_all(['img', 'div', 'li'], attrs={'data-media-id': True})
            for elem in gallery_images:
                img = elem.find('img') if elem.name != 'img' else elem
                if img:
                    src = img.get('data-src') or img.get('src') or ''
                    if 'cdn.shopify.com' in src:
                        all_image_urls.append(src)
            
            # Method 3: Find thumbnail gallery images (ATL uses slick-slide or similar)
            thumbnails = soup.select('.product-thumbnails img, .product__media-list img, .slick-slide img, [data-thumbnail] img, .product-single__thumbnail img')
            for thumb in thumbnails:
                src = thumb.get('data-src') or thumb.get('src') or ''
                # Get full-size URL from data attribute if available
                full_src = thumb.get('data-zoom') or thumb.get('data-full') or thumb.get('data-large') or src
                if 'cdn.shopify.com' in full_src:
                    all_image_urls.append(full_src)
            
            # Method 4: Find main product image and variants
            main_images = soup.select('.product__media img, .product-featured-media img, .product-single__photo img, [data-product-media] img')
            for img in main_images:
                src = img.get('data-src') or img.get('src') or ''
                if 'cdn.shopify.com' in src:
                    all_image_urls.append(src)
            
            # Method 5: Fallback - regex for ALL Shopify CDN URLs in HTML
            regex_urls = re.findall(
                r'(https://cdn\.shopify\.com/s/files/[^"\'>\s]+\.(?:jpg|jpeg|png|webp))',
                html
            )
            all_image_urls.extend(regex_urls)
            
            # Method 6: Look for srcset values (high-res images)
            srcset_matches = re.findall(r'srcset="([^"]+)"', html)
            for srcset in srcset_matches:
                urls = re.findall(r'(https://cdn\.shopify\.com/s/files/[^\s,]+)', srcset)
                all_image_urls.extend(urls)
            
            # Filter and deduplicate images
            filtered_images = []
            seen = set()
            for img_url in all_image_urls:
                # Clean URL (remove size params to get full resolution)
                clean_url = re.sub(r'_\d+x\d*\.', '.', img_url)  # Remove _100x or _100x100
                clean_url = re.sub(r'_\d+x\d*@\dx\.', '.', clean_url)  # Remove _100x@2x
                clean_url = re.sub(r'\?v=\d+', '', clean_url)  # Remove version param
                clean_url = clean_url.split('?')[0]  # Remove all query params
                
                if clean_url in seen:
                    continue
                seen.add(clean_url)
                
                # Skip non-product images (logos, badges, banners, placeholders)
                lower_url = img_url.lower()
                filename = lower_url.split("/")[-1].split("?")[0]
                
                # Skip small placeholder images (e.g., 88x88.png)
                if re.match(r'^\d+x\d+\.(png|jpg|gif)$', filename):
                    continue
                
                if any(skip in lower_url for skip in [
                    "offer", "icon", "logo", "banner", "badge",
                    "shipping", "oem", "30day", "expert", "payment",
                    "atldiesel", "atl-diesel", "atl_diesel", "atl-logo",
                    "pai-logo", "pai_logo", "brand-logo", "brand_logo",
                    "watermark", "overlay", "stamp", "placeholder"
                ]):
                    continue
                
                # Skip if filename starts with "atl" or contains "logo"
                if filename.startswith("atl") or "logo" in filename:
                    continue
                
                # Must be in products/ or files/ path with product-like naming
                if "products/" in img_url or ("files/" in img_url and any(
                    c.isdigit() for c in img_url.split("/")[-1][:10]
                )):
                    filtered_images.append(img_url)
            
            result.image_urls = filtered_images[:ATL_MAX_IMAGES]
            logger.info(f"ATL found {len(filtered_images)} product images (using {len(result.image_urls)})")
            
            # Extract Frequently Bought Together
            result.frequently_bought = _extract_frequently_bought(soup, html)
            
            # Extract You May Also Like
            result.related_products = _extract_related_products(soup, html)
            
    except Exception as e:
        result.error = f"Parse error: {e}"
        logger.exception(f"ATL product page parse error: {url}")
    
    return result


def _extract_cross_refs_from_body(body_html: str) -> list[str]:
    """Extract cross-reference part numbers from ATL product body_html.

    ATL stores cross-refs in a section like:
      <strong>Cross Reference Part Numbers</strong>
      ...
      3067460<br>2564787<br>2326557
    """
    soup = BeautifulSoup(body_html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Find "Cross Reference Part Numbers" and grab the numbers after it
    xrefs: list[str] = []
    match = re.search(
        r"Cross[\s-]*Refer(?:ence|enced)?\s*Part\s*Numbers?\s*[:\s]*(.*?)(?:Free Shipping|Core Return|Warranty|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        nums = re.findall(r"\b([A-Z0-9]{4,}(?:-[A-Z0-9]+)*)\b", match.group(1).upper())
        xrefs = [n for n in nums if any(c.isdigit() for c in n)]

    return xrefs


def _extract_frequently_bought(soup: BeautifulSoup, html: str) -> list[ATLRelatedProduct]:
    """Extract Frequently Bought Together products."""
    products = []
    
    try:
        # Find the FBT section
        fbt_header = soup.find(string=re.compile("FREQUENTLY BOUGHT TOGETHER", re.I))
        if not fbt_header:
            return products
        
        # Get the parent container
        fbt_container = fbt_header.find_parent()
        if not fbt_container:
            return products
        
        # Find product links within the container
        # Go up a few levels to get the full section
        for _ in range(5):
            if fbt_container.parent:
                fbt_container = fbt_container.parent
        
        links = fbt_container.find_all('a', href=re.compile('/products/'))
        
        for link in links:
            href = link.get('href', '')
            if not href or "cdn.shopify" in href:
                continue
            
            text = link.get_text(strip=True)
            if not text or len(text) < 5:
                continue
            
            # Skip if it's the main product (contains "This item:")
            if "This item:" in text:
                continue
            
            # Extract price from text
            price = None
            price_match = re.search(r'\$([0-9,]+\.?\d*)', text)
            if price_match:
                try:
                    price = float(price_match.group(1).replace(",", ""))
                except ValueError:
                    pass
            
            # Extract SKU from URL or text
            sku = None
            sku_match = re.search(r'([A-Z0-9]{3,}(?:-[A-Z0-9]+)*)', text.split("|")[0] if "|" in text else text)
            if sku_match:
                sku = sku_match.group(1)
            
            # Build full URL
            full_url = href if href.startswith("http") else ATL_BASE_URL + href
            full_url = full_url.split("?")[0]  # Remove query params
            
            # Clean title
            title = text.split("$")[0].strip() if "$" in text else text
            title = title.replace("|", "-").strip()
            
            products.append(ATLRelatedProduct(
                sku=sku,
                title=title[:200],  # Limit length
                price=price,
                url=full_url
            ))
        
        # Deduplicate by URL
        seen_urls = set()
        unique = []
        for p in products:
            if p.url and p.url not in seen_urls:
                seen_urls.add(p.url)
                unique.append(p)
        
        return unique[:5]  # Max 5 FBT products
        
    except Exception as e:
        logger.debug(f"FBT extraction error: {e}")
        return products


def _extract_related_products(soup: BeautifulSoup, html: str) -> list[ATLRelatedProduct]:
    """Extract You May Also Like products."""
    products = []
    
    try:
        # Find the YMAL section
        ymal_header = soup.find(string=re.compile("YOU MAY ALSO LIKE", re.I))
        if not ymal_header:
            return products
        
        # Get the parent container
        ymal_container = ymal_header.find_parent()
        if not ymal_container:
            return products
        
        # Go up to get the carousel/grid container
        for _ in range(5):
            if ymal_container.parent:
                ymal_container = ymal_container.parent
        
        links = ymal_container.find_all('a', href=re.compile('/products/'))
        
        for link in links:
            href = link.get('href', '')
            if not href or "cdn.shopify" in href:
                continue
            
            text = link.get_text(strip=True)
            if not text or len(text) < 5:
                continue
            
            # Extract price
            price = None
            price_match = re.search(r'Price:\s*\$([0-9,]+\.?\d*)', text)
            if not price_match:
                price_match = re.search(r'\$([0-9,]+\.?\d*)', text)
            if price_match:
                try:
                    price = float(price_match.group(1).replace(",", ""))
                except ValueError:
                    pass
            
            # Extract SKU
            sku = None
            sku_match = re.search(r'([A-Z0-9]{3,}(?:-[A-Z0-9]+)*)', text.split("|")[0] if "|" in text else text[:50])
            if sku_match:
                sku = sku_match.group(1)
            
            # Build URL
            full_url = href if href.startswith("http") else ATL_BASE_URL + href
            full_url = full_url.split("?")[0]
            
            # Clean title
            title = re.sub(r'Price:\s*\$[0-9,\.]+', '', text).strip()
            title = title.split("|")[0].strip() if "|" in title else title
            
            products.append(ATLRelatedProduct(
                sku=sku,
                title=title[:200],
                price=price,
                url=full_url
            ))
        
        # Deduplicate
        seen_urls = set()
        unique = []
        for p in products:
            if p.url and p.url not in seen_urls:
                seen_urls.add(p.url)
                unique.append(p)
        
        return unique[:6]  # Max 6 related products
        
    except Exception as e:
        logger.debug(f"YMAL extraction error: {e}")
        return products


async def download_atl_images(
    result: ATLDieselResult,
    output_dir: Path,
    watermark: bool = True,
    temp_dir: Path | None = None,
) -> list[Path]:
    """
    Download images from ATL Diesel result to output directory.
    
    If watermark=True, downloads to temp_dir first, then applies JAKS watermark
    and saves to output_dir (same flow as HHP images).
    
    Args:
        result: ATLDieselResult with image_urls
        output_dir: Final directory for watermarked images (e.g., output/.../images/atl/)
        watermark: Whether to apply JAKS watermark
        temp_dir: Temp directory for raw downloads (auto-created if None)
        
    Returns:
        List of downloaded file paths (in output_dir if watermarked)
    """
    if not result.image_urls:
        return []
    
    import os
    from PIL import Image
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # If watermarking, download to temp first
    if watermark:
        if temp_dir is None:
            temp_dir = Path.cwd() / "output" / "_temp_raw" / "atl_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        download_target = temp_dir
    else:
        download_target = output_dir
    
    downloaded_raw = []
    
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers=dict(ATL_BROWSER_HEADERS),
    ) as session:
        for i, url in enumerate(result.image_urls[:ATL_MAX_IMAGES]):
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    
                    content = await resp.read()
                    if len(content) < 1000:  # Skip tiny/broken images
                        continue
                    
                    # Always save as JPG for consistency
                    filename = f"atl_{i+1:02d}.jpg"
                    filepath = download_target / filename
                    
                    # Convert to RGB JPEG
                    try:
                        from io import BytesIO
                        with Image.open(BytesIO(content)) as img:
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")
                            elif img.mode != "RGB":
                                img = img.convert("RGB")
                            img.save(str(filepath), format="JPEG", quality=92, optimize=True)
                    except Exception as e:
                        logger.debug(f"Image conversion failed for {url}: {e}")
                        continue
                    
                    downloaded_raw.append(filepath)
                    logger.debug(f"Downloaded ATL image: {filepath}")
                    
            except Exception as e:
                logger.debug(f"Failed to download {url}: {e}")
                continue
            
            await asyncio.sleep(0.3)  # Small delay between downloads
    
    if not downloaded_raw:
        return []
    
    # Apply watermark if requested (same as HHP flow)
    if watermark and downloaded_raw:
        try:
            from utils.helpers import watermark_folder
            from core.constants import DEFAULT_LOGO_PATH
            
            logo_path = os.getenv(
                "JAKS_LOGO_PATH",
                str(DEFAULT_LOGO_PATH) if DEFAULT_LOGO_PATH else ""
            )
            
            if logo_path and Path(logo_path).exists():
                wm_count = watermark_folder(download_target, output_dir, logo_path)
                logger.info(f"[ATL Images] Watermarked {wm_count} image(s) -> {output_dir}")
                
                # Return watermarked files
                watermarked = list(output_dir.glob("*.jpg"))
                
                # Cleanup temp directory
                try:
                    import shutil
                    if temp_dir and temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass
                
                return watermarked
            else:
                logger.warning(f"[ATL Images] Logo not found at {logo_path}, skipping watermark")
                # Move files to output_dir without watermark
                final_paths = []
                for raw_path in downloaded_raw:
                    dest = output_dir / raw_path.name
                    raw_path.rename(dest)
                    final_paths.append(dest)
                return final_paths
                
        except Exception as e:
            logger.error(f"[ATL Images] Watermark failed: {e}")
            # Return raw files on failure
            return downloaded_raw
    
    return downloaded_raw


async def enrich_product_from_atl(
    product: "Product",
    session: aiohttp.ClientSession | None = None,
    download_images: bool = True
) -> ATLDieselResult:
    """
    Enrich a single product with ATL Diesel data.
    
    Tries multiple part number formats to find a match.
    
    Args:
        product: Product to enrich
        session: Optional shared aiohttp session
        download_images: Whether to download images
        
    Returns:
        ATLDieselResult (check .found for success)
    """
    # Try multiple part number formats
    part_numbers_to_try = []
    
    # Primary part number
    primary_pn = getattr(product, "primary_pn", None) or getattr(product, "oem_part_number", None)
    if primary_pn:
        part_numbers_to_try.append(primary_pn)
    
    # PAI part number (often matches ATL since they sell PAI)
    pai_pn = getattr(product, "pai_part_number", None)
    if pai_pn and pai_pn not in part_numbers_to_try:
        part_numbers_to_try.append(pai_pn)
    
    # SKU without prefix
    sku = getattr(product, "sku", "")
    if sku:
        clean_sku = sku.replace("JAKS-", "").strip()
        if clean_sku and clean_sku not in part_numbers_to_try:
            part_numbers_to_try.append(clean_sku)
    
    # Try each part number
    result = ATLDieselResult(search_query=part_numbers_to_try[0] if part_numbers_to_try else "")
    
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=ATL_TIMEOUT),
            headers=dict(ATL_BROWSER_HEADERS),
        )
    
    try:
        for pn in part_numbers_to_try:
            result = await search_atl_diesel(pn, session)
            if result.found:
                break
            await asyncio.sleep(ATL_RATE_LIMIT)
        
        # Download images if found
        if result.found and download_images and result.image_urls:
            # Determine output directory
            category = getattr(product, "category", "uncategorized") or "uncategorized"
            manufacturer = getattr(product, "manufacturer", "unknown") or "unknown"
            product_sku = getattr(product, "sku", "unknown") or "unknown"
            
            images_dir = Path("output") / category / manufacturer / product_sku / "images" / "atl"
            downloaded = await download_atl_images(result, images_dir, watermark=True)
            
            # Store paths in result for caller to use
            result.image_urls = [str(p) for p in downloaded]  # Replace URLs with local paths
            
    finally:
        if own_session and session:
            await session.close()
    
    return result


def update_product_with_atl_result(product: "Product", result: ATLDieselResult) -> None:
    """
    Update a Product object with ATL Diesel enrichment data.
    
    Args:
        product: Product to update (modified in place)
        result: ATLDieselResult from search
    """
    if not result.found:
        return
    
    # Update pricing dict
    pricing = getattr(product, "pricing", None)
    if not isinstance(pricing, dict):
        pricing = {}
    
    pricing["atl_price"] = result.atl_price
    pricing["atl_sale_price"] = result.atl_sale_price
    pricing["atl_compare_price"] = result.atl_compare_price
    pricing["atl_vendor"] = result.atl_vendor
    pricing["atl_stock"] = result.atl_stock
    pricing["atl_url"] = result.atl_url
    pricing["atl_sku"] = result.atl_sku
    pricing["atl_matched_query"] = result.matched_query
    pricing["atl_is_cross_reference"] = result.is_cross_reference
    
    # Store related products data
    if result.frequently_bought:
        pricing["atl_frequently_bought"] = [
            {"sku": p.sku, "title": p.title, "price": p.price, "url": p.url}
            for p in result.frequently_bought
        ]
    
    if result.related_products:
        pricing["atl_related_products"] = [
            {"sku": p.sku, "title": p.title, "price": p.price, "url": p.url}
            for p in result.related_products
        ]
    
    setattr(product, "pricing", pricing)
    
    # Store ATL image paths
    if result.image_urls:
        setattr(product, "images_atl", result.image_urls)


# Synchronous wrapper for non-async contexts
def search_atl_diesel_sync(part_number: str) -> ATLDieselResult:
    """Synchronous wrapper for search_atl_diesel."""
    return asyncio.run(search_atl_diesel(part_number))
