from __future__ import annotations

import copy
import datetime
import hashlib
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Callable, Protocol, TypedDict

import requests

from models import Product
from sources.pai_client import normalize_part_number, remove_pai_watermark


logger = logging.getLogger("sources.pai_enrichment")


class _PAICostLookupResult(TypedDict):
    cost: float
    sku: str
    source: str
    url: str
    stock: dict[str, object]
    image_urls: list[str]


class _PAILookup(Protocol):
    def lookup_cost(self, part_number: str) -> _PAICostLookupResult | None: ...


class _PAICrossLookup(_PAILookup, Protocol):
    def lookup_cross(self, oem_part_number: str) -> dict | None: ...


class PAIAdvisoryMatch(TypedDict, total=False):
    sku: str
    cost: float
    stock: dict[str, object]
    detail_url: str
    confidence: str  # HIGH | MEDIUM | LOW
    strategy: str
    source_candidate: str


def _phase3_now_iso() -> str:
    try:
        return datetime.datetime.now().isoformat(timespec="seconds")
    except Exception:
        return ""


def _phase3_base_pn(raw: str) -> str:
    """Best-effort base PN extraction.

    Example: C15101-01 -> C15101
    """
    try:
        s = str(raw or "").strip()
    except Exception:
        s = ""
    if not s:
        return ""
    # If it has a dash suffix, keep the left side.
    if "-" in s:
        left = s.split("-", 1)[0].strip()
        return left
    return s


def _phase3_stock_available(stock: object) -> bool:
    if not isinstance(stock, dict) or not stock:
        return False
    try:
        for _wh, val in stock.items():
            if isinstance(val, bool):
                if val:
                    return True
                continue
            if isinstance(val, (int, float)):
                if float(val) > 0:
                    return True
                continue
            if isinstance(val, dict):
                for k in ("qty", "quantity", "on_hand", "available"):
                    try:
                        if float(val.get(k) or 0) > 0:
                            return True
                    except Exception:
                        continue
                continue
            sval = str(val or "").strip().lower()
            # Include "low_stock" as available (PAI badge-warning = Quantity Limited)
            if sval in {"in_stock", "available", "yes", "y", "true", "low_stock"}:
                return True
    except Exception:
        return False
    return False


def _phase3_conf_rank(conf: str) -> int:
    c = str(conf or "").strip().upper()
    if c == "HIGH":
        return 3
    if c == "MEDIUM":
        return 2
    if c == "LOW":
        return 1
    return 0


# PAI SKU prefixes that indicate KIT products (rebuild kits, gasket kits, etc.)
# MUST be defined before _phase3_pick_primary which uses _is_kit_sku()
_KIT_SKU_PREFIXES = (
    "ERK",   # Engine Rebuild Kits
    "EGK",   # Engine Gasket Kits
    "LKT",   # Liner Kits
    "EIK",   # Engine Inframe Kits  
    "ELK",   # Engine Liner Kits
    "EOK",   # Engine Overhaul Kits
    "TCK",   # Timing Cover Kits
    "WPK",   # Water Pump Kits
    "TRK",   # Turbo Rebuild Kits
)


def _is_kit_sku(sku: str) -> bool:
    """Check if SKU looks like a kit product."""
    s = str(sku or "").strip().upper()
    if not s:
        return False
    # Check for known kit prefixes
    for prefix in _KIT_SKU_PREFIXES:
        if s.startswith(prefix):
            return True
    return False


def _phase3_pick_primary(
    matches: list[PAIAdvisoryMatch], 
    source_candidate: str = "",
    hhp_price: float = 0.0,
    is_kit_product: bool = False,
) -> PAIAdvisoryMatch | None:
    """Pick the best PAI match from a list of candidates.
    
    Priority order:
    1. EXACT match to source candidate (e.g., C15103-010 matches C15103-010 exactly)
    2. For kit products: prefer kit SKUs (ERK, EGK, etc.) over component SKUs
    3. Price sanity check: prefer matches within reasonable range of HHP price
    4. Stock availability
    5. Confidence rank
    6. Lower cost (as tiebreaker only)
    
    CRITICAL: We should NOT pick C15103-010HP when searching for C15103-010
    just because HP variant is cheaper. The suffix matters!
    
    CRITICAL: For kit products, we must NOT pick a $7.65 sealant when searching
    for a $4000 engine rebuild kit just because the sealant is in the xrefs!
    """
    if not matches:
        return None

    # Normalize source for comparison
    source_norm = normalize_part_number(str(source_candidate or "").strip())
    
    # First priority: Exact SKU match to source candidate
    if source_norm:
        for m in matches:
            sku_norm = normalize_part_number(str(m.get("sku") or ""))
            if sku_norm == source_norm:
                logger.debug(f"[PAI Pick] Exact match: {sku_norm}")
                return m
    
    # Second priority: SKU starts with source (e.g., source "C15103-010" matches "C15103-010")
    # but NOT "C15103-010HP" (has extra suffix)
    if source_norm:
        for m in matches:
            sku = str(m.get("sku") or "").strip().upper()
            if sku == source_norm.upper():
                logger.debug(f"[PAI Pick] Case-insensitive exact: {sku}")
                return m
    
    # Third priority: For KIT products, strongly prefer kit SKUs
    # This prevents picking a $7.65 sealant when we want a $4000 rebuild kit
    if is_kit_product:
        kit_matches = [m for m in matches if _is_kit_sku(str(m.get("sku") or ""))]
        if kit_matches:
            # Among kit matches, prefer one with price closest to HHP price
            if hhp_price > 0:
                def price_distance(m: PAIAdvisoryMatch) -> float:
                    try:
                        cost = float(m.get("cost") or 0.0)
                        return abs(cost - hhp_price) if cost > 0 else float('inf')
                    except:
                        return float('inf')
                kit_matches.sort(key=price_distance)
                best_kit = kit_matches[0]
                logger.debug(f"[PAI Pick] Kit match: {best_kit.get('sku')} (is_kit_product=True, hhp_price={hhp_price})")
                return best_kit
            # No HHP price, just pick first kit match with stock
            for m in kit_matches:
                if _phase3_stock_available(m.get("stock")):
                    logger.debug(f"[PAI Pick] Kit match with stock: {m.get('sku')}")
                    return m
            # Return first kit match
            logger.debug(f"[PAI Pick] First kit match: {kit_matches[0].get('sku')}")
            return kit_matches[0]
    
    # Fourth priority: Price sanity check - if HHP price is known, prefer matches
    # within a reasonable range (50%-200% of HHP price)
    if hhp_price > 100:  # Only apply for products > $100
        price_reasonable = []
        for m in matches:
            try:
                cost = float(m.get("cost") or 0.0)
                if cost > 0:
                    # Check if PAI cost is between 50% and 200% of HHP price
                    # (PAI is wholesale, should be lower than retail)
                    if hhp_price * 0.3 <= cost <= hhp_price * 1.5:
                        price_reasonable.append(m)
            except:
                continue
        
        if price_reasonable:
            # Among price-reasonable matches, prefer those with stock
            with_stock = [m for m in price_reasonable if _phase3_stock_available(m.get("stock"))]
            if with_stock:
                logger.debug(f"[PAI Pick] Price-reasonable with stock: {with_stock[0].get('sku')}")
                return with_stock[0]
            logger.debug(f"[PAI Pick] Price-reasonable: {price_reasonable[0].get('sku')}")
            return price_reasonable[0]

    with_stock = [m for m in matches if _phase3_stock_available(m.get("stock"))]
    if len(with_stock) == 1:
        return with_stock[0]

    def _key(m: PAIAdvisoryMatch) -> tuple[int, int, int, float]:
        stock_rank = 1 if _phase3_stock_available(m.get("stock")) else 0
        conf_rank = _phase3_conf_rank(m.get("confidence"))
        # Prefer shorter SKUs (avoid HP/HD variants when base part was requested)
        sku_len = len(str(m.get("sku") or ""))
        try:
            cost = float(m.get("cost") or 0.0)
        except Exception:
            cost = 0.0
        # Prefer stock, then confidence, then shorter SKU, then lower cost.
        return (stock_rank, conf_rank, -sku_len, -cost if cost > 0 else 0.0)

    # Sort descending for stock/conf; prefer shorter SKU; then by lower cost.
    best = sorted(matches, key=_key, reverse=True)[0]
    logger.debug(f"[PAI Pick] Best by ranking: {best.get('sku')} (source was: {source_candidate})")
    return best


def _phase3_strategy_confidence(strategy: str) -> str:
    s = str(strategy or "").strip().upper()
    if s.startswith("1_"):
        return "HIGH"
    if s.startswith("2_") or s.startswith("3_"):
        return "MEDIUM"
    return "LOW"


