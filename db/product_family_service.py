"""Product family service for managing product families and alternates.

Product families group related products that are variations of the same base part:
- Standard vs HP (High Performance) versions
- Economy vs Premium versions  
- Different size variants (+0.50mm, -0.25mm)
- Different manufacturers for same OEM part

Example family: "ISX15 Inframe Kit" (OEM: 5473069)
- PAI ISX119145 (Standard)
- PAI ISX119145HP (HP version)
- PAI ISX119145E (Economy version)
- McBee MCB-5473069 (Different manufacturer)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from db.database import get_connection
from utils.part_normalizer import normalize_part_number, get_base_part_number, categorize_variant


@dataclass
class ProductFamily:
    """A group of related products (alternates/variants)."""
    id: int
    name: str                              # "ISX15 Inframe Kit"
    base_part_number: Optional[str] = None # Base/standard part number
    oem_part_number: Optional[str] = None  # OEM part this replaces
    aftermarket_manufacturer_id: Optional[int] = None
    oem_manufacturer_id: Optional[int] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    # Populated when fetching with members
    members: list[ProductAlternate] = field(default_factory=list)


@dataclass
class ProductAlternate:
    """A product's membership in a family with variant info."""
    id: int
    family_id: int
    product_id: int
    variant_category: Optional[str] = None   # quality, size, manufacturer, package
    variant_code: Optional[str] = None       # HP, E, 010, PAI, etc.
    variant_detail: Optional[str] = None     # Human-readable: "High Performance"
    is_base_variant: bool = False            # TRUE for the standard/base product
    is_recommended: bool = False             # TRUE if this is the suggested option
    recommendation_score: float = 0.0        # Score for ranking (higher = better)
    sort_order: int = 0
    created_at: Optional[datetime] = None
    
    # Populated when fetching
    product_sku: Optional[str] = None
    product_title: Optional[str] = None
    product_cost: Optional[float] = None
    product_price: Optional[float] = None


# Variant categories (structured enum-like)
VARIANT_CAT_QUALITY = "quality"           # HP, E, STD, Premium
VARIANT_CAT_SIZE = "size"                 # 010, 020, 030
VARIANT_CAT_MANUFACTURER = "manufacturer" # Different maker, same OEM
VARIANT_CAT_PACKAGE = "package"           # Kit vs individual

# Legacy variant types (for backward compat)
VARIANT_STANDARD = "standard"
VARIANT_HP = "hp"
VARIANT_ECONOMY = "economy"
VARIANT_OVERSIZED = "oversized"
VARIANT_UNDERSIZED = "undersized"
VARIANT_PREMIUM = "premium"
VARIANT_OTHER = "other"


