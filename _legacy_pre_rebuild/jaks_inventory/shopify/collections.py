"""Shopify Collections module for JAKS Inventory.

Provides functions for:
- Creating/managing custom collections
- Creating automated (smart) collections based on product conditions
- Updating collection metadata
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

import requests

# Load environment variables
_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_root))
from dotenv_simple import load_dotenv
load_dotenv(_root / ".env")

# Shopify configuration
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "jaks-diesel-3")
API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-01")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")


def _shopify_request(
    method: str,
    endpoint: str,
    data: dict | None = None,
) -> requests.Response | None:
    """Make authenticated request to Shopify Admin API."""
    if not ACCESS_TOKEN:
        print("⚠️ SHOPIFY_ACCESS_TOKEN not set")
        return None
    
    url = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/{API_VERSION}{endpoint}"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    
    try:
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        elif method.upper() == "POST":
            resp = requests.post(url, headers=headers, json=data, timeout=30)
        elif method.upper() == "PUT":
            resp = requests.put(url, headers=headers, json=data, timeout=30)
        elif method.upper() == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=30)
        else:
            return None
        
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"Shopify API error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        return None


# ============================================================================
# Smart Collections (Automated)
# ============================================================================

def create_smart_collection(
    title: str,
    rules: list[dict],
    disjunctive: bool = False,
    handle: str | None = None,
    body_html: str = "",
    image_url: str | None = None,
    published: bool = True,
) -> dict | None:
    """Create a smart (automated) collection in Shopify.
    
    Args:
        title: Collection title (e.g., "CAT C15 Parts")
        rules: List of rule dicts, each with:
            - column: "title", "tag", "vendor", "product_type", "variant_title"
            - relation: "contains", "equals", "starts_with", "ends_with", 
                       "greater_than", "less_than"
            - condition: The value to match
        disjunctive: If True, match ANY rule (OR). If False, match ALL rules (AND)
        handle: URL handle (auto-generated from title if not provided)
        body_html: Collection description HTML
        image_url: URL to collection image (will be fetched by Shopify)
        published: Whether collection is visible
        
    Returns:
        Created collection dict or None on error
        
    Example:
        create_smart_collection(
            title="CAT C15 Parts",
            rules=[
                {"column": "title", "relation": "contains", "condition": "C15"},
                {"column": "tag", "relation": "equals", "condition": "cat-c15"}
            ],
            disjunctive=True  # Match either rule
        )
    """
    payload = {
        "smart_collection": {
            "title": title,
            "rules": rules,
            "disjunctive": disjunctive,
            "published": published,
        }
    }
    
    if handle:
        payload["smart_collection"]["handle"] = handle
    if body_html:
        payload["smart_collection"]["body_html"] = body_html
    if image_url:
        payload["smart_collection"]["image"] = {"src": image_url}
    
    resp = _shopify_request("POST", "/smart_collections.json", payload)
    if resp:
        return resp.json().get("smart_collection")
    return None


def get_smart_collections(limit: int = 250) -> list[dict]:
    """Get all smart collections."""
    resp = _shopify_request("GET", f"/smart_collections.json?limit={limit}")
    if resp:
        return resp.json().get("smart_collections", [])
    return []


def get_smart_collection_by_handle(handle: str) -> dict | None:
    """Get a smart collection by its handle."""
    resp = _shopify_request("GET", f"/smart_collections.json?handle={handle}")
    if resp:
        collections = resp.json().get("smart_collections", [])
        return collections[0] if collections else None
    return None


def update_smart_collection(collection_id: int, updates: dict) -> dict | None:
    """Update a smart collection.
    
    Args:
        collection_id: Shopify collection ID
        updates: Dict of fields to update (title, rules, body_html, etc.)
    """
    payload = {"smart_collection": updates}
    resp = _shopify_request("PUT", f"/smart_collections/{collection_id}.json", payload)
    if resp:
        return resp.json().get("smart_collection")
    return None


def delete_smart_collection(collection_id: int) -> bool:
    """Delete a smart collection."""
    resp = _shopify_request("DELETE", f"/smart_collections/{collection_id}.json")
    return resp is not None


# ============================================================================
# Custom Collections (Manual)
# ============================================================================

def create_custom_collection(
    title: str,
    handle: str | None = None,
    body_html: str = "",
    image_url: str | None = None,
    published: bool = True,
) -> dict | None:
    """Create a custom (manual) collection in Shopify.
    
    Products must be added separately via add_product_to_collection().
    """
    payload = {
        "custom_collection": {
            "title": title,
            "published": published,
        }
    }
    
    if handle:
        payload["custom_collection"]["handle"] = handle
    if body_html:
        payload["custom_collection"]["body_html"] = body_html
    if image_url:
        payload["custom_collection"]["image"] = {"src": image_url}
    
    resp = _shopify_request("POST", "/custom_collections.json", payload)
    if resp:
        return resp.json().get("custom_collection")
    return None


def get_custom_collections(limit: int = 250) -> list[dict]:
    """Get all custom collections."""
    resp = _shopify_request("GET", f"/custom_collections.json?limit={limit}")
    if resp:
        return resp.json().get("custom_collections", [])
    return []


def add_product_to_collection(collection_id: int, product_id: int) -> dict | None:
    """Add a product to a custom collection."""
    payload = {
        "collect": {
            "collection_id": collection_id,
            "product_id": product_id,
        }
    }
    resp = _shopify_request("POST", "/collects.json", payload)
    if resp:
        return resp.json().get("collect")
    return None


# ============================================================================
# Engine Collection Presets
# ============================================================================

# Predefined engine collections for JAKS Diesel
ENGINE_COLLECTION_PRESETS = [
    {
        "title": "CAT C15 Parts",
        "handle": "cat-c15",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "C15"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Caterpillar C15 engines.</p>",
    },
    {
        "title": "CAT C13 Parts", 
        "handle": "cat-c13",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "C13"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Caterpillar C13 engines.</p>",
    },
    {
        "title": "CAT C12 Parts",
        "handle": "cat-c12", 
        "rules": [
            {"column": "title", "relation": "contains", "condition": "C12"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Caterpillar C12 engines.</p>",
    },
    {
        "title": "CAT C10 Parts",
        "handle": "cat-c10",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "C10"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Caterpillar C10 engines.</p>",
    },
    {
        "title": "CAT 3406 Parts",
        "handle": "cat-3406",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "3406"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Caterpillar 3406 engines.</p>",
    },
    {
        "title": "Cummins ISX Parts",
        "handle": "cummins-isx",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "ISX"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Cummins ISX engines.</p>",
    },
    {
        "title": "Cummins ISM Parts",
        "handle": "cummins-ism",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "ISM"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Cummins ISM engines.</p>",
    },
    {
        "title": "Detroit DD15 Parts",
        "handle": "detroit-dd15",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "DD15"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Detroit Diesel DD15 engines.</p>",
    },
    {
        "title": "Detroit DD13 Parts",
        "handle": "detroit-dd13",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "DD13"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Detroit Diesel DD13 engines.</p>",
    },
    {
        "title": "Mack MP8 Parts",
        "handle": "mack-mp8",
        "rules": [
            {"column": "title", "relation": "contains", "condition": "MP8"},
        ],
        "body_html": "<p>Cylinder heads, rebuild kits, and components for Mack MP8 engines.</p>",
    },
]


def create_engine_collections(
    progress_callback: Callable[[int, str], None] | None = None,
    skip_existing: bool = True,
) -> dict:
    """Create all predefined engine collections.
    
    Args:
        progress_callback: Optional callback (progress_pct, message)
        skip_existing: If True, skip collections that already exist
        
    Returns:
        Dict with 'created', 'skipped', 'errors' lists
    """
    result = {
        "created": [],
        "skipped": [],
        "errors": [],
    }
    
    # Get existing collections to check for duplicates
    existing_handles = set()
    if skip_existing:
        existing = get_smart_collections()
        existing_handles = {c.get("handle") for c in existing if c.get("handle")}
    
    total = len(ENGINE_COLLECTION_PRESETS)
    
    for i, preset in enumerate(ENGINE_COLLECTION_PRESETS):
        handle = preset["handle"]
        title = preset["title"]
        
        if progress_callback:
            pct = int((i / total) * 100)
            progress_callback(pct, f"Processing {title}...")
        
        # Skip if exists
        if handle in existing_handles:
            result["skipped"].append(handle)
            print(f"⏭️ Skipped (exists): {title}")
            continue
        
        # Create collection
        collection = create_smart_collection(
            title=preset["title"],
            handle=preset["handle"],
            rules=preset["rules"],
            body_html=preset.get("body_html", ""),
            disjunctive=False,
        )
        
        if collection:
            result["created"].append(handle)
            print(f"✅ Created: {title} → /collections/{handle}")
        else:
            result["errors"].append(handle)
            print(f"❌ Error creating: {title}")
    
    if progress_callback:
        progress_callback(100, "Done!")
    
    return result


def get_collection_products(collection_id: int, limit: int = 250) -> list[dict]:
    """Get products in a collection."""
    resp = _shopify_request(
        "GET", 
        f"/collections/{collection_id}/products.json?limit={limit}"
    )
    if resp:
        return resp.json().get("products", [])
    return []


# ============================================================================
# CLI for testing
# ============================================================================

if __name__ == "__main__":
    import sys
    
    if not ACCESS_TOKEN:
        print("❌ Set SHOPIFY_ACCESS_TOKEN environment variable")
        sys.exit(1)
    
    print("=" * 60)
    print("JAKS Diesel - Shopify Collection Creator")
    print("=" * 60)
    print()
    
    # List existing collections
    print("📋 Existing Smart Collections:")
    existing = get_smart_collections()
    for c in existing:
        print(f"   • {c.get('title')} → /collections/{c.get('handle')}")
    print()
    
    # Ask to create engine collections
    print("Engine collections to create:")
    for preset in ENGINE_COLLECTION_PRESETS:
        print(f"   • {preset['title']} → /collections/{preset['handle']}")
    print()
    
    response = input("Create engine collections? (y/n): ").strip().lower()
    if response == "y":
        print()
        result = create_engine_collections()
        print()
        print("=" * 60)
        print(f"✅ Created: {len(result['created'])}")
        print(f"⏭️ Skipped: {len(result['skipped'])}")
        print(f"❌ Errors: {len(result['errors'])}")
    else:
        print("Cancelled.")