def download_pai_images_for_product(
    product: Product,
    image_urls: list[str],
    output_dir: Path | str,
    *,
    handle: str | None = None,
    remove_watermark: bool = True,
    max_images: int = 6,
    session: requests.Session | None = None,
) -> int:
    """Download PAI images for a product that has no images.
    
    Args:
        product: Product to download images for
        image_urls: List of PAI image URLs
        output_dir: Directory to save images (usually output/_temp_raw/<handle>)
        handle: Product handle for naming (defaults to product.handle)
        remove_watermark: Whether to crop bottom to remove PAI watermark
        max_images: Maximum number of images to download
        session: Optional requests session for connection pooling
    
    Returns:
        Number of images successfully downloaded
    """
    if not image_urls:
        return 0
    
    handle = handle or getattr(product, "handle", "") or "unknown"
    output_path = Path(output_dir)
    
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"[PAI Images] Failed to create directory {output_path}: {e}")
        return 0
    
    sess = session or requests.Session()
    downloaded = 0
    
    for i, img_url in enumerate(image_urls[:max_images], 1):
        if not img_url:
            continue
        
        try:
            logger.debug(f"[PAI Images] Downloading {img_url[:80]}...")
            resp = sess.get(img_url, timeout=30, stream=True)
            
            if resp.status_code != 200:
                logger.debug(f"[PAI Images] HTTP {resp.status_code} for {img_url[:60]}")
                continue
            
            data = resp.content
            if len(data) < 5000:
                logger.debug(f"[PAI Images] Too small ({len(data)} bytes): {img_url[:60]}")
                continue
            
            # Validate image dimensions
            try:
                from PIL import Image
                with Image.open(BytesIO(data)) as img:
                    w, h = img.size
                    if min(w, h) < 300:
                        logger.debug(f"[PAI Images] Too small dimensions ({w}x{h}): {img_url[:60]}")
                        continue
            except Exception as e:
                logger.debug(f"[PAI Images] Invalid image data: {e}")
                continue
            
            # Remove watermark if enabled
            if remove_watermark:
                processed = remove_pai_watermark(data)
                if processed:
                    data = processed
                    logger.debug(f"[PAI Images] Watermark cropped from image {i}")
            
            # Save image
            dest = output_path / f"{handle}_{i:02d}.jpg"
            
            # Convert to JPEG if not already
            try:
                from PIL import Image
                with Image.open(BytesIO(data)) as img:
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(str(dest), format="JPEG", quality=92, optimize=True)
                downloaded += 1
                logger.info(f"[PAI Images] Saved {dest.name} from PAI")
            except Exception as e:
                logger.debug(f"[PAI Images] Failed to save {dest}: {e}")
                continue
                
        except Exception as e:
            logger.debug(f"[PAI Images] Failed to download {img_url[:60]}: {e}")
            continue
    
    # Update product image counts
    if downloaded > 0:
        try:
            setattr(product, "image_count_raw", downloaded)
        except Exception:
            pass
        try:
            # Store source info in pricing dict
            pricing = getattr(product, "pricing", {}) or {}
            if not isinstance(pricing, dict):
                pricing = {}
            pricing["pai_images_downloaded"] = downloaded
            setattr(product, "pricing", pricing)
        except Exception:
            pass
    
    return downloaded


def enrich_pai_advisory(product: Product, pai_client: _PAICrossLookup) -> dict[str, object]:
    """Phase 3 advisory-only PAI enrichment.

    Writes only Product.pai_advisory. Does NOT mutate SKU/Supplier/Cost/Stage.
    """

    # Gather candidate part numbers.
    try:
        primary_raw = str(getattr(product, "primary_part_number", "") or getattr(product, "primary_pn", "") or "").strip()
    except Exception:
        primary_raw = ""
    try:
        oem_raw = str(getattr(product, "oem_part_number", "") or getattr(product, "oem_pn", "") or "").strip()
    except Exception:
        oem_raw = ""
    try:
        secondary_raw = str(getattr(product, "secondary_part_number", "") or getattr(product, "secondary_pn", "") or "").strip()
    except Exception:
        secondary_raw = ""

    # Competitor / cross refs.
    xrefs: list[str] = []
    try:
        xr = getattr(product, "xrefs", None)
        if isinstance(xr, list):
            xrefs = [str(x or "").strip() for x in xr]
    except Exception:
        xrefs = []
    xrefs = [x for x in xrefs if x]

    # Matching strategies in locked order.
    strategies: list[tuple[str, list[str]]] = []

    # 1) Exact full PN
    if primary_raw:
        strategies.append(("1_EXACT_FULL_PN", [primary_raw]))

    # 2) Normalized PN (dash/format normalization)
    norm_primary = normalize_part_number(primary_raw)
    if norm_primary and norm_primary != normalize_part_number(primary_raw):
        # (normalize_part_number is idempotent; this branch is mostly defensive)
        pass
    if norm_primary:
        strategies.append(("2_NORMALIZED_PN", [norm_primary]))

    # 3) Base PN
    base_primary = _phase3_base_pn(primary_raw)
    if base_primary and base_primary != primary_raw:
        strategies.append(("3_BASE_PN", [base_primary]))

    # 4) Alternate OEM PN
    alt_oems: list[str] = []
    for cand in (oem_raw, secondary_raw):
        if cand:
            alt_oems.append(cand)
    alt_oems = [c for c in alt_oems if c and c != primary_raw]
    if alt_oems:
        strategies.append(("4_ALT_OEM_PN", alt_oems))

    # 5) Known competitor PN - but FILTER OUT component part numbers
    # Component PNs (sealants, bearings, gaskets) should NOT be searched
    # as they will return wrong products (e.g., $7.65 sealant for a $4000 kit)
    component_patterns = (
        "342SX",    # Sealants
        "GB",       # Gaskets (553GB224, 590GB312B, etc.)
        "GC",       # Gaskets/Components (446GC1160, 57GC387, etc.)
        "AX",       # Small parts (56AX392, 97AX127, etc.)
    )
    
    def _is_component_pn(pn: str) -> bool:
        """Check if part number looks like a kit component."""
        p = str(pn or "").upper()
        if not p:
            return False
        for pattern in component_patterns:
            if pattern in p:
                return True
        return False
    
    # Filter xrefs: keep only product-level part numbers, not components
    filtered_xrefs = [x for x in xrefs if not _is_component_pn(x)]
    
    if filtered_xrefs:
        strategies.append(("5_COMPETITOR_PN", filtered_xrefs))
    elif xrefs:
        # Fallback: if ALL xrefs were filtered out, use original list
        # but mark it as low priority
        strategies.append(("6_COMPONENT_FALLBACK", xrefs))

    enriched_at = _phase3_now_iso()
    out: dict[str, object] = {
        "pai_matches": [],
        "pai_primary_sku": "",
        "pai_cost": 0.0,
        "pai_stock": {},
        "match_confidence": "",
        "match_strategy": "",
        "flags": [],
        "enriched_at": enriched_at,
        "detail_url": "",
    }

    flags: list[str] = []

    # Heuristic warning: kit/set ambiguity
    try:
        title = str(getattr(product, "title", "") or "").lower()
    except Exception:
        title = ""
    if title and any(tok in title for tok in (" kit", "kit ", " set", "set ")):
        flags.append("KIT_SET_AMBIGUITY")

    # Try strategies in order; stop at first strategy that yields any matches.
    matches: list[PAIAdvisoryMatch] = []
    chosen_strategy = ""
    chosen_conf = ""
    suffix_norm_used = False

    for strategy, candidates in strategies:
        candidates = [c for c in candidates if str(c or "").strip()]
        if not candidates:
            continue

        strategy_matches: list[PAIAdvisoryMatch] = []
        for cand_raw in candidates:
            cand = str(cand_raw or "").strip()
            if not cand:
                continue

            # Self-match: candidate is already a PAI-like SKU.
            do_self_match = _looks_like_pai_part_number(normalize_part_number(cand))
            if do_self_match:
                lookup = None
                try:
                    lookup = pai_client.lookup_cost(cand)
                except Exception:
                    lookup = None
                if isinstance(lookup, dict) and float(lookup.get("cost") or 0) > 0:
                    sku = normalize_part_number(str(lookup.get("sku") or ""))
                    if sku:
                        stock = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
                        strategy_matches.append(
                            {
                                "sku": sku,
                                "cost": float(lookup.get("cost") or 0.0),
                                "stock": dict(stock or {}),
                                "detail_url": str(lookup.get("url") or lookup.get("source") or ""),
                                "confidence": _phase3_strategy_confidence(strategy),
                                "strategy": strategy,
                                "source_candidate": cand,
                            }
                        )
                        continue

            cross = None
            try:
                cross = pai_client.lookup_cross(cand)
            except Exception:
                cross = None
            if not isinstance(cross, dict):
                continue

            status = str(cross.get("status", "") or "").strip().upper()
            if not status:
                if str(cross.get("primary", "") or "").strip():
                    status = "MULTI_MATCH" if bool(cross.get("multi", False)) else "EXACT"
                else:
                    status = "NO_MATCH"
            if status == "NO_MATCH":
                continue

            primary_sku = normalize_part_number(str(cross.get("primary", "") or ""))
            options = cross.get("matches")
            if not isinstance(options, list):
                options = cross.get("alternates")
            if not isinstance(options, list):
                options = []
            skus: list[str] = []
            if status == "MULTI_MATCH":
                skus = [normalize_part_number(s) for s in options]
                skus = [s for s in skus if s]
            else:
                if primary_sku:
                    skus = [primary_sku]
            skus = sorted(set(skus))
            if not skus:
                continue

            # Pull cost/stock per SKU.
            for sku in skus:
                lookup = None
                try:
                    lookup = pai_client.lookup_cost(sku)
                except Exception:
                    lookup = None
                if isinstance(lookup, dict) and float(lookup.get("cost") or 0) > 0:
                    stock = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
                    strategy_matches.append(
                        {
                            "sku": normalize_part_number(str(lookup.get("sku") or sku)),
                            "cost": float(lookup.get("cost") or 0.0),
                            "stock": dict(stock or {}),
                            "detail_url": str(lookup.get("url") or lookup.get("source") or cross.get("detail_url") or ""),
                            "confidence": _phase3_strategy_confidence(strategy),
                            "strategy": strategy,
                            "source_candidate": cand,
                        }
                    )

        if strategy == "2_NORMALIZED_PN" and primary_raw and normalize_part_number(primary_raw) != str(primary_raw).strip().upper():
            suffix_norm_used = True

        if strategy_matches:
            matches = strategy_matches
            chosen_strategy = strategy
            chosen_conf = _phase3_strategy_confidence(strategy)
            break

    # Deduplicate by SKU while keeping best (stock/conf/cost)
    by_sku: dict[str, PAIAdvisoryMatch] = {}
    for m in matches:
        sku = str(m.get("sku") or "").strip()
        if not sku:
            continue
        if sku not in by_sku:
            by_sku[sku] = m
            continue
        # Keep the better one.
        cur = by_sku[sku]
        cur_key = (
            1 if _phase3_stock_available(cur.get("stock")) else 0,
            _phase3_conf_rank(cur.get("confidence")),
            -float(cur.get("cost") or 0.0),
        )
        new_key = (
            1 if _phase3_stock_available(m.get("stock")) else 0,
            _phase3_conf_rank(m.get("confidence")),
            -float(m.get("cost") or 0.0),
        )
        if new_key > cur_key:
            by_sku[sku] = m

    matches = list(by_sku.values())

    # Sort matches (best first) per locked ranking.
    matches.sort(
        key=lambda m: (
            0 if _phase3_stock_available(m.get("stock")) else 1,
            -_phase3_conf_rank(m.get("confidence")),
            float(m.get("cost") or 0.0) if float(m.get("cost") or 0.0) > 0 else 1e12,
        )
    )

    if suffix_norm_used:
        flags.append("SUFFIX_NORMALIZATION_USED")

    if len(matches) > 1:
        flags.append("MULTIPLE_PAI_MATCHES")

    if chosen_conf and chosen_conf != "HIGH":
        flags.append("LOW_CONFIDENCE_CROSS")

    # Get HHP price to help with price sanity checks
    hhp_price = 0.0
    try:
        pricing = getattr(product, "pricing", {}) or {}
        if isinstance(pricing, dict):
            hhp_price = float(pricing.get("hhp_price") or pricing.get("price") or 0.0)
    except Exception:
        pass
    
    # Detect if this is a kit product based on title
    is_kit_product = "KIT_SET_AMBIGUITY" in flags
    
    # Pass the primary search candidate to help pick the right match
    # This prevents selecting C15103-010HP when we searched for C15103-010
    # Also prevents selecting a $7.65 sealant when searching for a $4000 kit
    primary = _phase3_pick_primary(
        matches, 
        source_candidate=primary_raw,
        hhp_price=hhp_price,
        is_kit_product=is_kit_product,
    )
    if primary is not None:
        out["pai_primary_sku"] = str(primary.get("sku") or "")
        try:
            out["pai_cost"] = float(primary.get("cost") or 0.0)
        except Exception:
            out["pai_cost"] = 0.0
        out["pai_stock"] = dict(primary.get("stock") or {}) if isinstance(primary.get("stock"), dict) else {}
        out["detail_url"] = str(primary.get("detail_url") or "")

    out["pai_matches"] = matches
    out["match_confidence"] = chosen_conf
    out["match_strategy"] = chosen_strategy
    out["flags"] = flags

    try:
        product.pai_advisory = dict(out)
    except Exception:
        pass
    
    # Also populate pai_alternates for UI context menu compatibility
    try:
        alt_skus = [str(m.get("sku") or "").strip() for m in matches if m.get("sku")]
        alt_skus = [s for s in alt_skus if s]
        if alt_skus:
            setattr(product, "pai_alternates", list(alt_skus))
    except Exception:
        pass
    
    # Set pai_sku from primary match
    try:
        if primary is not None:
            pai_primary_sku = str(primary.get("sku") or "").strip()
            if pai_primary_sku:
                setattr(product, "pai_sku", pai_primary_sku)
                setattr(product, "pai_part_number", pai_primary_sku)
    except Exception:
        pass
    
    # Set pai_cost from primary match
    try:
        if primary is not None:
            pai_cost = float(primary.get("cost") or 0.0)
            if pai_cost > 0:
                setattr(product, "pai_cost", pai_cost)
    except Exception:
        pass
    
    # Set pai_stock from primary match (for UI table display)
    try:
        if primary is not None:
            pai_stock = primary.get("stock")
            if isinstance(pai_stock, dict) and pai_stock:
                setattr(product, "pai_stock", dict(pai_stock))
    except Exception:
        pass
    
    # Set pai_status based on match results
    try:
        if len(matches) == 0:
            setattr(product, "pai_status", "NO_MATCH")
        elif len(matches) == 1:
            setattr(product, "pai_status", chosen_conf or "HIGH")  # Use confidence level
        else:
            setattr(product, "pai_status", "MULTI_MATCH")
    except Exception:
        pass

    return out


