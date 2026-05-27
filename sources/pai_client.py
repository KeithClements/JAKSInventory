from __future__ import annotations

import logging
import os
import re
import json
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from typing import TypedDict

logger = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"([\d,]+\.\d{2})")


def _default_cache_path() -> str:
    # Keep local state under output/ (already used by the app).
    try:
        base = os.path.join(os.getcwd(), "output")
    except Exception:
        base = "output"
    return os.path.join(base, "pai_cache.sqlite")


def _coerce_float_env(name: str, default: float) -> float:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        if not raw:
            return float(default)
        return float(raw)
    except Exception:
        return float(default)


_DEFAULT_UAS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/122.0 Safari/537.36",
]


class PAICostLookupResult(TypedDict, total=False):
    cost: float
    sku: str
    source: str
    # Optional extras (best-effort parsing)
    url: str
    stock: dict[str, object]
    image_urls: list[str]  # PAI product image URLs
    # Cross-reference info: when searched SKU differs from returned SKU
    is_cross_reference: bool  # True if this is a cross-reference match
    searched_sku: str  # The original SKU that was searched
    pai_sku: str  # The PAI catalog SKU (may differ from searched)


def _abs_url(base: str, maybe_path: str) -> str:
    try:
        return urljoin(str(base or "").rstrip("/") + "/", str(maybe_path or "").lstrip("/"))
    except Exception:
        return str(maybe_path or "")


def _find_login_form(html: str) -> tuple[str | None, str | None, str | None, dict[str, str]]:
    """Return (action, user_field, pass_field, hidden_fields)."""

    try:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    except Exception:
        return None, None, None, {}

    best_form = None
    best_pwd = None
    try:
        for form in soup.find_all("form"):
            pwd = form.find("input", attrs={"type": "password"})
            if pwd is not None:
                best_form = form
                best_pwd = pwd
                break
    except Exception:
        best_form = None

    if best_form is None:
        return None, None, None, {}

    action = best_form.get("action")
    pass_name = None
    try:
        pass_name = str(best_pwd.get("name") or "").strip() or None
    except Exception:
        pass_name = None

    user_name = None
    try:
        # Prefer email-type input.
        user_el = best_form.find("input", attrs={"type": "email"})
        if user_el is None:
            # Common fallbacks.
            for cand in best_form.find_all("input"):
                nm = str(cand.get("name") or "").lower()
                if "email" in nm or "user" in nm or "login" in nm or "username" in nm:
                    user_el = cand
                    break
        if user_el is not None:
            user_name = str(user_el.get("name") or "").strip() or None
    except Exception:
        user_name = None

    hidden: dict[str, str] = {}
    try:
        for inp in best_form.find_all("input", attrs={"type": "hidden"}):
            name = str(inp.get("name") or "").strip()
            if not name:
                continue
            hidden[name] = str(inp.get("value") or "")
    except Exception:
        hidden = {}

    return (str(action).strip() if action else None), user_name, pass_name, hidden


def normalize_part_number(part_number: str) -> str:
    """Very light normalization.

    Goal: make equivalent forms cache/query-matchable (e.g., 123-456 vs 123456).

    Rules:
    - Uppercase
    - Strip
    - Remove non-alphanumeric separators
    """

    s = str(part_number or "").strip().upper()
    if not s:
        return ""
    return re.sub(r"[^A-Z0-9]", "", s)


def _sku_matches_search(returned_sku: str, searched_key: str) -> bool:
    """Validate that PAI returned SKU matches what we searched for.
    
    PAI search can return similar but WRONG parts. For example:
    - Searched: C15111010HP
    - Returned: C15101010HP (different middle digits!)
    
    This function ensures we don't apply prices from wrong parts.
    
    STRICT Match rules (BUG-010 fix - Jan 10, 2026):
    1. Exact match (normalized): C15111010HP == C15111010HP
    2. One contains the other fully: C15111 in C15111010HP
    3. Known suffix variations only: HP, STD, size codes (010, 020, 030)
    
    Does NOT match (REJECTED):
    - C15101010HP vs C15111010HP (different core digits)
    - ERK8031005 vs ERK80301001 (similar prefix but different part)
    - MV1303001 vs MV1304001 (single digit difference in core)
    """
    sku = normalize_part_number(returned_sku)
    key = normalize_part_number(searched_key)
    
    if not sku or not key:
        return False
    
    # Exact match
    if sku == key:
        return True
    
    # One fully contains the other (handles suffix variations like HP, STD)
    if key in sku or sku in key:
        return True
    
    # Handle size suffix variations: C15111-010HP vs C15111010HP
    # Remove common suffixes and compare base
    def strip_size_suffix(s: str) -> str:
        """Remove size suffixes like 010, 020, 030, 040, STD, HP from end."""
        # Common patterns: -010HP, -020, -STD, HP
        return re.sub(r'(0[1-4]0|STD|HP)+$', '', s)
    
    base_sku = strip_size_suffix(sku)
    base_key = strip_size_suffix(key)
    
    if base_sku and base_key and base_sku == base_key:
        return True
    
    # STRICT: Only allow matches if base parts are identical after removing
    # known prefixes (ERK-, MV-, ISX, etc.) and suffixes
    def extract_core_number(s: str) -> str:
        """Extract the core numeric/alphanumeric identifier."""
        # Remove known prefixes
        prefixes = ['ERK', 'MV', 'ISX', 'ISB', 'ISC', 'ISL', 'ISM', 'MCB', 'PAI']
        core = s
        for prefix in prefixes:
            if core.startswith(prefix):
                core = core[len(prefix):]
                break
        # Remove trailing suffixes
        core = re.sub(r'(0[1-4]0|STD|HP|E|F|B|CA|Q)+$', '', core)
        return core
    
    core_sku = extract_core_number(sku)
    core_key = extract_core_number(key)
    
    # Core numbers must match exactly (no fuzzy matching!)
    if core_sku and core_key and core_sku == core_key:
        return True
    
    # BUG-010 FIX: REMOVED the 70% prefix match - it was too permissive
    # and allowed wrong parts like ERK8031005 vs ERK80301001
    
    return False


def _fuzzy_exact_part_pattern(norm_key: str) -> re.Pattern[str] | None:
    """Build a regex that matches the key allowing non-alnum separators between characters."""

    k = str(norm_key or "").strip().upper()
    if not k:
        return None
    # Safety: keep patterns reasonably bounded.
    if len(k) > 80:
        return None
    # Match the exact sequence with optional separators between characters.
    # Example: 123456 matches 123-456, 123 456, 123/456, etc.
    parts = [re.escape(ch) for ch in k]
    pat = r"\b" + r"[^A-Z0-9]*".join(parts) + r"\b"
    try:
        return re.compile(pat, re.IGNORECASE)
    except Exception:
        return None


def parse_pai_cost(html: str, *, part_number: str | None = None) -> float | None:
    """Production-grade parser for PAI price.

    Anchors on the unique element `id="priceToggle"` and extracts only the
    displayed sell price, ignoring list/MSRP/MAP by design.

    Returns None safely if anything is off.
    """

    try:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    except Exception:
        return None

    # Preferred: detail page price toggle (observed stable).
    price_el = soup.find("span", id="priceToggle")
    if price_el:
        text = price_el.get_text(strip=True)
        match = _PRICE_RE.search(text)
        if match:
            try:
                value = float(match.group(1).replace(",", ""))
            except Exception:
                value = None
            if value and value > 0:
                return value

    # Fallback: anchor on "YOUR PRICE" label (detail page).
    try:
        label = soup.find(string=re.compile(r"^\s*YOUR\s*PRICE\s*$", re.IGNORECASE))
    except Exception:
        label = None

    if label is not None:
        try:
            # Walk forward in the document for the first $-prefixed price.
            nxt = label
            for _ in range(50):
                nxt = getattr(nxt, "next_element", None)
                if nxt is None:
                    break
                if isinstance(nxt, str) and "$" in nxt:
                    match = _PRICE_RE.search(nxt)
                    if match:
                        try:
                            value = float(match.group(1).replace(",", ""))
                        except Exception:
                            value = None
                        if value and value > 0:
                            return value
        except Exception:
            pass

    return None


