"""Product Grouping and Duplicate Detection Service.

This module handles:
1. Detecting duplicate products across different part numbers
2. Grouping cross-referenced products together
3. Selecting the "master" product from a group
4. AI-assisted matching for similar products

Key Concept: A "Product Group" represents a single physical product that may have
multiple part numbers from different vendors/manufacturers:
- OEM: 5473069 (Cummins original)
- PAI: ISX119-145 (PAI aftermarket)
- HHP: Different URL/listing
- G2: G2-ISX-001 (another vendor)

All should be ONE product in our system with multiple purchase options.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("db.product_grouping")


def normalize_part_number(raw: str) -> str:
    """Normalize a part number for matching.
    
    Rules:
    - Uppercase
    - Remove all non-alphanumeric characters
    - Remove common prefixes (OEM, PN, PART, etc.)
    - Strip leading zeros from numeric portions
    
    Examples:
        '5473069' → '5473069'
        'ISX119-145' → 'ISX119145'
        'ISX-119-145' → 'ISX119145'
        'OEM-5473069' → '5473069'
    """
    if not raw:
        return ""
    s = str(raw).strip().upper()
    # Remove common prefixes
    for prefix in ("OEM-", "OEM", "PN-", "PN", "PART-", "PART", "P/N"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    # Remove all non-alphanumeric
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def extract_base_part_number(normalized: str) -> str:
    """Extract the base part number without size suffixes.
    
    Examples:
        'ISX119145' → 'ISX119145' (no suffix)
        'C15101010HP' → 'C15101' (remove -010HP)
        'C15101010STD' → 'C15101' (remove -010STD)
    """
    if not normalized:
        return ""
    # Remove size suffixes like 010, 020, 030, HP, STD at the end
    # Pattern: digits indicating size (010, 020) followed by optional HP/STD
    cleaned = re.sub(r'(0[1-4]0)?(HP|STD)?$', '', normalized)
    return cleaned if cleaned else normalized


def compute_part_signature(part_numbers: list[str]) -> str:
    """Compute a unique signature from a set of part numbers.
    
    This creates a deterministic hash from all normalized part numbers,
    which can be used to quickly check if products share any numbers.
    """
    normalized = sorted(set(normalize_part_number(pn) for pn in part_numbers if pn))
    if not normalized:
        return ""
    combined = "|".join(normalized)
    return hashlib.md5(combined.encode()).hexdigest()[:12]


@dataclass
class PartNumberMatch:
    """A match between a search part number and a product."""
    product_id: int
    product_sku: str
    matched_number: str  # The part number that matched
    match_type: str  # 'exact', 'normalized', 'base', 'cross_ref'
    confidence: float  # 0.0 to 1.0
    source: str  # Where this part number came from


@dataclass
class ProductGroup:
    """A group of products that are actually the same physical item."""
    group_id: str  # Unique identifier for the group
    master_product_id: int | None  # The "main" product we'll use
    master_sku: str  # SKU we'll use for Shopify
    member_product_ids: list[int]  # All products in this group
    all_part_numbers: dict[str, str]  # {normalized_pn: source}
    vendors: dict[str, dict]  # {vendor_code: {part_number, cost, stock}}
    preferred_vendor: str  # Which vendor we'll order from
    created_at: str = ""
    updated_at: str = ""


@dataclass
class DuplicateCandidate:
    """A potential duplicate detected during import/scrape."""
    existing_product_id: int
    existing_sku: str
    existing_title: str
    new_part_numbers: list[str]  # Numbers from the new product
    matching_numbers: list[str]  # Numbers that matched
    match_type: str  # 'exact', 'cross_ref', 'similar'
    confidence: float
    recommendation: str  # 'link', 'merge', 'separate', 'review'
    # NEW: Reason for the recommendation
    reason: str = ""


def should_link_products(
    new_product: dict,
    existing_product: dict,
    match_confidence: float,
) -> tuple[bool, str]:
    """Determine if two products should be linked (same physical item) or kept separate.
    
    Default behavior: LINK products unless there's a clear reason to separate.
    
    Args:
        new_product: Dict with 'category', 'engine', 'title', etc.
        existing_product: Same structure for existing product
        match_confidence: How confident we are they share a part number
        
    Returns:
        (should_link, reason)
    """
    new_cat = (new_product.get('category') or '').lower().strip()
    existing_cat = (existing_product.get('category') or '').lower().strip()
    new_engine = (new_product.get('engine') or '').lower().strip()
    existing_engine = (existing_product.get('engine') or '').lower().strip()
    new_title = (new_product.get('title') or '').lower()
    existing_title = (existing_product.get('title') or '').lower()
    
    # SEPARATE: Different categories = definitely different products
    if new_cat and existing_cat and new_cat != existing_cat:
        return (False, f"Different categories: {new_cat} vs {existing_cat}")
    
    # SEPARATE: Different engine families (ISX vs C15 vs N14)
    if new_engine and existing_engine:
        # Extract engine family
        new_family = _extract_engine_family(new_engine)
        existing_family = _extract_engine_family(existing_engine)
        if new_family and existing_family and new_family != existing_family:
            return (False, f"Different engine families: {new_family} vs {existing_family}")
    
    # SEPARATE: Kit vs individual component
    new_is_kit = 'kit' in new_title or 'set' in new_title
    existing_is_kit = 'kit' in existing_title or 'set' in existing_title
    if new_is_kit != existing_is_kit:
        return (False, "One is a kit, other is individual component")
    
    # LINK: High confidence part number match
    if match_confidence >= 0.9:
        return (True, "High confidence part number match - same physical product")
    
    # LINK: Same category + same engine = very likely same
    if new_cat == existing_cat and new_engine == existing_engine:
        return (True, "Same category and engine - likely same product, different vendor")
    
    # LINK: Cross-reference confirmed
    if match_confidence >= 0.7:
        return (True, "Cross-reference indicates same product")
    
    # DEFAULT: Link unless explicitly different
    return (True, "No clear difference detected - linking as same product")


def _extract_engine_family(engine: str) -> str:
    """Extract the engine family from an engine string.
    
    Examples:
        'ISX15' → 'ISX'
        'ISX 15' → 'ISX'
        'Cummins ISX15 Signature' → 'ISX'
        'C15 ACERT' → 'C15'
        'CAT C15' → 'C15'
        'N14' → 'N14'
    """
    engine = engine.upper()
    
    # Common engine families
    families = ['ISX', 'ISB', 'ISC', 'ISL', 'ISM', 'N14', 'M11', 'L10',
                'C15', 'C13', 'C12', 'C11', 'C10', 'C9', 'C7', '3406',
                'DD15', 'DD13', 'DD16', 'S60', 'MBE4000', 'MX13', 'D13']
    
    for family in families:
        if family in engine:
            return family
    
    return ""


class ProductGroupingService:
    """Service for managing product groups and detecting duplicates."""
    
    def __init__(self, db_connection=None):
        self.conn = db_connection
        self._part_number_cache: dict[str, list[int]] = {}  # normalized_pn → product_ids
        
    def _normalize(self, pn: str) -> str:
        return normalize_part_number(pn)
    
    def _execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute SQL query."""
        if self.conn is None:
            raise RuntimeError("No database connection")
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor
    
    # =========================================================================
    # Part Number Index
    # =========================================================================
    
    def build_part_number_index(self) -> dict[str, list[int]]:
        """Build an in-memory index of all part numbers → product IDs.
        
        This is the foundation for fast duplicate detection.
        """
        logger.info("Building part number index...")
        index: dict[str, set[int]] = {}
        
        # Get all part numbers from the part_numbers table
        cursor = self._execute("""
            SELECT product_id, number_normalized, type
            FROM part_numbers
        """)
        
        for row in cursor.fetchall():
            product_id, normalized, pn_type = row
            if normalized:
                norm = self._normalize(normalized)
                if norm not in index:
                    index[norm] = set()
                index[norm].add(product_id)
        
        # Also index from products table directly (legacy)
        cursor = self._execute("""
            SELECT id, sku, pai_sku
            FROM products
        """)
        
        for row in cursor.fetchall():
            prod_id, sku, pai_sku = row
            # Extract part from JAKS-XXXXX SKU
            if sku and sku.startswith("JAKS-"):
                base = sku[5:]  # Remove JAKS-
                norm = self._normalize(base)
                if norm:
                    if norm not in index:
                        index[norm] = set()
                    index[norm].add(prod_id)
            
            if pai_sku:
                norm = self._normalize(pai_sku)
                if norm:
                    if norm not in index:
                        index[norm] = set()
                    index[norm].add(prod_id)
        
        # Convert sets to lists
        self._part_number_cache = {k: list(v) for k, v in index.items()}
        logger.info(f"Indexed {len(self._part_number_cache)} unique part numbers")
        return self._part_number_cache
    
    # =========================================================================
    # Duplicate Detection
    # =========================================================================
    
    def find_duplicates_for_part_numbers(
        self,
        part_numbers: list[str],
        *,
        exclude_product_id: int | None = None,
    ) -> list[DuplicateCandidate]:
        """Find existing products that match any of the given part numbers.
        
        Args:
            part_numbers: List of part numbers to check (OEM, cross-refs, etc.)
            exclude_product_id: Don't match against this product (for updates)
            
        Returns:
            List of DuplicateCandidate objects with match info
        """
        if not self._part_number_cache:
            self.build_part_number_index()
        
        candidates: dict[int, DuplicateCandidate] = {}
        
        for pn in part_numbers:
            if not pn:
                continue
            
            normalized = self._normalize(pn)
            if not normalized:
                continue
            
            # Check exact normalized match
            if normalized in self._part_number_cache:
                for prod_id in self._part_number_cache[normalized]:
                    if exclude_product_id and prod_id == exclude_product_id:
                        continue
                    
                    if prod_id not in candidates:
                        # Fetch product info
                        cursor = self._execute(
                            "SELECT sku, title FROM products WHERE id = ?",
                            (prod_id,)
                        )
                        row = cursor.fetchone()
                        if row:
                            candidates[prod_id] = DuplicateCandidate(
                                existing_product_id=prod_id,
                                existing_sku=row[0] or "",
                                existing_title=row[1] or "",
                                new_part_numbers=part_numbers,
                                matching_numbers=[pn],
                                match_type="exact",
                                confidence=1.0,
                                recommendation="merge",
                            )
                    else:
                        # Add to matching numbers
                        if pn not in candidates[prod_id].matching_numbers:
                            candidates[prod_id].matching_numbers.append(pn)
            
            # Check base part number (without size suffixes)
            base = extract_base_part_number(normalized)
            if base != normalized and base in self._part_number_cache:
                for prod_id in self._part_number_cache[base]:
                    if exclude_product_id and prod_id == exclude_product_id:
                        continue
                    
                    if prod_id not in candidates:
                        cursor = self._execute(
                            "SELECT sku, title FROM products WHERE id = ?",
                            (prod_id,)
                        )
                        row = cursor.fetchone()
                        if row:
                            candidates[prod_id] = DuplicateCandidate(
                                existing_product_id=prod_id,
                                existing_sku=row[0] or "",
                                existing_title=row[1] or "",
                                new_part_numbers=part_numbers,
                                matching_numbers=[pn],
                                match_type="base",
                                confidence=0.8,
                                recommendation="review",
                            )
        
        return list(candidates.values())
    
    def check_for_duplicate_before_insert(
        self,
        product_data: dict,
    ) -> DuplicateCandidate | None:
        """Check if a product should be inserted or linked with existing.
        
        Call this BEFORE creating a new product to avoid duplicates.
        Default behavior: Recommend LINKING if match found (same physical product,
        just add the new part number/vendor to the existing product).
        
        IMPORTANT: Uses manufacturer identification to determine if products from
        different resellers (HHP, ATL) are actually the same underlying manufacturer
        part (PAI, McBee, etc.).
        
        Args:
            product_data: Dict with keys like 'primary_pn', 'oem_pn', 'xrefs',
                         'category', 'engine', 'title', 'source_vendor' etc.
            
        Returns:
            DuplicateCandidate if a match found, None if safe to insert
        """
        # Collect all part numbers from the product data
        part_numbers = []
        
        for key in ('primary_pn', 'primary_part_number', 'oem_pn', 'oem_part_number',
                    'secondary_pn', 'secondary_part_number', 'pai_sku', 'supplier_part_number'):
            val = product_data.get(key)
            if val:
                part_numbers.append(str(val))
        
        # Add cross-references
        xrefs = product_data.get('xrefs') or []
        if isinstance(xrefs, list):
            part_numbers.extend([str(x) for x in xrefs if x])
        
        if not part_numbers:
            return None
        
        # NEW: Identify the manufacturer from part numbers
        # This is crucial for recognizing that HHP selling "ISX119-145" is actually a PAI part
        try:
            from engine.part_number_patterns import identify_manufacturer, is_same_manufacturer_part
            
            for pn in part_numbers:
                mfr_match = identify_manufacturer(pn)
                if mfr_match and mfr_match.confidence >= 0.7:
                    product_data['_detected_manufacturer'] = mfr_match.manufacturer
                    product_data['_detected_mfr_code'] = mfr_match.manufacturer_code
                    logger.debug(f"Identified manufacturer for {pn}: {mfr_match.manufacturer} ({mfr_match.confidence:.0%})")
                    break
        except ImportError:
            pass  # Pattern recognition not available
        
        duplicates = self.find_duplicates_for_part_numbers(part_numbers)
        
        if duplicates:
            # Return the best match (highest confidence)
            duplicates.sort(key=lambda d: d.confidence, reverse=True)
            best = duplicates[0]
            
            # Fetch existing product details for comparison
            cursor = self._execute(
                "SELECT category, engine, supplier FROM products WHERE id = ?",
                (best.existing_product_id,)
            )
            row = cursor.fetchone()
            existing_data = {
                'category': row[0] if row else '',
                'engine': row[1] if row else '',
                'supplier': row[2] if row else '',
                'title': best.existing_title,
            }
            
            # NEW: Check if both products are from resellers selling the same manufacturer's part
            source_vendor = (product_data.get('source_vendor') or '').upper()
            existing_supplier = (existing_data.get('supplier') or '').upper()
            
            # If source is a reseller (HHP, ATL), boost confidence that this is a duplicate
            resellers = {'HHP', 'ATL', 'DPD', 'TTP'}
            if source_vendor in resellers or existing_supplier in resellers:
                # Both are likely reselling the same manufacturer's part
                best.confidence = min(1.0, best.confidence + 0.1)
                best.reason = f"Both from resellers ({source_vendor}, {existing_supplier}) - likely same manufacturer part"
            
            # Determine if we should link or separate
            should_link, reason = should_link_products(
                new_product=product_data,
                existing_product=existing_data,
                match_confidence=best.confidence,
            )
            
            # Update recommendation based on analysis
            if should_link:
                best.recommendation = "link"
                best.reason = reason
            else:
                best.recommendation = "separate"
                best.reason = reason
            
            return best
        
        return None
    
    # =========================================================================
    # Product Groups
    # =========================================================================
    
    def link_part_number_to_product(
        self,
        product_id: int,
        part_number: str,
        pn_type: str = "cross_ref",
        vendor: str = "",
        vendor_cost: float | None = None,
        source: str = "linked",
    ) -> bool:
        """Link a new part number to an existing product.
        
        This is the primary action when we detect a duplicate - instead of
        creating a new product, we add the new part number to the existing one.
        
        Args:
            product_id: ID of existing product to link to
            part_number: The new part number to add
            pn_type: Type of part number ('cross_ref', 'vendor', 'oem', 'alternate')
            vendor: Vendor code if this is a vendor part number
            vendor_cost: Vendor's cost for this part
            source: Where this link came from ('pai', 'csv_import', 'manual', etc.)
            
        Returns:
            True if link was added
        """
        normalized = self._normalize(part_number)
        if not normalized:
            return False
        
        # Check if already linked
        cursor = self._execute("""
            SELECT id FROM part_numbers
            WHERE product_id = ? AND number_normalized = ?
        """, (product_id, normalized))
        
        if cursor.fetchone():
            logger.debug(f"Part number {part_number} already linked to product {product_id}")
            return True  # Already linked
        
        # Add the part number
        try:
            self._execute("""
                INSERT INTO part_numbers
                (product_id, number, number_normalized, type, manufacturer, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (product_id, part_number, normalized, pn_type, vendor, source))
            
            # If vendor pricing provided, update vendor_pricing JSON on product
            if vendor and vendor_cost:
                cursor = self._execute(
                    "SELECT vendor_pricing FROM products WHERE id = ?",
                    (product_id,)
                )
                row = cursor.fetchone()
                import json
                pricing = {}
                if row and row[0]:
                    try:
                        pricing = json.loads(row[0])
                    except:
                        pass
                
                pricing[vendor] = {
                    "part_number": part_number,
                    "cost": vendor_cost,
                    "updated": "now",
                }
                
                self._execute(
                    "UPDATE products SET vendor_pricing = ? WHERE id = ?",
                    (json.dumps(pricing), product_id)
                )
            
            self.conn.commit()
            
            # Update cache
            if normalized not in self._part_number_cache:
                self._part_number_cache[normalized] = []
            if product_id not in self._part_number_cache[normalized]:
                self._part_number_cache[normalized].append(product_id)
            
            logger.info(f"Linked part number {part_number} to product {product_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to link part number: {e}")
            return False
    
    def link_product_from_duplicate(
        self,
        duplicate: DuplicateCandidate,
        new_product_data: dict,
        source: str = "duplicate_detection",
    ) -> bool:
        """Link a new product's data to an existing product (instead of creating new).
        
        Use this when check_for_duplicate_before_insert returns a match with
        recommendation='link'.
        
        Args:
            duplicate: The DuplicateCandidate from the check
            new_product_data: Dict with 'primary_pn', 'oem_pn', 'xrefs', 'vendor', 'cost'
            source: Where this came from
            
        Returns:
            True if linking successful
        """
        product_id = duplicate.existing_product_id
        vendor = new_product_data.get('vendor') or new_product_data.get('source_vendor') or ''
        cost = new_product_data.get('cost') or new_product_data.get('vendor_cost')
        
        linked_any = False
        
        # Link all part numbers from the new product
        for key in ('primary_pn', 'primary_part_number', 'oem_pn', 'oem_part_number',
                    'secondary_pn', 'secondary_part_number', 'pai_sku', 'supplier_part_number'):
            pn = new_product_data.get(key)
            if pn:
                pn_type = 'oem' if 'oem' in key else 'vendor' if vendor else 'cross_ref'
                if self.link_part_number_to_product(
                    product_id, str(pn), pn_type, vendor, cost if vendor else None, source
                ):
                    linked_any = True
        
        # Link cross-references
        xrefs = new_product_data.get('xrefs') or []
        for xref in xrefs:
            if xref:
                if self.link_part_number_to_product(
                    product_id, str(xref), 'cross_ref', '', None, source
                ):
                    linked_any = True
        
        return linked_any

    def create_product_group(
        self,
        product_ids: list[int],
        master_id: int | None = None,
        preferred_vendor: str = "",
    ) -> ProductGroup:
        """Create a product group from multiple product IDs.
        
        Args:
            product_ids: IDs of products to group together
            master_id: Which product should be the "main" one (first by default)
            preferred_vendor: Which vendor to use for ordering
            
        Returns:
            ProductGroup with all info consolidated
        """
        if not product_ids:
            raise ValueError("Cannot create group with no products")
        
        master_id = master_id or product_ids[0]
        if master_id not in product_ids:
            product_ids.insert(0, master_id)
        
        # Fetch all product data
        placeholders = ",".join("?" * len(product_ids))
        cursor = self._execute(f"""
            SELECT id, sku, title, supplier, cost, pai_sku, pai_cost
            FROM products
            WHERE id IN ({placeholders})
        """, tuple(product_ids))
        
        all_numbers: dict[str, str] = {}
        vendors: dict[str, dict] = {}
        master_sku = ""
        
        for row in cursor.fetchall():
            prod_id, sku, title, supplier, cost, pai_sku, pai_cost = row
            
            # Collect part numbers
            if sku and sku.startswith("JAKS-"):
                base = sku[5:]
                norm = self._normalize(base)
                if norm:
                    all_numbers[norm] = f"sku:{prod_id}"
            
            if pai_sku:
                norm = self._normalize(pai_sku)
                if norm:
                    all_numbers[norm] = f"pai:{prod_id}"
            
            # Track vendors
            vendor = supplier or "HHP"
            if vendor not in vendors:
                vendors[vendor] = {
                    "product_id": prod_id,
                    "cost": cost or pai_cost or 0,
                }
            
            if prod_id == master_id:
                master_sku = sku
        
        # Fetch additional part numbers from part_numbers table
        cursor = self._execute(f"""
            SELECT product_id, number_normalized, type, manufacturer
            FROM part_numbers
            WHERE product_id IN ({placeholders})
        """, tuple(product_ids))
        
        for row in cursor.fetchall():
            prod_id, normalized, pn_type, manufacturer = row
            if normalized:
                source = f"{pn_type}:{manufacturer}" if manufacturer else pn_type
                all_numbers[self._normalize(normalized)] = source
        
        # Generate group ID
        sorted_numbers = sorted(all_numbers.keys())
        group_hash = hashlib.md5("|".join(sorted_numbers).encode()).hexdigest()[:8]
        group_id = f"GRP-{group_hash}"
        
        # Determine preferred vendor if not specified
        if not preferred_vendor and vendors:
            # Choose vendor with lowest cost
            best = min(vendors.items(), key=lambda kv: kv[1].get("cost") or float("inf"))
            preferred_vendor = best[0]
        
        return ProductGroup(
            group_id=group_id,
            master_product_id=master_id,
            master_sku=master_sku,
            member_product_ids=product_ids,
            all_part_numbers=all_numbers,
            vendors=vendors,
            preferred_vendor=preferred_vendor,
        )
    
    def merge_products_into_master(
        self,
        master_id: int,
        other_ids: list[int],
        *,
        keep_best_data: bool = True,
    ) -> bool:
        """Merge multiple products into a single master product.
        
        Args:
            master_id: The product to keep
            other_ids: Products to merge into master (will be marked inactive)
            keep_best_data: If True, copy better data from others to master
            
        Returns:
            True if merge successful
        """
        if master_id in other_ids:
            other_ids.remove(master_id)
        
        if not other_ids:
            return True
        
        logger.info(f"Merging products {other_ids} into master {master_id}")
        
        # Copy part numbers to master
        for other_id in other_ids:
            cursor = self._execute("""
                SELECT number, number_normalized, type, manufacturer, source
                FROM part_numbers
                WHERE product_id = ?
            """, (other_id,))
            
            for row in cursor.fetchall():
                number, normalized, pn_type, manufacturer, source = row
                try:
                    self._execute("""
                        INSERT OR IGNORE INTO part_numbers
                        (product_id, number, number_normalized, type, manufacturer, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (master_id, number, normalized, pn_type, manufacturer, f"merged:{other_id}"))
                except Exception as e:
                    logger.debug(f"Part number already exists: {e}")
        
        if keep_best_data:
            # Fields where "missing" means NULL or empty string.
            text_fields = [
                "title", "category", "manufacturer", "supplier", "engine",
                "condition", "description_html", "kit_contents", "url",
                "handle", "pai_sku", "pai_status", "pai_stock", "pai_link",
            ]
            # Fields where "missing" means NULL or 0.
            numeric_fields = [
                "cost", "pai_cost", "selling_price", "compare_at_price",
                "core_charge", "image_count",
            ]

            def _row_to_dict(cursor, row):
                if row is None:
                    return {}
                cols = [d[0] for d in cursor.description]
                return dict(zip(cols, row))

            cursor = self._execute("SELECT * FROM products WHERE id = ?", (master_id,))
            master = _row_to_dict(cursor, cursor.fetchone())

            for other_id in other_ids:
                cursor = self._execute("SELECT * FROM products WHERE id = ?", (other_id,))
                other = _row_to_dict(cursor, cursor.fetchone())
                if not other:
                    continue

                updates: dict = {}
                for field in text_fields:
                    if not master.get(field) and other.get(field):
                        updates[field] = other[field]
                for field in numeric_fields:
                    m_val = master.get(field) or 0
                    o_val = other.get(field) or 0
                    if not m_val and o_val:
                        updates[field] = o_val

                if updates:
                    set_clause = ", ".join(f"{k} = ?" for k in updates)
                    self._execute(
                        f"UPDATE products SET {set_clause} WHERE id = ?",
                        (*updates.values(), master_id),
                    )
                    # Reflect the new master state for subsequent iterations.
                    master.update(updates)
        
        # Mark other products as merged (don't delete - keep for audit)
        for other_id in other_ids:
            self._execute("""
                UPDATE products 
                SET stage = -1,  -- Inactive/merged
                    sku = sku || '-MERGED-' || ?
                WHERE id = ?
            """, (master_id, other_id))
        
        self.conn.commit()
        return True
    
    # =========================================================================
    # AI-Assisted Matching
    # =========================================================================
    
    def find_similar_by_title(
        self,
        title: str,
        category: str = "",
        threshold: float = 0.8,
    ) -> list[tuple[int, str, float]]:
        """Find products with similar titles using fuzzy matching.
        
        This catches duplicates that have different part numbers but 
        are clearly the same product based on title.
        
        Args:
            title: Product title to match
            category: Limit to same category if provided
            threshold: Minimum similarity score (0-1)
            
        Returns:
            List of (product_id, title, similarity_score)
        """
        # Normalize title for comparison
        def normalize_title(t: str) -> str:
            t = t.lower()
            # Remove common filler words
            for word in ("for", "the", "new", "oem", "aftermarket", "reman", "remanufactured"):
                t = t.replace(word, " ")
            # Remove extra spaces
            t = re.sub(r"\s+", " ", t).strip()
            return t
        
        normalized = normalize_title(title)
        
        # Get potential matches
        sql = "SELECT id, title FROM products WHERE stage >= 0"
        params = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        
        cursor = self._execute(sql, tuple(params))
        
        matches = []
        for row in cursor.fetchall():
            prod_id, prod_title = row
            if not prod_title:
                continue
            
            prod_normalized = normalize_title(prod_title)
            
            # Simple token-based similarity
            tokens1 = set(normalized.split())
            tokens2 = set(prod_normalized.split())
            
            if not tokens1 or not tokens2:
                continue
            
            intersection = tokens1 & tokens2
            union = tokens1 | tokens2
            similarity = len(intersection) / len(union) if union else 0
            
            if similarity >= threshold:
                matches.append((prod_id, prod_title, similarity))
        
        matches.sort(key=lambda x: x[2], reverse=True)
        return matches[:20]  # Top 20
    
    def suggest_product_groups(
        self,
        min_confidence: float = 0.7,
    ) -> list[list[int]]:
        """Analyze entire catalog and suggest product groupings.
        
        Uses multiple signals:
        1. Shared part numbers (direct match)
        2. Cross-reference links
        3. Similar titles + same category
        
        Returns:
            List of product ID groups that should be merged
        """
        logger.info("Analyzing catalog for product groups...")
        
        if not self._part_number_cache:
            self.build_part_number_index()
        
        # Find products that share part numbers
        groups_by_number: dict[str, set[int]] = {}
        
        for normalized, product_ids in self._part_number_cache.items():
            if len(product_ids) > 1:
                key = tuple(sorted(product_ids))
                if key not in groups_by_number:
                    groups_by_number[tuple(sorted(product_ids))] = set(product_ids)
        
        # Convert to list of groups
        suggested_groups = [list(g) for g in groups_by_number.values() if len(g) > 1]
        
        logger.info(f"Found {len(suggested_groups)} potential product groups")
        return suggested_groups


# =============================================================================
# Convenience Functions
# =============================================================================

def detect_duplicate_on_scrape(
    product_data: dict,
    db_connection=None,
) -> DuplicateCandidate | None:
    """Quick check for duplicates during scraping.
    
    Call this after scraping a product but before saving to detect
    if it's a duplicate of an existing product.
    
    Example:
        product = scrape_product(url)
        duplicate = detect_duplicate_on_scrape({
            'primary_pn': product.primary_pn,
            'oem_pn': product.oem_part_number,
            'xrefs': product.xrefs,
            'pai_sku': product.pai_sku,
            'category': product.category,
            'engine': product.engine,
            'title': product.title,
        })
        
        if duplicate:
            if duplicate.recommendation == 'link':
                # Default: link to existing product
                link_duplicate(duplicate, product_data, db_connection)
            else:
                # Show UI for user decision
                # Options: link anyway, skip, create separate
    """
    service = ProductGroupingService(db_connection)
    return service.check_for_duplicate_before_insert(product_data)


def link_duplicate(
    duplicate: DuplicateCandidate,
    new_product_data: dict,
    db_connection=None,
    source: str = "duplicate_detection",
) -> bool:
    """Link a detected duplicate to the existing product.
    
    This is the DEFAULT action when a duplicate is detected.
    Adds all part numbers from the new product to the existing one.
    
    Args:
        duplicate: The DuplicateCandidate from detect_duplicate_on_scrape
        new_product_data: Dict with the new product's data
        db_connection: Database connection
        source: Where this came from
        
    Returns:
        True if link was successful
    """
    service = ProductGroupingService(db_connection)
    return service.link_product_from_duplicate(duplicate, new_product_data, source)


def get_all_part_numbers_for_product(
    product_id: int,
    db_connection=None,
) -> dict[str, list[str]]:
    """Get all part numbers for a product, grouped by type.
    
    Returns:
        {
            'oem': ['5473069'],
            'vendor': ['ISX119-145', 'G2-001'],
            'cross_ref': ['ABC123', 'XYZ789'],
        }
    """
    service = ProductGroupingService(db_connection)
    cursor = service._execute("""
        SELECT number, type, manufacturer
        FROM part_numbers
        WHERE product_id = ?
        ORDER BY type, manufacturer
    """, (product_id,))
    
    result: dict[str, list[str]] = {}
    for row in cursor.fetchall():
        number, pn_type, manufacturer = row
        pn_type = pn_type or "other"
        if pn_type not in result:
            result[pn_type] = []
        result[pn_type].append(number)
    
    return result


# =============================================================================
# AI-Assisted Duplicate Detection using Grok
# =============================================================================

def ai_confirm_duplicate(
    new_product: dict,
    candidate: dict,
    api_key: str | None = None,
) -> tuple[bool, float, str]:
    """Use Grok AI to confirm if two products are actually the same.
    
    This is the last layer of duplicate detection for cases where:
    - Part numbers don't match
    - Titles are similar but not identical
    - Category/engine matches but we're not 100% sure
    
    Args:
        new_product: Dict with 'title', 'category', 'engine', 'part_numbers'
        candidate: Existing product with same fields
        api_key: XAI_API_KEY (defaults to env var)
        
    Returns:
        (is_same_product, confidence, reasoning)
    """
    import os
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("OpenAI package not installed, skipping AI duplicate check")
        return (False, 0.0, "OpenAI package not installed")
    
    api_key = api_key or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.debug("No XAI_API_KEY, skipping AI duplicate check")
        return (False, 0.0, "No API key configured")
    
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1",
    )
    
    system = (
        "You are an expert diesel parts specialist. Your job is to determine if two "
        "product listings are for the same physical part or different parts. "
        "Be aware that the same part may have different part numbers from different "
        "vendors (OEM vs aftermarket). Respond with JSON only."
    )
    
    prompt = f"""
Are these two diesel engine parts the same physical product?

PRODUCT A (New):
- Title: {new_product.get('title', 'Unknown')}
- Part Numbers: {new_product.get('part_numbers', [])}
- Category: {new_product.get('category', 'Unknown')}
- Engine: {new_product.get('engine', 'Unknown')}

PRODUCT B (Existing):
- Title: {candidate.get('title', 'Unknown')}
- Part Numbers: {candidate.get('part_numbers', [])}
- Category: {candidate.get('category', 'Unknown')}
- Engine: {candidate.get('engine', 'Unknown')}

Consider:
1. OEM numbers like '5473069' may cross to PAI numbers like 'ISX119-145'
2. Same part type + same engine = very likely same if titles match
3. Different categories = definitely different parts
4. Kit vs individual component = different products

Respond with this exact JSON format:
{{
    "is_same_product": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}
"""
    
    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()
        
        # Parse JSON response
        import json
        # Clean up potential markdown formatting
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()
        
        result = json.loads(content)
        return (
            result.get("is_same_product", False),
            result.get("confidence", 0.0),
            result.get("reasoning", "No reasoning provided"),
        )
        
    except Exception as e:
        logger.warning(f"AI duplicate check failed: {e}")
        return (False, 0.0, f"AI check failed: {e}")