def enrich_pai_advisory_many_parallel(
    products: dict[str, Product],
    pai_client: _PAICrossLookup,
    *,
    keys: list[str],
    max_workers: int | None = None,
    on_progress: "Callable[[int, int], None] | None" = None,
) -> dict[str, int]:
    """Phase 3 advisory-only batch enrichment.

    Mutates only Product.pai_advisory.
    
    Args:
        on_progress: Optional callback(completed, total) for progress updates.
    """

    keys = [str(k) for k in (keys or []) if str(k or "").strip()]
    if not keys:
        return {"ok": 0, "no_match": 0, "multi": 0, "failed": 0}

    ok = 0
    no_match = 0
    multi = 0
    failed = 0
    total = len(keys)

    if max_workers is None:
        max_workers = int(os.getenv("PAI_THREADS", "6") or 6)
        if max_workers <= 0:
            max_workers = 6

    def _work(k: str) -> tuple[str, str]:
        prod = products.get(k)
        if prod is None:
            return (k, "failed")
        try:
            out = enrich_pai_advisory(prod, pai_client)
        except Exception:
            return (k, "failed")
        try:
            matches = out.get("pai_matches", [])
        except Exception:
            matches = []
        if not isinstance(matches, list) or len(matches) == 0:
            return (k, "no_match")
        if len(matches) > 1:
            return (k, "multi")
        return (k, "ok")

    with ThreadPoolExecutor(max_workers=int(max_workers)) as ex:
        futs = {ex.submit(_work, k): k for k in keys}
        completed = 0
        for fut in as_completed(futs):
            try:
                _k, status = fut.result()
            except Exception:
                status = "failed"
            if status == "ok":
                ok += 1
            elif status == "multi":
                multi += 1
            elif status == "no_match":
                no_match += 1
            else:
                failed += 1
            completed += 1
            # Emit progress callback
            if on_progress is not None:
                try:
                    on_progress(completed, total)
                except Exception:
                    pass

    return {"ok": ok, "multi": multi, "no_match": no_match, "failed": failed}


def _strip_jaks_prefix(value: str) -> str:
    v = str(value or "").strip()
    if v.upper().startswith("JAKS-"):
        return v[5:]
    return v


def _legacy_mirror_pai_fields(product: Product) -> None:
    """One-way mirror of canonical PAI fields into legacy pricing keys.

    LEGACY MIRROR — DO NOT READ FROM THIS IN UI
    """

    pricing = getattr(product, "pricing", None)
    if not isinstance(pricing, dict):
        return

    try:
        pricing["pai_status"] = str(getattr(product, "pai_status", "") or "").strip()
    except Exception:
        pass

    try:
        pai_sku = str(getattr(product, "pai_sku", "") or "").strip()
    except Exception:
        pai_sku = ""

    try:
        pricing.setdefault("pai", {})
        if not isinstance(pricing.get("pai"), dict):
            pricing["pai"] = {}
        pricing["pai"]["sku"] = pai_sku
    except Exception:
        pass


def _assert_pai_invariants(product: Product) -> None:
    try:
        pai_cost = float(getattr(product, "pai_cost", 0.0) or 0.0)
    except Exception:
        pai_cost = 0.0

    try:
        pai_sku = str(getattr(product, "pai_sku", "") or "").strip()
    except Exception:
        pai_sku = ""

    if pai_cost > 0 and not pai_sku:
        raise RuntimeError(
            f"Invariant violated: PAI cost without SKU for {str(getattr(product, 'primary_pn', '') or '')}"
        )


def _looks_like_pai_part_number(norm: str) -> bool:
    n = str(norm or "").strip().upper()
    if not n:
        return False
    if not n.isalnum():
        return False
    # Heuristic requested by UI workflow: treat 6–10 alphanumeric strings as
    # potentially PAI-like part numbers suitable for direct SKU lookup.
    if not (6 <= len(n) <= 10):
        return False
    # Avoid pure-alpha short tokens.
    if n.isalpha():
        return False
    return True


