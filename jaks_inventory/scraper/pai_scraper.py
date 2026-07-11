"""Playwright-based PAI portal scraper.

Searches portal.pai.com for parts, extracts pricing, stock, and details.
Reuses persistent browser profile for authenticated session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SESSION_DIR = os.path.join(_ROOT, "pai_portal_session")
PAI_BASE = "https://portal.pai.com"


# Cache subfolders inside SESSION_DIR that often go stale across Chromium
# upgrades (Playwright bumps the bundled Chrome version) and trigger
# "Target page, context or browser has been closed" / exit-code 33 on launch.
# We blow these away before retrying a failed launch — Chrome rebuilds them
# automatically and the login session in Default/ is preserved.
_PAI_STALE_CACHE_SUBDIRS = (
    "ShaderCache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "Snapshots",
    "Crashpad",
    "BrowserMetrics",
    "component_crx_cache",
    "extensions_crx_cache",
    "CrashpadMetrics-active.pma",
)


def _clear_pai_stale_caches() -> list[str]:
    """Remove known-stale Chromium cache dirs inside the PAI session.

    Returns the list of subpaths that were deleted (best-effort).
    """
    import shutil
    cleared: list[str] = []
    if not os.path.isdir(SESSION_DIR):
        return cleared
    for name in _PAI_STALE_CACHE_SUBDIRS:
        p = os.path.join(SESSION_DIR, name)
        if not os.path.exists(p):
            continue
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            cleared.append(name)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("[PAI] Could not clear %s: %s", p, exc)
    return cleared


def _launch_pai_browser(pw, *, headless: bool):
    """Launch the persistent PAI browser context with auto-recovery.

    Chromium occasionally fails to start against a profile dir that survived
    a Playwright upgrade (stale ShaderCache / Snapshots paths). When the
    first launch raises, we wipe known stale subfolders and retry once
    before giving up.
    """
    launch_kwargs = dict(
        user_data_dir=SESSION_DIR,
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--disable-infobars",
            "--disable-gpu-shader-disk-cache",
        ],
        viewport={"width": 1280, "height": 900},
        ignore_default_args=["--enable-automation"],
    )
    try:
        return pw.chromium.launch_persistent_context(**launch_kwargs)
    except Exception as first_err:
        cleared = _clear_pai_stale_caches()
        logger.warning(
            "[PAI] Initial browser launch failed (%s). Cleared %s and retrying.",
            first_err, cleared,
        )
        return pw.chromium.launch_persistent_context(**launch_kwargs)


@dataclass
class PAIPartResult:
    """Single PAI search result with pricing and stock info."""

    sku: str = ""
    description: str = ""
    your_price: float = 0.0
    list_price: float = 0.0
    oem_number: str = ""
    product_group: str = ""
    warranty: str = ""
    weight: str = ""
    upc: str = ""
    sell_pack: str = ""
    image_url: str = ""
    image_urls: list[str] = field(default_factory=list)
    detail_url: str = ""
    stock: dict[str, object] = field(default_factory=dict)
    in_stock: bool = False
    total_available: int = 0
    not_available: bool = False  # True when PAI STOCK says "Not Available" (vs warehouse out-of-stock)

    # Alternate / substitute part offered by PAI in the "Alternate" panel of the
    # detail page. PAI surfaces this when the searched part has a recommended
    # replacement (supersession or aftermarket equivalent). Captured here as a
    # cross-reference so the enrichment pipeline can offer it as a fallback.
    alternate_sku: str = ""
    alternate_description: str = ""
    alternate_your_price: float = 0.0
    alternate_in_stock: bool = False
    alternate_url: str = ""


def search_pai_portal(
    part_number: str,
    *,
    headless: bool = True,
    on_status=None,
    fetch_detail: bool = True,
    max_results: int = 5,
) -> list[PAIPartResult]:
    """Search PAI portal for a part number and return structured results.

    Uses persistent Playwright browser profile at pai_portal_session/.
    The user should be logged into portal.pai.com already.
    """
    from playwright.sync_api import sync_playwright

    def status(msg):
        if on_status:
            on_status(msg)
        logger.info(f"[PAI Scraper] {msg}")

    pn = str(part_number or "").strip()
    if not pn:
        return []

    status(f"Searching PAI for '{pn}'...")
    results: list[PAIPartResult] = []

    os.makedirs(SESSION_DIR, exist_ok=True)

    with sync_playwright() as pw:
        # Use Playwright's bundled Chromium to avoid user_data_dir lock conflicts
        # when the user has Chrome open (causes exit code 21). Auto-recovers
        # from stale-cache failures by wiping cache subdirs and retrying.
        browser = _launch_pai_browser(pw, headless=headless)

        page = browser.pages[0] if browser.pages else browser.new_page()

        try:
            # Navigate to search
            search_url = f"{PAI_BASE}/search/index?term={pn}&submitbtn=search"
            status("Navigating to PAI portal...")
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(1)

            # Check if redirected to login
            current_url = page.url.lower()
            if "log-in" in current_url or "login" in current_url or "entrance" in current_url:
                status("PAI session expired — attempting login...")
                logged_in, login_msg = _attempt_login(page)
                if not logged_in:
                    status(login_msg or "PAI login failed.")
                    return []
                # Retry search after login
                page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(1)

            # Dismiss any overlays
            _dismiss_overlays(page)

            # Wait for page to fully render
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)

            # Check if directly on a product detail page (single result redirect)
            if "/product/" in page.url:
                status("Single result — parsing detail page...")
                result = PAIPartResult()
                sku_match = re.search(r"/product/([A-Z0-9\-]+)", page.url, re.IGNORECASE)
                if sku_match:
                    result.sku = sku_match.group(1)
                result.detail_url = page.url
                _parse_detail_page(page, result)
                results = [result]
            else:
                # Parse search results page
                results = _parse_search_results(page, max_results)
                status(f"Found {len(results)} PAI results")

            # Fetch detail pages for stock info
            if fetch_detail and results:
                for i, result in enumerate(results[:3]):
                    if result.detail_url:
                        status(f"Fetching details for {result.sku} ({i+1}/{min(len(results), 3)})...")
                        try:
                            page.goto(result.detail_url, timeout=30000, wait_until="domcontentloaded")
                            time.sleep(1.5)
                            page.wait_for_load_state("networkidle", timeout=10000)
                            _parse_detail_page(page, result)
                        except Exception as e:
                            logger.warning(f"[PAI Scraper] Detail fetch failed for {result.sku}: {e}")
                        time.sleep(0.5)
            elif not results:
                # Dump search page HTML for debugging (overwrite stale dumps).
                try:
                    html = page.content()
                    dump_path = os.path.join(SESSION_DIR, "last_search_debug.html")
                    with open(dump_path, "w", encoding="utf-8") as f:
                        f.write(html)
                    status(f"No results found. Debug HTML saved to {dump_path}")
                    logger.info(f"[PAI Scraper] Search page HTML dumped to {dump_path}")
                except Exception:
                    pass
            else:
                # Even when results were found we keep a fresh dump so the
                # debug file is never misleadingly stale on the next miss.
                try:
                    html = page.content()
                    dump_path = os.path.join(SESSION_DIR, "last_search_debug.html")
                    with open(dump_path, "w", encoding="utf-8") as f:
                        f.write(html)
                except Exception:
                    pass

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[PAI Scraper] Error: {e}\n{tb}")
            status(f"PAI search error: {e}")
            # Best-effort dump of whatever the page currently shows.
            try:
                html = page.content()
                dump_path = os.path.join(SESSION_DIR, "last_search_debug.html")
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass
        finally:
            browser.close()

    return results


def grok_select_best_match(
    results: list[PAIPartResult],
    product_name: str,
    search_term: str,
    product_description: str = "",
) -> tuple[PAIPartResult | None, str]:
    """Use Grok AI to select the best PAI match from multiple results.

    Returns (best_match, reasoning).
    """
    if not results:
        return None, "No results to evaluate"
    if len(results) == 1:
        return results[0], "Only one result available"

    api_key = os.getenv("XAI_API_KEY", "")
    if not api_key:
        return _fallback_select(results), "AI unavailable — selected best in-stock option"

    import requests as req

    # Build prompt
    results_text = ""
    for i, r in enumerate(results, 1):
        stock_summary = ", ".join(
            f"{wh}: {qty}" + (" In Stock" if isinstance(qty, int) else f" ({qty})")
            for wh, qty in r.stock.items()
        ) if r.stock else "No stock info"

        results_text += (
            f"Result {i}:\n"
            f"  SKU: {r.sku}\n"
            f"  Description: {r.description}\n"
            f"  Your Price: ${r.your_price:,.2f}\n"
            f"  List Price: ${r.list_price:,.2f}\n"
            f"  In Stock: {'Yes' if r.in_stock else 'No'} (Total available: {r.total_available})\n"
            f"  Stock Detail: {stock_summary}\n"
            f"  Warranty: {r.warranty or 'N/A'}\n\n"
        )

    prompt = (
        f'You are a heavy diesel parts expert. A user searched PAI Industries portal for "{search_term}".\n'
        f'The product they\'re looking up is: "{product_name}"\n'
        f'{f"Description: {product_description}" if product_description else ""}\n\n'
        f"PAI returned these results:\n{results_text}\n"
        f"Select the BEST match. Consider:\n"
        f"1. SKU match to search term (exact or closest)\n"
        f"2. Description relevance to the product name\n"
        f"3. Availability (prefer in-stock items)\n"
        f"4. Price reasonableness (HP = High Performance premium, E = Economy/alternate)\n"
        f"5. Standard vs HP vs Economy variants — pick standard unless name says otherwise\n\n"
        f'Respond ONLY with this JSON (no markdown):\n'
        f'{{"selected_index": <1-based>, "reasoning": "<brief explanation>", "confidence": "high|medium|low"}}'
    )

    try:
        resp = req.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "grok-3-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        json_match = re.search(r"\{[^}]+\}", content)
        if json_match:
            parsed = json.loads(json_match.group())
            idx = int(parsed.get("selected_index", 1)) - 1
            reasoning = parsed.get("reasoning", "AI selected")
            confidence = parsed.get("confidence", "medium")
            if 0 <= idx < len(results):
                return results[idx], f"[{confidence}] {reasoning}"
    except Exception as e:
        logger.warning(f"[PAI Grok] AI selection failed: {e}")

    return _fallback_select(results), "AI fallback — selected best available option"


def _fallback_select(results: list[PAIPartResult]) -> PAIPartResult:
    """Non-AI fallback: prefer in-stock, cheapest, and exact SKU match."""
    in_stock = [r for r in results if r.in_stock]
    pool = in_stock if in_stock else results
    # Prefer items with price > 0
    priced = [r for r in pool if r.your_price > 0]
    if priced:
        return min(priced, key=lambda r: r.your_price)
    return pool[0]


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────


def _attempt_login(page) -> tuple[bool, str]:
    """Try to login to PAI portal using stored credentials.

    Returns (success, message) tuple.
    """
    try:
        from db import get_setting

        username = get_setting("pai_username") or ""
        password = get_setting("pai_password") or ""
        if not username or not password:
            return False, (
                "No PAI credentials configured.\n"
                "Go to Settings > Scraper to enter your PAI username and password."
            )

        login_url = f"{PAI_BASE}/account/entrance"
        page.goto(login_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(2)

        # Find email/username field — try many selectors used historically.
        email_selectors = [
            "input#email",
            "input[name='email']",
            "input[type='email']",
            "input[name='Username']",
            "input#loginEmail",
            "input[name='username']",
        ]
        email_field = None
        for sel in email_selectors:
            loc = page.locator(sel).first
            try:
                if loc.is_visible(timeout=1500):
                    email_field = loc
                    break
            except Exception:
                continue
        if email_field is None:
            _dump_login_debug(page, "email_field_missing")
            logger.warning("[PAI Scraper] Email field not found on login page")
            return False, "PAI login page changed — email field not found. See pai_portal_session/last_login_debug.png"

        email_field.fill(username)
        time.sleep(0.3)

        # Find password field
        pwd_field = page.locator("input#password, input[type='password']").first
        pwd_field.fill(password)
        time.sleep(0.3)

        # Click the Login button specifically (not the search form submit)
        clicked = False
        for sel in [
            "button:has-text('Login')",
            "button:has-text('Log In')",
            "button:has-text('Sign In')",
            "form:has(input[type='password']) button[type='submit']",
            "input[type='submit']",
        ]:
            loc = page.locator(sel).first
            try:
                if loc.is_visible(timeout=1500):
                    loc.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            # Last resort: press Enter inside password field
            try:
                pwd_field.press("Enter")
                clicked = True
            except Exception:
                pass

        # Wait up to 15s for URL to leave login/entrance.
        success = False
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass
            url = (page.url or "").lower()
            if "log-in" not in url and "login" not in url and "entrance" not in url:
                success = True
                break
            time.sleep(0.5)

        if success:
            logger.info("[PAI Scraper] Login successful")
            return True, ""

        # Stuck on login — capture evidence and surface real error if present.
        _dump_login_debug(page, "stuck_on_login")
        # Try to extract the visible PAI alert/danger message verbatim.
        pai_msg = ""
        for sel in [".alert-danger", ".danger-message", ".alert", ".error", ".invalid-feedback"]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=500):
                    txt = (loc.inner_text(timeout=500) or "").strip()
                    if txt:
                        pai_msg = txt
                        break
            except Exception:
                continue
        try:
            body = page.inner_text("body")[:500].lower()
        except Exception:
            body = ""
        if pai_msg or "login failed" in body or "check the email" in body or "invalid" in body or "incorrect" in body:
            return False, (
                f"PAI login failed for '{username}'.\n"
                f"PAI says: {pai_msg or 'Login failed, check the email and password.'}\n\n"
                "Open Settings > Scraper and click 'Test PAI Login' to log in interactively\n"
                "(handles CAPTCHA / 2FA / account-lockout prompts).\n"
                f"Debug: pai_portal_session/last_login_debug.png"
            )
        return False, (
            f"PAI login did not complete for '{username}' (still on login page after 15s).\n"
            "May be a CAPTCHA or 2FA challenge — try Settings > Scraper > Test PAI Login.\n"
            "Debug: pai_portal_session/last_login_debug.png"
        )
    except Exception as e:
        logger.warning(f"[PAI Scraper] Login attempt failed: {e}")
        return False, f"PAI login error: {e}"


def _dump_login_debug(page, tag: str = "") -> None:
    """Best-effort dump of current login page HTML + screenshot for debugging."""
    try:
        html = page.content()
        with open(os.path.join(SESSION_DIR, "last_login_debug.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass
    try:
        page.screenshot(path=os.path.join(SESSION_DIR, "last_login_debug.png"), full_page=True)
    except Exception:
        pass
    if tag:
        logger.info(f"[PAI Scraper] Login debug dumped (tag={tag})")


def pai_login_interactive(*, timeout_sec: int = 120, on_status=None) -> tuple[bool, str]:
    """Open PAI login in a HEADED browser so the user can complete login manually
    (handles CAPTCHA / 2FA / cookie consent). Cookies persist in SESSION_DIR for
    subsequent headless searches.

    Returns (success, message). Auto-detects login by polling URL up to timeout_sec.
    """
    from playwright.sync_api import sync_playwright

    def status(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass
        logger.info(f"[PAI Login] {msg}")

    os.makedirs(SESSION_DIR, exist_ok=True)
    try:
        from db import get_setting
        username = get_setting("pai_username") or ""
        password = get_setting("pai_password") or ""
    except Exception:
        username = password = ""

    with sync_playwright() as pw:
        # Use Playwright's bundled Chromium (NOT user's Chrome via channel="chrome").
        # The user typically has Chrome running already, which locks the
        # user_data_dir and causes exit code 21 / "Target page, context or
        # browser has been closed". Bundled Chromium has no such conflict.
        # _launch_pai_browser() wipes stale Chromium caches and retries once
        # before giving up.
        try:
            browser = _launch_pai_browser(pw, headless=False)
        except Exception as e:
            return False, (
                f"Could not launch browser for interactive login: {e}\n\n"
                "Tried clearing the stale Chromium cache automatically and it\n"
                "still won't start. Try one of the following:\n\n"
                "  1. Close any running Chrome / Chromium windows and try again.\n"
                "  2. Reinstall Playwright's Chromium:\n"
                "       .venv\\Scripts\\python.exe -m playwright install chromium\n"
                "  3. Delete the pai_portal_session/ folder to reset the profile\n"
                "     (you'll need to log in again the first time)."
            )
        page = browser.pages[0] if browser.pages else browser.new_page()
        try:
            status("Opening PAI login page...")
            page.goto(f"{PAI_BASE}/account/entrance", timeout=30000, wait_until="domcontentloaded")
            time.sleep(1)

            # Pre-fill credentials if we have them, but don't submit — let the
            # user verify and complete (handles CAPTCHA/2FA).
            if username:
                try:
                    for sel in ["input#email", "input[name='email']", "input[type='email']", "input[name='Username']"]:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=1500):
                            loc.fill(username)
                            break
                except Exception:
                    pass
            if password:
                try:
                    pwd = page.locator("input#password, input[type='password']").first
                    if pwd.is_visible(timeout=1500):
                        pwd.fill(password)
                except Exception:
                    pass

            status(f"Complete login in the browser window (waiting up to {timeout_sec}s)...")
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                url = (page.url or "").lower()
                if url and "log-in" not in url and "login" not in url and "entrance" not in url:
                    status("Login detected — saving session.")
                    time.sleep(2)
                    return True, "PAI login successful — session saved."
                time.sleep(1)
            _dump_login_debug(page, "interactive_timeout")
            return False, f"Login not completed within {timeout_sec}s. Try again or check credentials."
        except Exception as e:
            logger.warning(f"[PAI Login] Interactive login error: {e}")
            return False, f"Interactive login error: {e}"
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _dismiss_overlays(page):
    """Dismiss cookie banners and overlays."""
    for sel in [
        "button:has-text('Accept')",
        "button:has-text('OK')",
        ".cookie-close",
        "[aria-label='Close']",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                loc.click(timeout=2000)
                time.sleep(0.3)
        except Exception:
            pass


def _abs_pai_url(url: str) -> str:
    """Convert relative PAI image URL to absolute URL."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return f"{PAI_BASE}{raw}"
    if raw.startswith("http"):
        return raw
    return f"{PAI_BASE}/{raw.lstrip('/')}"