def parse_pai_sku(html: str, detail_url: str = "") -> str | None:
    """Parse PAI SKU from product page HTML.

    PRIORITY ORDER (BUG-010 fix - Jan 10, 2026):
    1. Extract from URL path: /product/MV1304-001 → MV1304-001 (MOST RELIABLE)
    2. Look for exact "SKU:" label in product details section
    3. Fallback to regex (least reliable)
    
    This prevents grabbing SKUs from ALTERNATES section which appears on the page.
    """
    
    # PRIORITY 1: Extract SKU from URL (most reliable!)
    # URL format: /product/MV1304-001 or /product/MV1304-001?cartSource=...
    if detail_url:
        try:
            import re as re_mod
            url_match = re_mod.search(r'/product/([A-Z0-9\-]+)', str(detail_url), re_mod.IGNORECASE)
            if url_match:
                url_sku = url_match.group(1).strip()
                if url_sku and len(url_sku) >= 4:
                    return normalize_part_number(url_sku)
        except Exception:
            pass
    
    try:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    except Exception:
        return None

    def _looks_like_sku_value(txt: str) -> bool:
        t = str(txt or "").strip()
        if len(t) < 4:
            return False
        if not re.fullmatch(r"[A-Z0-9\-]+", t, re.IGNORECASE):
            return False
        # Require at least one digit to avoid matching phrases like "here".
        return any(ch.isdigit() for ch in t)

    # PRIORITY 2: Find SKU label that is NOT inside ALTERNATES section
    # Look for standalone "SKU:" or "SKU" label
    try:
        # Find all potential SKU labels
        for label in soup.find_all(string=re.compile(r"^\s*SKU\s*:?\s*$", re.IGNORECASE)):
            # Check if this label is inside an ALTERNATES section (skip if so)
            parent = label.parent
            in_alternates = False
            for _ in range(10):  # Walk up the DOM tree
                if parent is None:
                    break
                parent_text = str(getattr(parent, 'get_text', lambda: '')() or '').upper()
                if 'ALTERNATE' in parent_text and parent_text.index('ALTERNATE') < parent_text.find('SKU'):
                    in_alternates = True
                    break
                parent = getattr(parent, 'parent', None)
            
            if in_alternates:
                continue  # Skip this SKU label, it's in ALTERNATES section
                
            # Find the next tag/text that looks like a SKU value
            nxt = label
            for _ in range(20):  # Reduced from 40 to be more precise
                nxt = getattr(nxt, "next_element", None)
                if nxt is None:
                    break
                if isinstance(nxt, str):
                    txt = str(nxt).strip()
                    if txt and _looks_like_sku_value(txt):
                        return normalize_part_number(txt)
                else:
                    try:
                        txt = str(nxt.get_text(" ", strip=True) or "").strip()
                    except Exception:
                        txt = ""
                    if txt and _looks_like_sku_value(txt):
                        return normalize_part_number(txt)
    except Exception:
        pass

    # PRIORITY 3: Fallback regex - but NOT if it's in ALTERNATES context
    text = soup.get_text(" ", strip=True)
    # Look specifically for "SKU: VALUE" pattern not preceded by "ALTERNATE"
    # (Python 3.14 rejects variable-width lookbehind, so we check context manually.)
    for m in re.finditer(r'SKU\s*:?\s*([A-Z0-9\-]{4,})\b', text, re.IGNORECASE):
        # Skip matches where "ALTERNATE" appears within the 20 chars preceding "SKU"
        preceding = text[max(0, m.start() - 20):m.start()]
        if re.search(r'ALTERNATE[S]?\s*$', preceding, re.IGNORECASE):
            continue
        raw = m.group(1).strip()
        if _looks_like_sku_value(raw):
            return normalize_part_number(raw)

    return None


def parse_pai_oem(html: str) -> str | None:
    """Parse OEM part number from PAI product page HTML.

    Looks for "OEM:" label followed by the OEM value.
    """
    try:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    except Exception:
        return None

    # Prefer field label patterns like "OEM" / "OEM:" / "OEM Part" with an adjacent value.
    try:
        label = soup.find(string=re.compile(r"^\s*OEM\s*(?:PART)?\s*:??\s*$", re.IGNORECASE))
    except Exception:
        label = None

    def _looks_like_oem_value(txt: str) -> bool:
        t = str(txt or "").strip()
        if len(t) < 4:
            return False
        if not re.fullmatch(r"[A-Z0-9\-]+", t, re.IGNORECASE):
            return False
        return any(ch.isdigit() for ch in t)

    if label is not None:
        try:
            nxt = label
            for _ in range(50):
                nxt = getattr(nxt, "next_element", None)
                if nxt is None:
                    break
                if isinstance(nxt, str):
                    continue
                try:
                    txt = str(nxt.get_text(" ", strip=True) or "").strip()
                except Exception:
                    txt = ""
                if txt and _looks_like_oem_value(txt):
                    return txt
        except Exception:
            pass

    # Fallback: regex across visible text
    text = soup.get_text(" ", strip=True)
    for pat in (
        r"\bOEM\s*:?\s*([A-Z0-9\-]{4,})\b",
        r"\bOEM\s+PART\s*:?\s*([A-Z0-9\-]{4,})\b",
        r"\bORIGINAL\s+EQUIPMENT\s*:?\s*([A-Z0-9\-]{4,})\b",
    ):
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            oem = match.group(1).strip()
            if _looks_like_oem_value(oem):
                return oem

    return None


