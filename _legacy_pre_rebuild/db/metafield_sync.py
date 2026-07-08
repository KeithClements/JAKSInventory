"""Enhanced Shopify Metafield Sync for cross-references and product families.

This module extends the basic Shopify sync with:
1. Cross-reference metafields (OEM part numbers, alternates)
2. Product family metafields (variants, recommendations)
3. OEM-based collection management
4. Engine family collections

Metafield Namespace: jaks.*
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any

import requests

logger = logging.getLogger(__name__)

# Shopify API configuration
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "jaks-heavy-duty")
SHOPIFY_API_VERSION = "2024-01"
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", os.getenv("ACCESS_TOKEN", ""))

# Metafield namespace for JAKS custom data
METAFIELD_NAMESPACE = "jaks"


@dataclass
class MetafieldDefinition:
    """Definition for a Shopify metafield."""
    key: str
    type: str  # single_line_text_field, multi_line_text_field, json, number_decimal, etc.
    name: str
    description: str = ""
    validations: list[dict] = field(default_factory=list)


# Cross-reference metafield definitions
XREF_METAFIELDS = [
    MetafieldDefinition(
        key="oem_part_numbers",
        type="list.single_line_text_field",
        name="OEM Part Numbers",
        description="Original Equipment Manufacturer part numbers this replaces",
    ),
    MetafieldDefinition(
        key="pai_part_number",
        type="single_line_text_field",
        name="PAI Part Number",
        description="PAI Industries catalog number",
    ),
    MetafieldDefinition(
        key="alternate_parts",
        type="json",
        name="Alternate Parts",
        description="JSON list of alternate/compatible part numbers with confidence scores",
    ),
    MetafieldDefinition(
        key="cross_reference_source",
        type="single_line_text_field",
        name="Cross Reference Source",
        description="Source of cross-reference data (PAI, DPD, pattern, etc.)",
    ),
    MetafieldDefinition(
        key="cross_reference_confidence",
        type="number_decimal",
        name="Cross Reference Confidence",
        description="Confidence score for cross-reference accuracy (0.0-1.0)",
    ),
]

# Product family metafield definitions
FAMILY_METAFIELDS = [
    MetafieldDefinition(
        key="product_family",
        type="single_line_text_field",
        name="Product Family",
        description="Family grouping for variants (e.g., ISX15 Inframe Kit)",
    ),
    MetafieldDefinition(
        key="variant_type",
        type="single_line_text_field",
        name="Variant Type",
        description="Type of variant: standard, hp, economy, oversize, etc.",
    ),
    MetafieldDefinition(
        key="base_part_number",
        type="single_line_text_field",
        name="Base Part Number",
        description="The standard/base part number for this family",
    ),
    MetafieldDefinition(
        key="family_variants",
        type="json",
        name="Family Variants",
        description="JSON list of all variants in this product family",
    ),
    MetafieldDefinition(
        key="is_base_variant",
        type="boolean",
        name="Is Base Variant",
        description="TRUE if this is the standard/base variant of the family",
    ),
    MetafieldDefinition(
        key="recommended_variant",
        type="boolean",
        name="Recommended Variant",
        description="TRUE if this is the recommended variant to purchase",
    ),
]

# Engine/application metafield definitions
ENGINE_METAFIELDS = [
    MetafieldDefinition(
        key="engine_models",
        type="list.single_line_text_field",
        name="Engine Models",
        description="Compatible engine models (e.g., ISX15, C15, DD15)",
    ),
    MetafieldDefinition(
        key="oem_manufacturers",
        type="list.single_line_text_field",
        name="OEM Manufacturers",
        description="OEM manufacturers (e.g., Caterpillar, Cummins, Detroit)",
    ),
    MetafieldDefinition(
        key="engine_family",
        type="single_line_text_field",
        name="Engine Family",
        description="Engine family for grouping (e.g., ISX, C-Series, DD)",
    ),
]

ALL_METAFIELDS = XREF_METAFIELDS + FAMILY_METAFIELDS + ENGINE_METAFIELDS


def _shopify_graphql(query: str, variables: dict = None) -> dict | None:
    """Execute a GraphQL query against Shopify Admin API."""
    url = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if "errors" in data:
            logger.error(f"GraphQL errors: {data['errors']}")
            return None
        
        return data.get("data")
    except requests.RequestException as e:
        logger.error(f"GraphQL request failed: {e}")
        return None


def _shopify_rest(method: str, endpoint: str, json_data: dict = None) -> requests.Response | None:
    """Make a REST request to Shopify Admin API."""
    base_url = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/{SHOPIFY_API_VERSION}"
    url = f"{base_url}{endpoint}"
    
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    
    try:
        resp = requests.request(method, url, headers=headers, json=json_data, timeout=30)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        logger.error(f"Shopify REST API error: {e}")
        return None


def ensure_metafield_definitions() -> dict:
    """
    Ensure all JAKS metafield definitions exist in Shopify.
    
    Creates missing definitions so metafields can be managed via Admin UI.
    
    Returns:
        dict with: {created: int, existing: int, errors: int}
    """
    stats = {"created": 0, "existing": 0, "errors": 0}
    
    # Use GraphQL to create metafield definitions
    mutation = """
    mutation CreateMetafieldDefinition($definition: MetafieldDefinitionInput!) {
        metafieldDefinitionCreate(definition: $definition) {
            createdDefinition {
                id
                name
                key
            }
            userErrors {
                field
                message
            }
        }
    }
    """
    
    for mf in ALL_METAFIELDS:
        variables = {
            "definition": {
                "name": mf.name,
                "namespace": METAFIELD_NAMESPACE,
                "key": mf.key,
                "type": mf.type,
                "description": mf.description,
                "ownerType": "PRODUCT",
            }
        }
        
        result = _shopify_graphql(mutation, variables)
        
        if result:
            creation = result.get("metafieldDefinitionCreate", {})
            errors = creation.get("userErrors", [])
            
            if errors:
                # Check if it's just a "already exists" error
                if any("already exists" in e.get("message", "").lower() for e in errors):
                    stats["existing"] += 1
                    logger.debug(f"Metafield {mf.key} already exists")
                else:
                    stats["errors"] += 1
                    logger.error(f"Error creating {mf.key}: {errors}")
            elif creation.get("createdDefinition"):
                stats["created"] += 1
                logger.info(f"Created metafield definition: {mf.key}")
        else:
            stats["errors"] += 1
    
    return stats


def update_product_cross_refs(
    shopify_product_id: int,
    oem_part_numbers: list[str] | None = None,
    pai_part_number: str | None = None,
    alternate_parts: list[dict] | None = None,
    confidence: float | None = None,
    source: str | None = None,
) -> bool:
    """
    Update cross-reference metafields for a Shopify product.
    
    Args:
        shopify_product_id: Shopify product GID or numeric ID
        oem_part_numbers: List of OEM part numbers
        pai_part_number: PAI catalog number
        alternate_parts: List of dicts with {part_number, manufacturer, confidence}
        confidence: Overall cross-reference confidence (0.0-1.0)
        source: Source of cross-ref data (PAI, DPD, pattern)
    
    Returns:
        True if successful
    """
    # Convert numeric ID to GID if needed
    if isinstance(shopify_product_id, int) or str(shopify_product_id).isdigit():
        gid = f"gid://shopify/Product/{shopify_product_id}"
    else:
        gid = shopify_product_id
    
    metafields = []
    
    if oem_part_numbers:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "oem_part_numbers",
            "value": json.dumps(oem_part_numbers),
            "type": "list.single_line_text_field",
        })
    
    if pai_part_number:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "pai_part_number",
            "value": pai_part_number,
            "type": "single_line_text_field",
        })
    
    if alternate_parts:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "alternate_parts",
            "value": json.dumps(alternate_parts),
            "type": "json",
        })
    
    if confidence is not None:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "cross_reference_confidence",
            "value": str(confidence),
            "type": "number_decimal",
        })
    
    if source:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "cross_reference_source",
            "value": source,
            "type": "single_line_text_field",
        })
    
    if not metafields:
        return True  # Nothing to update
    
    return _set_product_metafields(gid, metafields)


def update_product_family_metafields(
    shopify_product_id: int,
    family_name: str | None = None,
    variant_type: str | None = None,
    base_part_number: str | None = None,
    family_variants: list[dict] | None = None,
    is_base: bool | None = None,
    is_recommended: bool | None = None,
) -> bool:
    """
    Update product family metafields for a Shopify product.
    
    Args:
        shopify_product_id: Shopify product ID
        family_name: Product family name
        variant_type: Variant type (standard, hp, economy, etc.)
        base_part_number: Base part number for the family
        family_variants: List of variant info dicts
        is_base: Whether this is the base variant
        is_recommended: Whether this is the recommended variant
    
    Returns:
        True if successful
    """
    if isinstance(shopify_product_id, int) or str(shopify_product_id).isdigit():
        gid = f"gid://shopify/Product/{shopify_product_id}"
    else:
        gid = shopify_product_id
    
    metafields = []
    
    if family_name:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "product_family",
            "value": family_name,
            "type": "single_line_text_field",
        })
    
    if variant_type:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "variant_type",
            "value": variant_type,
            "type": "single_line_text_field",
        })
    
    if base_part_number:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "base_part_number",
            "value": base_part_number,
            "type": "single_line_text_field",
        })
    
    if family_variants:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "family_variants",
            "value": json.dumps(family_variants),
            "type": "json",
        })
    
    if is_base is not None:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "is_base_variant",
            "value": "true" if is_base else "false",
            "type": "boolean",
        })
    
    if is_recommended is not None:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "recommended_variant",
            "value": "true" if is_recommended else "false",
            "type": "boolean",
        })
    
    if not metafields:
        return True
    
    return _set_product_metafields(gid, metafields)


def update_product_engine_metafields(
    shopify_product_id: int,
    engine_models: list[str] | None = None,
    oem_manufacturers: list[str] | None = None,
    engine_family: str | None = None,
) -> bool:
    """
    Update engine/application metafields for a Shopify product.
    
    Args:
        shopify_product_id: Shopify product ID
        engine_models: List of compatible engine models
        oem_manufacturers: List of OEM manufacturers
        engine_family: Engine family for grouping
    
    Returns:
        True if successful
    """
    if isinstance(shopify_product_id, int) or str(shopify_product_id).isdigit():
        gid = f"gid://shopify/Product/{shopify_product_id}"
    else:
        gid = shopify_product_id
    
    metafields = []
    
    if engine_models:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "engine_models",
            "value": json.dumps(engine_models),
            "type": "list.single_line_text_field",
        })
    
    if oem_manufacturers:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "oem_manufacturers",
            "value": json.dumps(oem_manufacturers),
            "type": "list.single_line_text_field",
        })
    
    if engine_family:
        metafields.append({
            "namespace": METAFIELD_NAMESPACE,
            "key": "engine_family",
            "value": engine_family,
            "type": "single_line_text_field",
        })
    
    if not metafields:
        return True
    
    return _set_product_metafields(gid, metafields)


def _set_product_metafields(product_gid: str, metafields: list[dict]) -> bool:
    """
    Set multiple metafields on a product using GraphQL.
    
    Args:
        product_gid: Product GID (e.g., gid://shopify/Product/123)
        metafields: List of metafield dicts with namespace, key, value, type
    
    Returns:
        True if successful
    """
    mutation = """
    mutation productUpdate($input: ProductInput!) {
        productUpdate(input: $input) {
            product {
                id
            }
            userErrors {
                field
                message
            }
        }
    }
    """
    
    variables = {
        "input": {
            "id": product_gid,
            "metafields": metafields,
        }
    }
    
    result = _shopify_graphql(mutation, variables)
    
    if result:
        update_result = result.get("productUpdate", {})
        errors = update_result.get("userErrors", [])
        
        if errors:
            logger.error(f"Error setting metafields on {product_gid}: {errors}")
            return False
        
        return True
    
    return False


def get_product_metafields(shopify_product_id: int) -> dict[str, Any]:
    """
    Get all JAKS metafields for a product.
    
    Returns:
        dict mapping metafield key to value
    """
    if isinstance(shopify_product_id, int) or str(shopify_product_id).isdigit():
        gid = f"gid://shopify/Product/{shopify_product_id}"
    else:
        gid = shopify_product_id
    
    query = """
    query GetProductMetafields($id: ID!) {
        product(id: $id) {
            metafields(namespace: "jaks", first: 50) {
                edges {
                    node {
                        key
                        value
                        type
                    }
                }
            }
        }
    }
    """
    
    result = _shopify_graphql(query, {"id": gid})
    
    if not result:
        return {}
    
    metafields = {}
    product = result.get("product", {})
    edges = product.get("metafields", {}).get("edges", [])
    
    for edge in edges:
        node = edge.get("node", {})
        key = node.get("key")
        value = node.get("value")
        mf_type = node.get("type")
        
        # Parse JSON values
        if mf_type in ("json", "list.single_line_text_field"):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
        elif mf_type == "boolean":
            value = value.lower() == "true" if isinstance(value, str) else bool(value)
        elif mf_type == "number_decimal":
            try:
                value = float(value)
            except (ValueError, TypeError):
                pass
        
        metafields[key] = value
    
    return metafields


# ============================================================================
# Collection Management for OEM and Engine Families
# ============================================================================

def ensure_oem_collection(oem_manufacturer: str) -> Optional[int]:
    """
    Ensure a smart collection exists for an OEM manufacturer.
    
    Creates a collection like "Caterpillar Parts" with automated rules.
    
    Args:
        oem_manufacturer: OEM name (e.g., Caterpillar, Cummins)
    
    Returns:
        Collection ID if successful, None otherwise
    """
    # Normalize manufacturer name
    oem_clean = oem_manufacturer.strip().title()
    collection_title = f"{oem_clean} Parts"
    
    # Check if collection exists
    existing = _find_collection_by_title(collection_title)
    if existing:
        return existing
    
    # Create smart collection with metafield rule
    collection_data = {
        "smart_collection": {
            "title": collection_title,
            "rules": [
                {
                    "column": "product_metafield_definition",
                    "relation": "equals",
                    "condition": f"jaks.oem_manufacturers:{oem_clean}",
                }
            ],
            "disjunctive": False,
            "published": True,
        }
    }
    
    resp = _shopify_rest("POST", "/smart_collections.json", collection_data)
    
    if resp and resp.status_code == 201:
        data = resp.json()
        collection_id = data.get("smart_collection", {}).get("id")
        logger.info(f"Created OEM collection: {collection_title} (ID: {collection_id})")
        return collection_id
    
    return None


def ensure_engine_family_collection(engine_family: str) -> Optional[int]:
    """
    Ensure a smart collection exists for an engine family.
    
    Creates a collection like "ISX Series Parts" with automated rules.
    
    Args:
        engine_family: Engine family name (e.g., ISX, C-Series, DD)
    
    Returns:
        Collection ID if successful, None otherwise
    """
    family_clean = engine_family.strip().upper()
    collection_title = f"{family_clean} Series Parts"
    
    existing = _find_collection_by_title(collection_title)
    if existing:
        return existing
    
    collection_data = {
        "smart_collection": {
            "title": collection_title,
            "rules": [
                {
                    "column": "product_metafield_definition",
                    "relation": "equals",
                    "condition": f"jaks.engine_family:{family_clean}",
                }
            ],
            "disjunctive": False,
            "published": True,
        }
    }
    
    resp = _shopify_rest("POST", "/smart_collections.json", collection_data)
    
    if resp and resp.status_code == 201:
        data = resp.json()
        collection_id = data.get("smart_collection", {}).get("id")
        logger.info(f"Created engine family collection: {collection_title} (ID: {collection_id})")
        return collection_id
    
    return None


def _find_collection_by_title(title: str) -> Optional[int]:
    """Find a collection by title, return its ID if found."""
    endpoint = f"/smart_collections.json?title={title}"
    resp = _shopify_rest("GET", endpoint)
    
    if resp:
        data = resp.json()
        collections = data.get("smart_collections", [])
        if collections:
            return collections[0].get("id")
    
    return None


# ============================================================================
# Batch Sync Functions
# ============================================================================

def sync_all_product_metafields(progress_callback=None) -> dict:
    """
    Sync all cross-reference and family metafields from database to Shopify.
    
    This is a batch operation that:
    1. Gets all products with Shopify IDs from DB
    2. Updates metafields for each product
    
    Args:
        progress_callback: Optional callback(current, total, message)
    
    Returns:
        dict with stats: {synced: int, errors: int}
    """
    from db.database import get_connection
    from db.xref_chain_service import get_product_xref_chains
    from db.product_family_service import get_product_family
    
    conn = get_connection()
    stats = {"synced": 0, "errors": 0, "skipped": 0}
    
    # Get products with Shopify IDs
    rows = conn.execute("""
        SELECT id, sku, shopify_id, pai_part_number, oem_part_number,
               manufacturer, category
        FROM products
        WHERE shopify_id IS NOT NULL
    """).fetchall()
    
    total = len(rows)
    
    for i, row in enumerate(rows):
        if progress_callback:
            sku = row[1] if isinstance(row, (list, tuple)) else row["sku"]
            progress_callback(i + 1, total, f"Syncing metafields for {sku}...")
        
        try:
            product_id = row[0] if isinstance(row, (list, tuple)) else row["id"]
            shopify_id = row[2] if isinstance(row, (list, tuple)) else row["shopify_id"]
            pai_pn = row[3] if isinstance(row, (list, tuple)) else row.get("pai_part_number")
            oem_pn = row[4] if isinstance(row, (list, tuple)) else row.get("oem_part_number")
            manufacturer = row[5] if isinstance(row, (list, tuple)) else row.get("manufacturer")
            
            # Get cross-reference chains
            xref_chains = get_product_xref_chains(product_id)
            alternate_parts = []
            oem_parts = []
            
            for chain in xref_chains:
                if chain.source_type == "oem":
                    oem_parts.append(chain.source_part_number)
                alternate_parts.append({
                    "part_number": chain.source_part_number,
                    "type": chain.source_type,
                    "confidence": chain.confidence,
                })
            
            # Update cross-ref metafields
            if oem_parts or pai_pn or alternate_parts:
                update_product_cross_refs(
                    shopify_id,
                    oem_part_numbers=oem_parts or ([oem_pn] if oem_pn else None),
                    pai_part_number=pai_pn,
                    alternate_parts=alternate_parts if alternate_parts else None,
                    confidence=max((a["confidence"] for a in alternate_parts), default=None),
                )
            
            # Get product family
            family = get_product_family(product_id)
            if family:
                # Get variant info for this product
                member = next((m for m in family.members if m.product_id == product_id), None)
                
                update_product_family_metafields(
                    shopify_id,
                    family_name=family.name,
                    variant_type=member.variant_code if member else None,
                    base_part_number=family.base_part_number,
                    is_base=member.is_base_variant if member else None,
                    is_recommended=member.is_recommended if member else None,
                )
            
            # Update engine metafields based on manufacturer
            if manufacturer:
                update_product_engine_metafields(
                    shopify_id,
                    oem_manufacturers=[manufacturer] if manufacturer else None,
                )
            
            stats["synced"] += 1
            
        except Exception as e:
            logger.exception(f"Error syncing metafields for product {row}: {e}")
            stats["errors"] += 1
    
    return stats