def _extract_pai_alternates(payload: object, *, sku: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    out: list[str] = []

    alternates = payload.get("alternates", [])
    if isinstance(alternates, list):
        out.extend([normalize_part_number(a) for a in alternates])

    links = payload.get("links", None)
    if isinstance(links, dict):
        out.extend([normalize_part_number(k) for k in links.keys()])

    out = [a for a in out if a and a != normalize_part_number(sku)]
    out = sorted(set(out))
    return out[:5]


def enrich_pai(
    product: Product,
    pai_client: _PAICrossLookup,
    *,
    force_candidates: list[str] | None = None,
    force_self_match: bool = False,
    image_dir: Path | str | None = None,
    download_images_if_zero: bool = True,
) -> bool:
    """Unified PAI enrichment.

        Lookup order (stop on first hit that yields SKU + cost):
            1) product.oem_pn
            2) product.secondary_pn
            3) product.primary_pn

    Invariant:
      - No SKU → no cost (hard rule)

    Args:
        product: Product to enrich
        pai_client: PAI client for lookups
        force_candidates: Override candidate part numbers
        force_self_match: Allow SKU echo matches
        image_dir: Directory to save PAI images (if product has 0 images)
        download_images_if_zero: Whether to download PAI images when product has no images

    Does NOT flip supplier. Use apply_pai_supplier_gate() separately.
    """

    pricing = getattr(product, "pricing", None)
    if not isinstance(pricing, dict):
        try:
            product.pricing = {}
        except Exception:
            return False
        pricing = product.pricing

    # Track PAI image URLs for potential download
    _pai_image_urls: list[str] = []

    # This function may be called repeatedly (GUI refresh / reruns). Clear any
    # stale "vendor needed" flag at the start; we will set it again only on the
    # authoritative no-results outcome.
    try:
        if isinstance(pricing, dict) and "vendor_needed" in pricing:
            pricing.pop("vendor_needed", None)
    except Exception:
        pass

    # Candidates in authoritative order.
    # IMPORTANT: Always include legacy identity fields as fallbacks.
    # International/Navistar pages often have OEM placeholders like "Cross" in
    # one field while the real part number lives in primary_part_number.
    raw_candidates: list[tuple[str, str]] = []

    def _push(label: str, value: object) -> None:
        try:
            v = str(value or "").strip()
        except Exception:
            v = ""
        raw_candidates.append((label, v))

    _push("oem_pn", getattr(product, "oem_pn", ""))
    _push("secondary_pn", getattr(product, "secondary_pn", ""))
    _push("primary_pn", getattr(product, "primary_pn", ""))
    _push("oem_part_number", getattr(product, "oem_part_number", ""))
    _push("secondary_part_number", getattr(product, "secondary_part_number", ""))
    _push("primary_part_number", getattr(product, "primary_part_number", ""))

    raw_oem_field = ""
    for lbl, val in raw_candidates:
        if lbl in {"oem_pn", "oem_part_number"} and val:
            raw_oem_field = val
            break
    oem_norm = normalize_part_number(raw_oem_field)
    oem_invalid = (not oem_norm) or (str(raw_oem_field or "").strip().upper() == "CROSS") or (len(oem_norm) < 3)
    if oem_invalid and raw_oem_field:
        logger.info(f"PAI_ENRICH: OEM invalid '{raw_oem_field}' -> falling back to primary/secondary")

    seen: set[str] = set()
    ordered_norm: list[str] = []
    ordered_src: dict[str, str] = {}
    for label, raw in raw_candidates:
        if not raw:
            continue
        if label in {"oem_pn", "oem_part_number"} and str(raw).strip().upper() == "CROSS":
            logger.info(f"PAI_ENRICH: SKIP placeholder OEM '{raw}'")
            continue

        norm = normalize_part_number(str(raw or ""))
        if not norm:
            logger.info(f"PAI_ENRICH: SKIP invalid '{label}'='{raw}'")
            continue
        if norm in seen:
            continue
        seen.add(norm)
        ordered_norm.append(norm)
        ordered_src[norm] = label

    if isinstance(force_candidates, list) and force_candidates:
        seen_force: set[str] = set()
        forced_norm: list[str] = []
        for raw in force_candidates:
            norm = normalize_part_number(str(raw or ""))
            if not norm or norm in seen_force:
                continue
            seen_force.add(norm)
            forced_norm.append(norm)
        ordered_norm = forced_norm
        ordered_src = {n: "FORCED" for n in forced_norm}

    found_any_match = False

    for candidate in ordered_norm:
        logger.info(
            f"PAI_ENRICH: TRY candidate='{candidate}' src='{ordered_src.get(candidate, '?')}'"
        )
        # Self-match optimization:
        # If OEM is invalid (e.g., "Cross"/too-short), and the candidate looks like a
        # PAI SKU (6–10 alphanumeric), treat it as a direct PAI SKU and lookup cost.
        do_self_match = (oem_invalid or bool(force_self_match)) and _looks_like_pai_part_number(candidate)

        if (oem_invalid or bool(force_self_match)) and (not _looks_like_pai_part_number(candidate)):
            logger.info(
                f"PAI_ENRICH: NOTE candidate '{candidate}' not PAI-like -> fallback cross lookup attempted"
            )

        if do_self_match:
            lookup = None
            try:
                lookup = pai_client.lookup_cost(candidate)
            except Exception:
                lookup = None

            if isinstance(lookup, dict) and lookup.get("cost") and float(lookup.get("cost") or 0) > 0:
                sku_from_page = normalize_part_number(str(lookup.get("sku") or ""))
                if sku_from_page:
                    # Check if this is a cross-reference (searched SKU differs from returned)
                    is_cross_ref = bool(lookup.get("is_cross_reference", False))
                    
                    if is_cross_ref:
                        # Store cross-reference for user review but don't auto-apply
                        logger.info(
                            f"PAI_ENRICH: CROSS_REF candidate='{candidate}' -> pai_sku='{sku_from_page}' (user choice needed)"
                        )
                        cost_value = float(lookup.get("cost") or 0.0)
                        stock = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
                        
                        # Store cross-reference data for user to review
                        cross_ref_data = {
                            "searched_sku": candidate,
                            "pai_sku": sku_from_page,
                            "cost": cost_value,
                            "stock": dict(stock or {}),
                            "detail_url": str(lookup.get("url") or lookup.get("source") or ""),
                            "image_urls": lookup.get("image_urls", []),
                            "adopted": False,  # User hasn't chosen to use this yet
                        }
                        try:
                            setattr(product, "pai_cross_reference", cross_ref_data)
                        except Exception:
                            pass
                        try:
                            setattr(product, "pai_status", "CROSS_REF")
                        except Exception:
                            pass
                        
                        # Store in pricing for UI access
                        pricing["pai_cross_reference"] = cross_ref_data
                        pricing["pai_cross_ref_available"] = True
                        
                        # Don't auto-apply cost - let user decide
                        found_any_match = True
                        # Continue to next candidate to see if there's an exact match
                        continue
                    
                    logger.info(
                        f"PAI_ENRICH: SELF_MATCH hit candidate='{candidate}' -> sku='{sku_from_page}'"
                    )
                    cost_value = float(lookup.get("cost") or 0.0)

                    try:
                        src_lbl = str(ordered_src.get(candidate, "?") or "?")
                    except Exception:
                        src_lbl = "?"
                    try:
                        logger.info(
                            f"PAI_ENRICH: Fallback search on {src_lbl} {candidate}: Found {sku_from_page} with cost ${float(cost_value):.2f}"
                        )
                    except Exception:
                        pass

                    try:
                        product.cost = float(cost_value)
                    except Exception:
                        return False

                    try:
                        setattr(product, "pai_cost", float(cost_value))
                    except Exception:
                        pass
                    try:
                        setattr(product, "pai_sku", sku_from_page)
                    except Exception:
                        pass
                    alternates = _extract_pai_alternates(lookup, sku=sku_from_page)
                    try:
                        setattr(product, "pai_alternates", list(alternates))
                    except Exception:
                        pass
                    try:
                        setattr(product, "pai_status", "EXACT")
                    except Exception:
                        pass

                    try:
                        setattr(product, "cost_source", "PAI")
                    except Exception:
                        pass
                    try:
                        setattr(product, "pai_part_number", sku_from_page)
                    except Exception:
                        pass
                    try:
                        stock = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
                        setattr(product, "pai_stock", dict(stock or {}))
                    except Exception:
                        pass

                    # Capture PAI image URLs for potential download
                    try:
                        img_urls = lookup.get("image_urls")
                        if isinstance(img_urls, list) and img_urls:
                            _pai_image_urls.extend(img_urls)
                    except Exception:
                        pass

                    pricing["cost_source"] = "PAI"
                    pricing["pai_cost_source"] = sku_from_page
                    pricing["pai_match"] = sku_from_page
                    pricing.setdefault("cost_confidence", "supplier")
                    pricing["pai_stock"] = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
                    pricing["pai_detail_url"] = str(lookup.get("url") or lookup.get("source") or "")
                    pricing["pai_cross"] = {
                        "oem": candidate,
                        "pai": sku_from_page,
                        "alternates": list(alternates),
                        "multi": False,
                        "requires_click": False,
                        "valid": True,
                        "status": "EXACT",
                        "detail_url": str(lookup.get("url") or lookup.get("source") or ""),
                    }

                    # Download PAI images if product has no images
                    if download_images_if_zero and image_dir and _pai_image_urls:
                        try:
                            img_count = int(getattr(product, "image_count_raw", 0) or 0)
                        except Exception:
                            img_count = 0
                        if img_count == 0:
                            try:
                                handle = str(getattr(product, "handle", "") or "").strip()
                                if handle:
                                    out_path = Path(image_dir) / handle
                                    downloaded = download_pai_images_for_product(
                                        product, _pai_image_urls, out_path, handle=handle
                                    )
                                    if downloaded > 0:
                                        logger.info(f"[PAI Images] Downloaded {downloaded} images for {handle}")
                            except Exception as e:
                                logger.debug(f"[PAI Images] Failed to download for product: {e}")

                    _legacy_mirror_pai_fields(product)
                    _assert_pai_invariants(product)
                    return True


        cross = None
        try:
            cross = pai_client.lookup_cross(candidate)
        except Exception:
            cross = None

        if not isinstance(cross, dict):
            logger.info(f"PAI_ENRICH: MISS candidate='{candidate}' (no cross)")
            continue

        status = str(cross.get("status", "") or "").strip().upper()
        # Back-compat: older clients may not provide status.
        if not status:
            if str(cross.get("primary", "") or "").strip():
                status = "MULTI_MATCH" if bool(cross.get("multi", False)) else "EXACT"
            else:
                status = "NO_MATCH"

        if status == "NO_MATCH":
            logger.info(f"PAI_ENRICH: MISS candidate='{candidate}' (status=NO_MATCH)")
            continue

        sku = normalize_part_number(str(cross.get("primary", "") or ""))
        if not sku:
            logger.info(f"PAI_ENRICH: SKIP candidate='{candidate}' (no primary sku in response)")
            continue

        # Rule 3: Never treat the searched OEM/HHP/identity numbers as PAI SKUs.
        identity_set: set[str] = set()
        try:
            identity_set.add(normalize_part_number(str(getattr(product, "oem_pn", "") or "")))
        except Exception:
            pass
        try:
            identity_set.add(normalize_part_number(str(getattr(product, "secondary_pn", "") or "")))
        except Exception:
            pass
        try:
            identity_set.add(normalize_part_number(str(getattr(product, "primary_pn", "") or "")))
        except Exception:
            pass
        # Legacy fallbacks
        try:
            identity_set.add(normalize_part_number(str(getattr(product, "oem_part_number", "") or "")))
        except Exception:
            pass
        try:
            identity_set.add(normalize_part_number(str(getattr(product, "secondary_part_number", "") or "")))
        except Exception:
            pass
        try:
            identity_set.add(normalize_part_number(str(getattr(product, "primary_part_number", "") or "")))
        except Exception:
            pass
        identity_set.add(normalize_part_number(str(candidate or "")))
        identity_set = {x for x in identity_set if x}
        if sku in identity_set:
            # Treat as an invalid/echo result.
            logger.info(
                f"PAI_ENRICH: SKIP echoed sku='{sku}' for candidate='{candidate}' (Rule 3)"
            )
            continue

        alternates = _extract_pai_alternates(cross, sku=sku)

        # MULTI_MATCH: surface options but never auto-apply cost.
        if status == "MULTI_MATCH":
            found_any_match = True
            matches = cross.get("matches", alternates)
            if not isinstance(matches, list):
                matches = []
            matches_norm = [normalize_part_number(m) for m in matches]
            matches_norm = [m for m in matches_norm if m]
            matches_norm = sorted(set(matches_norm))
            matches_norm = matches_norm[:5]

            # NEW: Capture detailed match info with stock and images
            match_details = cross.get("match_details", [])
            if not isinstance(match_details, list):
                match_details = []
            in_stock_count = int(cross.get("in_stock_count", 0) or 0)
            
            # Filter to only in-stock matches if requested (first 3 in-stock)
            in_stock_matches = [m for m in match_details if m.get("in_stock")][:3]

            logger.info(
                f"PAI_ENRICH: MULTI candidate='{candidate}' primary='{sku}' options={len(matches_norm)} in_stock={in_stock_count}"
            )

            try:
                setattr(product, "pai_sku", None)
            except Exception:
                pass
            try:
                setattr(product, "pai_alternates", list(matches_norm))
            except Exception:
                pass
            try:
                setattr(product, "pai_status", "MULTI_MATCH")
            except Exception:
                pass
            try:
                setattr(product, "pai_multi_matches", list(match_details))
            except Exception:
                pass
            try:
                setattr(product, "pai_in_stock_count", in_stock_count)
            except Exception:
                pass

            pricing["pai_cross"] = {
                "oem": candidate,
                "pai": "",
                "alternates": matches_norm,
                "multi": True,
                "requires_click": True,
                "valid": False,
                "status": "MULTI_MATCH",
                "links": cross.get("links", {}) if isinstance(cross.get("links", {}), dict) else {},
                "match_details": match_details,
                "in_stock_count": in_stock_count,
                "in_stock_matches": in_stock_matches,
            }

            _legacy_mirror_pai_fields(product)
            _assert_pai_invariants(product)
            return False

        # EXACT: apply cost only if we can bind it to the same verified detail result.
        if status == "EXACT":
            found_any_match = True

            logger.info(f"PAI_ENRICH: EXACT candidate='{candidate}' -> pai='{sku}'")

            cost_value = None
            try:
                if cross.get("cost") is not None:
                    cost_value = float(cross.get("cost") or 0.0)
            except Exception:
                cost_value = None

            # If lookup_cross didn't provide cost, fall back to lookup_cost(sku).
            if not cost_value or cost_value <= 0:
                lookup = None
                try:
                    lookup = pai_client.lookup_cost(sku)
                except Exception:
                    lookup = None

                if isinstance(lookup, dict) and lookup.get("cost") and float(lookup.get("cost") or 0) > 0:
                    sku_from_page = normalize_part_number(str(lookup.get("sku") or ""))
                    if not sku_from_page:
                        raise RuntimeError(
                            f"Invariant violated: PAI lookup returned cost without SKU for {str(getattr(product, 'handle', '') or '')}"
                        )
                    sku = sku_from_page
                    cost_value = float(lookup.get("cost") or 0.0)
                    try:
                        stock = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
                        setattr(product, "pai_stock", dict(stock or {}))
                        pricing["pai_stock"] = dict(stock or {})
                        pricing["pai_detail_url"] = str(lookup.get("url") or lookup.get("source") or "")
                    except Exception:
                        pass
                    # Capture PAI image URLs for potential download
                    try:
                        img_urls = lookup.get("image_urls")
                        if isinstance(img_urls, list) and img_urls:
                            _pai_image_urls.extend(img_urls)
                    except Exception:
                        pass

            if cost_value and cost_value > 0:
                alternates_filtered = [a for a in alternates if a and a != sku]

                try:
                    src_lbl = str(ordered_src.get(candidate, "?") or "?")
                except Exception:
                    src_lbl = "?"
                try:
                    logger.info(
                        f"PAI_ENRICH: Fallback search on {src_lbl} {candidate}: Found {sku} with cost ${float(cost_value):.2f}"
                    )
                except Exception:
                    pass

                try:
                    product.cost = float(cost_value)
                except Exception:
                    return False

                try:
                    setattr(product, "pai_cost", float(cost_value))
                except Exception:
                    pass
                try:
                    setattr(product, "pai_sku", sku)
                except Exception:
                    pass
                try:
                    setattr(product, "pai_alternates", list(alternates_filtered))
                except Exception:
                    pass
                try:
                    setattr(product, "pai_status", "EXACT")
                except Exception:
                    pass

                try:
                    setattr(product, "cost_source", "PAI")
                except Exception:
                    pass
                try:
                    setattr(product, "pai_part_number", sku)
                except Exception:
                    pass

                pricing["cost_source"] = "PAI"
                pricing["pai_cost_source"] = sku
                pricing["pai_match"] = sku
                pricing.setdefault("cost_confidence", "supplier")
                pricing["pai_cross"] = {
                    "oem": candidate,
                    "pai": sku,
                    "alternates": alternates_filtered,
                    "multi": False,
                    "requires_click": True,
                    "valid": True,
                    "status": "EXACT",
                    "detail_url": str(cross.get("detail_url") or pricing.get("pai_detail_url") or ""),
                    "links": cross.get("links", {}) if isinstance(cross.get("links", {}), dict) else {},
                }

                # Download PAI images if product has no images
                if download_images_if_zero and image_dir and _pai_image_urls:
                    try:
                        img_count = int(getattr(product, "image_count_raw", 0) or 0)
                    except Exception:
                        img_count = 0
                    if img_count == 0:
                        try:
                            handle = str(getattr(product, "handle", "") or "").strip()
                            if handle:
                                out_path = Path(image_dir) / handle
                                downloaded = download_pai_images_for_product(
                                    product, _pai_image_urls, out_path, handle=handle
                                )
                                if downloaded > 0:
                                    logger.info(f"[PAI Images] Downloaded {downloaded} images for {handle}")
                        except Exception as e:
                            logger.debug(f"[PAI Images] Failed to download for product: {e}")

                _legacy_mirror_pai_fields(product)
                _assert_pai_invariants(product)
                return True

            # Exact OEM match but no cost parsed: record status and options without applying cost.
            try:
                setattr(product, "pai_sku", sku)
            except Exception:
                pass
            try:
                setattr(product, "pai_alternates", list(alternates))
            except Exception:
                pass
            try:
                setattr(product, "pai_status", "EXACT")
            except Exception:
                pass

            pricing["pai_cross"] = {
                "oem": candidate,
                "pai": sku,
                "alternates": alternates,
                "multi": False,
                "requires_click": True,
                "valid": True,
                "status": "EXACT",
                "detail_url": str(cross.get("detail_url") or ""),
                "links": cross.get("links", {}) if isinstance(cross.get("links", {}), dict) else {},
            }

            _legacy_mirror_pai_fields(product)
            _assert_pai_invariants(product)
            return False

    # AUTHORITATIVE RULE 2:
    # If PAI returns NO RESULTS for the searched part number(s), this is a valid
    # outcome and we must not emit any PAI SKU/cross/cost artifacts.
    if not found_any_match:
        try:
            ident = str(getattr(product, "sku", "") or getattr(product, "handle", "") or "").strip()
        except Exception:
            ident = ""
        logger.info(f"PAI_ENRICH_FAIL: NO_MATCH after {len(ordered_norm)} candidate(s) for '{ident}'")

        try:
            # Clear PAI artifacts from pricing. Keep pricing['pai'] as a legacy mirror
            # key so UI and tests can rely on its presence (with empty sku).
            for k in (
                "pai_cross",
                "pai_match",
                "pai_cost_source",
            ):
                pricing.pop(k, None)
        except Exception:
            pass

        # Source-of-truth fields
        try:
            setattr(product, "pai_status", "NO_MATCH")
        except Exception:
            pass
        try:
            setattr(product, "pai_sku", None)
        except Exception:
            pass
        try:
            setattr(product, "pai_alternates", [])
        except Exception:
            pass
        try:
            setattr(product, "pai_cost", 0.0)
        except Exception:
            pass

        # Mark row as "needs vendor" only when we still have no cost.
        try:
            cost_now = float(getattr(product, "cost", 0) or 0)
        except Exception:
            cost_now = 0.0
        if cost_now <= 0:
            try:
                pricing["vendor_needed"] = True
            except Exception:
                pass

        _legacy_mirror_pai_fields(product)
        _assert_pai_invariants(product)

    return False


def apply_pai_cost(product: Product, pai_client: _PAICrossLookup) -> None:
    """Apply a PAI-derived cost to a product.

    Rules:
    - Never override an existing positive cost
    - Exact-match only (delegated to client)
    - No pricing math, no validation, no UI logic
    """

    try:
        if float(getattr(product, "cost", 0) or 0) > 0:
            return
    except Exception:
        pass

    # MANDATORY search order:
    #   1) product.oem_pn
    #   2) product.secondary_pn
    #   3) product.primary_pn
    # Do NOT search handle. Do NOT search SKU. Do NOT search xrefs here.
    candidates: list[str] = []
    try:
        candidates.append(str(getattr(product, "oem_pn", "") or "").strip())
    except Exception:
        pass
    try:
        candidates.append(str(getattr(product, "secondary_pn", "") or "").strip())
    except Exception:
        pass
    try:
        candidates.append(str(getattr(product, "primary_pn", "") or "").strip())
    except Exception:
        pass

    if not any(candidates):
        # Legacy identity fields only (same order)
        try:
            candidates.append(str(getattr(product, "oem_part_number", "") or "").strip())
        except Exception:
            pass
        try:
            candidates.append(str(getattr(product, "secondary_part_number", "") or "").strip())
        except Exception:
            pass
        try:
            candidates.append(str(getattr(product, "primary_part_number", "") or "").strip())
        except Exception:
            pass

    seen: set[str] = set()
    normalized_candidates: list[str] = []

    def _is_engineish_norm(norm: str) -> bool:
        n = str(norm or "").strip().upper()
        if not n:
            return False
        if n in {"C15", "C13", "C12", "C16", "3406", "3406E", "ACERT", "ISX", "X15", "MP8", "D13", "D11", "D16"}:
            return True
        if len(n) <= 5 and any(ch.isdigit() for ch in n) and any(ch.isalpha() for ch in n):
            return True
        return False
    for raw in candidates:
        raw = str(raw or "").strip()
        if not raw:
            continue

        norm = normalize_part_number(raw)
        if not norm or norm in seen:
            continue
        if _is_engineish_norm(norm):
            continue
        seen.add(norm)
        normalized_candidates.append(norm)

    # Mandatory behavior: resolve SKU via cross first, then fetch cost by SKU.
    for norm_part in normalized_candidates:
        try:
            cross = pai_client.lookup_cross(norm_part)
        except Exception:
            cross = None
        if not isinstance(cross, dict):
            continue

        sku = normalize_part_number(str(cross.get("primary", "") or ""))
        if not sku:
            continue

        try:
            lookup = pai_client.lookup_cost(sku)
        except Exception:
            lookup = None
        if not isinstance(lookup, dict) or not lookup.get("cost") or float(lookup.get("cost") or 0) <= 0:
            continue

        sku_from_page = normalize_part_number(str(lookup.get("sku") or ""))
        if not sku_from_page:
            raise RuntimeError("Invariant violated: PAI lookup returned cost without SKU")

        cost_value = float(lookup.get("cost") or 0.0)

        try:
            product.cost = cost_value
        except Exception:
            return

        # Source-of-truth PAI fields
        try:
            setattr(product, "pai_cost", cost_value)
        except Exception:
            pass
        try:
            setattr(product, "pai_sku", sku_from_page)
        except Exception:
            pass
        try:
            alternates = cross.get("alternates", [])
            if not isinstance(alternates, list):
                alternates = []
            alternates = [normalize_part_number(a) for a in alternates]
            alternates = [a for a in alternates if a and a != sku_from_page]
            alternates = sorted(set(alternates))
            alternates = alternates[:5]
            setattr(product, "pai_alternates", list(alternates))
        except Exception:
            pass
        try:
            setattr(product, "pai_status", "EXACT")
        except Exception:
            pass

        try:
            if not isinstance(product.pricing, dict):
                product.pricing = {}
            product.pricing["cost_source"] = "PAI"
            product.pricing["pai_cost_source"] = sku_from_page
            product.pricing["pai_match"] = sku_from_page
            product.pricing.setdefault("cost_confidence", "supplier")
        except Exception:
            pass

        try:
            setattr(product, "cost_source", "PAI")
        except Exception:
            pass
        try:
            setattr(product, "pai_part_number", sku_from_page)
        except Exception:
            pass

        _legacy_mirror_pai_fields(product)
        _assert_pai_invariants(product)
        return


def enrich_pai_many(
    products: dict[str, Product],
    pai_client: _PAICrossLookup,
    *,
    keys: list[str] | None = None,
    image_dir: Path | str | None = None,
    download_images_if_zero: bool = True,
) -> dict[str, int]:
    """Re-enrich multiple existing products (no UI, deterministic).

    Returns counts by outcome bucket to support UI summaries.
    """

    if not isinstance(products, dict) or not products:
        return {"ok": 0, "multi": 0, "no_match": 0, "failed": 0}

    target_keys = keys if isinstance(keys, list) and keys else list(products.keys())
    _image_dir = Path(image_dir) if image_dir else None

    ok = 0
    multi = 0
    no_match = 0
    failed = 0

    for k in target_keys:
        prod = products.get(k)
        if prod is None:
            continue
        try:
            hit = enrich_pai(
                prod,
                pai_client,
                image_dir=_image_dir,
                download_images_if_zero=download_images_if_zero,
            )
            status = str(getattr(prod, "pai_status", "") or "").strip().upper()
            if status == "MULTI_MATCH":
                multi += 1
            elif status == "NO_MATCH":
                no_match += 1
            else:
                ok += 1 if hit else 0
                if (not hit) and (not status):
                    no_match += 1
        except Exception:
            failed += 1

    return {"ok": ok, "multi": multi, "no_match": no_match, "failed": failed}


def enrich_pai_many_parallel(
    products: dict[str, Product],
    pai_client: _PAICrossLookup,
    *,
    keys: list[str] | None = None,
    max_workers: int | None = None,
    image_dir: Path | str | None = None,
    download_images_if_zero: bool = True,
) -> dict[str, int]:
    """Parallel re-enrichment for many products.

    Notes:
    - Intended for UI batch refresh to overlap Playwright scraping and PAI lookups.
    - PAIClient enforces global rate limiting and is thread-safe via locks + thread-local sessions.
    
    Args:
        products: Dict of product key -> Product
        pai_client: PAI client for lookups
        keys: Specific keys to enrich (default: all)
        max_workers: Max parallel workers (default: 6)
        image_dir: Directory for PAI image downloads (products with 0 images)
        download_images_if_zero: Whether to download PAI images for products with no images
    """

    if not isinstance(products, dict) or not products:
        return {"ok": 0, "multi": 0, "no_match": 0, "failed": 0}

    target_keys = keys if isinstance(keys, list) and keys else list(products.keys())

    workers = max_workers
    if workers is None:
        try:
            workers = int(str(os.getenv("PAI_ENRICH_WORKERS", "6") or "6").strip())
        except Exception:
            workers = 6
    if workers < 1:
        workers = 1
    if workers > 16:
        workers = 16

    # Resolve image_dir path once
    _image_dir = Path(image_dir) if image_dir else None

    def _work(k: str) -> tuple[str, bool, str]:
        prod = products.get(k)
        if prod is None:
            return ("missing", False, "")
        hit = False
        try:
            hit = bool(enrich_pai(
                prod,
                pai_client,
                image_dir=_image_dir,
                download_images_if_zero=download_images_if_zero,
            ))
            status = str(getattr(prod, "pai_status", "") or "").strip().upper()
            return ("ok", hit, status)
        except Exception:
            return ("failed", hit, "")

    ok = 0
    multi = 0
    no_match = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=int(workers)) as ex:
        futs = [ex.submit(_work, str(k)) for k in target_keys]
        for fut in as_completed(futs):
            try:
                kind, hit, status = fut.result()
            except Exception:
                failed += 1
                continue

            if kind == "failed":
                failed += 1
                continue
            if kind == "missing":
                continue

            if status == "MULTI_MATCH":
                multi += 1
            elif status == "NO_MATCH":
                no_match += 1
            else:
                ok += 1 if hit else 0
                if (not hit) and (not status):
                    no_match += 1

    return {"ok": ok, "multi": multi, "no_match": no_match, "failed": failed}