def parse_pai_stock(html: str) -> dict[str, object]:
    """Best-effort parser for PAI stock/warehouse availability.

    Returns a dict like:
      {"ATL": "low_stock", "LAX": "out_of_stock", "DFW": 2, "IND": 1}

    IMPORTANT: The PAI detail page has a "Your Selection" panel (center)
    and an "Alternate" panel (right). Stock badges in the Alternate section
    must NOT be attributed to the main product.

    Heuristics only; safe to return {}.
    """

    # Known PAI warehouse codes (include IND for Indianapolis)
    KNOWN_WAREHOUSES = {"ATL", "LAX", "DFW", "CHI", "SEA", "NYC", "MIA", "PHX", "DEN", "HOU", "SLC", "PDX", "DTW", "MSP", "BOS", "IND"}

    try:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    except Exception:
        return {}

    # ── Scope to "Your Selection" section to avoid reading alternate's stock ──
    main_section = None
    alt_section = None
    for header_el in soup.find_all(string=re.compile(r"Your\s*Selection", re.IGNORECASE)):
        parent = header_el
        for _ in range(8):
            parent = getattr(parent, "parent", None)
            if parent is None:
                break
            if hasattr(parent, "name") and parent.name in ("div", "section", "td", "article"):
                container_text = parent.get_text(" ", strip=True)
                if "STOCK" in container_text.upper() or "WARRANTY" in container_text.upper():
                    main_section = parent
                    break
        if main_section:
            break

    for header_el in soup.find_all(string=re.compile(r"^Alternate$", re.IGNORECASE)):
        parent = header_el
        for _ in range(8):
            parent = getattr(parent, "parent", None)
            if parent is None:
                break
            if hasattr(parent, "name") and parent.name in ("div", "section", "td", "article"):
                container_text = parent.get_text(" ", strip=True)[:200]
                if any(wh in container_text for wh in ("ATL", "DFW", "LAX", "IND")):
                    alt_section = parent
                    break
        if alt_section:
            break

    # Use scoped soup for stock parsing
    stock_soup = main_section if main_section else soup
    stock_text = stock_soup.get_text(" ", strip=True)

    stock: dict[str, object] = {}

    # Method 1: Look for STOCK: section with quantity text like "DFW - 2 In Stock"
    try:
        qty_pattern = re.compile(r"\b([A-Z]{3})\s*[-–]\s*(\d+)\s*In\s*Stock\b", re.IGNORECASE)
        for match in qty_pattern.finditer(stock_text):
            wh = match.group(1).upper()
            qty = int(match.group(2))
            if wh in KNOWN_WAREHOUSES and wh not in stock:
                stock[wh] = qty
                logger.debug(f"[PAI Stock] Found quantity: {wh} = {qty}")
    except Exception as e:
        logger.debug(f"[PAI Stock] Quantity text parsing failed: {e}")

    # Method 2: Look for PAI badge-pill elements in main section only
    try:
        badges = stock_soup.find_all(class_=re.compile(r"badge-pill", re.IGNORECASE))

        # If using full soup, filter out alternate section badges
        if stock_soup is soup and alt_section:
            alt_badge_ids = {id(b) for b in alt_section.find_all(class_=re.compile(r"badge-pill", re.IGNORECASE))}
            badges = [b for b in badges if id(b) not in alt_badge_ids]
        for badge in badges:
            # Get warehouse code from text
            badge_text = badge.get_text(strip=True).upper()
            wh = None
            for known in KNOWN_WAREHOUSES:
                if known in badge_text:
                    wh = known
                    break
            
            if not wh:
                continue
            
            # Skip if we already have a numeric quantity for this warehouse
            if wh in stock and isinstance(stock[wh], int):
                continue
            
            # Determine stock status from class or data-original-title
            classes = " ".join(badge.get("class", []))
            title = str(badge.get("data-original-title", "") or badge.get("title", "") or "").lower()
            
            # Check title for quantity: "Southwest Warehouse (Dallas/Fort Worth) In Stock" 
            # might have quantity nearby
            qty_in_title = re.search(r"(\d+)\s*(?:in\s*stock|available|units?)", title, re.IGNORECASE)
            if qty_in_title and wh not in stock:
                stock[wh] = int(qty_in_title.group(1))
                continue
            
            if "badge-success" in classes or "in stock" in title:
                # In stock
                if wh not in stock:
                    stock[wh] = "in_stock"
            elif "badge-danger" in classes or "out of stock" in title or "out-of-stock" in title:
                # Out of stock
                if wh not in stock:
                    stock[wh] = "out_of_stock"
            elif "badge-warning" in classes or "quantity limited" in title or "low" in title:
                # Low stock / Quantity Limited (treat as available but limited)
                if wh not in stock:
                    stock[wh] = "low_stock"
        
        if stock:
            logger.debug(f"[PAI Stock] Parsed from badges: {stock}")
            return stock
    except Exception as e:
        logger.debug(f"[PAI Stock] Badge parsing failed: {e}")

    # Secondary: Look for "Not Available" badge in main section
    try:
        not_avail = stock_soup.find_all(string=re.compile(r"not\s*available", re.IGNORECASE))
        if not_avail:
            logger.debug("[PAI Stock] Found 'Not Available' indicator")
            return {"_status": "not_available"}
    except Exception:
        pass

    # Fallback: Try text-based parsing (scoped to main section)
    try:
        stock_elements = stock_soup.find_all(string=re.compile(r"stock|availability|warehouse|inventory", re.IGNORECASE))
        for elem in stock_elements[:15]:
            parent = elem.parent if elem.parent else elem
            for _ in range(3):
                if parent.parent:
                    parent = parent.parent
            text = parent.get_text("\n", strip=True)
            _parse_stock_from_text(text, stock, KNOWN_WAREHOUSES)
    except Exception:
        pass

    # Also look for specific class patterns (scoped to main section)
    if not stock:
        try:
            for cls in ["stock", "availability", "inventory", "warehouse"]:
                elems = stock_soup.find_all(class_=re.compile(cls, re.IGNORECASE))
                for elem in elems[:10]:
                    text = elem.get_text("\n", strip=True)
                    _parse_stock_from_text(text, stock, KNOWN_WAREHOUSES)
        except Exception:
            pass

    # Debug logging
    if stock:
        logger.debug(f"[PAI Stock] Parsed stock data: {stock}")
    
    return stock


def _parse_stock_from_text(text: str, stock: dict[str, object], known_warehouses: set[str] | None = None) -> None:
    """Helper to parse stock from text."""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]

    # Known PAI warehouse codes
    KNOWN_WAREHOUSES = known_warehouses or {"ATL", "LAX", "DFW", "CHI", "SEA", "NYC", "MIA", "PHX", "DEN", "HOU", "SLC", "PDX", "DTW", "MSP", "BOS", "IND"}

    # Pattern: "LAX - 4 in Stock" or "LAX: 4" or "LAX 4 available"
    qty_pat = re.compile(r"\b([A-Z]{3})\b\s*(?:[-:])?\s*(\d+)\s*(?:IN\s*STOCK|AVAILABLE|UNITS?|PCS?)?\b", re.IGNORECASE)
    
    # Pattern: "LAX In Stock" or "LAX - Available"
    in_pat = re.compile(r"\b([A-Z]{3})\b\s*(?:[-:])?\s*(?:IN\s*STOCK|AVAILABLE|YES)\b", re.IGNORECASE)
    
    # Pattern: "LAX Out of Stock" or "LAX - Not Available"
    out_pat = re.compile(r"\b([A-Z]{3})\b\s*(?:[-:])?\s*(?:OUT\s*(?:OF\s*)?STOCK|NOT\s*AVAILABLE|NO|UNAVAILABLE)\b", re.IGNORECASE)

    # Pattern: qty followed by warehouse "4 @ LAX" or "4 in LAX"
    reverse_qty_pat = re.compile(r"(\d+)\s*(?:@|in|at)\s*([A-Z]{3})\b", re.IGNORECASE)

    for ln in lines:
        # Try reverse pattern first (qty @ warehouse)
        for m in reverse_qty_pat.finditer(ln):
            try:
                qty = int(m.group(1))
                wh = str(m.group(2) or "").upper()
                if wh in KNOWN_WAREHOUSES:
                    stock[wh] = max(stock.get(wh, 0) if isinstance(stock.get(wh), int) else 0, qty)
            except Exception:
                pass

        # Standard patterns
        m = qty_pat.search(ln)
        if m:
            wh = str(m.group(1) or "").upper()
            if wh in KNOWN_WAREHOUSES:
                try:
                    qty = int(m.group(2))
                    stock[wh] = max(stock.get(wh, 0) if isinstance(stock.get(wh), int) else 0, qty)
                except Exception:
                    pass
            continue

        m = out_pat.search(ln)
        if m:
            wh = str(m.group(1) or "").upper()
            if wh in KNOWN_WAREHOUSES and wh not in stock:
                stock[wh] = "out_of_stock"
            continue

        m = in_pat.search(ln)
        if m:
            wh = str(m.group(1) or "").upper()
            if wh in KNOWN_WAREHOUSES and wh not in stock:
                stock[wh] = "in_stock"


def parse_pai_images(html: str, base_url: str = "https://portal.pai.com") -> list[str]:
    """Extract product image URLs from PAI product detail page.
    
    PAI uses a main product image and thumbnail gallery. Images are typically
    hosted on their CDN or in a structured path.
    
    Returns list of absolute image URLs (main image first, then thumbnails).
    """
    try:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    except Exception:
        return []
    
    images: list[str] = []
    seen: set[str] = set()
    
    def _add_image(url: str) -> None:
        if not url:
            return
        # Make absolute
        try:
            abs_url = _abs_url(base_url, url)
        except Exception:
            abs_url = url
        # Skip tiny placeholders, icons, logos
        lower = abs_url.lower()
        if any(skip in lower for skip in ["logo", "icon", "placeholder", "spinner", "loading", "blank"]):
            return
        # Must be an image extension or CDN path
        if not any(ext in lower for ext in [".jpg", ".jpeg", ".png", ".webp", "image", "/img/", "/images/"]):
            return
        if abs_url not in seen:
            seen.add(abs_url)
            images.append(abs_url)
    
    # Method 1: Look for main product image in "Images" section
    try:
        # PAI has "Images" header with product photos below
        images_header = soup.find(string=re.compile(r"^\s*Images\s*$", re.IGNORECASE))
        if images_header:
            # Find the container (usually parent div)
            container = images_header.parent
            for _ in range(5):
                if container and container.parent:
                    container = container.parent
                    # Look for img tags within this container
                    for img in container.find_all("img", limit=20):
                        src = img.get("src") or img.get("data-src") or ""
                        _add_image(str(src))
    except Exception:
        pass
    
    # Method 2: Look for product gallery / carousel images
    try:
        # Common gallery patterns
        for selector in [
            'img[class*="product"]',
            'img[class*="gallery"]',
            'img[class*="thumbnail"]',
            '.product-image img',
            '.product-gallery img',
            '.carousel img',
            '[data-gallery] img',
        ]:
            for img in soup.select(selector)[:10]:
                src = img.get("src") or img.get("data-src") or img.get("data-lazy") or ""
                _add_image(str(src))
    except Exception:
        pass
    
    # Method 3: Look for large images (by URL pattern or data attributes)
    try:
        for img in soup.find_all("img", limit=50):
            src = str(img.get("src") or "")
            # Skip small sizes
            if "_thumb" in src.lower() or "_small" in src.lower():
                continue
            # Prefer large/full size
            if "_large" in src.lower() or "_full" in src.lower() or "large" in src.lower():
                _add_image(src)
                continue
            # Check for data-zoom or data-full attributes
            for attr in ["data-zoom", "data-zoom-image", "data-full", "data-large", "data-src"]:
                val = img.get(attr)
                if val:
                    _add_image(str(val))
    except Exception:
        pass
    
    # Method 4: Fallback - any reasonably-sized image in the main content
    if not images:
        try:
            for img in soup.find_all("img", limit=30):
                src = str(img.get("src") or "")
                # Skip navigation/header images
                parent_classes = " ".join(img.parent.get("class", []) if img.parent else []).lower()
                if any(skip in parent_classes for skip in ["nav", "header", "footer", "menu"]):
                    continue
                _add_image(src)
        except Exception:
            pass
    
    logger.debug(f"[PAI Images] Found {len(images)} image URLs")
    return images[:6]  # Limit to 6 images max


