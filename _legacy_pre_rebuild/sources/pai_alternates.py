"""PAI Alternates Detection Module.

Detects PAI product variants (HP, Economy, Size) from portal data
and enables bulk creation of related SKUs.

Variant Types:
- Quality: HP (High Performance), E (Economy), Standard
- Size: -010, -020, -030 (oversizes)
- Package: Kit vs individual components
"""

from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from utils.part_normalizer import normalize_part_number


logger = logging.getLogger(__name__)


class VariantType(Enum):
    """Types of PAI product variants."""
    STANDARD = "standard"
    HIGH_PERFORMANCE = "hp"
    ECONOMY = "economy"
    OVERSIZE_010 = "010"
    OVERSIZE_020 = "020"
    OVERSIZE_030 = "030"
    UNDERSIZE_010 = "-010"


@dataclass
class PAIVariant:
    """A detected PAI product variant."""
    part_number: str
    variant_type: VariantType
    base_part_number: str
    display_name: str
    price: Optional[float] = None
    in_stock: bool = False
    stock_qty: Optional[int] = None
    
    @property
    def suffix(self) -> str:
        """Get the suffix for this variant type."""
        suffixes = {
            VariantType.STANDARD: "",
            VariantType.HIGH_PERFORMANCE: "HP",
            VariantType.ECONOMY: "E",
            VariantType.OVERSIZE_010: "-010",
            VariantType.OVERSIZE_020: "-020",
            VariantType.OVERSIZE_030: "-030",
            VariantType.UNDERSIZE_010: "--010",
        }
        return suffixes.get(self.variant_type, "")
    
    @property
    def sku_suffix(self) -> str:
        """Get SKU suffix for JAKS SKU generation."""
        # Convert variant type to clean SKU suffix
        if self.variant_type == VariantType.STANDARD:
            return ""
        elif self.variant_type == VariantType.HIGH_PERFORMANCE:
            return "-HP"
        elif self.variant_type == VariantType.ECONOMY:
            return "-E"
        else:
            # Size variants
            return f"-{self.variant_type.value}"


@dataclass
class AlternatesDetectionResult:
    """Result of alternates detection for a part number."""
    base_part_number: str
    base_part_normalized: str
    standard: Optional[PAIVariant] = None
    variants: list[PAIVariant] = field(default_factory=list)
    total_found: int = 0
    detection_source: str = "pattern"  # pattern, pai_portal, cache
    error: Optional[str] = None
    
    @property
    def has_variants(self) -> bool:
        """Check if any variants were found."""
        return len(self.variants) > 0
    
    @property
    def all_variants(self) -> list[PAIVariant]:
        """Get all variants including standard."""
        result = []
        if self.standard:
            result.append(self.standard)
        result.extend(self.variants)
        return result
    
    def get_variant(self, variant_type: VariantType) -> Optional[PAIVariant]:
        """Get a specific variant by type."""
        if self.standard and self.standard.variant_type == variant_type:
            return self.standard
        for v in self.variants:
            if v.variant_type == variant_type:
                return v
        return None


def detect_variant_type(part_number: str) -> tuple[str, VariantType]:
    """Detect variant type from a part number.
    
    Args:
        part_number: PAI part number (e.g., ISX119145HP)
    
    Returns:
        Tuple of (base_part_number, variant_type)
    """
    pn = part_number.strip().upper()
    
    # Check for HP suffix
    if pn.endswith("HP"):
        return pn[:-2], VariantType.HIGH_PERFORMANCE
    
    # Check for E suffix (but not part of base like "SLEEVE")
    if pn.endswith("E") and len(pn) > 3:
        # Make sure it's not a word ending in E
        base = pn[:-1]
        if base[-1].isdigit():  # ISX119145E → ISX119145 + E
            return base, VariantType.ECONOMY
    
    # Check for size suffixes
    size_patterns = [
        (r"(.+)-010$", VariantType.OVERSIZE_010),
        (r"(.+)-020$", VariantType.OVERSIZE_020),
        (r"(.+)-030$", VariantType.OVERSIZE_030),
        (r"(.+)010$", VariantType.OVERSIZE_010),  # Without dash
        (r"(.+)020$", VariantType.OVERSIZE_020),
        (r"(.+)030$", VariantType.OVERSIZE_030),
    ]
    
    for pattern, variant_type in size_patterns:
        match = re.match(pattern, pn)
        if match:
            base = match.group(1)
            # Make sure base isn't too short
            if len(base) >= 6:
                return base, variant_type
    
    # Standard - no variant suffix
    return pn, VariantType.STANDARD