async def ai_batch_detect_duplicates(
    new_products: list[dict],
    candidates: list[dict],
    api_key: str | None = None,
) -> list[tuple[int, int, float, str]]:
    """Batch check multiple products for duplicates.
    
    More efficient than calling ai_confirm_duplicate for each pair.
    
    Args:
        new_products: List of product dicts with 'title', 'category', etc.
        candidates: Existing products to check against
        api_key: XAI_API_KEY
        
    Returns:
        List of (new_idx, candidate_idx, confidence, reasoning) for matches
    """
    import os
    try:
        from openai import OpenAI
    except ImportError:
        return []
    
    api_key = api_key or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []
    
    # Build comparison pairs for products in same category
    pairs_to_check = []
    for i, new_prod in enumerate(new_products):
        new_cat = new_prod.get("category", "").lower()
        for j, cand in enumerate(candidates):
            cand_cat = cand.get("category", "").lower()
            if new_cat == cand_cat:
                pairs_to_check.append((i, j, new_prod, cand))
    
    if not pairs_to_check:
        return []
    
    # Limit to reasonable batch size
    pairs_to_check = pairs_to_check[:50]
    
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1",
    )
    
    # Build batch prompt
    pairs_text = "\n".join([
        f"Pair {idx}: A='{p[2].get('title', '')}' vs B='{p[3].get('title', '')}'"
        for idx, p in enumerate(pairs_to_check)
    ])
    
    prompt = f"""
Check if any of these diesel part pairs are the same physical product:

{pairs_text}

For each pair that IS the same product (confidence > 0.7), respond with:
{{"pair": pair_number, "confidence": 0.0-1.0, "reason": "brief"}}

Respond with a JSON array. If no matches, respond with [].
"""
    
    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        content = response.choices[0].message.content.strip()
        
        import json
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        
        matches = json.loads(content)
        
        results = []
        for match in matches:
            pair_idx = match.get("pair", -1)
            if 0 <= pair_idx < len(pairs_to_check):
                i, j, _, _ = pairs_to_check[pair_idx]
                results.append((
                    i, j,
                    match.get("confidence", 0.0),
                    match.get("reason", ""),
                ))
        
        return results
        
    except Exception as e:
        logger.warning(f"Batch AI duplicate check failed: {e}")
        return []