def remove_pai_watermark(image_data: bytes, crop_bottom_pct: float = 0.10) -> bytes | None:
    """Remove PAI watermark from image.
    
    Tries Replicate LaMa inpainting first (best quality), then falls back to
    local blur-based processing.
    
    Args:
        image_data: Raw image bytes
        crop_bottom_pct: Percentage of image height to crop from bottom (default 10%)
    
    Returns:
        Processed image as JPEG bytes, or None if processing fails
    """
    import os
    from io import BytesIO
    try:
        from PIL import Image
    except ImportError:
        logger.warning("PIL not available for watermark removal")
        return None
    
    try:
        # Try Replicate LaMa first (best quality for watermark removal)
        replicate_token = os.getenv("REPLICATE_API_TOKEN", "").strip()
        if replicate_token:
            logger.debug("[PAI Watermark] Trying Replicate LaMa inpainting...")
            lama_result = _remove_watermark_replicate(image_data, replicate_token)
            if lama_result:
                image_data = lama_result
                logger.info("[PAI Watermark] Diagonal watermark removed via Replicate LaMa")
            else:
                logger.debug("[PAI Watermark] LaMa failed, falling back to local processing")
                # Fall back to local blur-based removal
                processed_data = _remove_diagonal_watermark(image_data)
                if processed_data:
                    image_data = processed_data
                    logger.info("[PAI Watermark] Diagonal watermark reduced via local blur")
        else:
            # No Replicate token, use local processing
            processed_data = _remove_diagonal_watermark(image_data)
            if processed_data:
                image_data = processed_data
                logger.info("[PAI Watermark] Diagonal watermark reduced via local blur")
        
        # Crop bottom portion (copyright line "© 20XX PAI INDUSTRIES, INC.")
        with Image.open(BytesIO(image_data)) as img:
            w, h = img.size
            
            # Skip if image is too small
            if min(w, h) < 300:
                return None
            
            # Crop bottom 10% (where copyright typically appears)
            crop_pixels = int(h * crop_bottom_pct)
            new_h = h - crop_pixels
            
            # Ensure we keep enough of the image
            if new_h < h * 0.85:
                crop_pixels = int(h * 0.15)
                new_h = h - crop_pixels
            
            # Crop: (left, top, right, bottom)
            cropped = img.crop((0, 0, w, new_h))
            
            # Convert to RGB if needed (for JPEG output)
            if cropped.mode in ("RGBA", "P"):
                cropped = cropped.convert("RGB")
            
            logger.debug(f"[PAI Watermark] Cropped bottom {crop_pixels}px ({crop_bottom_pct*100:.0f}%) from {w}x{h} image")
            
            # Save as JPEG
            out = BytesIO()
            cropped.save(out, format="JPEG", quality=92, optimize=True)
            return out.getvalue()
    except Exception as e:
        logger.debug(f"[PAI Watermark] Failed to process image: {e}")
        return None


def _remove_diagonal_watermark(image_data: bytes) -> bytes | None:
    """Gentle local removal of semi-transparent diagonal 'PAI INDUSTRIES' watermark.
    
    This is the fallback when Replicate LaMa isn't available. Uses conservative
    blur-blending that may leave some watermark visible but won't damage the product.
    """
    from io import BytesIO
    
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageDraw
        import numpy as np
        
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            width, height = img.size
            
            # Convert to numpy for processing
            img_array = np.array(img, dtype=np.float32)
            
            r, g, b = img_array[:,:,0], img_array[:,:,1], img_array[:,:,2]
            avg = (r + g + b) / 3
            saturation = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
            
            # Conservative corner mask - smaller regions to avoid product damage
            corner_mask = np.zeros((height, width), dtype=bool)
            corner_size_h = int(height * 0.20)  # Top/bottom 20%
            corner_size_w = int(width * 0.25)   # Left/right 25%
            
            # All four corners only
            corner_mask[:corner_size_h, :corner_size_w] = True
            corner_mask[:corner_size_h, -corner_size_w:] = True
            corner_mask[-corner_size_h:, :corner_size_w] = True
            corner_mask[-corner_size_h:, -corner_size_w:] = True
            
            # Tight gray detection - only obvious watermark pixels
            gray_mask = (
                (avg > 160) & (avg < 240) &
                (saturation < 30) &
                ~((r > 245) & (g > 245) & (b > 245))
            )
            
            # Combined: gray pixels in corners only
            watermark_mask = corner_mask & gray_mask
            
            coverage = np.count_nonzero(watermark_mask) / (width * height)
            
            if coverage > 0.002:
                # Gentle blur for blending
                blur = img.filter(ImageFilter.GaussianBlur(radius=4))
                blur_arr = np.array(blur, dtype=np.float32)
                
                # Conservative blend: 70% blurred, 30% original
                blend_factor = 0.70
                
                for c in range(3):
                    img_array[:,:,c] = np.where(
                        watermark_mask,
                        img_array[:,:,c] * (1 - blend_factor) + blur_arr[:,:,c] * blend_factor,
                        img_array[:,:,c]
                    )
                
                logger.debug(f"[PAI Watermark] Gentle local blur: {coverage*100:.1f}% coverage")
            
            # Convert back to PIL
            result_img = Image.fromarray(np.clip(img_array, 0, 255).astype(np.uint8), mode="RGB")
            
            out = BytesIO()
            result_img.save(out, format="JPEG", quality=92, optimize=True)
            return out.getvalue()
            
    except ImportError as e:
        logger.debug(f"[PAI Watermark] Missing dependency: {e}")
        return None
            
    except Exception as e:
        logger.debug(f"[PAI Watermark] Processing failed: {e}")
        return None