def generate_variant_part_numbers(base_part: str) -> dict[VariantType, str]:
    """Generate all possible variant part numbers from a base.
    
    Args:
        base_part: Base PAI part number (e.g., ISX119145)
    
    Returns:
        Dict mapping variant type to full part number
    """
    base = base_part.strip().upper()
    
    # Remove any existing variant suffix first
    base, _ = detect_variant_type(base)
    
    return {
        VariantType.STANDARD: base,
        VariantType.HIGH_PERFORMANCE: f"{base}HP",
        VariantType.ECONOMY: f"{base}E",
        VariantType.OVERSIZE_010: f"{base}-010",
        VariantType.OVERSIZE_020: f"{base}-020",
        VariantType.OVERSIZE_030: f"{base}-030",
    }


def detect_alternates_from_pattern(part_number: str) -> AlternatesDetectionResult:
    """Detect alternates using pattern matching only.
    
    This is fast but doesn't verify if variants actually exist.
    Use for initial detection before PAI portal verification.
    
    Args:
        part_number: PAI part number to analyze
    
    Returns:
        AlternatesDetectionResult with potential variants
    """
    base, detected_type = detect_variant_type(part_number)
    normalized = normalize_part_number(part_number)
    
    result = AlternatesDetectionResult(
        base_part_number=base,
        base_part_normalized=normalize_part_number(base),
        detection_source="pattern",
    )
    
    # Generate all possible variants
    variants = generate_variant_part_numbers(base)
    
    # Standard is always the base
    result.standard = PAIVariant(
        part_number=variants[VariantType.STANDARD],
        variant_type=VariantType.STANDARD,
        base_part_number=base,
        display_name="Standard",
    )
    
    # Add quality variants (HP, E) as potential
    for variant_type in [VariantType.HIGH_PERFORMANCE, VariantType.ECONOMY]:
        result.variants.append(PAIVariant(
            part_number=variants[variant_type],
            variant_type=variant_type,
            base_part_number=base,
            display_name=variant_type.value.upper() if variant_type != VariantType.ECONOMY else "Economy",
        ))
    
    # Size variants only for parts that commonly have them
    # (pistons, rings, bearings, sleeves)
    if _is_size_variant_candidate(base):
        for variant_type in [VariantType.OVERSIZE_010, VariantType.OVERSIZE_020, VariantType.OVERSIZE_030]:
            result.variants.append(PAIVariant(
                part_number=variants[variant_type],
                variant_type=variant_type,
                base_part_number=base,
                display_name=f".{variant_type.value} Oversize",
            ))
    
    result.total_found = len(result.all_variants)
    return result


def _is_size_variant_candidate(part_number: str) -> bool:
    """Check if a part number is likely to have size variants.
    
    Size variants (.010, .020, .030 oversize) are typically only
    available for machined components like pistons, rings, bearings.
    """
    pn_upper = part_number.upper()
    
    # PAI part number patterns that typically have size variants
    size_variant_patterns = [
        r"PISTON",
        r"RING",
        r"BEARING",
        r"SLEEVE",
        r"LINER",
        r"^C\d{2}",  # C15, C13 pistons
        r"^ISX.*PST",  # ISX pistons
        r"^N14.*PST",  # N14 pistons
    ]
    
    for pattern in size_variant_patterns:
        if re.search(pattern, pn_upper):
            return True
    
    return False


