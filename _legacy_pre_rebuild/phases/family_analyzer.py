"""Phase 1.5: Lightweight family/cross-reference grouping analyzer.

This module groups products into families based on shared cross-reference numbers.
Products that share cross-refs are likely variants of the same underlying part.

Key Logic:
- Uses EXISTING xref data from products when available (no web fetch needed)
- Only fetches from web if product has no xrefs stored
- All products in a family get the SAME family_id (FAM-XXXXX)
- Works on ALL stages (1-5)
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import TYPE_CHECKING
from bs4 import BeautifulSoup
import requests

from utils.helpers import extract_xrefs, normalize
from phases.scraper import extract_primary_part_from_url

if TYPE_CHECKING:
    from workers.signals import Signals

__all__ = [
    "analyze_product_families",
    "FamilyGroup",
]


class FamilyGroup:
    """A group of related products sharing cross-reference numbers."""
    
    def __init__(self, family_id: str):
        self.family_id = family_id  # The shared family identifier
        self.products: list[dict] = []  # List of product dicts
        self.shared_xrefs: set[str] = set()  # Cross-refs shared by 2+ products
        self.all_xrefs: set[str] = set()  # All cross-refs in family
    
    def add_product(self, product: dict, xrefs: set[str]) -> None:
        """Add a product to this family."""
        self.products.append(product)
        
        # Track shared cross-refs (those appearing in multiple products)
        if self.all_xrefs:
            new_shared = self.all_xrefs & xrefs
            self.shared_xrefs.update(new_shared)
        
        self.all_xrefs.update(xrefs)
    
    def __len__(self) -> int:
        return len(self.products)
    
    def __repr__(self) -> str:
        return f"FamilyGroup({self.family_id!r}, {len(self.products)} products, {len(self.shared_xrefs)} shared xrefs)"


def _fetch_product_xrefs(url: str, session: requests.Session) -> tuple[str, list[str], str]:
    """Fetch a single product page and extract cross-references.
    
    Returns:
        (title, xrefs_list, error_or_empty)
    """
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 429:
            return "", [], "rate_limited"
        if resp.status_code != 200:
            return "", [], f"http_{resp.status_code}"
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Extract title
        title_el = soup.select_one("h1.product_title")
        title = normalize(title_el.text) if title_el else ""
        
        # Extract cross-references
        xrefs = extract_xrefs(title, soup)
        
        return title, xrefs, ""
        
    except requests.Timeout:
        return "", [], "timeout"
    except Exception as e:
        return "", [], str(e)[:50]


def _get_existing_xrefs(item: dict) -> list[str]:
    """Extract existing cross-references from product data.
    
    Checks multiple possible locations where xrefs might be stored.
    Returns list of xrefs if found, empty list if need to fetch.
    """
    xrefs = []
    
    # Check direct xrefs attribute
    existing_xrefs = item.get("xrefs") or []
    if existing_xrefs and isinstance(existing_xrefs, list):
        xrefs.extend([str(x).upper().strip() for x in existing_xrefs if x])
    
    # Check cross_refs attribute
    cross_refs = item.get("cross_refs") or item.get("cross_references") or []
    if cross_refs and isinstance(cross_refs, list):
        xrefs.extend([str(x).upper().strip() for x in cross_refs if x])
    
    # Clean and deduplicate
    xrefs = list(set(x for x in xrefs if x and len(x) >= 3))
    
    return xrefs


def analyze_product_families(
    items: list[dict],
    *,
    signals: "Signals" = None,
    cancel_event: threading.Event | None = None,
    max_concurrent: int = 3,
) -> dict:
    """Analyze products to identify related families via cross-references.
    
    IMPORTANT: Uses existing xref data when available. Only fetches from web
    if product has no stored cross-references.
    
    Args:
        items: List of product dicts, each with 'url', 'part', 'title', and optionally 'xrefs'
        signals: Optional signals for progress updates
        cancel_event: Optional event to cancel operation
        max_concurrent: Max concurrent HTTP requests (keep low to avoid rate limiting)
    
    Returns:
        dict with:
            - families: list of FamilyGroup objects
            - orphans: products with no shared cross-refs
            - stats: analysis statistics
    """
    if not items:
        return {"families": [], "orphans": [], "stats": {"total": 0}}
    
    # HTTP session (only used if we need to fetch)
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    })
    
    # Phase 1: Gather xrefs for all products
    product_xrefs: dict[str, dict] = {}  # url -> {xrefs: set, title, part, ...}
    xref_to_urls: dict[str, set[str]] = defaultdict(set)  # xref -> {urls that have it}
    
    total = len(items)
    analyzed = 0
    used_existing = 0
    fetched = 0
    rate_limited = 0
    errors = 0
    
    if signals:
        try:
            signals.log.emit(f"🔗 Analyzing {total} products for family groupings...")
        except Exception:
            pass
    
    for idx, item in enumerate(items):
        if cancel_event and cancel_event.is_set():
            break
        
        url = item.get("url", "")
        part = item.get("part", "")
        title = item.get("title", "")
        
        if not url:
            continue
        
        # FIRST: Check if we already have xref data
        existing_xrefs = _get_existing_xrefs(item)
        
        if existing_xrefs:
            # Use existing data - no fetch needed!
            xrefs = existing_xrefs
            used_existing += 1
        else:
            # Need to fetch from website
            fetched_title, fetched_xrefs, error = _fetch_product_xrefs(url, session)
            fetched += 1
            
            if error == "rate_limited":
                rate_limited += 1
                xrefs = []
            elif error:
                errors += 1
                xrefs = []
            else:
                xrefs = fetched_xrefs
                if fetched_title:
                    title = fetched_title
        
        # Always include the primary part number as a cross-ref
        if part:
            part_upper = part.upper().strip()
            if part_upper and part_upper not in xrefs:
                xrefs = list(set(xrefs) | {part_upper})
        
        product_xrefs[url] = {
            "url": url,
            "part": part,
            "title": title,
            "price": item.get("price"),
            "thumbnail": item.get("thumbnail", ""),
            "xrefs": set(xrefs),
            "had_existing_xrefs": bool(existing_xrefs),
        }
        
        # Build reverse index: which URLs have each xref
        for xref in xrefs:
            xref_to_urls[xref].add(url)
        
        analyzed += 1
        
        # Progress update
        if signals and (idx % 5 == 0 or idx == total - 1):
            try:
                signals.stats.emit({
                    "phase": "Phase 1.5: Analyze",
                    "attempted": analyzed,
                    "added": used_existing,
                    "skipped": rate_limited,
                    "failed": errors,
                    "total": total,
                    "current_url": url,
                })
                signals.log.emit(f"📊 Analyzed {analyzed}/{total}: {part}")
            except Exception:
                pass
    
    if cancel_event and cancel_event.is_set():
        return {
            "families": [],
            "orphans": [],
            "stats": {"total": total, "analyzed": analyzed, "cancelled": True},
        }
    
    if signals:
        try:
            signals.log.emit(f"📊 Data source: {used_existing} from cache, {fetched} fetched from web")
        except Exception:
            pass
    
    # Phase 2: Build families using STRICT direct matching
    # NO Union-Find chaining - products must share multiple xrefs DIRECTLY
    
    import re
    
    def _is_valid_xref(xref: str) -> bool:
        """Filter out junk xrefs that shouldn't be used for matching."""
        if not xref or len(xref) < 5:
            return False
        # Filter year ranges like "2010-2012", "2013-APR", "2007-2010"
        if re.match(r'^\d{4}-\d{4}$', xref):
            return False
        if re.match(r'^\d{4}-[A-Z]{3}$', xref):
            return False
        # Filter pure short numbers (likely generic)
        if re.match(r'^\d{1,5}$', xref):
            return False
        # Filter common generic terms
        if xref in {'NEW', 'USED', 'REMAN', 'OEM', 'AFTERMARKET'}:
            return False
        return True
    
    # Filter xrefs in product data
    for url, data in product_xrefs.items():
        filtered_xrefs = {x for x in data.get("xrefs", set()) if _is_valid_xref(x)}
        data["xrefs"] = filtered_xrefs
    
    # Rebuild xref_to_urls with filtered xrefs
    xref_to_urls_filtered: dict[str, set[str]] = defaultdict(set)
    for url, data in product_xrefs.items():
        for xref in data.get("xrefs", set()):
            xref_to_urls_filtered[xref].add(url)
    
    # Filter out xrefs that appear in too many products (still generic even if long)
    threshold = max(3, min(int(len(product_xrefs) * 0.10), 20))  # 10% or 20 max
    common_xrefs = {xref for xref, urls in xref_to_urls_filtered.items() if len(urls) > threshold}
    
    if signals and common_xrefs:
        try:
            signals.log.emit(f"⚠️ Filtering {len(common_xrefs)} common xrefs (appear in >{threshold} products)")
        except Exception:
            pass
    
    # Remove common xrefs from product data
    for url, data in product_xrefs.items():
        data["xrefs"] = data.get("xrefs", set()) - common_xrefs
    
    # STRICT MATCHING: Group products that share AT LEAST 2 xrefs
    # No chaining - direct pairwise comparison only
    MIN_SHARED_XREFS = 2
    
    urls = list(product_xrefs.keys())
    families: list[FamilyGroup] = []
    used_urls: set[str] = set()
    
    for i, url1 in enumerate(urls):
        if url1 in used_urls:
            continue
        
        xrefs1 = product_xrefs[url1].get("xrefs", set())
        if len(xrefs1) < MIN_SHARED_XREFS:
            continue
        
        # Find all products that share MIN_SHARED_XREFS with this one
        family_urls = [url1]
        
        for url2 in urls[i+1:]:
            if url2 in used_urls:
                continue
            
            xrefs2 = product_xrefs[url2].get("xrefs", set())
            shared = xrefs1 & xrefs2
            
            if len(shared) >= MIN_SHARED_XREFS:
                family_urls.append(url2)
        
        if len(family_urls) >= 2:
            # Create family with most common shared xref as ID
            all_xrefs = set()
            for u in family_urls:
                all_xrefs.update(product_xrefs[u].get("xrefs", set()))
            
            # Count which xrefs are most shared
            xref_counts: dict[str, int] = defaultdict(int)
            for u in family_urls:
                for x in product_xrefs[u].get("xrefs", set()):
                    xref_counts[x] += 1
            
            # Pick the family ID - prefer product-level part numbers over components
            # ERK (Engine Rebuild Kit), EGK (Gasket Kit), etc. are better family IDs
            # than generic components like sealants, bearings, gaskets
            shared_sorted = sorted(xref_counts.items(), key=lambda x: (-x[1], x[0]))
            
            # Define prefixes that indicate kit/assembly level part numbers
            kit_prefixes = ("ERK", "EGK", "LKT", "EIK", "ELK", "EOK", "TCK", "WPK", "TRK", "MV1", "215SB")
            
            # Define patterns that indicate generic COMPONENT part numbers to AVOID
            # These are small parts that appear in many kits but shouldn't be family IDs
            component_patterns = (
                "342SX",    # Sealants
                "GB",       # Gaskets (e.g., 553GB224, 590GB312)
                "GC",       # Gaskets/Components (e.g., 446GC1160)
                "AX",       # Small parts (e.g., 56AX392, 97AX127)
            )
            
            def _is_component_xref(xref: str) -> bool:
                """Check if xref looks like a generic component (sealant, gasket, etc)."""
                x = str(xref or "").upper()
                # Check for component patterns
                for pattern in component_patterns:
                    if pattern in x:
                        return True
                # Very short xrefs are likely components
                if len(x) < 8 and not any(x.startswith(p) for p in kit_prefixes):
                    return True
                return False
            
            def _is_kit_xref(xref: str) -> bool:
                """Check if xref looks like a kit/assembly part number."""
                x = str(xref or "").upper()
                for prefix in kit_prefixes:
                    if x.startswith(prefix):
                        return True
                return False
            
            # First try to find a kit-level xref
            family_id = None
            for xref, count in shared_sorted:
                if _is_kit_xref(xref):
                    family_id = xref
                    break
            
            # If no kit xref, try to find any non-component xref
            if not family_id:
                for xref, count in shared_sorted:
                    if not _is_component_xref(xref):
                        family_id = xref
                        break
            
            # Fallback to most shared xref (even if component)
            if not family_id:
                family_id = shared_sorted[0][0] if shared_sorted else product_xrefs[url1].get("part", "UNKNOWN")
            
            family = FamilyGroup(family_id)
            for u in family_urls:
                prod_data = product_xrefs[u]
                family.add_product(prod_data, prod_data.get("xrefs", set()))
                used_urls.add(u)
            
            families.append(family)
    
    # Remaining products are orphans
    orphans: list[dict] = []
    for url in urls:
        if url not in used_urls:
            orphans.append(product_xrefs[url])
    
    # Sort families by size (largest first)
    families.sort(key=lambda f: len(f), reverse=True)
    
    if signals:
        try:
            signals.log.emit(f"🔗 Found {len(families)} family groups")
        except Exception:
            pass
    
    # Count total shared xrefs across all families
    total_shared_xrefs = sum(len(f.shared_xrefs) for f in families)
    
    stats = {
        "total": total,
        "analyzed": analyzed,
        "used_existing_xrefs": used_existing,
        "fetched_from_web": fetched,
        "rate_limited": rate_limited,
        "errors": errors,
        "families_found": len(families),
        "products_in_families": sum(len(f) for f in families),
        "orphans": len(orphans),
        "shared_xrefs_found": total_shared_xrefs,
        "common_xrefs_filtered": len(common_xrefs),
        "common_xref_threshold": threshold,
        "min_shared_required": MIN_SHARED_XREFS,
    }
    
    if signals:
        try:
            signals.log.emit(
                f"✅ Family analysis complete: {len(families)} families, "
                f"{stats['products_in_families']} grouped products, {len(orphans)} orphans"
            )
        except Exception:
            pass
    
    return {
        "families": families,
        "orphans": orphans,
        "stats": stats,
    }


