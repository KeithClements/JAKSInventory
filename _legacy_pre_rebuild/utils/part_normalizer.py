"""Centralized part number normalization.

ALL part number storage and comparison MUST go through this module.
This ensures consistent matching across:
- Database storage (part_numbers.number_normalized)
- Cross-reference lookups (xref_chains)
- Shopify metafields
- Search queries

Rule: Store normalized, display original.
"""

from __future__ import annotations
import re
from typing import Optional

__all__ = [
    "normalize_part_number",
    "normalize_for_display",
    "are_same_part",
    "extract_oem_from_title",
]


def normalize_part_number(pn: str) -> str:
    """Canonical part number normalizer.
    
    Removes all spaces, dashes, dots, and converts to uppercase.
    This is the ONLY normalization function to use for storage/comparison.
    
    Examples:
        "5473069" → "5473069"
        "547-3069" → "5473069"
        "547 3069" → "5473069"
        "ISX-119-145" → "ISX119145"
        "isx119145" → "ISX119145"
        "MCB-ISX15-001" → "MCBISX15001"
    """
    if not pn:
        return ""
    
    # Strip whitespace, uppercase
    result = pn.strip().upper()
    
    # Remove common separators
    result = re.sub(r'[\s\-\.\,\/\\]+', '', result)
    
    # Remove any other non-alphanumeric except common suffixes
    # Keep letters and numbers only
    result = re.sub(r'[^A-Z0-9]', '', result)
    
    return result


def normalize_for_display(pn: str) -> str:
    """Light normalization for display purposes.
    
    Keeps dashes for readability but standardizes spacing.
    
    Examples:
        "  ISX-119-145  " → "ISX-119-145"
        "ISX 119 145" → "ISX-119-145"
    """
    if not pn:
        return ""
    
    result = pn.strip().upper()
    # Standardize separators to dashes
    result = re.sub(r'[\s\.\/\\]+', '-', result)
    # Remove consecutive dashes
    result = re.sub(r'-+', '-', result)
    # Remove leading/trailing dashes
    result = result.strip('-')
    
    return result


def are_same_part(pn1: str, pn2: str) -> bool:
    """Check if two part numbers refer to the same part.
    
    Uses normalized comparison.
    
    Examples:
        are_same_part("5473069", "547-3069") → True
        are_same_part("ISX119145", "ISX-119-145") → True
        are_same_part("ISX119145", "ISX119146") → False
    """
    return normalize_part_number(pn1) == normalize_part_number(pn2)


def extract_oem_from_title(title: str) -> Optional[str]:
    """Extract OEM part number from a product title.
    
    Common patterns:
        "ISX15 Kit Replaces Cummins 5473069" → "5473069"
        "Inframe Kit (OEM: 5473069)" → "5473069"
        "5473069 Replacement Kit" → "5473069"
    
    Returns None if no OEM number detected.
    """
    if not title:
        return None
    
    title = title.upper()
    
    # Pattern: "Replaces {OEM} {number}"
    match = re.search(r'REPLACES?\s+(?:CUMMINS|CAT|CATERPILLAR|DETROIT)?\s*(\d{7})', title)
    if match:
        return match.group(1)
    
    # Pattern: "OEM: {number}" or "OEM#{number}"
    match = re.search(r'OEM[:\s#]+(\d{7})', title)
    if match:
        return match.group(1)
    
    # Pattern: CAT format "10R-{4digits}"
    match = re.search(r'\b(10R-?\d{4})\b', title)
    if match:
        return normalize_part_number(match.group(1))
    
    # Pattern: Leading 7-digit number (likely OEM)
    match = re.match(r'^(\d{7})\b', title.strip())
    if match:
        return match.group(1)
    
    return None


def split_part_number(pn: str) -> tuple[str, Optional[str], Optional[str]]:
    """Split a part number into base, variant_category, and variant_code.
    
    Returns: (base_part, variant_category, variant_code)
    
    Examples:
        "ISX119145HP" → ("ISX119145", "quality", "HP")
        "ISX119145E" → ("ISX119145", "quality", "E")
        "ISX119145-010" → ("ISX119145", "size", "010")
        "ISX119145" → ("ISX119145", None, None)
    """
    pn = pn.strip().upper()
    
    # Check for HP suffix (High Performance)
    if pn.endswith("HP"):
        return (pn[:-2], "quality", "HP")
    
    # Check for E suffix (Economy) - but not SE, LE, etc.
    if pn.endswith("E") and len(pn) > 3 and not pn[-2].isalpha():
        return (pn[:-1], "quality", "E")
    
    # Check for size suffixes: -010, -020, -030, +010, etc.
    size_match = re.search(r'[-+]?(0[123]0)$', pn)
    if size_match:
        size = size_match.group(1)
        base = pn[:pn.rfind(size)].rstrip('-+')
        return (base, "size", size)
    
    # Check for STD suffix
    if pn.endswith("STD"):
        return (pn[:-3].rstrip('-'), "size", "STD")
    
    return (pn, None, None)