def create_family(
    name: str,
    base_part_number: Optional[str] = None,
    oem_part_number: Optional[str] = None,
    aftermarket_manufacturer_id: Optional[int] = None,
    oem_manufacturer_id: Optional[int] = None,
    description: Optional[str] = None,
) -> ProductFamily:
    """Create a new product family."""
    conn = get_connection()
    try:
        # Normalize part numbers for consistent storage
        if base_part_number:
            base_part_number = normalize_part_number(base_part_number)
        if oem_part_number:
            oem_part_number = normalize_part_number(oem_part_number)
            
        conn.execute(
            """INSERT INTO product_families 
               (name, base_part_number, oem_part_number, 
                aftermarket_manufacturer_id, oem_manufacturer_id, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, base_part_number, oem_part_number,
             aftermarket_manufacturer_id, oem_manufacturer_id, description)
        )
        conn.commit()
        
        family_id = conn.lastrowid
        return get_family_by_id(family_id)
    finally:
        conn.close()


def get_family_by_id(family_id: int, include_members: bool = False) -> Optional[ProductFamily]:
    """Get a product family by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM product_families WHERE id = ?",
            (family_id,)
        ).fetchone()
        
        if not row:
            return None
        
        family = ProductFamily(
            id=row["id"],
            name=row["name"],
            base_part_number=row.get("base_part_number"),
            oem_part_number=row.get("oem_part_number"),
            aftermarket_manufacturer_id=row.get("aftermarket_manufacturer_id"),
            oem_manufacturer_id=row.get("oem_manufacturer_id"),
            description=row.get("description"),
            notes=row.get("notes"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
        
        if include_members:
            family.members = get_family_members(family_id)
        
        return family
    finally:
        conn.close()


def get_family_by_part_number(part_number: str) -> Optional[ProductFamily]:
    """Find a family by base or OEM part number."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM product_families 
               WHERE base_part_number = ? OR oem_part_number = ?""",
            (part_number.upper(), part_number.upper())
        ).fetchone()
        
        if row:
            return ProductFamily(
                id=row["id"],
                name=row["name"],
                base_part_number=row.get("base_part_number"),
                oem_part_number=row.get("oem_part_number"),
                aftermarket_manufacturer_id=row.get("aftermarket_manufacturer_id"),
                oem_manufacturer_id=row.get("oem_manufacturer_id"),
                description=row.get("description"),
                notes=row.get("notes"),
            )
        return None
    finally:
        conn.close()


def get_product_family(product_id: int) -> Optional[ProductFamily]:
    """Get the family a product belongs to, if any."""
    conn = get_connection()
    try:
        # Check product_alternates first
        row = conn.execute(
            """SELECT pf.* FROM product_families pf
               JOIN product_alternates pa ON pa.family_id = pf.id
               WHERE pa.product_id = ?""",
            (product_id,)
        ).fetchone()
        
        if not row:
            # Check products.family_id
            row = conn.execute(
                """SELECT pf.* FROM product_families pf
                   JOIN products p ON p.family_id = pf.id
                   WHERE p.id = ?""",
                (product_id,)
            ).fetchone()
        
        if row:
            family = ProductFamily(
                id=row["id"],
                name=row["name"],
                base_part_number=row.get("base_part_number"),
                oem_part_number=row.get("oem_part_number"),
                aftermarket_manufacturer_id=row.get("aftermarket_manufacturer_id"),
                oem_manufacturer_id=row.get("oem_manufacturer_id"),
                description=row.get("description"),
                notes=row.get("notes"),
            )
            family.members = get_family_members(family.id)
            return family
        return None
    finally:
        conn.close()


def get_family_members(family_id: int) -> list[ProductAlternate]:
    """Get all product alternates in a family."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT pa.*, p.sku as product_sku, p.title as product_title
               FROM product_alternates pa
               JOIN products p ON p.id = pa.product_id
               WHERE pa.family_id = ?
               ORDER BY pa.sort_order, pa.is_base_variant DESC""",
            (family_id,)
        ).fetchall()
        
        return [
            ProductAlternate(
                id=row["id"],
                family_id=row["family_id"],
                product_id=row["product_id"],
                variant_type=row.get("variant_type"),
                variant_detail=row.get("variant_detail"),
                is_base_variant=bool(row.get("is_base_variant", False)),
                sort_order=row.get("sort_order", 0),
                created_at=row.get("created_at"),
                product_sku=row.get("product_sku"),
                product_title=row.get("product_title"),
            )
            for row in rows
        ]
    finally:
        conn.close()


