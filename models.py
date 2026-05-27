from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any


class ErrorCode(str, Enum):
    ERROR_NO_TITLE = "ERROR_NO_TITLE"
    ERROR_NO_PRICE = "ERROR_NO_PRICE"
    ERROR_BAD_SKU = "ERROR_BAD_SKU"
    ERROR_NO_IMAGES = "ERROR_NO_IMAGES"
    ERROR_NO_COST = "ERROR_NO_COST"


ERROR_MESSAGES: dict[str, str] = {
    ErrorCode.ERROR_NO_TITLE.value: "No product title found",
    ErrorCode.ERROR_NO_PRICE.value: "Missing or zero price",
    ErrorCode.ERROR_BAD_SKU.value: "Bad/unknown SKU (UNKNOWN_PART)",
    ErrorCode.ERROR_NO_IMAGES.value: "No images detected",
    ErrorCode.ERROR_NO_COST.value: "Missing or zero cost",
}


def _coerce_money(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, bool):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        if not s:
            return 0.0
        # Allow "$1,234.56" or "1,234.56"
        s = s.replace("$", "").replace(",", "")
        return float(s)
    except Exception:
        return 0.0


def _get_cost_from_fields(prod: object, *, attr_names: list[str], pricing_keys: list[str]) -> float:
    pricing = getattr(prod, "pricing", {}) or {}
    for name in attr_names:
        if hasattr(prod, name):
            v = _coerce_money(getattr(prod, name))
            if v > 0:
                return v
    for key in pricing_keys:
        if isinstance(pricing, dict) and key in pricing:
            v = _coerce_money(pricing.get(key))
            if v > 0:
                return v
    return 0.0


def resolve_cost(prod: object) -> tuple[float, str | None]:
    """Resolve cost from preferred upstream sources.

    Rule:
      - Resolved Cost = PAI_cost if exists else HHP_cost if exists else 0

    Notes:
      - Today, many flows only populate `prod.cost` (editable). We treat that as
        a fallback for HHP cost until a dedicated `hhp_cost` field exists.
    """

    pai_cost = _get_cost_from_fields(
        prod,
        attr_names=["pai_cost", "PAI_cost", "paiCost"],
        pricing_keys=["pai_cost", "PAI_cost", "paiCost"],
    )
    if pai_cost > 0:
        return float(pai_cost), "PAI"

    hhp_cost = _get_cost_from_fields(
        prod,
        attr_names=["hhp_cost", "HHP_cost", "hhpCost"],
        pricing_keys=["hhp_cost", "HHP_cost", "hhpCost"],
    )
    if hhp_cost > 0:
        return float(hhp_cost), "HHP"

    # Back-compat / current UI: `cost` is the only cost field.
    cost_val = _coerce_money(getattr(prod, "cost", 0.0))
    if cost_val > 0:
        return float(cost_val), "HHP"

    return 0.0, None


def resolve_selling_price(
    prod: object,
    *,
    markup_pct: float,
    include_core: bool,
) -> tuple[float, float, str | None]:
    """Return (resolved_cost, selling_price, source)."""
    resolved_cost, source = resolve_cost(prod)

    # Optional UI override: a user-specified selling price.
    pricing = getattr(prod, "pricing", {}) or {}
    try:
        if isinstance(pricing, dict):
            override = _coerce_money(pricing.get("price_override"))
            if float(override) > 0:
                return float(resolved_cost), float(override), "OVERRIDE"
    except Exception:
        pass

    # Primary behavior: when we have a cost, apply markup.
    if float(resolved_cost) > 0:
        m = _coerce_money(markup_pct)
        selling = float(resolved_cost) * (1.0 + (float(m) / 100.0))
    else:
        # Authoritative behavior for "no vendor cost": fall back to scraped HHP
        # price so the item can remain sellable.
        selling = 0.0
        try:
            if isinstance(pricing, dict):
                selling = _coerce_money(pricing.get("price", 0) or 0)
        except Exception:
            selling = 0.0
        source = "HHP_PRICE" if float(selling) > 0 else source

    if include_core:
        pricing = getattr(prod, "pricing", {}) or {}
        core = 0.0
        try:
            if isinstance(pricing, dict):
                core = _coerce_money(pricing.get("core_charge", 0) or 0)
        except Exception:
            core = 0.0
        selling += float(core)

    return float(resolved_cost), float(selling), source

