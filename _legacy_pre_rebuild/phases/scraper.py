import re
import shutil
import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from typing import List
from urllib.parse import urlparse
from playwright.async_api import TimeoutError, async_playwright
from bs4 import BeautifulSoup
from PIL import Image
import aiohttp

from utils.helpers import *
from models import Product, ScrapeResult, ErrorCode
from phases.description import generate_engaging_description  # Use AI version

PAGE_WAIT_MS = 1500
SKU_PREFIX = "JAKS-"
OUTPUT_ROOT = Path("output")


def _slug_for_path(s: str) -> str:
    """Convert string to filesystem-safe slug."""
    s = str(s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s or "unknown"


def get_sku_images_folder(category: str, manufacturer: str, sku: str) -> Path:
    """Return path for per-SKU images folder: output/<Category>/<Manufacturer>/<SKU>/images/"""
    cat = _slug_for_path(category)
    man = _slug_for_path(manufacturer)
    sku_clean = _slug_for_path(sku) or "unknown_sku"
    return OUTPUT_ROOT / cat / man / sku_clean / "images"


def _promote_stage(prod: object, stage: int) -> None:
    try:
        pr = getattr(prod, "pricing", None)
    except Exception:
        pr = None
    if not isinstance(pr, dict):
        pr = {}
        try:
            setattr(prod, "pricing", pr)
        except Exception:
            return
    try:
        cur = int(pr.get("stage") or 1)
    except Exception:
        cur = 1
    if stage > cur:
        pr["stage"] = int(stage)


def scrap_selected(
    rows: list[object],
    *,
    images_dir: Path,
    temp_dir: Path,
    generate_description: bool = True,
    logo_path: str | None = None,
) -> list[object]:
    """Phase 2: scrape a selected set of rows.

    This is a UI-agnostic helper intended for the "Scrape Selected" phase.
    It scrapes each row's URL and promotes successful scrapes to Stage 2.

    Accepted row types:
      - Product-like objects with `.url`
      - dicts with key "url"

    Returns a list of scraped product objects.
    """

    urls: list[str] = []
    for r in rows or []:
        u = ""
        try:
            u = str(getattr(r, "url", "") or "").strip()
        except Exception:
            u = ""
        if not u and isinstance(r, dict):
            u = str(r.get("url", "") or "").strip()
        if u:
            urls.append(u)

    async def _run() -> list[object]:
        out: list[object] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                try:
                    for url in urls:
                        page = await browser.new_page()
                        try:
                            result = await scrape_product(
                                page,
                                session,
                                url,
                                temp_dir,
                                "",
                                download_images=True,
                                generate_description=bool(generate_description),
                            )
                            prod = getattr(result, "product", None)
                            if prod is None:
                                continue

                            # Optional watermark step (best-effort)
                            # Use new organized folder structure: output/<Category>/<Manufacturer>/<SKU>/images/
                            try:
                                if logo_path:
                                    prod_cat = str(getattr(prod, "category", "") or "").strip() or "uncategorized"
                                    prod_man = str(getattr(prod, "manufacturer", "") or "").strip() or "unknown"
                                    prod_sku = str(getattr(prod, "sku", "") or "").strip() or str(getattr(prod, "handle", "") or "")
                                    out_dir = get_sku_images_folder(prod_cat, prod_man, prod_sku)
                                    out_dir.mkdir(parents=True, exist_ok=True)
                                    raw_dir = temp_dir / str(getattr(prod, "handle", "") or "")
                                    if raw_dir.exists():
                                        watermark_folder(raw_dir, out_dir, str(logo_path))
                            except Exception:
                                pass

                            _promote_stage(prod, 2)
                            out.append(prod)
                        finally:
                            try:
                                await page.close()
                            except Exception:
                                pass
                finally:
                    await browser.close()
        return out

    return asyncio.run(_run())


def normalize_product_url(url: str) -> str:
    """Return a stable URL string suitable for diffing listings.

    Strips query + fragment and normalizes trailing slashes.
    """

    try:
        u = str(url or "").strip()
        if not u:
            return ""
        p = urlparse(u)
        cleaned = p._replace(query="", fragment="").geturl()
        return str(cleaned).rstrip("/")
    except Exception:
        return str(url or "").strip()


def _derive_template_hint(kit_contents: list[str] | None, xrefs: list[str] | None) -> str:
    """Best-effort UI template hint.

    This is a deterministic fallback used when the engine can't infer a Shopify
    template suffix. It is NOT a Shopify template suffix; it's a human-visible
    hint derived from page content.
    """

    try:
        kc = [str(x or "").strip() for x in (kit_contents or [])]
    except Exception:
        kc = []
    kc = [x for x in kc if x]

    try:
        xr = [str(x or "").strip() for x in (xrefs or [])]
    except Exception:
        xr = []
    xr = [x for x in xr if x]

    items = kc or xr
    if not items:
        return "N/A"
    return ", ".join(items)


def _bs4_parser() -> str:
    """Prefer lxml if available, else fall back to the built-in html.parser."""
    try:
        import lxml  # type: ignore

        return "lxml"
    except Exception:
        return "html.parser"
MAX_IMAGES = 6


async def login_to_pai(playwright, username: str, password: str):
    """Login to PAI store and return logged-in context and page.

    Uses a persistent context directory so cookies/session survive between runs.
    """

    context_dir = Path("pai_session")
    ensure_dir(context_dir)

    # NOTE: Playwright's persistent context is launched from the browser type.
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(context_dir),
        headless=False,
        viewport={"width": 1280, "height": 720},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )

    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto("https://store.paiindustries.com/account/log-in", timeout=60000)

    # Fill login form. Selectors are best-effort (site may vary).
    await page.fill("input[name='email'], #email, input[type='email']", username)
    await page.fill("input[name='password'], #password, input[type='password']", password)
    await page.click("button[type='submit'], button:has-text('Log in'), button:has-text('Login')")

    # Wait for a post-login signal.
    try:
        await page.wait_for_selector(
            ".dashboard, a[href*='log-out'], a:has-text('Log out'), a:has-text('Logout')",
            timeout=30000,
        )
    except TimeoutError as e:
        raise ValueError("Login failed - check credentials or CAPTCHA") from e

    try:
        await context.storage_state(path=context_dir / "state.json")
    except Exception:
        pass

    return context, page


