"""Vendor Import Service — create inventory items from vendor search results.

Turns PAI / SAMPA / other vendor results into clean product records using
existing product creation rules, SKU generation, and duplicate detection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from db import (
    create_product,
    generate_sku,
    get_next_sku_preview,
    get_product_by_sku,
    add_interchange,
)
from .match_engine import MatchEngine, MatchResult

log = logging.getLogger(__name__)


@dataclass
class VendorImportData:
    """Pre-filled data for the import preview screen."""
    vendor: str = ""
    vendor_part_number: str = ""
    oem_part_number: str = ""
    sku: str = ""
    title: str = ""
    description: str = ""
    category: str = ""
    subcategory: str = ""
    manufacturer: str = ""
    condition: str = "New"
    cost: float = 0.0
    price: float = 0.0
    core_charge: float = 0.0
    cross_references: list[str] = field(default_factory=list)
    notes: str = ""
    image_url: str = ""
    raw_vendor_data: dict = field(default_factory=dict)


class VendorImportService:
    """Service for importing products from vendor search results."""

    def __init__(self):
        self._match_engine = MatchEngine()

    def prepare_import(self, data: VendorImportData) -> dict:
        """Prepare import data and check for duplicates.

        Returns dict with keys:
          - 'data': VendorImportData with auto-generated fields filled in
          - 'match_result': MatchResult from duplicate check
          - 'sku_preview': Generated SKU preview
        """
        # Auto-generate SKU if not provided
        if not data.sku:
            prefix = self._vendor_to_prefix(data.vendor)
            data.sku = get_next_sku_preview(prefix)

        # Set default condition
        if not data.condition:
            data.condition = "New"

        # Build title from parts if not provided
        if not data.title and data.oem_part_number:
            parts = [data.manufacturer, data.oem_part_number]
            if data.description:
                parts.append(data.description)
            data.title = " ".join(p for p in parts if p)

        # Run duplicate check
        match_result = self._match_engine.check_duplicate(
            sku=data.sku,
            oem_part_number=data.oem_part_number,
            vendor_part_number=data.vendor_part_number,
            xref_part_number=data.oem_part_number,
            title=data.title,
        )

        return {
            "data": data,
            "match_result": match_result,
            "sku_preview": data.sku,
        }

    def execute_import(self, data: VendorImportData) -> dict:
        """Create the product in the database.

        Returns dict with 'success', 'id', 'sku', or 'error'.
        """
        # Generate real SKU
        prefix = self._vendor_to_prefix(data.vendor)
        sku = generate_sku(prefix)

        result = create_product(
            sku=sku,
            title=data.title or f"{data.oem_part_number}",
            category=data.category,
            manufacturer=data.manufacturer,
            supplier=data.vendor,
            condition=data.condition,
            cost=data.cost,
            pai_cost=data.cost if data.vendor.upper() == "PAI" else 0,
            selling_price=data.price,
            core_charge=data.core_charge,
            oem_part_number=data.oem_part_number,
            pai_sku=data.vendor_part_number if data.vendor.upper() == "PAI" else "",
            description_html=data.description,
        )

        if not result.get("success"):
            return result

        product_id = result["id"]

        # Add cross references
        for xref in data.cross_references:
            if xref.strip():
                try:
                    add_interchange(product_id, xref.strip(), "OEM", data.manufacturer, "")
                except Exception as exc:
                    log.warning("Failed to add xref %s: %s", xref, exc)

        # Add OEM as interchange too
        if data.oem_part_number:
            try:
                add_interchange(product_id, data.oem_part_number, "OEM", data.manufacturer, "")
            except Exception:
                pass  # May already exist as duplicate

        return {"success": True, "id": product_id, "sku": sku}

    @staticmethod
    def _vendor_to_prefix(vendor: str) -> str:
        """Convert vendor name to SKU prefix."""
        vendor_map = {
            "PAI": "PAI",
            "SAMPA": "SMP",
        }
        v = vendor.strip().upper()
        return vendor_map.get(v, v[:3] if v else "GEN")