@dataclass
class Product:
    sku: str
    handle: str
    url: str
    title: str
    description_jaks_html: str
    brand: str
    engine: str
    cpl_prefix: str = ""
    dimensions: str = ""
    xrefs: List[str] = field(default_factory=list)
    pricing: Dict = field(default_factory=dict)
    image_count_raw: int = 0
    image_count_watermarked: int = 0
    scraped_at: str = ""
    cpl_list: List[str] = field(default_factory=list)
    kit_contents: List[str] = field(default_factory=list)
    engine_model_tags: List[str] = field(default_factory=list)
    category: str = ""
    manufacturer: str = ""
    # Source-of-truth identity fields (do not overload SKU/handle for these meanings)
    primary_pn: str = ""  # HHP primary product ID (from URL)
    secondary_pn: str = ""  # HHP cross / legacy PN (from URL, optional)
    oem_pn: str = ""  # OEM PN (from HHP page, optional)
    
    # Family grouping (from Phase 1.5 Analyzer)
    family_id: str = ""  # FAM-XXXXX format, shared by all products in same family

    # PAI source-of-truth fields (do not overload downstream SKU/handle/supplier_part_number)
    pai_sku: str | None = None
    pai_alternates: List[str] = field(default_factory=list)
    pai_status: str = ""  # NOT_FOUND | MULTI | FOUND | FORCED | CROSS_REF
    pai_cost: float = 0.0
    pai_stock: Dict[str, Any] = field(default_factory=dict)
    
    # PAI cross-reference: when search returns a different SKU (user can choose to adopt)
    pai_cross_reference: Dict[str, Any] = field(default_factory=dict)
    # Format: {"searched_sku": "5579308", "pai_sku": "ISX141386", "cost": 123.45, 
    #          "adopted": False, "detail_url": "...", "stock": {...}}
    
    # PAI multi-match details: when search returns multiple products
    # Each entry: {"sku": str, "cost": float|None, "stock": dict, "in_stock": bool, "url": str, "image_urls": list}
    pai_multi_matches: List[Dict[str, Any]] = field(default_factory=list)
    pai_in_stock_count: int = 0  # How many PAI alternates are in stock

    # Phase 3 (Advisory-only) PAI enrichment payload.
    # IMPORTANT: This is read-only advisory data and must not be used to
    # automatically mutate SKU/Supplier/Cost/Stage.
    # Expected keys include:
    #   pai_matches, pai_primary_sku, pai_cost, pai_stock,
    #   match_confidence, match_strategy, flags, enriched_at, detail_url
    pai_advisory: Dict[str, Any] = field(default_factory=dict)

    # Legacy identity fields (kept for back-compat; prefer *_pn above)
    primary_part_number: str = ""  # URL-leading part token (legacy)
    secondary_part_number: str = ""  # URL-trailing part token (legacy)
    oem_part_number: str = ""  # OEM PN (legacy)
    supplier: str = ""  # Who we buy from (e.g., "HHP", "PAI", "OEM")
    supplier_part_number: str = ""  # Supplier SKU/part (e.g., PAI "311148")
    pai_part_number: str = ""  # Back-compat alias (prefer supplier_part_number)
    oem_group: str = ""  # Canonical OEM family (e.g., "Volvo Group")
    cost_source: str = ""  # e.g., "PAI", "HHP" (best-effort convenience)
    flags: List[str] = field(default_factory=list)  # e.g., ["PAI_CROSS"]
    unknown_part: bool = False
    cost: float = 0.0  # ← ADD THIS LINE if missing
    weight: str = ""
    pai_options_count: int = 0
    
    # Category-specific spec fields (for template-based descriptions)
    flow_rate: str = ""  # For fuel injectors
    pressure_rating: str = ""  # For oil pumps
    material: str = ""  # For gaskets
    bore_size: str = ""  # For pistons/liners
    turbo_type: str = ""  # For turbochargers

    # Image source tracking (paths relative to product folder)
    images_hhp: List[str] = field(default_factory=list)  # HHP scraped images
    images_atl: List[str] = field(default_factory=list)  # ATL Diesel images
    images_pai: List[str] = field(default_factory=list)  # PAI portal images
    images_selected: List[str] = field(default_factory=list)  # User-selected images for upload
    
    # ATL Diesel enrichment data (advisory, like PAI)
    atl_advisory: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure `pricing` is always a dict and stage is always >= 1.
        if not isinstance(self.pricing, dict):
            self.pricing = {}
        try:
            st = int(self.pricing.get("stage") or 1)
        except Exception:
            st = 1
        if st < 1:
            st = 1
        self.pricing["stage"] = st


@dataclass
class ScrapeResult:
    product: Product | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)