def detect_alternates_from_pai_portal(
    part_number: str,
    pai_client=None,
) -> AlternatesDetectionResult:
    """Detect alternates by querying PAI portal.
    
    This verifies which variants actually exist and have stock/pricing.
    
    Args:
        part_number: PAI part number to analyze
        pai_client: Optional PAIClient instance (will create if not provided)
    
    Returns:
        AlternatesDetectionResult with verified variants
    """
    base, detected_type = detect_variant_type(part_number)
    
    result = AlternatesDetectionResult(
        base_part_number=base,
        base_part_normalized=normalize_part_number(base),
        detection_source="pai_portal",
    )
    
    # Generate all possible variant part numbers
    variant_pns = generate_variant_part_numbers(base)
    
    # Try to get or create PAI client
    if pai_client is None:
        try:
            from sources.pai_client import PAIClient
            pai_client = PAIClient()
            if not pai_client.login():
                result.error = "Failed to login to PAI portal"
                return result
        except Exception as e:
            result.error = f"PAI client error: {e}"
            return result
    
    # Check each variant against PAI portal
    for variant_type, pn in variant_pns.items():
        try:
            lookup_result = pai_client.lookup_cost(pn)
            
            if lookup_result and lookup_result.get("cost"):
                variant = PAIVariant(
                    part_number=pn,
                    variant_type=variant_type,
                    base_part_number=base,
                    display_name=_get_variant_display_name(variant_type),
                    price=lookup_result.get("cost"),
                    in_stock=bool(lookup_result.get("stock", {}).get("in_stock")),
                    stock_qty=lookup_result.get("stock", {}).get("quantity"),
                )
                
                if variant_type == VariantType.STANDARD:
                    result.standard = variant
                else:
                    result.variants.append(variant)
                    
        except Exception as e:
            logger.debug(f"PAI lookup failed for {pn}: {e}")
            continue
    
    result.total_found = len(result.all_variants)
    return result


def _get_variant_display_name(variant_type: VariantType) -> str:
    """Get human-readable display name for variant type."""
    names = {
        VariantType.STANDARD: "Standard",
        VariantType.HIGH_PERFORMANCE: "High Performance",
        VariantType.ECONOMY: "Economy",
        VariantType.OVERSIZE_010: ".010 Oversize",
        VariantType.OVERSIZE_020: ".020 Oversize",
        VariantType.OVERSIZE_030: ".030 Oversize",
        VariantType.UNDERSIZE_010: ".010 Undersize",
    }
    return names.get(variant_type, variant_type.value)


def generate_variant_skus(
    base_sku: str,
    variants: list[PAIVariant],
) -> dict[str, str]:
    """Generate JAKS SKUs for detected variants.
    
    Args:
        base_sku: Base JAKS SKU (e.g., JAKS-ISX119145)
        variants: List of detected variants
    
    Returns:
        Dict mapping variant part number to JAKS SKU
    """
    result = {}
    
    for variant in variants:
        if variant.variant_type == VariantType.STANDARD:
            result[variant.part_number] = base_sku
        else:
            result[variant.part_number] = f"{base_sku}{variant.sku_suffix}"
    
    return result


@dataclass
class AlternateProduct:
    """Product data for creating an alternate/variant in the system."""
    pai_part_number: str
    jaks_sku: str
    title: str
    variant_type: VariantType
    price: Optional[float] = None
    cost: Optional[float] = None
    family_id: Optional[str] = None
    

def prepare_alternate_products(
    base_product_title: str,
    base_sku: str,
    detection_result: AlternatesDetectionResult,
    margin: float = 0.25,
) -> list[AlternateProduct]:
    """Prepare alternate products for bulk creation.
    
    Args:
        base_product_title: Title of the base product
        base_sku: Base JAKS SKU
        detection_result: Result from alternates detection
        margin: Default margin for price calculation (0.25 = 25%)
    
    Returns:
        List of AlternateProduct ready for creation
    """
    products = []
    
    for variant in detection_result.all_variants:
        # Skip standard if it's the same as base
        if variant.variant_type == VariantType.STANDARD:
            continue
        
        # Generate variant title
        title = f"{base_product_title} - {variant.display_name}"
        
        # Generate variant SKU
        sku = f"{base_sku}{variant.sku_suffix}"
        
        # Calculate price from cost if available
        price = None
        cost = variant.price  # PAI portal returns cost
        if cost:
            price = round(cost / (1 - margin), 2)
        
        products.append(AlternateProduct(
            pai_part_number=variant.part_number,
            jaks_sku=sku,
            title=title,
            variant_type=variant.variant_type,
            price=price,
            cost=cost,
            family_id=detection_result.base_part_normalized,
        ))
    
    return products
