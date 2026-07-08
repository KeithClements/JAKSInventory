"""Template/type inference utilities.

These heuristics are used by both the UI (for display) and the uploader
(for Shopify template selection). Keeping them here ensures one source of truth.

Keep this logic deterministic and free of UI/network dependencies.

Template naming convention:
- Templates are named: product.<suffix>.json in Shopify theme
- This module returns just the suffix (e.g., "turbocharger-product")
- Shopify looks for: templates/product.turbocharger-product.json

Category-to-Template Mapping:
- Turbochargers → turbocharger-product (with boost specs, core charge display)
- Fuel Injectors → fuel-injector-product (with flow rate, set pricing)
- Engine Rebuild Kits → rebuild-kit-product (with kit contents, CPL/ESN compatibility)
- Cylinder Head & Components → cylinder-head-product (with casting numbers, specs)
- Gaskets → gasket-product (with material, dimensions)
- Piston Cylinder Kit & Components → piston-kit-product (with bore size, ring specs)
- Oil Pumps / Lubrication → oil-system-product (with pressure ratings)
- Exhaust System → exhaust-product (with pipe diameter, material)
- Cooling System → cooling-product (with capacity, thermostat specs)
- Fuel System → fuel-system-product (with pressure specs)
- Air Intake → air-intake-product (with filter specs)
- Crankshaft / Damper → crankshaft-product (with balance specs)
- Default → diesel-part-product (generic fallback)
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Product Type Inference
# ─────────────────────────────────────────────────────────────────────────────

def infer_product_type(title: str, category: str = "") -> str:
    """Infer Shopify product_type from title and category.
    
    This appears in Shopify admin and can be used for filtering/collections.
    Priority: category name if it maps to a product type, else infer from title.
    """
    t = (title or "").lower()
    c = (category or "").lower()
    
    # Check category first - use as product_type if it makes sense
    category_map = {
        "engine_rebuild_kits": "Engine Rebuild Kits",
        "engine rebuild kits": "Engine Rebuild Kits",
        "fuel_system": "Fuel System",
        "fuel system": "Fuel System",
        "turbochargers": "Turbochargers",
        "turbocharger": "Turbochargers",
        "cylinder_heads": "Cylinder Heads",
        "cylinder heads": "Cylinder Heads",
        "cooling_system": "Cooling System",
        "cooling system": "Cooling System",
        "lubrication_system": "Oil System",
        "lubrication system": "Oil System",
        "exhaust_system": "Exhaust",
        "exhaust system": "Exhaust",
        "air_intake": "Air Intake",
        "air intake": "Air Intake",
    }
    
    # Normalize category (replace underscores, try both formats)
    cat_normalized = c.replace("_", " ").strip()
    if cat_normalized in category_map:
        return category_map[cat_normalized]
    if c in category_map:
        return category_map[c]
    
    # Turbochargers
    if "turbo" in t or "turbocharger" in t:
        return "Turbochargers"
    
    # Fuel Injectors
    if "injector" in t or "fuel injector" in t:
        return "Fuel Injectors"
    
    # Rebuild Kits (check before generic "kit")
    if "rebuild kit" in t or "inframe kit" in t or "overhaul kit" in t:
        return "Engine Rebuild Kits"
    if "kit" in t and ("engine" in t or "inframe" in t or "overhaul" in t):
        return "Engine Rebuild Kits"
    
    # Cylinder Heads
    if "cylinder head" in t or "head gasket set" in t:
        return "Cylinder Heads"
    
    # Gaskets
    if "gasket" in t:
        return "Gaskets"
    
    # Pistons & Liners
    if "piston" in t or "liner" in t or "ring set" in t:
        return "Pistons & Liners"
    
    # Oil System
    if "oil pump" in t or "oil cooler" in t or "oil pan" in t:
        return "Oil System"
    
    # Exhaust
    if "exhaust" in t or "manifold" in t or "dpf" in t or "egr" in t:
        return "Exhaust"
    
    # Cooling System
    if "water pump" in t or "thermostat" in t or "radiator" in t or "coolant" in t:
        return "Cooling System"
    
    # Fuel System (non-injector)
    if "fuel pump" in t or "fuel filter" in t or "fuel line" in t:
        return "Fuel System"
    
    # Air Intake
    if "air filter" in t or "intake" in t or "air cleaner" in t:
        return "Air Intake"
    
    # Crankshaft / Damper
    if "crankshaft" in t or "damper" in t or "harmonic balancer" in t:
        return "Crankshaft"
    
    # Generic kit fallback
    if "kit" in t:
        return "Parts Kits"
    
    return "Diesel Engine Parts"


# ─────────────────────────────────────────────────────────────────────────────
# Template Suffix Inference
# ─────────────────────────────────────────────────────────────────────────────

# Mapping from category slugs to template suffixes
CATEGORY_TEMPLATE_MAP: dict[str, str] = {
    # Primary categories
    "turbochargers": "turbocharger-product",
    "fuel injectors": "fuel-injector-product",
    "engine rebuild kits": "rebuild-kit-product",
    "cylinder head & components": "cylinder-head-product",
    "cylinder heads": "cylinder-head-product",
    "gaskets": "gasket-product",
    "piston cylinder kit & components": "piston-kit-product",
    "pistons & liners": "piston-kit-product",
    "lubrication system": "oil-system-product",
    "oil pumps & coolers": "oil-system-product",
    "exhaust system": "exhaust-product",
    "cooling system": "cooling-product",
    "fuel system": "fuel-system-product",
    "air intake system": "air-intake-product",
    "crankshaft damper": "crankshaft-product",
    "engine parts": "diesel-part-product",
}

# Mapping from product_type to template (fallback when category doesn't match)
PRODUCT_TYPE_TEMPLATE_MAP: dict[str, str] = {
    # Plural forms (what infer_product_type returns)
    "Turbochargers": "turbocharger-product",
    "Fuel Injectors": "fuel-injector-product",
    "Engine Rebuild Kits": "rebuild-kit-product",
    "Cylinder Heads": "cylinder-head-product",
    "Gaskets": "gasket-product",
    "Pistons & Liners": "piston-kit-product",
    "Oil System": "oil-system-product",
    "Exhaust": "exhaust-product",
    "Cooling System": "cooling-product",
    "Fuel System": "fuel-system-product",
    "Air Intake": "air-intake-product",
    "Crankshaft": "crankshaft-product",
    "Parts Kits": "rebuild-kit-product",
    "Diesel Engine Parts": "diesel-part-product",
    # Singular forms (for backwards compatibility / manual entry)
    "Turbocharger": "turbocharger-product",
    "Fuel Injector": "fuel-injector-product",
    "Rebuild Kit": "rebuild-kit-product",
    "Cylinder Head": "cylinder-head-product",
    "Gasket": "gasket-product",
    "Piston Kit": "piston-kit-product",
    "Parts Kit": "rebuild-kit-product",
    "Diesel Engine Part": "diesel-part-product",
}


def infer_template_suffix(title: str, category: str, product_type: str) -> str | None:
    """Return a Shopify template_suffix (without the 'product.' prefix).
    
    Priority:
    1. Category-based mapping (most specific)
    2. Product type-based mapping (fallback)
    3. Title keyword detection (last resort)
    4. None = use default product template
    
    Returns:
        Template suffix string (e.g., "turbocharger-product") or None for default.
    """
    t = (title or "").lower()
    c = (category or "").lower()
    pt = (product_type or "").strip()
    
    # 1. Try category-based mapping first (most reliable)
    for cat_key, template in CATEGORY_TEMPLATE_MAP.items():
        if cat_key in c:
            return template
    
    # 2. Try product type mapping
    if pt in PRODUCT_TYPE_TEMPLATE_MAP:
        return PRODUCT_TYPE_TEMPLATE_MAP[pt]
    
    # 3. Title keyword fallback (for edge cases)
    if "turbo" in t:
        return "turbocharger-product"
    if "injector" in t:
        return "fuel-injector-product"
    if "rebuild kit" in t or "inframe kit" in t or "overhaul kit" in t:
        return "rebuild-kit-product"
    if "cylinder head" in t:
        return "cylinder-head-product"
    if "gasket" in t:
        return "gasket-product"
    if "piston" in t or "liner" in t:
        return "piston-kit-product"
    if "oil pump" in t or "oil cooler" in t:
        return "oil-system-product"
    if "exhaust" in t or "manifold" in t:
        return "exhaust-product"
    if "water pump" in t or "thermostat" in t:
        return "cooling-product"
    if "kit" in t:
        return "rebuild-kit-product"
    
    # 4. Default fallback - use generic diesel part template
    return "diesel-part-product"


def get_all_template_suffixes() -> list[str]:
    """Return list of all template suffixes that need to be created in Shopify."""
    templates = set(CATEGORY_TEMPLATE_MAP.values())
    templates.update(PRODUCT_TYPE_TEMPLATE_MAP.values())
    return sorted(templates)


def get_template_for_category(category: str) -> str:
    """Direct lookup of template for a category name."""
    c = (category or "").lower()
    for cat_key, template in CATEGORY_TEMPLATE_MAP.items():
        if cat_key in c:
            return template
    return "diesel-part-product"
