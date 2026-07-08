"""QBO sync and duplicate detection logic.

Compares local products against QBO inventory to find:
- New items (safe to add)
- Duplicates (exact SKU match)
- Variants (fuzzy match on part numbers)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .client import get_qbo_client

if TYPE_CHECKING:
    from models import Product


def normalize_sku_for_matching(sku: str) -> str:
    """
    Normalize SKU for comparison.
    - Uppercase
    - Remove JAKS- prefix
    - Remove dashes, spaces, underscores
    """
    if not sku:
        return ""
    normalized = str(sku).upper().strip()
    # Remove common prefixes
    for prefix in ("JAKS-", "JAKS_", "JAKS"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    # Remove separators
    normalized = re.sub(r"[\s\-_./]", "", normalized)
    return normalized


def _extract_part_numbers(product: "Product") -> set[str]:
    """Extract all part number variants from a product for matching."""
    numbers = set()
    
    # Primary SKU
    sku = getattr(product, "sku", "") or ""
    if sku:
        numbers.add(normalize_sku_for_matching(sku))
    
    # Primary part number
    primary_pn = getattr(product, "primary_pn", "") or ""
    if primary_pn:
        numbers.add(normalize_sku_for_matching(primary_pn))
    
    # PAI SKU
    pai_sku = getattr(product, "pai_sku", "") or ""
    if pai_sku:
        numbers.add(normalize_sku_for_matching(pai_sku))
    
    # OEM part number
    oem_pn = getattr(product, "oem_part_number", "") or ""
    if oem_pn:
        numbers.add(normalize_sku_for_matching(oem_pn))
    
    # Cross references
    xrefs = getattr(product, "xrefs", []) or []
    for xref in xrefs:
        if xref:
            numbers.add(normalize_sku_for_matching(str(xref)))
    
    # Remove empty
    numbers.discard("")
    return numbers


def build_qbo_duplicate_report(
    local_products: dict[str, "Product"],
    *,
    progress_callback: callable = None,
) -> dict:
    """
    Compare local products against QBO inventory.
    
    Args:
        local_products: Dict of SKU -> Product objects
        progress_callback: Optional callback(pct, message)
    
    Returns:
        {
            'summary': {'new': int, 'duplicates': int, 'variants': int, 'qbo_only': int},
            'new': [{'sku': str, 'title': str, 'cost': float}],
            'duplicates': [{'sku': str, 'title': str, 'qbo_id': str, 'qbo_name': str, 'qbo_sku': str}],
            'variants': [{'sku': str, 'title': str, 'qbo_id': str, 'qbo_name': str, 'matched_by': str, 'confidence': float}],
            'qbo_only': [{'qbo_id': str, 'qbo_name': str, 'qbo_sku': str}],
            'is_mock': bool,
        }
    """
    result = {
        'summary': {'new': 0, 'duplicates': 0, 'variants': 0, 'qbo_only': 0},
        'new': [],
        'duplicates': [],
        'variants': [],
        'qbo_only': [],
        'is_mock': False,
    }
    
    if not local_products:
        return result
    
    # Get QBO client
    client = get_qbo_client()
    result['is_mock'] = client.is_mock_mode
    
    if progress_callback:
        progress_callback(10, "Connecting to QuickBooks...")
    
    # Connect
    if not client.connect():
        result['error'] = "Failed to connect to QuickBooks"
        return result
    
    if progress_callback:
        progress_callback(20, "Fetching QBO inventory...")
    
    # Fetch all QBO items
    qbo_items = client.fetch_all_items()
    
    if progress_callback:
        progress_callback(40, f"Comparing {len(local_products)} local vs {len(qbo_items)} QBO items...")
    
    # Build QBO index by normalized SKU
    qbo_by_sku: dict[str, dict] = {}
    qbo_all_skus: set[str] = set()
    
    for item in qbo_items:
        sku_norm = normalize_sku_for_matching(item.get("sku", ""))
        if sku_norm:
            qbo_by_sku[sku_norm] = item
            qbo_all_skus.add(sku_norm)
    
    # Track which QBO items are matched
    matched_qbo_skus: set[str] = set()
    
    # Compare each local product
    total = len(local_products)
    for idx, (key, product) in enumerate(local_products.items()):
        if progress_callback and idx % 10 == 0:
            pct = 40 + int((idx / max(total, 1)) * 50)
            progress_callback(pct, f"Checking {idx + 1}/{total}...")
        
        sku = str(getattr(product, "sku", "") or key).strip()
        title = str(getattr(product, "title", "") or "")[:80]
        cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
        
        sku_norm = normalize_sku_for_matching(sku)
        part_numbers = _extract_part_numbers(product)
        
        # Check for exact SKU match
        if sku_norm in qbo_by_sku:
            qbo_item = qbo_by_sku[sku_norm]
            matched_qbo_skus.add(sku_norm)
            result['duplicates'].append({
                'sku': sku,
                'title': title,
                'cost': cost,
                'qbo_id': qbo_item.get("id", ""),
                'qbo_name': qbo_item.get("name", ""),
                'qbo_sku': qbo_item.get("sku", ""),
                'qbo_cost': qbo_item.get("cost", 0),
                'qbo_price': qbo_item.get("price", 0),
                'qbo_qty': qbo_item.get("qty_on_hand", 0),
            })
            continue
        
        # Check for variant match (other part numbers)
        variant_match = None
        matched_by = None
        for pn in part_numbers:
            if pn in qbo_by_sku and pn != sku_norm:
                variant_match = qbo_by_sku[pn]
                matched_by = pn
                matched_qbo_skus.add(pn)
                break
        
        if variant_match:
            result['variants'].append({
                'sku': sku,
                'title': title,
                'cost': cost,
                'qbo_id': variant_match.get("id", ""),
                'qbo_name': variant_match.get("name", ""),
                'qbo_sku': variant_match.get("sku", ""),
                'matched_by': matched_by,
                'confidence': 0.8,  # Partial match
            })
            continue
        
        # No match - it's new
        result['new'].append({
            'sku': sku,
            'title': title,
            'cost': cost,
        })
    
    # Find QBO-only items (in QBO but not local)
    for sku_norm, qbo_item in qbo_by_sku.items():
        if sku_norm not in matched_qbo_skus:
            # Check if it's a JAKS item
            qbo_sku = qbo_item.get("sku", "")
            if qbo_sku.upper().startswith("JAKS"):
                result['qbo_only'].append({
                    'qbo_id': qbo_item.get("id", ""),
                    'qbo_name': qbo_item.get("name", ""),
                    'qbo_sku': qbo_sku,
                    'qbo_qty': qbo_item.get("qty_on_hand", 0),
                })
    
    # Update summary
    result['summary'] = {
        'new': len(result['new']),
        'duplicates': len(result['duplicates']),
        'variants': len(result['variants']),
        'qbo_only': len(result['qbo_only']),
    }
    
    if progress_callback:
        progress_callback(100, "Complete")
    
    return result