def _remove_watermark_replicate(image_data: bytes, api_token: str) -> bytes | None:
    """Remove watermark using Replicate's SD Inpainting model.
    
    Uses tight mask detection to only target the light gray watermark text,
    not the product itself.
    """
    import base64
    from io import BytesIO
    import httpx
    import time
    
    try:
        from PIL import Image, ImageFilter, ImageDraw
        import numpy as np
        
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            width, height = img.size
            
            # Create automatic mask for watermark
            img_array = np.array(img, dtype=np.float32)
            r, g, b = img_array[:,:,0], img_array[:,:,1], img_array[:,:,2]
            avg = (r + g + b) / 3
            saturation = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
            
            # TIGHT detection - only light gray watermark text
            # The watermark is lighter than the product, low saturation
            gray_mask = (
                (avg > 175) & (avg < 235) &  # Light gray only
                (saturation < 28) &  # Very low saturation (gray)
                ~((r > 248) & (g > 248) & (b > 248))  # Not white
            )
            
            # Small corner regions where text actually appears
            corner_mask = np.zeros((height, width), dtype=bool)
            ch = int(height * 0.28)  # 28% from top/bottom
            cw = int(width * 0.32)   # 32% from left/right
            corner_mask[:ch, :cw] = True       # Top-left
            corner_mask[:ch, -cw:] = True      # Top-right
            corner_mask[-ch:, :cw] = True      # Bottom-left
            corner_mask[-ch:, -cw:] = True     # Bottom-right
            
            # Combine: light gray pixels in corners only
            mask_array = (corner_mask & gray_mask).astype(np.uint8) * 255
            
            # Dilate moderately to catch text edges
            from scipy import ndimage
            mask_array = ndimage.binary_dilation(mask_array > 0, iterations=4).astype(np.uint8) * 255
            
            coverage = np.count_nonzero(mask_array) / (width * height)
            if coverage < 0.005:
                logger.debug(f"[PAI Watermark] Mask coverage too low: {coverage*100:.1f}%")
                return None
            if coverage > 0.20:
                logger.debug(f"[PAI Watermark] Mask coverage too high: {coverage*100:.1f}%")
                return None
            
            logger.debug(f"[PAI Watermark] Mask: {coverage*100:.1f}% coverage")
            
            # Create grayscale mask (white = inpaint, black = keep)
            mask_img = Image.fromarray(mask_array, mode="L")
            
            # Save image as base64
            img_buf = BytesIO()
            img.save(img_buf, format="PNG")
            img_b64 = base64.b64encode(img_buf.getvalue()).decode("utf-8")
            img_uri = f"data:image/png;base64,{img_b64}"
            
            # Save mask as base64
            mask_buf = BytesIO()
            mask_img.save(mask_buf, format="PNG")
            mask_b64 = base64.b64encode(mask_buf.getvalue()).decode("utf-8")
            mask_uri = f"data:image/png;base64,{mask_b64}"
        
        # Direct API call to Replicate
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Prefer": "wait"  # Synchronous mode
        }
        
        # Use SD inpainting with better prompt for watermark removal
        # The key is to use a simple prompt that preserves the background
        payload = {
            "version": "95b7223104132402a9ae91cc677285bc5eb997834bd2349fa486f53910fd68b3",
            "input": {
                "image": img_uri,
                "mask": mask_uri,
                "prompt": "clean white background, plain, no text, no watermark, seamless",
                "negative_prompt": "text, watermark, logo, letters, words, PAI, INDUSTRIES",
                "num_inference_steps": 30,
                "guidance_scale": 9.0,
                "strength": 0.99,  # High strength to fully replace masked area
            }
        }
        
        # Start prediction
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                "https://api.replicate.com/v1/predictions",
                headers=headers,
                json=payload
            )
            
            if resp.status_code not in (200, 201):
                logger.debug(f"[PAI Watermark] Replicate API error: {resp.status_code} - {resp.text[:200]}")
                return None
            
            result = resp.json()
            
            # If synchronous mode worked, output is ready
            output = result.get("output")
            status = result.get("status")
            
            # Poll if not completed yet
            if not output and status in ("starting", "processing"):
                prediction_url = result.get("urls", {}).get("get") or f"https://api.replicate.com/v1/predictions/{result['id']}"
                
                for _ in range(120):  # Max 120 seconds
                    time.sleep(1)
                    poll_resp = client.get(prediction_url, headers=headers)
                    if poll_resp.status_code == 200:
                        poll_result = poll_resp.json()
                        status = poll_result.get("status")
                        if status == "succeeded":
                            output = poll_result.get("output")
                            break
                        elif status == "failed":
                            logger.debug(f"[PAI Watermark] Prediction failed: {poll_result.get('error')}")
                            return None
            
            # Output is a URL for LaMa
            if output:
                output_url = output if isinstance(output, str) else (output[0] if isinstance(output, list) else None)
                if output_url:
                    # Download the result image
                    img_resp = client.get(output_url, timeout=60)
                    if img_resp.status_code == 200:
                        return img_resp.content
        
        return None
        
    except Exception as e:
        logger.debug(f"[PAI Watermark] Replicate API error: {e}")
        return None


def parse_pai_cross_part(html: str, *, oem_part_number: str) -> str | None:
    """Attempt to resolve an OEM part number to a PAI part number from a search response.

    This is intentionally conservative:
    - Requires a canonical/og:url that contains a plausible part token.
    - Requires the OEM part to appear on the page (fuzzy-exact, separators allowed).
    - Returns None if anything is ambiguous.
    """

    oem_key = normalize_part_number(oem_part_number)
    if not oem_key:
        return None

    try:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    except Exception:
        return None

    # Verify the OEM part appears somewhere on the page (allowing separators).
    # Use raw HTML (not only visible text) because many sites render refs in hidden elements.
    pat = _fuzzy_exact_part_pattern(oem_key)
    if pat is not None:
        try:
            if not pat.search(str(html or "")):
                return None
        except Exception:
            return None

    url_candidates: list[str] = []
    try:
        for link in soup.find_all("link", rel=True):
            rel = link.get("rel")
            rel_vals: list[str] = []
            if isinstance(rel, str):
                rel_vals = [rel]
            elif isinstance(rel, (list, tuple)):
                rel_vals = [str(x) for x in rel if x]
            rel_vals = [r.strip().lower() for r in rel_vals if str(r).strip()]
            if "canonical" in rel_vals:
                href = link.get("href")
                if href:
                    url_candidates.append(str(href))
                break
    except Exception:
        pass

    for prop in ("og:url", "twitter:url"):
        try:
            meta = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if meta and meta.get("content"):
                url_candidates.append(str(meta.get("content")))
        except Exception:
            pass

    def _is_plausible_part(token: str) -> bool:
        if not token:
            return False
        if token == oem_key:
            return False
        if len(token) < 5:
            return False
        if not token.isalnum() or not any(ch.isdigit() for ch in token):
            return False
        return True

    # Strategy 1: canonical URL-derived token (legacy behavior)
    for u in url_candidates:
        u = str(u or "").strip()
        if not u:
            continue
        token = u.rstrip("/").split("/")[-1]
        token = normalize_part_number(token)
        if _is_plausible_part(token):
            return token

    # Strategy 2: cross-reference table cell.
    # Example observed:
    #   <td class="col-7 border-top-0">311148</td>
    # We require the OEM part to be present on the page (already verified above),
    # then we try to find a row containing the OEM and extract the PAI part cell.
    candidates: set[str] = set()

    try:
        rows = soup.find_all("tr")
    except Exception:
        rows = []

    if rows:
        for tr in rows:
            try:
                tr_html = str(tr)
            except Exception:
                continue
            if pat is not None:
                try:
                    if not pat.search(tr_html):
                        continue
                except Exception:
                    continue

            try:
                # Prefer the specific class combo; allow minor variations.
                tds = tr.select("td.col-7.border-top-0, td.col-7")
            except Exception:
                tds = []

            for td in tds:
                try:
                    raw = td.get_text(" ", strip=True)
                except Exception:
                    raw = ""
                token = normalize_part_number(raw)
                if _is_plausible_part(token):
                    candidates.add(token)

    # Fallback: if we couldn't anchor to a row, and the page only yields one
    # plausible token in that td class, accept it.
    if not candidates:
        try:
            for td in soup.select("td.col-7.border-top-0, td.col-7"):
                token = normalize_part_number(td.get_text(" ", strip=True))
                if _is_plausible_part(token):
                    candidates.add(token)
        except Exception:
            pass

    if len(candidates) == 1:
        return next(iter(candidates))

    return None


def _is_in_stock(stock_info: dict) -> bool:
    """Check if any warehouse shows stock available."""
    if not isinstance(stock_info, dict) or not stock_info:
        return False
    try:
        for wh, val in stock_info.items():
            if wh.startswith("_"):  # Skip metadata keys like "_status"
                continue
            if isinstance(val, str):
                if val.lower() in ("in_stock", "available", "yes", "low_stock"):
                    return True
            elif isinstance(val, (int, float)):
                if val > 0:
                    return True
            elif isinstance(val, bool) and val:
                return True
    except Exception:
        pass
    return False


