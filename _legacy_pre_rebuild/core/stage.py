"""Stage management: single source of truth for product lifecycle state.

Stage values:
    1 = 🔍 Discovered (URL + part# only, no scrape data)
    2 = 🧱 Scraped (full product data)
    3 = 🧪 PAI Checked (cost enrichment attempted)
    4 = 🧠 Reviewed (manually verified)
    5 = 🚀 Uploaded (pushed to Shopify)

RULES:
    - Stage is stored in product.pricing["stage"]
    - Stage 0 is NEVER valid (normalize to 1)
    - Stage can only increase (no downgrades)
    - Stage 2 requires scraped_at timestamp
"""

from __future__ import annotations

__all__ = [
    "STAGE_EMOJI",
    "STAGE_TOOLTIP_ALL",
    "get_stage",
    "set_stage",
    "promote_stage",
    "coerce_stage_min1",
    "looks_scraped",
    "normalize_stage_on_load",
    "stage_sort_key",
]

STAGE_EMOJI: dict[int, str] = {
    1: "🔍",  # Discovered
    2: "🧱",  # Scraped
    3: "🧪",  # PAI Checked
    4: "🧠",  # Reviewed
    5: "🚀",  # Uploaded
}

STAGE_TOOLTIP_ALL = "\n".join([
    "🔍 Discovered",
    "🧱 Scraped",
    "🧪 PAI Checked",
    "🧠 Reviewed",
    "🚀 Uploaded",
])


def coerce_stage_min1(v: object) -> int:
    """Coerce any value to a valid stage (minimum 1)."""
    try:
        s = str(v).strip()
    except Exception:
        return 1
    if not s:
        return 1
    try:
        i = int(s)
    except Exception:
        return 1
    return i if i >= 1 else 1


def _ensure_pricing_dict(prod: object) -> dict:
    """Return product.pricing dict, creating if needed."""
    try:
        pr = getattr(prod, "pricing", None)
    except Exception:
        pr = None
    if not isinstance(pr, dict):
        pr = {}
        try:
            setattr(prod, "pricing", pr)
        except Exception:
            pass
    return pr


def get_stage(prod: object) -> int:
    """Read stage from product (never returns < 1)."""
    pr = _ensure_pricing_dict(prod)
    return coerce_stage_min1(pr.get("stage"))


def set_stage(prod: object, stage: int) -> None:
    """Write stage to product (enforces minimum 1)."""
    pr = _ensure_pricing_dict(prod)
    pr["stage"] = coerce_stage_min1(stage)


def promote_stage(prod: object, target: int) -> bool:
    """Promote stage to target if current < target. Returns True if promoted."""
    current = get_stage(prod)
    target = coerce_stage_min1(target)
    if target > current:
        set_stage(prod, target)
        return True
    return False


def looks_scraped(prod: object) -> bool:
    """Heuristic: does this product have data consistent with a successful scrape?
    
    NOTE: Title alone is NOT sufficient - discovered products have titles from Phase 1.
    We need evidence of actual scraping: description, images, or scraped_at timestamp.
    """
    pr = _ensure_pricing_dict(prod)
    
    # Check pricing.last_scraped_at
    try:
        if str(pr.get("last_scraped_at", "") or "").strip():
            return True
    except Exception:
        pass

    # Check product.scraped_at
    try:
        if str(getattr(prod, "scraped_at", "") or "").strip():
            return True
    except Exception:
        pass

    # NOTE: Title alone is NOT evidence of scraping - Phase 1 discovers titles too!
    # Only check title if accompanied by description or images
    
    # Check product.description_jaks_html - THIS is real scrape evidence
    try:
        if str(getattr(prod, "description_jaks_html", "") or "").strip():
            return True
    except Exception:
        pass

    # Check product.image_count_raw - THIS is real scrape evidence
    try:
        if int(getattr(prod, "image_count_raw", 0) or 0) > 0:
            return True
    except Exception:
        pass
    
    # Check for local_images - evidence of actual image download
    try:
        local_imgs = getattr(prod, "local_images", None) or []
        if isinstance(local_imgs, list) and len(local_imgs) > 0:
            return True
    except Exception:
        pass

    return False


def normalize_stage_on_load(prod: object) -> None:
    """Normalize stage when loading from persistence.
    
    - Stage 0/missing → Stage 1
    - If scraped_at exists and stage < 2 → Stage 2
    """
    pr = _ensure_pricing_dict(prod)
    st = coerce_stage_min1(pr.get("stage"))
    
    # Promote to Stage 2 only if scraped_at timestamp exists
    scraped_at = ""
    try:
        scraped_at = str(getattr(prod, "scraped_at", "") or "").strip()
    except Exception:
        pass
    
    if st < 2 and scraped_at:
        pr["stage"] = 2
    else:
        pr["stage"] = st


def stage_sort_key(stage_i: int, last_scraped_at: object) -> str:
    """Generate sort key: stage asc, then timestamp asc (NULLs first)."""
    s = str(last_scraped_at or "").strip()
    if s.endswith("Z"):
        s = s[:-1]
    return f"{int(stage_i):02d}|{s}"
