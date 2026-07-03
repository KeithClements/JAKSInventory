"""Vendor Service for JAKS Scraper.

Handles vendor CRUD operations, import templates, and default vendor setup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("db.vendor_service")


@dataclass
class Vendor:
    """Represents a vendor/supplier.
    
    IMPORTANT: Distinguish between:
    - manufacturer: Makes the parts (PAI, Interstate McBee, AFA, OEM Cummins)
    - reseller: Sells other manufacturers' parts (HHP, ATL Diesel, us)
    
    When we scrape HHP, we need to identify the MANUFACTURER from the part number,
    not assume HHP made it.
    """
    id: int | None = None
    code: str = ""           # Short code like "PAI", "G2", "ATL"
    name: str = ""           # Full name like "PAI Industries"
    vendor_type: str = "supplier"  # 'manufacturer', 'reseller', 'oem', 'competitor'
    is_manufacturer: bool = False  # True = makes parts, False = resells
    default_markup: float = 25.0   # Default markup percentage
    import_template: dict = field(default_factory=dict)  # Column mappings
    enrichment_enabled: bool = True
    portal_url: str = ""
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_row(cls, row: tuple) -> "Vendor":
        """Create from database row."""
        return cls(
            id=row[0],
            code=row[1] or "",
            name=row[2] or "",
            vendor_type=row[3] or "supplier",
            default_markup=row[4] or 25.0,
            import_template=json.loads(row[5]) if row[5] else {},
            enrichment_enabled=bool(row[6]) if len(row) > 6 else True,
            portal_url=row[7] if len(row) > 7 else "",
            notes=row[8] if len(row) > 8 else "",
            created_at=row[9] if len(row) > 9 else "",
            updated_at=row[10] if len(row) > 10 else "",
            # Infer is_manufacturer from vendor_type
            is_manufacturer=(row[3] or "").lower() in ("manufacturer", "oem", "supplier"),
        )


@dataclass
class ImportTemplate:
    """Template for importing vendor CSV files."""
    name: str
    vendor_code: str
    column_mapping: dict[str, str]  # CSV column → Product field
    default_category: str = ""
    default_engine: str = ""
    skip_rows: int = 0
    notes: str = ""


# Default column patterns for auto-detection
COLUMN_PATTERNS = {
    "vendor_part_number": ["part_num", "part#", "sku", "partno", "part_number", "vendor_part", "item"],
    "title": ["description", "title", "name", "desc", "product_name", "item_desc"],
    "cost": ["price", "cost", "unit_price", "unit_cost", "list_price", "your_price"],
    "oem_part_number": ["oem", "oem_num", "oem_part", "oem_number", "replaces", "replaces_oem"],
    "weight": ["weight", "wt", "lbs", "weight_lb", "weight_lbs"],
    "dimensions": ["dimensions", "dim", "size", "l_w_h", "lwh"],
    "category": ["category", "cat", "type", "product_type", "class"],
    "engine": ["engine", "engine_model", "application", "fits"],
    "brand": ["brand", "mfr", "manufacturer", "make"],
    "cross_ref_1": ["cross_ref", "xref", "alt_part", "cross_ref_1", "xref1", "alternate"],
    "cross_ref_2": ["cross_ref_2", "xref2", "alt_part_2"],
    "cross_ref_3": ["cross_ref_3", "xref3", "alt_part_3"],
    "cpl": ["cpl", "cpl_list", "cpl_code"],
    "esn": ["esn", "serial", "esn_list"],
    "quantity": ["qty", "quantity", "stock", "on_hand", "available"],
}


# Default vendors to create
# CRITICAL: Distinguish MANUFACTURERS (make parts) from RESELLERS (sell others' parts)
DEFAULT_VENDORS = [
    # === MANUFACTURERS (actually make the parts) ===
    Vendor(
        code="PAI",
        name="PAI Industries",
        vendor_type="manufacturer",
        is_manufacturer=True,
        default_markup=25.0,
        enrichment_enabled=True,
        portal_url="https://www.pai-intl.com",
        notes="Aftermarket manufacturer. Part numbers: ISX*, C15*, N14*, etc.",
        import_template={
            "PAI_Part": "vendor_part_number",
            "Description": "title",
            "Replaces_OEM": "oem_part_number",
            "List_Price": "cost",
            "Weight": "weight",
            "Dimensions": "dimensions",
        },
    ),
    Vendor(
        code="MCB",
        name="Interstate McBee",
        vendor_type="manufacturer",
        is_manufacturer=True,
        default_markup=25.0,
        enrichment_enabled=True,
        portal_url="https://www.mcbee.com",
        notes="Aftermarket manufacturer. Part numbers: MCB-*, IM-*",
    ),
    Vendor(
        code="AFA",
        name="AFA Industries",
        vendor_type="manufacturer",
        is_manufacturer=True,
        default_markup=25.0,
        enrichment_enabled=False,
        notes="Aftermarket manufacturer. Part numbers: AFA-*",
    ),
    Vendor(
        code="IPD",
        name="IPD",
        vendor_type="manufacturer",
        is_manufacturer=True,
        default_markup=25.0,
        enrichment_enabled=False,
        notes="Aftermarket manufacturer. Part numbers: IPD-*",
    ),
    Vendor(
        code="CUM",
        name="OEM Cummins",
        vendor_type="oem",
        is_manufacturer=True,
        default_markup=0.0,
        enrichment_enabled=False,
        notes="OEM parts. Part numbers: 7-digit numeric (5579308)",
    ),
    Vendor(
        code="CAT",
        name="OEM Caterpillar",
        vendor_type="oem",
        is_manufacturer=True,
        default_markup=0.0,
        enrichment_enabled=False,
        notes="OEM parts. Part numbers: 10R-*, 3E-*, OR-*",
    ),
    
    # === RESELLERS (sell other manufacturers' parts) ===
    Vendor(
        code="HHP",
        name="Highway and Heavy Parts",
        vendor_type="reseller",
        is_manufacturer=False,
        default_markup=0.0,
        enrichment_enabled=True,
        portal_url="https://www.highwayandheavyparts.com",
        notes="RESELLER - sells PAI, McBee, AFA parts. Identify manufacturer from part number!",
    ),
    Vendor(
        code="ATL",
        name="ATL Diesel",
        vendor_type="reseller",
        is_manufacturer=False,
        default_markup=0.0,
        enrichment_enabled=True,
        portal_url="https://www.atldiesel.com",
        notes="RESELLER - sells PAI, McBee, AFA parts. Identify manufacturer from part number!",
    ),
    Vendor(
        code="G2",
        name="G2 Injectors",
        vendor_type="supplier",
        is_manufacturer=False,  # They're also a reseller/rebuilder
        default_markup=30.0,
        enrichment_enabled=True,
        notes="Injector specialist - rebuilds and resells",
        import_template={
            "Part_Number": "vendor_part_number",
            "Description": "title",
            "OEM_Number": "oem_part_number",
            "Engine": "engine",
            "Cost": "cost",
            "Weight_lb": "weight",
            "Cross_Ref_1": "cross_ref_1",
            "Cross_Ref_2": "cross_ref_2",
        },
    ),
]


class VendorService:
    """Service for vendor management."""
    
    def __init__(self, db_connection=None):
        self.conn = db_connection
        
    def _execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute SQL query."""
        if self.conn is None:
            raise RuntimeError("No database connection")
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor
        
    def ensure_table(self):
        """Ensure vendors table exists with all columns."""
        self._execute("""
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                vendor_type TEXT DEFAULT 'supplier',
                default_markup REAL DEFAULT 25.0,
                import_template TEXT,
                enrichment_enabled BOOLEAN DEFAULT 1,
                portal_url TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
        
    def ensure_defaults(self):
        """Ensure default vendors exist."""
        self.ensure_table()
        for vendor in DEFAULT_VENDORS:
            existing = self.get_by_code(vendor.code)
            if not existing:
                self.create(vendor)
                logger.info(f"Created default vendor: {vendor.code}")
                
    # =========================================================================
    # CRUD Operations
    # =========================================================================
    
    def create(self, vendor: Vendor) -> int:
        """Create a new vendor."""
        now = datetime.now().isoformat()
        cursor = self._execute("""
            INSERT INTO vendors (
                code, name, vendor_type, default_markup, import_template,
                enrichment_enabled, portal_url, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            vendor.code,
            vendor.name,
            vendor.vendor_type,
            vendor.default_markup,
            json.dumps(vendor.import_template) if vendor.import_template else None,
            vendor.enrichment_enabled,
            vendor.portal_url,
            vendor.notes,
            now,
            now,
        ))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_by_id(self, vendor_id: int) -> Vendor | None:
        """Get vendor by ID."""
        cursor = self._execute(
            "SELECT * FROM vendors WHERE id = ?",
            (vendor_id,)
        )
        row = cursor.fetchone()
        return Vendor.from_row(row) if row else None
    
    def get_by_code(self, code: str) -> Vendor | None:
        """Get vendor by code."""
        cursor = self._execute(
            "SELECT * FROM vendors WHERE code = ?",
            (code.upper(),)
        )
        row = cursor.fetchone()
        return Vendor.from_row(row) if row else None
    
    def get_all(self) -> list[Vendor]:
        """Get all vendors."""
        cursor = self._execute("SELECT * FROM vendors ORDER BY name")
        return [Vendor.from_row(row) for row in cursor.fetchall()]
    
    def get_suppliers(self) -> list[Vendor]:
        """Get only supplier-type vendors."""
        cursor = self._execute(
            "SELECT * FROM vendors WHERE vendor_type = 'supplier' ORDER BY name"
        )
        return [Vendor.from_row(row) for row in cursor.fetchall()]
    
    def get_competitors(self) -> list[Vendor]:
        """Get only competitor-type vendors."""
        cursor = self._execute(
            "SELECT * FROM vendors WHERE vendor_type = 'competitor' ORDER BY name"
        )
        return [Vendor.from_row(row) for row in cursor.fetchall()]
    
    def update(self, vendor: Vendor) -> bool:
        """Update an existing vendor."""
        if not vendor.id:
            return False
            
        now = datetime.now().isoformat()
        self._execute("""
            UPDATE vendors SET
                code = ?,
                name = ?,
                vendor_type = ?,
                default_markup = ?,
                import_template = ?,
                enrichment_enabled = ?,
                portal_url = ?,
                notes = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            vendor.code,
            vendor.name,
            vendor.vendor_type,
            vendor.default_markup,
            json.dumps(vendor.import_template) if vendor.import_template else None,
            vendor.enrichment_enabled,
            vendor.portal_url,
            vendor.notes,
            now,
            vendor.id,
        ))
        self.conn.commit()
        return True
    
    def delete(self, vendor_id: int) -> bool:
        """Delete a vendor."""
        self._execute("DELETE FROM vendors WHERE id = ?", (vendor_id,))
        self.conn.commit()
        return True
    
    # =========================================================================
    # Import Templates
    # =========================================================================
    
    def save_import_template(
        self,
        vendor_code: str,
        template: dict[str, str],
    ) -> bool:
        """Save an import template for a vendor."""
        vendor = self.get_by_code(vendor_code)
        if not vendor:
            return False
            
        vendor.import_template = template
        return self.update(vendor)
    
    def get_import_template(self, vendor_code: str) -> dict[str, str]:
        """Get the import template for a vendor."""
        vendor = self.get_by_code(vendor_code)
        if vendor and vendor.import_template:
            return vendor.import_template
        return {}
    
    def auto_detect_columns(self, headers: list[str]) -> dict[str, str]:
        """Auto-detect column mappings from CSV headers.
        
        Args:
            headers: List of column headers from CSV
            
        Returns:
            Dict mapping CSV column name → product field
        """
        mapping = {}
        
        for header in headers:
            header_lower = header.lower().strip().replace(" ", "_").replace("-", "_")
            
            for field, patterns in COLUMN_PATTERNS.items():
                if header_lower in patterns or any(p in header_lower for p in patterns):
                    mapping[header] = field
                    break
                    
        return mapping


# =============================================================================
# Convenience Functions
# =============================================================================

def get_vendor_service(db_connection=None) -> VendorService:
    """Get a VendorService instance."""
    return VendorService(db_connection)


def ensure_default_vendors(db_connection=None):
    """Ensure default vendors exist in database."""
    service = VendorService(db_connection)
    service.ensure_defaults()