def _is_product_image_url(url: str) -> bool:
    """Basic filter to keep likely product images and skip UI assets."""
    low = url.lower()
    blocked = (
        "placeholder",
        "logo",
        "sprite",
        "icon",
        "favicon",
        "banner",
        "loading",
    )
    return not any(b in low for b in blocked)


def _parse_search_results(page, max_results: int = 5) -> list[PAIPartResult]:
    """Parse PAI search results page."""
    from bs4 import BeautifulSoup

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Check for "0 SEARCH RESULTS"
    header_text = soup.get_text(" ", strip=True)
    if re.search(r"\b0\s+SEARCH\s+RESULTS?\b", header_text, re.IGNORECASE):
        return []

    results: list[PAIPartResult] = []
    seen_skus: set[str] = set()
    price_re = re.compile(r"\$?([\d,]+\.\d{2})")

    # Find product links — try multiple selectors
    product_links = soup.select('a[href*="/product/"]')
    if not product_links:
        # Fallback: look for links that contain part-number-like slugs
        product_links = soup.select('a[href*="/Product/"]')
    if not product_links:
        # Broadest fallback: any link whose href contains a product path
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if re.search(r"/[Pp]roduct/[A-Z0-9\-]{3,}", href):
                product_links.append(a_tag)
    if not product_links:
        # Dump debug HTML when no product links found
        try:
            dump_path = os.path.join(SESSION_DIR, "last_search_debug.html")
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(str(soup))
            logger.info(f"[PAI Scraper] No product links found. Debug HTML saved to {dump_path}")
        except Exception:
            pass

    for link in product_links:
        if len(results) >= max_results:
            break

        href = str(link.get("href", ""))
        sku_match = re.search(r"/product/([A-Z0-9\-]+)", href, re.IGNORECASE)
        if not sku_match:
            continue

        sku = sku_match.group(1).strip()
        if sku in seen_skus or len(sku) < 3:
            continue
        seen_skus.add(sku)

        # Walk up DOM to find the card container with pricing info
        card = link
        for _ in range(10):
            parent = card.parent
            if not parent:
                break
            card_text = parent.get_text(" ", strip=True).upper()
            if "YOUR PRICE" in card_text or "LIST PRICE" in card_text:
                card = parent
                break
            card = parent

        card_text = card.get_text("\n", strip=True)

        result = PAIPartResult(
            sku=sku,
            detail_url=f"{PAI_BASE}{href}" if not href.startswith("http") else href,
        )

        # YOUR PRICE
        yp = re.search(r"YOUR\s*PRICE\s*:?\s*\$?([\d,]+\.\d{2})", card_text, re.IGNORECASE)
        if yp:
            try:
                result.your_price = float(yp.group(1).replace(",", ""))
            except Exception:
                pass

        # LIST PRICE
        lp = re.search(r"LIST\s*PRICE\s*:?\s*\$?([\d,]+\.\d{2})", card_text, re.IGNORECASE)
        if lp:
            try:
                result.list_price = float(lp.group(1).replace(",", ""))
            except Exception:
                pass

        # DESCRIPTION
        desc = re.search(
            r"DESC(?:RIPTION)?\s*:?\s*(?:English:?\s*)?([^\n]+)", card_text, re.IGNORECASE
        )
        if desc:
            result.description = re.sub(
                r"\s*Spanish:.*$", "", desc.group(1).strip(), flags=re.IGNORECASE
            ).strip()

        # SKU field from card text (might be more precise than URL)
        sku_field = re.search(r"\bSKU\s*:?\s*([A-Z0-9\-]+)", card_text, re.IGNORECASE)
        if sku_field:
            result.sku = sku_field.group(1).strip()

        # OEM number (require 4+ chars, skip known false-positive words)
        _OEM_BLOCKLIST = {"SELL", "PACK", "NEW", "SET", "KIT", "THE", "AND", "FOR"}
        oem = re.search(r"\bOEM\s*(?:NUMBER|#|NO\.?)?\s*:?\s*([A-Z0-9][A-Z0-9\-]{3,})", card_text, re.IGNORECASE)
        if oem and oem.group(1).strip().upper() not in _OEM_BLOCKLIST:
            result.oem_number = oem.group(1).strip()

        # Image
        img = card.find("img") if hasattr(card, "find") else None
        if img:
            src = img.get("src") or img.get("data-src") or ""
            if src and "placeholder" not in src.lower() and "logo" not in src.lower():
                result.image_url = _abs_pai_url(src)
                if result.image_url:
                    result.image_urls = [result.image_url]

        results.append(result)

    return results


