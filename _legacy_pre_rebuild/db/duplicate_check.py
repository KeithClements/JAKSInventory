"""
Duplicate check integration for JAKS Scraper.

Provides a simple interface to check for duplicates before adding products
to the inventory, integrating with the ProductGroupingService.
"""

from pathlib import Path
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


class DuplicateCheckResult:
    """Result of a duplicate check operation."""
    
    def __init__(self):
        self.has_duplicate = False
        self.duplicate_product = None
        self.confidence = 0.0
        self.match_type = None  # "exact", "fuzzy", "ai"
        self.matched_part_number = None
        self.recommendation = "create"  # "link", "merge", "create", "skip"
    
    def __repr__(self):
        if self.has_duplicate:
            return f"DuplicateCheckResult(dup={self.matched_part_number}, conf={self.confidence:.0%}, rec={self.recommendation})"
        return "DuplicateCheckResult(no_duplicate)"


def check_product_for_duplicates(
    product: Any,
    db_path: str | Path,
    threshold: float = 0.85
) -> DuplicateCheckResult:
    """
    Check if a product might be a duplicate of an existing product in the database.
    
    Args:
        product: Product object with part numbers (sku, oem_part_number, pai_sku, etc.)
        db_path: Path to the SQLite database
        threshold: Minimum confidence threshold for fuzzy matching
        
    Returns:
        DuplicateCheckResult with duplicate information
    """
    result = DuplicateCheckResult()
    
    try:
        from db.product_grouping import (
            check_for_duplicate_before_insert,
            should_link_products,
        )
        
        # Extract part numbers from product
        part_numbers = []
        
        # SKU (without JAKS- prefix)
        sku = str(getattr(product, "sku", "") or "").strip()
        if sku:
            # Strip JAKS- prefix for comparison
            if sku.upper().startswith("JAKS-"):
                sku = sku[5:]
            part_numbers.append(("sku", sku))
        
        # OEM part number
        oem = str(getattr(product, "oem_part_number", "") or "").strip()
        if oem:
            part_numbers.append(("oem", oem))
        
        # PAI SKU
        pai_sku = str(getattr(product, "pai_sku", "") or "").strip()
        if pai_sku:
            part_numbers.append(("pai", pai_sku))
        
        # Supplier part number
        supplier_pn = str(getattr(product, "supplier_part_number", "") or "").strip()
        if supplier_pn:
            part_numbers.append(("supplier", supplier_pn))
        
        # Cross-references
        xrefs = getattr(product, "xrefs", []) or []
        for xref in xrefs:
            xref_str = str(xref or "").strip()
            if xref_str:
                part_numbers.append(("xref", xref_str))
        
        if not part_numbers:
            return result
        
        # Check each part number
        for pn_type, pn_value in part_numbers:
            dup_result = check_for_duplicate_before_insert(
                str(db_path),
                pn_value,
                pn_type
            )
            
            if dup_result.get("duplicate_found"):
                result.has_duplicate = True
                result.duplicate_product = dup_result.get("existing_product")
                result.confidence = dup_result.get("confidence", 0.8)
                result.match_type = dup_result.get("match_layer", "exact")
                result.matched_part_number = pn_value
                
                # Determine recommendation
                existing = dup_result.get("existing_product")
                if existing:
                    link_decision = should_link_products(product, existing)
                    result.recommendation = "link" if link_decision else "create"
                
                # Return on first match
                return result
                
    except ImportError:
        logger.warning("Product grouping service not available")
    except Exception as e:
        logger.error(f"Error checking for duplicates: {e}")
    
    return result


def link_product_to_existing(
    new_product: Any,
    existing_product: Any,
    db_path: str | Path,
    vendor: str = "HHP"
) -> bool:
    """
    Link a new product's part numbers to an existing product.
    
    Args:
        new_product: The new product being imported/scraped
        existing_product: The existing product to link to
        db_path: Path to the SQLite database
        vendor: Vendor source for the new product
        
    Returns:
        True if link was successful
    """
    try:
        from db.product_grouping import link_product_from_duplicate
        
        # Get existing product ID
        existing_id = getattr(existing_product, "id", None)
        if existing_id is None:
            existing_id = getattr(existing_product, "product_id", None)
        
        if existing_id is None:
            logger.warning("Cannot link - existing product has no ID")
            return False
        
        # Build duplicate info structure
        duplicate_info = {
            "sku": str(getattr(new_product, "sku", "") or "").strip(),
            "oem_part_number": str(getattr(new_product, "oem_part_number", "") or "").strip(),
            "pai_sku": str(getattr(new_product, "pai_sku", "") or "").strip(),
            "supplier_part_number": str(getattr(new_product, "supplier_part_number", "") or "").strip(),
            "title": str(getattr(new_product, "title", "") or "").strip(),
            "cost": float(getattr(new_product, "cost", 0) or 0),
            "vendor": vendor,
        }
        
        link_product_from_duplicate(str(db_path), existing_id, duplicate_info)
        return True
        
    except Exception as e:
        logger.error(f"Error linking product: {e}")
        return False


def merge_products(
    source_product: Any,
    target_product: Any,
    db_path: str | Path
) -> bool:
    """
    Merge source product into target product (deletes source after merging).
    
    Args:
        source_product: Product to merge from (will be deleted)
        target_product: Product to merge into (will be updated)
        db_path: Path to the SQLite database
        
    Returns:
        True if merge was successful
    """
    try:
        from db.product_grouping import merge_products_into_master
        
        source_id = getattr(source_product, "id", None) or getattr(source_product, "product_id", None)
        target_id = getattr(target_product, "id", None) or getattr(target_product, "product_id", None)
        
        if source_id is None or target_id is None:
            logger.warning("Cannot merge - products need IDs")
            return False
        
        merge_products_into_master(str(db_path), target_id, [source_id])
        return True
        
    except Exception as e:
        logger.error(f"Error merging products: {e}")
        return False
