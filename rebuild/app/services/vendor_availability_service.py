"""
app/services/vendor_availability_service.py
=============================================
Vendor availability lookup — Phase 2/3 feature stub.

Planned capability:
  - Query vendor APIs / websites for current stock levels
  - Compare JAKS cost vs vendor current price
  - Suggest reorder when below reorder_point
  - Multi-vendor comparison: given product, rank by availability + cost + lead time

In Phase 1: all methods return stubbed "check manually" responses.
The interface is defined now so the quote screen can call it without
changing call sites when real vendor feeds are wired up.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.base import BaseService


@dataclass
class VendorAvailabilityResult:
    vendor_id: int
    vendor_name: str
    vendor_part_number: str
    qty_available: int | None        # None = unknown
    unit_cost: float | None          # None = unknown / not returned by feed
    lead_time_days: int | None
    source: str                      # "api" | "manual" | "cached"
    last_checked_at: str | None


class VendorAvailabilityService(BaseService):

    def check_availability(
        self, product_id: int, qty_needed: int = 1
    ) -> list[VendorAvailabilityResult]:
        """
        Return availability from all vendors linked to this product.
        Phase 1: returns stubbed results with source='manual'.
        Phase 2+: hits vendor APIs where configured.
        """
        raise NotImplementedError

    def get_best_vendor(
        self, product_id: int, qty_needed: int = 1
    ) -> VendorAvailabilityResult | None:
        """
        Return the best vendor option based on:
          1. has qty_available >= qty_needed
          2. lowest unit_cost
          3. shortest lead_time_days
        Returns None if no vendors have availability data.
        """
        raise NotImplementedError

    def get_reorder_candidates(self) -> list[dict]:
        """
        Return products below reorder_point grouped by preferred vendor.
        Used for the reorder report / PO suggestion feature.
        """
        raise NotImplementedError