def _parse_detail_page(page, result: PAIPartResult):
    """Parse a PAI product detail page for stock, warranty, and extras.

    IMPORTANT: The PAI detail page has two sections:
      - "Your Selection" (center) — the main product
      - "Alternate" (right) — alternate/substitute product
    Stock badges in the Alternate section must NOT be attributed to the main product.
    We scope parsing to the main product section only.
    """
    from bs4 import BeautifulSoup

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    KNOWN_WAREHOUSES = {
        "ATL", "LAX", "LAT", "DFW", "CHI", "IND", "SEA", "NYC", "MIA",
        "PHX", "DEN", "HOU", "DAL", "SLC", "MSP", "DTW", "BOS", "PHL",
    }

    # ── Identify the main product section ("Your Selection") ──
    # The page has a "Your Selection" panel (center) and an "Alternate" panel (right).
    # We need to restrict stock badge parsing to the main product section only.
    main_section = None
    alt_section = None

    # Strategy 1: Find by header text "Your Selection" / "Alternate"
    for header_el in soup.find_all(string=re.compile(r"Your\s*Selection", re.IGNORECASE)):
        parent = header_el
        for _ in range(8):
            parent = getattr(parent, "parent", None)
            if parent is None:
                break
            # Look for a container div/section that wraps the selection panel
            if hasattr(parent, "name") and parent.name in ("div", "section", "td", "article"):
                # Check this container has stock/warranty info but NOT "Alternate" header
                container_text = parent.get_text(" ", strip=True)
                if ("STOCK" in container_text.upper() or "WARRANTY" in container_text.upper()):
                    # Make sure this section doesn't ALSO contain the Alternate panel
                    if "Alternate" not in container_text or "Your Selection" in container_text:
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

    # Use main_section for stock parsing if found; otherwise fall back to full page
    # but try to exclude the alternate section
    if main_section:
        stock_soup = main_section
        stock_text = main_section.get_text("\n", strip=True)
    elif alt_section:
        # No clean main section found, but we know where alternate is — use full page
        # but mark that we need to filter out alternate badges
        stock_soup = soup
        stock_text = soup.get_text("\n", strip=True)
    else:
        stock_soup = soup
        stock_text = soup.get_text("\n", strip=True)

    # Full page text for non-stock fields (warranty, weight, etc.)
    full_text = soup.get_text("\n", strip=True)

    # Product images from detail/gallery area.
    # Prefer main section so alternate-product images are not mixed in.
    image_scope = main_section if main_section is not None else soup
    image_candidates: list[str] = []
    seen_images: set[str] = set()

    for el in image_scope.find_all(["img", "source"]):
        attrs = (
            "src",
            "data-src",
            "data-zoom-image",
            "data-large_image",
            "data-large-image",
            "srcset",
        )
        for attr in attrs:
            val = el.get(attr)
            if not val:
                continue
            if attr == "srcset":
                val = str(val).split(",")[0].strip().split(" ")[0]
            abs_url = _abs_pai_url(str(val))
            if not abs_url or abs_url in seen_images or not _is_product_image_url(abs_url):
                continue
            seen_images.add(abs_url)
            image_candidates.append(abs_url)

    if result.image_url:
        primary = _abs_pai_url(result.image_url)
        if primary and primary not in seen_images and _is_product_image_url(primary):
            image_candidates.insert(0, primary)

    if image_candidates:
        result.image_urls = image_candidates[:8]
        result.image_url = result.image_urls[0]
    elif result.image_url:
        result.image_urls = [_abs_pai_url(result.image_url)]

    stock: dict[str, object] = {}
    total_qty = 0

    # Method 1: Quantity text "DFW - 3 In Stock" or "IND - 7 In Stock"
    # Only search within the main product section text
    qty_pattern = re.compile(r"\b([A-Z]{3})\s*[-–]\s*(\d+)\s*In\s*Stock\b", re.IGNORECASE)
    for m in qty_pattern.finditer(stock_text):
        wh = m.group(1).upper()
        qty = int(m.group(2))
        if wh in KNOWN_WAREHOUSES:
            stock[wh] = qty
            total_qty += qty

    # Method 2: Badge classes for warehouses (with or without quantities)
    # Only look at badges within the main product section
    badges = stock_soup.find_all(class_=re.compile(r"badge", re.IGNORECASE))

    # If we're using the full soup but have an alt_section, filter out badges inside it
    if stock_soup is soup and alt_section:
        alt_badge_ids = {id(b) for b in alt_section.find_all(class_=re.compile(r"badge", re.IGNORECASE))}
        badges = [b for b in badges if id(b) not in alt_badge_ids]

    for badge in badges:
        badge_text = badge.get_text(strip=True).upper()
        for wh in KNOWN_WAREHOUSES:
            if wh in badge_text and wh not in stock:
                classes = " ".join(badge.get("class", []))
                if "badge-success" in classes or "success" in classes:
                    stock[wh] = "in_stock"
                elif "badge-danger" in classes or "danger" in classes:
                    stock[wh] = "out_of_stock"
                elif "badge-warning" in classes or "warning" in classes:
                    stock[wh] = "low_stock"

    # Method 3: Check for "Notify me" (all out of stock)
    notify_elements = stock_soup.find_all(string=re.compile(r"notify\s*me", re.IGNORECASE))
    if notify_elements and not stock:
        for wh in KNOWN_WAREHOUSES:
            stock.setdefault(wh, "out_of_stock")

    # Determine overall availability
    has_any_stock = any(
        (isinstance(v, int) and v > 0) or v == "in_stock" or v == "low_stock"
        for v in stock.values()
    )

    if has_any_stock:
        # At least one warehouse has stock — product IS available
        result.stock = stock
        result.in_stock = True
        result.total_available = total_qty
        result.not_available = False
    elif stock:
        # Warehouses found but all out of stock
        result.stock = stock
        result.in_stock = False
        result.total_available = 0
        result.not_available = False
    else:
        # No warehouse data found — check for "Not Available" text in main section
        # Look specifically in the main product section, not the alternate
        not_avail_text = stock_soup.find(string=re.compile(r"Not\s*Available", re.IGNORECASE))
        if not_avail_text:
            result.not_available = True
            result.stock = {}
            result.in_stock = False
            result.total_available = 0
        else:
            result.stock = {}
            result.in_stock = False
            result.total_available = 0
            result.not_available = False

    # WARRANTY — use main section text if available, fall back to full page
    wty_text = stock_text if main_section else full_text
    wty = re.search(r"WARRANTY\s*:?\s*(\d+\s*(?:Year|Month)s?)", wty_text, re.IGNORECASE)
    if wty:
        result.warranty = wty.group(1).strip()

    # PRODUCT GROUP
    pg = re.search(r"PRODUCT\s*GROUP\s*:?\s*([^\n]+)", full_text, re.IGNORECASE)
    if pg:
        result.product_group = pg.group(1).strip()

    # WEIGHT — use main section text to avoid getting alternate's weight
    wt = re.search(r"WEIGHT\s*:?\s*([\d.]+\s*(?:LBS?|KG))", wty_text, re.IGNORECASE)
    if wt:
        result.weight = wt.group(1).strip()

    # SELL PACK
    sp = re.search(r"SELL\s*PACK\s*:?\s*([^\n]+)", full_text, re.IGNORECASE)
    if sp:
        result.sell_pack = sp.group(1).strip()

    # UPC CODE
    upc = re.search(r"UPC\s*(?:CODE)?\s*:?\s*(\d{10,})", full_text, re.IGNORECASE)
    if upc:
        result.upc = upc.group(1).strip()

    # Prices from detail page (if not already captured)
    # Use main section text for prices to avoid reading alternate's price
    price_text = stock_text if main_section else full_text
    if result.your_price <= 0:
        yp = re.search(r"YOUR\s*PRICE\s*:?\s*\$?([\d,]+\.\d{2})", price_text, re.IGNORECASE)
        if yp:
            try:
                result.your_price = float(yp.group(1).replace(",", ""))
            except Exception:
                pass

    if result.your_price <= 0:
        # Fallback: look for price in structured elements
        for sel in [".your-price", ".price-your", "[data-price]", ".product-price"]:
            el = soup.select_one(sel)
            if el:
                price_text = el.get_text(strip=True)
                pm = re.search(r"\$?([\d,]+\.\d{2})", price_text)
                if pm:
                    try:
                        result.your_price = float(pm.group(1).replace(",", ""))
                        break
                    except Exception:
                        pass
        # Fallback: any element with "$X.XX" near "your price" text
        if result.your_price <= 0:
            all_prices = re.findall(r"\$?([\d,]+\.\d{2})", price_text)
            if all_prices:
                # First price is often YOUR PRICE, second is LIST PRICE
                try:
                    result.your_price = float(all_prices[0].replace(",", ""))
                except Exception:
                    pass

    if result.list_price <= 0:
        lp = re.search(r"LIST\s*PRICE\s*:?\s*\$?([\d,]+\.\d{2})", price_text, re.IGNORECASE)
        if lp:
            try:
                result.list_price = float(lp.group(1).replace(",", ""))
            except Exception:
                pass

    if result.list_price <= 0:
        # Fallback: second price on page
        all_prices = re.findall(r"\$?([\d,]+\.\d{2})", price_text)
        if len(all_prices) >= 2 and result.your_price > 0:
            try:
                candidate = float(all_prices[1].replace(",", ""))
                if candidate > result.your_price:
                    result.list_price = candidate
            except Exception:
                pass

    # Full description from detail page
    desc = re.search(
        r"DESCRIPTION\s*:?\s*(?:English:?\s*)?([^\n]+)", full_text, re.IGNORECASE
    )
    if desc and not result.description:
        result.description = re.sub(
            r"\s*Spanish:.*$", "", desc.group(1).strip(), flags=re.IGNORECASE
        ).strip()

    # OEM number from detail page
    _OEM_BLOCKLIST = {"SELL", "PACK", "NEW", "SET", "KIT", "THE", "AND", "FOR"}
    if not result.oem_number:
        oem = re.search(r"\bOEM\s*(?:NUMBER|#|NO\.?)?\s*:?\s*([A-Z0-9][A-Z0-9\-]{3,})", full_text, re.IGNORECASE)
        if oem and oem.group(1).strip().upper() not in _OEM_BLOCKLIST:
            result.oem_number = oem.group(1).strip()

    # Alternate / substitute part from the "Alternate" panel (cross-reference).
    _extract_alternate(soup, result, alt_section)