def format_family_report(result: dict) -> str:
    """Format family analysis result as readable report."""
    lines = ["=" * 60, "PRODUCT FAMILY ANALYSIS REPORT", "=" * 60, ""]
    
    stats = result.get("stats", {})
    lines.append(f"Total products analyzed: {stats.get('analyzed', 0)}/{stats.get('total', 0)}")
    lines.append(f"  - Used existing xref data: {stats.get('used_existing_xrefs', 0)}")
    lines.append(f"  - Fetched from web: {stats.get('fetched_from_web', 0)}")
    lines.append(f"Common xrefs filtered: {stats.get('common_xrefs_filtered', 0)} (threshold: >{stats.get('common_xref_threshold', 0)} products)")
    lines.append(f"Min shared xrefs required: {stats.get('min_shared_required', 2)}")
    lines.append(f"Families found: {stats.get('families_found', 0)}")
    lines.append(f"Products in families: {stats.get('products_in_families', 0)}")
    lines.append(f"Orphan products: {stats.get('orphans', 0)}")
    lines.append(f"Shared cross-refs: {stats.get('shared_xrefs_found', 0)}")
    lines.append("")
    
    families = result.get("families", [])
    for i, family in enumerate(families[:20], 1):  # Show top 20 families
        lines.append(f"Family #{i}: FAM-{family.family_id} ({len(family)} products)")
        lines.append(f"  Shared XRefs: {', '.join(sorted(family.shared_xrefs)[:5])}")
        for prod in family.products[:5]:  # Show first 5 products
            title = prod.get("title", "")[:50]
            part = prod.get("part", "")
            lines.append(f"    - {part}: {title}...")
        if len(family.products) > 5:
            lines.append(f"    ... and {len(family.products) - 5} more")
        lines.append("")
    
    if len(families) > 20:
        lines.append(f"... and {len(families) - 20} more families")
    
    return "\n".join(lines)