def detect_pai_cross(product: Product, pai_client: _PAICrossLookup) -> None:
    """Detect an OEM→PAI cross and store it on the product.

    Critically:
    - Never mutates manufacturer
    - Never changes SKU
    - Never applies cost
    - Only runs after exact PAI cost lookup failed (cost still <= 0)
    """

    pricing = getattr(product, "pricing", None)
    if not isinstance(pricing, dict):
        try:
            product.pricing = {}
        except Exception:
            return
        pricing = product.pricing

    # If we've already found a cross, don't redo.
    if isinstance(pricing.get("pai_cross"), dict) and pricing["pai_cross"].get("pai"):
        return

    # OEM candidates: explicit OEM part, then URL-derived part, then xrefs, then SKU (stripped).
    candidates: list[str] = []

    try:
        oem_pn = str(getattr(product, "oem_part_number", "") or "").strip()
        if oem_pn:
            candidates.append(oem_pn)
    except Exception:
        pass

    try:
        from phases.scraper import extract_primary_part_from_url

        url = str(getattr(product, "url", "") or "").strip()
        if url:
            candidates.append(extract_primary_part_from_url(url))
    except Exception:
        pass

    try:
        for x in (getattr(product, "xrefs", None) or []):
            if x:
                candidates.append(str(x))
    except Exception:
        pass

    try:
        candidates.append(_strip_jaks_prefix(str(getattr(product, "sku", "") or "")))
    except Exception:
        pass

    seen: set[str] = set()
    for raw in candidates:
        norm = normalize_part_number(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        try:
            cross = pai_client.lookup_cross(norm)
        except Exception:
            cross = None
        if cross:
            primary = normalize_part_number(str(cross.get("primary", "") or ""))
            if not primary:
                return

            alternates = cross.get("alternates", [])
            if not isinstance(alternates, list):
                alternates = []
            alternates = [normalize_part_number(a) for a in alternates]
            alternates = [a for a in alternates if a and a != primary]
            alternates = sorted(set(alternates))
            alternates = alternates[:5]

            requires_click = bool(cross.get("requires_click", True))
            multi = bool(cross.get("multi", False))
            valid_single = (not multi) and (not requires_click)

            # Back-compat structure (used by existing UI bits)
            pricing["pai_cross"] = {
                "oem": norm,
                "pai": primary,
                "alternates": alternates,
                "multi": multi,
                "requires_click": requires_click,
                "valid": valid_single,
            }

            # Single source of truth structure
            pricing.setdefault("pai", {})
            if isinstance(pricing.get("pai"), dict):
                pricing["pai"].update(
                    {
                        "sku": primary,
                        "multi": multi,
                        "alternates": alternates,
                        "valid_single": valid_single,
                        "requires_click": requires_click,
                    }
                )
            return


def should_use_pai_supplier(product: Product) -> bool:
    """Single authoritative decision for when supplier is allowed to flip to PAI."""

    pricing = getattr(product, "pricing", None)
    if not isinstance(pricing, dict):
        return False

    # Explicit user action always wins.
    if bool(pricing.get("pai_cross_used", False)):
        return True

    pai = pricing.get("pai")
    if not isinstance(pai, dict):
        # Fallback to legacy.
        pai = pricing.get("pai_cross")
    if not isinstance(pai, dict):
        return False

    if not bool(pai.get("valid_single", False)) and not bool(pai.get("valid", False)):
        return False
    if bool(pai.get("multi", False)):
        return False

    return True


def apply_pai_supplier_gate(product: Product) -> None:
    """Apply the should_use_pai_supplier() decision to the product in one place."""

    pricing = getattr(product, "pricing", None)
    if not isinstance(pricing, dict):
        return

    # Only touch supplier for HHP-ish products that have PAI context.
    has_pai_context = bool(
        pricing.get("pai_cross_used")
        or isinstance(pricing.get("pai"), dict)
        or isinstance(pricing.get("pai_cross"), dict)
    )
    if not has_pai_context:
        return

    if should_use_pai_supplier(product):
        pai = pricing.get("pai") if isinstance(pricing.get("pai"), dict) else None
        if not isinstance(pai, dict):
            pai = pricing.get("pai_cross") if isinstance(pricing.get("pai_cross"), dict) else {}
        pai_part = normalize_part_number(str(pai.get("sku", "") or pai.get("pai", "") or ""))
        try:
            setattr(product, "supplier", "PAI")
        except Exception:
            pass
        if pai_part:
            try:
                setattr(product, "supplier_part_number", pai_part)
            except Exception:
                pass
            try:
                setattr(product, "pai_part_number", pai_part)
            except Exception:
                pass
        pricing["supplier"] = "PAI"
    else:
        try:
            # Keep supplier as HHP unless it was explicitly forced.
            setattr(product, "supplier", "HHP")
        except Exception:
            pass
        try:
            setattr(product, "supplier_part_number", "")
        except Exception:
            pass
        pricing["supplier"] = "HHP"


def apply_pai_cross_choice(product: Product, pai_client: _PAICrossLookup) -> bool:
    """Opt-in action: choose PAI as supplier for this OEM part and apply PAI cost.

    Critically:
    - Manufacturer stays OEM (never touched)
    - OEM identity (and SKU) remain stable
    - Supplier part number is recorded separately (pai_part_number)
    """

    pricing = getattr(product, "pricing", None)
    if not isinstance(pricing, dict):
        try:
            product.pricing = {}
        except Exception:
            return False
        pricing = product.pricing

    cross = pricing.get("pai_cross") if isinstance(pricing, dict) else None
    if not isinstance(cross, dict):
        return False

    pai_part = normalize_part_number(str(cross.get("pai", "") or ""))
    if not pai_part:
        return False

    alternates = cross.get("alternates", [])
    if not isinstance(alternates, list):
        alternates = []
    alternates = [normalize_part_number(a) for a in alternates]
    alternates = [a for a in alternates if a and a != pai_part]
    alternates = sorted(set(alternates))
    alternates = alternates[:5]

    # Record supplier part number (explicit user action).
    try:
        setattr(product, "supplier_part_number", pai_part)
    except Exception:
        pass
    try:
        setattr(product, "pai_part_number", pai_part)
    except Exception:
        pass

    # Explicit forced selection: record source-of-truth PAI fields.
    try:
        setattr(product, "pai_sku", pai_part)
    except Exception:
        pass
    try:
        setattr(product, "pai_alternates", list(alternates))
    except Exception:
        pass
    try:
        setattr(product, "pai_status", "FORCED")
    except Exception:
        pass

    _legacy_mirror_pai_fields(product)

    # Mark that we are explicitly using a cross.
    try:
        flags = getattr(product, "flags", None)
        if not isinstance(flags, list):
            setattr(product, "flags", [])
            flags = getattr(product, "flags", [])
        if "PAI_CROSS" not in flags:
            flags.append("PAI_CROSS")
    except Exception:
        pass

    # Pull cost for the PAI SKU.
    try:
        lookup = pai_client.lookup_cost(pai_part)
    except Exception:
        lookup = None

    if not isinstance(lookup, dict) or not lookup.get("cost") or float(lookup.get("cost") or 0) <= 0:
        pricing["pai_cross_used"] = True
        pricing["supplier"] = "PAI"
        pricing["cost_source"] = pricing.get("cost_source") or "PAI"
        pricing["pai_match"] = pai_part
        pricing.setdefault("cost_confidence", "supplier")
        try:
            setattr(product, "supplier", "PAI")
            setattr(product, "cost_source", pricing.get("cost_source") or "PAI")
        except Exception:
            pass
        return False

    sku_from_page = normalize_part_number(str(lookup.get("sku") or ""))
    if not sku_from_page:
        raise RuntimeError("Invariant violated: PAI lookup returned cost without SKU")
    if sku_from_page != normalize_part_number(pai_part):
        # Safety: user forced a specific SKU; refuse if the site returns a different SKU.
        return False

    cost_value = float(lookup.get("cost") or 0.0)

    try:
        product.cost = cost_value
    except Exception:
        return False

    try:
        setattr(product, "pai_cost", cost_value)
    except Exception:
        pass

    try:
        stock = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
        setattr(product, "pai_stock", dict(stock or {}))
    except Exception:
        pass

    _legacy_mirror_pai_fields(product)
    _assert_pai_invariants(product)

    pricing["pai_cross_used"] = True
    pricing["supplier"] = "PAI"
    pricing["cost_source"] = "PAI"
    pricing["pai_match"] = pai_part
    try:
        pricing["pai_stock"] = lookup.get("stock") if isinstance(lookup.get("stock"), dict) else {}
        pricing["pai_detail_url"] = str(lookup.get("url") or lookup.get("source") or "")
    except Exception:
        pass
    pricing.setdefault("cost_confidence", "supplier")
    try:
        setattr(product, "supplier", "PAI")
        setattr(product, "cost_source", "PAI")
    except Exception:
        pass
    return True


def clone_product_for_pai_alternate(product: Product, pai_sku: str) -> Product:
    """Create a new Product instance for a specific PAI alternate SKU.

    Intended for UI-driven "Add alternates as new parts" workflows.

    Notes:
      - Does not perform any network calls.
      - Clears cost fields so the caller can apply cost via apply_pai_cross_choice().
      - Preserves OEM identity/manufacturer fields.
    """

    clone = copy.deepcopy(product)

    # Clear pricing so we don't inherit unrelated overrides.
    try:
        clone.cost = 0.0
    except Exception:
        pass
    try:
        setattr(clone, "pai_cost", 0.0)
    except Exception:
        pass

    pricing = getattr(clone, "pricing", None)
    if not isinstance(pricing, dict):
        try:
            setattr(clone, "pricing", {})
        except Exception:
            pricing = {}
        pricing = getattr(clone, "pricing", {}) if isinstance(getattr(clone, "pricing", None), dict) else {}

    # Seed a cross choice so apply_pai_cross_choice() can be used directly.
    pai_part = normalize_part_number(str(pai_sku or ""))
    oem_val = ""
    try:
        oem_val = str(getattr(clone, "oem_pn", "") or "").strip()
    except Exception:
        oem_val = ""
    if not oem_val:
        try:
            oem_val = str(getattr(clone, "oem_part_number", "") or "").strip()
        except Exception:
            oem_val = ""

    pricing["pai_cross"] = {
        "oem": oem_val,
        "pai": pai_part,
        "alternates": [],
        "multi": False,
        "requires_click": False,
        "valid": True,
        "status": "FORCED",
    }

    # Ensure the clone isn't treated as already-used.
    try:
        pricing.pop("pai_cross_used", None)
    except Exception:
        pass

    # Keep supplier as HHP until the caller explicitly applies the cross choice.
    try:
        setattr(clone, "supplier", "HHP")
    except Exception:
        pass
    try:
        setattr(clone, "supplier_part_number", "")
    except Exception:
        pass
    try:
        setattr(clone, "pai_part_number", "")
    except Exception:
        pass
    try:
        setattr(clone, "pai_sku", None)
        setattr(clone, "pai_status", "MULTI_MATCH")
        setattr(clone, "pai_alternates", [])
    except Exception:
        pass

    _legacy_mirror_pai_fields(clone)
    return clone


def expand_pai_multi_match_to_products(
    product: Product,
    *,
    only_in_stock: bool = True,
    max_products: int = 3,
    image_dir: Path | str | None = None,
) -> list[Product]:
    """Expand a product with multiple PAI matches into additional products.
    
    When PAI search returns multiple matching products (e.g., different part variants),
    this function creates additional Product instances for each in-stock PAI SKU.
    Copies all product info (description, weight, ESN/CPL, etc.) to the new products.
    
    Args:
        product: Product with multiple PAI matches (MULTI_MATCH or advisory matches)
        only_in_stock: If True, only create products for in-stock PAI SKUs
        max_products: Maximum number of additional products to create
        image_dir: Directory to download PAI images for new products
    
    Returns:
        List of new Product instances (does NOT include the original)
    """
    # Get match details from multiple sources
    match_details = getattr(product, "pai_multi_matches", [])
    if not isinstance(match_details, list):
        match_details = []
    
    # Also check pai_advisory for multiple matches
    if not match_details:
        advisory = getattr(product, "pai_advisory", {})
        if isinstance(advisory, dict):
            adv_matches = advisory.get("pai_matches", [])
            if isinstance(adv_matches, list) and len(adv_matches) > 1:
                match_details = adv_matches
    
    # Fallback to pricing dict
    if not match_details:
        pricing = getattr(product, "pricing", {}) or {}
        pai_cross = pricing.get("pai_cross", {}) or {}
        match_details = pai_cross.get("match_details", [])
    
    if not match_details or len(match_details) < 2:
        return []
    
    # Filter to in-stock if requested
    if only_in_stock:
        match_details = [m for m in match_details if m.get("in_stock")]
    
    # Limit to max_products
    match_details = match_details[:max_products]
    
    if not match_details:
        return []
    
    new_products: list[Product] = []
    base_handle = str(getattr(product, "handle", "") or "").strip()
    base_sku = str(getattr(product, "sku", "") or "").strip()
    
    for i, match in enumerate(match_details):
        pai_sku = str(match.get("sku") or "").strip()
        if not pai_sku:
            continue
        
        # Create a clone of the product
        clone = copy.deepcopy(product)
        
        # Generate unique handle/SKU for this variant
        variant_suffix = f"-PAI{i+1}"
        new_handle = f"{base_handle}{variant_suffix}"
        new_sku = f"{base_sku}{variant_suffix}"
        
        try:
            setattr(clone, "handle", new_handle)
        except Exception:
            pass
        try:
            setattr(clone, "sku", new_sku)
        except Exception:
            pass
        
        # Set PAI fields from match details
        try:
            setattr(clone, "pai_sku", pai_sku)
        except Exception:
            pass
        try:
            setattr(clone, "pai_status", "EXACT")
        except Exception:
            pass
        try:
            setattr(clone, "pai_alternates", [])
        except Exception:
            pass
        try:
            setattr(clone, "pai_multi_matches", [])
        except Exception:
            pass
        try:
            setattr(clone, "pai_in_stock_count", 0)
        except Exception:
            pass
        
        # Set cost from match
        cost = match.get("cost")
        if cost and float(cost) > 0:
            try:
                setattr(clone, "pai_cost", float(cost))
            except Exception:
                pass
            try:
                setattr(clone, "cost", float(cost))
            except Exception:
                pass
        
        # Set stock info
        stock = match.get("stock", {})
        try:
            setattr(clone, "pai_stock", dict(stock) if isinstance(stock, dict) else {})
        except Exception:
            pass
        
        # Update pricing dict
        pricing = getattr(clone, "pricing", {}) or {}
        if not isinstance(pricing, dict):
            pricing = {}
        pricing["pai_cross"] = {
            "oem": str(getattr(product, "oem_pn", "") or ""),
            "pai": pai_sku,
            "alternates": [],
            "multi": False,
            "requires_click": False,
            "valid": True,
            "status": "EXACT",
            "detail_url": match.get("url", ""),
            "from_multi_match": True,
        }
        pricing["pai_match"] = pai_sku
        pricing["pai_cost_source"] = pai_sku
        pricing["cost_source"] = "PAI"
        pricing["stage"] = 3  # PAI checked
        try:
            setattr(clone, "pricing", pricing)
        except Exception:
            pass
        
        # Download PAI images if directory provided and product has no images
        image_urls = match.get("image_urls", [])
        if image_dir and image_urls:
            try:
                img_count = int(getattr(clone, "image_count_raw", 0) or 0)
            except Exception:
                img_count = 0
            if img_count == 0:
                try:
                    out_path = Path(image_dir) / new_handle
                    downloaded = download_pai_images_for_product(
                        clone, image_urls, out_path, handle=new_handle
                    )
                    if downloaded > 0:
                        logger.info(f"[PAI Multi] Downloaded {downloaded} images for {new_handle}")
                except Exception as e:
                    logger.debug(f"[PAI Multi] Failed to download images for {new_handle}: {e}")
        
        # Set supplier to PAI
        try:
            setattr(clone, "supplier", "PAI")
        except Exception:
            pass
        try:
            setattr(clone, "supplier_part_number", pai_sku)
        except Exception:
            pass
        
        # Add flag to indicate this is from multi-match expansion
        flags = getattr(clone, "flags", []) or []
        if not isinstance(flags, list):
            flags = []
        if "PAI_MULTI_EXPANDED" not in flags:
            flags.append("PAI_MULTI_EXPANDED")
        try:
            setattr(clone, "flags", flags)
        except Exception:
            pass
        
        _legacy_mirror_pai_fields(clone)
        new_products.append(clone)
    
    return new_products


def get_in_stock_pai_options(product: Product) -> list[dict]:
    """Get list of in-stock PAI options for a product with multiple PAI matches.
    
    Works with MULTI_MATCH status or products with multiple matches in pai_advisory.
    
    Returns list of dicts with: sku, cost, stock, url, in_stock
    """
    # Check pai_multi_matches first (set by MULTI_MATCH handling)
    match_details = getattr(product, "pai_multi_matches", [])
    if not isinstance(match_details, list):
        match_details = []
    
    # Also check pai_advisory for multiple matches
    if not match_details:
        advisory = getattr(product, "pai_advisory", {})
        if isinstance(advisory, dict):
            adv_matches = advisory.get("pai_matches", [])
            if isinstance(adv_matches, list) and len(adv_matches) > 1:
                match_details = adv_matches
    
    # Fallback to pricing dict
    if not match_details:
        pricing = getattr(product, "pricing", {}) or {}
        pai_cross = pricing.get("pai_cross", {}) or {}
        match_details = pai_cross.get("match_details", [])
    
    if not match_details:
        return []
    
    # Return only in-stock options
    return [m for m in match_details if isinstance(m, dict) and m.get("in_stock")]


def adopt_pai_cross_reference(product: Product) -> bool:
    """Adopt a pending PAI cross-reference SKU.
    
    When PAI returns a different SKU than searched (cross-reference),
    call this function to apply the PAI SKU/cost to the product.
    
    Returns True if successfully adopted, False if no cross-reference available.
    
    Example:
        - Searched: '5579308' (HHP/OEM part)
        - PAI returned: 'ISX141386' with cost $123.45
        - After adopt: product.pai_sku = 'ISX141386', product.pai_cost = 123.45
    """
    cross_ref = getattr(product, "pai_cross_reference", {})
    if not isinstance(cross_ref, dict) or not cross_ref:
        # Also check pricing dict
        pricing = getattr(product, "pricing", {}) or {}
        cross_ref = pricing.get("pai_cross_reference", {})
        
    if not cross_ref or not cross_ref.get("pai_sku"):
        logger.warning(f"No PAI cross-reference available to adopt for product")
        return False
    
    if cross_ref.get("adopted"):
        logger.info(f"PAI cross-reference already adopted: {cross_ref.get('pai_sku')}")
        return True
    
    pai_sku = str(cross_ref.get("pai_sku") or "").strip()
    cost = float(cross_ref.get("cost") or 0.0)
    stock = cross_ref.get("stock", {})
    detail_url = str(cross_ref.get("detail_url") or "")
    searched = str(cross_ref.get("searched_sku") or "")
    
    logger.info(f"Adopting PAI cross-reference: '{searched}' → '{pai_sku}' @ ${cost:.2f}")
    
    # Apply PAI data to product
    try:
        setattr(product, "pai_sku", pai_sku)
    except Exception:
        pass
    try:
        setattr(product, "pai_cost", cost)
    except Exception:
        pass
    try:
        setattr(product, "pai_part_number", pai_sku)
    except Exception:
        pass
    try:
        setattr(product, "supplier_part_number", pai_sku)
    except Exception:
        pass
    try:
        setattr(product, "pai_stock", dict(stock or {}))
    except Exception:
        pass
    try:
        setattr(product, "pai_status", "CROSS_REF_ADOPTED")
    except Exception:
        pass
    try:
        setattr(product, "cost_source", "PAI")
    except Exception:
        pass
    try:
        product.cost = cost
    except Exception:
        pass
    
    # Mark as adopted
    cross_ref["adopted"] = True
    cross_ref["adopted_at"] = _phase3_now_iso()
    try:
        setattr(product, "pai_cross_reference", cross_ref)
    except Exception:
        pass
    
    # Update pricing dict
    pricing = getattr(product, "pricing", {}) or {}
    if not isinstance(pricing, dict):
        pricing = {}
    pricing["pai_cross_reference"] = cross_ref
    pricing["cost_source"] = "PAI"
    pricing["pai_cost_source"] = pai_sku
    pricing["pai_match"] = pai_sku
    pricing["pai_detail_url"] = detail_url
    pricing.setdefault("cost_confidence", "supplier")
    pricing["pai_stock"] = stock
    
    try:
        setattr(product, "pricing", pricing)
    except Exception:
        pass
    
    # Add flag
    flags = getattr(product, "flags", []) or []
    if not isinstance(flags, list):
        flags = []
    if "PAI_CROSS_REF_ADOPTED" not in flags:
        flags.append("PAI_CROSS_REF_ADOPTED")
    try:
        setattr(product, "flags", flags)
    except Exception:
        pass
    
    return True


def get_pai_cross_reference(product: Product) -> dict | None:
    """Get pending PAI cross-reference for a product.
    
    Returns cross-reference dict if available and not yet adopted, else None.
    
    Dict format:
        {
            "searched_sku": "5579308",
            "pai_sku": "ISX141386", 
            "cost": 123.45,
            "stock": {...},
            "detail_url": "https://...",
            "adopted": False
        }
    """
    cross_ref = getattr(product, "pai_cross_reference", {})
    if not isinstance(cross_ref, dict) or not cross_ref:
        # Also check pricing dict
        pricing = getattr(product, "pricing", {}) or {}
        cross_ref = pricing.get("pai_cross_reference", {})
    
    if not cross_ref or not cross_ref.get("pai_sku"):
        return None
    
    return cross_ref