def add_product_to_family(
    family_id: int,
    product_id: int,
    variant_type: str = VARIANT_STANDARD,
    variant_detail: Optional[str] = None,
    is_base_variant: bool = False,
    sort_order: int = 0,
) -> ProductAlternate:
    """Add a product to a family as an alternate."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO product_alternates 
               (family_id, product_id, variant_type, variant_detail, is_base_variant, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (family_id, product_id, variant_type, variant_detail, is_base_variant, sort_order)
        )
        
        # Also update the product's family_id and variant columns
        conn.execute(
            """UPDATE products 
               SET family_id = ?, variant_type = ?, variant_detail = ?
               WHERE id = ?""",
            (family_id, variant_type, variant_detail, product_id)
        )
        
        conn.commit()
        
        alt_id = conn.lastrowid
        row = conn.execute(
            "SELECT * FROM product_alternates WHERE id = ?",
            (alt_id,)
        ).fetchone()
        
        return ProductAlternate(
            id=row["id"],
            family_id=row["family_id"],
            product_id=row["product_id"],
            variant_type=row.get("variant_type"),
            variant_detail=row.get("variant_detail"),
            is_base_variant=bool(row.get("is_base_variant", False)),
            sort_order=row.get("sort_order", 0),
        )
    finally:
        conn.close()


def remove_product_from_family(product_id: int) -> bool:
    """Remove a product from its family."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM product_alternates WHERE product_id = ?",
            (product_id,)
        )
        conn.execute(
            """UPDATE products 
               SET family_id = NULL, variant_type = NULL, variant_detail = NULL
               WHERE id = ?""",
            (product_id,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def detect_variant_type(part_number: str, title: str = "") -> tuple[str, Optional[str]]:
    """Detect variant type from part number suffix or title.
    
    Returns: (variant_type, variant_detail)
    
    Examples:
        "ISX101HP" -> ("hp", "High Performance")
        "ISX101E" -> ("economy", "Economy")
        "ISX101+050" -> ("oversized", "+0.50mm")
    """
    part_number = part_number.upper().strip()
    title = title.upper()
    
    # Check HP suffix
    if part_number.endswith("HP") or "HIGH PERFORMANCE" in title:
        return (VARIANT_HP, "High Performance")
    
    # Check Economy suffix
    if part_number.endswith("E") and not part_number.endswith("SE"):
        return (VARIANT_ECONOMY, "Economy")
    
    # Check size variants
    import re
    size_match = re.search(r'[+\-](\d{2,3})', part_number)
    if size_match:
        size = size_match.group(1)
        if part_number.count('+') > 0:
            mm = f"+0.{size}mm" if len(size) == 2 else f"+{size[0]}.{size[1:]}mm"
            return (VARIANT_OVERSIZED, mm)
        else:
            mm = f"-0.{size}mm" if len(size) == 2 else f"-{size[0]}.{size[1:]}mm"
            return (VARIANT_UNDERSIZED, mm)
    
    # Check title for variant hints
    if "PREMIUM" in title:
        return (VARIANT_PREMIUM, "Premium")
    
    return (VARIANT_STANDARD, None)


def get_or_create_family_for_product(
    product_id: int,
    part_number: str,
    title: str,
    oem_part_number: Optional[str] = None,
    aftermarket_manufacturer_id: Optional[int] = None,
) -> ProductFamily:
    """Get existing family for product or create a new one.
    
    Attempts to find existing family by base part number,
    otherwise creates a new family.
    """
    # Strip variant suffixes to get base part number
    base_part = _get_base_part_number(part_number)
    
    # Check if family already exists
    family = get_family_by_part_number(base_part)
    if not family and oem_part_number:
        family = get_family_by_part_number(oem_part_number)
    
    if not family:
        # Create new family
        family = create_family(
            name=title.split(" - ")[0] if " - " in title else title,
            base_part_number=base_part,
            oem_part_number=oem_part_number,
            aftermarket_manufacturer_id=aftermarket_manufacturer_id,
        )
    
    # Add product to family if not already a member
    existing = [m for m in get_family_members(family.id) if m.product_id == product_id]
    if not existing:
        variant_type, variant_detail = detect_variant_type(part_number, title)
        is_base = variant_type == VARIANT_STANDARD
        add_product_to_family(
            family.id,
            product_id,
            variant_type=variant_type,
            variant_detail=variant_detail,
            is_base_variant=is_base,
        )
    
    return family


def _get_base_part_number(part_number: str) -> str:
    """Strip variant suffixes to get base part number.
    
    Examples:
        "ISX101HP" -> "ISX101"
        "ISX101E" -> "ISX101"
        "ISX101+050" -> "ISX101"
    """
    # Use centralized normalizer
    return get_base_part_number(part_number)


def get_all_families(
    limit: int = 100,
    offset: int = 0,
    search: Optional[str] = None,
) -> list[ProductFamily]:
    """Get all product families with pagination."""
    conn = get_connection()
    try:
        if search:
            search_norm = normalize_part_number(search)
            rows = conn.execute(
                """SELECT * FROM product_families 
                   WHERE name LIKE ? OR base_part_number LIKE ? OR oem_part_number LIKE ?
                   ORDER BY name LIMIT ? OFFSET ?""",
                (f"%{search}%", f"%{search_norm}%", f"%{search_norm}%", limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM product_families ORDER BY name LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        
        return [
            ProductFamily(
                id=row["id"],
                name=row["name"],
                base_part_number=row.get("base_part_number"),
                oem_part_number=row.get("oem_part_number"),
                aftermarket_manufacturer_id=row.get("aftermarket_manufacturer_id"),
                oem_manufacturer_id=row.get("oem_manufacturer_id"),
                description=row.get("description"),
            )
            for row in rows
        ]
    finally:
        conn.close()


# ============================================================================
# RECOMMENDATION SCORING (User 2's suggestion)
# ============================================================================

def calculate_recommendation_score(
    product_id: int,
    cost: float = 0,
    price: float = 0,
    qty_on_hand: int = 0,
    is_preferred_supplier: bool = False,
    sales_count: int = 0,
) -> float:
    """Calculate a recommendation score for ranking alternates.
    
    Higher score = more recommended.
    
    Factors (User 2's recommendation):
    - Margin (price - cost) / price
    - Availability (qty_on_hand > 0)
    - Sales history
    - Preferred supplier status
    
    Returns score from 0.0 to 100.0
    """
    score = 0.0
    
    # Margin component (0-40 points)
    if price > 0 and cost > 0:
        margin_pct = (price - cost) / price
        score += min(40, margin_pct * 100)  # 40% margin = 40 points
    
    # Availability component (0-30 points)
    if qty_on_hand > 0:
        score += 20  # In stock base
        score += min(10, qty_on_hand)  # +1 per unit up to 10
    
    # Sales history component (0-20 points)
    score += min(20, sales_count * 2)  # +2 per sale up to 20
    
    # Preferred supplier bonus (0-10 points)
    if is_preferred_supplier:
        score += 10
    
    return round(score, 2)


def update_family_recommendations(family_id: int) -> None:
    """Recalculate and update recommendations for all alternates in a family.
    
    Sets is_recommended=True on the highest-scoring alternate.
    """
    conn = get_connection()
    try:
        # Get all alternates with product data
        rows = conn.execute(
            """SELECT pa.id, pa.product_id, p.cost, p.selling_price, p.qty_on_hand
               FROM product_alternates pa
               JOIN products p ON p.id = pa.product_id
               WHERE pa.family_id = ?""",
            (family_id,)
        ).fetchall()
        
        if not rows:
            return
        
        # Calculate scores
        scores = []
        for row in rows:
            score = calculate_recommendation_score(
                product_id=row["product_id"],
                cost=row.get("cost") or 0,
                price=row.get("selling_price") or 0,
                qty_on_hand=row.get("qty_on_hand") or 0,
            )
            scores.append((row["id"], score))
        
        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        
        # Update all alternates
        for i, (alt_id, score) in enumerate(scores):
            is_recommended = (i == 0)  # Top scorer is recommended
            conn.execute(
                """UPDATE product_alternates 
                   SET is_recommended = ?, recommendation_score = ?
                   WHERE id = ?""",
                (is_recommended, score, alt_id)
            )
        
        conn.commit()
    finally:
        conn.close()


def get_recommended_alternate(family_id: int) -> Optional[ProductAlternate]:
    """Get the recommended alternate for a family."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT pa.*, p.sku as product_sku, p.title as product_title,
                      p.cost as product_cost, p.selling_price as product_price
               FROM product_alternates pa
               JOIN products p ON p.id = pa.product_id
               WHERE pa.family_id = ? AND pa.is_recommended = 1
               LIMIT 1""",
            (family_id,)
        ).fetchone()
        
        if row:
            return ProductAlternate(
                id=row["id"],
                family_id=row["family_id"],
                product_id=row["product_id"],
                variant_category=row.get("variant_type"),  # Map old column
                variant_code=row.get("variant_detail"),
                is_base_variant=bool(row.get("is_base_variant", False)),
                is_recommended=True,
                recommendation_score=row.get("recommendation_score", 0),
                product_sku=row.get("product_sku"),
                product_title=row.get("product_title"),
                product_cost=row.get("product_cost"),
                product_price=row.get("product_price"),
            )
        return None
    finally:
        conn.close()

