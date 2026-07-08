"""
Part Number Pattern Recognition for Diesel Parts.

Identifies the MANUFACTURER (not reseller) based on part number format.
This is critical because HHP/ATL are resellers - they sell PAI, McBee, AFA parts.

Supply Chain:
    OEM (Cummins, CAT) → Aftermarket Mfrs (PAI, McBee) → Resellers (HHP, ATL) → Us (JAKS)
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ManufacturerMatch:
    """Result of manufacturer identification."""
    manufacturer: str          # "PAI", "Interstate McBee", "OEM Cummins", etc.
    manufacturer_code: str     # "PAI", "MCB", "CUM", "CAT"
    confidence: float          # 0.0 - 1.0
    pattern_name: str          # Which pattern matched
    normalized_pn: str         # Cleaned part number
    original_pn: str           # Original input


# Manufacturer patterns - order matters (more specific first)
MANUFACTURER_PATTERNS = [
    # PAI Industries patterns
    {
        "manufacturer": "PAI Industries",
        "code": "PAI",
        "patterns": [
            # ISX engine family (ISX119145, ISX-119-145, ISX15xxxxx)
            (r"^ISX[\-]?\d{5,7}[A-Z]?$", "pai_isx", 0.95),
            (r"^ISX[\-]?1[0-9][\-]?\d{4,6}$", "pai_isx_variant", 0.90),
            
            # C15/C13 Caterpillar replacement
            (r"^C1[35][\-]?\d{5,7}[A-Z]?$", "pai_cat_c15", 0.95),
            (r"^C1[35]\d{6}[A-Z]{0,2}$", "pai_cat_alt", 0.90),
            
            # N14 engine family
            (r"^N14[\-]?\d{5,7}[A-Z]?$", "pai_n14", 0.95),
            
            # Detroit Diesel series
            (r"^DD[\-]?\d{5,7}$", "pai_detroit", 0.90),
            (r"^S60[\-]?\d{5,7}$", "pai_series60", 0.90),
            
            # 6 digit PAI format (generic)
            (r"^[A-Z]{2,4}\d{6,8}[A-Z]?$", "pai_generic", 0.70),
            
            # 3-letter prefix + 6 digits (common PAI format)
            (r"^[A-Z]{3}\d{6}$", "pai_standard", 0.75),
        ],
    },
    
    # Interstate McBee patterns
    {
        "manufacturer": "Interstate McBee",
        "code": "MCB",
        "patterns": [
            (r"^MCB[\-]?\d{4,8}$", "mcbee_standard", 0.95),
            (r"^IM[\-]?\d{5,8}$", "mcbee_im", 0.90),
            (r"^M[\-]?\d{6,8}$", "mcbee_m_prefix", 0.70),
            # McBee often uses dash-separated: 23538823
            (r"^235\d{5}$", "mcbee_235_series", 0.80),
        ],
    },
    
    # AFA Industries patterns  
    {
        "manufacturer": "AFA Industries",
        "code": "AFA",
        "patterns": [
            (r"^AFA[\-]?\d{4,8}$", "afa_standard", 0.95),
            (r"^AF[\-]?\d{5,8}$", "afa_short", 0.85),
        ],
    },
    
    # IPD patterns
    {
        "manufacturer": "IPD",
        "code": "IPD",
        "patterns": [
            (r"^IPD[\-]?\d{4,8}$", "ipd_standard", 0.95),
            (r"^IP[\-]?\d{5,8}$", "ipd_short", 0.85),
        ],
    },
    
    # Reliance Power Parts
    {
        "manufacturer": "Reliance Power Parts",
        "code": "RPP",
        "patterns": [
            (r"^RPP[\-]?\d{4,8}$", "rpp_standard", 0.95),
            (r"^RP[\-]?\d{5,8}$", "rpp_short", 0.85),
        ],
    },
    
    # OEM Cummins patterns (check AFTER aftermarket)
    {
        "manufacturer": "OEM Cummins",
        "code": "CUM",
        "patterns": [
            # 7-digit numeric (most common Cummins OEM format)
            (r"^\d{7}$", "cummins_7digit", 0.85),
            # 8-digit with leading zeros
            (r"^0\d{7}$", "cummins_8digit_padded", 0.80),
            # Cummins with dashes: 3803703, 3096200
            (r"^3[0-9]{6}$", "cummins_3_series", 0.90),
            (r"^4[0-9]{6}$", "cummins_4_series", 0.85),
            (r"^5[0-9]{6}$", "cummins_5_series", 0.85),
        ],
    },
    
    # OEM Caterpillar patterns
    {
        "manufacturer": "OEM Caterpillar", 
        "code": "CAT",
        "patterns": [
            # CAT format: 1234567 or 123-4567
            (r"^\d{3}[\-]?\d{4}$", "cat_7digit", 0.75),
            # 3E-xxxx format
            (r"^3E[\-]?\d{4}$", "cat_3e_series", 0.90),
            # 10R-xxxx format (reman)
            (r"^10R[\-]?\d{4}$", "cat_10r_reman", 0.95),
            # OR-xxxx format
            (r"^OR[\-]?\d{4,6}$", "cat_or_series", 0.85),
        ],
    },
    
    # OEM Detroit Diesel
    {
        "manufacturer": "OEM Detroit Diesel",
        "code": "DET",
        "patterns": [
            # Detroit format: 23538823
            (r"^23[0-9]{6}$", "detroit_23_series", 0.85),
            (r"^R23[0-9]{6}$", "detroit_reman", 0.90),
        ],
    },
]

# Resellers - these are NOT manufacturers
KNOWN_RESELLERS = {
    "HHP": "Highway & Heavy Parts",
    "ATL": "ATL Diesel", 
    "DPD": "Diesel Parts Direct",
    "TTP": "TTP Hard Parts",
    "JAKS": "JAKS (us)",
}


def normalize_part_number(raw: str) -> str:
    """
    Normalize a part number for comparison.
    
    - Uppercase
    - Remove common prefixes that don't identify manufacturer
    - Strip dashes, spaces
    - Handle common variations
    """
    if not raw:
        return ""
    
    # Uppercase and strip
    pn = raw.upper().strip()
    
    # Remove common non-manufacturer prefixes
    prefixes_to_strip = [
        "JAKS-",
        "HHP-",
        "ATL-",
        "NEW-",
        "REMAN-",
        "REBUILT-",
        "OEM-",
    ]
    
    for prefix in prefixes_to_strip:
        if pn.startswith(prefix):
            pn = pn[len(prefix):]
    
    # Remove dashes and spaces for pattern matching
    pn_clean = re.sub(r"[\-\s\.]", "", pn)
    
    return pn_clean


def identify_manufacturer(part_number: str) -> Optional[ManufacturerMatch]:
    """
    Identify the manufacturer from a part number.
    
    Returns ManufacturerMatch or None if unknown.
    
    Example:
        identify_manufacturer("ISX119-145") → ManufacturerMatch(manufacturer="PAI Industries", ...)
        identify_manufacturer("5579308") → ManufacturerMatch(manufacturer="OEM Cummins", ...)
    """
    if not part_number:
        return None
    
    # Normalize for matching
    pn_clean = normalize_part_number(part_number)
    
    if not pn_clean:
        return None
    
    best_match = None
    best_confidence = 0.0
    
    for mfr_group in MANUFACTURER_PATTERNS:
        manufacturer = mfr_group["manufacturer"]
        code = mfr_group["code"]
        
        for pattern, pattern_name, base_confidence in mfr_group["patterns"]:
            if re.match(pattern, pn_clean, re.IGNORECASE):
                # Calculate final confidence
                # Longer matches are more specific
                specificity_bonus = min(0.1, len(pn_clean) / 100)
                confidence = min(1.0, base_confidence + specificity_bonus)
                
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = ManufacturerMatch(
                        manufacturer=manufacturer,
                        manufacturer_code=code,
                        confidence=confidence,
                        pattern_name=pattern_name,
                        normalized_pn=pn_clean,
                        original_pn=part_number,
                    )
    
    return best_match


def identify_manufacturer_with_context(
    part_number: str,
    title: str = "",
    description: str = "",
) -> Optional[ManufacturerMatch]:
    """
    Identify manufacturer using part number AND contextual clues.
    
    Sometimes the title/description contains manufacturer info:
    - "PAI ISX Inframe Kit"
    - "Interstate McBee Fuel Injector"
    """
    # First try part number alone
    match = identify_manufacturer(part_number)
    
    if match and match.confidence >= 0.85:
        return match  # High confidence from part number
    
    # Check title/description for manufacturer names
    text = f"{title} {description}".upper()
    
    manufacturer_keywords = {
        "PAI": ("PAI Industries", "PAI", 0.90),
        "INTERSTATE MCBEE": ("Interstate McBee", "MCB", 0.95),
        "MCBEE": ("Interstate McBee", "MCB", 0.85),
        "AFA": ("AFA Industries", "AFA", 0.90),
        "IPD": ("IPD", "IPD", 0.90),
        "RELIANCE": ("Reliance Power Parts", "RPP", 0.85),
        "OEM CUMMINS": ("OEM Cummins", "CUM", 0.95),
        "GENUINE CUMMINS": ("OEM Cummins", "CUM", 0.95),
        "OEM CATERPILLAR": ("OEM Caterpillar", "CAT", 0.95),
        "GENUINE CAT": ("OEM Caterpillar", "CAT", 0.90),
    }
    
    for keyword, (mfr_name, mfr_code, conf) in manufacturer_keywords.items():
        if keyword in text:
            # If we have a weak part number match, boost it
            if match and match.manufacturer_code == mfr_code:
                return ManufacturerMatch(
                    manufacturer=mfr_name,
                    manufacturer_code=mfr_code,
                    confidence=min(1.0, match.confidence + 0.15),
                    pattern_name=f"{match.pattern_name}+context",
                    normalized_pn=normalize_part_number(part_number),
                    original_pn=part_number,
                )
            # Otherwise use context alone if no match
            elif not match:
                return ManufacturerMatch(
                    manufacturer=mfr_name,
                    manufacturer_code=mfr_code,
                    confidence=conf - 0.10,  # Lower without part number
                    pattern_name="context_only",
                    normalized_pn=normalize_part_number(part_number),
                    original_pn=part_number,
                )
    
    return match


def is_same_manufacturer_part(pn1: str, pn2: str) -> tuple[bool, float]:
    """
    Check if two part numbers are from the same manufacturer.
    
    Returns (is_same, confidence).
    
    Example:
        is_same_manufacturer_part("ISX119-145", "ISX119145") → (True, 0.98)
        is_same_manufacturer_part("ISX119-145", "5579308") → (False, 0.0)
    """
    mfr1 = identify_manufacturer(pn1)
    mfr2 = identify_manufacturer(pn2)
    
    if not mfr1 or not mfr2:
        return (False, 0.0)
    
    # Same manufacturer code?
    if mfr1.manufacturer_code != mfr2.manufacturer_code:
        return (False, 0.0)
    
    # Same normalized part number?
    if mfr1.normalized_pn == mfr2.normalized_pn:
        confidence = min(mfr1.confidence, mfr2.confidence)
        return (True, confidence)
    
    # Similar part numbers (allow for minor variations)
    # E.g., ISX119145 vs ISX119-145HP
    base1 = re.sub(r"[A-Z]+$", "", mfr1.normalized_pn)  # Strip trailing letters
    base2 = re.sub(r"[A-Z]+$", "", mfr2.normalized_pn)
    
    if base1 == base2:
        confidence = min(mfr1.confidence, mfr2.confidence) * 0.85
        return (True, confidence)
    
    return (False, 0.0)


def extract_oem_from_aftermarket(
    aftermarket_pn: str,
    title: str = "",
) -> Optional[str]:
    """
    Try to extract OEM part number from aftermarket part or title.
    
    Many aftermarket parts include OEM number in title:
    - "ISX119-145 (Replaces Cummins 5579308)"
    - "PAI ISX Kit - OEM 5579308"
    """
    # Check title for OEM references
    text = f"{aftermarket_pn} {title}"
    
    # Pattern: "replaces XXXXXXX" or "OEM XXXXXXX"
    oem_patterns = [
        r"replaces?\s+(\d{7})",
        r"OEM[\s\#:]+(\d{7})",
        r"cummins[\s\#:]+(\d{7})",
        r"\((\d{7})\)",  # Number in parentheses
    ]
    
    for pattern in oem_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return None


def get_all_manufacturers() -> list[dict]:
    """Get list of all known manufacturers with their codes."""
    return [
        {
            "code": mfr["code"],
            "name": mfr["manufacturer"],
            "is_oem": mfr["manufacturer"].startswith("OEM"),
        }
        for mfr in MANUFACTURER_PATTERNS
    ]


def get_all_resellers() -> list[dict]:
    """Get list of known resellers (not manufacturers)."""
    return [
        {"code": code, "name": name}
        for code, name in KNOWN_RESELLERS.items()
    ]


# Quick test function
def _test_patterns():
    """Test the pattern recognition."""
    test_cases = [
        ("ISX119-145", "PAI Industries"),
        ("ISX119145", "PAI Industries"),
        ("C15101010", "PAI Industries"),
        ("N14221017", "PAI Industries"),
        ("5579308", "OEM Cummins"),
        ("3803703", "OEM Cummins"),
        ("MCB-12345", "Interstate McBee"),
        ("AFA-54321", "AFA Industries"),
        ("10R-1234", "OEM Caterpillar"),
        ("3E-1234", "OEM Caterpillar"),
        ("JAKS-ISX119145", "PAI Industries"),  # Should strip prefix
    ]
    
    print("Part Number Pattern Recognition Tests:")
    print("-" * 60)
    
    for pn, expected in test_cases:
        result = identify_manufacturer(pn)
        status = "✓" if result and result.manufacturer == expected else "✗"
        actual = result.manufacturer if result else "Unknown"
        conf = f"{result.confidence:.0%}" if result else "N/A"
        print(f"{status} {pn:20} → {actual:20} ({conf}) [expected: {expected}]")


if __name__ == "__main__":
    _test_patterns()
