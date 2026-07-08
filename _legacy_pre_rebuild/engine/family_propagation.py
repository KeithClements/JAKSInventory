"""
Family Propagation Service - Propagate data across product families.

When one product in a family gets enriched (PAI cost, ATL price, etc.),
this service can propagate that data to all other family members.

This enables the workflow:
1. Scan category (Phase 1)
2. Scrape products (Phase 2) - xrefs become available
3. Run family analysis (Phase 1.5) - groups products by shared xrefs
4. Scrape/PAI enrich ONE product per family
5. Propagate that data to all family members
6. Upload all with complete pricing

Usage:
    from engine.family_propagation import FamilyPropagationService
    
    service = FamilyPropagationService(products)
    service.propagate_pai_data(source_sku)  # Copy PAI data to family members
    service.propagate_atl_price(source_sku)  # Copy ATL price to family members
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from models import Product


__all__ = [
    "FamilyPropagationService",
    "get_family_members",
    "find_best_enriched_product",
]


@dataclass
class PropagationResult:
    """Result of a propagation operation."""
    source_sku: str
    family_id: str
    field: str
    value: Any
    targets_updated: list[str]
    targets_skipped: list[str]  # Already had data or no family


def get_family_members(products: dict[str, "Product"], family_id: str) -> dict[str, "Product"]:
    """Get all products that belong to a given family.
    
    Args:
        products: Dict of SKU -> Product
        family_id: The family ID (e.g., "FAM-12345")
        
    Returns:
        Dict of SKU -> Product for all family members
    """
    if not family_id or not products:
        return {}
    
    family_id_normalized = family_id.upper().strip()
    if not family_id_normalized.startswith("FAM-"):
        family_id_normalized = f"FAM-{family_id_normalized}"
    
    members = {}
    for sku, prod in products.items():
        prod_family = str(getattr(prod, "family_id", "") or "").upper().strip()
        if prod_family == family_id_normalized:
            members[sku] = prod
    
    return members


def find_best_enriched_product(products: dict[str, "Product"], family_id: str) -> str | None:
    """Find the product in a family with the most complete enrichment data.
    
    Preference order:
    1. Has PAI cost > 0 AND ATL price
    2. Has PAI cost > 0
    3. Has ATL price
    4. Highest stage
    
    Args:
        products: Dict of SKU -> Product
        family_id: The family ID
        
    Returns:
        SKU of the best enriched product, or None if no family members
    """
    members = get_family_members(products, family_id)
    if not members:
        return None
    
    def _score(sku: str) -> tuple[int, int, float]:
        """Score a product for enrichment completeness."""
        prod = members[sku]
        
        # PAI cost
        pai_cost = float(getattr(prod, "pai_cost", 0) or 0)
        has_pai = pai_cost > 0
        
        # ATL price
        pricing = getattr(prod, "pricing", {}) or {}
        atl_price = float(pricing.get("atl_price") or 0)
        has_atl = atl_price > 0
        
        # Stage
        stage = int(pricing.get("stage") or 1)
        
        # Score: (has_both, has_pai, stage)
        both_score = 2 if (has_pai and has_atl) else 0
        pai_score = 1 if has_pai else 0
        
        return (both_score + pai_score, stage, pai_cost)
    
    # Sort by score descending
    ranked = sorted(members.keys(), key=_score, reverse=True)
    return ranked[0] if ranked else None


class FamilyPropagationService:
    """Service to propagate enrichment data across product families.
    
    Usage:
        service = FamilyPropagationService(products)
        
        # Propagate PAI data from one product to family members
        result = service.propagate_pai_data("JAKS-12345")
        
        # Auto-propagate: find best source and propagate to all families
        results = service.auto_propagate_all_families()
    """
    
    def __init__(self, products: dict[str, "Product"]):
        """Initialize with products dict.
        
        Args:
            products: Dict of SKU -> Product (will be modified in-place)
        """
        self.products = products
    
    def get_all_families(self) -> dict[str, list[str]]:
        """Get all family IDs and their member SKUs.
        
        Returns:
            Dict of family_id -> list of SKUs
        """
        families: dict[str, list[str]] = {}
        
        for sku, prod in (self.products or {}).items():
            family_id = str(getattr(prod, "family_id", "") or "").strip()
            if family_id and family_id.startswith("FAM-"):
                if family_id not in families:
                    families[family_id] = []
                families[family_id].append(sku)
        
        return families
    
    def propagate_pai_data(
        self,
        source_sku: str,
        overwrite: bool = False,
    ) -> PropagationResult | None:
        """Propagate PAI data from source product to all family members.
        
        Copies: pai_cost, pai_sku, pai_status, pai_stock, pai_alternates
        
        Args:
            source_sku: SKU of the product with PAI data to propagate
            overwrite: If True, overwrite existing PAI data. If False, skip products that already have PAI.
            
        Returns:
            PropagationResult or None if source not found/no family
        """
        source = self.products.get(source_sku)
        if not source:
            return None
        
        family_id = str(getattr(source, "family_id", "") or "").strip()
        if not family_id:
            return None
        
        # Get source PAI data
        pai_cost = float(getattr(source, "pai_cost", 0) or 0)
        pai_sku = getattr(source, "pai_sku", None)
        pai_status = str(getattr(source, "pai_status", "") or "")
        pai_stock = getattr(source, "pai_stock", {}) or {}
        pai_alternates = list(getattr(source, "pai_alternates", []) or [])
        
        if pai_cost <= 0 and pai_status not in ("FOUND", "FORCED", "CROSS_REF"):
            return None  # No PAI data to propagate
        
        members = get_family_members(self.products, family_id)
        
        updated = []
        skipped = []
        
        for sku, prod in members.items():
            if sku == source_sku:
                continue  # Don't update source
            
            # Check if target already has PAI data
            target_cost = float(getattr(prod, "pai_cost", 0) or 0)
            target_status = str(getattr(prod, "pai_status", "") or "")
            
            has_existing = target_cost > 0 or target_status in ("FOUND", "FORCED", "CROSS_REF")
            
            if has_existing and not overwrite:
                skipped.append(sku)
                continue
            
            # Propagate PAI data
            try:
                setattr(prod, "pai_cost", pai_cost)
                if pai_sku:
                    setattr(prod, "pai_sku", pai_sku)
                setattr(prod, "pai_status", f"PROPAGATED:{pai_status}")
                setattr(prod, "pai_stock", dict(pai_stock))
                setattr(prod, "pai_alternates", list(pai_alternates))
                updated.append(sku)
            except Exception:
                skipped.append(sku)
        
        return PropagationResult(
            source_sku=source_sku,
            family_id=family_id,
            field="pai_data",
            value={"cost": pai_cost, "sku": pai_sku, "status": pai_status},
            targets_updated=updated,
            targets_skipped=skipped,
        )
    
    def propagate_atl_price(
        self,
        source_sku: str,
        overwrite: bool = False,
    ) -> PropagationResult | None:
        """Propagate ATL price from source product to all family members.
        
        Args:
            source_sku: SKU of the product with ATL price to propagate
            overwrite: If True, overwrite existing ATL price
            
        Returns:
            PropagationResult or None if source not found/no family
        """
        source = self.products.get(source_sku)
        if not source:
            return None
        
        family_id = str(getattr(source, "family_id", "") or "").strip()
        if not family_id:
            return None
        
        # Get source ATL price
        pricing = getattr(source, "pricing", {}) or {}
        atl_price = pricing.get("atl_price")
        atl_xref_match = pricing.get("atl_xref_match")  # If matched via xref
        
        if not atl_price or float(atl_price) <= 0:
            return None
        
        members = get_family_members(self.products, family_id)
        
        updated = []
        skipped = []
        
        for sku, prod in members.items():
            if sku == source_sku:
                continue
            
            # Check if target already has ATL price
            target_pricing = getattr(prod, "pricing", {}) or {}
            target_atl = target_pricing.get("atl_price")
            
            if target_atl and float(target_atl) > 0 and not overwrite:
                skipped.append(sku)
                continue
            
            # Propagate ATL price
            try:
                if not hasattr(prod, "pricing") or not isinstance(prod.pricing, dict):
                    prod.pricing = {}
                prod.pricing["atl_price"] = atl_price
                prod.pricing["atl_propagated_from"] = source_sku
                if atl_xref_match:
                    prod.pricing["atl_xref_match"] = atl_xref_match
                updated.append(sku)
            except Exception:
                skipped.append(sku)
        
        return PropagationResult(
            source_sku=source_sku,
            family_id=family_id,
            field="atl_price",
            value=atl_price,
            targets_updated=updated,
            targets_skipped=skipped,
        )
    
    def propagate_pricing(
        self,
        source_sku: str,
        overwrite: bool = False,
    ) -> PropagationResult | None:
        """Propagate full pricing data from source product to family members.
        
        Copies: price (Shopify price), compare_at_price, markup_pct
        Does NOT copy: stage, atl_price (use propagate_atl_price for that)
        
        Args:
            source_sku: SKU of the product with pricing to propagate
            overwrite: If True, overwrite existing prices
            
        Returns:
            PropagationResult or None
        """
        source = self.products.get(source_sku)
        if not source:
            return None
        
        family_id = str(getattr(source, "family_id", "") or "").strip()
        if not family_id:
            return None
        
        pricing = getattr(source, "pricing", {}) or {}
        price = pricing.get("price")
        compare_at = pricing.get("compare_at_price")
        markup = pricing.get("markup_pct")
        
        if not price:
            return None
        
        members = get_family_members(self.products, family_id)
        
        updated = []
        skipped = []
        
        for sku, prod in members.items():
            if sku == source_sku:
                continue
            
            target_pricing = getattr(prod, "pricing", {}) or {}
            target_price = target_pricing.get("price")
            
            if target_price and not overwrite:
                skipped.append(sku)
                continue
            
            try:
                if not hasattr(prod, "pricing") or not isinstance(prod.pricing, dict):
                    prod.pricing = {}
                prod.pricing["price"] = price
                if compare_at:
                    prod.pricing["compare_at_price"] = compare_at
                if markup:
                    prod.pricing["markup_pct"] = markup
                prod.pricing["price_propagated_from"] = source_sku
                updated.append(sku)
            except Exception:
                skipped.append(sku)
        
        return PropagationResult(
            source_sku=source_sku,
            family_id=family_id,
            field="pricing",
            value={"price": price, "compare_at": compare_at},
            targets_updated=updated,
            targets_skipped=skipped,
        )
    
    def auto_propagate_family(
        self,
        family_id: str,
        overwrite: bool = False,
    ) -> list[PropagationResult]:
        """Auto-propagate all available data within a family.
        
        Finds the best-enriched product and propagates its data to all members.
        
        Args:
            family_id: The family ID to propagate within
            overwrite: If True, overwrite existing data
            
        Returns:
            List of PropagationResults for each field propagated
        """
        best_sku = find_best_enriched_product(self.products, family_id)
        if not best_sku:
            return []
        
        results = []
        
        # Propagate PAI data
        pai_result = self.propagate_pai_data(best_sku, overwrite=overwrite)
        if pai_result and pai_result.targets_updated:
            results.append(pai_result)
        
        # Propagate ATL price
        atl_result = self.propagate_atl_price(best_sku, overwrite=overwrite)
        if atl_result and atl_result.targets_updated:
            results.append(atl_result)
        
        # Propagate pricing if available
        pricing_result = self.propagate_pricing(best_sku, overwrite=overwrite)
        if pricing_result and pricing_result.targets_updated:
            results.append(pricing_result)
        
        return results
    
    def auto_propagate_all_families(
        self,
        overwrite: bool = False,
    ) -> dict[str, list[PropagationResult]]:
        """Auto-propagate data for ALL families in products.
        
        For each family, finds the best-enriched product and propagates to members.
        
        Args:
            overwrite: If True, overwrite existing data
            
        Returns:
            Dict of family_id -> list of PropagationResults
        """
        all_families = self.get_all_families()
        
        results = {}
        for family_id in all_families:
            family_results = self.auto_propagate_family(family_id, overwrite=overwrite)
            if family_results:
                results[family_id] = family_results
        
        return results
    
    def get_family_summary(self, family_id: str) -> dict:
        """Get a summary of enrichment status for a family.
        
        Args:
            family_id: The family ID
            
        Returns:
            Dict with family status info
        """
        members = get_family_members(self.products, family_id)
        if not members:
            return {"family_id": family_id, "exists": False}
        
        pai_count = 0
        atl_count = 0
        price_count = 0
        stages = {}
        
        for sku, prod in members.items():
            # PAI
            if float(getattr(prod, "pai_cost", 0) or 0) > 0:
                pai_count += 1
            
            # Pricing
            pricing = getattr(prod, "pricing", {}) or {}
            if float(pricing.get("atl_price") or 0) > 0:
                atl_count += 1
            if pricing.get("price"):
                price_count += 1
            
            # Stage
            stage = int(pricing.get("stage") or 1)
            stages[stage] = stages.get(stage, 0) + 1
        
        best_sku = find_best_enriched_product(self.products, family_id)
        
        return {
            "family_id": family_id,
            "exists": True,
            "member_count": len(members),
            "members": list(members.keys()),
            "best_enriched_sku": best_sku,
            "pai_enriched_count": pai_count,
            "atl_enriched_count": atl_count,
            "price_set_count": price_count,
            "stage_distribution": stages,
        }