@dataclass
class PAIClient:
    username: str
    password: str

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.logged_in = False
        self.cache: dict[str, PAICostLookupResult | None] = {}
        self.cross_cache: dict[str, dict | None] = {}
        # Default to the public PAI store site (used by our parsers).
        # Can be overridden for debugging via env var PAI_BASE_URL.
        self.base_url = os.getenv("PAI_BASE_URL", "https://store.paiindustries.com").rstrip("/")
        self._login_attempted = False

        # Thread-safe helpers: allow to_thread / parallel usage without corrupting sessions.
        self._thread_local = threading.local()
        self._login_lock = threading.Lock()

        # Rate limiting is global per client (across threads).
        # Default to ~1.5 req/s unless overridden.
        min_interval = _coerce_float_env("PAI_MIN_INTERVAL_SEC", 0.67)
        if min_interval < 0:
            min_interval = 0.0
        self._min_interval_sec = float(min_interval)
        self._rate_lock = threading.Lock()
        self._last_request_ts = 0.0

        # User-Agent rotation.
        self._ua_pool = list(_DEFAULT_UAS)
        self._ua_lock = threading.Lock()
        self._ua_idx = 0

        # Persistent cache (sqlite). TTL defaults to 24h.
        self._cache_path = str(os.getenv("PAI_CACHE_PATH", "") or "").strip() or _default_cache_path()
        self._cache_ttl_sec = float(_coerce_float_env("PAI_CACHE_TTL_SEC", 24.0 * 3600.0))
        if self._cache_ttl_sec < 0:
            self._cache_ttl_sec = 0.0
        self._db_lock = threading.Lock()

    def _cache_db_init(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
        except Exception:
            pass
        try:
            with sqlite3.connect(self._cache_path, timeout=5) as con:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pai_cache (
                        namespace TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        PRIMARY KEY(namespace, key)
                    )
                    """
                )
                con.commit()
        except Exception:
            return

    def _cache_get(self, namespace: str, key: str) -> object | None:
        if not namespace or not key:
            return None
        try:
            self._cache_db_init()
        except Exception:
            pass
        now = time.time()
        try:
            with self._db_lock:
                with sqlite3.connect(self._cache_path, timeout=5) as con:
                    cur = con.execute(
                        "SELECT value, created_at FROM pai_cache WHERE namespace=? AND key=?",
                        (str(namespace), str(key)),
                    )
                    row = cur.fetchone()
            if not row:
                return None
            raw_val, created_at = row
            try:
                created = float(created_at or 0.0)
            except Exception:
                created = 0.0
            if self._cache_ttl_sec > 0 and created and (now - created) > float(self._cache_ttl_sec):
                return None
            try:
                payload = json.loads(str(raw_val or "") or "null")
            except Exception:
                payload = None
            return payload
        except Exception:
            return None

    def _cache_set(self, namespace: str, key: str, value: object) -> None:
        if not namespace or not key:
            return
        try:
            self._cache_db_init()
        except Exception:
            pass
        try:
            raw = json.dumps(value)
        except Exception:
            return
        try:
            with self._db_lock:
                with sqlite3.connect(self._cache_path, timeout=5) as con:
                    con.execute(
                        "INSERT OR REPLACE INTO pai_cache(namespace, key, value, created_at) VALUES(?,?,?,?)",
                        (str(namespace), str(key), str(raw), float(time.time())),
                    )
                    con.commit()
        except Exception:
            return

    def _next_user_agent(self) -> str:
        with self._ua_lock:
            if not self._ua_pool:
                return ""
            ua = self._ua_pool[self._ua_idx % len(self._ua_pool)]
            self._ua_idx += 1
            return str(ua)

    def _respect_rate_limit(self) -> None:
        if float(self._min_interval_sec) <= 0:
            return
        with self._rate_lock:
            now = time.time()
            wait = float(self._min_interval_sec) - (now - float(self._last_request_ts or 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.time()

    def _get_session(self) -> requests.Session:
        s = getattr(self._thread_local, "session", None)
        if isinstance(s, requests.Session):
            return s

        sess = requests.Session()
        try:
            # Copy cookies from the base session (after login this contains auth state).
            sess.cookies.update(self.session.cookies)
        except Exception:
            pass

        try:
            ua = self._next_user_agent()
            if ua:
                sess.headers.update({"User-Agent": ua})
        except Exception:
            pass

        setattr(self._thread_local, "session", sess)
        return sess

    def _request(
        self,
        method: str,
        url: str,
        *,
        session: requests.Session | None = None,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        allow_redirects: bool = True,
        timeout: float = 20.0,
    ):
        # Retry a few times on transient errors.
        attempts = 3
        backoff_base = 0.6
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                self._respect_rate_limit()
                sess = session if isinstance(session, requests.Session) else self._get_session()
                # Rotate UA per request (best-effort).
                try:
                    ua = self._next_user_agent()
                    if ua:
                        sess.headers.update({"User-Agent": ua})
                except Exception:
                    pass

                resp = sess.request(
                    str(method or "GET").upper(),
                    str(url),
                    params=params,
                    data=data,
                    allow_redirects=bool(allow_redirects),
                    timeout=float(timeout),
                )
                if resp is None:
                    raise RuntimeError("No response")

                # Retry only on throttling/server errors.
                if int(getattr(resp, "status_code", 0) or 0) in {429, 500, 502, 503, 504}:
                    raise RuntimeError(f"HTTP {getattr(resp, 'status_code', None)}")

                return resp
            except Exception as e:
                last_exc = e
                if i >= attempts - 1:
                    break
                # Exponential backoff + a tiny jitter.
                try:
                    time.sleep(float(backoff_base) * (2.0 ** float(i)) + random.random() * 0.1)
                except Exception:
                    pass
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Request failed")

    def login(self) -> bool:
        """Attempt a single login for this run.

        Rules:
        - Login once
        - No retries
        - If login fails, the caller should disable PAI silently
        """

        if self._login_attempted:
            return bool(self.logged_in)

        # Ensure only one thread attempts login.
        with self._login_lock:
            if self._login_attempted:
                return bool(self.logged_in)

        self._login_attempted = True

        base = str(self.base_url or "").rstrip("/")
        # Try a few common login endpoints seen on PAI sites.
        login_pages = [
            _abs_url(base, "/account/log-in"),
            _abs_url(base, "/account/login"),
            _abs_url(base, "/account/entrance"),
            _abs_url(base, "/login"),
        ]

        resp = None
        html = ""
        for u in login_pages:
            try:
                resp = self._request("GET", u, timeout=20, session=self.session)
            except Exception:
                resp = None
                continue
            if resp is not None and resp.status_code in {200, 302}:
                try:
                    html = str(resp.text or "")
                except Exception:
                    html = ""
                if html:
                    break

        action, user_field, pass_field, hidden = _find_login_form(html)
        if not action:
            # Fall back to posting to the last attempted login page.
            action = (resp.url if resp is not None else (login_pages[0] if login_pages else base))

        post_url = _abs_url(base, action)
        # Build a few candidate payloads. We try parsed field names first.
        payloads: list[dict[str, str]] = []
        if user_field and pass_field:
            d = dict(hidden)
            d[user_field] = self.username
            d[pass_field] = self.password
            payloads.append(d)
        # Common fallbacks.
        payloads.append({**hidden, "email": self.username, "password": self.password})
        payloads.append({**hidden, "username": self.username, "password": self.password})

        self.logged_in = False
        for data in payloads:
            try:
                r2 = self._request("POST", post_url, data=data, timeout=20, allow_redirects=True, session=self.session)
            except Exception:
                continue

            text = ""
            try:
                text = str(r2.text or "").lower()
            except Exception:
                text = ""

            # Heuristic: presence of logout/dashboard or absence of login form.
            if r2.status_code in {200, 302} and (
                ("log out" in text)
                or ("logout" in text)
                or ("account" in text and "log in" not in text and "login" not in text)
            ):
                self.logged_in = True
                break

        if not self.logged_in:
            logger.info("PAI login failed; disabling PAI enrichment for this run")
        else:
            logger.info("PAI login ok")

        # Copy base session cookies to any thread-local sessions created earlier.
        try:
            s = getattr(self._thread_local, "session", None)
            if isinstance(s, requests.Session):
                s.cookies.update(self.session.cookies)
        except Exception:
            pass
        return bool(self.logged_in)

    def lookup_cost(self, part_number: str) -> PAICostLookupResult | None:
        """Cost lookup.

        Implements the mandatory behavior:
        - Search page (/search...) is NOT trusted for price.
        - Always click the first /product/ result and read "YOUR PRICE" on the detail page.
        """

        if not self.logged_in:
            try:
                if not self.login():
                    return None
            except Exception:
                return None

        key = normalize_part_number(part_number)
        if not key:
            return None

        if key in self.cache:
            return self.cache[key]

        # Persistent cache (store both hits and misses)
        try:
            cached = self._cache_get("cost", key)
            if isinstance(cached, dict) and cached.get("__none__"):
                self.cache[key] = None
                return None
            if isinstance(cached, dict) and ("cost" in cached or "sku" in cached):
                try:
                    # Normalize types minimally.
                    cached_cost = float(cached.get("cost") or 0.0)
                    cached_sku = normalize_part_number(str(cached.get("sku") or ""))
                    if cached_cost > 0 and cached_sku:
                        result2: PAICostLookupResult = {
                            "cost": float(cached_cost),
                            "sku": cached_sku,
                            "source": str(cached.get("source") or cached.get("url") or "CACHE"),
                            "url": str(cached.get("url") or ""),
                            "stock": dict(cached.get("stock") or {}),
                            "image_urls": list(cached.get("image_urls") or []),
                        }
                        self.cache[key] = result2
                        return result2
                except Exception:
                    pass
        except Exception:
            pass

        # PAI sites vary; probe a few known search endpoints/parameter names.
        candidates: list[tuple[str, dict[str, str]]] = [
            ("/search/index", {"term": key}),
            ("/search", {"term": key}),
            ("/search", {"q": key}),
            ("/search", {"query": key}),
            ("/search", {"keyword": key}),
            ("/search", {"s": key}),
            ("/search", {"type": "product", "q": key}),
            ("/search", {"type": "products", "q": key}),
        ]

        for path, params in candidates:
            try:
                resp = self._request("GET", _abs_url(self.base_url, path), params=params, timeout=20)
            except Exception:
                continue
            if resp.status_code != 200:
                continue

            detail_url = ""
            detail_html = ""

            # Page Type B: detail page (can read price + SKU here)
            try:
                if "/product/" in str(resp.url or ""):
                    detail_url = str(resp.url or "")
                    detail_html = str(resp.text or "")
            except Exception:
                detail_url = ""
                detail_html = ""

            # Page Type A: search results (must click first /product/ result)
            if not detail_html:
                try:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = soup.select('a[href*="/product/"]')
                except Exception:
                    links = []

                if not links:
                    continue

                try:
                    detail_url = _abs_url(self.base_url, str(links[0].get("href") or ""))
                except Exception:
                    detail_url = ""
                if not detail_url:
                    continue

                try:
                    resp_detail = self._request("GET", detail_url, timeout=20)
                except Exception:
                    continue
                if resp_detail.status_code != 200:
                    continue
                try:
                    detail_html = str(resp_detail.text or "")
                except Exception:
                    detail_html = ""

            if not detail_html:
                continue

            cost = None
            sku = None
            try:
                cost = parse_pai_cost(detail_html, part_number=key)
            except Exception:
                cost = None
            try:
                # Pass detail_url for reliable SKU extraction from URL path
                sku = parse_pai_sku(detail_html, detail_url=detail_url)
            except Exception:
                sku = None

            if cost and float(cost) > 0:
                sku_norm = normalize_part_number(str(sku or ""))
                if not sku_norm:
                    logger.error(
                        "PAI cost found but no SKU on detail page; refusing to apply cost",
                        extra={"searched": key, "detail_url": detail_url},
                    )
                    self.cache[key] = None
                    return None

                # Check if returned SKU matches searched part number
                # PAI search can return cross-references (e.g., search OEM '5579308' → PAI 'ISX141386')
                is_cross_ref = not _sku_matches_search(sku_norm, key)
                
                if is_cross_ref:
                    logger.info(
                        f"PAI cross-reference found: searched '{key}' → PAI SKU '{sku_norm}'",
                        extra={"searched": key, "pai_sku": sku_norm, "detail_url": detail_url},
                    )

                result: PAICostLookupResult = {
                    "cost": float(cost),
                    "sku": sku_norm,
                    "source": str(detail_url or "DETAIL_PAGE"),
                    "url": str(detail_url or ""),
                    "stock": parse_pai_stock(detail_html),
                    "image_urls": parse_pai_images(detail_html, self.base_url),
                    "is_cross_reference": is_cross_ref,
                    "searched_sku": key,
                    "pai_sku": sku_norm,
                }
                self.cache[key] = result
                try:
                    self._cache_set("cost", key, result)
                except Exception:
                    pass
                return result

        self.cache[key] = None
        try:
            self._cache_set("cost", key, {"__none__": True})
        except Exception:
            pass
        return None

    def lookup_images(self, part_number: str) -> list[str]:
        """Return product image URLs for *part_number* from the PAI portal.

        Unlike :meth:`lookup_cost`, this method does **not** require a parsed
        cost on the detail page — it returns image URLs even when pricing is
        unavailable.  Walks the same /search/ → /product/ flow and runs
        :func:`parse_pai_images` on the detail HTML.

        Returns an empty list if PAI is not configured, login fails, no
        matching product is found, or no images are parseable.
        """
        if not self.logged_in:
            try:
                if not self.login():
                    return []
            except Exception:
                return []

        key = normalize_part_number(part_number)
        if not key:
            return []

        # If lookup_cost already cached a result with images, return them.
        cached_result = self.cache.get(key)
        if isinstance(cached_result, dict):
            urls = list(cached_result.get("image_urls") or [])
            if urls:
                return urls

        candidates: list[tuple[str, dict[str, str]]] = [
            ("/search/index", {"term": key}),
            ("/search", {"term": key}),
            ("/search", {"q": key}),
            ("/search", {"query": key}),
            ("/search", {"keyword": key}),
            ("/search", {"s": key}),
            ("/search", {"type": "product", "q": key}),
            ("/search", {"type": "products", "q": key}),
        ]

        for path, params in candidates:
            try:
                resp = self._request("GET", _abs_url(self.base_url, path), params=params, timeout=20)
            except Exception:
                continue
            if resp.status_code != 200:
                continue

            detail_html = ""
            try:
                if "/product/" in str(resp.url or ""):
                    detail_html = str(resp.text or "")
            except Exception:
                detail_html = ""

            if not detail_html:
                try:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = soup.select('a[href*="/product/"]')
                except Exception:
                    links = []
                if not links:
                    continue
                try:
                    detail_url = _abs_url(self.base_url, str(links[0].get("href") or ""))
                except Exception:
                    detail_url = ""
                if not detail_url:
                    continue
                try:
                    resp_detail = self._request("GET", detail_url, timeout=20)
                except Exception:
                    continue
                if resp_detail.status_code != 200:
                    continue
                try:
                    detail_html = str(resp_detail.text or "")
                except Exception:
                    detail_html = ""

            if not detail_html:
                continue

            try:
                urls = parse_pai_images(detail_html, self.base_url)
            except Exception:
                urls = []
            if urls:
                return urls

        return []

    def lookup_cross(self, oem_part_number: str) -> dict | None:
        """OEM → PAI part resolution.

                Mandatory behavior:
                - Reject garbage/invalid OEM candidates (numeric-only with >=4 digits).
                - Search by OEM number.
                - Collect up to 10 product results.
                - For each result, open detail page and parse OEM field.
                - Accept a SKU only when normalize(pai_oem) == normalize(searched_oem).

                Structured statuses returned:
                    - EXACT: exactly one SKU matched OEM (includes cost from same detail page)
                    - MULTI_MATCH: multiple SKUs matched OEM (no cost auto-applied)
                    - NO_MATCH: no SKU matched OEM
        """

        if not self.logged_in:
            try:
                if not self.login():
                    return None
            except Exception:
                return None

        raw_oem = str(oem_part_number or "").strip()
        key = normalize_part_number(raw_oem)

        def _is_valid_oem_candidate(norm: str) -> bool:
            if not norm:
                return False
            # Reject tiny/low-signal candidates (minimum 4 characters)
            if len(norm) < 4:
                return False
            # Must contain at least some alphanumeric characters
            if not any(c.isalnum() for c in norm):
                return False
            return True

        if not _is_valid_oem_candidate(key):
            logger.info(f"PAI search skipped invalid candidate: {raw_oem}")
            return {"status": "NO_MATCH", "primary": "", "alternates": [], "multi": False, "requires_click": False, "matches": []}

        if key in self.cross_cache:
            return self.cross_cache[key]

        # Persistent cache
        try:
            cached = self._cache_get("cross", key)
            if isinstance(cached, dict) and cached.get("__none__"):
                self.cross_cache[key] = None
                return None
            if isinstance(cached, dict) and cached.get("status"):
                self.cross_cache[key] = cached
                return cached
        except Exception:
            pass

        logger.info(f"PAI search OEM: {key}")

        candidates: list[tuple[str, dict[str, str]]] = [
            ("/search/index", {"term": key}),
            ("/search", {"term": key}),
            ("/search", {"q": key}),
            ("/search", {"query": key}),
            ("/search", {"keyword": key}),
            ("/search", {"s": key}),
            ("/search", {"type": "product", "q": key}),
            ("/search", {"type": "products", "q": key}),
        ]

        resp = None
        for path, params in candidates:
            try:
                resp = self._request("GET", _abs_url(self.base_url, path), params=params, timeout=20)
            except Exception:
                resp = None
                continue
            if resp.status_code == 200:
                break

        if resp is None or resp.status_code != 200:
            result = {"status": "NO_MATCH", "primary": "", "alternates": [], "multi": False, "requires_click": False, "matches": []}
            self.cross_cache[key] = result
            try:
                self._cache_set("cross", key, result)
            except Exception:
                pass
            return result

        # Build candidate detail URLs (up to 10).
        detail_urls: list[str] = []
        try:
            is_detail = "/product/" in str(getattr(resp, "url", "") or "")
        except Exception:
            is_detail = False

        # Initialize search_page_stock before conditional
        search_page_stock: dict[str, object] = {}

        if is_detail:
            try:
                detail_urls = [str(getattr(resp, "url", "") or "")]
            except Exception:
                detail_urls = []
        else:
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                # NEW: explicit no-results guard
                try:
                    if re.search(r"\b0\s+search\s+results\b", soup.get_text(" ", strip=True), re.IGNORECASE):
                        logger.info(f"PAI search OEM: {key} | Results found: [] | NO_MATCH")
                        result = {
                            "status": "NO_MATCH",
                            "primary": "",
                            "alternates": [],
                            "multi": False,
                            "requires_click": True,
                            "matches": [],
                        }
                        self.cross_cache[key] = result
                        try:
                            self._cache_set("cross", key, result)
                        except Exception:
                            pass
                        return result
                except Exception:
                    pass

                # Parse stock from search results page (visible before clicking detail)
                search_page_stock = {}
                try:
                    search_page_stock = parse_pai_stock(resp.text)
                    if search_page_stock:
                        logger.debug(f"[PAI Cross] Stock from search page: {search_page_stock}")
                except Exception:
                    search_page_stock = {}

                # Tight selector first, then fallback
                links = soup.select('.search-results a[href*="/product/"]')
                if not links:
                    links = soup.select('a[href*="/product/"]')
            except Exception:
                links = []

            seen: set[str] = set()
            for a in links:
                try:
                    href = str(a.get("href") or "").strip()
                except Exception:
                    href = ""
                if not href:
                    continue
                u = _abs_url(self.base_url, href)
                if not u or u in seen:
                    continue
                seen.add(u)
                detail_urls.append(u)
                if len(detail_urls) >= 10:
                    break

        if not detail_urls:
            result = {"status": "NO_MATCH", "primary": "", "alternates": [], "multi": False, "requires_click": True, "matches": []}
            self.cross_cache[key] = result
            try:
                self._cache_set("cross", key, result)
            except Exception:
                pass
            logger.info(f"PAI search OEM: {key} | Results found: [] | NO_MATCH")
            return result

        matches: list[dict] = []
        for u in detail_urls:
            try:
                r = self._request("GET", u, timeout=20)
            except Exception:
                continue
            if r.status_code != 200:
                continue

            html = ""
            try:
                html = str(r.text or "")
            except Exception:
                html = ""

            sku = None
            pai_oem = None
            cost = None
            try:
                sku = parse_pai_sku(html)
            except Exception:
                sku = None
            try:
                pai_oem = parse_pai_oem(html)
            except Exception:
                pai_oem = None
            try:
                cost = parse_pai_cost(html, part_number=key)
            except Exception:
                cost = None

            sku_norm = normalize_part_number(str(sku or ""))
            oem_norm = normalize_part_number(str(pai_oem or ""))

            # Parse stock info for this product
            stock_info = {}
            try:
                stock_info = parse_pai_stock(html)
            except Exception:
                stock_info = {}
            
            # Merge with search page stock if detail page stock is empty
            # Search page often has better stock info (quantities visible)
            if not stock_info and search_page_stock:
                stock_info = dict(search_page_stock)
            elif search_page_stock:
                # Prefer numeric quantities from search page over status strings
                for wh, val in search_page_stock.items():
                    if isinstance(val, int) and wh not in stock_info:
                        stock_info[wh] = val
                    elif wh not in stock_info:
                        stock_info[wh] = val
            
            # Parse image URLs for this product
            image_urls = []
            try:
                image_urls = parse_pai_images(html, self.base_url)
            except Exception:
                image_urls = []

            accepted = bool(sku_norm and oem_norm and oem_norm == key)
            reason = "exact" if accepted else "mismatch"
            if not sku_norm:
                reason = "no_sku"
            elif not oem_norm:
                reason = "no_oem"
            elif oem_norm != key:
                reason = "mismatch"

            logger.info(
                f"Detail SKU checked: {sku_norm or ''} | Detail OEM found: {oem_norm or ''} | Accepted: {'YES' if accepted else 'NO'} | Reason: {reason}"
            )

            if accepted:
                matches.append({
                    "sku": sku_norm,
                    "oem": oem_norm,
                    "url": u,
                    "cost": float(cost) if cost else None,
                    "stock": stock_info,
                    "image_urls": image_urls,
                    "in_stock": _is_in_stock(stock_info),
                })

        all_skus = [m.get("sku") for m in matches if m.get("sku")]
        logger.info(f"PAI search OEM: {key} | Results found: {all_skus}")

        links: dict[str, str] = {}
        try:
            for m in matches:
                s = str(m.get("sku") or "").strip()
                u = str(m.get("url") or "").strip()
                if s and u:
                    links[s] = u
        except Exception:
            links = {}

        if len(all_skus) == 1:
            m = matches[0]
            sku_norm = str(m.get("sku") or "")
            cost_val = m.get("cost")
            result = {
                "status": "EXACT",
                "primary": sku_norm,
                "alternates": [],
                "multi": False,
                "requires_click": True,
                "matches": [sku_norm],
                "detail_url": m.get("url"),
                "detail_oem": m.get("oem"),
                "cost": float(cost_val) if cost_val else None,
                "links": links,
            }
            self.cross_cache[key] = result
            try:
                self._cache_set("cross", key, result)
            except Exception:
                pass
            return result

        if len(all_skus) > 1:
            # Multi exact OEM matches: never auto-select.
            # Sort by in_stock status first (in-stock items first), then alphabetically
            matches_sorted = sorted(
                matches,
                key=lambda m: (not m.get("in_stock", False), str(m.get("sku") or ""))
            )
            
            skus_sorted = [str(m.get("sku") or "") for m in matches_sorted if m.get("sku")]
            alternates = skus_sorted[:10]
            
            # Build detailed match info with stock and images
            match_details = []
            for m in matches_sorted[:10]:
                match_details.append({
                    "sku": str(m.get("sku") or ""),
                    "cost": m.get("cost"),
                    "stock": m.get("stock", {}),
                    "in_stock": m.get("in_stock", False),
                    "url": m.get("url", ""),
                    "image_urls": m.get("image_urls", []),
                })
            
            # Count how many are in stock
            in_stock_count = sum(1 for m in match_details if m.get("in_stock"))
            
            result = {
                "status": "MULTI_MATCH",
                "primary": skus_sorted[0] if skus_sorted else "",
                "alternates": alternates,
                "multi": True,
                "requires_click": True,
                "matches": alternates,
                "match_details": match_details,  # NEW: detailed info per match
                "in_stock_count": in_stock_count,  # NEW: how many are in stock
                "links": links,
            }
            self.cross_cache[key] = result
            try:
                self._cache_set("cross", key, result)
            except Exception:
                pass
            return result

        result = {"status": "NO_MATCH", "primary": "", "alternates": [], "multi": False, "requires_click": True, "matches": []}
        self.cross_cache[key] = result
        try:
            self._cache_set("cross", key, result)
        except Exception:
            pass
        return result
