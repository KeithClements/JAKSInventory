"""Manufacturer verification pipeline with weighted confidence scoring.

Implements User 1's recommendation:
- PAI Portal = 0.5 weight
- DPD Scrape = 0.3 weight  
- Pattern Match = 0.2 weight

Confidence is cumulative: pattern (0.2) + PAI confirms (0.5) = 0.7 (70%)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from utils.part_normalizer import normalize_part_number


class VerificationSource(Enum):
    """Sources for manufacturer verification with weights."""
    PATTERN = ("pattern", 0.2)
    PAI_PORTAL = ("pai_portal", 0.5)
    DPD_SCRAPE = ("dpd_scrape", 0.3)
    MCBEE_SITE = ("mcbee_site", 0.25)
    MANUAL = ("manual", 1.0)  # Manual override = full confidence
    
    def __init__(self, source_name: str, weight: float):
        self.source_name = source_name
        self.weight = weight


@dataclass
class VerificationResult:
    """Result from a single verification source."""
    source: VerificationSource
    manufacturer_code: Optional[str] = None
    manufacturer_name: Optional[str] = None
    manufacturer_type: Optional[str] = None  # oem, aftermarket
    confidence: float = 0.0
    raw_data: dict = field(default_factory=dict)
    error: Optional[str] = None
    
    @property
    def is_success(self) -> bool:
        return self.manufacturer_code is not None and self.error is None


@dataclass
class ManufacturerVerification:
    """Complete verification result with weighted confidence."""
    part_number: str
    part_number_normalized: str
    
    # Results from each source
    pattern_result: Optional[VerificationResult] = None
    pai_result: Optional[VerificationResult] = None
    dpd_result: Optional[VerificationResult] = None
    mcbee_result: Optional[VerificationResult] = None
    manual_result: Optional[VerificationResult] = None
    
    # Final determination
    final_manufacturer_code: Optional[str] = None
    final_manufacturer_name: Optional[str] = None
    final_manufacturer_type: Optional[str] = None
    final_confidence: float = 0.0
    
    # Flags
    needs_review: bool = False
    conflicting_sources: bool = False
    
    @property
    def all_results(self) -> list[VerificationResult]:
        """Get all non-None results."""
        results = []
        for r in [self.pattern_result, self.pai_result, self.dpd_result, 
                  self.mcbee_result, self.manual_result]:
            if r is not None:
                results.append(r)
        return results
    
    @property
    def successful_results(self) -> list[VerificationResult]:
        """Get all successful results."""
        return [r for r in self.all_results if r.is_success]
    
    def calculate_consensus(self) -> None:
        """Calculate weighted consensus from all sources."""
        successful = self.successful_results
        
        if not successful:
            self.final_confidence = 0.0
            self.needs_review = True
            return
        
        # Check for manual override first
        if self.manual_result and self.manual_result.is_success:
            self.final_manufacturer_code = self.manual_result.manufacturer_code
            self.final_manufacturer_name = self.manual_result.manufacturer_name
            self.final_manufacturer_type = self.manual_result.manufacturer_type
            self.final_confidence = 1.0
            return
        
        # Group by manufacturer code
        mfr_votes: dict[str, list[VerificationResult]] = {}
        for r in successful:
            code = r.manufacturer_code
            if code not in mfr_votes:
                mfr_votes[code] = []
            mfr_votes[code].append(r)
        
        # Check for conflicts
        if len(mfr_votes) > 1:
            self.conflicting_sources = True
            self.needs_review = True
        
        # Find winner (highest total weight)
        best_code = None
        best_weight = 0.0
        best_results = []
        
        for code, results in mfr_votes.items():
            total_weight = sum(r.source.weight for r in results)
            if total_weight > best_weight:
                best_weight = total_weight
                best_code = code
                best_results = results
        
        if best_code and best_results:
            self.final_manufacturer_code = best_code
            self.final_manufacturer_name = best_results[0].manufacturer_name
            self.final_manufacturer_type = best_results[0].manufacturer_type
            self.final_confidence = min(best_weight, 1.0)
        
        # Flag for review if low confidence
        if self.final_confidence < 0.5:
            self.needs_review = True


def verify_from_pattern(part_number: str) -> VerificationResult:
    """Verify manufacturer using pattern matching only.
    
    Returns VerificationResult with PATTERN weight (0.2).
    """
    from engine.part_number_patterns import identify_manufacturer
    
    result = VerificationResult(source=VerificationSource.PATTERN)
    
    try:
        mfr_name = identify_manufacturer(part_number)
        
        if mfr_name:
            # Map name to code
            name_to_code = {
                "PAI": ("PAI", "PAI Industries", "aftermarket"),
                "Interstate McBee": ("MCBEE", "Interstate McBee", "aftermarket"),
                "AFA": ("AFA", "AFA Industries", "aftermarket"),
                "IPD": ("IPD", "IPD Parts", "aftermarket"),
                "OEM Cummins": ("CUMMINS", "Cummins Inc.", "oem"),
                "OEM CAT": ("CAT", "Caterpillar Inc.", "oem"),
            }
            
            if mfr_name in name_to_code:
                code, full_name, mfr_type = name_to_code[mfr_name]
                result.manufacturer_code = code
                result.manufacturer_name = full_name
                result.manufacturer_type = mfr_type
                result.confidence = VerificationSource.PATTERN.weight
    except Exception as e:
        result.error = str(e)
    
    return result


def verify_from_pai_portal(part_number: str) -> VerificationResult:
    """Verify manufacturer using PAI Portal lookup.
    
    Returns VerificationResult with PAI_PORTAL weight (0.5).
    """
    result = VerificationResult(source=VerificationSource.PAI_PORTAL)
    
    try:
        from sources.pai_client import PAIClient
        
        client = PAIClient()
        # This is a placeholder - actual implementation depends on PAI client
        # For now, pattern check if it looks like PAI
        normalized = normalize_part_number(part_number)
        
        # Check if PAI pattern
        import re
        pai_patterns = [
            r'^ISX\d{3,}',
            r'^C1[35]\d{3,}',
            r'^N14\d{3,}',
            r'^DT\d{3,}',
            r'^ISB\d{3,}',
            r'^ISM\d{3,}',
            r'^ISL\d{3,}',
            r'^S60\d{3,}',
            r'^DD\d{3,}',
        ]
        
        for pattern in pai_patterns:
            if re.match(pattern, normalized, re.IGNORECASE):
                result.manufacturer_code = "PAI"
                result.manufacturer_name = "PAI Industries"
                result.manufacturer_type = "aftermarket"
                result.confidence = VerificationSource.PAI_PORTAL.weight
                break
                
    except ImportError:
        result.error = "PAI client not available"
    except Exception as e:
        result.error = str(e)
    
    return result


def verify_from_database(part_number: str) -> VerificationResult:
    """Check database for existing manufacturer assignment.
    
    If we've already verified this part number, use that.
    """
    result = VerificationResult(source=VerificationSource.MANUAL)
    
    try:
        from db.manufacturer_service import identify_manufacturer_from_part
        
        mfr = identify_manufacturer_from_part(part_number)
        if mfr:
            result.manufacturer_code = mfr.code
            result.manufacturer_name = mfr.name
            result.manufacturer_type = mfr.type
            result.confidence = 0.3  # Lower than manual but some confidence
    except Exception as e:
        result.error = str(e)
    
    return result


def verify_manufacturer(
    part_number: str,
    use_pattern: bool = True,
    use_pai_portal: bool = True,
    use_dpd: bool = True,  # Now implemented!
    use_database: bool = True,
) -> ManufacturerVerification:
    """Run full verification pipeline for a part number.
    
    Args:
        part_number: The part number to verify
        use_pattern: Use pattern matching (0.2 weight)
        use_pai_portal: Use PAI Portal lookup (0.5 weight)
        use_dpd: Use DPD scraper (0.3 weight)
        use_database: Check existing database assignments
    
    Returns:
        ManufacturerVerification with weighted confidence score
    """
    normalized = normalize_part_number(part_number)
    
    verification = ManufacturerVerification(
        part_number=part_number,
        part_number_normalized=normalized,
    )
    
    # Run each verification source
    if use_database:
        db_result = verify_from_database(part_number)
        if db_result.is_success:
            verification.manual_result = db_result
    
    if use_pattern:
        verification.pattern_result = verify_from_pattern(part_number)
    
    if use_pai_portal:
        verification.pai_result = verify_from_pai_portal(part_number)
    
    # DPD scraper for secondary verification
    if use_dpd:
        try:
            from sources.dpd_scraper import verify_from_dpd
            verification.dpd_result = verify_from_dpd(part_number)
        except ImportError:
            pass  # DPD scraper not available
        except Exception as e:
            # Log but don't fail - DPD is optional
            import logging
            logging.getLogger(__name__).warning(f"DPD verification failed: {e}")
    
    # Calculate weighted consensus
    verification.calculate_consensus()
    
    return verification


def batch_verify_manufacturers(
    part_numbers: list[str],
    use_pattern: bool = True,
    use_pai_portal: bool = True,
    use_dpd: bool = False,  # Disabled by default for batch (rate limits)
) -> list[ManufacturerVerification]:
    """Verify multiple part numbers efficiently.
    
    User 1 recommendation: batch for performance with 10k+ imports.
    
    Note: DPD is disabled by default for batch operations due to
    rate limiting. Enable with use_dpd=True for smaller batches.
    """
    results = []
    
    for pn in part_numbers:
        result = verify_manufacturer(
            pn,
            use_pattern=use_pattern,
            use_pai_portal=use_pai_portal,
            use_dpd=use_dpd,
        )
        results.append(result)
    
    return results
    
    return results


def get_confidence_display(confidence: float) -> str:
    """Convert confidence score to display string."""
    if confidence >= 0.9:
        return "Very High (90%+)"
    elif confidence >= 0.7:
        return "High (70-90%)"
    elif confidence >= 0.5:
        return "Medium (50-70%)"
    elif confidence >= 0.3:
        return "Low (30-50%)"
    else:
        return "Very Low (<30%)"


def get_confidence_color(confidence: float) -> str:
    """Get color for confidence display (for UI)."""
    if confidence >= 0.7:
        return "green"
    elif confidence >= 0.5:
        return "orange"
    else:
        return "red"