def get_base_part_number(pn: str) -> str:
    """Get the base part number without variant suffixes.
    
    Examples:
        "ISX119145HP" → "ISX119145"
        "ISX119145-010" → "ISX119145"
        "ISX119145" → "ISX119145"
    """
    base, _, _ = split_part_number(pn)
    return base


# ============================================================================
# VARIANT CATEGORIZATION
# ============================================================================

# Variant categories (User 2's recommendation)
VARIANT_CATEGORY_QUALITY = "quality"      # HP, E, STD, Premium
VARIANT_CATEGORY_SIZE = "size"            # 010, 020, 030
VARIANT_CATEGORY_MANUFACTURER = "manufacturer"  # Different maker, same OEM
VARIANT_CATEGORY_PACKAGE = "package"      # Kit vs individual components

# Quality variants
QUALITY_HP = "HP"           # High Performance
QUALITY_E = "E"             # Economy
QUALITY_STD = "STD"         # Standard
QUALITY_PREMIUM = "PREMIUM"

# Size variants
SIZE_STD = "STD"
SIZE_010 = "010"
SIZE_020 = "020"
SIZE_030 = "030"


def categorize_variant(part_number: str, title: str = "") -> tuple[str, Optional[str]]:
    """Categorize a product's variant type.
    
    Returns: (category, code) or (None, None) if standard/base product.
    
    Categories:
    - quality: HP, E, STD, PREMIUM (performance/price tier)
    - size: 010, 020, 030 (dimensional variants)
    - manufacturer: Different aftermarket maker for same OEM
    - package: Kit vs individual
    """
    _, category, code = split_part_number(part_number)
    
    if category and code:
        return (category, code)
    
    # Check title for hints
    title_upper = title.upper()
    
    if "HIGH PERFORMANCE" in title_upper or "HP " in title_upper:
        return (VARIANT_CATEGORY_QUALITY, QUALITY_HP)
    
    if "ECONOMY" in title_upper or "BUDGET" in title_upper:
        return (VARIANT_CATEGORY_QUALITY, QUALITY_E)
    
    if "PREMIUM" in title_upper or "PRO " in title_upper:
        return (VARIANT_CATEGORY_QUALITY, QUALITY_PREMIUM)
    
    if "OVERSIZE" in title_upper or "OVER SIZE" in title_upper:
        return (VARIANT_CATEGORY_SIZE, None)  # Size unknown from title alone
    
    return (None, None)


# ============================================================================
# BRAND DISPLAY (from CROSS_REF_LINK_V2.md Part 3)
# ============================================================================

def generate_brand_display(
    oem_manufacturer: Optional[str] = None,
    aftermarket_manufacturer: Optional[str] = None,
) -> str:
    """Generate the brand to show customers.
    
    Format: "{OEM} {Aftermarket}" or just "{Aftermarket}"
    
    Examples:
        generate_brand_display("Cummins", "PAI") → "Cummins PAI"
        generate_brand_display("CAT", "Interstate McBee") → "CAT Interstate McBee"
        generate_brand_display(None, "PAI") → "PAI"
        generate_brand_display("Cummins", None) → "Cummins"
        generate_brand_display(None, None) → ""
        generate_brand_display("PAI", "PAI") → "PAI"  # No duplication
    """
    oem = (oem_manufacturer or "").strip()
    aftermarket = (aftermarket_manufacturer or "").strip()
    
    if oem and aftermarket:
        # Avoid duplication if same manufacturer
        if oem.upper() == aftermarket.upper():
            return aftermarket
        return f"{oem} {aftermarket}"
    elif aftermarket:
        return aftermarket
    elif oem:
        return oem
    else:
        return ""


def generate_title_with_brand(
    title: str,
    oem_manufacturer: Optional[str] = None,
    aftermarket_manufacturer: Optional[str] = None,
    include_replaces: bool = True,
    oem_part_number: Optional[str] = None,
) -> str:
    """Generate a full product title with brand and cross-reference.
    
    Examples:
        "ISX15 Inframe Kit" + Cummins/PAI + 5473069 →
        "ISX15 Inframe Kit - PAI | Replaces Cummins 5473069"
    """
    parts = [title.strip()]
    
    # Add aftermarket manufacturer
    if aftermarket_manufacturer:
        parts.append(f"- {aftermarket_manufacturer}")
    
    # Add replaces info
    if include_replaces and oem_part_number:
        if oem_manufacturer:
            parts.append(f"| Replaces {oem_manufacturer} {oem_part_number}")
        else:
            parts.append(f"| Replaces OEM {oem_part_number}")
    
    return " ".join(parts)

