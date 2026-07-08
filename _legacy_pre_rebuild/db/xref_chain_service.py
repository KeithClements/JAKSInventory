"""Cross-reference chain service.

Manages verified cross-references between OEM and aftermarket part numbers.
Each chain links an OEM part to its aftermarket equivalent with confidence level.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from db.database import get_connection
from db.product_grouping import normalize_part_number


@dataclass
class XRefChain:
    """A verified cross-reference between OEM and aftermarket part."""
    id: int
    oem_part_number: str
    oem_manufacturer: Optional[str] = None
    aftermarket_part_number: str = ""
    aftermarket_manufacturer: Optional[str] = None
    source: Optional[str] = None              # pai_portal, dpd_scrape, manual, vendor_import
    confidence: str = "medium"                # high, medium, low
    verified_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# Source constants
SOURCE_PAI_PORTAL = "pai_portal"
SOURCE_DPD_SCRAPE = "dpd_scrape"
SOURCE_MANUAL = "manual"
SOURCE_VENDOR_IMPORT = "vendor_import"
SOURCE_HHP = "hhp"
SOURCE_ATL = "atl"

# Confidence levels
CONFIDENCE_HIGH = "high"      # Verified by multiple sources
CONFIDENCE_MEDIUM = "medium"  # Single reliable source
CONFIDENCE_LOW = "low"        # Unverified or uncertain


def add_xref_chain(
    oem_part_number: str,
    aftermarket_part_number: str,
    oem_manufacturer: Optional[str] = None,
    aftermarket_manufacturer: Optional[str] = None,
    source: str = SOURCE_MANUAL,
    confidence: str = CONFIDENCE_MEDIUM,
) -> XRefChain:
    """Add a new cross-reference chain.
    
    If the xref already exists, updates the confidence if higher.
    """
    conn = get_connection()
    try:
        oem = oem_part_number.strip().upper()
        aftermarket = aftermarket_part_number.strip().upper()
        
        # Check if already exists
        existing = conn.execute(
            """SELECT * FROM xref_chains 
               WHERE oem_part_number = ? AND aftermarket_part_number = ?""",
            (oem, aftermarket)
        ).fetchone()
        
        if existing:
            # Update confidence if new source is more reliable
            existing_confidence = existing.get("confidence", "low")
            if _confidence_rank(confidence) > _confidence_rank(existing_confidence):
                conn.execute(
                    """UPDATE xref_chains 
                       SET confidence = ?, source = ?, verified_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (confidence, source, existing["id"])
                )
                conn.commit()
            return _row_to_xref(existing)
        
        # Insert new
        conn.execute(
            """INSERT INTO xref_chains 
               (oem_part_number, oem_manufacturer, aftermarket_part_number, 
                aftermarket_manufacturer, source, confidence)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (oem, oem_manufacturer, aftermarket, aftermarket_manufacturer, source, confidence)
        )
        conn.commit()
        
        return get_xref_by_parts(oem, aftermarket)
    finally:
        conn.close()


def get_xref_by_parts(
    oem_part_number: str,
    aftermarket_part_number: str,
) -> Optional[XRefChain]:
    """Get a specific xref chain by both part numbers."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM xref_chains 
               WHERE oem_part_number = ? AND aftermarket_part_number = ?""",
            (oem_part_number.upper(), aftermarket_part_number.upper())
        ).fetchone()
        return _row_to_xref(row) if row else None
    finally:
        conn.close()


def find_aftermarket_for_oem(oem_part_number: str) -> list[XRefChain]:
    """Find all aftermarket equivalents for an OEM part number."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM xref_chains 
               WHERE oem_part_number = ?
               ORDER BY 
                 CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 aftermarket_manufacturer""",
            (oem_part_number.strip().upper(),)
        ).fetchall()
        return [_row_to_xref(row) for row in rows]
    finally:
        conn.close()


def find_oem_for_aftermarket(aftermarket_part_number: str) -> list[XRefChain]:
    """Find the OEM part number(s) for an aftermarket part."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM xref_chains 
               WHERE aftermarket_part_number = ?
               ORDER BY confidence DESC""",
            (aftermarket_part_number.strip().upper(),)
        ).fetchall()
        return [_row_to_xref(row) for row in rows]
    finally:
        conn.close()


def search_xrefs(query: str) -> list[XRefChain]:
    """Search xref chains by any part number (OEM or aftermarket)."""
    conn = get_connection()
    try:
        query = query.strip().upper()
        rows = conn.execute(
            """SELECT * FROM xref_chains 
               WHERE oem_part_number LIKE ? OR aftermarket_part_number LIKE ?
               ORDER BY 
                 CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 oem_part_number""",
            (f"%{query}%", f"%{query}%")
        ).fetchall()
        return [_row_to_xref(row) for row in rows]
    finally:
        conn.close()


def verify_xref(
    xref_id: int,
    new_confidence: str = CONFIDENCE_HIGH,
    verified_source: Optional[str] = None,
) -> bool:
    """Mark an xref as verified (typically after confirming with another source)."""
    conn = get_connection()
    try:
        update_parts = ["verified_at = CURRENT_TIMESTAMP", f"confidence = ?"]
        params = [new_confidence]
        
        if verified_source:
            update_parts.append("source = ?")
            params.append(verified_source)
        
        params.append(xref_id)
        
        conn.execute(
            f"UPDATE xref_chains SET {', '.join(update_parts)} WHERE id = ?",
            tuple(params)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_xref(xref_id: int) -> bool:
    """Delete an xref chain."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM xref_chains WHERE id = ?", (xref_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def get_xrefs_by_source(source: str) -> list[XRefChain]:
    """Get all xrefs from a specific source."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM xref_chains WHERE source = ? ORDER BY oem_part_number",
            (source,)
        ).fetchall()
        return [_row_to_xref(row) for row in rows]
    finally:
        conn.close()


def get_unverified_xrefs() -> list[XRefChain]:
    """Get xrefs that haven't been verified."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM xref_chains 
               WHERE verified_at IS NULL AND confidence != 'high'
               ORDER BY created_at DESC"""
        ).fetchall()
        return [_row_to_xref(row) for row in rows]
    finally:
        conn.close()


def bulk_add_xrefs(xrefs: list[dict]) -> int:
    """Add multiple xref chains at once.
    
    Each dict should have: oem_part_number, aftermarket_part_number, and optionally
    oem_manufacturer, aftermarket_manufacturer, source, confidence.
    
    Returns number of xrefs added (skips duplicates).
    """
    conn = get_connection()
    added = 0
    try:
        for xref in xrefs:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO xref_chains 
                       (oem_part_number, oem_manufacturer, aftermarket_part_number, 
                        aftermarket_manufacturer, source, confidence)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        xref["oem_part_number"].upper(),
                        xref.get("oem_manufacturer"),
                        xref["aftermarket_part_number"].upper(),
                        xref.get("aftermarket_manufacturer"),
                        xref.get("source", SOURCE_MANUAL),
                        xref.get("confidence", CONFIDENCE_MEDIUM),
                    )
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    added += 1
            except Exception:
                continue
        
        conn.commit()
        return added
    finally:
        conn.close()


def _row_to_xref(row: dict) -> XRefChain:
    """Convert database row to XRefChain object."""
    return XRefChain(
        id=row["id"],
        oem_part_number=row["oem_part_number"],
        oem_manufacturer=row.get("oem_manufacturer"),
        aftermarket_part_number=row["aftermarket_part_number"],
        aftermarket_manufacturer=row.get("aftermarket_manufacturer"),
        source=row.get("source"),
        confidence=row.get("confidence", "medium"),
        verified_at=row.get("verified_at"),
        created_at=row.get("created_at"),
    )


def _confidence_rank(confidence: str) -> int:
    """Get numeric rank for confidence level (higher = more confident)."""
    ranks = {
        "high": 3,
        "medium": 2,
        "low": 1,
    }
    return ranks.get(confidence.lower(), 0)


# ============================================================================
# PHASE 2: Enhanced Search Functions
# ============================================================================

@dataclass
class AftermarketOption:
    """An aftermarket alternative with all relevant info."""
    aftermarket_part_number: str
    aftermarket_manufacturer: Optional[str]
    confidence: str
    source: Optional[str]
    price_hint: Optional[float] = None  # If we have pricing info
    availability: Optional[str] = None  # in_stock, out_of_stock, unknown


@dataclass
class OEMSearchResult:
    """Result of searching by OEM part number."""
    oem_part_number: str
    oem_manufacturer: Optional[str]
    aftermarket_options: list[AftermarketOption]
    total_options: int
    has_verified: bool  # At least one high-confidence option


def search_aftermarket_by_oem(
    oem_part_number: str,
    manufacturer_filter: Optional[str] = None,
    min_confidence: str = "low",
) -> OEMSearchResult:
    """
    Search for all aftermarket options for a given OEM part number.
    
    This is the primary customer-facing search: "I have Cummins part 4025271,
    what aftermarket options are available?"
    
    Args:
        oem_part_number: The OEM part number to search for
        manufacturer_filter: Optional - only return results from this aftermarket brand
        min_confidence: Minimum confidence level to include (low, medium, high)
    
    Returns:
        OEMSearchResult with all matching aftermarket alternatives
    """
    conn = get_connection()
    try:
        oem = oem_part_number.strip().upper()
        min_rank = _confidence_rank(min_confidence)
        
        # Build query with optional manufacturer filter
        sql = """SELECT * FROM xref_chains 
                 WHERE oem_part_number = ?"""
        params = [oem]
        
        if manufacturer_filter:
            sql += " AND UPPER(aftermarket_manufacturer) = ?"
            params.append(manufacturer_filter.upper())
        
        sql += """ ORDER BY 
                   CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                   aftermarket_manufacturer"""
        
        rows = conn.execute(sql, tuple(params)).fetchall()
        
        # Filter by minimum confidence and build options
        options = []
        oem_mfr = None
        has_verified = False
        
        for row in rows:
            if _confidence_rank(row["confidence"]) >= min_rank:
                options.append(AftermarketOption(
                    aftermarket_part_number=row["aftermarket_part_number"],
                    aftermarket_manufacturer=row.get("aftermarket_manufacturer"),
                    confidence=row.get("confidence", "medium"),
                    source=row.get("source"),
                ))
                if row.get("confidence") == "high":
                    has_verified = True
                if not oem_mfr and row.get("oem_manufacturer"):
                    oem_mfr = row["oem_manufacturer"]
        
        return OEMSearchResult(
            oem_part_number=oem,
            oem_manufacturer=oem_mfr,
            aftermarket_options=options,
            total_options=len(options),
            has_verified=has_verified,
        )
    finally:
        conn.close()


def get_all_aftermarket_for_oem_batch(
    oem_part_numbers: list[str],
) -> dict[str, OEMSearchResult]:
    """
    Batch search for aftermarket options for multiple OEM part numbers.
    
    Useful for cart/wishlist views where customer has multiple OEM parts
    and wants to see all aftermarket options at once.
    
    Args:
        oem_part_numbers: List of OEM part numbers to search
    
    Returns:
        Dict mapping OEM part number to its OEMSearchResult
    """
    results = {}
    for oem_pn in oem_part_numbers:
        results[oem_pn.strip().upper()] = search_aftermarket_by_oem(oem_pn)
    return results


def find_cheapest_aftermarket(
    oem_part_number: str,
    min_confidence: str = "medium",
) -> Optional[AftermarketOption]:
    """
    Find the cheapest aftermarket option for an OEM part.

    Looks up each candidate aftermarket part number in the local
    `part_numbers` / `products` tables to populate `price_hint`, then
    sorts so that priced options come first (cheapest first) and
    unpriced options fall back to confidence ranking.

    Args:
        oem_part_number: The OEM part number
        min_confidence: Minimum confidence level

    Returns:
        The cheapest AftermarketOption, or None if none found
    """
    result = search_aftermarket_by_oem(oem_part_number, min_confidence=min_confidence)
    if not result.aftermarket_options:
        return None

    # Populate price_hint from the local products table when we have a
    # matching part number. selling_price > 0 is required so unpriced
    # rows don't masquerade as "$0.00".
    conn = get_connection()
    for opt in result.aftermarket_options:
        normalized = normalize_part_number(opt.aftermarket_part_number)
        if not normalized:
            continue
        row = conn.execute(
            """SELECT p.selling_price
                 FROM part_numbers pn
                 JOIN products p ON p.id = pn.product_id
                WHERE pn.number_normalized = ?
                  AND p.selling_price > 0
                ORDER BY p.selling_price ASC
                LIMIT 1""",
            (normalized,),
        ).fetchone()
        if row and row[0]:
            opt.price_hint = float(row[0])

    # Sort: priced options ascending by price, then unpriced by
    # descending confidence (high → low).
    def _sort_key(opt: AftermarketOption) -> tuple:
        has_price = opt.price_hint is not None
        return (
            0 if has_price else 1,
            opt.price_hint if has_price else 0.0,
            -_confidence_rank(opt.confidence),
        )

    return sorted(result.aftermarket_options, key=_sort_key)[0]


def get_xref_statistics() -> dict:
    """
    Get statistics about the xref database.
    
    Returns dict with:
        - total_xrefs: Total number of cross-references
        - by_source: Count by source (pai_portal, dpd_scrape, etc.)
        - by_confidence: Count by confidence level
        - unique_oem_parts: Number of unique OEM part numbers
        - unique_aftermarket_parts: Number of unique aftermarket part numbers
        - verified_count: Number of verified (high confidence) xrefs
    """
    conn = get_connection()
    try:
        stats = {}
        
        # Total count
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM xref_chains"
        ).fetchone()
        stats["total_xrefs"] = row["cnt"] if row else 0
        
        # By source
        rows = conn.execute(
            """SELECT source, COUNT(*) as count 
               FROM xref_chains 
               GROUP BY source"""
        ).fetchall()
        stats["by_source"] = {row["source"] or "unknown": row["count"] for row in rows}
        
        # By confidence
        rows = conn.execute(
            """SELECT confidence, COUNT(*) as count 
               FROM xref_chains 
               GROUP BY confidence"""
        ).fetchall()
        stats["by_confidence"] = {row["confidence"]: row["count"] for row in rows}
        
        # Unique counts
        row = conn.execute(
            "SELECT COUNT(DISTINCT oem_part_number) as cnt FROM xref_chains"
        ).fetchone()
        stats["unique_oem_parts"] = row["cnt"] if row else 0
        
        row = conn.execute(
            "SELECT COUNT(DISTINCT aftermarket_part_number) as cnt FROM xref_chains"
        ).fetchone()
        stats["unique_aftermarket_parts"] = row["cnt"] if row else 0
        
        # Verified count
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM xref_chains WHERE confidence = 'high'"
        ).fetchone()
        stats["verified_count"] = row["cnt"] if row else 0
        
        return stats
    finally:
        conn.close()


def find_related_products(
    part_number: str,
    search_both_directions: bool = True,
) -> list[XRefChain]:
    """
    Find all related products for a given part number.
    
    Searches both OEM→Aftermarket and Aftermarket→OEM if search_both_directions=True.
    
    This is useful for "Related Products" or "You might also like" sections.
    
    Args:
        part_number: Any part number (OEM or aftermarket)
        search_both_directions: If True, search as both OEM and aftermarket
    
    Returns:
        List of related XRefChain objects
    """
    conn = get_connection()
    try:
        pn = part_number.strip().upper()
        results = []
        seen_ids = set()
        
        # Search as OEM part
        for row in conn.execute(
            "SELECT * FROM xref_chains WHERE oem_part_number = ?", (pn,)
        ).fetchall():
            if row["id"] not in seen_ids:
                results.append(_row_to_xref(row))
                seen_ids.add(row["id"])
        
        if search_both_directions:
            # Also search as aftermarket part
            for row in conn.execute(
                "SELECT * FROM xref_chains WHERE aftermarket_part_number = ?", (pn,)
            ).fetchall():
                if row["id"] not in seen_ids:
                    results.append(_row_to_xref(row))
                    seen_ids.add(row["id"])
        
        # Sort by confidence
        results.sort(key=lambda x: _confidence_rank(x.confidence), reverse=True)
        return results
    finally:
        conn.close()
