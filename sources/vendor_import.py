"""Vendor CSV/Excel Import Module for JAKS Scraper.

Handles:
- Loading CSV/Excel files
- Column mapping (auto-detect + manual)
- Validation before import
- Cross-reference checking against existing DB
- Actual import with duplicate handling
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("sources.vendor_import")


@dataclass
class ValidationError:
    """A validation error for a specific row."""
    row_index: int
    column: str
    value: str
    message: str
    severity: str = "error"  # 'error', 'warning'


@dataclass
class CrossRefMatch:
    """A cross-reference match found during analysis."""
    row_index: int
    new_part_number: str
    existing_product_id: int
    existing_sku: str
    existing_title: str
    match_type: str  # 'exact', 'cross_ref', 'similar'
    confidence: float


@dataclass 
class ImportAnalysis:
    """Results of analyzing a file before import."""
    total_rows: int = 0
    exact_matches: list[CrossRefMatch] = field(default_factory=list)
    cross_ref_matches: list[CrossRefMatch] = field(default_factory=list)
    potential_duplicates: list[CrossRefMatch] = field(default_factory=list)
    new_products: list[int] = field(default_factory=list)  # Row indices
    validation_errors: list[ValidationError] = field(default_factory=list)
    
    @property
    def exact_match_count(self) -> int:
        return len(self.exact_matches)
    
    @property
    def cross_ref_count(self) -> int:
        return len(self.cross_ref_matches)
    
    @property
    def duplicate_count(self) -> int:
        return len(self.potential_duplicates)
    
    @property
    def new_count(self) -> int:
        return len(self.new_products)


@dataclass
class ImportResult:
    """Results of an import operation."""
    imported: int = 0
    linked: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    products_created: list[int] = field(default_factory=list)  # Product IDs


class VendorImport:
    """Import products from vendor CSV/Excel files."""
    
    def __init__(
        self,
        vendor_code: str,
        db_connection=None,
    ):
        self.vendor_code = vendor_code
        self.conn = db_connection
        self.mapping: dict[str, str] = {}  # CSV column → product field
        self.data: list[dict] = []
        self.headers: list[str] = []
        
    def load_file(self, filepath: Path | str) -> list[dict]:
        """Load CSV or Excel file.
        
        Args:
            filepath: Path to the file
            
        Returns:
            List of row dictionaries
        """
        filepath = Path(filepath)
        
        if filepath.suffix.lower() in ('.xlsx', '.xls'):
            return self._load_excel(filepath)
        else:
            return self._load_csv(filepath)
            
    def _load_csv(self, filepath: Path) -> list[dict]:
        """Load a CSV file."""
        rows = []
        
        # Try different encodings
        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
        
        for encoding in encodings:
            try:
                with open(filepath, 'r', encoding=encoding, newline='') as f:
                    # Detect delimiter
                    sample = f.read(8192)
                    f.seek(0)
                    
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
                    except csv.Error:
                        dialect = csv.excel  # Default to comma
                    
                    reader = csv.DictReader(f, dialect=dialect)
                    self.headers = reader.fieldnames or []
                    
                    for row in reader:
                        # Clean up values
                        clean_row = {k: (v.strip() if v else "") for k, v in row.items()}
                        rows.append(clean_row)
                        
                self.data = rows
                logger.info(f"Loaded {len(rows)} rows from {filepath}")
                return rows
                
            except UnicodeDecodeError:
                continue
                
        raise ValueError(f"Could not read {filepath} with any supported encoding")
        
    def _load_excel(self, filepath: Path) -> list[dict]:
        """Load an Excel file."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required for Excel support: pip install pandas openpyxl")
            
        df = pd.read_excel(filepath, dtype=str)
        df = df.fillna("")
        
        self.headers = list(df.columns)
        self.data = df.to_dict('records')
        
        logger.info(f"Loaded {len(self.data)} rows from {filepath}")
        return self.data
        
    def set_mapping(self, mapping: dict[str, str]) -> None:
        """Set column to field mapping.
        
        Args:
            mapping: Dict of {csv_column: product_field}
        """
        self.mapping = mapping
        
    def auto_detect_mapping(self) -> dict[str, str]:
        """Auto-detect column mappings based on header names.
        
        Returns:
            Dict mapping CSV column → product field
        """
        from db.vendor_service import COLUMN_PATTERNS
        
        mapping = {}
        
        for header in self.headers:
            header_lower = header.lower().strip().replace(" ", "_").replace("-", "_")
            
            for field_name, patterns in COLUMN_PATTERNS.items():
                if header_lower in patterns or any(p in header_lower for p in patterns):
                    mapping[header] = field_name
                    break
                    
        self.mapping = mapping
        return mapping
        
    def get_preview(self, num_rows: int = 5) -> list[dict]:
        """Get preview of first N rows.
        
        Returns:
            List of row dicts with mapped field names
        """
        preview = []
        for row in self.data[:num_rows]:
            mapped_row = {}
            for csv_col, value in row.items():
                field = self.mapping.get(csv_col, csv_col)
                mapped_row[field] = value
            preview.append(mapped_row)
        return preview
        
    def validate_rows(self) -> list[ValidationError]:
        """Validate all rows before import.
        
        Returns:
            List of validation errors
        """
        errors = []
        
        # Check required fields are mapped
        required = ['vendor_part_number', 'title']
        for req in required:
            if req not in self.mapping.values():
                errors.append(ValidationError(
                    row_index=-1,
                    column="",
                    value="",
                    message=f"Required field '{req}' is not mapped to any column",
                    severity="error",
                ))
                
        # Validate each row
        for i, row in enumerate(self.data):
            # Get mapped values
            mapped = self._map_row(row)
            
            # Check vendor_part_number
            pn = mapped.get('vendor_part_number', '').strip()
            if not pn:
                errors.append(ValidationError(
                    row_index=i,
                    column="vendor_part_number",
                    value=pn,
                    message="Missing part number",
                    severity="error",
                ))
                
            # Check title
            title = mapped.get('title', '').strip()
            if not title:
                errors.append(ValidationError(
                    row_index=i,
                    column="title",
                    value=title,
                    message="Missing title/description",
                    severity="warning",
                ))
                
            # Validate cost if present
            cost_str = mapped.get('cost', '').strip()
            if cost_str:
                try:
                    # Remove currency symbols
                    cost_clean = re.sub(r'[$€£,]', '', cost_str)
                    float(cost_clean)
                except ValueError:
                    errors.append(ValidationError(
                        row_index=i,
                        column="cost",
                        value=cost_str,
                        message=f"Invalid cost format: {cost_str}",
                        severity="warning",
                    ))
                    
        return errors
        
    def _map_row(self, row: dict) -> dict:
        """Map a CSV row to product fields."""
        mapped = {}
        for csv_col, value in row.items():
            if csv_col in self.mapping:
                field = self.mapping[csv_col]
                mapped[field] = value
        return mapped
        
    def analyze_cross_references(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ImportAnalysis:
        """Analyze all rows for cross-references against existing DB.
        
        Args:
            progress_callback: Optional (current, total) progress callback
            
        Returns:
            ImportAnalysis with categorized results
        """
        if self.conn is None:
            logger.warning("No DB connection, skipping cross-ref analysis")
            return ImportAnalysis(
                total_rows=len(self.data),
                new_products=list(range(len(self.data))),
            )
            
        from db.product_grouping import ProductGroupingService, normalize_part_number
        
        service = ProductGroupingService(self.conn)
        service.build_part_number_index()
        
        analysis = ImportAnalysis(total_rows=len(self.data))
        
        for i, row in enumerate(self.data):
            if progress_callback:
                progress_callback(i, len(self.data))
                
            mapped = self._map_row(row)
            
            # Collect all part numbers from this row
            part_numbers = []
            
            pn = mapped.get('vendor_part_number', '').strip()
            if pn:
                part_numbers.append(pn)
                
            oem = mapped.get('oem_part_number', '').strip()
            if oem:
                part_numbers.append(oem)
                
            for j in range(1, 6):
                xref = mapped.get(f'cross_ref_{j}', '').strip()
                if xref:
                    part_numbers.append(xref)
                    
            if not part_numbers:
                analysis.new_products.append(i)
                continue
                
            # Check against database
            duplicates = service.find_duplicates_for_part_numbers(part_numbers)
            
            if not duplicates:
                analysis.new_products.append(i)
                continue
                
            # Categorize the match
            best_match = duplicates[0]
            
            match = CrossRefMatch(
                row_index=i,
                new_part_number=pn,
                existing_product_id=best_match.existing_product_id,
                existing_sku=best_match.existing_sku,
                existing_title=best_match.existing_title,
                match_type=best_match.match_type,
                confidence=best_match.confidence,
            )
            
            if best_match.confidence >= 1.0:
                analysis.exact_matches.append(match)
            elif best_match.confidence >= 0.8:
                analysis.cross_ref_matches.append(match)
            else:
                analysis.potential_duplicates.append(match)
                
        return analysis
        
    def import_products(
        self,
        *,
        on_exact_match: str = "update",   # 'skip', 'update', 'alternate'
        on_cross_ref: str = "link",       # 'skip', 'link', 'separate'
        on_potential_dup: str = "review", # 'skip', 'link', 'separate', 'review'
        category: str = "",
        default_markup: float = 25.0,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ImportResult:
        """Import products to database.
        
        Args:
            on_exact_match: Action for exact matches
            on_cross_ref: Action for cross-reference matches
            on_potential_dup: Action for potential duplicates
            category: Default category for new products
            default_markup: Default markup percentage
            progress_callback: Optional (current, total, message) callback
            
        Returns:
            ImportResult with counts and errors
        """
        if self.conn is None:
            return ImportResult(errors=["No database connection"])
            
        from db.product_grouping import ProductGroupingService, link_duplicate
        
        result = ImportResult()
        service = ProductGroupingService(self.conn)
        service.build_part_number_index()
        
        for i, row in enumerate(self.data):
            if progress_callback:
                progress_callback(i, len(self.data), f"Processing row {i+1}")
                
            try:
                mapped = self._map_row(row)
                outcome = self._import_single_row(
                    mapped, service, i,
                    on_exact_match, on_cross_ref, on_potential_dup,
                    category, default_markup,
                )
                
                if outcome == "imported":
                    result.imported += 1
                elif outcome == "linked":
                    result.linked += 1
                elif outcome == "updated":
                    result.updated += 1
                elif outcome == "skipped":
                    result.skipped += 1
                    
            except Exception as e:
                logger.error(f"Error importing row {i}: {e}")
                result.errors.append(f"Row {i}: {e}")
                result.skipped += 1
                
        self.conn.commit()
        return result
        
    def _import_single_row(
        self,
        mapped: dict,
        service,  # ProductGroupingService instance
        row_index: int,
        on_exact_match: str,
        on_cross_ref: str,
        on_potential_dup: str,
        category: str,
        default_markup: float,
    ) -> str:
        """Import a single row.
        
        Returns:
            'imported', 'linked', 'updated', or 'skipped'
        """
        # Collect part numbers
        part_numbers = []
        pn = mapped.get('vendor_part_number', '').strip()
        if pn:
            part_numbers.append(pn)
        oem = mapped.get('oem_part_number', '').strip()
        if oem:
            part_numbers.append(oem)
        for j in range(1, 6):
            xref = mapped.get(f'cross_ref_{j}', '').strip()
            if xref:
                part_numbers.append(xref)
        
        # NEW: Identify the actual manufacturer from part numbers
        # This is CRITICAL because HHP/ATL are resellers, not manufacturers
        manufacturer = ""
        manufacturer_code = ""
        try:
            from engine.part_number_patterns import identify_manufacturer_with_context
            
            title = mapped.get('title', '')
            for check_pn in part_numbers:
                mfr_match = identify_manufacturer_with_context(check_pn, title)
                if mfr_match and mfr_match.confidence >= 0.7:
                    manufacturer = mfr_match.manufacturer
                    manufacturer_code = mfr_match.manufacturer_code
                    logger.debug(f"Row {row_index}: Identified manufacturer {manufacturer} from {check_pn}")
                    break
        except ImportError:
            pass
                
        # Check for duplicates
        product_data = {
            'primary_pn': pn,
            'oem_pn': oem,
            'xrefs': part_numbers[2:],  # After vendor and OEM
            'title': mapped.get('title', ''),
            'category': category or mapped.get('category', ''),
            'engine': mapped.get('engine', ''),
            'source_vendor': self.vendor_code,  # Where we got the data
            'manufacturer': manufacturer,  # Who actually makes the part
            'manufacturer_code': manufacturer_code,
        }
        
        duplicate = service.check_for_duplicate_before_insert(product_data)
        
        if duplicate:
            # Decide action based on match type
            if duplicate.confidence >= 1.0:
                action = on_exact_match
            elif duplicate.confidence >= 0.8:
                action = on_cross_ref
            else:
                action = on_potential_dup
                
            if action == "skip":
                return "skipped"
            elif action == "link":
                # Link part numbers to existing product
                service.link_product_from_duplicate(
                    duplicate, product_data, source=f"csv_import:{self.vendor_code}"
                )
                return "linked"
            elif action == "update":
                # Update cost/pricing on existing product
                self._update_existing_product(duplicate.existing_product_id, mapped, default_markup)
                return "updated"
            elif action == "alternate":
                # Create as alternate (fall through to create)
                pass
            elif action == "review":
                # Should be handled by caller with UI
                return "skipped"
                
        # Create new product with manufacturer info
        product_id = self._create_product(mapped, category, default_markup, manufacturer, manufacturer_code)
        return "imported"
        
    def _create_product(
        self,
        mapped: dict,
        category: str,
        default_markup: float,
        manufacturer: str = "",
        manufacturer_code: str = "",
    ) -> int:
        """Create a new product from mapped data.
        
        Args:
            mapped: Mapped row data
            category: Product category
            default_markup: Markup percentage
            manufacturer: Identified manufacturer (PAI, McBee, etc.)
            manufacturer_code: Manufacturer code (PAI, MCB, etc.)
        """
        from db.product_grouping import normalize_part_number
        
        pn = mapped.get('vendor_part_number', '').strip()
        title = mapped.get('title', '').strip() or f"Product {pn}"
        
        # Generate SKU - use manufacturer code if identified
        if manufacturer_code:
            sku = f"JAKS-{normalize_part_number(pn)}"
        else:
            sku = f"JAKS-{normalize_part_number(pn)}"
        
        # Parse cost
        cost = 0.0
        cost_str = mapped.get('cost', '').strip()
        if cost_str:
            try:
                cost = float(re.sub(r'[$€£,]', '', cost_str))
            except ValueError:
                pass
                
        # Calculate price with markup
        price = cost * (1 + default_markup / 100) if cost > 0 else 0
        
        # Parse weight
        weight = 0.0
        weight_str = mapped.get('weight', '').strip()
        if weight_str:
            try:
                # Extract numeric portion
                weight_match = re.search(r'[\d.]+', weight_str)
                if weight_match:
                    weight = float(weight_match.group())
            except ValueError:
                pass
                
        now = datetime.now().isoformat()
        
        # Use manufacturer as brand if identified, otherwise use vendor
        brand = manufacturer or mapped.get('brand', '') or self.vendor_code
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO products (
                sku, title, category, engine, brand,
                cost, price, weight, dimensions,
                supplier, supplier_part_number,
                oem_part_number, stage, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sku,
            title,
            category or mapped.get('category', ''),
            mapped.get('engine', ''),
            brand,  # Use identified manufacturer
            cost,
            price,
            weight,
            mapped.get('dimensions', ''),
            self.vendor_code,  # Source vendor (where we got the data)
            pn,
            mapped.get('oem_part_number', ''),
            2,  # Stage 2 = Scraped/Imported
            now,
            now,
        ))
        
        product_id = cursor.lastrowid
        
        # Add part numbers to part_numbers table with manufacturer info
        self._add_part_numbers(product_id, mapped, manufacturer)
        
        return product_id
        
    def _add_part_numbers(self, product_id: int, mapped: dict, manufacturer: str = ""):
        """Add part numbers to the part_numbers table.
        
        Args:
            product_id: Product ID to link to
            mapped: Mapped row data
            manufacturer: Identified manufacturer (PAI, McBee, etc.)
        """
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        
        # Vendor part number - note: this might be a MANUFACTURER's part number
        # even if we got it from a reseller like HHP
        pn = mapped.get('vendor_part_number', '').strip()
        if pn:
            from db.product_grouping import normalize_part_number
            
            # If we identified a manufacturer, use that instead of the source vendor
            pn_manufacturer = manufacturer if manufacturer else self.vendor_code
            pn_type = 'manufacturer' if manufacturer else 'vendor'
            
            cursor.execute("""
                INSERT OR IGNORE INTO part_numbers
                (product_id, number, number_normalized, type, manufacturer, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (product_id, pn, normalize_part_number(pn), pn_type, pn_manufacturer, 
                  f'csv_import:{self.vendor_code}', now))
                  
        # OEM part number
        oem = mapped.get('oem_part_number', '').strip()
        if oem:
            cursor.execute("""
                INSERT OR IGNORE INTO part_numbers
                (product_id, number, number_normalized, type, manufacturer, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (product_id, oem, normalize_part_number(oem), 'oem', '', 
                  f'csv_import:{self.vendor_code}', now))
                  
        # Cross references
        for j in range(1, 6):
            xref = mapped.get(f'cross_ref_{j}', '').strip()
            if xref:
                cursor.execute("""
                    INSERT OR IGNORE INTO part_numbers
                    (product_id, number, number_normalized, type, manufacturer, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (product_id, xref, normalize_part_number(xref), 'cross_ref', '', 
                      f'csv_import:{self.vendor_code}', now))
                      
    def _update_existing_product(
        self,
        product_id: int,
        mapped: dict,
        default_markup: float,
    ):
        """Update cost/pricing on existing product."""
        import json
        
        cursor = self.conn.cursor()
        
        # Get current vendor_pricing
        cursor.execute("SELECT vendor_pricing FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        
        pricing = {}
        if row and row[0]:
            try:
                pricing = json.loads(row[0])
            except:
                pass
                
        # Parse new cost
        cost = 0.0
        cost_str = mapped.get('cost', '').strip()
        if cost_str:
            try:
                cost = float(re.sub(r'[$€£,]', '', cost_str))
            except ValueError:
                pass
                
        # Update vendor pricing
        pricing[self.vendor_code] = {
            'part_number': mapped.get('vendor_part_number', ''),
            'cost': cost,
            'updated': datetime.now().isoformat(),
        }
        
        cursor.execute("""
            UPDATE products SET vendor_pricing = ?, updated_at = ?
            WHERE id = ?
        """, (json.dumps(pricing), datetime.now().isoformat(), product_id))


# =============================================================================
# Convenience Functions
# =============================================================================

def import_vendor_csv(
    filepath: Path | str,
    vendor_code: str,
    mapping: dict[str, str] | None = None,
    db_connection=None,
    **kwargs,
) -> ImportResult:
    """Convenience function to import a vendor CSV.
    
    Args:
        filepath: Path to CSV/Excel file
        vendor_code: Vendor code (e.g., 'PAI', 'G2')
        mapping: Column mapping (auto-detected if None)
        db_connection: Database connection
        **kwargs: Passed to import_products()
        
    Returns:
        ImportResult
    """
    importer = VendorImport(vendor_code, db_connection)
    importer.load_file(filepath)
    
    if mapping:
        importer.set_mapping(mapping)
    else:
        importer.auto_detect_mapping()
        
    return importer.import_products(**kwargs)