def _load_image_cache(cache_path: Path) -> dict:
    try:
        import json

        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("by_url", {})
            data.setdefault("meta", {})
            return data
    except FileNotFoundError:
        return {"by_url": {}, "meta": {}}
    except Exception:
        return {"by_url": {}, "meta": {}}
    return {"by_url": {}, "meta": {}}


def _save_image_cache(cache_path: Path, data: dict) -> None:
    import json

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(cache_path)

def infer_manufacturer_from_title(title: str) -> str:
    t = title.lower()
    if "detroit" in t: return "Detroit Diesel"
    if "cummins" in t: return "Cummins"
    if "caterpillar" in t or " cat " in t: return "Caterpillar"
    if "mack" in t: return "Mack"
    if "volvo" in t: return "Volvo"
    if "international" in t or "navistar" in t: return "International"
    return "Unknown"


def _clean_category(category: str, url: str) -> str:
    """Clean category string - preserve UI selection, only slugify if needed."""
    cat = str(category or "").strip()
    
    # If already looks like a proper title (has spaces, mixed case), keep it
    if cat and " " in cat and not cat.islower():
        return cat
    
    # If it looks like a URL slug, convert it
    if cat and (cat.startswith("all-") or (cat.islower() and "-" in cat)):
        return cat.replace("all-", "").replace("-", " ").title()
    
    # If empty, try to infer from URL
    if not cat:
        url_lower = url.lower()
        if "rebuild-kit" in url_lower or "engine-rebuild" in url_lower:
            return "Engine Rebuild Kits"
        elif "turbo" in url_lower:
            return "Turbochargers"
        elif "injector" in url_lower:
            return "Fuel Injectors"
        elif "cylinder-head" in url_lower:
            return "Cylinder Heads"
        elif "oil-cooler" in url_lower:
            return "Oil Coolers"
        else:
            return "Engine Parts"
    
    return cat or "Engine Parts"