def _extract_alternate(soup, result: PAIPartResult, alt_section=None) -> None:
    """Capture the alternate/substitute part PAI offers on the detail page.

    The detail page splits into a "Your Selection" panel (the searched part)
    and an "Alternate" panel (PAI's recommended replacement). Previously the
    alternate panel was only located so its stock badges could be EXCLUDED
    from the main product. Here we mine that same panel for the alternate's
    SKU, description, price, and availability and attach them to ``result`` as
    a cross-reference.

    Best-effort and defensive: any parse failure leaves the alternate fields
    at their defaults.
    """
    try:
        # Reuse the alt_section the caller already located if it has a product
        # link; otherwise locate the panel from the "Alternate" header.
        container = None
        if alt_section is not None and alt_section.select_one('a[href*="/product/"]'):
            container = alt_section
        else:
            for header_el in soup.find_all(string=re.compile(r"^\s*Alternate\s*$", re.IGNORECASE)):
                node = header_el
                for _ in range(8):
                    node = getattr(node, "parent", None)
                    if node is None:
                        break
                    if hasattr(node, "select_one") and node.select_one('a[href*="/product/"]'):
                        container = node
                        break
                if container is not None:
                    break

        if container is None:
            return

        main_sku = (result.sku or "").strip().upper()

        # Find the alternate product link whose SKU differs from the main part.
        alt_href = ""
        alt_sku = ""
        for link in container.select('a[href*="/product/"]'):
            href = str(link.get("href", ""))
            m = re.search(r"/product/([A-Z0-9\-]+)", href, re.IGNORECASE)
            if not m:
                continue
            candidate = m.group(1).strip()
            if len(candidate) < 3 or candidate.upper() == main_sku:
                continue
            alt_sku = candidate
            alt_href = href
            break

        if not alt_sku:
            return

        result.alternate_sku = alt_sku
        result.alternate_url = (
            alt_href if alt_href.startswith("http") else f"{PAI_BASE}{alt_href}"
        )

        container_text = container.get_text("\n", strip=True)

        # Alternate price (logged-in customer price).
        yp = re.search(r"YOUR\s*PRICE\s*:?\s*\$?([\d,]+\.\d{2})", container_text, re.IGNORECASE)
        if yp:
            try:
                result.alternate_your_price = float(yp.group(1).replace(",", ""))
            except Exception:
                pass

        # Alternate description.
        desc = re.search(
            r"DESC(?:RIPTION)?\s*:?\s*(?:English:?\s*)?([^\n]+)", container_text, re.IGNORECASE
        )
        if desc:
            result.alternate_description = re.sub(
                r"\s*Spanish:.*$", "", desc.group(1).strip(), flags=re.IGNORECASE
            ).strip()

        # Alternate availability: any "XXX - N In Stock" quantity, or a success badge.
        if re.search(r"\b[A-Z]{3}\s*[-–]\s*[1-9]\d*\s*In\s*Stock\b", container_text, re.IGNORECASE):
            result.alternate_in_stock = True
        else:
            for badge in container.find_all(class_=re.compile(r"badge", re.IGNORECASE)):
                classes = " ".join(badge.get("class", []))
                if "badge-success" in classes or "success" in classes:
                    result.alternate_in_stock = True
                    break
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("[PAI Scraper] Alternate extraction failed: %s", exc)