def extract_part_number_from_title(title: str) -> str:
    """Extract part number from title.

    Patterns searched (in order):
    1. ERK pattern with dashes at start: "ERK-8039-001 | ..." → "ERK8039001"
    2. Part number in parentheses at end: "... New (1816626C1)" → "1816626C1"
    3. Part number at start followed by separator: "1808832C92 | New..." → "1808832C92"
    4. Part number in parentheses anywhere (including 215SB362 format): "(215SB362)" → "215SB362"
    5. Part number as comma-separated item: "..., MV1101001, ..." → "MV1101001"
    
    Returns empty string if no valid part number found.
    """
    if not title:
        return ""
    
    # Pattern 0: ERK pattern with dashes at start (ERK-8039-001 | ...)
    # Combine ERK-XXXX-XXX into ERKXXXXXXX
    m = re.match(r"^ERK[\-\s]*(\d{4,})[\-\s]*(\d{3})\s*[\|\-]", title, re.IGNORECASE)
    if m:
        return f"ERK{m.group(1)}{m.group(2)}".upper()
    
    # Pattern 1: Part number in parentheses at end of title
    # Example: "International/Navistar 7.3L Powerstroke Oil Cooler, New (1816626C1)"
    # Also catches (215SB362) format
    m = re.search(r"\(([A-Z0-9]{5,}[A-Z0-9\-]*)\)\s*$", title, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    
    # Pattern 2: Part number at start followed by separator (| or -)
    # Example: "1808832C92 | New, Navistar DT466/DTA466 1.25" Rotor Set"
    m = re.match(r"^([A-Z0-9]{6,}[A-Z0-9\-]*)\s*[\|\-]", title, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    
    # Pattern 3: Part number in parentheses anywhere
    # Example: "International/Navistar DT466, DT466E Oil Pump, New (1822326C93)"
    # Also catches (ERK8017501) and (215SB362) formats
    m = re.search(r"\(([A-Z0-9]{5,}[A-Z0-9\-]*)\)", title, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    
    # Pattern 4: Part number as comma-separated item in title
    # Example: "MP7/D11 Engine Rebuild Kit, MV1101001, MV1101-001, Mack/Volvo, New"
    # Look for alphanumeric tokens with letters AND digits, at least 7 chars
    parts = [p.strip() for p in title.split(",")]
    for part in parts:
        # Skip common non-PN words
        part_lower = part.lower()
        if part_lower in {"new", "reman", "remanufactured", "used", "oem"}:
            continue
        # Match alphanumeric part numbers like MV1101001, ISX119145, ERK8039001
        m = re.match(r"^([A-Z]{1,3}[0-9]{5,}[A-Z0-9\-]*)$", part, re.IGNORECASE)
        if m and len(m.group(1)) >= 7:
            return m.group(1).upper()
    
    return ""


def _extract_primary_secondary_parts_from_url(url: str) -> tuple[str, str]:
    """Extract primary/secondary part numbers from HHP product URL slugs.

    Rules:
    - If the slug starts with ERK-XXX-XXX pattern, combine into ERKXXXXXXX
    - Handle patterns like C15103-010 where suffix is a variant identifier
    - If the slug contains numeric tokens (digits-only):
      - First numeric token = primary
      - Last numeric token (if different) = secondary
    - If slug starts with a P-prefixed part (e.g. p016364), keep it as primary.
    - If no numeric tokens exist, fall back to the first plausible alphanumeric part token.
    """

    path = urlparse(url).path
    basename = Path(path).stem
    if not basename:
        return "", ""

    parts = [p for p in basename.split("-") if p]
    if not parts:
        return basename, ""

    def _is_p_prefixed_number(token: str) -> bool:
        return bool(re.match(r"^[pP]\d{4,}$", str(token or "").strip()))

    def _is_engineish(token: str) -> bool:
        t = str(token or "").strip().upper()
        if not t:
            return False
        if t in {"C15", "C13", "C12", "C16", "3406", "3406E", "ACERT", "ISX", "X15", "MP8", "D13", "D11", "D16"}:
            return True
        if len(t) <= 5 and any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t):
            return True
        return False

    def _is_candidate_alnum(token: str, *, allow_p_prefixed: bool = False) -> bool:
        token = str(token or "").strip()
        if not token:
            return False
        if (not allow_p_prefixed) and _is_p_prefixed_number(token):
            return False
        if len(token) < 4:
            return False
        if not token.isalnum():
            return False
        if _is_engineish(token):
            return False
        return any(ch.isdigit() for ch in token)

    first = parts[0]
    
    # Handle patterns like C15103-010 or C15103-010HP (alphanumeric followed by variant suffix)
    # These are common kit/variant identifiers that should NOT be split
    # e.g., "c15103-010-caterpillar" → "C15103-010" NOT just "C15103"
    # e.g., "c15107-010hp-caterpillar" → "C15107-010HP" (High Performance variant)
    if len(parts) >= 2:
        second = parts[1]
        # Check if first is alphanumeric part number and second is variant suffix:
        # - Pure numeric: 001-999 (e.g., "010")
        # - Numeric + letters: 010HP, 001A (e.g., "010hp" for High Performance)
        variant_match = re.match(r"^(\d{2,3})([a-zA-Z]{0,2})$", second)
        if (_is_candidate_alnum(first) and 
            variant_match and 
            not _is_engineish(first)):
            # Combine with dash preserved: C15107-010HP (matches PAI SKU format)
            combined = f"{first}-{second}".upper()
            # Look for secondary reference in remaining parts
            secondary = ""
            remaining = parts[2:]
            numeric_tokens = [p for p in remaining if str(p).strip().isdigit() and len(str(p).strip()) >= 5]
            if numeric_tokens:
                secondary = numeric_tokens[-1]
            return combined, secondary
    
    # Handle ERK-XXXX-XXX pattern (combine into single part number)
    # e.g., "erk-8039-001" → "ERK8039001"
    if first.upper() == "ERK" and len(parts) >= 3:
        combined = "".join(parts[:3]).upper()
        if re.match(r"^ERK\d{4,}$", combined):
            # Successfully combined ERK pattern
            secondary = ""
            # Look for secondary reference in remaining parts
            remaining = parts[3:]
            numeric_tokens = [p for p in remaining if str(p).strip().isdigit() and len(str(p).strip()) >= 5]
            if numeric_tokens:
                secondary = numeric_tokens[-1]
            return combined, secondary
    
    # If the slug starts with a P-prefixed part, keep it as primary.
    if _is_p_prefixed_number(first) and _is_candidate_alnum(first, allow_p_prefixed=True):
        primary = str(first)
        numeric_tokens = [p for p in parts if str(p).strip().isdigit()]
        secondary = ""
        if numeric_tokens:
            last_num = str(numeric_tokens[-1]).strip()
            if last_num and last_num != primary:
                secondary = last_num
        return primary, secondary

    # Primary/secondary from numeric tokens (digits-only) when present.
    # Require at least 5 digits to avoid false positives like "001"
    numeric_tokens = [str(p).strip() for p in parts if str(p).strip().isdigit() and len(str(p).strip()) >= 5]
    if numeric_tokens:
        primary = numeric_tokens[0]
        last_num = numeric_tokens[-1]
        secondary = last_num if last_num and last_num != primary else ""
        return primary, secondary

    # Fallback: preserve legacy behavior for alphanumeric part slugs.
    for token in parts:
        if _is_candidate_alnum(token):
            return str(token), ""

    for token in reversed(parts):
        if _is_candidate_alnum(token):
            return str(token), ""

    return str(first), ""

def extract_primary_part_from_url(url: str) -> str:
    primary, _secondary = _extract_primary_secondary_parts_from_url(url)
    return primary


def extract_secondary_part_from_url(url: str) -> str:
    """Return the trailing part token (reference) when present.

    Example:
      "5927284-...-2486744" -> 2486744

    Returns "" when we can't confidently determine a distinct trailing token.
    """

    _primary, secondary = _extract_primary_secondary_parts_from_url(url)
    return secondary

async def scrape_product(
    page,
    session,
    url,
    temp_dir: Path,
    category: str,
    *,
    site: str = "hhp",
    username: str | None = None,
    password: str | None = None,
    playwright=None,
    download_images: bool = True,
    generate_description: bool = True,
    image_download_semaphore=None,
):
    errors: list[str] = []
    warnings: list[str] = []

    site = str(site or "hhp").strip().lower()
    try:
        print(f"SCRAPE_START {url}")
    except Exception:
        pass
    print(f"Scraping: {url}")

    # PAI mode: login + cost scrape (minimal fields; does not affect OEM manufacturer logic elsewhere).
    if site == "pai":
        if not (username and password):
            raise ValueError("PAI requires username/password")

        owns_playwright = False
        context = None
        try:
            if playwright is None:
                playwright = await async_playwright().start()
                owns_playwright = True

            context, logged_page = await login_to_pai(playwright, str(username), str(password))
            await logged_page.goto(url, timeout=90000)
            await logged_page.wait_for_timeout(PAGE_WAIT_MS)
            html = await logged_page.content()
            soup = BeautifulSoup(html, _bs4_parser())

            title_el = soup.select_one("h1, h1.product_title")
            title = normalize(title_el.text) if title_el else "PAI Product"

            # Extract PAI SKU and OEM from page
            pai_sku = None
            oem_part = None
            try:
                from sources.pai_client import parse_pai_sku, parse_pai_oem
                pai_sku = parse_pai_sku(html)
                oem_part = parse_pai_oem(html)
            except Exception:
                pass

            # Use PAI SKU as base_sku, fallback to URL extraction
            base_sku = pai_sku or extract_primary_part_from_url(url) or "PAI"
            handle = re.sub(r"[^a-z0-9\-]+", "-", base_sku.lower()).strip("-") or "pai"
            sku = f"{SKU_PREFIX}{base_sku}"

            # Price/cost extraction (use the same anchor as our PAI parser if present).
            cost_val = 0.0
            try:
                from sources.pai_client import parse_pai_cost

                parsed = parse_pai_cost(html, part_number=base_sku)
                if parsed:
                    cost_val = float(parsed)
            except Exception:
                cost_val = 0.0
            if cost_val <= 0:
                errors.append(ErrorCode.ERROR_NO_COST.value)

            prod = Product(
                sku=sku,
                handle=handle,
                url=url,
                title=title,
                description_jaks_html="",
                brand="",
                engine="",
                pricing={
                    "price": 0.0,
                    "core_charge": 0.0,
                    "currency": "USD",
                    "supplier": "PAI",
                    "cost_source": "PAI",
                },
                image_count_raw=0,
                image_count_watermarked=0,
                scraped_at=now_stamp(),
                category=category,
                manufacturer="",
                unknown_part=False,
                cost=float(cost_val),
                supplier="PAI",
                supplier_part_number=pai_sku or "",
                oem_part_number=oem_part or "",
            )
            return ScrapeResult(product=prod, errors=errors, warnings=warnings)
        finally:
            try:
                if context is not None:
                    await context.close()
            except Exception:
                pass
            try:
                if owns_playwright and playwright is not None:
                    await playwright.stop()
            except Exception:
                pass

    # HHP mode: comprehensive error wrapping
    try:
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                break
            except TimeoutError:
                retries += 1
                msg = f"Timeout on {url} - retry {retries}/{max_retries}"
                warnings.append(msg)
                print(msg)
                await asyncio.sleep(2 ** retries)  # Exponential backoff
            except Exception as e:
                errors.append(f"Navigation exception: {e}")
                return ScrapeResult(product=None, errors=errors, warnings=warnings)

        if retries == max_retries:
            errors.append(f"Failed to load {url} after {max_retries} retries")
            return ScrapeResult(product=None, errors=errors, warnings=warnings)

        try:
            await page.wait_for_selector(".woocommerce-product-gallery", timeout=10000)
        except TimeoutError:
            pass
        await page.wait_for_timeout(PAGE_WAIT_MS * 2)  # Extra wait

        soup = BeautifulSoup(await page.content(), _bs4_parser())

        title_el = soup.select_one("h1.product_title")
        if not title_el:
            errors.append(ErrorCode.ERROR_NO_TITLE.value)
            return ScrapeResult(product=None, errors=errors, warnings=warnings)

        title = normalize(title_el.text)

        manufacturer = infer_manufacturer_from_title(title)
        brand = manufacturer

        xrefs = extract_xrefs(title, soup)

        primary_part = extract_primary_part_from_url(url)
        secondary_part = extract_secondary_part_from_url(url)
        
        # Try to extract part number from title - more reliable than URL
        title_part_number = extract_part_number_from_title(title)

        # Best-effort OEM PN extraction from the product page text.
        # (Fallback to secondary URL token when missing.)
        oem_part_number = ""
        try:
            text = soup.get_text(" ", strip=True)
            # Require at least one digit to avoid capturing words like "Cross" from "OEM Cross References"
            m = re.search(r"\bOEM\b\s*(?:#|:)?\s*([A-Z0-9\-]*\d[A-Z0-9\-]*)", text, re.IGNORECASE)
            if m:
                candidate = str(m.group(1) or "").strip()
                # Additional validation: must be 4+ chars and not a common word
                invalid_words = {"cross", "references", "reference", "part", "number", "parts"}
                if len(candidate) >= 4 and candidate.lower() not in invalid_words:
                    oem_part_number = candidate
        except Exception:
            oem_part_number = ""
        oem_part_number = oem_part_number or str(secondary_part or "").strip()
        bad_starts = {"x15", "isx", "cummins", "engine", "kit", "rebuild", "inframe", "single", "dual"}

        def _is_engineish_primary(token: str) -> bool:
            t = str(token or "").strip().upper()
            if not t:
                return False
            if t in {"C15", "C13", "C12", "C16", "3406", "3406E", "ACERT", "ISX", "X15", "MP8", "D13", "D11", "D16"}:
                return True
            if len(t) <= 5 and any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t):
                return True
            return False

        def _is_word_not_part_number(token: str) -> bool:
            """Return True if token looks like a word rather than a part number."""
            t = str(token or "").strip().lower()
            if not t:
                return True
            # All letters = definitely a word (e.g., "kitsealcrnk")
            if t.isalpha():
                return True
            # Very few digits relative to letters = probably a word (e.g., "dt466e")
            digit_count = sum(1 for c in t if c.isdigit())
            alpha_count = sum(1 for c in t if c.isalpha())
            if alpha_count > 0 and digit_count / (digit_count + alpha_count) < 0.4:
                return True
            return False

        # Use title-extracted part number if URL-based one is bad
        url_part_is_bad = (
            not primary_part 
            or len(primary_part) < 4 
            or primary_part.lower().split("-")[0] in bad_starts
            or _is_word_not_part_number(primary_part)
        )
        
        if url_part_is_bad and title_part_number:
            msg = f"Bad primary from URL ('{primary_part}') → using title part '{title_part_number}'"
            warnings.append(msg)
            print(f"Info: {msg} for {title[:60]}...")
            primary_part = title_part_number
        elif url_part_is_bad:
            msg = f"Bad primary from URL ('{primary_part}') → using best xref"
            warnings.append(msg)
            print(f"Warning: {msg} for {title[:60]}...")
            best_ref = next((ref for ref in xrefs if ref.lower().startswith("m-")), None)
            if not best_ref:
                best_ref = next(
                    (
                        ref
                        for ref in xrefs
                        if len(ref.replace("-", "")) >= 7 and ref.replace("-", "").isalnum()
                    ),
                    None,
                )
            primary_part = best_ref or "UNKNOWN_PART"  # Better fallback to avoid dupes

        unknown_part = primary_part == "UNKNOWN_PART"
        if unknown_part:
            errors.append(ErrorCode.ERROR_BAD_SKU.value)
            print(f"ERROR: UNKNOWN_PART for {url} — blocking product selection")

        part_needs_review = _is_engineish_primary(primary_part)
        if part_needs_review and ErrorCode.ERROR_BAD_SKU.value not in errors:
            errors.append(ErrorCode.ERROR_BAD_SKU.value)
            warnings.append(f"Primary part looks like an engine model ('{primary_part}') — review required")

        base_sku = primary_part
        handle = re.sub(r"[^a-z0-9\-]+", "-", base_sku.lower()).strip("-")
        sku = f"{SKU_PREFIX}{base_sku}"

        attrs = parse_attrs(soup)
        engine = infer_engine_from_title(title)
        price, core, _ = extract_price_and_core(soup)
        price = price if price is not None else 0.0
        if price <= 0:
            errors.append(ErrorCode.ERROR_NO_PRICE.value)

        cpl_list = extract_cpl_list(soup)
        cpl_prefix = assign_cpl_prefix(brand, engine, cpl_list)
        kit_contents = extract_kit_contents(soup, title)
        engine_model_tags = extract_engine_model_tags(soup, manufacturer)

        if generate_description:
            jaks_desc = generate_engaging_description(
                brand,
                engine,
                kit_contents,
                attrs.get("dimensions", ""),
                title,
                xrefs=xrefs,
                cpl_list=cpl_list,
            )
        else:
            jaks_desc = ""

        def _normalize_image_url(u: str) -> str:
            """Normalize image URL to remove CDN prefixes for proper deduplication.
            
            WordPress sites often serve the same image via multiple URLs:
            - https://highwayandheavyparts.com/wp-content/uploads/image.jpg
            - https://i0.wp.com/highwayandheavyparts.com/wp-content/uploads/image.jpg
            
            This normalizes them to the canonical form without CDN prefix.
            """
            u = (u or "").strip()
            if not u:
                return u
            # Remove WordPress CDN prefix (i0.wp.com, i1.wp.com, etc.)
            import re
            u = re.sub(r'^https?://i\d+\.wp\.com/', 'https://', u)
            # Remove query params
            u = u.split('?')[0]
            return u

        def _is_probably_product_image_url(u: str) -> bool:
            u = (u or "").strip()
            if not u:
                return False
            low = u.lower()
            # Must look like an image URL or be from uploads
            if not (
                low.endswith((".jpg", ".jpeg", ".png", ".webp"))
                or "wp-content/uploads" in low
                or "cdn" in low
            ):
                return False
            # Filter common site chrome images
            bad_tokens = (
                "logo", "badge", "icon", "sprite", "payment", "paypal",
                "sezzle", "affirm", "klarna", "chat", "favicon", "mainlogo",
                "placeholder", "woocommerce-placeholder", "gravatar",
            )
            if any(tok in low for tok in bad_tokens):
                return False
            # Skip small thumbnail sizes
            if any(x in low for x in ["-150x", "-100x", "-75x", "-50x", "-32x", "thumb"]):
                return False
            return True

        # Comprehensive image discovery with multiple strategies
        img_urls = []
        
        # JavaScript to extract image URLs from various sources
        js_extract_images = """() => {
            const urls = new Set();
            const push = (u) => {
                if (!u) return;
                u = String(u).trim();
                if (!u || u === '#' || u.startsWith('javascript:') || u.startsWith('data:')) return;
                urls.add(u.split('?')[0]);
            };
            
            // Strategy 1: ALL anchors linking to image files (most reliable for HHP)
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href || '';
                if (/\\.(jpe?g|png|webp)$/i.test(href) && href.includes('uploads')) {
                    push(href);
                }
            });
            
            // Strategy 2: WooCommerce gallery figures/images
            document.querySelectorAll('.woocommerce-product-gallery__image, .woocommerce-product-gallery figure, figure.woocommerce-product-gallery__image').forEach(fig => {
                const a = fig.querySelector('a');
                const img = fig.querySelector('img');
                if (a) push(a.href);
                if (img) {
                    push(img.getAttribute('data-large_image'));
                    push(img.getAttribute('data-src'));
                    push(img.getAttribute('data-lazy-src'));
                    push(img.getAttribute('data-original'));
                    push(img.src);
                    const srcset = img.srcset || '';
                    srcset.split(',').forEach(s => push(s.trim().split(' ')[0]));
                }
            });
            
            // Strategy 3: Direct gallery selectors
            document.querySelectorAll('.woocommerce-product-gallery a, .woocommerce-product-gallery img').forEach(el => {
                if (el.tagName === 'A') push(el.href);
                else {
                    push(el.getAttribute('data-large_image'));
                    push(el.getAttribute('data-src'));
                    push(el.src);
                }
            });
            
            // Strategy 4: Flexslider and other sliders
            document.querySelectorAll('.flex-viewport img, .flexslider img, .flex-control-thumbs img, .slick-slide img, .swiper-slide img').forEach(img => {
                push(img.getAttribute('data-src') || img.src);
            });
            
            // Strategy 5: Generic product images with uploads path
            document.querySelectorAll('.product img, .single-product img, div.product img').forEach(img => {
                const src = img.getAttribute('data-large_image') || img.getAttribute('data-src') || img.src;
                if (src && src.includes('uploads')) push(src);
            });
            
            // Strategy 6: wp-post-image class
            document.querySelectorAll('img.wp-post-image, img.attachment-woocommerce_single, img.attachment-shop_single').forEach(img => {
                push(img.getAttribute('data-large_image'));
                push(img.getAttribute('data-src'));
                push(img.src);
            });
            
            // Strategy 7: Any img with src containing uploads
            document.querySelectorAll('img[src*="uploads"]').forEach(img => {
                push(img.src);
            });
            
            return Array.from(urls);
        }"""
        
        try:
            img_urls = await page.evaluate(js_extract_images)
            img_urls = [u for u in (img_urls or []) if _is_probably_product_image_url(u)]
            print(f"[IMG DEBUG] {handle}: JS evaluate found {len(img_urls)} candidates")
        except Exception as e:
            print(f"[IMG DEBUG] {handle}: JS evaluate failed: {e}")
            img_urls = []
        
        # Fallback: BeautifulSoup parsing if JS found nothing
        if not img_urls:
            try:
                candidates: list[str] = []
                
                # Strategy 1: ALL anchors linking to image files (most reliable for HHP)
                for a in soup.select("a[href]"):
                    href = str(a.get("href") or "").strip()
                    if href and "/uploads/" in href and any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                        candidates.append(href.split("?")[0])
                
                # Strategy 2: Gallery images
                for img in soup.select(".woocommerce-product-gallery img, figure img, .product img"):
                    for attr in ("data-large_image", "data-src", "data-lazy-src", "data-original", "src"):
                        u = str(img.get(attr) or "").strip()
                        if u:
                            candidates.append(u.split("?")[0])
                    srcset = str(img.get("srcset") or "")
                    if srcset:
                        for part in srcset.split(","):
                            src = part.strip().split(" ")[0]
                            if src:
                                candidates.append(src.split("?")[0])
                
                # Strategy 3: Any img with uploads in src
                for img in soup.select("img[src*='uploads']"):
                    src = str(img.get("src") or "").strip()
                    if src:
                        candidates.append(src.split("?")[0])
                
                img_urls = [u for u in candidates if _is_probably_product_image_url(u)]
                img_urls = list(dict.fromkeys(img_urls))  # Dedupe preserving order
                print(f"[IMG DEBUG] {handle}: BeautifulSoup fallback found {len(img_urls)} candidates")
            except Exception as e:
                print(f"[IMG DEBUG] {handle}: BeautifulSoup failed: {e}")
                img_urls = []

        # Dedupe by normalized URL (handles CDN variants like i0.wp.com)
        # Preserve order while keeping the non-CDN URL when both exist
        seen_normalized = {}
        deduped_urls = []
        for u in img_urls:
            norm = _normalize_image_url(u)
            if norm not in seen_normalized:
                seen_normalized[norm] = u
                # Prefer non-CDN URL (direct highwayandheavyparts.com)
                if 'i0.wp.com' not in u and 'i1.wp.com' not in u and 'i2.wp.com' not in u:
                    deduped_urls.append(u)
                else:
                    # Check if we already have a non-CDN version
                    existing = seen_normalized.get(norm)
                    if existing and 'wp.com/' not in existing:
                        continue  # Skip CDN version, we have direct
                    deduped_urls.append(u)
            else:
                # We've seen this normalized URL; prefer non-CDN
                existing = seen_normalized[norm]
                if 'wp.com/' in existing and 'wp.com/' not in u:
                    # Replace CDN URL with direct URL
                    try:
                        idx = deduped_urls.index(existing)
                        deduped_urls[idx] = u
                        seen_normalized[norm] = u
                    except ValueError:
                        pass
        img_urls = deduped_urls
        
        print(f"[IMG DEBUG] {handle}: Final candidate count = {len(img_urls)}")
        for i, u in enumerate(img_urls[:6], 1):
            print(f"  [{i}] {u[:100]}...")

        raw_dir = None
        count = 0
        print(f"[IMG DEBUG] {handle}: download_images={download_images}, temp_dir={temp_dir}")
        if download_images:
            raw_dir = temp_dir / handle
            print(f"[IMG DEBUG] {handle}: raw_dir={raw_dir}")
            if raw_dir.exists():
                shutil.rmtree(raw_dir, ignore_errors=True)
            ensure_dir(raw_dir)
            print(f"[IMG DEBUG] {handle}: raw_dir created? {raw_dir.exists()}")

            output_dir = temp_dir.parent
            cache_path = output_dir / "_image_cache.json"
            store_dir = output_dir / "_image_store"
            ensure_dir(store_dir)

            cache = _load_image_cache(cache_path)
            by_url = cache.get("by_url", {}) if isinstance(cache.get("by_url", {}), dict) else {}
            meta = cache.get("meta", {}) if isinstance(cache.get("meta", {}), dict) else {}

            if image_download_semaphore is None:
                for i, img_url in enumerate(img_urls, 1):
                    try:
                        # URL cache hit: copy from canonical store without network.
                        cached_sha = by_url.get(img_url)
                        if cached_sha:
                            cached_sha = str(cached_sha)
                            info = meta.get(cached_sha) if isinstance(meta, dict) else None
                            cached_path = None
                            if isinstance(info, dict) and info.get("valid"):
                                cached_path = info.get("path")
                            if cached_path and Path(cached_path).exists():
                                dest = raw_dir / f"{handle}_{i:02d}.jpg"
                                shutil.copyfile(cached_path, dest)
                                count += 1
                                continue
                            # Cache miss for valid path but we have SHA - need to re-download
                        
                        # Download the image (either no cache or cache miss)
                        async with session.get(img_url, timeout=40) as r:
                            if r.status != 200:
                                print(f"  [IMG SKIP] HTTP {r.status}: {img_url[:60]}")
                                continue
                            data = await r.read()
                            if len(data) < 10000:
                                print(f"  [IMG SKIP] Too small ({len(data)} bytes): {img_url[:60]}")
                                continue

                        sha = hashlib.sha256(data).hexdigest()
                        by_url[img_url] = sha
                        info = meta.get(sha)
                        if isinstance(info, dict) and (info.get("valid") is False):
                            continue
                        cached_path = None
                        if isinstance(info, dict) and info.get("valid"):
                            cached_path = info.get("path")
                        if cached_path and Path(cached_path).exists():
                            dest = raw_dir / f"{handle}_{i:02d}.jpg"
                            shutil.copyfile(cached_path, dest)
                            count += 1
                            continue

                        # Validate image once from bytes (no extra write), then save canonical + copy.
                        try:
                            with Image.open(BytesIO(data)) as img:
                                w, h = img.size
                        except Exception:
                            meta[sha] = {"valid": False}
                            continue

                        if min(w, h) < 500 or max(w / h, h / w) > 2.5:
                            print(f"  [IMG SKIP] Bad dimensions ({w}x{h}): {img_url[:60]}")
                            meta[sha] = {"valid": False, "w": int(w), "h": int(h)}
                            continue

                        canonical = store_dir / f"{sha}.jpg"
                        if not canonical.exists():
                            canonical.write_bytes(data)
                        meta[sha] = {"valid": True, "w": int(w), "h": int(h), "path": str(canonical)}
                        dest = raw_dir / f"{handle}_{i:02d}.jpg"
                        shutil.copyfile(canonical, dest)
                        count += 1
                    except Exception as e:
                        print(f"Image skipped ({img_url}): {e}")
            else:
                async with image_download_semaphore:
                    for i, img_url in enumerate(img_urls, 1):
                        try:
                            cached_sha = by_url.get(img_url)
                            if cached_sha:
                                cached_sha = str(cached_sha)
                                info = meta.get(cached_sha) if isinstance(meta, dict) else None
                                cached_path = None
                                if isinstance(info, dict) and info.get("valid"):
                                    cached_path = info.get("path")
                                if cached_path and Path(cached_path).exists():
                                    dest = raw_dir / f"{handle}_{i:02d}.jpg"
                                    shutil.copyfile(cached_path, dest)
                                    count += 1
                                    continue
                                # Cache miss for valid path but we have SHA - need to re-download

                            # Download the image (either no cache or cache miss)
                            async with session.get(img_url, timeout=40) as r:
                                if r.status != 200:
                                    print(f"  [IMG SKIP] HTTP {r.status}: {img_url[:60]}")
                                    continue
                                data = await r.read()
                                if len(data) < 10000:
                                    print(f"  [IMG SKIP] Too small ({len(data)} bytes): {img_url[:60]}")
                                    continue

                            sha = hashlib.sha256(data).hexdigest()
                            by_url[img_url] = sha
                            info = meta.get(sha)
                            if isinstance(info, dict) and (info.get("valid") is False):
                                continue
                            cached_path = None
                            if isinstance(info, dict) and info.get("valid"):
                                cached_path = info.get("path")
                            if cached_path and Path(cached_path).exists():
                                dest = raw_dir / f"{handle}_{i:02d}.jpg"
                                shutil.copyfile(cached_path, dest)
                                count += 1
                                continue

                            try:
                                with Image.open(BytesIO(data)) as img:
                                    w, h = img.size
                            except Exception:
                                meta[sha] = {"valid": False}
                                continue

                            if min(w, h) < 500 or max(w / h, h / w) > 2.5:
                                print(f"  [IMG SKIP] Bad dimensions ({w}x{h}): {img_url[:60]}")
                                meta[sha] = {"valid": False, "w": int(w), "h": int(h)}
                                continue

                            canonical = store_dir / f"{sha}.jpg"
                            if not canonical.exists():
                                canonical.write_bytes(data)
                            meta[sha] = {"valid": True, "w": int(w), "h": int(h), "path": str(canonical)}
                            dest = raw_dir / f"{handle}_{i:02d}.jpg"
                            shutil.copyfile(canonical, dest)
                            count += 1
                        except Exception as e:
                            print(f"Image skipped ({img_url}): {e}")

            cache["by_url"] = by_url
            cache["meta"] = meta
            try:
                _save_image_cache(cache_path, cache)
            except Exception:
                pass

            actual_images = sorted(raw_dir.glob("*.jpg"))[:MAX_IMAGES]
            for idx, img_path in enumerate(actual_images, 1):
                new_path = raw_dir / f"{handle}_{idx:02d}.jpg"
                if img_path != new_path:
                    img_path.rename(new_path)
            print(f"[IMG DEBUG] {handle}: Final count = {count} valid images in {raw_dir}")
            print(f"[IMG DEBUG] {handle}: raw_dir exists? {raw_dir.exists()}, files: {list(raw_dir.glob('*.jpg'))[:3] if raw_dir.exists() else []}")
        else:
            # In validate-only mode we don't download, but we still want to know if
            # the product page appears to have images.
            count = min(len(img_urls), MAX_IMAGES)

        if count <= 0:
            errors.append(ErrorCode.ERROR_NO_IMAGES.value)

        pricing = {
            "price": price,
            "core_charge": core if core is not None else 0.0,
            "currency": "USD",
        }
        # Phase 2 (Scrape) must always advance Stage to 2 for completed scrape attempts.
        # Validation may annotate flags/errors but must not block stage advancement.
        pricing["stage"] = 2
        if part_needs_review:
            pricing["part_needs_review"] = True
            pricing["part_review_reason"] = f"Primary part looks like engine model: {str(primary_part)}"

        try:
            from utils.helpers import canonical_oem_group

            oem_group = canonical_oem_group(manufacturer)
        except Exception:
            oem_group = manufacturer

        prod = Product(
            sku=sku,
            handle=handle,
            url=url,
            title=title,
            description_jaks_html=jaks_desc,
            brand=brand,
            engine=engine,
            cpl_prefix=cpl_prefix,
            dimensions=attrs.get("dimensions", ""),
            xrefs=xrefs,
            pricing=pricing,
            image_count_raw=count,
            image_count_watermarked=0,
            scraped_at=now_stamp(),
            cpl_list=cpl_list,
            kit_contents=kit_contents,
            engine_model_tags=engine_model_tags,
            category=_clean_category(category, url),
            manufacturer=manufacturer,
            primary_pn=str(primary_part or "").strip(),
            secondary_pn=str(secondary_part or "").strip(),
            oem_pn=str(oem_part_number or "").strip(),
            primary_part_number=str(primary_part or "").strip(),
            secondary_part_number=str(secondary_part or "").strip(),
            oem_part_number=str(oem_part_number or "").strip(),
            supplier="HHP",
            oem_group=str(oem_group or "").strip(),
            unknown_part=unknown_part,
            weight=attrs.get("weight", ""),
        )

        try:
            setattr(prod, "stage", 2)
        except Exception:
            pass

        # Template hint (UI-visible fallback; not a Shopify template suffix)
        try:
            setattr(prod, "template", _derive_template_hint(kit_contents, xrefs))
        except Exception:
            pass

        try:
            if isinstance(prod.pricing, dict) and "template" not in prod.pricing:
                prod.pricing["template"] = str(getattr(prod, "template", "") or "").strip()
        except Exception:
            pass

        try:
            print(
                f"SCRAPE_DONE {str(getattr(prod, 'sku', '') or '').strip()} stage={str((getattr(prod, 'pricing', {}) or {}).get('stage') if isinstance(getattr(prod, 'pricing', None), dict) else '')}"
            )
        except Exception:
            pass

        return ScrapeResult(product=prod, errors=errors, warnings=warnings)
    except Exception as e:
        # Comprehensive error handler: ANY unhandled exception in scrape returns structured failure
        errors.append(f"Scrape exception: {str(e)}")
        print(f"SCRAPE_FAILED (exception) {url}: {e}")
        return ScrapeResult(product=None, errors=errors, warnings=warnings)